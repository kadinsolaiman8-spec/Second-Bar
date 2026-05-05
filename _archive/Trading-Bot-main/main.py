"""
Discord Trading Alert Bot - main entry point.
Pycord bot with /recap slash command and auto-recap every 30 min when market is open.
"""

import asyncio
import io
import logging
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import discord
import pandas as pd
import yaml
from dotenv import load_dotenv

from src.data import fetch_ohlcv, fetch_single, fetch_vix_current, get_stock_exchange, set_provider_config, fetch_fundamentals, fetch_bond_yield
from src.indicators import get_latest_indicators
from src.news import fetch_news, compute_sentiment, format_headlines_for_embed, sentiment_label, filter_by_severity
from src.indices import get_constituents, get_supported_summary, resolve_input
from src.market_hours import is_market_open
from src.recap import format_recap_embed
from src.backtest import BacktestResult, run_backtest as run_backtest_engine, format_backtest_embed
from src.walk_forward import format_walk_forward_embed, run_walk_forward_optimization
from src.signals import Signal, evaluate_all, evaluate_signal
from src.stock import format_stock_embed
from src.daytrade import compute_daytrade_levels, format_daytrade_embed
from src.tutorial import build_tutorial_embed
from src.watchlist import add_ticker, get_tickers, remove_ticker
from src.expert import get_expert_sentiments
from src.stop import StopRequested, clear_stop, is_stop_requested, request_stop
from src.recap_queue import RecapJob, enqueue_recap, init_recap_queue, recap_queue_worker
from src.config_resolver import get_config_for_ticker
from src.validation_routing import warn_hybrid_vs_official_validation
from src.charts import build_stock_chart, build_equity_chart, build_daytrade_chart
from src.utils import sanitize_for_discord

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Load config
CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)

# Optional: load external ticker profiles for scalability (data/ticker_profiles.yaml)
TICKER_PROFILES_PATH = Path(__file__).parent / "data" / "ticker_profiles.yaml"
if TICKER_PROFILES_PATH.exists():
    try:
        with open(TICKER_PROFILES_PATH) as f:
            external = yaml.safe_load(f)
        if isinstance(external, dict) and "ticker_profiles" in external:
            ext_profiles = external.get("ticker_profiles") or {}
            cfg_profiles = CONFIG.get("ticker_profiles") or {}
            CONFIG["ticker_profiles"] = {**ext_profiles, **cfg_profiles}
    except Exception as e:
        logger.warning("Could not load ticker_profiles.yaml: %s", e)


def _validate_config() -> None:
    """Validate config values at startup. Raises ValueError on invalid config."""
    ind = CONFIG.get("indicators", {})
    # Periods and windows: positive integers
    for key in ("rsi_period", "macd_fast", "macd_slow", "macd_signal", "bb_period",
                "supertrend_period", "stoch_window", "stoch_smooth", "willr_period",
                "ema_fast", "ema_slow", "atr_period", "atr_avg_period"):
        v = ind.get(key)
        if v is not None and (not isinstance(v, (int, float)) or v < 1 or v > 200):
            raise ValueError(f"config indicators.{key} must be 1-200, got {v}")
    # bb_std and supertrend_multiplier: positive
    if ind.get("bb_std") is not None and (ind["bb_std"] <= 0 or ind["bb_std"] > 10):
        raise ValueError(f"config indicators.bb_std must be 0-10, got {ind.get('bb_std')}")
    if ind.get("supertrend_multiplier") is not None and (ind["supertrend_multiplier"] <= 0 or ind["supertrend_multiplier"] > 20):
        raise ValueError(f"config indicators.supertrend_multiplier must be 0-20, got {ind.get('supertrend_multiplier')}")
    # RSI: oversold < overbought
    rsi_os, rsi_ob = ind.get("rsi_oversold", 35), ind.get("rsi_overbought", 65)
    if rsi_os >= rsi_ob:
        raise ValueError(f"config indicators.rsi_oversold ({rsi_os}) must be < rsi_overbought ({rsi_ob})")
    # Stochastic: oversold < overbought
    so_os, so_ob = ind.get("stoch_oversold", 20), ind.get("stoch_overbought", 80)
    if so_os >= so_ob:
        raise ValueError(f"config indicators.stoch_oversold ({so_os}) must be < stoch_overbought ({so_ob})")
    # Williams %R: oversold < overbought (e.g. -80 < -20)
    wr_os, wr_ob = ind.get("willr_oversold", -80), ind.get("willr_overbought", -20)
    if wr_os >= wr_ob:
        raise ValueError(f"config indicators.willr_oversold ({wr_os}) must be < willr_overbought ({wr_ob})")
    # Top-level config
    min_conf = CONFIG.get("min_confidence", 60)
    if min_conf is not None and (not isinstance(min_conf, (int, float)) or min_conf < 1 or min_conf > 100):
        raise ValueError(f"config min_confidence must be 1-100, got {min_conf}")
    recap_tf = CONFIG.get("recap_timeframe", "Daily")
    if recap_tf is not None and not isinstance(recap_tf, str):
        raise ValueError(f"config recap_timeframe must be a string, got {type(recap_tf)}")
    data_days = CONFIG.get("data_period_days", 60)
    if data_days is not None and (not isinstance(data_days, (int, float)) or data_days < 1 or data_days > 365):
        raise ValueError(f"config data_period_days must be 1-365, got {data_days}")
    recap_int = CONFIG.get("recap_interval_minutes", 30)
    if recap_int is not None and (not isinstance(recap_int, (int, float)) or recap_int < 1 or recap_int > 1440):
        raise ValueError(f"config recap_interval_minutes must be 1-1440, got {recap_int}")
    # indicator_weights: optional dict of str -> float
    iw = CONFIG.get("indicator_weights")
    if iw is not None and not isinstance(iw, dict):
        raise ValueError(f"config indicator_weights must be a dict, got {type(iw)}")
    # min_net_score: optional float
    mns = CONFIG.get("min_net_score")
    if mns is not None and (not isinstance(mns, (int, float)) or mns < 0 or mns > 10):
        raise ValueError(f"config min_net_score must be 0-10, got {mns}")
    # news: optional dict
    nc = CONFIG.get("news")
    if nc is not None and not isinstance(nc, dict):
        raise ValueError(f"config news must be a dict, got {type(nc)}")
    # timeframe_confidence_factors: optional dict Daily/1W/1H -> 0.5-1.0
    tcf = CONFIG.get("timeframe_confidence_factors")
    if tcf is not None:
        if not isinstance(tcf, dict):
            raise ValueError(f"config timeframe_confidence_factors must be a dict, got {type(tcf)}")
        for k, v in tcf.items():
            if v is not None and (not isinstance(v, (int, float)) or v < 0.5 or v > 1.0):
                raise ValueError(f"config timeframe_confidence_factors.{k} must be 0.5-1.0, got {v}")
    # timeframe_indicator_weights: optional dict Daily/1W/1H -> indicator weights
    tiw = CONFIG.get("timeframe_indicator_weights")
    if tiw is not None and not isinstance(tiw, dict):
        raise ValueError(f"config timeframe_indicator_weights must be a dict, got {type(tiw)}")
    # regime_indicator_weights: optional dict bull/bear -> indicator weights
    riw = CONFIG.get("regime_indicator_weights")
    if riw is not None and not isinstance(riw, dict):
        raise ValueError(f"config regime_indicator_weights must be a dict, got {type(riw)}")
    # ticker_profiles: optional dict ticker -> overrides
    tp = CONFIG.get("ticker_profiles")
    if tp is not None and not isinstance(tp, dict):
        raise ValueError(f"config ticker_profiles must be a dict, got {type(tp)}")
    # asset_class_profiles: optional dict asset_class -> overrides
    acp = CONFIG.get("asset_class_profiles")
    if acp is not None and not isinstance(acp, dict):
        raise ValueError(f"config asset_class_profiles must be a dict, got {type(acp)}")
    # daytrade: optional atr_stop_multiplier, atr_tp_multiplier (0.5-5.0)
    dt = CONFIG.get("daytrade", {})
    for key in ("atr_stop_multiplier", "atr_tp_multiplier"):
        v = dt.get(key)
        if v is not None and (not isinstance(v, (int, float)) or v < 0.5 or v > 5.0):
            raise ValueError(f"config daytrade.{key} must be 0.5-5.0, got {v}")
    # backtest: stop_pct, take_profit_pct, trailing_stop_pct, max_hold_bars
    bt = CONFIG.get("backtest", {})
    for key, (lo, hi) in [
        ("stop_pct", (0, 100)),
        ("take_profit_pct", (0, 100)),
        ("trailing_stop_pct", (0, 100)),
        ("max_hold_bars", (0, 1000)),
    ]:
        v = bt.get(key)
        if v is not None and (not isinstance(v, (int, float)) or v < lo or v > hi):
            raise ValueError(f"config backtest.{key} must be {lo}-{hi}, got {v}")
    # walk_forward optimize_metric
    wf = CONFIG.get("walk_forward", {})
    om = wf.get("optimize_metric")
    if om is not None and om not in ("outperformance", "total_return", "sharpe", "return_drawdown"):
        raise ValueError(f"config walk_forward.optimize_metric must be outperformance|total_return|sharpe|return_drawdown, got {om}")
    # recap_queue max_size
    rq = CONFIG.get("recap_queue", {})
    rq_max = rq.get("max_size", 3)
    if rq_max is not None and (not isinstance(rq_max, (int, float)) or rq_max < 0 or rq_max > 100):
        raise ValueError(f"config recap_queue.max_size must be 0-100, got {rq_max}")
    # charts: optional
    ch = CONFIG.get("charts", {})
    if ch is not None and not isinstance(ch, dict):
        raise ValueError(f"config charts must be a dict, got {type(ch)}")
    if isinstance(ch, dict):
        lb = ch.get("lookback_bars", 60)
        if lb is not None and (not isinstance(lb, (int, float)) or lb < 1 or lb > 500):
            raise ValueError(f"config charts.lookback_bars must be 1-500, got {lb}")
    # expert_input: optional enabled (bool)
    ei = CONFIG.get("expert_input")
    if ei is not None and not isinstance(ei, dict):
        raise ValueError(f"config expert_input must be a dict, got {type(ei)}")


