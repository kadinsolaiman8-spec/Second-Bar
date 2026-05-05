# scanner.py — Main scanning loop, data fetching, anti-late-entry filters, query functions

import asyncio
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz
import yfinance as yf
from colorama import Fore, Style

from config import (
    BATCH_SIZE, BATCH_SLEEP_SECONDS, MARKET_TIMEZONE,
    MIN_INDICATORS_FULL_ALERT, MIN_INDICATORS_WATCHLIST, TOTAL_INDICATORS,
    CONFIDENCE_CAUTION, CONFIDENCE_WATCHLIST, CONFIDENCE_STRONG,
    REGIME_RANGING_MIN_INDICATORS, REGIME_RANGING_MIN_CONFIDENCE,
    REGIME_HIGH_VOL_MIN_CONFIDENCE, REGIME_HIGH_VOLATILITY, REGIME_RANGING,
    SPY_TICKER, TIMEFRAMES,
    MAX_SIGNAL_AGE_CANDLES, MAX_PRICE_EXTENSION_ATR, RSI_EXHAUSTION_CANDLES,
    MAX_CONSECUTIVE_BULLISH_1M, VWAP_TRAP_COOLDOWN_MINUTES,
    MIN_DAILY_VOLUME, BB_OVEREXTENSION_CANDLES, MAX_IMPLIED_SPREAD_PCT,
    SCAN_HISTORY_MAX, RS_MIN_OUTPERFORMANCE,
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    SECTOR_MAP, ATR_STOP_LOSS_MULTIPLIER,
    ATR_TAKE_PROFIT_1_MULTIPLIER, ATR_TAKE_PROFIT_2_MULTIPLIER,
    ATR_TAKE_PROFIT_3_MULTIPLIER,
    ORB_END_HOUR, ORB_END_MINUTE,
    CIRCUIT_BREAKER_CONSECUTIVE_LOSSES, CIRCUIT_BREAKER_PAUSE_MINUTES,
    REGIME_QUALITY_MIN_SCORE, HOURLY_BULLISH_MIN_CONDITIONS,
    TRIGGER_VOLUME_MULTIPLIER, TRIGGER_VOLUME_LOOKBACK, MIN_ADX_15M,
    MIN_STOCK_PRICE, MIN_AVG_DAILY_VOLUME, SIGNAL_QUALITY_MIN,
    TREND_CONSISTENCY_CANDLES, RSI_MOMENTUM_MIN_DELTA,
    MIDDAY_QUIET_START_HOUR, MIDDAY_QUIET_START_MINUTE,
    MIDDAY_QUIET_END_HOUR, MIDDAY_QUIET_END_MINUTE,
    SECTOR_ETFS, SECTOR_VWAP_PENALTY, SECTOR_VWAP_SUPPRESS_PCT,
    TIME_DECAY_BEFORE_11, TIME_DECAY_MIDDAY, TIME_DECAY_2PM_TO_3PM,
    TIME_DECAY_POWER_HOUR,
    ALERT_DEDUP_MINUTES, MIN_SCORE_INCREASE_TO_ALERT,
)
from indicators import (
    run_all_indicators, count_bullish_signals, count_bearish_signals,
)
from strategies import detect_strategy
from scoring import calculate_full_score, classify_threshold
from regime import get_current_regime, is_strategy_enabled
from market_state import (
    get_current_market_state, check_alert_rate_limit, record_alert_sent,
    get_min_confidence_for_state, get_state_label,
    get_active_strategies_for_state, is_strategy_allowed_for_state,
)
from mean_reversion import detect_mean_reversion, is_mean_reversion_strategy
from institutional_flow import analyze_institutional_flow
from dynamic_stops import calculate_dynamic_stop
from cooldown import (
    is_in_cooldown, is_muted, is_vwap_trapped, set_vwap_trap,
    check_reversal_reset, get_due_reminders,
)
from watchlist import is_stable, is_volatile, ALL_STOCKS, STABLE_STOCKS
from news import get_news_score_modifier
from utils import (
    log_scan_line, safe_last,
    has_bullish_candle_pattern, has_bearish_candle_pattern,
    check_trigger_candle_volume, calculate_regime_quality_score,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)

# Permanent direction guard — BUY signals only
DIRECTION_OVERRIDE = "BUY_ONLY"

# Suppress noisy yfinance / peewee / urllib3 logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

_executor = ThreadPoolExecutor(max_workers=12)

# ══════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════════════════════

ticker_state: dict = {}       # {ticker: full state dict}
spy_cache: dict = {}          # {timeframe: DataFrame}
orb_data: dict = {}           # {ticker: {high, low, computed}}
scan_history: list = []       # ring buffer of scan events
_quiet_mode: bool = False
_min_confidence: int = 0
_orb_computed: bool = False

# Dynamic delisted / broken ticker detection
_fetch_fail_count: dict = {}  # {ticker: consecutive_failure_count}
_delisted_cache: set = set()  # tickers to skip after repeated failures
DELISTED_FAIL_THRESHOLD = 3   # skip ticker after this many consecutive failures

# Circuit breaker state
_consecutive_losses: int = 0
_circuit_breaker_until: datetime | None = None
_last_regime_quality: int = 100  # cached regime quality score

# Alert deduplication: {ticker: datetime_of_last_alert}
_alert_sent_at: dict = {}
# Midday quiet hours state
_midday_quiet_notified: bool = False
# Sector ETF VWAP cache: {etf: float_vwap_pct}
_sector_vwap_cache: dict = {}
_sector_vwap_last_update: datetime | None = None


def is_circuit_breaker_active() -> bool:
    """Check if circuit breaker is currently pausing alerts."""
    global _circuit_breaker_until
    if _circuit_breaker_until is None:
        return False
    if datetime.now(ET) >= _circuit_breaker_until:
        _circuit_breaker_until = None
        return False
    return True


def get_circuit_breaker_resume_time() -> str:
    """Return the time when circuit breaker expires."""
    if _circuit_breaker_until:
        return _circuit_breaker_until.strftime("%I:%M %p ET")
    return ""


# ══════════════════════════════════════════════════════════════════════════
# ACCURACY HELPERS
# ══════════════════════════════════════════════════════════════════════════

def is_midday_quiet() -> bool:
    """Return True during 11:30 AM - 3:00 PM ET (midday quiet hours)."""
    now = datetime.now(ET)
    start = now.replace(hour=MIDDAY_QUIET_START_HOUR, minute=MIDDAY_QUIET_START_MINUTE, second=0)
    end = now.replace(hour=MIDDAY_QUIET_END_HOUR, minute=MIDDAY_QUIET_END_MINUTE, second=0)
    return start <= now < end


def _get_time_decay_multiplier() -> float:
    """Time decay multiplier for signal confidence."""
    now = datetime.now(ET)
    hour = now.hour
    minute = now.minute
    t = hour + minute / 60.0
    if t < 11.0:
        return TIME_DECAY_BEFORE_11
    if t < 14.0:
        return TIME_DECAY_MIDDAY
    if t < 15.0:
        return TIME_DECAY_2PM_TO_3PM
    return TIME_DECAY_POWER_HOUR


def _check_alert_dedup(ticker: str) -> bool:
    """Return True if ticker can be alerted (not recently sent)."""
    last = _alert_sent_at.get(ticker)
    if last is None:
        return True
    elapsed = (datetime.now(ET) - last).total_seconds() / 60.0
    return elapsed >= ALERT_DEDUP_MINUTES


def _record_alert_dedup(ticker: str):
    """Record that an alert was just sent for this ticker."""
    _alert_sent_at[ticker] = datetime.now(ET)


def _check_score_increase(ticker: str, current_score: int) -> bool:
    """Return True if score rose by MIN_SCORE_INCREASE_TO_ALERT since last scan."""
    prev = ticker_state.get(ticker, {}).get("prev_score", 0)
    if prev <= 0:
        return True  # First time seeing this ticker
    return current_score >= prev + MIN_SCORE_INCREASE_TO_ALERT


def _get_sector_vwap_penalty(ticker: str) -> tuple:
    """Check if sector ETF is above VWAP. Returns (penalty: int, suppress: bool)."""
    sector = SECTOR_MAP.get(ticker, "")
    etf = SECTOR_ETFS.get(sector, "")
    if not etf:
        return 0, False

    # Use cached sector VWAP data (updated every scan cycle)
    vwap_pct = _sector_vwap_cache.get(etf, None)
    if vwap_pct is None:
        return 0, False

    if vwap_pct < SECTOR_VWAP_SUPPRESS_PCT:
        return SECTOR_VWAP_PENALTY, True  # Suppress alert
    if vwap_pct < 0:
        return SECTOR_VWAP_PENALTY, False  # Penalty but don't suppress
    return 0, False


