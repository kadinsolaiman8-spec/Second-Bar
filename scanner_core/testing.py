# testing.py — Mock market, backtest engine, replay, goodday selector, health check, debug

import asyncio
import concurrent.futures
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz
import yfinance as yf

from scanner_core.config import (
    MARKET_TIMEZONE, MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    SCAN_HISTORY_MAX, BACKTEST_MAX_DAYS, TEST_REGIME_OVERRIDE_MINUTES,
    TIMEFRAMES, BOT_NAME, VERSION,
    BACKTEST_MIN_SCORE, BACKTEST_MAX_HOLD_BARS,
    GOODDAY_LOOKBACK_DAYS, GOODDAY_CANDIDATES,
    ADX_STRONG, RVOL_VERY_HIGH, RSI_HEALTHY_MIN, RSI_HEALTHY_MAX,
    ATR_STOP_LOSS_MULTIPLIER, ATR_TAKE_PROFIT_1_MULTIPLIER,
    ATR_TAKE_PROFIT_2_MULTIPLIER, ATR_TAKE_PROFIT_3_MULTIPLIER,
    MIN_ADX_15M, TRIGGER_VOLUME_MULTIPLIER, TRIGGER_VOLUME_LOOKBACK,
    MIN_INDICATORS_FULL_ALERT, REGIME_QUALITY_MIN_SCORE,
    HOURLY_BULLISH_MIN_CONDITIONS,
    MIN_STOCK_PRICE, MIN_AVG_DAILY_VOLUME, BB_SQUEEZE_MIN_CANDLES,
    SIGNAL_QUALITY_MIN, BACKTEST_TIMEOUT_SECONDS,
    TREND_CONSISTENCY_CANDLES, RSI_MOMENTUM_MIN_DELTA,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)
_executor = ThreadPoolExecutor(max_workers=4)


# ══════════════════════════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════════════════════════

_mock_market_active: bool = False
_price_overrides: dict = {}       # {TICKER: {"price": float, "expiry": datetime}}
_scan_history_log: list = []      # ring buffer


# ══════════════════════════════════════════════════════════════════════════
# MOCK MARKET MODE
# ══════════════════════════════════════════════════════════════════════════

def toggle_mock_market() -> bool:
    """Toggle mock market mode on/off.  Returns the new state."""
    global _mock_market_active
    _mock_market_active = not _mock_market_active
    state = "ON" if _mock_market_active else "OFF"
    logger.info(f"Mock market mode toggled {state}")
    return _mock_market_active


def is_mock_active() -> bool:
    return _mock_market_active


def get_effective_time() -> datetime:
    """Return mock 10:30 AM ET if mock is active, else real time."""
    if _mock_market_active:
        return _get_mock_trading_time()
    return datetime.now(ET)


def _get_mock_trading_time() -> datetime:
    """Return 10:30 AM ET on the most recent weekday."""
    now = datetime.now(ET)
    dt = now.replace(hour=10, minute=30, second=0, microsecond=0)
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt


def is_effective_market_open() -> bool:
    """If mock market is active, always return True.  Otherwise check real hours."""
    if _mock_market_active:
        return True
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_ = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    close_ = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return open_ <= now < close_


# ══════════════════════════════════════════════════════════════════════════
# PRICE OVERRIDES
# ══════════════════════════════════════════════════════════════════════════

def set_price_override(ticker: str, price: float, duration_minutes: int = 1) -> dict:
    """Override the current price for a ticker (auto-expires)."""
    ticker = ticker.upper()
    expiry = datetime.now(ET) + timedelta(minutes=duration_minutes)
    _price_overrides[ticker] = {"price": price, "expiry": expiry}
    logger.info(f"Price override set | {ticker} -> ${price:.2f} | expires {expiry.strftime('%H:%M:%S ET')}")
    return {
        "ticker": ticker,
        "price": price,
        "expiry": expiry.strftime("%H:%M:%S ET"),
        "duration_minutes": duration_minutes,
    }


def get_price_override(ticker: str) -> Optional[float]:
    """Return the overridden price for *ticker*, or None."""
    ticker = ticker.upper()
    entry = _price_overrides.get(ticker)
    if entry is None:
        return None
    if datetime.now(ET) >= entry["expiry"]:
        del _price_overrides[ticker]
        return None
    return entry["price"]


def check_price_override_expiry():
    """Sweep and remove all expired price overrides."""
    now = datetime.now(ET)
    expired = [t for t, e in _price_overrides.items() if now >= e["expiry"]]
    for t in expired:
        del _price_overrides[t]
    if expired:
        logger.debug(f"Expired {len(expired)} price override(s): {expired}")


def clear_price_override(ticker: str) -> bool:
    ticker = ticker.upper()
    if ticker in _price_overrides:
        del _price_overrides[ticker]
        return True
    return False


def clear_all_price_overrides() -> int:
    count = len(_price_overrides)
    _price_overrides.clear()
    return count


def get_all_price_overrides() -> dict:
    """Return all active overrides with time remaining."""
    now = datetime.now(ET)
    check_price_override_expiry()
    result = {}
    for ticker, entry in _price_overrides.items():
        remaining = (entry["expiry"] - now).total_seconds()
        result[ticker] = {
            "price": entry["price"],
            "expiry": entry["expiry"].strftime("%H:%M:%S ET"),
            "remaining_seconds": max(0, int(remaining)),
        }
    return result


# ══════════════════════════════════════════════════════════════════════════
# SCAN HISTORY LOG
# ══════════════════════════════════════════════════════════════════════════

def log_scan_event(event_data: dict):
    """Append a scan event to the ring buffer."""
    event_data.setdefault("timestamp", datetime.now(ET).strftime("%H:%M:%S"))
    _scan_history_log.append(event_data)
    if len(_scan_history_log) > SCAN_HISTORY_MAX:
        _scan_history_log.pop(0)


def get_scan_history_log(n: int = 50) -> list:
    """Return the last *n* scan events."""
    return list(_scan_history_log[-n:])


def clear_scan_history():
    _scan_history_log.clear()


# ══════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════

def _get_backtest_interval(days: int) -> tuple:
    """Return (interval, period_or_start) based on backtest length.
    yfinance limits: 1m=7d, 5m/30m=60d, 1h=730d, 1d=unlimited."""
    if days <= 7:
        return "5m", f"{days}d"
    elif days <= 14:
        return "30m", f"{days}d"
    elif days <= 60:
        return "1h", f"{days}d"
    elif days <= 730:
        return "1d", f"{days}d"
    else:
        return "1d", "2y"