_validate_config()
warn_hybrid_vs_official_validation(CONFIG.get("ticker_profiles"))

set_provider_config(CONFIG)

DATA_PERIOD_DAYS = CONFIG.get("data_period_days", 60)
RECAP_INTERVAL = CONFIG.get("recap_interval_minutes", 30)
MIN_CONFIDENCE = CONFIG.get("min_confidence", 60)
RECAP_TIMEFRAME = (CONFIG.get("recap_timeframe") or "Daily").strip() or "Daily"
SHOW_SIGNAL_BREAKDOWN = CONFIG.get("show_signal_breakdown", False)
CHANNEL_ID = CONFIG.get("channel_id")
GUILD_ID = CONFIG.get("guild_id")

# Guild-specific commands sync instantly; omit for global (can take up to 1 hour)
def _slash_kwargs():
    return {"guild_ids": [GUILD_ID]} if GUILD_ID else {}

# Map days to yfinance period (need ~50+ trading days for indicators; 2mo ≈ 44, 3mo ≈ 63)
PERIOD_MAP = {30: "3mo", 60: "3mo", 90: "6mo"}
PERIOD = PERIOD_MAP.get(DATA_PERIOD_DAYS, "3mo")

bot = discord.Bot()
_executor = ThreadPoolExecutor(max_workers=2)

RECAP_QUEUE_MAX = CONFIG.get("recap_queue", {}).get("max_size", 3) or 0
init_recap_queue(max_size=RECAP_QUEUE_MAX if RECAP_QUEUE_MAX > 0 else 0)

_auto_recap_task = None
_recap_queue_task = None

# Rate limit: only one backtest/WFO at a time (prevents duplicate runs and compute waste)
_backtest_in_progress = False

# Keys needed by get_latest_indicators (subset of indicator params)
_INDICATOR_KEYS_FOR_INDICATORS = (
    "rsi_period", "macd_fast", "macd_slow", "macd_signal",
    "bb_period", "bb_std", "supertrend_period", "supertrend_multiplier",
    "stoch_window", "stoch_smooth", "willr_period",
    "ema_fast", "ema_slow", "atr_period", "atr_avg_period",
)


def _indicator_params() -> dict:
    """Return indicator params from CONFIG for evaluate_signal/evaluate_all."""
    ind = CONFIG.get("indicators", {})
    return {
        "rsi_oversold": ind.get("rsi_oversold", 35),
        "rsi_overbought": ind.get("rsi_overbought", 65),
        "rsi_period": ind.get("rsi_period", 14),
        "macd_fast": ind.get("macd_fast", 12),
        "macd_slow": ind.get("macd_slow", 26),
        "macd_signal": ind.get("macd_signal", 9),
        "bb_period": ind.get("bb_period", 20),
        "bb_std": ind.get("bb_std", 2),
        "supertrend_period": ind.get("supertrend_period", 10),
        "supertrend_multiplier": ind.get("supertrend_multiplier", 3),
        "stoch_window": ind.get("stoch_window", 14),
        "stoch_smooth": ind.get("stoch_smooth", 3),
        "stoch_oversold": ind.get("stoch_oversold", 20),
        "stoch_overbought": ind.get("stoch_overbought", 80),
        "willr_period": ind.get("willr_period", 14),
        "willr_oversold": ind.get("willr_oversold", -80),
        "willr_overbought": ind.get("willr_overbought", -20),
        "ema_fast": ind.get("ema_fast", 9),
        "ema_slow": ind.get("ema_slow", 21),
        "atr_period": ind.get("atr_period", 14),
        "atr_avg_period": ind.get("atr_avg_period", 20),
    }


def _get_high_volume_symbols(ohlcv: dict, top_n: int) -> list[str]:
    """Return top N symbols by average volume (descending)."""
    vols = []
    for symbol, df in ohlcv.items():
        if df is not None and not df.empty and "Volume" in df.columns:
            avg_vol = df["Volume"].mean()
            if avg_vol > 0:
                vols.append((symbol, avg_vol))
    vols.sort(key=lambda x: -x[1])
    return [s for s, _ in vols[:top_n]]


def _fetch_news_for_recap(symbols: list[str]) -> tuple[dict[str, float], dict[str, str]]:
    """
    Fetch news for symbols, compute sentiment. Returns (news_sentiments, news_labels).
    news_sentiments: symbol -> float (-1 to +1)
    news_labels: symbol -> "Bullish" | "Bearish" | "Neutral"
    """
    news_cfg = CONFIG.get("news", {})
    if not news_cfg.get("enabled", False) or not symbols:
        return {}, {}
    max_hl = news_cfg.get("max_headlines", 5)
    provider = news_cfg.get("sentiment_provider", "vader")
    sentiments: dict[str, float] = {}
    labels: dict[str, str] = {}
    for symbol in symbols:
        try:
            headlines = fetch_news(symbol, count=max_hl)
            if headlines:
                s = compute_sentiment(headlines, provider=provider)
                sentiments[symbol] = s
                labels[symbol] = sentiment_label(s)
        except Exception as e:
            logger.warning("Failed to fetch news for %s in recap: %s", symbol, e)
    return sentiments, labels


def run_recap(
    ignore_volatility: bool = False,
    timeframe: str = "Daily",
    show_breakdown: bool = False,
) -> discord.Embed:
    """Fetch data, compute signals, return Discord Embed."""
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    logger.info("Running market recap...")
    recap_period, recap_interval = _resolve_period_interval(timeframe, PERIOD)
    ohlcv = fetch_ohlcv(period=recap_period, interval=recap_interval)
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    if not ohlcv:
        return discord.Embed(
            title="Market Recap - Error",
            description="Could not fetch market data. Please try again later.",
            color=0x808080,
        )

    news_sentiments: dict[str, float] = {}
    news_labels: dict[str, str] = {}
    news_cfg = CONFIG.get("news", {})
    recap_top = news_cfg.get("recap_top_volume", 0)
    if news_cfg.get("enabled", False) and recap_top > 0:
        high_vol = _get_high_volume_symbols(ohlcv, recap_top)
        if high_vol:
            news_sentiments, news_labels = _fetch_news_for_recap(high_vol)

    vix = fetch_vix_current()
    expert_sentiments = get_expert_sentiments(list(ohlcv.keys()), CONFIG)
    signals = evaluate_all(
        ohlcv,
        **_indicator_params(),
        ignore_volatility=ignore_volatility,
        config=CONFIG,
        news_sentiments=news_sentiments,
        expert_sentiments=expert_sentiments,
        timeframe=timeframe,
        vix=vix,
    )

    expert_labels = {sym: sentiment_label(s) for sym, s in expert_sentiments.items()}
    embed_dict = format_recap_embed(
        signals, include_hold=False, min_confidence=MIN_CONFIDENCE,
        ignore_volatility=ignore_volatility,
        timeframe=timeframe,
        news_labels=news_labels,
        expert_labels=expert_labels,
        show_breakdown=show_breakdown,
    )
    embed = discord.Embed(
        title=embed_dict["title"],
        description=embed_dict["description"],
        color=embed_dict["color"],
    )
    embed.set_footer(text=embed_dict.get("footer", {}).get("text", "S&P 100 | RSI, MACD, BB, SuperTrend, Stochastic, Williams %R, EMA"))
    return embed