def _get_prev_day_context(ticker: str, df_5m) -> dict:
    """Get previous day close/high/low for context."""
    result = {"prev_close": 0, "gap_pct": 0, "score_adj": 0, "label": ""}
    try:
        if df_5m is None or len(df_5m) < 78:  # Need at least 1 day of 5m bars
            return result
        # Find previous day's data (bars from yesterday)
        dates = df_5m.index.date if hasattr(df_5m.index, 'date') else []
        if len(set(dates)) < 2:
            return result
        unique_dates = sorted(set(dates))
        prev_date = unique_dates[-2]
        prev_bars = df_5m[df_5m.index.date == prev_date]
        if prev_bars.empty:
            return result

        prev_close = float(prev_bars["Close"].iloc[-1])
        prev_high = float(prev_bars["High"].max())
        current_price = float(df_5m["Close"].iloc[-1])

        if prev_close <= 0:
            return result

        gap_pct = (current_price - prev_close) / prev_close * 100
        result["prev_close"] = prev_close
        result["gap_pct"] = gap_pct

        # Score adjustments based on previous day context
        if gap_pct > 1.0 and current_price > prev_close:
            result["score_adj"] = 5
            result["label"] = f"Prev Close: `${prev_close:.2f}` | Gap: `{gap_pct:+.1f}%` \u2705 Holding"
        elif gap_pct > 0.5 and current_price < prev_close + (prev_high - prev_close) * 0.5:
            result["score_adj"] = -5
            result["label"] = f"Prev Close: `${prev_close:.2f}` | Gap: `{gap_pct:+.1f}%` \u26a0\ufe0f Fading"
        elif current_price > prev_high:
            result["score_adj"] = 8
            result["label"] = f"Prev Close: `${prev_close:.2f}` | Breaking prev high \u2705"
        else:
            result["label"] = f"Prev Close: `${prev_close:.2f}` | Gap: `{gap_pct:+.1f}%`"

    except Exception:
        pass
    return result


async def _update_sector_vwap_cache():
    """Refresh sector ETF VWAP positions. Called once per scan cycle."""
    global _sector_vwap_cache, _sector_vwap_last_update
    now = datetime.now(ET)
    if _sector_vwap_last_update and (now - _sector_vwap_last_update).total_seconds() < 300:
        return  # Only update every 5 minutes

    unique_etfs = set(SECTOR_ETFS.values())
    for etf in unique_etfs:
        try:
            df = spy_cache.get("5m") if etf == "SPY" else None
            if df is None or df.empty:
                df = await fetch_ohlcv_async(etf, "5m")
            if df is not None and not df.empty and len(df) >= 10:
                price = float(df["Close"].iloc[-1])
                # Simple VWAP approximation: cumulative (price * volume) / cumulative volume
                pv = (df["Close"] * df["Volume"]).cumsum()
                cv = df["Volume"].cumsum()
                vwap = float(pv.iloc[-1] / cv.iloc[-1]) if float(cv.iloc[-1]) > 0 else price
                if vwap > 0:
                    _sector_vwap_cache[etf] = (price - vwap) / vwap * 100
        except Exception:
            pass
    _sector_vwap_last_update = now


def record_trade_result(is_win: bool):
    """Record win/loss for circuit breaker tracking. Returns True if breaker just tripped."""
    global _consecutive_losses, _circuit_breaker_until
    if is_win:
        _consecutive_losses = 0
        return False
    _consecutive_losses += 1
    if _consecutive_losses >= CIRCUIT_BREAKER_CONSECUTIVE_LOSSES:
        _circuit_breaker_until = datetime.now(ET) + timedelta(minutes=CIRCUIT_BREAKER_PAUSE_MINUTES)
        _consecutive_losses = 0
        logger.warning(
            f"CIRCUIT BREAKER TRIPPED — {CIRCUIT_BREAKER_CONSECUTIVE_LOSSES} consecutive losses. "
            f"Pausing alerts until {get_circuit_breaker_resume_time()}"
        )
        return True
    return False


def get_regime_quality() -> int:
    """Return the last cached regime quality score."""
    return _last_regime_quality


def _check_hourly_bullish(ind_1h: dict) -> bool:
    """
    For BUY alerts: 1H chart must be ACTIVELY BULLISH.
    At least 2 of 3: EMA9 > EMA21, Price > VWAP, RSI > 50.
    """
    if not ind_1h:
        return False
    conditions_met = 0

    ema_data = ind_1h.get("ema", ind_1h.get("ema_stack", {}))
    ema9 = ema_data.get("ema9", ema_data.get("ema_9", 0))
    ema21 = ema_data.get("ema21", ema_data.get("ema_21", 0))
    if isinstance(ema9, (int, float)) and isinstance(ema21, (int, float)) and ema9 > ema21:
        conditions_met += 1

    vwap_data = ind_1h.get("vwap", {})
    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
    price_val = ind_1h.get("close", ind_1h.get("price", 0))
    if isinstance(price_val, dict):
        price_val = price_val.get("value", 0)
    if isinstance(vwap_val, (int, float)) and isinstance(price_val, (int, float)) and price_val > vwap_val > 0:
        conditions_met += 1

    rsi_data = ind_1h.get("rsi", {})
    rsi_val = rsi_data.get("value", rsi_data.get("rsi", 50))
    if isinstance(rsi_val, (int, float)) and rsi_val > 50:
        conditions_met += 1

    return conditions_met >= HOURLY_BULLISH_MIN_CONDITIONS


def _check_hourly_bearish(ind_1h: dict) -> bool:
    """
    For SELL alerts: 1H chart must be ACTIVELY BEARISH.
    At least 2 of 3: EMA9 < EMA21, Price < VWAP, RSI < 50.
    """
    if not ind_1h:
        return False
    conditions_met = 0

    ema_data = ind_1h.get("ema", ind_1h.get("ema_stack", {}))
    ema9 = ema_data.get("ema9", ema_data.get("ema_9", 0))
    ema21 = ema_data.get("ema21", ema_data.get("ema_21", 0))
    if isinstance(ema9, (int, float)) and isinstance(ema21, (int, float)) and ema9 < ema21:
        conditions_met += 1

    vwap_data = ind_1h.get("vwap", {})
    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
    price_val = ind_1h.get("close", ind_1h.get("price", 0))
    if isinstance(price_val, dict):
        price_val = price_val.get("value", 0)
    if isinstance(vwap_val, (int, float)) and isinstance(price_val, (int, float)) and vwap_val > 0 and price_val < vwap_val:
        conditions_met += 1

    rsi_data = ind_1h.get("rsi", {})
    rsi_val = rsi_data.get("value", rsi_data.get("rsi", 50))
    if isinstance(rsi_val, (int, float)) and rsi_val < 50:
        conditions_met += 1

    return conditions_met >= HOURLY_BULLISH_MIN_CONDITIONS


def _check_stock_adx_15m(ind_15m: dict) -> bool:
    """Stock must have ADX > 20 on 15m chart."""
    if not ind_15m:
        return False
    adx_data = ind_15m.get("adx", {})
    adx_val = adx_data.get("value", adx_data.get("adx", 0))
    return isinstance(adx_val, (int, float)) and adx_val >= MIN_ADX_15M


def _check_trend_consistency(df: pd.DataFrame, direction: str) -> bool:
    """FIX 2A: 3 candles before trigger must show consistent trend structure."""
    n = TREND_CONSISTENCY_CANDLES
    if df is None or len(df) < n + 1:
        return True
    lows = df["Low"].values
    highs = df["High"].values
    if direction == "BUY":
        for j in range(-n, -1):
            if lows[j] >= lows[j + 1]:
                return False
    else:
        for j in range(-n, -1):
            if highs[j] <= highs[j + 1]:
                return False
    return True


def _check_rsi_momentum(indicators: dict, direction: str) -> bool:
    """FIX 2D: RSI must be actively moving in trade direction."""
    rsi_data = indicators.get("rsi", {})
    rsi_val = rsi_data.get("value", rsi_data.get("rsi", None))
    rsi_prev = rsi_data.get("prev_value", rsi_data.get("prev", None))
    if rsi_val is None or rsi_prev is None:
        return True
    if not isinstance(rsi_val, (int, float)) or not isinstance(rsi_prev, (int, float)):
        return True
    delta = rsi_val - rsi_prev
    if direction == "BUY":
        return delta >= RSI_MOMENTUM_MIN_DELTA
    return delta <= -RSI_MOMENTUM_MIN_DELTA


def _check_macd_expanding(indicators: dict, direction: str) -> bool:
    """FIX 2E: MACD histogram must be expanding in signal direction."""
    macd_data = indicators.get("macd", {})
    hist = macd_data.get("histogram", macd_data.get("hist", None))
    prev_hist = macd_data.get("prev_histogram", macd_data.get("prev_hist", None))
    if hist is None or prev_hist is None:
        return True
    if not isinstance(hist, (int, float)) or not isinstance(prev_hist, (int, float)):
        return True
    if direction == "BUY":
        return hist > 0 and hist > prev_hist
    return hist < 0 and hist < prev_hist


