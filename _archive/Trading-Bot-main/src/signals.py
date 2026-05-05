"""
Signal logic: Buy/Sell/Hold with unified confidence (1-100).
Based on weighted consensus (Option A) from indicator_scores.
"""

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.config_resolver import get_config_for_ticker
from src.indicator_scores import WeightedScores, compute_weighted_scores
from src.indicators import get_latest_indicators
from src.regime import RegimeState, classify_regime


@dataclass
class Signal:
    symbol: str
    signal_type: Literal["Buy", "Sell", "Hold"]
    confidence: int  # 1-100
    rsi: float
    macd_hist: float
    price: float
    atr_pct: float = 0.0  # ATR as % of price, for display
    net_score: float | None = None
    weighted_scores: WeightedScores | None = None
    regime: str | None = None  # e.g. "Low Vol (MR) | VIX 15.2"
    stop_price: float | None = None  # hard stop or ATR trailing level
    take_profit_price: float | None = None  # take-profit target
    stop_pct: float | None = None  # % distance to stop (negative for Buy, positive for Sell)
    intrinsic_value: float | None = None  # Graham intrinsic value (display-only)
    margin_of_safety: float | None = None  # % positive = undervalued
    valuation_label: str | None = None  # "Undervalued" / "Fair Value" / "Overvalued"


def _compute_confidence(
    weighted_scores: WeightedScores,
    signal_type: str,
    rsi: float,
    macd_hist: float,
    rsi_oversold: float,
    rsi_overbought: float,
    timeframe: str | None = None,
    config: dict | None = None,
) -> int:
    """
    Consensus-based confidence 1-100.
    consensus = winning_weight / total_weight (0.5 to 1.0)
    confidence = 20 + 80 * consensus
    Extremity bonus (up to +10) only when RSI/MACD align with signal.
    When timeframe is set, applies timeframe_confidence_factors scaling.
    """
    if signal_type == "Hold":
        return 0

    # Consensus = winning weight / max possible (so 3-0 is ~34%, not 100%)
    winning_weight = max(weighted_scores.buy_weight, weighted_scores.sell_weight)
    max_possible = max(weighted_scores.max_possible_weight, 0.01)
    consensus = min(1.0, winning_weight / max_possible)
    base_confidence = 20 + 80 * consensus

    # Extremity bonus only when aligned; capped at +5 total to avoid inflating to 100
    extremity_bonus = 0.0
    if signal_type == "Buy":
        rsi_extreme = max(0, rsi_oversold - rsi) / max(1, rsi_oversold)
    else:
        rsi_extreme = max(0, rsi - rsi_overbought) / max(1, 100 - rsi_overbought)
    extremity_bonus += rsi_extreme * 2.5  # 0-2.5 from RSI
    macd_aligned = (signal_type == "Buy" and macd_hist > 0) or (signal_type == "Sell" and macd_hist < 0)
    if macd_aligned:
        extremity_bonus += min(2.5, abs(macd_hist) / 0.6)  # 0-2.5 from MACD
    extremity_bonus = min(5.0, extremity_bonus)  # cap total at +5

    confidence = base_confidence + extremity_bonus

    # Timeframe-specific scaling (shorter TFs noisier)
    tf_factor = 1.0
    if timeframe and config:
        factors = config.get("timeframe_confidence_factors", {})
        tf_factor = factors.get(timeframe, 1.0)
        if not isinstance(tf_factor, (int, float)) or tf_factor < 0.5 or tf_factor > 1.0:
            tf_factor = 1.0
    confidence = confidence * tf_factor

    return max(1, min(100, int(round(confidence))))


def _compute_stop_tp_levels(
    signal_type: str,
    price: float,
    atr: float | None,
    atr_pct: float,
    config: dict,
) -> tuple[float | None, float | None, float | None]:
    """
    Compute stop price, take-profit price, and stop_pct for display.
    Uses backtest config: stop_pct, take_profit_pct, trailing_stop_atr_multiplier.
    Returns (stop_price, take_profit_price, stop_pct) — any may be None.
    """
    if signal_type == "Hold":
        return (None, None, None)

    bt = config.get("backtest", {})
    stop_pct_config = bt.get("stop_pct", 0)
    take_profit_pct_config = bt.get("take_profit_pct", 0)
    trailing_atr_mult = bt.get("trailing_stop_atr_multiplier", 0)

    stop_price: float | None = None
    stop_pct_display: float | None = None

    if trailing_atr_mult > 0 and atr is not None and atr > 0:
        if signal_type == "Buy":
            stop_price = price - atr * trailing_atr_mult
            stop_pct_display = -((price - stop_price) / price * 100) if price > 0 else None
        else:
            stop_price = price + atr * trailing_atr_mult
            stop_pct_display = (stop_price - price) / price * 100 if price > 0 else None
    elif stop_pct_config > 0:
        if signal_type == "Buy":
            stop_price = price * (1 - stop_pct_config / 100)
            stop_pct_display = -stop_pct_config
        else:
            stop_price = price * (1 + stop_pct_config / 100)
            stop_pct_display = stop_pct_config

    take_profit_price: float | None = None
    if take_profit_pct_config > 0 and price > 0:
        if signal_type == "Buy":
            take_profit_price = price * (1 + take_profit_pct_config / 100)
        else:
            take_profit_price = price * (1 - take_profit_pct_config / 100)

    return (stop_price, take_profit_price, stop_pct_display)