def run_market(
    index_id: str,
    index_name: str,
    ignore_volatility: bool = False,
    timeframe: str = "Daily",
    show_breakdown: bool = False,
) -> discord.Embed:
    """Fetch data for index constituents, compute signals, return Discord Embed."""
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    logger.info("Running market recap for %s...", index_name)
    constituents = get_constituents(index_id)
    if not constituents:
        return discord.Embed(
            title=f"Market Recap - {index_name}",
            description="No constituent data for this index. It may not be supported yet.",
            color=0x808080,
        )

    market_period, market_interval = _resolve_period_interval(timeframe, PERIOD)
    ohlcv = fetch_ohlcv(symbols=constituents, period=market_period, interval=market_interval)
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    if not ohlcv:
        return discord.Embed(
            title=f"Market Recap - {index_name}",
            description="Could not fetch market data. Please try again later.",
            color=0x808080,
        )

    news_sentiments: dict[str, float] = {}
    news_labels: dict[str, str] = {}
    news_cfg = CONFIG.get("news", {})
    recap_top = news_cfg.get("recap_top_volume", 0)
    if news_cfg.get("enabled", False) and recap_top > 0:
        high_vol = _get_high_volume_symbols(ohlcv, recap_top)
        if high_vol:
            news_sentiments, news_labels = _fetch_news_for_recap(high_vol)

    vix = fetch_vix_current()
    expert_sentiments = get_expert_sentiments(list(ohlcv.keys()), CONFIG)
    signals = evaluate_all(
        ohlcv,
        **_indicator_params(),
        ignore_volatility=ignore_volatility,
        config=CONFIG,
        news_sentiments=news_sentiments,
        expert_sentiments=expert_sentiments,
        timeframe=timeframe,
        index_id=index_id,
        vix=vix,
    )

    expert_labels = {sym: sentiment_label(s) for sym, s in expert_sentiments.items()}
    embed_dict = format_recap_embed(
        signals, include_hold=False, min_confidence=MIN_CONFIDENCE, index_name=index_name,
        ignore_volatility=ignore_volatility,
        timeframe=timeframe,
        news_labels=news_labels,
        expert_labels=expert_labels,
        show_breakdown=show_breakdown,
    )
    embed = discord.Embed(
        title=embed_dict["title"],
        description=embed_dict["description"],
        color=embed_dict["color"],
    )
    embed.set_footer(text=embed_dict.get("footer", {}).get("text", f"{index_name} | RSI, MACD, BB, SuperTrend, Stochastic, Williams %R, EMA"))
    return embed


# Period/interval for each timeframe (1d, 1wk, 1h)
# Each covers its period with ~48 bars: 1d=1 day, 1h=1 hour, 1wk=1 week
# Fetch extra for indicator warmup (min_len ~49)
TF_PERIOD_INTERVAL = {
    "1d": ("5d", "30m"),   # 48 bars of 30m = 1 day
    "1wk": ("1mo", "1h"),  # 33 bars of 1h = 1 week (closest to 48)
    "1h": ("2d", "1m"),    # 60 bars of 1m = 1 hour
}


def _eval_signal_for_df(
    df,
    ticker: str,
    ignore_volatility: bool,
    news_sentiment: float | None = None,
    expert_sentiment: float | None = None,
    timeframe: str | None = None,
    index_id: str | None = None,
    vix: float | None = None,
) -> Signal | None:
    """Evaluate signal for a DataFrame. Returns None if insufficient data.
    timeframe: Daily/1W/1H for per-timeframe weights and confidence scaling; None uses defaults.
    index_id: When from /market, pass index id for asset-class resolution.
    vix: current VIX level for regime classification.
    """
    resolved_config = get_config_for_ticker(ticker, CONFIG, timeframe=timeframe, index_id=index_id)
    strategy = resolved_config.get("strategy", "mr")
    if strategy == "hybrid":
        from src.hybrid import evaluate_hybrid
        return evaluate_hybrid(
            df, ticker, config=resolved_config,
            ignore_volatility=ignore_volatility,
            news_sentiment=news_sentiment, expert_sentiment=expert_sentiment,
            timeframe=timeframe, vix=vix,
        )
    if strategy == "tf":
        from src.signals_trend import evaluate_breakout_signal
        tf_cfg = resolved_config.get("trend_following", {})
        return evaluate_breakout_signal(
            df, ticker,
            donchian_period=tf_cfg.get("donchian_period", 20),
            atr_period=tf_cfg.get("atr_period", 14),
            adx_period=tf_cfg.get("adx_period", 14),
            adx_threshold=tf_cfg.get("adx_threshold", 25),
            config=resolved_config,
        )
    return evaluate_signal(
        df,
        ticker,
        **_indicator_params(),
        ignore_volatility=ignore_volatility,
        config=resolved_config,
        news_sentiment=news_sentiment,
        expert_sentiment=expert_sentiment,
        timeframe=timeframe,
        vix=vix,
    )


def _attach_graham_value(signal: Signal, ticker: str) -> None:
    """Compute Graham intrinsic value and attach to signal (display-only)."""
    graham_cfg = CONFIG.get("graham", {})
    if not graham_cfg.get("enabled", True):
        return
    fundamentals = fetch_fundamentals(ticker)
    if fundamentals is None:
        return
    bond_yield = fetch_bond_yield()
    if bond_yield is None:
        bond_yield = graham_cfg.get("default_bond_yield", 0.045)
    from src.intrinsic import compute_graham_value, classify_valuation
    iv = compute_graham_value(fundamentals["eps"], fundamentals["growth_rate"], bond_yield)
    if iv is None:
        return
    label, margin = classify_valuation(signal.price, iv)
    signal.intrinsic_value = iv
    signal.margin_of_safety = margin
    signal.valuation_label = label


