"""
Backtest engine: event-driven, reuses evaluate_signal.
Fills at next bar open; applies commission and optional slippage.
"""

import logging
import threading
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.data import fetch_single
from src.indicators import compute_atr
from src.stop import StopRequested, clear_stop, is_stop_requested

logger = logging.getLogger(__name__)
from src.signals import evaluate_signal
from src.signals_trend import evaluate_breakout_signal

# Module-level cache for DXY/TIP series (avoid repeated fetches during WFO).
# Keyed by (symbol, period, interval).  Protected by lock for thread safety.
_macro_series_cache: dict[tuple, pd.Series | None] = {}
_macro_cache_lock = threading.Lock()


@dataclass
class Trade:
    """Single round-trip trade."""
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    side: Literal["long", "short"]
    pnl_pct: float
    pnl_abs: float
    bars_held: int = 0


@dataclass
class BacktestResult:
    """Backtest summary and trade list."""
    symbol: str
    total_return: float
    buy_hold_return: float
    num_trades: int
    win_rate: float
    max_drawdown: float
    trades: list[Trade]
    start_date: str
    end_date: str
    bar_returns: list[float] | None = None
    forced_skipped: int = 0
    profit_factor: float = 0.0


def _compute_min_warmup(config: dict) -> int:
    """Minimum bars needed for mean-reversion indicators to be valid."""
    ind = config.get("indicators", {})
    rsi_period = ind.get("rsi_period", 14)
    macd_slow = ind.get("macd_slow", 26)
    bb_period = ind.get("bb_period", 20)
    supertrend_period = ind.get("supertrend_period", 10)
    stoch_window = ind.get("stoch_window", 14)
    willr_period = ind.get("willr_period", 14)
    ema_slow = ind.get("ema_slow", 21)
    atr_period = ind.get("atr_period", 14)
    atr_avg_period = ind.get("atr_avg_period", 20)
    return max(
        rsi_period, macd_slow, bb_period, supertrend_period,
        stoch_window, willr_period, ema_slow, atr_period + atr_avg_period
    ) + 15


def _compute_min_warmup_trend(config: dict) -> int:
    """Minimum bars needed for trend-following indicators (Donchian, ATR, ADX)."""
    tf_cfg = config.get("trend_following", {})
    donchian_period = tf_cfg.get("donchian_period", 20)
    atr_period = tf_cfg.get("atr_period", 14)
    adx_period = tf_cfg.get("adx_period", 14)
    return max(donchian_period, atr_period, adx_period) + 5


def _apply_costs(price: float, commission_pct: float, slippage_pct: float, is_buy: bool) -> float:
    """Apply commission and slippage to fill price."""
    mult = 1.0 if is_buy else -1.0
    cost = price * (commission_pct / 100)
    slip = price * (slippage_pct / 100)
    return price + mult * (cost + slip)


def _get_gold_macro_filter(df: pd.DataFrame, period: str, interval: str) -> "pd.Series | None":
    """
    Fetch DXY and TIP, compute gold macro filter for the date range in df.
    Cached by (period, interval) to avoid repeated fetches during WFO.
    Returns pd.Series of bool indexed by date, or None if data unavailable.
    """
    from src.regime import get_gold_macro_filter
    cache_key = ("_gold_macro", period, interval)
    with _macro_cache_lock:
        if cache_key not in _macro_series_cache:
            dxy = fetch_single("DX-Y.NYB", period=period, interval=interval)
            tip = fetch_single("TIP", period=period, interval=interval)
            dxy_close = dxy["Close"] if dxy is not None and not dxy.empty else None
            tip_close = tip["Close"] if tip is not None and not tip.empty else None
            _macro_series_cache[cache_key] = get_gold_macro_filter(dxy_close, tip_close)
        return _macro_series_cache[cache_key]