def evaluate_signal(
    df: pd.DataFrame,
    symbol: str,
    rsi_oversold: float = 35,
    rsi_overbought: float = 65,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    supertrend_period: int = 10,
    supertrend_multiplier: float = 3.0,
    stoch_window: int = 14,
    stoch_smooth: int = 3,
    stoch_oversold: float = 20,
    stoch_overbought: float = 80,
    willr_period: int = 14,
    willr_oversold: float = -80,
    willr_overbought: float = -20,
    ema_fast: int = 9,
    ema_slow: int = 21,
    atr_period: int = 14,
    atr_avg_period: int = 20,
    ignore_volatility: bool = False,
    config: dict | None = None,
    min_net_score: float = 0.5,
    news_sentiment: float | None = None,
    expert_sentiment: float | None = None,
    timeframe: str | None = None,
    vix: float | None = None,
) -> Signal | None:
    """
    Evaluate Buy/Sell/Hold for a single symbol from OHLCV DataFrame.
    Uses weighted consensus for signal type and confidence.
    vix: current VIX level for regime classification. Falls back to SMA-200 if None.
    Returns Signal or None if insufficient data.
    """
    ind = get_latest_indicators(
        df,
        rsi_period=rsi_period,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        bb_period=bb_period,
        bb_std=bb_std,
        supertrend_period=supertrend_period,
        supertrend_multiplier=supertrend_multiplier,
        stoch_window=stoch_window,
        stoch_smooth=stoch_smooth,
        willr_period=willr_period,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        atr_period=atr_period,
        atr_avg_period=atr_avg_period,
    )
    if ind is None:
        return None

    config = config or {}

    # Build regime state from VIX + SMA-200
    regime_state = classify_regime(
        close=ind["close"],
        sma_200=ind.get("sma_200"),
        vix=vix,
        config=config,
    )

    weighted = compute_weighted_scores(
        ind, config, ignore_volatility=ignore_volatility, news_sentiment=news_sentiment,
        expert_sentiment=expert_sentiment, timeframe=timeframe, regime_state=regime_state,
    )
    min_net = config.get("min_net_score", min_net_score)

    rsi = ind["rsi"]
    macd_hist = ind["macd_hist"]
    close = ind["close"]
    atr_pct = ind.get("atr_pct", 0.0)

    if weighted.net_score >= min_net:
        signal_type = "Buy"
        confidence = _compute_confidence(
            weighted, signal_type, rsi, macd_hist,
            rsi_oversold, rsi_overbought,
            timeframe=timeframe, config=config,
        )
    elif weighted.net_score <= -min_net:
        signal_type = "Sell"
        confidence = _compute_confidence(
            weighted, signal_type, rsi, macd_hist,
            rsi_oversold, rsi_overbought,
            timeframe=timeframe, config=config,
        )
    else:
        signal_type = "Hold"
        confidence = 0

    stop_price, take_profit_price, stop_pct = _compute_stop_tp_levels(
        signal_type=signal_type,
        price=close,
        atr=ind.get("atr"),
        atr_pct=atr_pct,
        config=config,
    )

    return Signal(
        symbol=symbol,
        signal_type=signal_type,
        confidence=confidence,
        rsi=rsi,
        macd_hist=macd_hist,
        price=close,
        atr_pct=atr_pct,
        net_score=weighted.net_score,
        weighted_scores=weighted,
        regime=regime_state.display_label,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        stop_pct=stop_pct,
    )


def evaluate_all(
    ohlcv_data: dict[str, pd.DataFrame],
    rsi_oversold: float = 35,
    rsi_overbought: float = 65,
    ignore_volatility: bool = False,
    config: dict | None = None,
    news_sentiments: dict[str, float] | None = None,
    expert_sentiments: dict[str, float] | None = None,
    timeframe: str | None = None,
    index_id: str | None = None,
    vix: float | None = None,
    **indicator_params,
) -> list[Signal]:
    """
    Evaluate signals for all symbols in ohlcv_data.
    Returns list of Signal objects (includes Hold; filter in recap if desired).
    news_sentiments: optional dict symbol -> sentiment (-1 to +1); when provided, used for that symbol.
    expert_sentiments: optional dict symbol -> sentiment (-1 to +1); when provided, used for that symbol.
    timeframe: optional Daily/1W/1H; when set, uses per-timeframe indicator weights and confidence scaling.
    index_id: when from /market, pass index id for per-ticker asset-class resolution.
    vix: current VIX level for regime classification (shared across all symbols in batch).
    """
    base_config = config or {}
    signals = []
    for symbol, df in ohlcv_data.items():
        ns = news_sentiments.get(symbol) if news_sentiments else None
        es = expert_sentiments.get(symbol) if expert_sentiments else None
        resolved_config = get_config_for_ticker(
            symbol, base_config, timeframe=timeframe, index_id=index_id
        )
        strategy = resolved_config.get("strategy", "mr")
        if strategy == "hybrid":
            from src.hybrid import evaluate_hybrid
            sig = evaluate_hybrid(
                df, symbol, config=resolved_config,
                ignore_volatility=ignore_volatility,
                news_sentiment=ns, expert_sentiment=es, timeframe=timeframe, vix=vix,
            )
        elif strategy == "tf":
            from src.signals_trend import evaluate_breakout_signal
            tf_cfg = resolved_config.get("trend_following", {})
            sig = evaluate_breakout_signal(
                df, symbol,
                donchian_period=tf_cfg.get("donchian_period", 20),
                atr_period=tf_cfg.get("atr_period", 14),
                adx_period=tf_cfg.get("adx_period", 14),
                adx_threshold=tf_cfg.get("adx_threshold", 25),
                config=resolved_config,
            )
        else:
            sig = evaluate_signal(
                df,
                symbol,
                rsi_oversold=rsi_oversold,
                rsi_overbought=rsi_overbought,
                ignore_volatility=ignore_volatility,
                config=resolved_config,
                news_sentiment=ns,
                expert_sentiment=es,
                timeframe=timeframe,
                vix=vix,
                **indicator_params,
            )
        if sig:
            signals.append(sig)
    return signals