def run_stock(
    ticker: str,
    ignore_volatility: bool = False,
    timeframe: str | None = None,
    show_breakdown: bool = False,
    include_news: bool = True,
    return_df: bool = False,
) -> discord.Embed | tuple[discord.Embed, pd.DataFrame | None]:
    """Fetch data for a single ticker, compute signals, return Discord Embed.
    When return_df=True, returns (embed, df) for chart generation."""
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    ticker = ticker.upper().strip()[:10]
    display_ticker = sanitize_for_discord(ticker)
    if not ticker:
        emb = discord.Embed(
            title="Stock – Error",
            description="Please provide a valid ticker symbol.",
            color=0x808080,
        )
        return (emb, None) if return_df else emb

    logger.info("Running stock quote for %s...", ticker)

    news_cfg = CONFIG.get("news", {})
    news_weight = news_cfg.get("weight") or CONFIG.get("indicator_weights", {}).get("news", 0)
    news_enabled = include_news and news_cfg.get("enabled", False) and (news_weight is None or float(news_weight) > 0)
    news_headlines: list[dict] = []
    news_sentiment: float | None = None
    if news_enabled:
        max_hl = news_cfg.get("max_headlines", 5)
        news_headlines = fetch_news(ticker, count=max_hl)
        if news_headlines:
            provider = news_cfg.get("sentiment_provider", "vader")
            news_sentiment = compute_sentiment(news_headlines, provider=provider)

    # Default: daily only, no multi-timeframe
    if timeframe is None:
        ohlcv = fetch_ohlcv(symbols=[ticker], period=PERIOD, interval="1d")
        if not ohlcv or ticker not in ohlcv:
            df_fallback = fetch_single(ticker, period=PERIOD, interval="1d")
            if df_fallback is not None and not df_fallback.empty:
                ohlcv = {ticker: df_fallback}
        if not ohlcv or ticker not in ohlcv:
            emb = discord.Embed(
                title=f"{display_ticker} – No data",
                description=f"No data found for '{display_ticker}'. Check the symbol and try again.",
                color=0x808080,
            )
            return (emb, None) if return_df else emb
        df = ohlcv[ticker]
        resolved_config = get_config_for_ticker(ticker, CONFIG, timeframe="Daily")
        if resolved_config.get("strategy") == "tf":
            # TF: only compute atr_pct for daily range display
            from src.indicators import compute_atr, compute_atr_pct
            atr_s = compute_atr(df["High"], df["Low"], df["Close"], window=14)
            atr_pct_s = compute_atr_pct(df["Close"], atr_s)
            indicators = {"atr_pct": float(atr_pct_s.iloc[-1]) if len(atr_pct_s) > 0 else 0.0}
        else:
            ip = _indicator_params()
            indicators = get_latest_indicators(
                df,
                **{k: ip[k] for k in _INDICATOR_KEYS_FOR_INDICATORS},
            )
        if indicators is None:
            emb = discord.Embed(
                title=f"{display_ticker} – No data",
                description=f"No data found for '{display_ticker}'. Check the symbol and try again.",
                color=0x808080,
            )
            return (emb, None) if return_df else emb
        vix = fetch_vix_current()
        expert_sentiments = get_expert_sentiments([ticker], CONFIG)
        expert_sentiment = expert_sentiments.get((ticker or "").upper().strip())
        signal = _eval_signal_for_df(df, ticker, ignore_volatility, news_sentiment=news_sentiment, expert_sentiment=expert_sentiment, timeframe="Daily", vix=vix)
        if signal is None:
            emb = discord.Embed(
                title=f"{display_ticker} – No data",
                description=f"No data found for '{display_ticker}'. Check the symbol and try again.",
                color=0x808080,
            )
            return (emb, None) if return_df else emb
        _attach_graham_value(signal, ticker)
        markets = get_stock_exchange(ticker)
        embed_dict = format_stock_embed(
            display_ticker, signal, indicators, config=CONFIG, markets=markets,
            ignore_volatility=ignore_volatility,
            news_headlines=news_headlines if news_cfg.get("show_in_stock", True) else None,
            timeframe="Daily",
            show_breakdown=show_breakdown,
        )
        embed = discord.Embed(
            title=embed_dict["title"],
            description=embed_dict["description"],
            color=embed_dict["color"],
        )
        embed.set_footer(text=embed_dict.get("footer", {}).get("text", ""))
        for field in embed_dict.get("fields", []):
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", False),
            )
        return (embed, df) if return_df else embed

    # Timeframe chosen: fetch only the selected timeframe, show that analysis
    if timeframe not in ("1d", "1wk", "1h"):
        timeframe = "1d"

    primary_period, primary_interval = TF_PERIOD_INTERVAL[timeframe]
    tf_labels = {"1d": "Daily", "1wk": "1W", "1h": "1H"}

    ohlcv_primary = fetch_ohlcv(symbols=[ticker], period=primary_period, interval=primary_interval)
    if not ohlcv_primary or ticker not in ohlcv_primary:
        emb = discord.Embed(
            title=f"{display_ticker} – No data",
            description=f"No data found for '{display_ticker}'. Check the symbol and try again.",
            color=0x808080,
        )
        return (emb, None) if return_df else emb
    df_primary = ohlcv_primary[ticker]
    tf_label = tf_labels[timeframe]
    vix = fetch_vix_current()
    expert_sentiments_tf = get_expert_sentiments([ticker], CONFIG)
    expert_sentiment_tf = expert_sentiments_tf.get((ticker or "").upper().strip())
    signal_primary = _eval_signal_for_df(df_primary, ticker, ignore_volatility, news_sentiment=news_sentiment, expert_sentiment=expert_sentiment_tf, timeframe=tf_label, vix=vix)
    if signal_primary is None:
        emb = discord.Embed(
            title=f"{display_ticker} – No data",
            description=f"No data found for '{display_ticker}'. Check the symbol and try again.",
            color=0x808080,
        )
        return (emb, None) if return_df else emb

    resolved_config_tf = get_config_for_ticker(ticker, CONFIG, timeframe=tf_label)
    if resolved_config_tf.get("strategy") == "tf":
        from src.indicators import compute_atr, compute_atr_pct
        atr_s = compute_atr(df_primary["High"], df_primary["Low"], df_primary["Close"], window=14)
        atr_pct_s = compute_atr_pct(df_primary["Close"], atr_s)
        indicators = {"atr_pct": float(atr_pct_s.iloc[-1]) if len(atr_pct_s) > 0 else 0.0}
    else:
        ip = _indicator_params()
        indicators = get_latest_indicators(
            df_primary,
            **{k: ip[k] for k in _INDICATOR_KEYS_FOR_INDICATORS},
        )
    _attach_graham_value(signal_primary, ticker)
    markets = get_stock_exchange(ticker)
    embed_dict = format_stock_embed(
        display_ticker,
        signal_primary,
        indicators,
        config=CONFIG,
        markets=markets,
        ignore_volatility=ignore_volatility,
        news_headlines=news_headlines if news_cfg.get("show_in_stock", True) else None,
        timeframe=tf_label,
        show_breakdown=show_breakdown,
    )
    embed = discord.Embed(
        title=embed_dict["title"],
        description=embed_dict["description"],
        color=embed_dict["color"],
    )
    embed.set_footer(text=embed_dict.get("footer", {}).get("text", ""))
    for field in embed_dict.get("fields", []):
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field.get("inline", False),
        )
    return (embed, df_primary) if return_df else embed


def run_daytrade(
    ticker: str,
    return_chart_data: bool = False,
) -> discord.Embed | tuple[discord.Embed, pd.DataFrame | None, dict]:
    """
    Realtime stop-loss and take-profit suggestions for a ticker.
    Only runs when market is open; uses 1h intraday data.
    When return_chart_data=True, returns (embed, df, levels) for chart generation.
    """
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    ticker = ticker.upper().strip()[:10]
    display_ticker = sanitize_for_discord(ticker)
    if not ticker:
        emb = discord.Embed(
            title="Daytrade – Error",
            description="Please provide a valid ticker symbol.",
            color=0x808080,
        )
        return (emb, None, {}) if return_chart_data else emb

    if not is_market_open():
        emb = discord.Embed(
            title=f"Daytrade – {display_ticker}",
            description=f"Market is closed. Use `/stock {display_ticker}` for daily analysis.",
            color=0x808080,
        )
        return (emb, None, {}) if return_chart_data else emb

    logger.info("Running daytrade for %s...", ticker)
    ohlcv = fetch_ohlcv(symbols=[ticker], period="5d", interval="1h")
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    if not ohlcv or ticker not in ohlcv:
        emb = discord.Embed(
            title=f"Daytrade – {display_ticker}",
            description=f"No intraday data for '{display_ticker}'. Check the symbol and try again.",
            color=0x808080,
        )
        return (emb, None, {}) if return_chart_data else emb
    df = ohlcv[ticker]
    ip = _indicator_params()
    indicators = get_latest_indicators(
        df,
        **{k: ip[k] for k in _INDICATOR_KEYS_FOR_INDICATORS},
    )
    if indicators is None:
        emb = discord.Embed(
            title=f"Daytrade – {display_ticker}",
            description=f"No intraday data for '{display_ticker}'. Check the symbol and try again.",
            color=0x808080,
        )
        return (emb, None, {}) if return_chart_data else emb
    vix = fetch_vix_current()
    expert_sentiments_dt = get_expert_sentiments([ticker], CONFIG)
    expert_sentiment_dt = expert_sentiments_dt.get((ticker or "").upper().strip())
    signal = _eval_signal_for_df(df, ticker, ignore_volatility=False, expert_sentiment=expert_sentiment_dt, timeframe="1H", vix=vix)
    if signal is None:
        emb = discord.Embed(
            title=f"Daytrade – {display_ticker}",
            description=f"No intraday data for '{display_ticker}'. Check the symbol and try again.",
            color=0x808080,
        )
        return (emb, None, {}) if return_chart_data else emb
    price = signal.price
    atr = indicators.get("atr", 0.0) or 0.0
    levels = compute_daytrade_levels(price, atr, CONFIG)
    embed_dict = format_daytrade_embed(
        display_ticker, signal, indicators, levels, CONFIG
    )
    embed = discord.Embed(
        title=embed_dict["title"],
        description=embed_dict["description"],
        color=embed_dict["color"],
    )
    embed.set_footer(text=embed_dict.get("footer", {}).get("text", ""))
    for field in embed_dict.get("fields", []):
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field.get("inline", False),
        )
    return (embed, df, levels) if return_chart_data else embed


def run_watchlist_recap(
    user_id: int,
    guild_id: int | None,
    ignore_volatility: bool = False,
    timeframe: str = "Daily",
    show_breakdown: bool = False,
) -> discord.Embed:
    """Fetch user's watchlist, compute signals, return Discord Embed (grouped BUY/SELL/HOLD, no RSI in HOLD)."""
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    tickers = get_tickers(user_id, guild_id)
    if not tickers:
        return discord.Embed(
            title="Watchlist Recap",
            description="Your watchlist is empty. Use `/watchlist add AAPL` to add tickers.",
            color=0x808080,
        )

    logger.info("Running watchlist recap for user %s (%d tickers)...", user_id, len(tickers))
    wl_period, wl_interval = _resolve_period_interval(timeframe, PERIOD)
    ohlcv = fetch_ohlcv(symbols=tickers, period=wl_period, interval=wl_interval)
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    if not ohlcv:
        return discord.Embed(
            title="Watchlist Recap - Error",
            description="Could not fetch market data. Please try again later.",
            color=0x808080,
        )

    news_sentiments: dict[str, float] = {}
    news_labels: dict[str, str] = {}
    news_cfg = CONFIG.get("news", {})
    if news_cfg.get("enabled", False):
        news_sentiments, news_labels = _fetch_news_for_recap(tickers)

    vix = fetch_vix_current()
    expert_sentiments = get_expert_sentiments(list(ohlcv.keys()), CONFIG)
    signals = evaluate_all(
        ohlcv,
        **_indicator_params(),
        ignore_volatility=ignore_volatility,
        config=CONFIG,
        news_sentiments=news_sentiments,
        expert_sentiments=expert_sentiments,
        timeframe=timeframe,
        vix=vix,
    )

    expert_labels = {sym: sentiment_label(s) for sym, s in expert_sentiments.items()}
    embed_dict = format_recap_embed(
        signals,
        include_hold=True,
        min_confidence=0,
        index_name="Watchlist",
        ignore_volatility=ignore_volatility,
        include_rsi_in_hold=False,
        timeframe=timeframe,
        news_labels=news_labels,
        expert_labels=expert_labels,
        show_breakdown=show_breakdown,
    )
    embed = discord.Embed(
        title=embed_dict["title"],
        description=embed_dict["description"],
        color=embed_dict["color"],
    )
    embed.set_footer(text=embed_dict.get("footer", {}).get("text", "Watchlist | RSI, MACD, BB, SuperTrend, Stochastic, Williams %R, EMA"))
    return embed