def run_backtest(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    config: dict | None = None,
    ignore_volatility: bool = False,
    commission_pct: float | None = None,
    slippage_pct: float | None = None,
    timeframe: str | None = None,
    df: pd.DataFrame | None = None,
    min_confidence: int | None = None,
    vix_series: pd.Series | None = None,
    forced_entry_dates: set[str] | None = None,
    gold_macro_filter_series: "pd.Series | None" = None,
) -> BacktestResult | None:
    """
    Run event-driven backtest for a single symbol.
    Reuses evaluate_signal; fills at next bar open.
    Returns None if insufficient data.
    When df is provided, use it instead of fetching (for WFO).
    vix_series: historical VIX close values indexed by date, for regime classification per bar.
    """
    config = config or {}
    bt_cfg = config.get("backtest", {})
    commission_pct = commission_pct if commission_pct is not None else bt_cfg.get("commission_pct", 0.2)
    slippage_pct = slippage_pct if slippage_pct is not None else bt_cfg.get("slippage_pct", 0.05)
    min_conf = (
        min_confidence
        if min_confidence is not None
        else config.get("min_confidence", config.get("backtest", {}).get("min_confidence", 0))
    )
    stop_pct = bt_cfg.get("stop_pct", 0)
    take_profit_pct = bt_cfg.get("take_profit_pct", 0)
    trailing_stop_pct = bt_cfg.get("trailing_stop_pct", 0)
    trailing_stop_atr_multiplier = bt_cfg.get("trailing_stop_atr_multiplier", 0)
    max_hold_bars = bt_cfg.get("max_hold_bars", 0)

    if df is None:
        logger.info("Backtest %s: fetching data (period=%s, interval=%s)", symbol, period, interval)
        df = fetch_single(symbol, period=period, interval=interval)
    if df is None or df.empty or len(df) < 30:
        logger.warning("Backtest %s: insufficient data (got %d bars)", symbol, len(df) if df is not None else 0)
        return None

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in df.columns for c in required):
        return None

    ind = config.get("indicators", {})
    atr_period = ind.get("atr_period", 14)
    atr_series: pd.Series | None = None
    if trailing_stop_atr_multiplier > 0:
        atr_series = compute_atr(df["High"], df["Low"], df["Close"], window=atr_period)

    strategy = config.get("strategy", "mr")
    min_warmup = _compute_min_warmup_trend(config) if strategy == "tf" else _compute_min_warmup(config)

    # Gold macro filter: DXY/TIP regime gate for GLD Donchian longs.
    # Enabled when config has trend_following.gold_macro_filter: true and symbol is GLD.
    _gold_filter: "pd.Series | None" = None
    if (
        strategy == "tf"
        and symbol.upper() == "GLD"
        and config.get("trend_following", {}).get("gold_macro_filter", False)
    ):
        _gold_filter = gold_macro_filter_series
        if _gold_filter is None:
            try:
                _gold_filter = _get_gold_macro_filter(df, period, interval)
            except Exception as e:
                logger.warning("GLD macro filter fetch failed: %s — proceeding without filter", e)
    # Cap warmup for short windows (e.g. weekly 52-bar train); need at least 1 tradable bar
    min_warmup = min(min_warmup, len(df) - 2)

    position: float | None = None  # entry price when long
    entry_date: str | None = None
    entry_bar_index: int | None = None
    peak_price: float | None = None  # for trailing stop
    trades: list[Trade] = []
    position_flags = [0] * len(df)  # 1 when long, 0 when flat (for bar-level returns)
    forced_skipped = 0

    for i in range(min_warmup, len(df) - 1):
        if (i - min_warmup) % 50 == 0 and is_stop_requested():
            clear_stop()
            logger.info("Backtest %s: stopped by user", symbol)
            raise StopRequested()

        if position is not None:
            position_flags[i] = 1

        next_open = float(df.iloc[i + 1]["Open"])
        next_date = str(df.index[i + 1])[:10]

        # Check exit rules when in position (before signal evaluation)
        if position is not None:
            current_close = float(df.iloc[i]["Close"])
            pnl_pct_unrealized = (current_close - position) / position * 100

            # Update peak for trailing stop
            if peak_price is None:
                peak_price = max(position, current_close)
            else:
                peak_price = max(peak_price, current_close)

            exit_triggered = False

            # Hard stop
            if stop_pct > 0 and pnl_pct_unrealized <= -stop_pct:
                exit_triggered = True
            # Take-profit
            elif take_profit_pct > 0 and pnl_pct_unrealized >= take_profit_pct:
                exit_triggered = True
            # ATR trailing stop: exit when price drops (peak - ATR*mult) from peak
            elif trailing_stop_atr_multiplier > 0 and atr_series is not None and peak_price > 0:
                atr_val = float(atr_series.iloc[i]) if i < len(atr_series) and pd.notna(atr_series.iloc[i]) else 0.0
                if atr_val > 0:
                    stop_distance = atr_val * trailing_stop_atr_multiplier
                    if peak_price - current_close >= stop_distance:
                        exit_triggered = True
            # Trailing stop: price retraced X% from peak (only if ATR not used)
            elif trailing_stop_pct > 0 and peak_price > 0 and trailing_stop_atr_multiplier <= 0:
                retrace_pct = (peak_price - current_close) / peak_price * 100
                if retrace_pct >= trailing_stop_pct:
                    exit_triggered = True
            # Time-based max hold
            elif max_hold_bars > 0 and entry_bar_index is not None and (i - entry_bar_index) >= max_hold_bars:
                exit_triggered = True

            if exit_triggered:
                fill_price = _apply_costs(next_open, commission_pct, slippage_pct, is_buy=False)
                pnl_abs = fill_price - position
                pnl_pct = (pnl_abs / position) * 100
                trades.append(
                    Trade(
                        entry_date=entry_date or "",
                        exit_date=next_date,
                        entry_price=position,
                        exit_price=fill_price,
                        side="long",
                        pnl_pct=pnl_pct,
                        pnl_abs=pnl_abs,
                        bars_held=i - (entry_bar_index or i),
                    )
                )
                position = None
                entry_date = None
                entry_bar_index = None
                peak_price = None
                continue

        bar_df = df.iloc[: i + 1].copy()

        # Look up VIX at this bar's date for regime classification
        bar_vix = None
        if vix_series is not None:
            bar_date = df.index[i]
            # Find nearest VIX value at or before this date
            try:
                mask = vix_series.index <= bar_date
                if mask.any():
                    bar_vix = float(vix_series.loc[mask].iloc[-1])
            except Exception:
                pass
        if strategy == "tf":
            tf_cfg = config.get("trend_following", {})
            signal = evaluate_breakout_signal(
                bar_df,
                symbol,
                donchian_period=tf_cfg.get("donchian_period", 20),
                atr_period=tf_cfg.get("atr_period", 14),
                adx_period=tf_cfg.get("adx_period", 14),
                adx_threshold=tf_cfg.get("adx_threshold", 25),
                config=config,
                macro_filter=_gold_filter,
            )
        else:
            signal = evaluate_signal(
                bar_df,
                symbol,
                rsi_oversold=ind.get("rsi_oversold", 35),
                rsi_overbought=ind.get("rsi_overbought", 65),
                rsi_period=ind.get("rsi_period", 14),
                macd_fast=ind.get("macd_fast", 12),
                macd_slow=ind.get("macd_slow", 26),
                macd_signal=ind.get("macd_signal", 9),
                bb_period=ind.get("bb_period", 20),
                bb_std=ind.get("bb_std", 2),
                supertrend_period=ind.get("supertrend_period", 10),
                supertrend_multiplier=ind.get("supertrend_multiplier", 3),
                stoch_window=ind.get("stoch_window", 14),
                stoch_smooth=ind.get("stoch_smooth", 3),
                stoch_oversold=ind.get("stoch_oversold", 20),
                stoch_overbought=ind.get("stoch_overbought", 80),
                willr_period=ind.get("willr_period", 14),
                willr_oversold=ind.get("willr_oversold", -80),
                willr_overbought=ind.get("willr_overbought", -20),
                ema_fast=ind.get("ema_fast", 9),
                ema_slow=ind.get("ema_slow", 21),
                atr_period=ind.get("atr_period", 14),
                atr_avg_period=ind.get("atr_avg_period", 20),
                ignore_volatility=ignore_volatility,
                config=config,
                news_sentiment=None,
                timeframe=timeframe,
                vix=bar_vix,
            )
        # Forced entry check (randomized entry test): bypass signal gate
        is_forced_entry_bar = forced_entry_dates is not None and next_date in forced_entry_dates
        if is_forced_entry_bar and position is not None:
            forced_skipped += 1  # skip if >10% of forced entries were blocked by open positions

        # Normal signal gate (skip if no signal or low confidence, unless forced entry)
        if not is_forced_entry_bar:
            if signal is None:
                continue
            if min_conf > 0 and signal.confidence < min_conf:
                continue

        should_enter = (
            (forced_entry_dates is None and signal is not None and signal.signal_type == "Buy" and position is None) or
            (is_forced_entry_bar and position is None)
        )
        if should_enter:
            fill_price = _apply_costs(next_open, commission_pct, slippage_pct, is_buy=True)
            position = fill_price
            entry_date = next_date
            entry_bar_index = i  # bar index when we entered; hold duration = current_i - entry_bar_index
            peak_price = fill_price
        elif signal is not None and signal.signal_type == "Sell" and position is not None:
            fill_price = _apply_costs(next_open, commission_pct, slippage_pct, is_buy=False)
            pnl_abs = fill_price - position
            pnl_pct = (pnl_abs / position) * 100
            trades.append(
                Trade(
                    entry_date=entry_date or "",
                    exit_date=next_date,
                    entry_price=position,
                    exit_price=fill_price,
                    side="long",
                    pnl_pct=pnl_pct,
                    pnl_abs=pnl_abs,
                    bars_held=i - (entry_bar_index or i),
                )
            )
            position = None
            entry_date = None

    if position is not None:
        last_close = float(df.iloc[-1]["Close"])
        fill_price = _apply_costs(last_close, commission_pct, slippage_pct, is_buy=False)
        pnl_abs = fill_price - position
        pnl_pct = (pnl_abs / position) * 100
        trades.append(
            Trade(
                entry_date=entry_date or "",
                exit_date=str(df.index[-1])[:10],
                entry_price=position,
                exit_price=fill_price,
                side="long",
                pnl_pct=pnl_pct,
                pnl_abs=pnl_abs,
                bars_held=len(df) - 1 - (entry_bar_index or len(df) - 1),
            )
        )

    if not trades:
        total_return = 0.0
        win_rate = 0.0
        max_drawdown = 0.0
    else:
        wins = sum(1 for t in trades if t.pnl_pct > 0)
        win_rate = (wins / len(trades)) * 100
        cumret = 1.0
        peak = 1.0
        max_dd = 0.0
        for t in trades:
            cumret *= 1.0 + (t.pnl_pct / 100)
            peak = max(peak, cumret)
            dd = (peak - cumret) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        total_return = (cumret - 1.0) * 100
        max_drawdown = max_dd * 100

    start_date = str(df.index[min_warmup])[:10]
    end_date = str(df.index[-1])[:10]

    close_first = float(df.iloc[min_warmup]["Close"])
    close_last = float(df.iloc[-1]["Close"])
    buy_hold_return = ((close_last / close_first) - 1.0) * 100

    # Bar-level returns: position_flags[i-1] * close_pct_change[i]
    closes = df["Close"].values
    bar_returns = []
    for i in range(min_warmup + 1, len(df)):
        pct = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0.0
        bar_returns.append(position_flags[i - 1] * pct)

    logger.info(
        "Backtest %s: done | %s-%s | %d bars | %d trades | return %.1f%% | buy&hold %.1f%%",
        symbol, start_date, end_date, len(df) - min_warmup, len(trades), total_return, buy_hold_return,
    )

    gross_profit = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    return BacktestResult(
        symbol=symbol,
        total_return=total_return,
        buy_hold_return=buy_hold_return,
        num_trades=len(trades),
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        trades=trades,
        start_date=start_date,
        end_date=end_date,
        bar_returns=bar_returns,
        forced_skipped=forced_skipped,
        profit_factor=profit_factor,
    )