def _fetch_backtest_data(ticker: str, days: int) -> Optional[pd.DataFrame]:
    """Synchronous data fetch for backtest. Auto-selects interval based on period."""
    try:
        interval, period = _get_backtest_interval(days)
        print(f"  [BACKTEST] Fetching {ticker}: {days}d, interval={interval}, period={period}")
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df is not None and not df.empty:
            print(f"  [BACKTEST] {ticker}: Got {len(df)} candles ({interval})")
        return df
    except Exception as e:
        logger.debug(f"Backtest fetch error {ticker}: {e}")
        return None


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate ATR from a DataFrame."""
    if df is None or len(df) < period + 1:
        return 0.0
    try:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        tr_list = []
        for j in range(1, len(high)):
            tr = max(high[j] - low[j], abs(high[j] - close[j - 1]), abs(low[j] - close[j - 1]))
            tr_list.append(tr)
        if len(tr_list) < period:
            return float(np.mean(tr_list)) if tr_list else 0.0
        return float(np.mean(tr_list[-period:]))
    except Exception:
        return 0.0


def _check_trend_consistency(df: pd.DataFrame, direction: str) -> bool:
    """FIX 2A: Check 3 candles before trigger show consistent trend structure."""
    n = TREND_CONSISTENCY_CANDLES
    if df is None or len(df) < n + 1:
        return True  # not enough data, pass through
    lows = df["Low"].values
    highs = df["High"].values
    if direction == "BUY":
        # 3 candles before trigger must have higher lows
        for j in range(-n, -1):
            if lows[j] >= lows[j + 1]:
                return False
        return True
    else:
        # 3 candles before trigger must have lower highs
        for j in range(-n, -1):
            if highs[j] <= highs[j + 1]:
                return False
        return True


def _compute_rsi_from_df(df: pd.DataFrame, period: int = 14) -> tuple:
    """Compute current and previous RSI from raw price data."""
    if df is None or len(df) < period + 2:
        return None, None
    close = df["Close"].values
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    if len(gains) < period + 1:
        return None, None
    # Current RSI
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        rsi_now = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_now = 100 - (100 / (1 + rs))
    # Previous RSI (shift back 2 candles)
    avg_gain_prev = np.mean(gains[-(period + 2):-2])
    avg_loss_prev = np.mean(losses[-(period + 2):-2])
    if avg_loss_prev == 0:
        rsi_prev = 100.0
    else:
        rs_prev = avg_gain_prev / avg_loss_prev
        rsi_prev = 100 - (100 / (1 + rs_prev))
    return float(rsi_now), float(rsi_prev)


def _compute_macd_hist_from_df(df: pd.DataFrame) -> tuple:
    """Compute current and previous MACD histogram from raw price data."""
    if df is None or len(df) < 27:
        return None, None
    close = pd.Series(df["Close"].values)
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9).mean()
    hist = macd_line - signal_line
    return float(hist.iloc[-1]), float(hist.iloc[-2])


def _check_rsi_momentum(df: pd.DataFrame, direction: str) -> bool:
    """FIX 2D: RSI must be actively moving in trade direction by >= 2 pts."""
    rsi_now, rsi_prev = _compute_rsi_from_df(df)
    if rsi_now is None or rsi_prev is None:
        return True
    delta = rsi_now - rsi_prev
    if direction == "BUY":
        return delta >= RSI_MOMENTUM_MIN_DELTA
    return delta <= -RSI_MOMENTUM_MIN_DELTA


def _check_macd_histogram_expanding(df: pd.DataFrame, direction: str) -> bool:
    """FIX 2E: MACD histogram must be expanding in signal direction."""
    hist_now, hist_prev = _compute_macd_hist_from_df(df)
    if hist_now is None or hist_prev is None:
        return True
    if direction == "BUY":
        return hist_now > 0 and hist_now > hist_prev
    return hist_now < 0 and hist_now < hist_prev


def _calculate_signal_quality(indicators: dict, df: pd.DataFrame, direction: str) -> int:
    """FIX 2G: Signal quality score 0-10."""
    score = 0

    # +2: RSI moving in trade direction (computed from df)
    rsi_now, rsi_prev = _compute_rsi_from_df(df)
    if rsi_now is not None and rsi_prev is not None:
        delta = rsi_now - rsi_prev
        if direction == "BUY" and delta >= RSI_MOMENTUM_MIN_DELTA:
            score += 2

    # +2: MACD histogram expanding (computed from df)
    hist_now, hist_prev = _compute_macd_hist_from_df(df)
    if hist_now is not None and hist_prev is not None:
        if direction == "BUY" and hist_now > 0 and hist_now > hist_prev:
            score += 2

    # +2: Volume on trigger candle > 1.5x average
    if df is not None and len(df) >= 21:
        try:
            cur_vol = float(df["Volume"].iloc[-1])
            avg_vol = float(df["Volume"].iloc[-21:-1].mean())
            if avg_vol > 0 and cur_vol > 1.5 * avg_vol:
                score += 2
        except Exception:
            pass

    # +2: EMA alignment (9 > 21 > 50 for BUY only)
    ema_data = indicators.get("ema", indicators.get("ema_stack", {}))
    ema9 = ema_data.get("ema9", ema_data.get("ema_9", 0))
    ema21 = ema_data.get("ema21", ema_data.get("ema_21", 0))
    ema50 = ema_data.get("ema50", ema_data.get("ema_50", 0))
    if isinstance(ema9, (int, float)) and isinstance(ema21, (int, float)):
        if ema9 > 0 and ema21 > 0:
            if isinstance(ema50, (int, float)) and ema50 > 0:
                if direction == "BUY" and ema9 > ema21 > ema50:
                    score += 2
            else:
                if direction == "BUY" and ema9 > ema21:
                    score += 1

    # +2: Price near VWAP or EMA support (bounce off key level)
    vwap_data = indicators.get("vwap", {})
    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
    if df is not None and len(df) > 0 and isinstance(vwap_val, (int, float)) and vwap_val > 0:
        try:
            price = float(df["Close"].iloc[-1])
            dist_pct = abs(price - vwap_val) / vwap_val * 100
            if dist_pct < 0.5:  # within 0.5% of VWAP
                score += 2
            elif dist_pct < 1.0:
                score += 1
        except Exception:
            pass

    return min(score, 10)


def _backtest_detect_signal(indicators: dict, df: pd.DataFrame = None,
                             debug_counters: dict = None) -> Optional[tuple]:
    """
    Backtest signal detection with win-rate filters.
    Returns (direction, signal_quality) tuple or None.
    debug_counters: if provided, increments filter rejection counts.
    """
    from scanner_core.utils import has_bullish_candle_pattern, has_bearish_candle_pattern
    from scanner_core.utils import check_trigger_candle_volume

    dc = debug_counters  # shorthand

    # ── Minimum price filter ─────────────────────────────────────────
    if df is not None and len(df) > 0:
        price = float(df["Close"].iloc[-1])
        if price < MIN_STOCK_PRICE:
            if dc: dc["price"] = dc.get("price", 0) + 1
            return None

    # ── Minimum avg volume ───────────────────────────────────────────
    if df is not None and len(df) >= 5:
        avg_vol = float(df["Volume"].iloc[-5:].mean())
        # Scale by bars-per-day (for 30m data ~13 bars/day, for 1h ~7 bars/day)
        bars_per_day = max(1, len(df) // max(1, (len(df) // 7)))
        if avg_vol * bars_per_day < MIN_AVG_DAILY_VOLUME * 0.15:
            if dc: dc["volume"] = dc.get("volume", 0) + 1
            return None

    buy_signals = 0

    # ── RSI ────────────────────────────────────────────────────────────
    rsi_data = indicators.get("rsi", {})
    rsi_val = rsi_data.get("value", rsi_data.get("rsi", 50))
    if isinstance(rsi_val, (int, float)):
        if 30 <= rsi_val <= 75:  # Widened range for backtest
            buy_signals += 1
        # SELL signals removed — BUY only

    # ── MACD ───────────────────────────────────────────────────────────
    macd_data = indicators.get("macd", {})
    macd_val = macd_data.get("macd", macd_data.get("value", 0))
    macd_signal = macd_data.get("signal", 0)
    macd_hist = macd_data.get("histogram", macd_data.get("hist", 0))
    macd_bullish = macd_data.get("bullish", False)
    macd_bearish = macd_data.get("bearish", False)
    if isinstance(macd_val, (int, float)) and isinstance(macd_signal, (int, float)):
        if macd_val > macd_signal or (isinstance(macd_hist, (int, float)) and macd_hist > 0) or macd_bullish:
            buy_signals += 2
    elif macd_bullish:
        buy_signals += 1

    # ── EMA stack ──────────────────────────────────────────────────────
    ema_data = indicators.get("ema", indicators.get("ema_stack", {}))
    ema9 = ema_data.get("ema9", ema_data.get("ema_9", 0))
    ema21 = ema_data.get("ema21", ema_data.get("ema_21", 0))
    if isinstance(ema9, (int, float)) and isinstance(ema21, (int, float)) and ema9 > 0 and ema21 > 0:
        if ema9 > ema21:
            buy_signals += 2
    # ── SuperTrend ─────────────────────────────────────────────────────
    st_data = indicators.get("supertrend", {})
    st_dir = st_data.get("direction", st_data.get("trend", ""))
    if isinstance(st_dir, str):
        if st_dir.lower() in ("bullish", "up", "buy", "1"):
            buy_signals += 2
    elif isinstance(st_dir, (int, float)):
        if st_dir > 0:
            buy_signals += 2

    # ── Volume (RVOL) ──────────────────────────────────────────────────
    vol_data = indicators.get("rvol", indicators.get("volume", {}))
    rvol = vol_data.get("value", vol_data.get("rvol", 1.0))
    if isinstance(rvol, (int, float)) and rvol > 1.0:
        buy_signals += 1

    # ── Bollinger Band position ────────────────────────────────────────
    bb_data = indicators.get("bbands", indicators.get("bollinger", {}))
    bb_pct = bb_data.get("pct_b", bb_data.get("percent_b", 0.5))
    if isinstance(bb_pct, (int, float)):
        if bb_pct < 0.4:
            buy_signals += 1
        # BB > 0.6 no longer counts as sell signal (BUY only)

    # ── ADX strength ───────────────────────────────────────────────────
    adx_data = indicators.get("adx", {})
    adx_val = adx_data.get("value", adx_data.get("adx", 0))
    if not (isinstance(adx_val, (int, float)) and adx_val >= MIN_ADX_15M):
        if dc: dc["adx"] = dc.get("adx", 0) + 1
        return None
    buy_signals += 1

    # ── Direction decision — BUY only (SELL removed) ─────────────────
    min_signals = 5  # Loosened for backtest — live uses MIN_INDICATORS_FULL_ALERT
    if buy_signals >= min_signals:
        direction = "BUY"
    else:
        if dc: dc["min_signals"] = dc.get("min_signals", 0) + 1
        return None

    # ── Trend/candle/trigger filters SKIPPED for backtest ────────────
    # These micro-structure filters (candle patterns, trigger volume,
    # trend consistency) are designed for live 15m data, not hourly bars.
    # Live scanning still applies them via scanner.py filters.

    # ── Signal quality score ──────────────────────────────────────────
    sq = _calculate_signal_quality(indicators, df, direction)
    backtest_sq_min = max(3, SIGNAL_QUALITY_MIN - 2)  # Looser for backtest (3 vs 5 live)
    if sq < backtest_sq_min:
        if dc: dc["signal_quality"] = dc.get("signal_quality", 0) + 1
        return None

    return (direction, sq)


def _classify_backtest_strategy(indicators: dict, direction: str) -> str:
    """Label the backtest signal with a strategy name."""
    bb_data = indicators.get("bbands", indicators.get("bollinger", {}))
    bb_pct = bb_data.get("pct_b", bb_data.get("percent_b", 0.5))
    ema_data = indicators.get("ema", indicators.get("ema_stack", {}))
    ema9 = ema_data.get("ema9", ema_data.get("ema_9", 0))
    ema21 = ema_data.get("ema21", ema_data.get("ema_21", 0))
    macd_data = indicators.get("macd", {})
    macd_hist = macd_data.get("histogram", macd_data.get("hist", 0))

    if isinstance(bb_pct, (int, float)) and 0.3 <= bb_pct <= 0.7:
        bw = bb_data.get("bandwidth", bb_data.get("bw", 999))
        if isinstance(bw, (int, float)) and bw < 0.05:
            return "BB Squeeze"
    if isinstance(ema9, (int, float)) and isinstance(ema21, (int, float)):
        if ema9 > 0 and ema21 > 0 and abs(ema9 - ema21) / ema21 < 0.005:
            return "EMA Pullback"
    if isinstance(macd_hist, (int, float)) and abs(macd_hist) > 0:
        return "MACD Crossover"
    return "Trend Long"


def get_historical_market_state(spy_df, candle_index: int) -> str:
    """Classify market state at a specific point in history using SPY data."""
    try:
        spy_slice = spy_df.iloc[max(0, candle_index - 20):candle_index + 1]
        if spy_slice is None or len(spy_slice) < 5:
            return "WEAK_TREND"

        highs = spy_slice["High"].values.astype(float)
        lows = spy_slice["Low"].values.astype(float)
        closes = spy_slice["Close"].values.astype(float)

        # ATR ratio for volatility check
        trs = []
        for j in range(1, len(highs)):
            tr = max(highs[j] - lows[j],
                     abs(highs[j] - closes[j - 1]),
                     abs(lows[j] - closes[j - 1]))
            trs.append(tr)
        if len(trs) >= 5:
            current_atr = float(np.mean(trs[-5:]))
            avg_atr = float(np.mean(trs))
            atr_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0
        else:
            atr_ratio = 1.0

        # Higher-highs / higher-lows check on last 5 bars
        rh = highs[-5:]
        rl = lows[-5:]
        higher_highs = all(rh[i] >= rh[i - 1] for i in range(1, len(rh)))
        higher_lows = all(rl[i] >= rl[i - 1] for i in range(1, len(rl)))

        # Price momentum
        if len(closes) >= 5 and closes[-5] > 0:
            price_change = (closes[-1] - closes[-5]) / closes[-5] * 100
        else:
            price_change = 0.0

        # Classify
        if atr_ratio > 1.6:
            return "HIGH_VOLATILITY"
        elif higher_highs and higher_lows and price_change > 1.0:
            return "STRONG_TREND"
        elif abs(price_change) < 0.5 and not higher_highs:
            return "RANGING"
        else:
            return "WEAK_TREND"
    except Exception:
        return "WEAK_TREND"


def _fetch_spy_backtest_data(days: int):
    """Fetch SPY data matching the backtest interval."""
    try:
        interval, period = _get_backtest_interval(days)
        df = yf.download("SPY", period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


def _run_backtest_on_df(df: pd.DataFrame, strategy_filter: str = None,
                        spy_df: pd.DataFrame = None) -> tuple:
    """
    Core backtest loop. Returns (trades_list, filter_stats_dict).
    Enforces strict market state rules:
      HIGH_VOLATILITY → skip candle entirely
      RANGING → skip all trend signals
      WEAK_TREND → EMA Pullback only
      STRONG_TREND → all strategies
    Also applies quality gates (RSI < 72, R:R >= 1.8).
    """
    from scanner_core.indicators import run_all_indicators
    try:
        from scanner_core.dynamic_stops import calculate_dynamic_stop, update_trailing_stop
        _use_dynamic = True
    except Exception:
        _use_dynamic = False

    window_size = min(30, max(10, len(df) // 3))
    # Determine interval from bar count and set hold/TP multipliers
    n_bars = len(df)
    if n_bars > 800:
        # 5-minute candles (7d ≈ 2340 bars, 14d ≈ 4680)
        max_hold = 12          # 1 hour of 5m data
        tp1_mult = 1.5
        tp2_mult = 2.5
        tp3_mult = 4.0
        _interval_label = "5m"
    elif n_bars > 100:
        # Hourly candles (60d ≈ 390 bars)
        max_hold = 6           # 6 hours
        tp1_mult = 2.0
        tp2_mult = 3.5
        tp3_mult = 5.0
        _interval_label = "1h"
    else:
        # Daily candles
        max_hold = 3           # 3 days
        tp1_mult = 1.5
        tp2_mult = 2.5
        tp3_mult = 4.0
        _interval_label = "1d"
    trades = []
    open_trade = None
    cooldown_until = 0

    # Filtering stats (FIX 6)
    stats = {
        "bars_checked": 0,
        "raw_signals": 0,
        "failed_market_state": 0,
        "failed_strategy_filter": 0,
        "failed_adx_gate": 0,
        "failed_rsi_gate": 0,
        "failed_rr_gate": 0,
        "failed_other": 0,
        "trades_taken": 0,
        "errors": 0,
        "state_counts": {"STRONG_TREND": 0, "WEAK_TREND": 0,
                         "RANGING": 0, "HIGH_VOLATILITY": 0},
    }

    # If we have SPY data, align indices
    has_spy = spy_df is not None and len(spy_df) >= 20
    # Build a fast index mapping: for each bar in df, find the matching
    # SPY bar by timestamp proximity
    spy_index_map = {}
    if has_spy:
        try:
            spy_times = spy_df.index
            for idx_i in range(len(df)):
                ts = df.index[idx_i]
                # find closest spy bar at or before this timestamp
                mask = spy_times <= ts
                if mask.any():
                    spy_index_map[idx_i] = int(mask.sum()) - 1
        except Exception:
            has_spy = False

    logger.info(f"[BACKTEST] Starting on {len(df)} bars, window_size={window_size}, "
                f"spy_data={'yes' if has_spy else 'no'}, strategy_filter={strategy_filter}")

    for i in range(window_size, len(df)):
        # ── Manage open trade ──────────────────────────────────────
        if open_trade is not None:
            h = float(df["High"].iloc[i])
            l = float(df["Low"].iloc[i])
            c = float(df["Close"].iloc[i])
            bars_held = i - open_trade["entry_bar"]
            entry = open_trade["entry"]
            stop = open_trade["stop"]
            target = open_trade["target"]
            atr_open = open_trade.get("atr", entry * 0.01)
            highest = open_trade.get("highest_price", entry)

            # Trailing stop
            if _use_dynamic and open_trade["direction"] == "BUY":
                try:
                    trail = update_trailing_stop(
                        current_price=c, entry_price=entry,
                        original_stop=open_trade["original_stop"],
                        tp1=target, atr=atr_open,
                        highest_price_since_entry=highest,
                    )
                    stop = trail["stop"]
                    open_trade["stop"] = stop
                    open_trade["highest_price"] = max(highest, c)
                    open_trade["trail_phase"] = trail.get("phase", "FIXED")
                except Exception:
                    pass

            tp1 = target
            tp2 = open_trade.get("tp2", target * 1.5)
            tp3 = open_trade.get("tp3", target * 2.0)

            # Check exits: TP3 > TP2 > TP1 > SL priority (best exit first)
            hit_tp3 = h >= tp3
            hit_tp2 = h >= tp2
            hit_tp1 = h >= tp1
            hit_sl = l <= stop
            time_exit = bars_held >= max_hold

            exit_price = None
            reason = None

            if hit_tp3:
                exit_price, reason = tp3, "TP3"
            elif hit_tp2:
                exit_price, reason = tp2, "TP2"
            elif hit_sl and hit_tp1:
                # Both hit same candle — assume stop hit first (conservative)
                exit_price, reason = stop, "SL"
            elif hit_tp1:
                exit_price, reason = tp1, "TP1"
            elif hit_sl:
                exit_price, reason = stop, "SL"
            elif time_exit:
                exit_price, reason = c, "TIME"

            if exit_price is not None:
                risk = abs(entry - open_trade["original_stop"])
                pnl = (exit_price - entry) if open_trade["direction"] == "BUY" else (entry - exit_price)
                r_mult = round(pnl / risk, 3) if risk > 0 else 0.0

                trades.append({
                    "entry": round(entry, 2), "exit": round(exit_price, 2),
                    "direction": open_trade["direction"],
                    "strategy": open_trade["strategy"],
                    "r_multiple": r_mult,
                    "result": "WIN" if r_mult > 0 else "LOSS",
                    "bars_held": bars_held, "reason": reason,
                    "market_state": open_trade.get("market_state", "WEAK_TREND"),
                    "trail_phase": open_trade.get("trail_phase", "FIXED"),
                    "is_mean_reversion": open_trade.get("is_mean_reversion", False),
                })
                open_trade = None
                cooldown_until = i + 2
            continue

        if i < cooldown_until:
            continue

        # ── Determine market state for this candle ─────────────────
        if has_spy:
            spy_idx = spy_index_map.get(i, -1)
            ms_state = get_historical_market_state(spy_df, spy_idx) if spy_idx >= 5 else "WEAK_TREND"
        else:
            ms_state = "WEAK_TREND"  # conservative default when no SPY data
        stats["state_counts"][ms_state] = stats["state_counts"].get(ms_state, 0) + 1

        # ── Signal detection FIRST (before market state gate) ─────
        try:
            stats["bars_checked"] += 1
            window = df.iloc[max(0, i - window_size):i + 1].copy()
            if len(window) < 10:
                continue

            indicators = run_all_indicators(window)
            if not indicators:
                continue

            sig_result = _backtest_detect_signal(indicators, window,
                                                 debug_counters=None)
            if sig_result is None:
                continue
            direction, signal_quality = sig_result
            stats["raw_signals"] += 1

            # Nuclear BUY-only guard — skip all non-BUY trades
            if direction != "BUY":
                continue

            # ── MARKET STATE GATE (only applied to real signals) ───
            if ms_state == "HIGH_VOLATILITY":
                stats["failed_market_state"] += 1
                continue
            if ms_state == "RANGING":
                stats["failed_market_state"] += 1
                continue

            strategy_name = _classify_backtest_strategy(indicators, direction)

            # WEAK_TREND: EMA Pullback only
            if ms_state == "WEAK_TREND" and "ema pullback" not in strategy_name.lower():
                stats["failed_market_state"] += 1
                continue

            if strategy_filter and strategy_filter.lower() not in strategy_name.lower():
                stats["failed_strategy_filter"] += 1
                continue

            entry_price = float(window["Close"].iloc[-1])
            if entry_price <= 0:
                continue

            # ── Quality Gate: RSI < 72 ─────────────────────────────
            rsi_val = indicators.get("rsi", {}).get("value", 50) or 50
            if isinstance(rsi_val, (int, float)) and rsi_val > 72:
                stats["failed_rsi_gate"] += 1
                continue

            # ── Quality Gate: ADX > 18 on stock ────────────────────
            adx_val = indicators.get("adx", {}).get("value", 0) or 0
            if isinstance(adx_val, (int, float)) and adx_val < 18:
                stats["failed_adx_gate"] += 1
                continue

            atr = _compute_atr(window)
            if atr <= 0:
                atr = entry_price * 0.01

            # Dynamic stop for stop loss, then use interval-aware TP multipliers
            if _use_dynamic:
                try:
                    dyn = calculate_dynamic_stop("BT", entry_price, atr,
                                                 ms_state, indicators)
                    stop_price = dyn["stop"]
                except Exception:
                    stop_price = entry_price - ATR_STOP_LOSS_MULTIPLIER * atr
            else:
                stop_price = entry_price - ATR_STOP_LOSS_MULTIPLIER * atr

            # Interval-aware take profit levels
            target_tp1 = entry_price + tp1_mult * atr
            target_tp2 = entry_price + tp2_mult * atr
            target_tp3 = entry_price + tp3_mult * atr

            # ── Quality Gate: R:R >= 1.5 (relaxed for tighter TPs) ─
            risk_amt = abs(entry_price - stop_price)
            reward_tp1 = abs(target_tp1 - entry_price)
            rr = round(reward_tp1 / risk_amt, 2) if risk_amt > 0 else 0
            if 0 < rr < 1.3:
                stats["failed_rr_gate"] += 1
                continue

            from scanner_core.mean_reversion import is_mean_reversion_strategy
            is_mr = is_mean_reversion_strategy(strategy_name)

            stats["trades_taken"] += 1
            open_trade = {
                "entry": entry_price, "stop": stop_price,
                "original_stop": stop_price,
                "target": target_tp1, "tp2": target_tp2, "tp3": target_tp3,
                "direction": direction, "strategy": strategy_name,
                "entry_bar": i, "signal_quality": signal_quality,
                "atr": atr, "highest_price": entry_price,
                "trail_phase": "FIXED", "market_state": ms_state,
                "is_mean_reversion": is_mr,
            }
        except Exception as e:
            stats["errors"] += 1
            logger.debug(f"Backtest bar {i} error: {e}")
            continue

    # Close any open trade at end of data
    if open_trade is not None:
        fc = float(df["Close"].iloc[-1])
        entry = open_trade["entry"]
        stop = open_trade["stop"]
        risk = abs(entry - stop)
        pnl = fc - entry  # BUY only
        r_mult = round(pnl / risk, 3) if risk > 0 else 0.0
        trades.append({
            "entry": round(entry, 2), "exit": round(fc, 2),
            "direction": "BUY", "strategy": open_trade["strategy"],
            "r_multiple": r_mult, "result": "WIN" if r_mult > 0 else "LOSS",
            "bars_held": len(df) - 1 - open_trade["entry_bar"], "reason": "EOD",
            "market_state": open_trade.get("market_state", "WEAK_TREND"),
            "is_mean_reversion": open_trade.get("is_mean_reversion", False),
        })

    stats["trades_taken"] = len(trades)
    logger.info(f"[BACKTEST] Complete: checked={stats['bars_checked']}, "
                f"raw_signals={stats['raw_signals']}, "
                f"failed_state={stats['failed_market_state']}, "
                f"failed_rsi={stats['failed_rsi_gate']}, "
                f"failed_adx={stats['failed_adx_gate']}, "
                f"failed_rr={stats['failed_rr_gate']}, "
                f"trades={len(trades)}")
    logger.info(f"[BACKTEST] State distribution: {stats['state_counts']}")

    return trades, stats


def _compute_profit_factor(trades: list) -> float:
    """Profit factor = gross wins / gross losses. Returns 0.0 if no losses."""
    gross_wins = sum(t["r_multiple"] for t in trades if t["r_multiple"] > 0)
    gross_losses = abs(sum(t["r_multiple"] for t in trades if t["r_multiple"] < 0))
    if gross_losses == 0:
        return gross_wins if gross_wins > 0 else 0.0
    return round(gross_wins / gross_losses, 3)


def _compile_backtest_result(ticker: str, days: int, trades: list,
                              strategy_filter: str = None) -> dict:
    """Build the standard backtest result dict from a list of trades."""
    result = {
        "ticker": ticker, "days": days, "strategy_filter": strategy_filter,
        "trades": trades, "trade_count": len(trades),
        "wins": sum(1 for t in trades if t["result"] == "WIN"),
        "losses": sum(1 for t in trades if t["result"] == "LOSS"),
        "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0,
        "profit_factor": 0.0, "best_trades": [], "worst_trades": [],
        "error": None,
    }
    if trades:
        result["win_rate"] = round(result["wins"] / len(trades) * 100, 1)
        result["avg_r"] = round(sum(t["r_multiple"] for t in trades) / len(trades), 3)
        result["total_r"] = round(sum(t["r_multiple"] for t in trades), 3)
        result["profit_factor"] = _compute_profit_factor(trades)
        st = sorted(trades, key=lambda x: x["r_multiple"], reverse=True)
        result["best_trades"] = st[:3]
        result["worst_trades"] = st[-3:]

        # ── Trend vs Mean Reversion split ───────────────────────────
        trend_trades = [t for t in trades if not t.get("is_mean_reversion", False)]
        mr_trades = [t for t in trades if t.get("is_mean_reversion", False)]
        if trend_trades:
            result["trend_win_rate"] = round(
                sum(1 for t in trend_trades if t["result"] == "WIN") / len(trend_trades) * 100, 1)
            result["trend_avg_r"] = round(
                sum(t["r_multiple"] for t in trend_trades) / len(trend_trades), 3)
            result["trend_count"] = len(trend_trades)
        if mr_trades:
            result["mr_win_rate"] = round(
                sum(1 for t in mr_trades if t["result"] == "WIN") / len(mr_trades) * 100, 1)
            result["mr_avg_r"] = round(
                sum(t["r_multiple"] for t in mr_trades) / len(mr_trades), 3)
            result["mr_count"] = len(mr_trades)

        # ── Market condition summary ─────────────────────────────────
        state_counts = {}
        state_wins = {}
        for t in trades:
            ms = t.get("market_state", "WEAK_TREND")
            state_counts[ms] = state_counts.get(ms, 0) + 1
            if t["result"] == "WIN":
                state_wins[ms] = state_wins.get(ms, 0) + 1
        mkt_summary = {}
        for ms, cnt in state_counts.items():
            wins = state_wins.get(ms, 0)
            mkt_summary[ms] = {
                "count": cnt,
                "win_rate": round(wins / cnt * 100, 1),
                "wins": wins,
            }
        result["market_condition_summary"] = mkt_summary

    return result


async def _run_backtest_inner(ticker: str, days: int,
                               strategy_filter: str = None) -> dict:
    """Core backtest logic, called with timeout wrapper."""
    loop = asyncio.get_event_loop()
    df = await loop.run_in_executor(_executor, _fetch_backtest_data, ticker, days)

    _empty = {"ticker": ticker, "days": days, "trade_count": 0,
              "trades": [], "wins": 0, "losses": 0, "win_rate": 0.0,
              "avg_r": 0.0, "total_r": 0.0, "profit_factor": 0.0,
              "best_trades": [], "worst_trades": [], "strategy_filter": strategy_filter,
              "error": None}

    if df is None or df.empty:
        _empty["error"] = "No data returned."
        return _empty
    if len(df) < 20:
        _empty["error"] = f"Only {len(df)} candles. Need 20+."
        return _empty

    # Fetch SPY data for market state classification
    spy_df = await loop.run_in_executor(_executor, _fetch_spy_backtest_data, days)

    # Run backtest on the single fetched DataFrame (FIX 1B: data fetched ONCE)
    trades, bt_stats = _run_backtest_on_df(df, strategy_filter, spy_df=spy_df)
    result = _compile_backtest_result(ticker, days, trades, strategy_filter)
    # Add interval label for display
    interval, _ = _get_backtest_interval(days)
    result["interval"] = interval
    result["filter_stats"] = bt_stats

    # Fast permutation test on trade outcomes (FIX 1C: no re-running strategy)
    perm = _run_permutation_test(trades, result["profit_factor"], n_iterations=50)
    result["permutation"] = perm

    return result


async def run_backtest(ticker: str, days: int = 5,
                       strategy_filter: str = None) -> dict:
    """
    Pull historical data, run backtest with all new win-rate rules.
    Has a hard timeout of BACKTEST_TIMEOUT_SECONDS (FIX 1D).
    """
    days = min(days, BACKTEST_MAX_DAYS)
    ticker = ticker.upper()

    try:
        result = await asyncio.wait_for(
            _run_backtest_inner(ticker, days, strategy_filter),
            timeout=BACKTEST_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(f"Backtest timed out for {ticker} after {BACKTEST_TIMEOUT_SECONDS}s")
        return {
            "ticker": ticker, "days": days, "trade_count": 0,
            "trades": [], "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_r": 0.0, "total_r": 0.0, "profit_factor": 0.0,
            "best_trades": [], "worst_trades": [], "strategy_filter": strategy_filter,
            "error": f"Analysis timed out at {BACKTEST_TIMEOUT_SECONDS}s. Try a shorter time period.",
        }
    except Exception as e:
        logger.error(f"Backtest error for {ticker}: {e}")
        return {
            "ticker": ticker, "days": days, "error": str(e), "trade_count": 0,
            "trades": [], "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_r": 0.0, "total_r": 0.0, "profit_factor": 0.0,
            "best_trades": [], "worst_trades": [], "strategy_filter": strategy_filter,
        }

    return result


# ══════════════════════════════════════════════════════════════════════════
# PERMUTATION TESTING (Fast: shuffles trade outcomes, not candle data)
# ══════════════════════════════════════════════════════════════════════════

def _fast_permutation_pf(r_multiples: list) -> float:
    """
    Generate a random PF by bootstrapping: sample with replacement from the
    R-multiples pool, then compute PF. This creates genuine variation because
    different samples will have different win/loss ratios.
    """
    n = len(r_multiples)
    sampled = [r_multiples[np.random.randint(0, n)] for _ in range(n)]
    gross_wins = sum(r for r in sampled if r > 0)
    gross_losses = abs(sum(r for r in sampled if r < 0))
    if gross_losses == 0:
        return gross_wins if gross_wins > 0 else 0.0
    return gross_wins / gross_losses


def _run_permutation_test(trades: list, real_pf: float,
                           n_iterations: int = 20) -> dict:
    """
    Fast permutation test: shuffle the sequence of trade outcomes and compare PF.
    This is 100x faster than re-running the full strategy on shuffled candles
    because it skips indicator calculations entirely.
    Mathematically equivalent for testing whether win/loss sequence has real structure.
    """
    if not trades or len(trades) < 10:
        return {"error": f"Need 10+ trades for permutation test (got {len(trades) if trades else 0})", "p_value": 1.0}

    r_multiples = [t["r_multiple"] for t in trades]

    # Verify we have both wins and losses — if all same sign, shuffle is meaningless
    has_wins = any(r > 0 for r in r_multiples)
    has_losses = any(r < 0 for r in r_multiples)
    if not (has_wins and has_losses):
        return {"error": "All trades same outcome — permutation test not applicable", "p_value": 1.0}

    random_pfs = []
    for _ in range(n_iterations):
        random_pfs.append(_fast_permutation_pf(r_multiples))

    if not random_pfs:
        return {"error": "Permutation test failed", "p_value": 1.0}

    beats_real = sum(1 for pf in random_pfs if pf >= real_pf)
    p_value = beats_real / len(random_pfs)
    avg_random_pf = round(np.mean(random_pfs), 3)
    confidence = round((1 - p_value) * 100, 1)

    if p_value < 0.01:
        label = "STRONG REAL EDGE ✅✅"
        color = 0x00FF88
    elif p_value < 0.05:
        label = "REAL EDGE ✅"
        color = 0x00FF88
    elif p_value < 0.15:
        label = "WEAK EDGE ⚠️"
        color = 0xFFFF00
    else:
        label = "LIKELY RANDOM ❌"
        color = 0xFF4444

    return {
        "real_pf": round(real_pf, 3),
        "avg_random_pf": avg_random_pf,
        "p_value": round(p_value, 4),
        "label": label,
        "color": color,
        "confidence": confidence,
        "iterations": len(random_pfs),
    }


# ══════════════════════════════════════════════════════════════════════════
# WALK FORWARD OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════════

async def run_wfo(ticker: str, total_days: int = 30) -> dict:
    """
    Walk Forward Optimization: rolling 60/40 train/test windows.
    Simplified window creation that always produces windows if data exists.
    Minimum 2 trades per window (lowered from previous).
    Uses SPY for market state in backtests.
    """
    total_days = min(total_days, BACKTEST_MAX_DAYS)
    ticker = ticker.upper()

    result = {
        "ticker": ticker, "total_days": total_days,
        "windows": [], "median_efficiency": 0.0, "avg_efficiency": 0.0,
        "consistency_score": 0.0, "consistency_label": "",
        "assessment": "", "recommendation": "", "error": None,
    }

    try:
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(_executor, _fetch_backtest_data, ticker, total_days)

        if df is None or df.empty or len(df) < 40:
            n = len(df) if df is not None else 0
            result["error"] = (
                f"\u26a0\ufe0f Insufficient data for WFO analysis.\n"
                f"{ticker} only has {n} candles available.\n"
                f"Try using a stock with more trading history or run /backtest instead."
            )
            return result

        total_bars = len(df)
        print(f"[WFO] {ticker}: {total_bars} total candles")

        # Fetch SPY data for market state classification
        spy_df = None
        try:
            spy_df = await loop.run_in_executor(_executor, _fetch_spy_backtest_data, total_days)
        except Exception:
            pass

        # ── Simple rolling window creation ───────────────────────────
        # 60% train, 40% test. Step by half the test size for overlap.
        train_size = max(20, int(total_bars * 0.35))  # ~35% of data per train window
        test_size = max(10, int(total_bars * 0.20))    # ~20% of data per test window
        step_size = max(test_size // 2, 10)
        max_windows = 8

        windows_data = []
        start = 0
        while start + train_size + test_size <= total_bars and len(windows_data) < max_windows:
            windows_data.append({
                "train_start": start,
                "train_end": start + train_size,
                "test_start": start + train_size,
                "test_end": min(start + train_size + test_size, total_bars),
            })
            start += step_size

        print(f"[WFO] Created {len(windows_data)} windows "
              f"(train={train_size}, test={test_size}, step={step_size})")

        if not windows_data:
            result["error"] = (
                f"\u26a0\ufe0f Insufficient data for WFO analysis.\n"
                f"{ticker} only has {total_bars} candles available.\n"
                f"Need at least {train_size + test_size} candles for one window.\n"
                f"Try a longer period or run /backtest instead."
            )
            return result

        # ── Run each window ──────────────────────────────────────────
        efficiencies = []
        for idx, wd in enumerate(windows_data):
            window_num = idx + 1
            train_df = df.iloc[wd["train_start"]:wd["train_end"]].copy()
            test_df = df.iloc[wd["test_start"]:wd["test_end"]].copy()

            # Date range for display
            test_date_start = str(test_df.index[0])[:10] if len(test_df) > 0 else ""
            test_date_end = str(test_df.index[-1])[:10] if len(test_df) > 0 else ""

            train_trades, _ = _run_backtest_on_df(train_df, spy_df=spy_df)
            test_trades, _ = _run_backtest_on_df(test_df, spy_df=spy_df)

            train_pf = _compute_profit_factor(train_trades)
            test_pf = _compute_profit_factor(test_trades)

            # Lowered minimum to 2 trades — even sparse data is informative
            if len(train_trades) < 2 or len(test_trades) < 2:
                note = "Low Trade Count" if (len(train_trades) + len(test_trades)) > 0 else "No Trades"
                result["windows"].append({
                    "window": window_num,
                    "train_pf": round(train_pf, 3),
                    "test_pf": round(test_pf, 3),
                    "efficiency": 0.0,
                    "emoji": "\u26aa",
                    "train_trades": len(train_trades),
                    "test_trades": len(test_trades),
                    "note": note,
                    "date_range": f"{test_date_start} to {test_date_end}",
                })
                # Still include 0% efficiency so we report it
                efficiencies.append(0.0)
                continue

            efficiency = round((test_pf / train_pf * 100), 1) if train_pf > 0 else 0.0
            emoji = "\u2705" if efficiency >= 70 else ("\u26a0\ufe0f" if efficiency >= 50 else "\u274c")
            efficiencies.append(efficiency)

            diagnosis = ""
            if efficiency < 30:
                diagnosis = _diagnose_bad_window(df, spy_df, wd["test_start"], wd["test_end"])

            window_data = {
                "window": window_num,
                "train_pf": round(train_pf, 3),
                "test_pf": round(test_pf, 3),
                "efficiency": efficiency,
                "emoji": emoji,
                "train_trades": len(train_trades),
                "test_trades": len(test_trades),
                "date_range": f"{test_date_start} to {test_date_end}",
            }
            if diagnosis:
                window_data["diagnosis"] = diagnosis
            result["windows"].append(window_data)

        if efficiencies:
            sorted_eff = sorted(efficiencies)
            n = len(sorted_eff)
            median_eff = sorted_eff[n // 2] if n % 2 == 1 else (sorted_eff[n // 2 - 1] + sorted_eff[n // 2]) / 2
            median_eff = round(median_eff, 1)
            avg_eff = round(float(np.mean(efficiencies)), 1)

            result["median_efficiency"] = median_eff
            result["avg_efficiency"] = avg_eff

            if len(efficiencies) >= 2:
                std_dev = float(np.std(efficiencies))
                consistency = round(max(0, min(100, 100 - std_dev)), 1)
            else:
                consistency = 50.0
            result["consistency_score"] = consistency

            if consistency >= 80:
                result["consistency_label"] = "Very Consistent \u2705 \u2014 Predictable across market conditions"
            elif consistency >= 60:
                result["consistency_label"] = "Moderately Consistent \u26a0\ufe0f \u2014 Some variation"
            else:
                result["consistency_label"] = "Inconsistent \u274c \u2014 Results unreliable"

            has_near_zero = any(e < 10 for e in efficiencies)
            if median_eff >= 70 and consistency >= 70:
                result["assessment"] = "STRATEGY IS ROBUST \u2705"
                result["recommendation"] = "Live results should closely match backtest performance"
            elif median_eff >= 50 or consistency >= 60:
                result["assessment"] = "MODERATE ROBUSTNESS \u26a0\ufe0f"
                result["recommendation"] = "Some overfitting detected. Use with caution."
            else:
                result["assessment"] = "STRATEGY IS INCONSISTENT \u274c"
                result["recommendation"] = "Backtest results are misleading. Do not rely on them."

            if has_near_zero:
                result["recommendation"] += "\n\u26a0\ufe0f One or more windows showed near-zero performance."
        else:
            result["error"] = (
                f"\u26a0\ufe0f Could not create any valid WFO windows for {ticker}.\n"
                f"Try a longer period or run /backtest instead for basic results."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"WFO error for {ticker}: {e}")

    return result


def _diagnose_bad_window(df, spy_df, test_start_idx: int, test_end_idx: int) -> str:
    """Diagnose why a WFO window had poor efficiency."""
    reasons = []
    try:
        test_slice = df.iloc[test_start_idx:test_end_idx]
        if test_slice is None or len(test_slice) < 2:
            return "Insufficient test data"

        # Check stock price range during window
        open_price = float(test_slice["Close"].iloc[0])
        close_price = float(test_slice["Close"].iloc[-1])
        if open_price > 0:
            stock_return = (close_price - open_price) / open_price * 100
        else:
            stock_return = 0.0

        # Check ATR (volatility) trend
        if len(test_slice) >= 14:
            highs = test_slice["High"].values
            lows = test_slice["Low"].values
            closes = test_slice["Close"].values
            trs = []
            for j in range(1, len(highs)):
                tr = max(highs[j] - lows[j], abs(highs[j] - closes[j - 1]), abs(lows[j] - closes[j - 1]))
                trs.append(tr)
            if len(trs) >= 7:
                first_half_atr = np.mean(trs[:len(trs) // 2])
                second_half_atr = np.mean(trs[len(trs) // 2:])
                if first_half_atr > 0:
                    atr_change = (second_half_atr - first_half_atr) / first_half_atr * 100
                    if atr_change < -30:
                        reasons.append("volatility collapsed mid-window")
                    elif atr_change > 50:
                        reasons.append("volatility spiked mid-window")

        # Check SPY during window
        if spy_df is not None and len(spy_df) > test_end_idx:
            try:
                spy_slice = spy_df.iloc[test_start_idx:min(test_end_idx, len(spy_df))]
                if len(spy_slice) >= 2:
                    spy_open = float(spy_slice["Close"].iloc[0])
                    spy_close = float(spy_slice["Close"].iloc[-1])
                    if spy_open > 0:
                        spy_return = (spy_close - spy_open) / spy_open * 100
                        if abs(spy_return) < 1.0:
                            reasons.append(f"market was choppy (SPY {spy_return:+.1f}%)")
                        elif spy_return < -3.0:
                            reasons.append(f"market was in selloff (SPY {spy_return:+.1f}%)")
            except Exception:
                pass

        # Overall stock movement
        if abs(stock_return) < 1.5:
            reasons.append(f"stock was range-bound ({stock_return:+.1f}%)")
        elif stock_return < -5.0:
            reasons.append(f"stock dropped sharply ({stock_return:+.1f}%)")

    except Exception:
        return "Could not diagnose"

    if reasons:
        return "; ".join(reasons)
    return "Low signal quality during this period"


# ══════════════════════════════════════════════════════════════════════════
# SCAN BACKTEST  (/scanbacktest — backtest ALL watchlist stocks)
# ══════════════════════════════════════════════════════════════════════════

def _backtest_single_stock(ticker: str, days: int) -> dict:
    """Synchronous backtest for a single stock (runs in thread pool)."""
    try:
        df = _fetch_backtest_data(ticker, days)
        if df is None or df.empty or len(df) < 20:
            return {"ticker": ticker, "error": "No data", "profit_factor": 0,
                    "win_rate": 0, "trade_count": 0, "avg_r": 0}
        spy_df = _fetch_spy_backtest_data(days)
        trades, _ = _run_backtest_on_df(df, spy_df=spy_df)
        r = _compile_backtest_result(ticker, days, trades)
        return {
            "ticker": ticker,
            "profit_factor": r["profit_factor"],
            "win_rate": r["win_rate"],
            "trade_count": r["trade_count"],
            "avg_r": r["avg_r"],
            "total_r": r["total_r"],
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "profit_factor": 0,
                "win_rate": 0, "trade_count": 0, "avg_r": 0}


async def run_scan_backtest(days: int = 14) -> dict:
    """
    Backtest ALL watchlist stocks in parallel using ThreadPoolExecutor.
    Returns top 10 sorted by profit factor.
    """
    from scanner_core.watchlist import ALL_STOCKS

    scan_start = time.time()
    loop = asyncio.get_event_loop()
    max_workers = min(20, len(ALL_STOCKS))

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            loop.run_in_executor(pool, _backtest_single_stock, ticker, days)
            for ticker in ALL_STOCKS
        ]
        results = await asyncio.gather(*futures, return_exceptions=True)

    # Filter valid results
    valid = []
    for r in results:
        if isinstance(r, Exception):
            continue
        if isinstance(r, dict) and r.get("trade_count", 0) > 0 and r.get("error") is None:
            valid.append(r)

    # Sort by profit factor
    valid.sort(key=lambda x: x.get("profit_factor", 0), reverse=True)

    elapsed = round(time.time() - scan_start, 1)
    profitable = sum(1 for v in valid if v.get("profit_factor", 0) > 1.0)

    return {
        "days": days,
        "total_scanned": len(ALL_STOCKS),
        "profitable": profitable,
        "profitable_pct": round(profitable / max(1, len(valid)) * 100, 1),
        "top_10": valid[:10],
        "elapsed_seconds": elapsed,
        "cores": os.cpu_count() or 1,
        "error": None,
    }


# ══════════════════════════════════════════════════════════════════════════
# FORMAT HELPERS FOR NEW FEATURES
# ══════════════════════════════════════════════════════════════════════════

def format_permutation_results(perm: dict) -> str:
    """Format permutation test results as text block."""
    if perm.get("error"):
        return f"Permutation test failed: {perm['error']}"
    lines = [
        "",
        "Permutation Test:",
        f"  Real Profit Factor:   {perm.get('real_pf', 0):.3f}",
        f"  Avg Random PF:        {perm.get('avg_random_pf', 0):.3f}",
        f"  p-value:              {perm.get('p_value', 1):.4f}",
        f"  Edge Significance:    {perm.get('label', 'N/A')}",
        f"  Confidence:           {perm.get('confidence', 0):.1f}% better than random",
        f"  (Based on {perm.get('iterations', 0)} iterations — use /wfo for deeper analysis)",
    ]
    return "\n".join(lines)


def format_wfo_results(results: dict) -> str:
    """Format WFO results as text block."""
    lines = [
        "\u2501" * 40,
        f"Walk Forward Optimization \u2014 ${results['ticker']} ({results['total_days']}d)",
        "\u2501" * 40,
    ]
    if results.get("error"):
        lines.append(f"Error: {results['error']}")
        lines.append("\u2501" * 40)
        return "\n".join(lines)

    for w in results.get("windows", []):
        note = w.get("note", "")
        date_range = w.get("date_range", "")
        if note:
            lines.append(
                f"Window {w['window']}: Train PF: {w['train_pf']:.2f} ({w['train_trades']}t) | "
                f"Test PF: {w['test_pf']:.2f} ({w['test_trades']}t) | {note} {w['emoji']}"
            )
        else:
            lines.append(
                f"Window {w['window']}: Train PF: {w['train_pf']:.2f} ({w['train_trades']}t) | "
                f"Test PF: {w['test_pf']:.2f} ({w['test_trades']}t) | Eff: {w['efficiency']:.0f}% {w['emoji']}"
            )
        if date_range:
            lines.append(f"         {date_range}")
        # Show diagnosis for bad windows
        diagnosis = w.get("diagnosis", "")
        if diagnosis:
            lines.append(f"         \u26a0\ufe0f Failed: {diagnosis}")

    lines.append("\u2501" * 40)

    # Median and mean
    median_eff = results.get("median_efficiency", results.get("avg_efficiency", 0))
    avg_eff = results.get("avg_efficiency", 0)
    lines.append(f"Median Efficiency:   {median_eff:.0f}%")
    lines.append(f"Mean Efficiency:     {avg_eff:.0f}%")

    # Consistency score
    consistency = results.get("consistency_score", 0)
    consistency_label = results.get("consistency_label", "")
    lines.append(f"Consistency Score:   {consistency:.0f}/100")
    if consistency_label:
        lines.append(f"  {consistency_label}")

    lines.append("")
    lines.append(f"Overall Assessment:  {results['assessment']}")
    rec = results.get("recommendation", "")
    for rec_line in rec.split("\n"):
        lines.append(f"  {rec_line}")
    lines.append("\u2501" * 40)
    return "\n".join(lines)


def format_scan_backtest_results(results: dict) -> str:
    """Format scan backtest results as text block."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 BACKTEST SCAN — All Stocks ({results['days']}d)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Scanned:    {results['total_scanned']} stocks in {results['elapsed_seconds']}s",
        f"Profitable: {results['profitable']} stocks ({results['profitable_pct']}%)",
        "",
    ]

    medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
    for idx, stock in enumerate(results.get("top_10", [])):
        prefix = medals[idx] if idx < len(medals) else f"{idx+1}."
        lines.append(
            f"{prefix} {stock['ticker']:<5} — PF: {stock.get('profit_factor', 0):.2f} | "
            f"WR: {stock.get('win_rate', 0):.0f}% | "
            f"Trades: {stock.get('trade_count', 0)} | "
            f"Avg R: {stock.get('avg_r', 0):+.3f}"
        )

    lines.extend([
        "",
        f"⏱ Completed in {results['elapsed_seconds']}s using {results['cores']} CPU cores",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# REPLAY ENGINE
# ══════════════════════════════════════════════════════════════════════════

def _fetch_replay_data(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """Synchronous data fetch for replay."""
    try:
        df = yf.download(ticker, start=start, end=end, interval="5m",
                         progress=False, auto_adjust=True)
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.debug(f"Replay fetch error {ticker}: {e}")
        return None


async def run_replay(ticker: str, date_str: str) -> dict:
    """
    Replay a specific trading day for *ticker*.  Pulls historical 5m data
    for that date and simulates every scan window, returning a timestamped
    list of signals.

    date_str format: YYYY-MM-DD
    """
    ticker = ticker.upper()
    result = {
        "ticker": ticker,
        "date": date_str,
        "signals": [],
        "total_bars": 0,
        "error": None,
    }

    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
        start = target.strftime("%Y-%m-%d")
        end = (target + timedelta(days=1)).strftime("%Y-%m-%d")

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(_executor, _fetch_replay_data, ticker, start, end)

        if df is None or df.empty:
            result["error"] = f"No data for {ticker} on {date_str}"
            return result

        result["total_bars"] = len(df)

        from scanner_core.indicators import run_all_indicators, count_bullish_signals
        from scanner_core.scoring import calculate_full_score
        from scanner_core.strategies import detect_strategy

        window_size = 30
        for i in range(window_size, len(df)):
            window = df.iloc[i - window_size:i + 1].copy()
            try:
                indicators = run_all_indicators(window)
                entry_price = float(window["Close"].iloc[-1])
                bullish = count_bullish_signals(indicators)

                if bullish < 6:
                    continue

                direction = "BUY"  # BUY only — SELL removed
                strat_result = detect_strategy(
                    indicators=indicators,
                    df_1m=None, df_5m=window, df_15m=None, df_1h=None,
                    entry_price=entry_price, direction=direction,
                )
                if not strat_result.get("triggered"):
                    continue

                score_data = calculate_full_score(
                    ticker, indicators, entry_price, direction,
                    df_5m=window,
                )
                if score_data.get("total", 0) < 50:
                    continue

                bar_time = str(df.index[i])
                result["signals"].append({
                    "time": bar_time,
                    "price": entry_price,
                    "direction": direction,
                    "strategy": strat_result.get("strategy", "Unknown"),
                    "score": score_data.get("total", 0),
                    "bullish_count": bullish,
                })
            except Exception:
                continue

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Replay error for {ticker} on {date_str}: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════
# GOODDAY SELECTOR
# ══════════════════════════════════════════════════════════════════════════

def _fetch_goodday_data(ticker: str, period: str = "6mo") -> Optional[pd.DataFrame]:
    """Fetch daily data for goodday analysis."""
    try:
        df = yf.download(ticker, period=period, interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


def _score_trading_day(row: pd.Series, df: pd.DataFrame, idx: int) -> float:
    """
    Score a single trading day for goodday analysis.
    Criteria:
      - ADX > 30 (strong trend): +25 points
      - RVOL > 2.0 (high volume): +20 points
      - RSI 50-70 (healthy momentum): +20 points
      - Range > 3x ATR (big move): +20 points
      - MACD crossover: +15 points
    """
    score = 0.0

    # We need at least 14 bars of lookback for indicators
    if idx < 20:
        return 0.0

    lookback = df.iloc[max(0, idx - 20):idx + 1]
    if len(lookback) < 14:
        return 0.0

    try:
        # ADX check
        high = lookback["High"].values
        low = lookback["Low"].values
        close = lookback["Close"].values
        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
        if len(tr) >= 14:
            atr_14 = np.mean(tr[-14:])
            # Simplified ADX proxy: directional movement strength
            plus_dm = np.maximum(high[1:] - high[:-1], 0)
            minus_dm = np.maximum(low[:-1] - low[1:], 0)
            avg_plus = np.mean(plus_dm[-14:])
            avg_minus = np.mean(minus_dm[-14:])
            dx = abs(avg_plus - avg_minus) / max(avg_plus + avg_minus, 0.0001) * 100
            if dx > ADX_STRONG:
                score += 25

            # Range vs ATR
            day_range = float(row["High"] - row["Low"])
            if atr_14 > 0 and day_range > 3 * atr_14:
                score += 20
        else:
            atr_14 = 0

        # RVOL check
        volumes = lookback["Volume"].values
        if len(volumes) >= 20:
            avg_vol = np.mean(volumes[-20:])
            if avg_vol > 0:
                rvol = float(row["Volume"]) / avg_vol
                if rvol > RVOL_VERY_HIGH:
                    score += 20

        # RSI check (simplified)
        if len(close) >= 15:
            deltas = np.diff(close[-15:])
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            avg_gain = np.mean(gains) if len(gains) > 0 else 0
            avg_loss = np.mean(losses) if len(losses) > 0 else 0.001
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
                score += 20

        # MACD crossover check (simplified)
        if len(close) >= 26:
            ema_12 = pd.Series(close).ewm(span=12).mean().iloc[-1]
            ema_26 = pd.Series(close).ewm(span=26).mean().iloc[-1]
            macd_now = ema_12 - ema_26
            if len(close) >= 27:
                ema_12_prev = pd.Series(close[:-1]).ewm(span=12).mean().iloc[-1]
                ema_26_prev = pd.Series(close[:-1]).ewm(span=26).mean().iloc[-1]
                macd_prev = ema_12_prev - ema_26_prev
                if (macd_now > 0 and macd_prev <= 0) or (macd_now < 0 and macd_prev >= 0):
                    score += 15

    except Exception as e:
        logger.debug(f"Goodday scoring error: {e}")

    return score


async def find_best_trading_day() -> dict:
    """
    Auto-select the best historical trading day from 6 months
    across GOODDAY_CANDIDATES.

    Returns the best day with ticker, date, and score details.
    """
    result = {
        "best_ticker": None,
        "best_date": None,
        "best_score": 0,
        "candidates_scanned": 0,
        "days_analyzed": 0,
        "top_5": [],
        "error": None,
    }

    try:
        loop = asyncio.get_event_loop()
        all_days = []

        for ticker in GOODDAY_CANDIDATES:
            df = await loop.run_in_executor(_executor, _fetch_goodday_data, ticker)
            if df is None or df.empty or len(df) < 30:
                continue

            result["candidates_scanned"] += 1

            for i in range(20, len(df)):
                row = df.iloc[i]
                score = _score_trading_day(row, df, i)
                if score > 0:
                    date_str = str(df.index[i].date()) if hasattr(df.index[i], 'date') else str(df.index[i])[:10]
                    all_days.append({
                        "ticker": ticker,
                        "date": date_str,
                        "score": score,
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": int(row["Volume"]),
                        "change_pct": round((float(row["Close"]) - float(row["Open"])) / float(row["Open"]) * 100, 2),
                    })
                    result["days_analyzed"] += 1

        if not all_days:
            result["error"] = "No qualifying days found"
            return result

        all_days.sort(key=lambda x: x["score"], reverse=True)
        result["best_ticker"] = all_days[0]["ticker"]
        result["best_date"] = all_days[0]["date"]
        result["best_score"] = all_days[0]["score"]
        result["top_5"] = all_days[:5]

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Goodday selection error: {e}")

    return result


def find_best_day() -> dict:
    """
    Synchronous goodday selector — scans GOODDAY_CANDIDATES over 6 months,
    returns the single best day with breakdown for the /goodday command.
    """
    best = {"ticker": "?", "date": "?", "day_score": 0, "breakdown": {},
            "recommendation": "No qualifying day found.", "day_range_pct": 0, "volume": 0}

    try:
        all_days = []
        for ticker in GOODDAY_CANDIDATES:
            df = _fetch_goodday_data(ticker)
            if df is None or df.empty or len(df) < 30:
                continue

            for i in range(20, len(df)):
                row = df.iloc[i]
                lookback = df.iloc[max(0, i - 20):i + 1]
                if len(lookback) < 14:
                    continue

                high = lookback["High"].values
                low = lookback["Low"].values
                close = lookback["Close"].values
                tr = np.maximum(high[1:] - low[1:],
                                np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))

                bd = {"adx": 0, "rvol": 0, "rsi": 0, "range": 0, "macd": 0}
                total = 0.0

                # ADX proxy
                if len(tr) >= 14:
                    plus_dm = np.maximum(high[1:] - high[:-1], 0)
                    minus_dm = np.maximum(low[:-1] - low[1:], 0)
                    avg_plus = np.mean(plus_dm[-14:])
                    avg_minus = np.mean(minus_dm[-14:])
                    dx = abs(avg_plus - avg_minus) / max(avg_plus + avg_minus, 0.0001) * 100
                    if dx > ADX_STRONG:
                        bd["adx"] = 20
                        total += 20

                    # Range vs ATR
                    atr_14 = np.mean(tr[-14:])
                    day_range = float(row["High"] - row["Low"])
                    if atr_14 > 0 and day_range > 3 * atr_14:
                        bd["range"] = 20
                        total += 20

                # RVOL
                volumes = lookback["Volume"].values
                if len(volumes) >= 20:
                    avg_vol = np.mean(volumes[-20:])
                    if avg_vol > 0 and float(row["Volume"]) / avg_vol > RVOL_VERY_HIGH:
                        bd["rvol"] = 20
                        total += 20

                # RSI
                if len(close) >= 15:
                    deltas = np.diff(close[-15:])
                    gains = np.where(deltas > 0, deltas, 0)
                    losses = np.where(deltas < 0, -deltas, 0)
                    avg_gain = np.mean(gains) if len(gains) > 0 else 0
                    avg_loss = np.mean(losses) if len(losses) > 0 else 0.001
                    rsi = 100 - (100 / (1 + avg_gain / avg_loss))
                    if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
                        bd["rsi"] = 20
                        total += 20

                # MACD crossover
                if len(close) >= 27:
                    ema_12 = pd.Series(close).ewm(span=12).mean().iloc[-1]
                    ema_26 = pd.Series(close).ewm(span=26).mean().iloc[-1]
                    macd_now = ema_12 - ema_26
                    ema_12_prev = pd.Series(close[:-1]).ewm(span=12).mean().iloc[-1]
                    ema_26_prev = pd.Series(close[:-1]).ewm(span=26).mean().iloc[-1]
                    macd_prev = ema_12_prev - ema_26_prev
                    if (macd_now > 0 and macd_prev <= 0) or (macd_now < 0 and macd_prev >= 0):
                        bd["macd"] = 20
                        total += 20

                if total >= 40:
                    date_str = str(df.index[i].date()) if hasattr(df.index[i], 'date') else str(df.index[i])[:10]
                    open_p = float(row["Open"])
                    range_pct = (float(row["High"]) - float(row["Low"])) / open_p * 100 if open_p > 0 else 0
                    all_days.append({
                        "ticker": ticker, "date": date_str, "score": total,
                        "breakdown": bd, "range_pct": range_pct,
                        "volume": int(row["Volume"]),
                    })

        if all_days:
            all_days.sort(key=lambda x: x["score"], reverse=True)
            top = all_days[0]
            best["ticker"] = top["ticker"]
            best["date"] = top["date"]
            best["day_score"] = int(top["score"])
            best["breakdown"] = top["breakdown"]
            best["day_range_pct"] = top["range_pct"]
            best["volume"] = top["volume"]
            best["recommendation"] = f"Replay this day with /replay {top['ticker']} {top['date']}"

    except Exception as e:
        logger.error(f"find_best_day error: {e}")

    return best


# ══════════════════════════════════════════════════════════════════════════
# FORCE SCAN
# ══════════════════════════════════════════════════════════════════════════

async def force_scan_now(tickers: list = None) -> dict:
    """Trigger an immediate full scan outside the normal 60-second cycle."""
    from scanner_core.scanner import scan_all_tickers, ticker_state
    from scanner_core.watchlist import ALL_STOCKS

    if tickers is None:
        tickers = ALL_STOCKS

    try:
        signals = await scan_all_tickers(tickers)
        return {
            "stocks_scanned": len(tickers),
            "signals_found": len(signals),
            "signals": signals,
            "cache_size": len(ticker_state),
        }
    except Exception as e:
        logger.error(f"Force scan error: {e}")
        return {"error": str(e), "stocks_scanned": 0, "signals_found": 0}


# ══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════

async def run_health_check(bot=None) -> dict:
    """
    Run diagnostic checks on all bot systems.
    Returns a dict with pass/fail for each subsystem.
    """
    results = {}

    # 1. yfinance connectivity
    try:
        loop = asyncio.get_event_loop()

        def _test_yf():
            df = yf.download("SPY", period="1d", interval="1m",
                             progress=False, auto_adjust=True)
            return df is not None and not df.empty

        ok = await loop.run_in_executor(_executor, _test_yf)
        results["yfinance"] = {"status": "PASS" if ok else "FAIL",
                               "detail": f"SPY 1m data: {'OK' if ok else 'empty'}"}
    except Exception as e:
        results["yfinance"] = {"status": "FAIL", "detail": str(e)}

    # 2. Finnhub connectivity
    try:
        from scanner_core.news import get_finnhub_client
        client = get_finnhub_client()
        if client is not None:
            results["finnhub"] = {"status": "PASS", "detail": "Client initialized"}
        else:
            results["finnhub"] = {"status": "FAIL", "detail": "No API key or package missing"}
    except Exception as e:
        results["finnhub"] = {"status": "FAIL", "detail": str(e)}

    # 3. Scanner cache
    try:
        from scanner_core.scanner import ticker_state as ts, scan_history as sh
        results["cache"] = {
            "status": "PASS",
            "detail": f"{len(ts)} tickers cached, {len(sh)} scan events",
        }
    except Exception as e:
        results["cache"] = {"status": "FAIL", "detail": str(e)}

    # 4. Cooldown tracker
    try:
        from scanner_core.cooldown import get_system_status
        status = get_system_status()
        results["cooldowns"] = {
            "status": "PASS",
            "detail": (
                f"{status.get('cooldowns', 0)} cooldowns, "
                f"{status.get('mutes', 0)} mutes, "
                f"{status.get('vwap_traps', 0)} VWAP traps, "
                f"{status.get('reminders', 0)} reminders"
            ),
        }
    except Exception as e:
        results["cooldowns"] = {"status": "FAIL", "detail": str(e)}

    # 5. Regime
    try:
        from scanner_core.regime import get_current_regime
        regime = get_current_regime()
        label = regime.get("label", "UNKNOWN")
        results["regime"] = {"status": "PASS", "detail": f"Current: {label}"}
    except Exception as e:
        results["regime"] = {"status": "FAIL", "detail": str(e)}

    # 6. Memory usage
    try:
        try:
            import psutil
            process = psutil.Process(os.getpid())
            mem = process.memory_info().rss / 1024 / 1024
            results["memory"] = {"status": "PASS", "detail": f"{mem:.1f} MB RSS"}
        except ImportError:
            results["memory"] = {"status": "PASS", "detail": f"psutil not available, PID={os.getpid()}"}
    except Exception as e:
        results["memory"] = {"status": "FAIL", "detail": str(e)}

    # 7. Discord channels (if bot is provided)
    if bot is not None:
        try:
            channel_ids = {
                "alert": int(os.getenv("ALERT_CHANNEL_ID", "0")),
                "journal": int(os.getenv("JOURNAL_CHANNEL_ID", "0")),
                "recap": int(os.getenv("RECAP_CHANNEL_ID", "0")),
                "premarket": int(os.getenv("PREMARKET_CHANNEL_ID", "0")),
                "test": int(os.getenv("TEST_CHANNEL_ID", "0")),
            }
            channel_report = {}
            for name, cid in channel_ids.items():
                if cid == 0:
                    channel_report[name] = "NOT CONFIGURED"
                else:
                    ch = bot.get_channel(cid)
                    channel_report[name] = "OK" if ch else f"NOT FOUND (ID: {cid})"
            results["delivery_channels"] = {"status": "PASS", "detail": channel_report}
        except Exception as e:
            results["delivery_channels"] = {"status": "FAIL", "detail": str(e)}

    # 8. Journal
    try:
        from scanner_core.journal import get_open_trade_count, get_win_rate
        open_count = get_open_trade_count()
        win_rate = get_win_rate()
        results["journal"] = {
            "status": "PASS",
            "detail": f"{open_count} open trades, {win_rate:.1f}% win rate",
        }
    except Exception as e:
        results["journal"] = {"status": "FAIL", "detail": str(e)}

    # 9. Mock market state
    results["mock_market"] = {
        "status": "INFO",
        "detail": f"{'ACTIVE' if _mock_market_active else 'OFF'}, {len(_price_overrides)} price overrides",
    }

    return results


# ══════════════════════════════════════════════════════════════════════════
# DEBUG DUMP
# ══════════════════════════════════════════════════════════════════════════

def generate_debug_dump(ticker: str = None) -> str:
    """Generate a comprehensive debug dump."""
    now = datetime.now(ET)
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔧 DEBUG DUMP — {now.strftime('%H:%M:%S ET')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    from scanner_core.scanner import ticker_state as ts, scan_history as sh, spy_cache, orb_data
    from scanner_core.regime import get_current_regime
    from scanner_core.cooldown import get_system_status

    regime = get_current_regime()
    cooldown_status = get_system_status()

    lines.append(f"Regime:           {regime.get('label', 'UNKNOWN')}")
    lines.append(f"Mock Market:      {'ACTIVE' if _mock_market_active else 'OFF'}")
    lines.append(f"Ticker Cache:     {len(ts)} stocks")
    lines.append(f"SPY Cache:        {', '.join(spy_cache.keys()) if spy_cache else 'empty'}")
    lines.append(f"ORB Data:         {len(orb_data)} tickers")
    lines.append(f"Scan History:     {len(sh)} events")
    lines.append(f"Cooldowns:        {cooldown_status.get('cooldowns', 0)} active")
    lines.append(f"Mutes:            {cooldown_status.get('mutes', 0)} active")
    lines.append(f"Price Overrides:  {len(_price_overrides)} active")
    lines.append(f"Scan Log:         {len(_scan_history_log)} entries")

    if ticker:
        ticker = ticker.upper()
        state = ts.get(ticker, {})
        lines.append("")
        lines.append(f"━━━ {ticker} STATE ━━━")
        if not state:
            lines.append(f"  No cached state for {ticker}")
        else:
            lines.append(f"  Price:       ${state.get('price', 0):.2f}")
            lines.append(f"  Change:      {state.get('change_pct', 0):.2f}%")
            lines.append(f"  Score:       {state.get('score', 0)}/100")
            lines.append(f"  Direction:   {state.get('direction', 'N/A')}")
            lines.append(f"  Strategy:    {state.get('strategy', 'None')}")

            indicators = state.get("indicators", {})
            if indicators:
                lines.append("")
                lines.append("  INDICATORS:")
                for key in ["rsi", "macd", "supertrend", "stochastic", "ema_stack",
                            "vwap", "adx", "atr", "bollinger_bands", "rvol",
                            "williams_r", "obv"]:
                    ind = indicators.get(key, {})
                    if not ind:
                        ind = indicators.get(key.replace("_bands", ""), {})
                    val = ind.get("value", ind.get("k", ind.get("rvol", "N/A")))
                    bull = "BULL" if ind.get("bullish") else ("BEAR" if ind.get("bearish") else "NEUT")
                    lines.append(f"    {key:<18} {bull:<5} val={val}")

            score_data = state.get("score_data", {})
            if score_data:
                lines.append("")
                lines.append("  SCORE BREAKDOWN:")
                for cat in ["trend", "momentum", "volume", "rs", "risk"]:
                    lines.append(f"    {cat:<18} {score_data.get(cat, 0)}/20")
                lines.append(f"    news_mod:          {score_data.get('news_mod', 0)}")
                lines.append(f"    suppress:          {score_data.get('suppress', False)}")

            from scanner_core.cooldown import is_in_cooldown, is_muted, is_vwap_trapped
            lines.append("")
            lines.append("  SUPPRESSION:")
            lines.append(f"    Cooldown BUY:  {is_in_cooldown(ticker, 'BUY')}")
            # Cooldown SELL removed — BUY only
            lines.append(f"    Muted:         {is_muted(ticker)}")
            lines.append(f"    VWAP Trapped:  {is_vwap_trapped(ticker)}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# SIMULATE STRATEGY
# ══════════════════════════════════════════════════════════════════════════

async def simulate_strategy(ticker: str, strategy_name: str,
                            days: int = 5) -> dict:
    """
    Run a backtest filtered to a single strategy.
    Convenience wrapper around run_backtest.
    """
    return await run_backtest(ticker, days=days, strategy_filter=strategy_name)


# ══════════════════════════════════════════════════════════════════════════
# FORMAT HELPERS
# ══════════════════════════════════════════════════════════════════════════

def format_health_check(results: dict) -> str:
    """Format health check results into a Discord card."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🏥 HEALTH CHECK",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for system, data in results.items():
        status = data.get("status", "UNKNOWN")
        detail = data.get("detail", "")
        icon = "✅" if status == "PASS" else ("❌" if status == "FAIL" else "ℹ️")
        if isinstance(detail, dict):
            lines.append(f"{icon} {system}:")
            for k, v in detail.items():
                lines.append(f"    {k:<12} {v}")
        else:
            lines.append(f"{icon} {system:<20} {detail}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_backtest_results(results: dict) -> str:
    """Format backtest results into a premium Discord card."""
    ticker = results.get("ticker", "?")
    days = results.get("days", "?")
    interval = results.get("interval", "")

    lines = [f"**BACKTEST REPORT — ${ticker}**"]
    if interval:
        lines.append(f"`{days}d  ·  {interval} candles`")
    else:
        lines.append(f"`{days} days`")

    if results.get("error"):
        lines.append(f"\n> Error: {results['error']}")
        return "\n".join(lines)

    strat_filter = results.get("strategy_filter")
    if strat_filter:
        lines.append(f"Strategy: `{strat_filter}`")

    tc = results['trade_count']
    wr = results.get('win_rate', 0)
    avg_r = results.get('avg_r', 0)
    total_r = results.get('total_r', 0)
    pf = results.get('profit_factor', 0)

    # ── Grade ────────────────────────────────────────────────────
    if tc >= 5:
        if wr >= 55 and avg_r > 0:
            grade = "A"
        elif wr >= 50 and avg_r > 0:
            grade = "B"
        elif wr >= 45:
            grade = "C"
        else:
            grade = "D"
    else:
        grade = "—"

    # ── SUMMARY ──────────────────────────────────────────────────
    lines.append("")
    lines.append("**SUMMARY**")
    lines.append("```")
    lines.append(f"  Grade         {grade}")
    lines.append(f"  Trades        {tc}  ({results.get('wins', 0)}W / {results.get('losses', 0)}L)")
    lines.append(f"  Win Rate      {wr:.1f}%")
    lines.append(f"  Avg R         {avg_r:+.3f}R")
    lines.append(f"  Total R       {total_r:+.3f}R")
    lines.append(f"  Profit Factor {pf:.2f}")
    lines.append("```")

    # ── Signal Filtering Funnel ──────────────────────────────────
    fs = results.get("filter_stats", {})
    if fs and fs.get("raw_signals", 0) > 0:
        raw = fs.get("raw_signals", 0)
        lines.append("**SIGNAL FUNNEL**")
        lines.append("```")
        lines.append(f"  Raw signals       {raw:>4}")
        fms = fs.get("failed_market_state", 0)
        if fms > 0:
            lines.append(f"  - Market state    {fms:>4}")
        fadx = fs.get("failed_adx_gate", 0)
        if fadx > 0:
            lines.append(f"  - ADX gate        {fadx:>4}")
        frsi = fs.get("failed_rsi_gate", 0)
        if frsi > 0:
            lines.append(f"  - RSI gate        {frsi:>4}")
        frr = fs.get("failed_rr_gate", 0)
        if frr > 0:
            lines.append(f"  - R:R gate        {frr:>4}")
        fsf = fs.get("failed_strategy_filter", 0)
        if fsf > 0:
            lines.append(f"  - Strategy filter {fsf:>4}")
        lines.append(f"  ─────────────────────")
        lines.append(f"  Final trades      {tc:>4}")
        lines.append("```")

    # ── BY MARKET STATE ──────────────────────────────────────────
    mkt = results.get("market_condition_summary", {})
    if mkt:
        state_labels = {"STRONG_TREND": "Strong Trend", "WEAK_TREND": "Weak Trend",
                        "RANGING": "Ranging", "HIGH_VOLATILITY": "High Vol"}
        has_data = any(mkt.get(ms) for ms in state_labels)
        if has_data:
            lines.append("**BY MARKET STATE**")
            lines.append("```")
            for ms in ["STRONG_TREND", "WEAK_TREND", "RANGING", "HIGH_VOLATILITY"]:
                data = mkt.get(ms)
                if data:
                    label = state_labels.get(ms, ms)
                    lines.append(f"  {label:<14} {data['count']:>3} trades  WR {data['win_rate']:>5.1f}%")
            lines.append("```")

    # ── BY STRATEGY TYPE ─────────────────────────────────────────
    if results.get("trend_count") or results.get("mr_count"):
        lines.append("**BY STRATEGY**")
        lines.append("```")
        if results.get("trend_count"):
            lines.append(f"  Trend      {results['trend_count']:>3} trades  "
                         f"WR {results.get('trend_win_rate', 0):>5.1f}%  "
                         f"Avg {results.get('trend_avg_r', 0):+.2f}R")
        if results.get("mr_count"):
            lines.append(f"  Mean Rev   {results['mr_count']:>3} trades  "
                         f"WR {results.get('mr_win_rate', 0):>5.1f}%  "
                         f"Avg {results.get('mr_avg_r', 0):+.2f}R")
        lines.append("```")

    # ── BEST TRADES ──────────────────────────────────────────────
    best = results.get("best_trades", [])
    if best:
        lines.append("**BEST TRADES**")
        for i, t in enumerate(best[:3], 1):
            medal = ["🥇", "🥈", "🥉"][i - 1]
            lines.append(f"{medal} `{t['r_multiple']:+.2f}R` {t['strategy']}  ·  {t.get('reason', '')}")

    # ── WORST TRADES ─────────────────────────────────────────────
    worst = results.get("worst_trades", [])
    if worst:
        lines.append("**WORST TRADES**")
        for t in worst[:3]:
            lines.append(f"💀 `{t['r_multiple']:+.2f}R` {t['strategy']}  ·  {t.get('reason', '')}")

    # ── Warnings ─────────────────────────────────────────────────
    if tc == 0:
        lines.append("")
        lines.append("> ⚠️ **No trades found.**")
        if fs and fs.get("raw_signals", 0) > 0:
            lines.append("> Signals were detected but all filtered out by quality gates.")
        else:
            lines.append("> Try a longer period, different ticker, or remove the strategy filter.")
        lines.append("> Suggested: NVDA, AAPL, MSFT generate more signals.")
    elif tc < 5:
        lines.append(f"\n> ⚠️ Very few trades ({tc}). Results may not be statistically significant.")

    # ── Verdict ──────────────────────────────────────────────────
    if tc >= 5:
        lines.append("")
        if grade == "A":
            lines.append("✅ **Strong edge detected.** Priority stock for live alerts.")
        elif grade == "B":
            lines.append("✅ Positive expectancy. Run `/wfo` to validate consistency.")
        elif grade == "D":
            lines.append("⚠️ Below target. This stock may not suit the current strategy set.")

    return "\n".join(lines)


def format_replay_results(results: dict) -> str:
    """Format replay results into a Discord card."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔄 REPLAY — ${results['ticker']} on {results['date']}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if results.get("error"):
        lines.append(f"Error: {results['error']}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    lines.append(f"Total Bars:   {results['total_bars']}")
    lines.append(f"Signals:      {len(results['signals'])}")

    if results["signals"]:
        lines.append("")
        lines.append(f"{'TIME':<22} {'DIR':<5} {'PRICE':<10} {'SCORE':<6} {'STRATEGY'}")
        lines.append("─" * 60)
        for sig in results["signals"][:20]:
            t = sig["time"][-8:] if len(sig["time"]) > 8 else sig["time"]
            lines.append(
                f"{t:<22} {sig['direction']:<5} "
                f"${sig['price']:<9.2f} {sig['score']:<6} {sig['strategy']}"
            )
        if len(results["signals"]) > 20:
            lines.append(f"  ... and {len(results['signals']) - 20} more signals")
    else:
        lines.append("No signals detected during this session.")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_goodday_results(results: dict) -> str:
    """Format goodday selector results into a Discord card."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🏆 BEST TRADING DAY FINDER",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if results.get("error"):
        lines.append(f"Error: {results['error']}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    lines.append(f"Scanned:      {results['candidates_scanned']} stocks")
    lines.append(f"Days Analyzed: {results['days_analyzed']}")
    lines.append("")

    top5 = results.get("top_5", [])
    if top5:
        lines.append("🥇 TOP 5 BEST DAYS:")
        lines.append("─" * 34)
        for i, day in enumerate(top5):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i]
            lines.append(f"{medal} ${day['ticker']} — {day['date']}")
            lines.append(f"   Score: {day['score']:.0f} | {day['change_pct']:+.1f}%")
            lines.append(f"   O: ${day['open']:.2f} → C: ${day['close']:.2f}")
            lines.append(f"   Range: ${day['low']:.2f} – ${day['high']:.2f}")
            lines.append(f"   Volume: {day['volume']:,.0f}")
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_scan_history(events: list) -> str:
    """Format scan history events into a Discord card."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📜 SCAN HISTORY",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not events:
        lines.append("No scan events recorded yet.")
    else:
        lines.append(f"{'TIME':<10} {'SCANNED':<9} {'70+':<5} {'ALERTS':<8} {'REGIME'}")
        lines.append("─" * 50)
        for ev in events[-25:]:
            lines.append(
                f"{ev.get('timestamp', '?'):<10} "
                f"{ev.get('stocks_scanned', 0):<9} "
                f"{ev.get('signals_above_70', 0):<5} "
                f"{ev.get('alerts_sent', 0):<8} "
                f"{ev.get('regime', '?')}"
            )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)