def run_watchlist_news(user_id: int, guild_id: int | None) -> discord.Embed:
    """Fetch news for all watchlist tickers and return Discord Embed with one field per ticker."""
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    tickers = get_tickers(user_id, guild_id)
    if not tickers:
        return discord.Embed(
            title="Watchlist News",
            description="Your watchlist is empty. Use `/watchlist add AAPL` to add tickers.",
            color=0x808080,
        )

    news_cfg = CONFIG.get("news", {})
    if not news_cfg.get("enabled", False):
        return discord.Embed(
            title="Watchlist News",
            description="News is disabled in bot configuration.",
            color=0x808080,
        )

    max_hl = news_cfg.get("max_headlines", 5)
    provider = news_cfg.get("sentiment_provider", "vader")
    max_items_per_ticker = 3  # Keep fields compact for multi-ticker view
    max_tickers = 10  # Limit to avoid very long responses

    embed = discord.Embed(
        title="Watchlist News",
        description=f"News for your watchlisted tickers ({len(tickers)} total).",
        color=0x2196F3,
    )

    for ticker in tickers[:max_tickers]:
        if is_stop_requested():
            clear_stop()
            raise StopRequested()
        try:
            headlines = fetch_news(ticker, count=max_hl)
            if not headlines:
                embed.add_field(
                    name=sanitize_for_discord(ticker),
                    value="_No recent news._",
                    inline=False,
                )
                continue
            sentiment = compute_sentiment(headlines, provider=provider)
            sentiment_str = sentiment_label(sentiment)
            body = format_headlines_for_embed(headlines, ticker, max_items=max_items_per_ticker)
            value = body + f"\n_Sentiment: {sentiment_str}_"
            if len(value) > 1024:
                value = value[:1021] + "…"
            embed.add_field(
                name=sanitize_for_discord(ticker),
                value=value,
                inline=False,
            )
        except Exception as e:
            logger.warning("Failed to fetch news for %s in watchlist news: %s", ticker, e)
            embed.add_field(
                name=sanitize_for_discord(ticker),
                value="_Failed to fetch news._",
                inline=False,
            )

    if len(tickers) > max_tickers:
        embed.set_footer(text=f"Showing first {max_tickers} of {len(tickers)} tickers.")
    return embed


class IndexSelectView(discord.ui.View):
    """View with buttons for selecting an index when multiple match."""

    def __init__(
        self,
        matches: list,
        timeout: float = 120.0,
        ignore_volatility: bool = False,
        timeframe: str = "Daily",
        show_breakdown: bool = False,
    ):
        super().__init__(timeout=timeout)
        self.ignore_volatility = ignore_volatility
        self.timeframe = timeframe
        self.show_breakdown = show_breakdown
        for m in matches[:5]:  # Max 5 buttons
            self.add_item(
                IndexSelectButton(
                    m,
                    ignore_volatility=ignore_volatility,
                    timeframe=timeframe,
                    show_breakdown=show_breakdown,
                )
            )
        self.add_item(IndexCancelButton())

    def disable_all(self):
        """Disable all buttons (grey out) while loading."""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def on_timeout(self):
        """Disable buttons when view times out."""
        self.disable_all()
        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            pass


class IndexCancelButton(discord.ui.Button):
    """Cancel button - must have explicit custom_id to avoid conflicts."""

    def __init__(self):
        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="index_select_cancel",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Cancelled.", view=None)


async def _animate_loading_dots(interaction: discord.Interaction, base_text: str, view: discord.ui.View):
    """Cycle loading dots (., .., ...) until cancelled."""
    dots = [".", "..", "..."]
    i = 0
    try:
        while True:
            await interaction.edit_original_response(
                content=f"{base_text}{dots[i % 3]}",
                view=view,
            )
            i += 1
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass


