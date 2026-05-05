"""
Chart generation for Discord embeds. Uses mplfinance for candlestick/OHLC charts.
Produces PNG bytes for attachment via discord.File + embed.set_image.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import mplfinance as mpf
import pandas as pd

from src.backtest import Trade
from src.indicators import (
    compute_macd,
    compute_rsi,
    compute_supertrend,
)

logger = logging.getLogger(__name__)

# mplfinance expects specific column names; data.py uses Open, High, Low, Close, Volume
_OHLCV = ["Open", "High", "Low", "Close", "Volume"]

# Custom style: refined colors, softer grid, thinner overlay lines
_CHART_STYLE = mpf.make_mpf_style(
    base_mpl_style="ggplot",
    marketcolors=mpf.make_marketcolors(
        up="#26a69a",
        down="#ef5350",
        edge="inherit",
        wick="inherit",
        volume="inherit",
    ),
    gridcolor="#f0f0f0",
    gridstyle=":",
    facecolor="#ffffff",
    figcolor="#ffffff",
    rc={"axes.grid": True, "axes.grid.axis": "y"},
)


def _get_indicator_params(config: dict) -> dict[str, Any]:
    """Extract indicator params from config for chart overlays."""
    ind = config.get("indicators", {})
    charts_cfg = config.get("charts", {})
    # Chart-specific SuperTrend overrides (lower = more responsive line)
    st_period = charts_cfg.get("supertrend_period", ind.get("supertrend_period", 10))
    st_mult = charts_cfg.get("supertrend_multiplier", ind.get("supertrend_multiplier", 3.0))
    return {
        "rsi_period": ind.get("rsi_period", 14),
        "macd_fast": ind.get("macd_fast", 12),
        "macd_slow": ind.get("macd_slow", 26),
        "macd_signal": ind.get("macd_signal", 9),
        "bb_period": ind.get("bb_period", 20),
        "bb_std": ind.get("bb_std", 2.0),
        "supertrend_period": st_period,
        "supertrend_multiplier": st_mult,
    }


def _prepare_df(df: pd.DataFrame, lookback: int) -> pd.DataFrame | None:
    """Slice and validate DataFrame for mplfinance. Returns None if invalid."""
    if df is None or df.empty or len(df) < 2:
        return None
    for col in _OHLCV:
        if col not in df.columns:
            return None
    sliced = df.tail(lookback).copy()
    if sliced.empty or len(sliced) < 2:
        return None
    return sliced


def _year_from_index(index: pd.DatetimeIndex) -> str:
    """Derive year or date range text from index for left margin display."""
    if index is None or len(index) == 0:
        return ""
    first = index.min()
    last = index.max()
    y1, y2 = first.year, last.year
    if y1 == y2:
        return str(y1)
    return f"{y1}–{y2}"


def _margin_text_from_index(index: pd.DatetimeIndex, timeframe: str) -> str:
    """Build top-left margin text: year, optional date for 1h, and timeframe label."""
    if index is None or len(index) == 0:
        return ""
    tf = (timeframe or "1d").lower()
    label = _TF_LABELS.get(tf, "Daily")
    year_text = _year_from_index(index)
    if tf == "1h":
        last_ts = index.max()
        day_str = last_ts.strftime("%b %d")
        return f"{day_str} {year_text} {label}".strip()
    return f"{year_text} {label}".strip()


# Display labels for chart margin (e.g. "Daily", "1W", "1H")
_TF_LABELS = {"1d": "Daily", "1wk": "1W", "1h": "1H"}


def _is_intraday(index: pd.DatetimeIndex) -> bool:
    """True if index has intraday timestamps (time component varies)."""
    if index is None or len(index) < 2:
        return False
    deltas = index.to_series().diff().dropna()
    if deltas.empty:
        return False
    median_td = deltas.median()
    return median_td < pd.Timedelta(days=1)


def _chart_format_for_timeframe(timeframe: str, index: pd.DatetimeIndex | None = None) -> dict[str, Any]:
    """Return datetime_format, xrotation, tf_label. Omit time when data is not intraday. 12-hour clock."""
    tf = (timeframe or "1d").lower()
    label = _TF_LABELS.get(tf, "Daily")
    show_time = _is_intraday(index) if index is not None else True
    if tf == "1h":
        fmt = "%I:%M %p" if show_time else "%b %d"
    elif tf == "1wk":
        fmt = "%b %d %I:%M %p" if show_time else "%b %d"
    else:
        fmt = "%b %d %I:%M %p" if show_time else "%b %d"
    return {"datetime_format": fmt, "xrotation": 45, "tf_label": label}


def build_stock_chart(
    df: pd.DataFrame,
    ticker: str,
    indicators: list[str],
    config: dict,
    timeframe: str | None = None,
) -> bytes | None:
    """
    Build candlestick chart with indicator overlays.

    Args:
        df: OHLCV DataFrame (Open, High, Low, Close, Volume).
        ticker: Display ticker symbol.
        indicators: List of indicator names, e.g. ["supertrend"] or ["rsi", "macd"].
        config: Bot config for indicator params and charts.lookback_bars, charts.dpi.
        timeframe: "1d", "1wk", or "1h" for date formatting; None treated as "1d".

    Returns:
        PNG bytes or None on failure.
    """
    charts_cfg = config.get("charts", {})
    tf = (timeframe or "1d").lower()
    lookback_by_tf = charts_cfg.get("lookback_by_timeframe", {})
    lookback = lookback_by_tf.get(tf, charts_cfg.get("lookback_bars", 60))
    dpi = charts_cfg.get("dpi", 120)

    plot_df = _prepare_df(df, lookback)
    if plot_df is None:
        return None

    params = _get_indicator_params(config)
    addplots: list[Any] = []

    # Primary: SuperTrend overlay on main panel
    if "supertrend" in indicators:
        try:
            high = plot_df["High"].reindex(plot_df.index).ffill().bfill()
            low = plot_df["Low"].reindex(plot_df.index).ffill().bfill()
            close = plot_df["Close"].dropna()
            st_series, _ = compute_supertrend(
                high, low, close,
                period=params["supertrend_period"],
                multiplier=params["supertrend_multiplier"],
            )
            st_aligned = st_series.reindex(plot_df.index).ffill()
            if st_aligned.notna().any():
                ap = mpf.make_addplot(st_aligned, color="orange", panel=0, width=0.7, secondary_y=False)
                addplots.append(ap)
        except Exception as e:
            logger.warning("SuperTrend chart overlay failed: %s", e)

    # Fallback: RSI + MACD if SuperTrend not used or failed
    if not addplots and ("rsi" in indicators or "macd" in indicators):
        close = plot_df["Close"].dropna()
        try:
            if "rsi" in indicators:
                rsi = compute_rsi(close, period=params["rsi_period"])
                rsi_aligned = rsi.reindex(plot_df.index).ffill().bfill()
                if rsi_aligned.notna().any():
                    addplots.append(mpf.make_addplot(rsi_aligned, color="purple", panel=1, ylabel="RSI", width=1.0))
            if "macd" in indicators:
                _, _, macd_hist = compute_macd(
                    close,
                    window_slow=params["macd_slow"],
                    window_fast=params["macd_fast"],
                    window_sign=params["macd_signal"],
                )
                hist_aligned = macd_hist.reindex(plot_df.index).ffill().bfill()
                if hist_aligned.notna().any():
                    addplots.append(mpf.make_addplot(hist_aligned, type="bar", color="blue", panel=2, ylabel="MACD"))
        except Exception as e:
            logger.warning("RSI/MACD chart overlay failed: %s", e)

    # If still no addplots, try SuperTrend as fallback when indicators list is empty/default
    if not addplots:
        try:
            high = plot_df["High"].reindex(plot_df.index).ffill().bfill()
            low = plot_df["Low"].reindex(plot_df.index).ffill().bfill()
            close = plot_df["Close"].dropna()
            st_series, _ = compute_supertrend(
                high, low, close,
                period=params["supertrend_period"],
                multiplier=params["supertrend_multiplier"],
            )
            st_aligned = st_series.reindex(plot_df.index).ffill()
            if st_aligned.notna().any():
                addplots.append(mpf.make_addplot(st_aligned, color="orange", panel=0, width=0.7, secondary_y=False))
        except Exception as e:
            logger.warning("Fallback SuperTrend overlay failed: %s", e)

    try:
        buf = io.BytesIO()
        fmt = _chart_format_for_timeframe(timeframe or "1d", plot_df.index)
        plot_kw: dict[str, Any] = {
            "type": "candle",
            "volume": False,
            "title": ticker,
            "style": _CHART_STYLE,
            "figsize": (10, 5),
            "returnfig": True,
            "datetime_format": fmt["datetime_format"],
            "xrotation": fmt["xrotation"],
        }
        if addplots:
            plot_kw["addplot"] = addplots
        fig, _ = mpf.plot(plot_df, **plot_kw)
        margin_text = _margin_text_from_index(plot_df.index, timeframe or "1d")
        if margin_text:
            fig.text(0.01, 0.98, margin_text, fontsize=9, color="#888888", va="top", ha="left")
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
        fig.clear()
        import matplotlib.pyplot as plt
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        logger.warning("build_stock_chart failed for %s: %s", ticker, e)
        return None


def build_equity_chart(
    trades: list[Trade],
    df: pd.DataFrame,
    ticker: str,
    config: dict | None = None,
) -> bytes | None:
    """
    Build equity curve chart: strategy vs buy-and-hold.

    Computes equity from trades (no backtest engine changes). Uses matplotlib.
    Returns PNG bytes or None.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    config = config or {}
    charts_cfg = config.get("charts", {})
    dpi = charts_cfg.get("dpi", 120)

    if df is None or df.empty or "Close" not in df.columns:
        return None

    plot_df = df.copy()
    if not isinstance(plot_df.index, pd.DatetimeIndex):
        return None

    # Buy-and-hold: cumulative return from first close
    close = plot_df["Close"].dropna()
    if len(close) < 2:
        return None
    first_close = float(close.iloc[0])
    buy_hold = (close / first_close).reindex(plot_df.index).ffill().bfill()

    # Strategy equity: compute from trades
    equity = pd.Series(1.0, index=plot_df.index)
    cum = 1.0
    for t in trades:
        try:
            exit_dt = pd.Timestamp(t.exit_date)
            if exit_dt in equity.index:
                cum *= 1.0 + (t.pnl_pct / 100.0)
                equity.loc[exit_dt:] = cum
            else:
                later = equity.index[equity.index >= exit_dt]
                if len(later) > 0:
                    cum *= 1.0 + (t.pnl_pct / 100.0)
                    equity.loc[later[0]:] = cum
        except (ValueError, TypeError):
            continue

    equity = equity.ffill().fillna(1.0)

    try:
        fig, ax = plt.subplots(figsize=(8, 4), dpi=dpi)
        ax.plot(buy_hold.index, buy_hold.values, color="gray", label="Buy & Hold", linewidth=1.5)
        ax.plot(equity.index, equity.values, color="green", label="Strategy", linewidth=1.5)
        ax.set_title(f"{ticker} – Equity")
        ax.set_ylabel("Cumulative return")
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        logger.warning("build_equity_chart failed for %s: %s", ticker, e)
        return None