def _calculate_signal_quality(indicators: dict, df: pd.DataFrame, direction: str) -> int:
    """FIX 2G: Signal quality score 0-10."""
    score = 0
    # +2: RSI moving in direction
    rsi_data = indicators.get("rsi", {})
    rsi_val = rsi_data.get("value", rsi_data.get("rsi", 50))
    rsi_prev = rsi_data.get("prev_value", rsi_data.get("prev", rsi_val))
    if isinstance(rsi_val, (int, float)) and isinstance(rsi_prev, (int, float)):
        delta = rsi_val - rsi_prev
        if direction == "BUY" and delta >= RSI_MOMENTUM_MIN_DELTA:
            score += 2

    # +2: MACD histogram expanding
    macd_data = indicators.get("macd", {})
    hist = macd_data.get("histogram", macd_data.get("hist", 0))
    prev_hist = macd_data.get("prev_histogram", macd_data.get("prev_hist", 0))
    if isinstance(hist, (int, float)) and isinstance(prev_hist, (int, float)):
        if direction == "BUY" and hist > 0 and hist > prev_hist:
            score += 2

    # +2: Volume > 1.5x average
    if df is not None and len(df) >= 21:
        try:
            cur_vol = float(df["Volume"].iloc[-1])
            avg_vol = float(df["Volume"].iloc[-21:-1].mean())
            if avg_vol > 0 and cur_vol > 1.5 * avg_vol:
                score += 2
        except Exception:
            pass

    # +2: Full EMA alignment (9 > 21 > 50)
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

    # +2: Price near VWAP
    vwap_data = indicators.get("vwap", {})
    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
    if df is not None and len(df) > 0 and isinstance(vwap_val, (int, float)) and vwap_val > 0:
        try:
            price = float(df["Close"].iloc[-1])
            dist_pct = abs(price - vwap_val) / vwap_val * 100
            if dist_pct < 0.5:
                score += 2
            elif dist_pct < 1.0:
                score += 1
        except Exception:
            pass

    return min(score, 10)


def update_regime_quality(spy_indicators: dict, vix_value: float = 0.0):
    """Recalculate and cache regime quality score."""
    global _last_regime_quality
    _last_regime_quality = calculate_regime_quality_score(spy_indicators, vix_value)


# ══════════════════════════════════════════════════════════════════════════
# MARKET HOURS
# ══════════════════════════════════════════════════════════════════════════

def is_market_open(override_dt: Optional[datetime] = None) -> bool:
    """Check if US stock market is currently open."""
    now = override_dt or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return market_open <= now < market_close


def is_premarket_active() -> bool:
    """Check if we're in premarket window (4 AM - 9:30 AM ET)."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    pre_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    mkt_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    return pre_start <= now < mkt_open


def _is_orb_window() -> bool:
    """Check if within first 15 min of market (ORB window)."""
    now = datetime.now(ET)
    orb_end = now.replace(hour=ORB_END_HOUR, minute=ORB_END_MINUTE, second=0, microsecond=0)
    mkt_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    return mkt_open <= now < orb_end


# ══════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ══════════════════════════════════════════════════════════════════════════

def _fetch_ohlcv(ticker: str, timeframe: str) -> Optional[pd.DataFrame]:
    """
    Synchronous yfinance download — runs in a thread so the event loop
    never blocks.  Tracks consecutive failures to auto-skip delisted tickers.
    """
    if ticker in _delisted_cache:
        return None
    try:
        period_map = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "30d"}
        period = period_map.get(timeframe, "5d")
        df = yf.download(ticker, period=period, interval=timeframe,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            _fetch_fail_count[ticker] = _fetch_fail_count.get(ticker, 0) + 1
            if _fetch_fail_count[ticker] >= DELISTED_FAIL_THRESHOLD:
                _delisted_cache.add(ticker)
                print(f"  [DELISTED] {ticker} skipped after {DELISTED_FAIL_THRESHOLD} consecutive failures")
            return None
        # Success — reset failure counter
        _fetch_fail_count.pop(ticker, None)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        _fetch_fail_count[ticker] = _fetch_fail_count.get(ticker, 0) + 1
        if _fetch_fail_count[ticker] >= DELISTED_FAIL_THRESHOLD:
            _delisted_cache.add(ticker)
            print(f"  [DELISTED] {ticker} skipped after {DELISTED_FAIL_THRESHOLD} consecutive failures")
        logger.debug(f"Fetch error {ticker} {timeframe}: {e}")
        return None


async def fetch_ohlcv_async(ticker: str, timeframe: str) -> Optional[pd.DataFrame]:
    """Async wrapper around _fetch_ohlcv using thread executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _fetch_ohlcv, ticker, timeframe)


def fetch_ticker_data(ticker: str, period: str = "5d",
                      interval: str = "5m") -> Optional[pd.DataFrame]:
    """
    General-purpose synchronous fetch for any ticker/period/interval.
    Flattens MultiIndex columns.  Respects delisted cache.
    """
    if ticker in _delisted_cache:
        return None
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.debug(f"fetch_ticker_data error {ticker}: {e}")
        return None


async def fetch_all_timeframes(ticker: str) -> dict:
    """Fetch 1m, 5m, 15m, 1h DataFrames in parallel."""
    tasks = {tf: fetch_ohlcv_async(ticker, tf) for tf in TIMEFRAMES}
    results = {}
    for tf, coro in tasks.items():
        results[tf] = await coro
    return results


async def refresh_spy_cache():
    """Refresh SPY data across all timeframes."""
    global spy_cache
    for tf in TIMEFRAMES:
        df = await fetch_ohlcv_async(SPY_TICKER, tf)
        if df is not None and not df.empty:
            spy_cache[tf] = df
    logger.debug(f"SPY cache refreshed: {list(spy_cache.keys())}")


# ══════════════════════════════════════════════════════════════════════════
# ORB (OPENING RANGE BREAKOUT)
# ══════════════════════════════════════════════════════════════════════════

def _compute_orb_levels(df_5m: pd.DataFrame) -> dict:
    """
    Compute ORB high/low from the first 15 minutes of trading.
    Uses 5m bars between 9:30 and 9:45 AM ET.
    """
    if df_5m is None or df_5m.empty:
        return {"high": None, "low": None, "computed": False}

    try:
        idx = df_5m.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC").tz_convert(ET)
        elif str(idx.tz) != MARKET_TIMEZONE:
            idx = idx.tz_convert(ET)

        today = datetime.now(ET).date()
        orb_start = ET.localize(datetime.combine(today, datetime.min.time()).replace(
            hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE))
        orb_end = ET.localize(datetime.combine(today, datetime.min.time()).replace(
            hour=ORB_END_HOUR, minute=ORB_END_MINUTE))

        mask = (idx >= orb_start) & (idx < orb_end)
        orb_bars = df_5m.loc[mask]

        if orb_bars.empty or len(orb_bars) < 2:
            return {"high": None, "low": None, "computed": False}

        return {
            "high": float(orb_bars["High"].max()),
            "low": float(orb_bars["Low"].min()),
            "computed": True,
        }
    except Exception as e:
        logger.debug(f"ORB compute error: {e}")
        return {"high": None, "low": None, "computed": False}


async def compute_orb_for_all(tickers: list):
    """Compute ORB levels for a list of tickers."""
    global orb_data, _orb_computed
    if _orb_computed:
        return
    for ticker in tickers:
        df_5m = await fetch_ohlcv_async(ticker, "5m")
        if df_5m is not None:
            orb_data[ticker] = _compute_orb_levels(df_5m)
    _orb_computed = True
    logger.info(f"ORB computed for {len(orb_data)} tickers")


def get_orb_data(ticker: str) -> dict:
    """Return cached ORB levels for a ticker."""
    return orb_data.get(ticker.upper(), {"high": None, "low": None, "computed": False})


def reset_orb_data():
    """Reset ORB data — called at start of each trading day."""
    global orb_data, _orb_computed
    orb_data.clear()
    _orb_computed = False


# ══════════════════════════════════════════════════════════════════════════
# ANTI-LATE-ENTRY FILTERS (10 filters)
# ══════════════════════════════════════════════════════════════════════════

def _check_signal_age(df_5m: pd.DataFrame, direction: str) -> tuple:
    """Filter 1: Signal must be recent (within MAX_SIGNAL_AGE_CANDLES bars)."""
    if df_5m is None or len(df_5m) < 3:
        return True, "insufficient data"
    recent = df_5m.tail(MAX_SIGNAL_AGE_CANDLES + 1)
    if direction == "BUY":
        pattern = all(recent["Close"].iloc[i] > recent["Close"].iloc[i - 1]
                      for i in range(1, len(recent)))
    else:
        pattern = all(recent["Close"].iloc[i] < recent["Close"].iloc[i - 1]
                      for i in range(1, len(recent)))
    if pattern:
        return False, f"Signal age > {MAX_SIGNAL_AGE_CANDLES} candles — stale signal"
    return True, "signal fresh"


def _check_price_extension(df_5m: pd.DataFrame, entry: float,
                           atr: float) -> tuple:
    """Filter 2: Price should not be extended beyond ATR threshold from VWAP."""
    if atr <= 0:
        return True, "no ATR"
    if df_5m is None or len(df_5m) < 10:
        return True, "insufficient data"
    try:
        typical = (df_5m["High"] + df_5m["Low"] + df_5m["Close"]) / 3
        cum_vol = df_5m["Volume"].cumsum()
        cum_tp_vol = (typical * df_5m["Volume"]).cumsum()
        vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        last_vwap = float(vwap.dropna().iloc[-1])
        extension = abs(entry - last_vwap) / atr
        if extension > MAX_PRICE_EXTENSION_ATR:
            return False, f"Price extended {extension:.1f}x ATR from VWAP"
        return True, f"Extension {extension:.1f}x ATR — OK"
    except Exception:
        return True, "VWAP calc error"