class IndexSelectButton(discord.ui.Button):
    """Button that runs market recap for the selected index."""

    def __init__(
        self,
        match,
        ignore_volatility: bool = False,
        timeframe: str = "Daily",
        show_breakdown: bool = False,
    ):
        super().__init__(
            label=match.name[:80],
            custom_id=match.id,
            style=discord.ButtonStyle.primary,
        )
        self._match = match
        self._ignore_volatility = ignore_volatility
        self._timeframe = timeframe
        self._show_breakdown = show_breakdown

    async def callback(self, interaction: discord.Interaction):
        async def deliver(emb, err):
            try:
                if emb is not None:
                    await interaction.edit_original_response(embed=emb, view=None)
                else:
                    await interaction.edit_original_response(
                        content=err or "An error occurred while generating the recap.",
                        embed=None,
                        view=None,
                    )
            except discord.NotFound:
                logger.warning("Market button interaction expired before delivery")

        job = RecapJob(
            job_type="market",
            params={
                "index_id": self._match.id,
                "index_name": self._match.name,
                "ignore_volatility": self._ignore_volatility,
                "timeframe": self._timeframe,
                "show_breakdown": self._show_breakdown,
            },
            deliver=deliver,
        )
        pos = enqueue_recap(job)
        if pos is None:
            await interaction.response.send_message(
                f"Recap queue is full (max {RECAP_QUEUE_MAX}). Try again in a moment.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        if isinstance(self.view, IndexSelectView):
            self.view.disable_all()
            await interaction.edit_original_response(view=self.view)
        if pos > 1:
            await interaction.edit_original_response(
                content=f"Your recap is queued (position {pos}). Processing...",
                view=self.view,
            )


@bot.slash_command(description="Learn how the bot works and reasons", **_slash_kwargs())
async def tutorial(ctx):
    """Slash command: tutorial on bot functionality."""
    await ctx.respond(embed=build_tutorial_embed())


@bot.slash_command(description="List all markets, indexes, and supported stock count", **_slash_kwargs())
async def supported(ctx):
    """Slash command: list supported markets, indexes, and total stock count."""
    summary = get_supported_summary()
    markets_lines = [f"**{name}**: {', '.join(indices)}" for name, indices in summary["markets"]]
    indexes_lines = [f"{name}: {count}" for name, count in summary["indexes"]]
    embed = discord.Embed(
        title="Supported Markets & Indexes",
        description="Markets and indexes the bot can analyze for recaps and signals.",
        color=0x2196F3,
    )
    embed.add_field(name="Markets", value="\n".join(markets_lines) or "—", inline=False)
    embed.add_field(name="Indexes", value="\n".join(indexes_lines) or "—", inline=False)
    embed.set_footer(text=f"Total unique stocks supported: {summary['total_unique']}")
    await ctx.respond(embed=embed)


@bot.slash_command(description="Get a market recap of large-cap stocks", **_slash_kwargs())
async def recap(
    ctx,
    volatility: discord.Option(
        str,
        "Volatility",
        choices=[
            discord.OptionChoice("Considered", "considered"),
            discord.OptionChoice("Not considered", "not_considered"),
        ],
        default="considered",
    ),
    timeframe: discord.Option(
        str,
        "Timeframe for analysis",
        choices=[
            discord.OptionChoice("Daily", "Daily"),
            discord.OptionChoice("Weekly", "1W"),
        ],
        default="Daily",
    ),
    breakdown: discord.Option(
        str,
        "Indicator breakdown",
        name="breakdown",
        choices=[
            discord.OptionChoice("Yes", "yes"),
            discord.OptionChoice("No", "no"),
        ],
        default="no",
    ) = "no",
):
    """Slash command: on-demand market recap (S&P 100 only)."""
    ignore_volatility = volatility == "not_considered"
    show_breakdown = breakdown == "yes"

    async def deliver(emb, err):
        try:
            if emb is not None:
                await ctx.edit(embed=emb)
            else:
                await ctx.edit(content=err or "An error occurred while generating the recap.")
        except discord.NotFound:
            logger.warning("Recap interaction expired before delivery")

    job = RecapJob(
        job_type="recap",
        params={
            "ignore_volatility": ignore_volatility,
            "timeframe": timeframe,
            "show_breakdown": show_breakdown,
        },
        deliver=deliver,
    )
    pos = enqueue_recap(job)
    if pos is None:
        await ctx.respond(
            f"Recap queue is full (max {RECAP_QUEUE_MAX}). Try again in a moment.",
            ephemeral=True,
        )
        return
    await ctx.defer()
    if pos > 1:
        await ctx.edit(content=f"Your recap is queued (position {pos}). Processing...")


@bot.slash_command(
    description="Get market recap for an index (by name or country)",
    **_slash_kwargs(),
)
async def market(
    ctx,
    query: discord.Option(str, "Index name or country (e.g. S&P 500, USA, Germany)"),
    volatility: discord.Option(
        str,
        "Volatility",
        choices=[
            discord.OptionChoice("Considered", "considered"),
            discord.OptionChoice("Not considered", "not_considered"),
        ],
        default="considered",
    ),
    timeframe: discord.Option(
        str,
        "Timeframe for analysis",
        choices=[
            discord.OptionChoice("Daily", "Daily"),
            discord.OptionChoice("Weekly", "1W"),
        ],
        default="Daily",
    ),
    breakdown: discord.Option(
        str,
        "Indicator breakdown",
        name="breakdown",
        choices=[
            discord.OptionChoice("Yes", "yes"),
            discord.OptionChoice("No", "no"),
        ],
        default="no",
    ) = "no",
):
    """Slash command: market recap for a specific index."""
    ignore_volatility = volatility == "not_considered"
    show_breakdown = breakdown == "yes"
    await ctx.defer()
    try:
        query = (query or "")[:100].strip()
        if not query:
            await ctx.edit(content="Please enter an index name or country (e.g. S&P 500, USA, Germany).")
            return

        matches = resolve_input(query)
        safe_query = sanitize_for_discord(query)
        if not matches:
            await ctx.edit(content=f"No index found for '{safe_query}'. Try an index name (e.g. S&P 500, DAX) or country (e.g. USA, Germany).")
            return

        if len(matches) == 1:
            m = matches[0]
            constituents = get_constituents(m.id)
            if not constituents:
                await ctx.edit(content=f"No constituent data for {m.name}. This index is not supported yet.")
                return

            async def deliver(emb, err):
                try:
                    if emb is not None:
                        await ctx.edit(embed=emb)
                    else:
                        await ctx.edit(content=err or "An error occurred while generating the recap.")
                except discord.NotFound:
                    logger.warning("Market recap interaction expired before delivery")

            job = RecapJob(
                job_type="market",
                params={
                    "index_id": m.id,
                    "index_name": m.name,
                    "ignore_volatility": ignore_volatility,
                    "timeframe": timeframe,
                    "show_breakdown": show_breakdown,
                },
                deliver=deliver,
            )
            pos = enqueue_recap(job)
            if pos is None:
                await ctx.edit(content=f"Recap queue is full (max {RECAP_QUEUE_MAX}). Try again in a moment.")
                return
            if pos > 1:
                await ctx.edit(content=f"Your recap is queued (position {pos}). Processing...")
        else:
            view = IndexSelectView(
                matches,
                ignore_volatility=ignore_volatility,
                timeframe=timeframe,
                show_breakdown=show_breakdown,
            )
            await ctx.edit(
                content=f"Multiple indices found for '{safe_query}'. Choose one:",
                view=view,
            )
    except Exception:
        logger.exception("Market command failed")
        await ctx.edit(content="An error occurred. Please try again later.")


@bot.slash_command(
    description="Get a stock rundown with plain-English indicator summary",
    **_slash_kwargs(),
)
async def stock(
    ctx,
    ticker: discord.Option(str, "Stock ticker (e.g. AAPL, MSFT)"),
    volatility: discord.Option(
        str,
        "Volatility",
        choices=[
            discord.OptionChoice("Considered", "considered"),
            discord.OptionChoice("Not considered", "not_considered"),
        ],
        default="considered",
    ),
    timeframe: discord.Option(
        str,
        "Timeframe-based analysis",
        choices=[
            discord.OptionChoice("1 week", "1wk"),
            discord.OptionChoice("1 day", "1d"),
            discord.OptionChoice("1 hour", "1h"),
        ],
        required=False,
        default=None,
    ),
    breakdown: discord.Option(
        str,
        "Indicator breakdown",
        name="breakdown",
        choices=[
            discord.OptionChoice("Yes", "yes"),
            discord.OptionChoice("No", "no"),
        ],
        default="no",
    ) = "no",
):
    """Slash command: single-stock quote with bullet points and one-line summary."""
    ignore_volatility = volatility == "not_considered"
    show_breakdown = breakdown == "yes"
    await ctx.defer()
    try:
        ticker = (ticker or "").strip()[:10]
        if not ticker:
            await ctx.edit(content="Please provide a ticker symbol (e.g. AAPL, MSFT).")
            return

        loop = asyncio.get_event_loop()
        embed = await loop.run_in_executor(
            _executor,
            lambda t=ticker, ig=ignore_volatility, tf=timeframe, sb=show_breakdown: (
                clear_stop(),
                run_stock(t, ignore_volatility=ig, timeframe=tf, show_breakdown=sb),
            )[1],
        )
        await ctx.edit(embed=embed)
    except StopRequested:
        logger.info("Stock command stopped by user")
        await ctx.edit(content="Operation stopped.")
    except Exception:
        logger.exception("Stock command failed")
        await ctx.edit(content="An error occurred. Please try again later.")


@bot.slash_command(
    description="Stock chart with signal (no news). Same as /stock but chart always included.",
    **_slash_kwargs(),
)
async def stockchart(
    ctx,
    ticker: discord.Option(str, "Stock ticker (e.g. AAPL, MSFT)"),
    volatility: discord.Option(
        str,
        "Volatility",
        choices=[
            discord.OptionChoice("Considered", "considered"),
            discord.OptionChoice("Not considered", "not_considered"),
        ],
        default="considered",
    ),
    timeframe: discord.Option(
        str,
        "Timeframe-based analysis",
        choices=[
            discord.OptionChoice("1 week", "1wk"),
            discord.OptionChoice("1 day", "1d"),
            discord.OptionChoice("1 hour", "1h"),
        ],
        required=False,
        default=None,
    ),
    breakdown: discord.Option(
        str,
        "Indicator breakdown",
        name="breakdown",
        choices=[
            discord.OptionChoice("Yes", "yes"),
            discord.OptionChoice("No", "no"),
        ],
        default="no",
    ) = "no",
):
    """Slash command: stock rundown with chart, no news."""
    ignore_volatility = volatility == "not_considered"
    show_breakdown = breakdown == "yes"
    await ctx.defer()
    try:
        ticker = (ticker or "").strip()[:10]
        if not ticker:
            await ctx.edit(content="Please provide a ticker symbol (e.g. AAPL, MSFT).")
            return

        loop = asyncio.get_event_loop()
        tf = timeframe or "1d"
        result = await loop.run_in_executor(
            _executor,
            lambda t=ticker, ig=ignore_volatility, tf=tf, sb=show_breakdown: (
                clear_stop(),
                run_stock(t, ignore_volatility=ig, timeframe=tf, show_breakdown=sb, include_news=False, return_df=True),
            )[1],
        )
        embed, df = result if isinstance(result, tuple) else (result, None)
        charts_cfg = CONFIG.get("charts", {})
        if charts_cfg.get("enabled", True) and df is not None:
            indicators = charts_cfg.get("stock_indicators", ["supertrend"])
            chart_bytes = build_stock_chart(df, ticker, indicators, CONFIG, timeframe=tf)
            if chart_bytes:
                buf = io.BytesIO(chart_bytes)
                buf.seek(0)
                file = discord.File(fp=buf, filename="chart.png")
                embed.set_image(url="attachment://chart.png")
                await ctx.edit(embed=embed, file=file)
            else:
                await ctx.edit(embed=embed)
        else:
            await ctx.edit(embed=embed)
    except StopRequested:
        logger.info("Stockchart command stopped by user")
        await ctx.edit(content="Operation stopped.")
    except Exception:
        logger.exception("Stockchart command failed")
        await ctx.edit(content="An error occurred. Please try again later.")


@bot.slash_command(
    description="Realtime stop-loss and take-profit suggestions (market hours only)",
    **_slash_kwargs(),
)
async def daytrade(
    ctx,
    ticker: discord.Option(str, "Stock ticker (e.g. AAPL, MSFT)"),
):
    """Slash command: daytrade suggestions when market is open."""
    await ctx.defer()
    try:
        ticker = (ticker or "").strip()[:10]
        if not ticker:
            await ctx.edit(content="Please provide a ticker symbol (e.g. AAPL, MSFT).")
            return

        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(
            _executor,
            lambda t=ticker: (clear_stop(), run_daytrade(t, return_chart_data=True))[1],
        )
        embed, df, levels = out if isinstance(out, tuple) else (out, None, {})
        charts_cfg = CONFIG.get("charts", {})
        if charts_cfg.get("enabled", True) and df is not None:
            chart_bytes = build_daytrade_chart(df, ticker, levels or {}, CONFIG, timeframe="1h")
            if chart_bytes:
                buf = io.BytesIO(chart_bytes)
                buf.seek(0)
                file = discord.File(fp=buf, filename="chart.png")
                embed.set_image(url="attachment://chart.png")
                await ctx.edit(embed=embed, file=file)
            else:
                await ctx.edit(embed=embed)
        else:
            await ctx.edit(embed=embed)
    except StopRequested:
        logger.info("Daytrade command stopped by user")
        await ctx.edit(content="Operation stopped.")
    except Exception:
        logger.exception("Daytrade command failed")
        await ctx.edit(content="An error occurred. Please try again later.")


def _resolve_period_interval(timeframe: str | None, period: str) -> tuple[str, str]:
    """Map timeframe to (period, interval). Ensures 1W/1H use correct bar intervals."""
    tf = timeframe or "Daily"
    if tf == "Daily":
        return (period, "1d")
    if tf == "1W":
        # Weekly: need ~60+ bars for warmup; 3mo=13, 6mo=26, 1y=52, 2y=104
        if period in ("3mo", "6mo", "1y"):
            return ("2y", "1wk")
        return (period, "1wk")
    if tf == "1H":
        return ("2y", "1h")  # yfinance 1h max ~730 days
    return (period, "1d")


def run_backtest(
    ticker: str,
    period: str = "1y",
    ignore_volatility: bool = False,
    timeframe: str | None = None,
    mode: str = "walk_forward",
    return_result: bool = False,
) -> discord.Embed | tuple[discord.Embed, BacktestResult | None, pd.DataFrame | None]:
    """Run backtest for a ticker and return Discord Embed. When return_result=True, returns (embed, result, df)."""
    ticker = ticker.upper().strip()[:10]
    display_ticker = sanitize_for_discord(ticker)
    bt_period, bt_interval = _resolve_period_interval(timeframe, period)

    resolved_config = get_config_for_ticker(ticker, CONFIG, timeframe=timeframe)
    result: BacktestResult | None = None
    if mode == "simple":
        result = run_backtest_engine(
            ticker,
            period=bt_period,
            interval=bt_interval,
            config=resolved_config,
            ignore_volatility=ignore_volatility,
            timeframe=timeframe,
        )
        if result is None:
            emb = discord.Embed(
                title=f"Backtest – {display_ticker}",
                description=f"No data or insufficient history for '{display_ticker}'. Try a different ticker or period.",
                color=0x808080,
            )
            return (emb, None, None) if return_result else emb
        embed_dict = format_backtest_embed(result, display_ticker, timeframe=timeframe)
    else:
        wf_cfg = CONFIG.get("walk_forward", {})
        tf_overrides = wf_cfg.get("timeframe_overrides", {}).get(timeframe or "Daily", {})
        wf_results = run_walk_forward_optimization(
            ticker,
            config=resolved_config,
            period=bt_period,
            interval=bt_interval,
            train_bars=tf_overrides.get("train_bars", wf_cfg.get("train_bars", 504)),
            test_bars=tf_overrides.get("test_bars", wf_cfg.get("test_bars", 63)),
            step_bars=tf_overrides.get("step_bars", wf_cfg.get("step_bars", 63)),
            embargo_bars=wf_cfg.get("embargo_bars", 5),
            param_grid=tf_overrides.get("param_grid", wf_cfg.get("param_grid")),
            optimize_metric=wf_cfg.get("optimize_metric", "outperformance"),
            ignore_volatility=ignore_volatility,
            timeframe=timeframe,
        )
        embed_dict = format_walk_forward_embed(wf_results, display_ticker, timeframe=timeframe)
        if not wf_results:
            emb = discord.Embed(
                title=embed_dict["title"],
                description=embed_dict["description"],
                color=embed_dict["color"],
            )
            return (emb, None, None) if return_result else emb
        result = wf_results[-1].oos_result

    embed = discord.Embed(
        title=embed_dict["title"],
        description=embed_dict["description"],
        color=embed_dict["color"],
    )
    for field in embed_dict.get("fields", []):
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field.get("inline", False),
        )
    if return_result and result is not None:
        df = fetch_single(ticker, period=bt_period, interval=bt_interval)
        return (embed, result, df)
    return embed