def format_backtest_embed(result: BacktestResult, ticker: str, timeframe: str | None = None) -> dict:
    """Format BacktestResult as Discord embed dict."""
    ret_str = f"{result.total_return:+.1f}%"
    bh_str = f"{result.buy_hold_return:+.1f}%"
    outperformance = result.total_return - result.buy_hold_return
    out_str = f"{outperformance:+.1f}%"
    color = 0x4CAF50 if result.total_return >= 0 else 0xF44336
    tf_line = f"**Strategy:** {timeframe}\n" if timeframe else ""
    desc = (
        f"{tf_line}"
        f"**Period:** {result.start_date} → {result.end_date}\n"
        f"**Strategy return:** {ret_str}\n"
        f"**Buy & hold return:** {bh_str}\n"
        f"**Outperformance:** {out_str}\n"
        f"**Trades:** {result.num_trades}\n"
        f"**Win rate:** {result.win_rate:.0f}%\n"
        f"**Max drawdown:** {result.max_drawdown:.1f}%"
    )

    fields = []
    if result.trades:
        lines = []
        for t in result.trades[:8]:
            pnl_sign = "+" if t.pnl_pct >= 0 else ""
            lines.append(f"{t.entry_date} → {t.exit_date}: {pnl_sign}{t.pnl_pct:.1f}%")
        trade_text = "\n".join(lines)
        if len(result.trades) > 8:
            trade_text += f"\n... and {len(result.trades) - 8} more"
        fields.append({"name": "Recent trades", "value": trade_text, "inline": False})

    return {
        "title": f"Backtest – {ticker}",
        "description": desc,
        "color": color,
        "fields": fields,
    }