def _check_rsi_exhaustion(indicators: dict) -> tuple:
    """Filter 3: RSI should not be in exhaustion territory."""
    rsi = indicators.get("rsi", {})
    rsi_val = rsi.get("value", 50)
    if rsi_val > 85:
        return False, f"RSI exhaustion (RSI={rsi_val:.0f}) — extreme overbought"
    if rsi_val < 15:
        return False, f"RSI exhaustion (RSI={rsi_val:.0f}) — extreme oversold"
    return True, f"RSI {rsi_val:.0f} — within range"


def _check_trend_age(df_1m: pd.DataFrame, direction: str) -> tuple:
    """Filter 4: Check for too many consecutive same-direction 1m candles."""
    if df_1m is None or len(df_1m) < MAX_CONSECUTIVE_BULLISH_1M:
        return True, "insufficient 1m data"
    recent = df_1m.tail(MAX_CONSECUTIVE_BULLISH_1M)
    if direction == "BUY":
        consecutive = sum(1 for i in range(len(recent))
                          if recent["Close"].iloc[i] > recent["Open"].iloc[i])
    else:
        consecutive = sum(1 for i in range(len(recent))
                          if recent["Close"].iloc[i] < recent["Open"].iloc[i])
    if consecutive >= MAX_CONSECUTIVE_BULLISH_1M:
        return False, f"{consecutive} consecutive {direction} 1m candles — trend exhausted"
    return True, f"{consecutive} consecutive — OK"


def _check_candle_rejection(df_1m: pd.DataFrame, direction: str) -> tuple:
    """Filter 5: Last candle should not be a strong rejection."""
    if df_1m is None or len(df_1m) < 1:
        return True, "no 1m data"
    last = df_1m.iloc[-1]
    body = abs(last["Close"] - last["Open"])
    wick_hi = last["High"] - max(last["Close"], last["Open"])
    wick_lo = min(last["Close"], last["Open"]) - last["Low"]

    if body < 0.001:
        return True, "doji — neutral"

    if direction == "BUY" and wick_hi > body * 2.5:
        return False, "Upper wick rejection on BUY signal"
    # SELL wick rejection removed — BUY only
    return True, "no rejection"


def _check_vwap_trap(ticker: str, df_5m: pd.DataFrame,
                     entry: float, direction: str) -> tuple:
    """Filter 6: Detect VWAP trap — price crosses VWAP then reverses."""
    if df_5m is None or len(df_5m) < 5:
        return True, "insufficient data"
    if is_vwap_trapped(ticker):
        return False, "VWAP trap — suppressed"
    try:
        typical = (df_5m["High"] + df_5m["Low"] + df_5m["Close"]) / 3
        cum_vol = df_5m["Volume"].cumsum()
        cum_tp_vol = (typical * df_5m["Volume"]).cumsum()
        vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        last5_close = df_5m["Close"].tail(5)
        last5_vwap = vwap.tail(5)

        crosses = 0
        for i in range(1, len(last5_close)):
            prev_above = float(last5_close.iloc[i - 1]) > float(last5_vwap.iloc[i - 1])
            curr_above = float(last5_close.iloc[i]) > float(last5_vwap.iloc[i])
            if prev_above != curr_above:
                crosses += 1
        if crosses >= 3:
            set_vwap_trap(ticker)
            return False, f"VWAP trap detected ({crosses} crosses in 5 bars)"
        return True, "no VWAP trap"
    except Exception:
        return True, "VWAP trap check error"


def _check_liquidity(df_1m: pd.DataFrame) -> tuple:
    """Filter 7: Check minimum volume threshold."""
    if df_1m is None or len(df_1m) < 5:
        return True, "insufficient 1m data"
    try:
        daily_vol = float(df_1m["Volume"].sum())
        if daily_vol < MIN_DAILY_VOLUME:
            return False, f"Low daily volume ({daily_vol:,.0f} < {MIN_DAILY_VOLUME:,.0f})"
        return True, f"Volume OK ({daily_vol:,.0f})"
    except Exception:
        return True, "volume check error"


def _check_bb_overextension(indicators: dict, direction: str) -> tuple:
    """Filter 8: Price should not be outside Bollinger Bands for too long."""
    bb = indicators.get("bollinger_bands", indicators.get("bollinger", {}))
    upper = bb.get("upper", 0)
    lower = bb.get("lower", 0)
    price = bb.get("price", bb.get("close", 0))

    if upper == 0 or lower == 0:
        return True, "no BB data"

    if direction == "BUY" and price > upper:
        return False, "Price above upper BB — overextended"
    # SELL BB check removed — BUY only
    return True, "within BB range"


def _check_williams_divergence(indicators: dict, direction: str) -> tuple:
    """Filter 9: Williams %R divergence check."""
    wr = indicators.get("williams_r", {})
    wr_val = wr.get("value", -50)

    if direction == "BUY" and wr_val > -10:
        return False, f"Williams %R extreme ({wr_val:.0f}) — BUY divergence risk"
    # SELL Williams check removed — BUY only
    return True, f"Williams %R {wr_val:.0f} — OK"


def _check_spread_sanity(df_5m: pd.DataFrame) -> tuple:
    """Filter 10: Check implied spread isn't too wide."""
    if df_5m is None or len(df_5m) < 3:
        return True, "insufficient data"
    try:
        last = df_5m.iloc[-1]
        high = float(last["High"])
        low = float(last["Low"])
        close = float(last["Close"])
        if close <= 0:
            return True, "zero price"
        implied_spread = (high - low) / close * 100
        if implied_spread > MAX_IMPLIED_SPREAD_PCT:
            return False, f"Implied spread {implied_spread:.1f}% > {MAX_IMPLIED_SPREAD_PCT}%"
        return True, f"Spread {implied_spread:.1f}% — OK"
    except Exception:
        return True, "spread check error"


def passes_all_anti_late_entry_filters(ticker: str, df_1m, df_5m,
                                       indicators: dict, entry_price: float,
                                       direction: str, atr: float) -> tuple:
    """
    Run all 10 anti-late-entry filters.  Returns (passed: bool, details: list).
    """
    filters = [
        _check_signal_age(df_5m, direction),
        _check_price_extension(df_5m, entry_price, atr),
        _check_rsi_exhaustion(indicators),
        _check_trend_age(df_1m, direction),
        _check_candle_rejection(df_1m, direction),
        _check_vwap_trap(ticker, df_5m, entry_price, direction),
        _check_liquidity(df_1m),
        _check_bb_overextension(indicators, direction),
        _check_williams_divergence(indicators, direction),
        _check_spread_sanity(df_5m),
    ]
    details = []
    all_passed = True
    for passed, reason in filters:
        details.append({"passed": passed, "reason": reason})
        if not passed:
            all_passed = False
    return all_passed, details


# ══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def _run_indicators_safe(df) -> dict:
    """Run indicators with exception safety."""
    try:
        return run_all_indicators(df)
    except Exception as e:
        logger.debug(f"Indicator error: {e}")
        return {}


def _check_multi_tf_bullish(ind_5m: dict, ind_15m: dict, ind_1h: dict) -> bool:
    """Check if majority of timeframes are bullish."""
    bull_count = 0
    for ind in [ind_5m, ind_15m, ind_1h]:
        if ind:
            bull = count_bullish_signals(ind)
            if bull >= TOTAL_INDICATORS // 2:
                bull_count += 1
    return bull_count >= 2


def _check_multi_tf_bearish(ind_5m: dict, ind_15m: dict, ind_1h: dict) -> bool:
    """Check if majority of timeframes are bearish."""
    bear_count = 0
    for ind in [ind_5m, ind_15m, ind_1h]:
        if ind:
            bear = count_bearish_signals(ind)
            if bear >= TOTAL_INDICATORS // 2:
                bear_count += 1
    return bear_count >= 2


def _passes_rs_filter(ticker: str, df_5m, df_15m, direction: str) -> bool:
    """Check relative strength vs SPY."""
    try:
        spy_5m = spy_cache.get("5m")
        if spy_5m is None or df_5m is None:
            return True

        min_len = min(len(df_5m), len(spy_5m))
        if min_len < 10:
            return True

        ticker_ret = (float(df_5m["Close"].iloc[-1]) / float(df_5m["Close"].iloc[-min_len]) - 1)
        spy_ret = (float(spy_5m["Close"].iloc[-1]) / float(spy_5m["Close"].iloc[-min_len]) - 1)
        rs = ticker_ret - spy_ret

        if direction == "BUY" and rs < -RS_MIN_OUTPERFORMANCE:
            return False
        # SELL RS check removed — BUY only
        return True
    except Exception:
        return True