@bot.slash_command(
    description="Stop the current operation (backtest, recap, etc.)",
    **_slash_kwargs(),
)
async def stop(ctx):
    """Slash command: interrupt and stop whatever the bot is doing."""
    request_stop()
    logger.info("Stop requested by user %s", ctx.author)
    await ctx.respond("Stopping current operation...", ephemeral=False)


@bot.slash_command(
    name="indicatorbacktest",
    description="Backtest the indicator strategy on historical data for a ticker",
    **_slash_kwargs(),
)
async def indicator_backtest(
    ctx,
    ticker: discord.Option(str, "Stock ticker (e.g. AAPL, MSFT)"),
    period: discord.Option(
        str,
        "Backtest period (required)",
        choices=[
            discord.OptionChoice("6 months", "6mo"),
            discord.OptionChoice("1 year", "1y"),
            discord.OptionChoice("2 years", "2y"),
            discord.OptionChoice("3 years", "3y"),
        ],
        required=True,
    ),
    volatility: discord.Option(
        str,
        "Volatility",
        choices=[
            discord.OptionChoice("Considered", "considered"),
            discord.OptionChoice("Not considered", "not_considered"),
        ],
        default="considered",
    ),
    timeframe: discord.Option(
        str,
        "Strategy timeframe",
        choices=[
            discord.OptionChoice("Daily", "Daily"),
            discord.OptionChoice("1W", "1W"),
            discord.OptionChoice("1H", "1H"),
        ],
        default="Daily",
    ),
    mode: discord.Option(
        str,
        "Backtest mode",
        choices=[
            discord.OptionChoice("Walk-forward", "walk_forward"),
            discord.OptionChoice("Simple", "simple"),
        ],
        default="walk_forward",
    ),
):
    """Slash command: backtest strategy on historical data."""
    global _backtest_in_progress
    if _backtest_in_progress:
        await ctx.respond(
            "A backtest is already running. Please wait for it to finish or use /stop to cancel.",
            ephemeral=True,
        )
        return
    _backtest_in_progress = True
    await ctx.defer()
    try:
        ticker = (ticker or "").strip()[:10]
        if not ticker:
            await ctx.edit(content="Please provide a ticker symbol (e.g. AAPL, MSFT).")
            return

        logger.info("Backtest command: %s | period=%s | mode=%s | timeframe=%s", ticker, period, mode, timeframe)
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(
            _executor,
            lambda: (clear_stop(), run_backtest(
                ticker,
                period=period,
                ignore_volatility=volatility == "not_considered",
                timeframe=timeframe,
                mode=mode,
                return_result=True,
            ))[1],
        )
        embed, result, df = out if isinstance(out, tuple) else (out, None, None)
        charts_cfg = CONFIG.get("charts", {})
        if charts_cfg.get("enabled", True) and result is not None and df is not None and result.trades:
            chart_bytes = build_equity_chart(result.trades, df, ticker, CONFIG)
            if chart_bytes:
                buf = io.BytesIO(chart_bytes)
                buf.seek(0)
                file = discord.File(fp=buf, filename="chart.png")
                embed.set_image(url="attachment://chart.png")
                await ctx.edit(embed=embed, file=file)
            else:
                await ctx.edit(embed=embed)
        else:
            await ctx.edit(embed=embed)
    except StopRequested:
        logger.info("Backtest stopped by user")
        await ctx.edit(content="Operation stopped.")
    except Exception:
        logger.exception("Backtest command failed")
        await ctx.edit(content="An error occurred. Please try again later.")
    finally:
        _backtest_in_progress = False


def run_news(ticker: str | None = None) -> discord.Embed:
    """Fetch news and return Discord Embed. Filters by severity keyword analysis. When ticker is None, shows general market news."""
    if is_stop_requested():
        clear_stop()
        raise StopRequested()
    news_cfg = CONFIG.get("news", {})
    market_ticker = news_cfg.get("market_news_ticker", "SPY")
    if not ticker or not ticker.strip():
        ticker = market_ticker
        is_market = True
    else:
        ticker = ticker.upper().strip()[:10]
        is_market = False
    display_ticker = "Market" if is_market else sanitize_for_discord(ticker)

    # Fetch more headlines, then filter by severity keyword analysis
    raw_count = 15  # Pool size for severity filtering
    raw_headlines = fetch_news(ticker, count=raw_count)
    headlines = filter_by_severity(raw_headlines, min_severity=0.3, max_items=5)
    if not headlines:
        headlines = raw_headlines[:3]  # Fallback: show top 3 if none pass severity

    if not headlines:
        return discord.Embed(
            title="News – Market" if is_market else f"News – {display_ticker}",
            description="No recent news found." if is_market else f"No recent news found for {display_ticker}.",
            color=0x808080,
        )

    provider = news_cfg.get("sentiment_provider", "vader")
    sentiment = compute_sentiment(headlines, provider=provider)
    sentiment_str = sentiment_label(sentiment)

    body = format_headlines_for_embed(headlines, display_ticker, max_items=5)
    embed = discord.Embed(
        title="News – Market" if is_market else f"News – {display_ticker}",
        description=body,
        color=0x2196F3,
    )
    embed.add_field(name="Sentiment", value=sentiment_str, inline=True)
    return embed