def build_daytrade_chart(
    df: pd.DataFrame,
    ticker: str,
    levels: dict[str, float | None],
    config: dict,
    timeframe: str = "1h",
) -> bytes | None:
    """
    Build daytrade chart: candlestick + SuperTrend + stop/TP horizontal lines.

    Args:
        df: OHLCV DataFrame (1h intraday).
        ticker: Display ticker symbol.
        levels: From compute_daytrade_levels; stop_atr, tp_atr for horizontal lines.
        config: Bot config.
        timeframe: "1h" for intraday date formatting.
    """
    charts_cfg = config.get("charts", {})
    lookback = charts_cfg.get("lookback_bars", 60)
    dpi = charts_cfg.get("dpi", 120)

    plot_df = _prepare_df(df, lookback)
    if plot_df is None:
        return None

    params = _get_indicator_params(config)
    addplots: list[Any] = []

    # SuperTrend overlay
    try:
        high = plot_df["High"].reindex(plot_df.index).ffill().bfill()
        low = plot_df["Low"].reindex(plot_df.index).ffill().bfill()
        close = plot_df["Close"].dropna()
        st_series, _ = compute_supertrend(
            high, low, close,
            period=params["supertrend_period"],
            multiplier=params["supertrend_multiplier"],
        )
        st_aligned = st_series.reindex(plot_df.index).ffill()
        if st_aligned.notna().any():
            addplots.append(mpf.make_addplot(st_aligned, color="orange", panel=0, width=0.7, secondary_y=False))
    except Exception as e:
        logger.warning("Daytrade SuperTrend overlay failed: %s", e)

    # Horizontal lines for stop and TP (constant value across all dates)
    stop_atr = levels.get("stop_atr")
    tp_atr = levels.get("tp_atr")
    if stop_atr is not None and stop_atr > 0:
        stop_series = pd.Series(stop_atr, index=plot_df.index)
        addplots.append(mpf.make_addplot(stop_series, color="red", panel=0, linestyle="--", width=0.7, secondary_y=False))
    if tp_atr is not None and tp_atr > 0:
        tp_series = pd.Series(tp_atr, index=plot_df.index)
        addplots.append(mpf.make_addplot(tp_series, color="green", panel=0, linestyle="--", width=0.7, secondary_y=False))

    try:
        buf = io.BytesIO()
        fmt = _chart_format_for_timeframe(timeframe, plot_df.index)
        plot_kw: dict[str, Any] = {
            "type": "candle",
            "volume": False,
            "title": f"{ticker} – Daytrade",
            "style": _CHART_STYLE,
            "figsize": (10, 5),
            "returnfig": True,
            "datetime_format": fmt["datetime_format"],
            "xrotation": fmt["xrotation"],
        }
        if addplots:
            plot_kw["addplot"] = addplots
        fig, _ = mpf.plot(plot_df, **plot_kw)
        margin_text = _margin_text_from_index(plot_df.index, timeframe)
        if margin_text:
            fig.text(0.01, 0.98, margin_text, fontsize=9, color="#888888", va="top", ha="left")
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
        fig.clear()
        import matplotlib.pyplot as plt
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        logger.warning("build_daytrade_chart failed for %s: %s", ticker, e)
        return None