def _passes_regime_filter(ticker: str, direction: str, score: int,
                          bullish_count: int, strategy_name: str) -> bool:
    """
    Apply regime-specific filtering rules.
    Ranging regime: needs higher confidence + more indicators.
    High Volatility: needs even higher confidence.
    """
    regime = get_current_regime()
    label = regime.get("label", "TRENDING")

    if not is_strategy_enabled(strategy_name, label):
        logger.debug(f"Strategy {strategy_name} not enabled in {label} regime")
        return False

    if label == REGIME_RANGING:
        if score < REGIME_RANGING_MIN_CONFIDENCE:
            return False
        if bullish_count < REGIME_RANGING_MIN_INDICATORS:
            return False
    elif label == REGIME_HIGH_VOLATILITY:
        if score < REGIME_HIGH_VOL_MIN_CONFIDENCE:
            return False

    return True


def _store_state(ticker: str, price: float, indicators: dict,
                 score_data: dict, direction: str, strategy_name: str,
                 bullish_count: int, bearish_count: int,
                 change_pct: float = 0.0):
    """Store scan result in ticker_state cache.
    If score_data has total=0 but we have indicators, compute a quick score
    so /watchlist and /best always show real values."""
    # Capture old score before overwriting for trend arrow
    old_score = ticker_state.get(ticker, {}).get("score", 0)
    total = score_data.get("total", 0) if score_data else 0

    # If score is 0 and we have indicator data, compute a quick estimate
    if total <= 0 and indicators and isinstance(indicators, dict):
        try:
            from scoring import score_from_available_data
            rsi = 0.0
            rvol = 0.0
            rsi_data = indicators.get("rsi", {})
            if isinstance(rsi_data, dict):
                rsi = float(rsi_data.get("value", 0) or 0)
            rvol_data = indicators.get("rvol", {})
            if isinstance(rvol_data, dict):
                rvol = float(rvol_data.get("rvol", rvol_data.get("value", 0)) or 0)
            if rsi > 0 or rvol > 0:
                quick = score_from_available_data(ticker, rsi=rsi, rvol=rvol)
                score_data = quick
                total = quick.get("total", 0)
        except Exception:
            pass

    ticker_state[ticker] = {
        "ticker": ticker,
        "price": price,
        "change_pct": change_pct,
        "direction": direction,
        "strategy": strategy_name if strategy_name and strategy_name != "None" else "",
        "score": total,
        "score_data": score_data,
        "indicators": indicators,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "sector": SECTOR_MAP.get(ticker, "unknown"),
        "stable": is_stable(ticker),
        "volatile": is_volatile(ticker),
        "last_scan": datetime.now(ET).strftime("%H:%M:%S"),
        "prev_score": old_score,  # For confidence trend arrows
    }


# ══════════════════════════════════════════════════════════════════════════
# CORE SCAN LOGIC
# ══════════════════════════════════════════════════════════════════════════