@bot.slash_command(
    description="Get general stock market news (no ticker required)",
    **_slash_kwargs(),
)
async def news(
    ctx,
    ticker: discord.Option(str, "Optional: specific ticker (e.g. AAPL). Omit for general market news.", required=False, default=None),
):
    """Slash command: general market news filtered by severity. Optionally provide a ticker for stock-specific news."""
    await ctx.defer()
    try:
        loop = asyncio.get_event_loop()
        embed = await loop.run_in_executor(
            _executor,
            lambda t=ticker: (clear_stop(), run_news(t))[1],
        )
        await ctx.edit(embed=embed)
    except StopRequested:
        logger.info("News command stopped by user")
        await ctx.edit(content="Operation stopped.")
    except Exception:
        logger.exception("News command failed")
        await ctx.edit(content="An error occurred. Please try again later.")


watchlist_group = bot.create_group("watchlist", "Manage your stock watchlist", **_slash_kwargs())

@watchlist_group.command(name="add", description="Add a ticker to your watchlist", **_slash_kwargs())
async def watchlist_add(
    ctx,
    ticker: discord.Option(str, "Ticker symbol (e.g. AAPL)"),
):
    """Add a ticker to the user's watchlist."""
    ticker = (ticker or "").strip()[:10]
    if not ticker:
        await ctx.respond("Please provide a ticker symbol (e.g. AAPL, MSFT).", ephemeral=True)
        return

    guild_id = ctx.guild.id if ctx.guild else None
    ok, err = add_ticker(ctx.author.id, guild_id, ticker)
    if ok:
        display = sanitize_for_discord(ticker)
        await ctx.respond(f"Added `{display}` to your watchlist.", ephemeral=True)
    else:
        await ctx.respond(err, ephemeral=True)


@watchlist_group.command(name="remove", description="Remove a ticker from your watchlist", **_slash_kwargs())
async def watchlist_remove(
    ctx,
    ticker: discord.Option(str, "Ticker symbol to remove"),
):
    """Remove a ticker from the user's watchlist."""
    ticker = (ticker or "").strip()[:10]
    if not ticker:
        await ctx.respond("Please provide a ticker symbol to remove.", ephemeral=True)
        return

    guild_id = ctx.guild.id if ctx.guild else None
    ok, err = remove_ticker(ctx.author.id, guild_id, ticker)
    if ok:
        display = sanitize_for_discord(ticker)
        await ctx.respond(f"Removed `{display}` from your watchlist.", ephemeral=True)
    else:
        await ctx.respond(err, ephemeral=True)


@watchlist_group.command(name="list", description="Show your watchlist", **_slash_kwargs())
async def watchlist_list(ctx):
    """Show the user's watchlist."""
    guild_id = ctx.guild.id if ctx.guild else None
    tickers = get_tickers(ctx.author.id, guild_id)
    if not tickers:
        await ctx.respond("Your watchlist is empty. Use `/watchlist add AAPL` to add tickers.", ephemeral=True)
        return

    display = ", ".join(f"`{sanitize_for_discord(t)}`" for t in tickers)
    await ctx.respond(f"Your watchlist ({len(tickers)}): {display}", ephemeral=True)


@watchlist_group.command(name="news", description="Get news for your watchlisted tickers", **_slash_kwargs())
async def watchlist_news(ctx):
    """Subcommand: news for user's watchlist."""
    await ctx.defer()
    try:
        guild_id = ctx.guild.id if ctx.guild else None
        loop = asyncio.get_event_loop()
        embed = await loop.run_in_executor(
            _executor,
            lambda uid=ctx.author.id, gid=guild_id: (clear_stop(), run_watchlist_news(uid, gid))[1],
        )
        await ctx.edit(embed=embed)
    except StopRequested:
        logger.info("Watchlist news stopped by user")
        await ctx.edit(content="Operation stopped.")
    except Exception:
        logger.exception("Watchlist news command failed")
        await ctx.edit(content="An error occurred while fetching news. Please try again later.")


@watchlist_group.command(
    name="recap",
    description="Get a recap of your watchlisted tickers",
    **_slash_kwargs(),
)
async def watchlist_recap(
    ctx,
    volatility: discord.Option(
        str,
        "Volatility",
        choices=[
            discord.OptionChoice("Considered", "considered"),
            discord.OptionChoice("Not considered", "not_considered"),
        ],
        default="considered",
    ),
    timeframe: discord.Option(
        str,
        "Timeframe for analysis",
        choices=[
            discord.OptionChoice("Daily", "Daily"),
            discord.OptionChoice("Weekly", "1W"),
        ],
        default="Daily",
    ),
    breakdown: discord.Option(
        str,
        "Indicator breakdown",
        name="breakdown",
        choices=[
            discord.OptionChoice("Yes", "yes"),
            discord.OptionChoice("No", "no"),
        ],
        default="no",
    ) = "no",
):
    """Subcommand: recap of user's watchlist."""
    ignore_volatility = volatility == "not_considered"
    show_breakdown = breakdown == "yes"
    guild_id = ctx.guild.id if ctx.guild else None

    async def deliver(emb, err):
        try:
            if emb is not None:
                await ctx.edit(embed=emb)
            else:
                await ctx.edit(content=err or "An error occurred while generating the recap.")
        except discord.NotFound:
            logger.warning("Watchlist recap interaction expired before delivery")

    job = RecapJob(
        job_type="watchlist_recap",
        params={
            "user_id": ctx.author.id,
            "guild_id": guild_id,
            "ignore_volatility": ignore_volatility,
            "timeframe": timeframe,
            "show_breakdown": show_breakdown,
        },
        deliver=deliver,
    )
    pos = enqueue_recap(job)
    if pos is None:
        await ctx.respond(
            f"Recap queue is full (max {RECAP_QUEUE_MAX}). Try again in a moment.",
            ephemeral=True,
        )
        return
    await ctx.defer()
    if pos > 1:
        await ctx.edit(content=f"Your recap is queued (position {pos}). Processing...")


async def auto_recap_loop():
    """Background task: run recap every N minutes when market is open."""
    await bot.wait_until_ready()
    interval_seconds = RECAP_INTERVAL * 60

    while not bot.is_closed():
        await asyncio.sleep(interval_seconds)

        try:
            if not is_market_open():
                logger.info("Auto recap skipped: market closed")
            elif not CHANNEL_ID:
                logger.info("Auto recap skipped: no channel_id configured")
            else:
                channel = bot.get_channel(CHANNEL_ID)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(CHANNEL_ID)
                    except discord.NotFound:
                        logger.warning("Channel %s not found or inaccessible", CHANNEL_ID)
                        channel = None
                if channel:
                    async def deliver(emb, err):
                        if emb is not None:
                            await channel.send(content="@everyone", embed=emb)
                            logger.info("Auto recap posted to channel %s", CHANNEL_ID)
                        elif err:
                            logger.warning("Auto recap failed: %s", err)

                    job = RecapJob(
                        job_type="recap",
                        params={
                            "ignore_volatility": False,
                            "timeframe": RECAP_TIMEFRAME,
                            "show_breakdown": SHOW_SIGNAL_BREAKDOWN,
                        },
                        deliver=deliver,
                    )
                    pos = enqueue_recap(job)
                    if pos is None:
                        logger.warning("Auto recap skipped: queue full")
        except Exception as e:
            logger.exception("Auto recap failed: %s", e)


@bot.event
async def on_ready():
    global _auto_recap_task, _recap_queue_task
    logger.info("Bot ready: %s", bot.user)
    if _auto_recap_task is None or _auto_recap_task.done():
        _auto_recap_task = bot.loop.create_task(auto_recap_loop())
    if _recap_queue_task is None or _recap_queue_task.done():
        _recap_queue_task = bot.loop.create_task(
            recap_queue_worker(_executor, run_recap, run_market, run_watchlist_recap)
        )


def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set. Create a .env file with your bot token.")
        return

    if not CHANNEL_ID:
        logger.warning("channel_id not set in config.yaml. Auto recap will not post. Set it to your channel ID.")
    if not GUILD_ID:
        logger.info("guild_id not set. Slash commands sync globally and may take up to 1 hour to appear. For instant sync, add your server ID to config.yaml.")
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
        logger.warning("SUPABASE_URL or SUPABASE_KEY not set. Watchlist features will not work. Add them to .env.")

    bot.run(token)


if __name__ == "__main__":
    main()