async def scan_ticker(ticker: str) -> Optional[dict]:
    """
    Full scan pipeline for a single ticker:
    1. Fetch all timeframes
    2. Run indicators on 5m
    3. Count bullish/bearish signals
    4. Determine direction
    5. Detect strategy
    6. Calculate score
    7. Apply regime filter
    8. Apply anti-late-entry filters
    9. Check cooldowns/mutes
    10. Store state and return signal if alertable
    """
    try:
        if not is_market_open():
            logger.debug(f"{ticker}: market closed; trade scans are disabled.")
            return None

        # ── Step 1: Fetch data ──────────────────────────────────────────
        data = await fetch_all_timeframes(ticker)
        df_1m = data.get("1m")
        df_5m = data.get("5m")
        df_15m = data.get("15m")
        df_1h = data.get("1h")

        if df_5m is None or df_5m.empty or len(df_5m) < 30:
            return None

        entry_price = float(df_5m["Close"].iloc[-1])
        if entry_price <= 0:
            return None

        # ── FIX 2B: Minimum price filter ──────────────────────────────
        if entry_price < MIN_STOCK_PRICE:
            return None

        # ── FIX 2C: Minimum avg daily volume ─────────────────────────
        if len(df_5m) >= 10:
            avg_bar_vol = float(df_5m["Volume"].iloc[-10:].mean())
            # 5m bars = ~78 per day; avg_bar_vol * 78 estimates daily volume
            est_daily_vol = avg_bar_vol * 78
            if est_daily_vol < MIN_AVG_DAILY_VOLUME:
                return None

        # Calculate change %
        change_pct = 0.0
        if len(df_5m) > 1:
            prev_close = float(df_5m["Close"].iloc[0])
            if prev_close > 0:
                change_pct = round((entry_price - prev_close) / prev_close * 100, 2)

        # ── Step 2: Run indicators ──────────────────────────────────────
        indicators = _run_indicators_safe(df_5m)
        if not indicators:
            return None

        ind_15m = _run_indicators_safe(df_15m) if df_15m is not None else {}
        ind_1h = _run_indicators_safe(df_1h) if df_1h is not None else {}

        # ── Step 3: Count signals ───────────────────────────────────────
        bullish_count = count_bullish_signals(indicators)
        bearish_count = count_bearish_signals(indicators)

        # ── Step 4: Direction (requires 7/12 for watchlist tier) ─────────
        if bullish_count >= MIN_INDICATORS_WATCHLIST:
            direction = "BUY"
        else:
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, "NEUTRAL", "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # Multi-timeframe alignment check (BUY only)
        tf_aligned = _check_multi_tf_bullish(indicators, ind_15m, ind_1h)

        # ── Step 4b: 1H actively bullish/bearish filter (FIX 2) ───────
        if direction == "BUY" and not _check_hourly_bullish(ind_1h):
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None
        # ── Step 4c: Stock ADX > 20 on 15m (FIX 4) ───────────────────
        if not _check_stock_adx_15m(ind_15m):
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # ── Step 4d: Candle pattern required (FIX 5) ──────────────────
        if direction == "BUY" and not has_bullish_candle_pattern(df_5m):
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None
        # ── Step 4e: Trigger candle volume (FIX 6) ────────────────────
        if not check_trigger_candle_volume(df_5m, TRIGGER_VOLUME_MULTIPLIER,
                                           TRIGGER_VOLUME_LOOKBACK):
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # ── Step 4f: Trend consistency (FIX 2A) ─────────────────────
        if not _check_trend_consistency(df_5m, direction):
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # ── Step 4g: RSI momentum direction (FIX 2D) ────────────────
        if not _check_rsi_momentum(indicators, direction):
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # ── Step 4h: MACD histogram expanding (FIX 2E) ──────────────
        if not _check_macd_expanding(indicators, direction):
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # ── Step 4i: Signal quality score (FIX 2G) ──────────────────
        signal_quality = _calculate_signal_quality(indicators, df_5m, direction)
        if signal_quality < SIGNAL_QUALITY_MIN:
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # ── Step 4j: Regime quality score (FIX 9) ─────────────────────
        regime_quality = get_regime_quality()

        # ── Step 4g: Circuit breaker (FIX 8) ──────────────────────────
        if is_circuit_breaker_active():
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        # ── Step 4k: HARD MARKET STATE GATE ─────────────────────────────
        # HIGH_VOLATILITY: zero alerts. RANGING: mean-reversion only.
        # WEAK_TREND: EMA Pullback only. STRONG_TREND: all strategies.
        ms_state = get_current_market_state()

        if ms_state == "HIGH_VOLATILITY":
            print(f"  [GATE] {ticker}: Blocked — HIGH_VOLATILITY state (0% trend WR)")
            _store_state(ticker, entry_price, indicators,
                         {"total": 0}, direction, "None",
                         bullish_count, bearish_count, change_pct)
            return None

        if ms_state == "RANGING":
            # Only allow mean reversion — no trend following
            spy_ind_cur = _run_indicators_safe(spy_cache.get("5m")) if spy_cache.get("5m") is not None else {}
            mr_result = detect_mean_reversion(df_5m, df_15m, spy_ind_cur)
            if mr_result and mr_result.get("triggered"):
                strat_result = mr_result
                strategy_name = strat_result.get("strategy", "Mean Reversion")
            else:
                print(f"  [GATE] {ticker}: Blocked — RANGING state, no MR signal")
                _store_state(ticker, entry_price, indicators,
                             {"total": 0}, direction, "None",
                             bullish_count, bearish_count, change_pct)
                return None
        else:
            # ── Step 5: Detect strategy (STRONG_TREND / WEAK_TREND) ────
            ticker_orb = orb_data.get(ticker, None)
            strat_result = detect_strategy(
                df_5m, indicators, orb_data=ticker_orb, df_15m=df_15m,
            )

            if not strat_result.get("triggered"):
                _store_state(ticker, entry_price, indicators,
                             {"total": 0}, direction, "None",
                             bullish_count, bearish_count, change_pct)
                return None

            strategy_name = strat_result.get("strategy", "Unknown")

            # WEAK_TREND: only allow EMA Pullback
            if ms_state == "WEAK_TREND":
                if not is_strategy_allowed_for_state(strategy_name, ms_state):
                    print(f"  [GATE] {ticker}: Blocked — WEAK_TREND only allows EMA Pullback, got {strategy_name}")
                    _store_state(ticker, entry_price, indicators,
                                 {"total": 0}, direction, strategy_name,
                                 bullish_count, bearish_count, change_pct)
                    return None

        # ── Dynamic stop/target calculation ──────────────────────────────
        atr_data = indicators.get("atr", {})
        atr_val = float(atr_data.get("value", atr_data.get("atr", 0)) or 0)
        ms_state = get_current_market_state()

        if atr_val > 0:
            dyn = calculate_dynamic_stop(ticker, entry_price, atr_val,
                                         ms_state, indicators)
            stop = dyn["stop"]
            tp1 = dyn["tp1"]
            tp2 = dyn["tp2"]
            tp3 = dyn["tp3"]
            stop_type = dyn.get("stop_type", "Dynamic")
        else:
            stop = strat_result.get("stop", 0)
            tp1 = strat_result.get("tp1", 0)
            tp2 = strat_result.get("tp2", 0)
            tp3 = 0
            stop_type = "Strategy"

        risk = abs(entry_price - stop) if stop != 0 else 1
        rr = round(abs(tp1 - entry_price) / risk, 2) if risk > 0 else 0

        # ── Step 6: Institutional flow detection (volume-only, fast) ─────
        flow_result = None
        try:
            flow_result = analyze_institutional_flow(
                ticker, df_5m, check_options=False, check_shorts=False
            )
        except Exception:
            pass

        # ── Step 6b: Calculate score ────────────────────────────────────
        spy_5m = spy_cache.get("5m")
        spy_15m = spy_cache.get("15m")
        score_data = calculate_full_score(
            ticker, indicators, entry_price, direction,
            df_5m=df_5m, df_15m=df_15m,
            spy_5m=spy_5m, spy_15m=spy_15m,
            flow_result=flow_result,
        )
        total_score = score_data.get("total", 0)

        # ── Step 6c: Previous day context ───────────────────────────────
        prev_day = _get_prev_day_context(ticker, df_5m)
        if prev_day.get("score_adj", 0) != 0:
            total_score = max(0, min(100, total_score + prev_day["score_adj"]))
            score_data["total"] = total_score

        # ── Step 6d: Sector VWAP confirmation ───────────────────────────
        sector_penalty, sector_suppress = _get_sector_vwap_penalty(ticker)
        if sector_suppress:
            print(f"  [GATE] {ticker}: Sector ETF too far below VWAP — suppressed")
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None
        if sector_penalty > 0:
            total_score = max(0, total_score - sector_penalty)
            score_data["total"] = total_score

        # ── Step 6e: Time decay multiplier ──────────────────────────────
        decay = _get_time_decay_multiplier()
        if decay < 1.0:
            raw_score = total_score
            total_score = max(0, int(total_score * decay))
            score_data["total"] = total_score
            score_data["time_decay_from"] = raw_score

        # ── Store prev_score for trend arrows ───────────────────────────
        prev_score = ticker_state.get(ticker, {}).get("score", 0)

        # ── Step 7: Regime filter ───────────────────────────────────────
        if not _passes_regime_filter(ticker, direction, total_score,
                                     bullish_count, strategy_name):
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        # ── Step 8: Anti-late-entry filters ─────────────────────────────
        ale_passed, ale_details = passes_all_anti_late_entry_filters(
            ticker, df_1m, df_5m, indicators, entry_price, direction, atr_val
        )

        # ── Step 9: Cooldowns/mutes ─────────────────────────────────────
        if is_muted(ticker):
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        if is_in_cooldown(ticker, direction):
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        check_reversal_reset(ticker, entry_price)

        # ── Step 10: RS filter ──────────────────────────────────────────
        if not _passes_rs_filter(ticker, df_5m, df_15m, direction):
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        # ── Quality Gates (FIX 5) ──────────────────────────────────────
        # GATE 5: RSI not overextended (>72 = overbought, skip)
        rsi_val = indicators.get("rsi", {}).get("value", 50) or 50
        if isinstance(rsi_val, (int, float)) and rsi_val > 72:
            print(f"  [GATE] {ticker}: Failed Gate 5 — RSI {rsi_val:.0f} (overextended)")
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        # GATE 7: Minimum R:R ratio 1.8:1
        if 0 < rr < 1.8:
            print(f"  [GATE] {ticker}: Failed Gate 7 — R:R {rr:.2f} < 1.8 minimum")
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        # ── Store and determine if alertable ────────────────────────────
        _store_state(ticker, entry_price, indicators, score_data,
                     direction, strategy_name, bullish_count,
                     bearish_count, change_pct)

        if total_score < CONFIDENCE_WATCHLIST:
            return None
        # Require 9/12 for full alert (FIX 1)
        if bullish_count < MIN_INDICATORS_FULL_ALERT and direction == "BUY":
            return None
        if not ale_passed:
            logger.debug(f"{ticker} failed anti-late-entry filters")
            return None

        # Regime quality gate: below 60 → watchlist only, no full alert
        if regime_quality < REGIME_QUALITY_MIN_SCORE:
            logger.debug(f"{ticker} regime quality {regime_quality} < {REGIME_QUALITY_MIN_SCORE}")
            return None

        # Market state min-confidence gate
        ms_min_conf = get_min_confidence_for_state()
        if total_score < ms_min_conf:
            logger.debug(f"{ticker} score {total_score} < market state min {ms_min_conf}")
            return None

        # Market state rate limit gate
        if not check_alert_rate_limit():
            logger.debug(f"{ticker} alert rate limit reached for current market state")
            return None

        # ── Midday quiet hours ──────────────────────────────────────────
        if is_midday_quiet():
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        # ── Alert deduplication ────────────────────────────────────────
        if not _check_alert_dedup(ticker):
            logger.debug(f"{ticker} alert dedup — sent too recently")
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        # ── Score must be rising to re-alert ───────────────────────────
        if not _check_score_increase(ticker, total_score):
            logger.debug(f"{ticker} score not rising enough to re-alert")
            _store_state(ticker, entry_price, indicators, score_data,
                         direction, strategy_name, bullish_count,
                         bearish_count, change_pct)
            return None

        # Record the alert being sent
        record_alert_sent()
        _record_alert_dedup(ticker)

        # Build signal result
        regime = get_current_regime()
        ms_label = get_state_label()
        signal = {
            "ticker": ticker,
            "direction": direction,
            "entry_price": entry_price,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "strategy": strategy_name,
            "score_data": score_data,
            "indicators": indicators,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "regime": regime.get("label", "TRENDING"),
            "regime_quality": regime_quality,
            "signal_quality": signal_quality,
            "change_pct": change_pct,
            "tf_aligned": tf_aligned,
            "ale_details": ale_details,
            "conditions_met": strat_result.get("conditions_met", []),
            "conditions_failed": strat_result.get("conditions_failed", []),
            "market_state": ms_state,
            "market_state_label": ms_label,
            "stop_type": stop_type,
            "flow_result": flow_result,
            "prev_score": prev_score,
            "prev_close_info": prev_day.get("label", ""),
        }
        return signal

    except Exception as e:
        logger.error(f"scan_ticker error {ticker}: {e}")
        return None


async def scan_all_tickers(tickers: list, alert_channel=None) -> list:
    """
    Scan the entire watchlist using parallel async tasks.
    Returns list of signal dicts for stocks that passed all filters.
    """
    import os
    signals = []
    now_str = datetime.now(ET).strftime("%H:%M:%S")
    scan_start = time.time()

    if _is_orb_window() and not _orb_computed:
        await compute_orb_for_all(tickers[:20])

    # Update sector VWAP cache for sector confirmation
    await _update_sector_vwap_cache()

    # Update regime quality score and market state before scanning
    spy_5m = spy_cache.get("5m")
    if spy_5m is not None and not spy_5m.empty:
        spy_ind = _run_indicators_safe(spy_5m)
        try:
            from utils import fetch_vix
            vix_data = fetch_vix()
            vix_val = vix_data.get("value", 0)
        except Exception:
            vix_val = 0
        update_regime_quality(spy_ind, vix_val)
        # Update market state (non-blocking, uses cached data)
        try:
            from market_state import update_market_state
            await update_market_state(alert_channel, None)
        except Exception:
            pass

    # Parallel scan: launch all tickers as async tasks in larger batches
    batch_size = max(BATCH_SIZE, 20)
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        tasks = [scan_ticker(t) for t in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"Batch scan exception: {result}")
                continue
            if result is not None:
                signals.append(result)

        if i + batch_size < len(tickers):
            await asyncio.sleep(BATCH_SLEEP_SECONDS)

    signals.sort(key=lambda s: s.get("score_data", {}).get("total", 0), reverse=True)

    elapsed = time.time() - scan_start
    above_70 = sum(1 for s in signals if s.get("score_data", {}).get("total", 0) >= 70)
    regime = get_current_regime()
    cores = os.cpu_count() or 1

    # Enhanced console log with timing
    from colorama import Fore, Style
    print(
        f"  {Fore.CYAN}[{now_str}]{Style.RESET_ALL} "
        f"Parallel scan: {len(tickers)} stocks in {elapsed:.1f}s | "
        f"Signals: {above_70} | Alerts: {len(signals)} | "
        f"Regime Q: {get_regime_quality()} | Cores: {cores}"
    )

    # Get SPY price for scan log
    _spy_5m = spy_cache.get("5m")
    _spy_price = float(_spy_5m["Close"].iloc[-1]) if _spy_5m is not None and not _spy_5m.empty else 0.0

    log_scan_line(
        timestamp=now_str,
        scanned=len(tickers),
        above_70=above_70,
        alerts=len(signals),
        regime=regime.get("label", "?"),
        spy_price=_spy_price,
    )

    add_to_scan_history({
        "stocks_scanned": len(tickers),
        "signals_above_70": above_70,
        "alerts_sent": len(signals),
        "regime": regime.get("label", "UNKNOWN"),
        "regime_quality": get_regime_quality(),
        "scan_time_seconds": round(elapsed, 2),
    })

    # ── WATCHING: stocks close to triggering (score 60-69) ─────────
    for t, state in ticker_state.items():
        sc = state.get("score", 0)
        if 60 <= sc < 70:
            weakness = _identify_weakness(
                state.get("indicators", {}), state.get("score_data", {}))
            print(f"  [WATCHING] {t}: Score {sc}/100 — Missing: {weakness}")

    return signals


# ══════════════════════════════════════════════════════════════════════════
# SCAN HISTORY
# ══════════════════════════════════════════════════════════════════════════

def add_to_scan_history(scan_event: dict):
    """Append scan event to ring buffer."""
    scan_event.setdefault("timestamp", datetime.now(ET).strftime("%H:%M:%S"))
    scan_history.append(scan_event)
    if len(scan_history) > SCAN_HISTORY_MAX:
        scan_history.pop(0)


def get_scan_history(n: int = 50) -> list:
    """Return last n scan events."""
    return list(scan_history[-n:])


# ══════════════════════════════════════════════════════════════════════════
# QUERY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def get_ticker_state(ticker: str) -> dict:
    """Return cached state for a ticker."""
    return ticker_state.get(ticker.upper(), {})


def get_all_watchlist_stocks() -> list:
    """Return all cached stocks sorted by score.
    If a stock has score=0 but has indicator data, re-score it live."""
    items = []
    for ticker, state in ticker_state.items():
        score = state.get("score", 0)
        strategy = state.get("strategy", "")

        # Re-score stocks that have 0 but have cached indicators
        if score <= 0 and state.get("indicators"):
            try:
                from scoring import get_score
                price = state.get("price", 0)
                new_score, new_data = get_score(
                    ticker, indicators=state["indicators"],
                    entry_price=price,
                )
                score = new_score
                # Update cache so next read is fast
                state["score"] = score
                state["score_data"] = new_data
            except Exception:
                pass

        items.append({
            "ticker": ticker,
            "price": state.get("price", 0),
            "score": score,
            "direction": state.get("direction", "NEUTRAL"),
            "strategy": strategy if strategy else "No Active Setup",
            "change_pct": state.get("change_pct", 0),
            "sector": state.get("sector", "unknown"),
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def _identify_weakness(indicators: dict, score_data: dict) -> str:
    """Identify the weakest aspect of a stock's setup."""
    weaknesses = []
    rsi = indicators.get("rsi", {}).get("value", 50)
    if rsi > 70:
        weaknesses.append("RSI overbought")
    elif rsi < 30:
        weaknesses.append("RSI oversold")

    rvol = indicators.get("rvol", {}).get("rvol", 1.0)
    if rvol < 0.8:
        weaknesses.append("Low volume")

    adx = indicators.get("adx", {}).get("value", 0)
    if adx < 20:
        weaknesses.append("Weak trend")

    if score_data.get("trend", 0) < 10:
        weaknesses.append("Poor trend alignment")
    if score_data.get("momentum", 0) < 10:
        weaknesses.append("Weak momentum")

    return "; ".join(weaknesses) if weaknesses else "No major weakness"


def get_best_stocks(min_score: int = 0, limit: int = 5) -> list:
    """Return top-scoring stocks from cache. Always returns up to `limit`
    stocks even if none meet `min_score` — sorted by score descending."""
    all_stocks = get_all_watchlist_stocks()
    candidates = []
    for s in all_stocks:
        score = s.get("score", 0)
        strategy = s.get("strategy", "")
        if not strategy or strategy == "None":
            strategy = "No Active Setup"
        candidates.append({
            "ticker": s["ticker"],
            "price": s.get("price", 0),
            "score": score,
            "direction": s.get("direction", "NEUTRAL"),
            "strategy": strategy,
            "change_pct": s.get("change_pct", 0),
            "weakness": _identify_weakness(
                ticker_state.get(s["ticker"], {}).get("indicators", {}),
                ticker_state.get(s["ticker"], {}).get("score_data", {}),
            ),
        })
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:limit]


def get_market_breadth() -> dict:
    """Calculate market breadth from cached ticker states."""
    total = len(ticker_state)
    if total == 0:
        return {
            "total": 0, "bullish": 0, "bearish": 0, "neutral": 0,
            "bullish_pct": 0, "bearish_pct": 0,
            "avg_score": 0, "above_70": 0, "above_85": 0,
            "advances": 0, "declines": 0, "ad_ratio": 0.0,
        }

    bullish = sum(1 for s in ticker_state.values() if s.get("direction") == "BUY")
    bearish = 0  # SELL signals removed
    neutral = total - bullish - bearish
    scores = [s.get("score", 0) for s in ticker_state.values()]
    advances = sum(1 for s in ticker_state.values() if s.get("change_pct", 0) > 0)
    declines = sum(1 for s in ticker_state.values() if s.get("change_pct", 0) < 0)

    return {
        "total": total,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "bullish_pct": round(bullish / total * 100, 1) if total > 0 else 0,
        "bearish_pct": round(bearish / total * 100, 1) if total > 0 else 0,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "above_70": sum(1 for s in scores if s >= 70),
        "above_85": sum(1 for s in scores if s >= 85),
        "advances": advances,
        "declines": declines,
        "ad_ratio": round(advances / max(declines, 1), 2),
    }


def get_movers(n: int = 10) -> list:
    """Return top movers by absolute change %."""
    items = []
    for ticker, state in ticker_state.items():
        items.append({
            "ticker": ticker,
            "change_pct": state.get("change_pct", 0),
            "price": state.get("price", 0),
            "score": state.get("score", 0),
            "direction": state.get("direction", "NEUTRAL"),
        })
    items.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return items[:n]


def get_volatile_by_atr(n: int = 10) -> list:
    """Return stocks with highest ATR relative to price."""
    items = []
    for ticker, state in ticker_state.items():
        indicators = state.get("indicators", {})
        atr = indicators.get("atr", {}).get("value", indicators.get("atr", {}).get("atr", 0))
        price = state.get("price", 0)
        if price > 0 and atr > 0:
            atr_pct = atr / price * 100
            items.append({
                "ticker": ticker,
                "atr": atr,
                "atr_pct": round(atr_pct, 2),
                "price": price,
                "score": state.get("score", 0),
            })
    items.sort(key=lambda x: x["atr_pct"], reverse=True)
    return items[:n]


def get_leaders(vs_spy_min: float = 0.005, n: int = 5) -> list:
    """Return stocks outperforming SPY."""
    spy_5m = spy_cache.get("5m")
    if spy_5m is None or spy_5m.empty:
        return []

    spy_ret = 0
    if len(spy_5m) > 1:
        spy_ret = float(spy_5m["Close"].iloc[-1]) / float(spy_5m["Close"].iloc[0]) - 1

    items = []
    for ticker, state in ticker_state.items():
        change = state.get("change_pct", 0) / 100
        rs = change - spy_ret
        if rs >= vs_spy_min:
            items.append({
                "ticker": ticker,
                "rs": round(rs * 100, 2),
                "change_pct": state.get("change_pct", 0),
                "score": state.get("score", 0),
            })
    items.sort(key=lambda x: x["rs"], reverse=True)
    return items[:n]


def get_laggards(n: int = 5) -> list:
    """Return stocks underperforming SPY."""
    spy_5m = spy_cache.get("5m")
    if spy_5m is None or spy_5m.empty:
        return []

    spy_ret = 0
    if len(spy_5m) > 1:
        spy_ret = float(spy_5m["Close"].iloc[-1]) / float(spy_5m["Close"].iloc[0]) - 1

    items = []
    for ticker, state in ticker_state.items():
        change = state.get("change_pct", 0) / 100
        rs = change - spy_ret
        items.append({
            "ticker": ticker,
            "rs": round(rs * 100, 2),
            "change_pct": state.get("change_pct", 0),
            "score": state.get("score", 0),
        })
    items.sort(key=lambda x: x["rs"])
    return items[:n]


def get_squeeze_stocks() -> list:
    """Return stocks in Bollinger Band squeeze."""
    items = []
    for ticker, state in ticker_state.items():
        indicators = state.get("indicators", {})
        bb = indicators.get("bollinger_bands", indicators.get("bollinger", {}))
        upper = bb.get("upper", 0)
        lower = bb.get("lower", 0)
        mid = bb.get("mid", bb.get("middle", 0))

        if upper > 0 and lower > 0 and mid > 0:
            width = (upper - lower) / mid * 100
            if width < 4.0:
                items.append({
                    "ticker": ticker,
                    "bb_width": round(width, 2),
                    "price": state.get("price", 0),
                    "score": state.get("score", 0),
                })
    items.sort(key=lambda x: x["bb_width"])
    return items


def get_sector_summary(sector: str) -> dict:
    """Get aggregate stats for a sector."""
    sector = sector.lower()
    tickers_in_sector = [t for t, s in SECTOR_MAP.items() if s == sector]
    if not tickers_in_sector:
        return {"sector": sector, "count": 0, "avg_score": 0, "bullish": 0, "bearish": 0}

    scores = []
    bullish = 0
    bearish = 0
    changes = []
    for t in tickers_in_sector:
        state = ticker_state.get(t, {})
        if state:
            scores.append(state.get("score", 0))
            changes.append(state.get("change_pct", 0))
            if state.get("direction") == "BUY":
                bullish += 1
            # SELL removed — only count BUY

    return {
        "sector": sector,
        "count": len(tickers_in_sector),
        "cached": len(scores),
        "avg_score": round(sum(scores) / max(len(scores), 1), 1),
        "avg_change": round(sum(changes) / max(len(changes), 1), 2),
        "bullish": bullish,
        "bearish": bearish,
    }


def get_daily_move(ticker: str) -> float:
    """Get the daily change % for a ticker from cache."""
    state = ticker_state.get(ticker.upper(), {})
    return state.get("change_pct", 0.0)


def get_52_week_range(ticker: str) -> tuple:
    """Fetch 52-week high/low for a ticker."""
    try:
        info = yf.Ticker(ticker).info
        high = info.get("fiftyTwoWeekHigh", 0)
        low = info.get("fiftyTwoWeekLow", 0)
        return (low, high)
    except Exception:
        return (0, 0)


async def get_price_history(ticker: str, period: str = "1d") -> Optional[pd.DataFrame]:
    """Fetch price history asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: fetch_ticker_data(ticker, period=period, interval="5m"),
    )


def get_halts() -> list:
    """Return tickers that might be halted (no recent data)."""
    halted = []
    for ticker, state in ticker_state.items():
        last_scan = state.get("last_scan", "")
        if not last_scan:
            continue
        try:
            scan_time = datetime.strptime(last_scan, "%H:%M:%S")
            now = datetime.now(ET)
            scan_dt = now.replace(hour=scan_time.hour, minute=scan_time.minute,
                                  second=scan_time.second)
            if (now - scan_dt).total_seconds() > 300:
                halted.append(ticker)
        except Exception:
            pass
    return halted


def compare_tickers(ticker1: str, ticker2: str) -> dict:
    """Compare two tickers side by side."""
    s1 = ticker_state.get(ticker1.upper(), {})
    s2 = ticker_state.get(ticker2.upper(), {})
    return {
        "ticker1": {
            "ticker": ticker1.upper(),
            "price": s1.get("price", 0),
            "score": s1.get("score", 0),
            "direction": s1.get("direction", "N/A"),
            "change_pct": s1.get("change_pct", 0),
            "strategy": s1.get("strategy", "None"),
            "indicators": s1.get("indicators", {}),
            "score_data": s1.get("score_data", {}),
        },
        "ticker2": {
            "ticker": ticker2.upper(),
            "price": s2.get("price", 0),
            "score": s2.get("score", 0),
            "direction": s2.get("direction", "N/A"),
            "change_pct": s2.get("change_pct", 0),
            "strategy": s2.get("strategy", "None"),
            "indicators": s2.get("indicators", {}),
            "score_data": s2.get("score_data", {}),
        },
    }


def get_strategy_hits(strategy_name: str = None) -> list:
    """Return tickers that matched a specific strategy."""
    items = []
    for ticker, state in ticker_state.items():
        strat = state.get("strategy", "None")
        if strat == "None":
            continue
        if strategy_name and strategy_name.lower() not in strat.lower():
            continue
        items.append({
            "ticker": ticker,
            "strategy": strat,
            "score": state.get("score", 0),
            "direction": state.get("direction", "NEUTRAL"),
            "price": state.get("price", 0),
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def get_relative_strength(ticker: str) -> float:
    """Get relative strength vs SPY for a single ticker."""
    spy_5m = spy_cache.get("5m")
    state = ticker_state.get(ticker.upper(), {})
    if spy_5m is None or not state:
        return 0.0
    spy_ret = 0
    if len(spy_5m) > 1:
        spy_ret = float(spy_5m["Close"].iloc[-1]) / float(spy_5m["Close"].iloc[0]) - 1
    ticker_ret = state.get("change_pct", 0) / 100
    return round((ticker_ret - spy_ret) * 100, 2)


def get_sector_rotation() -> dict:
    """Get sector rotation data — which sectors are leading/lagging."""
    sectors = set(SECTOR_MAP.values())
    rotation = {}
    for sector in sectors:
        summary = get_sector_summary(sector)
        rotation[sector] = {
            "avg_score": summary["avg_score"],
            "avg_change": summary.get("avg_change", 0),
            "bullish": summary["bullish"],
            "bearish": summary["bearish"],
        }
    return rotation


def get_orb_breakouts() -> list:
    """Return tickers that broke their ORB levels."""
    breakouts = []
    for ticker, levels in orb_data.items():
        if not levels.get("computed"):
            continue
        state = ticker_state.get(ticker, {})
        price = state.get("price", 0)
        if price <= 0:
            continue
        orb_high = levels.get("high", 0)
        orb_low = levels.get("low", 0)
        if orb_high and price > orb_high:
            breakouts.append({"ticker": ticker, "direction": "BULLISH", "price": price,
                              "orb_high": orb_high, "orb_low": orb_low})
        elif orb_low and price < orb_low:
            breakouts.append({"ticker": ticker, "direction": "BEARISH", "price": price,
                              "orb_high": orb_high, "orb_low": orb_low})
    return breakouts


def get_scan_stats() -> dict:
    """Return summary statistics about the scanner state."""
    total = len(ticker_state)
    scores = [s.get("score", 0) for s in ticker_state.values()]
    directions = [s.get("direction", "NEUTRAL") for s in ticker_state.values()]
    strategies = [s.get("strategy", "None") for s in ticker_state.values()
                  if s.get("strategy", "None") != "None"]

    return {
        "total_cached": total,
        "avg_score": round(sum(scores) / max(len(scores), 1), 1),
        "max_score": max(scores) if scores else 0,
        "bullish": directions.count("BUY"),
        "bearish": 0,  # SELL signals removed
        "neutral": directions.count("NEUTRAL"),
        "strategies_triggered": len(strategies),
        "scan_events": len(scan_history),
        "spy_cached": bool(spy_cache),
        "orb_computed": _orb_computed,
        "quiet_mode": _quiet_mode,
    }


def get_snapshot() -> dict:
    """Return a full snapshot of scanner state for debugging."""
    return {
        "ticker_count": len(ticker_state),
        "spy_timeframes": list(spy_cache.keys()),
        "orb_tickers": len(orb_data),
        "orb_computed": _orb_computed,
        "scan_events": len(scan_history),
        "quiet_mode": _quiet_mode,
        "min_confidence": _min_confidence,
    }


# ══════════════════════════════════════════════════════════════════════════
# CONTROL
# ══════════════════════════════════════════════════════════════════════════

def toggle_quiet_mode() -> bool:
    """Toggle quiet mode (suppress console logging)."""
    global _quiet_mode
    _quiet_mode = not _quiet_mode
    return _quiet_mode


def set_min_confidence(threshold: int) -> int:
    """Set minimum confidence for alerts (runtime override)."""
    global _min_confidence
    _min_confidence = max(0, min(100, threshold))
    return _min_confidence


def reset_scanner():
    """Reset all scanner state."""
    global _orb_computed, _quiet_mode, _min_confidence
    ticker_state.clear()
    spy_cache.clear()
    orb_data.clear()
    scan_history.clear()
    _orb_computed = False
    _quiet_mode = False
    _min_confidence = 0
    logger.info("Scanner state reset")


async def warm_up(tickers: list = None):
    """Pre-populate caches at startup."""
    if tickers is None:
        tickers = ALL_STOCKS

    logger.info(f"Warming up scanner with {len(tickers)} tickers...")
    await refresh_spy_cache()

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        tasks = [scan_ticker(t) for t in batch]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(BATCH_SLEEP_SECONDS)

    logger.info(f"Warm-up complete: {len(ticker_state)} tickers cached")


async def fetch_vix_level() -> float:
    """Fetch current VIX level."""
    try:
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            _executor,
            lambda: fetch_ticker_data("^VIX", period="1d", interval="1m"),
        )
        if df is not None and not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception as e:
        logger.debug(f"VIX fetch error: {e}")
    return 0.0
