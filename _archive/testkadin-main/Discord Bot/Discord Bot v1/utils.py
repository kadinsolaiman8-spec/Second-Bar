# utils.py — Time utilities, formatters, chart generation, pattern detection

import math
import logging
from datetime import datetime, timedelta, time as dtime
from typing import Optional
import numpy as np
import pandas as pd
import pytz
from colorama import Fore, Back, Style, init as colorama_init

colorama_init(autoreset=True)

from config import (
    MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE,
    PREMARKET_START_HOUR, PREMARKET_START_MINUTE, MARKET_TIMEZONE,
    ATR_STOP_LOSS_MULTIPLIER, ATR_TAKE_PROFIT_1_MULTIPLIER, ATR_TAKE_PROFIT_2_MULTIPLIER,
    CONFIDENCE_CAUTION, CONFIDENCE_STRONG, CONFIDENCE_HIGH_CONVICTION,
    FLAG_CAUTION, FLAG_STRONG, FLAG_HIGH_CONVICTION, FLAG_BELOW,
    FIB_LEVELS, VIX_TICKER, VIX_THRESHOLD_HIGH, VIX_THRESHOLD_EXTREME,
    MAX_MESSAGE_LENGTH,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


# ══════════════════════════════════════════════════════════════════════════
# TIME UTILITIES
# ══════════════════════════════════════════════════════════════════════════

def get_et_now() -> datetime:
    """Current datetime in Eastern Time."""
    return datetime.now(ET)


def is_market_open(override_time: Optional[datetime] = None) -> bool:
    """True if the market is currently open (9:30–16:00 ET, Mon–Fri)."""
    now = override_time or get_et_now()
    if now.weekday() >= 5:
        return False
    open_dt  = now.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MINUTE,  second=0, microsecond=0)
    close_dt = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return open_dt <= now < close_dt


def is_premarket(override_time: Optional[datetime] = None) -> bool:
    """True if in pre-market (4:00–9:30 AM ET, Mon–Fri)."""
    now = override_time or get_et_now()
    if now.weekday() >= 5:
        return False
    pre_start = now.replace(hour=PREMARKET_START_HOUR, minute=PREMARKET_START_MINUTE, second=0, microsecond=0)
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    return pre_start <= now < market_open


def next_market_open() -> datetime:
    """Calculate the next 9:30 AM ET on a weekday."""
    now = get_et_now()
    candidate = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    # Skip weekends
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def market_time_remaining(override_time: Optional[datetime] = None) -> timedelta:
    """Time until market close. Returns zero if market is not open."""
    now = override_time or get_et_now()
    if not is_market_open(now):
        return timedelta(0)
    close_dt = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return close_dt - now


def time_into_session(override_time: Optional[datetime] = None) -> timedelta:
    """Time elapsed since market open. Returns zero if before open."""
    now = override_time or get_et_now()
    open_dt = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    if now < open_dt:
        return timedelta(0)
    return now - open_dt


def get_expiry_time(minutes: int = 60) -> str:
    """ET time N minutes from now as 'HH:MM AM/PM ET'."""
    expiry = get_et_now() + timedelta(minutes=minutes)
    return expiry.strftime("%I:%M %p ET")


def get_mock_trading_time() -> datetime:
    """Return a mock 10:30 AM ET on the most recent weekday."""
    now = get_et_now()
    candidate = now.replace(hour=10, minute=30, second=0, microsecond=0)
    # Find most recent weekday
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


# ══════════════════════════════════════════════════════════════════════════
# FORMATTERS
# ══════════════════════════════════════════════════════════════════════════

def format_price(price: float) -> str:
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return "N/A"
    return f"${price:.2f}"


def format_pct(value: float, include_sign: bool = True) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    sign = "+" if value >= 0 and include_sign else ""
    return f"{sign}{value:.2f}%"


def format_r_multiple(r: float) -> str:
    if r is None or (isinstance(r, float) and math.isnan(r)):
        return "0.00R"
    sign = "+" if r >= 0 else ""
    return f"{sign}{r:.2f}R"


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def format_time_remaining(dt: datetime) -> str:
    """'1h 23m remaining' from a future datetime."""
    now = get_et_now()
    if dt.tzinfo is None:
        dt = ET.localize(dt)
    remaining = dt - now
    if remaining.total_seconds() <= 0:
        return "Expired"
    return format_elapsed(remaining.total_seconds()) + " remaining"


def calculate_stop_pct(entry: float, stop: float) -> str:
    if entry <= 0:
        return "0.00%"
    return f"{abs((stop - entry) / entry * 100):.2f}%"


def calculate_target_pct(entry: float, target: float) -> str:
    if entry <= 0:
        return "0.00%"
    sign = "+" if target >= entry else "-"
    return f"{sign}{abs((target - entry) / entry * 100):.2f}%"


def calculate_rr(entry: float, stop: float, target: float) -> float:
    risk   = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old * 100


def truncate_headline(headline: str, max_len: int = 80) -> str:
    if not headline:
        return "No major news"
    if len(headline) <= max_len:
        return headline
    return headline[:max_len - 3] + "..."


def is_valid_ticker_format(ticker: str) -> bool:
    if not ticker:
        return False
    t = ticker.upper().strip()
    return t.isalpha() and 1 <= len(t) <= 6


# ══════════════════════════════════════════════════════════════════════════
# CARD FORMATTING
# ══════════════════════════════════════════════════════════════════════════

def card_border(width: int = 32) -> str:
    return "━" * width


def align_columns(rows: list, widths: list) -> list:
    """Pad each cell in each row to a specified width for column alignment."""
    result = []
    for row in rows:
        parts = []
        for i, cell in enumerate(row):
            w = widths[i] if i < len(widths) else 20
            parts.append(str(cell).ljust(w))
        result.append("".join(parts))
    return result


def confidence_flag(score: int) -> str:
    if score >= CONFIDENCE_HIGH_CONVICTION:
        return FLAG_HIGH_CONVICTION
    if score >= CONFIDENCE_STRONG:
        return FLAG_STRONG
    if score >= CONFIDENCE_CAUTION:
        return FLAG_CAUTION
    return ""


def direction_emoji(direction: str) -> str:
    """Only BUY (green) or NEUTRAL (white). SELL signals removed."""
    if direction.upper() == "BUY":
        return "🟢"
    return "⚪"


def make_confidence_bar(score: int) -> str:
    """Build emoji confidence bar — works with any score 0-100."""
    score = max(0, min(100, int(score) if isinstance(score, (int, float)) else 0))
    filled = score // 10
    empty = 10 - filled
    if score >= 85:
        block = "🟩"
    elif score >= 70:
        block = "🟨"
    elif score >= 50:
        block = "🟧"
    else:
        block = "🟥"
    bar = block * filled + "⬜" * empty
    return f"{bar} {score}/100"


def tf_emoji(bullish: bool = False, neutral: bool = False, bearish: bool = False) -> str:
    if bullish:
        return "✅ BULLISH"
    if neutral:
        return "⚪ NEUTRAL"
    return "❌ BEARISH"


def build_timeframe_check_block(results: dict) -> str:
    """results: {'1m': 'bullish'/'neutral'/'bearish', '5m': ..., '15m': ..., '1h': ...}"""
    lines = ["Timeframe Check:"]
    for tf in ["1m", "5m", "15m", "1h"]:
        status = results.get(tf, "neutral").lower()
        if status == "bullish":
            emoji_str = "✅ BULLISH"
        elif status == "bearish":
            emoji_str = "❌ BEARISH"
        else:
            emoji_str = "⚪ NEUTRAL"
        lines.append(f"  {tf:<4} {emoji_str}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# ATR-BASED STOPS AND TARGETS
# ══════════════════════════════════════════════════════════════════════════

def atr_based_stop(entry: float, atr: float, direction: str) -> float:
    if direction.upper() == "BUY":
        return round(entry - ATR_STOP_LOSS_MULTIPLIER * atr, 2)
    return round(entry + ATR_STOP_LOSS_MULTIPLIER * atr, 2)


def atr_based_tp1(entry: float, atr: float, direction: str) -> float:
    if direction.upper() == "BUY":
        return round(entry + ATR_TAKE_PROFIT_1_MULTIPLIER * atr, 2)
    return round(entry - ATR_TAKE_PROFIT_1_MULTIPLIER * atr, 2)


def atr_based_tp2(entry: float, atr: float, direction: str) -> float:
    if direction.upper() == "BUY":
        return round(entry + ATR_TAKE_PROFIT_2_MULTIPLIER * atr, 2)
    return round(entry - ATR_TAKE_PROFIT_2_MULTIPLIER * atr, 2)


def calculate_position_size(account_size: float, risk_pct: float, entry: float, stop: float) -> dict:
    """Returns position sizing details."""
    dollar_risk   = account_size * (risk_pct / 100.0)
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return {"error": "Stop and entry are the same price."}
    shares        = int(dollar_risk / stop_distance)
    position_val  = round(shares * entry, 2)
    actual_risk   = round(shares * stop_distance, 2)
    return {
        "shares":        shares,
        "position_value": position_val,
        "dollar_risk":   actual_risk,
        "stop_distance": round(stop_distance, 2),
        "risk_pct":      round((actual_risk / account_size) * 100, 2),
    }


# ══════════════════════════════════════════════════════════════════════════
# FIBONACCI
# ══════════════════════════════════════════════════════════════════════════

def calculate_fibonacci_levels(swing_high: float, swing_low: float) -> dict:
    """Calculate all Fibonacci retracement levels."""
    diff = swing_high - swing_low
    levels = {}
    for fib in FIB_LEVELS:
        levels[fib] = round(swing_high - fib * diff, 4)
    return levels


def find_nearest_fib_level(current_price: float, fib_levels: dict) -> tuple:
    """Returns (level_pct, level_price, distance_pct) for nearest Fib level."""
    nearest_pct   = None
    nearest_price = None
    min_dist      = float("inf")
    for pct, price in fib_levels.items():
        dist = abs(current_price - price)
        if dist < min_dist:
            min_dist      = dist
            nearest_pct   = pct
            nearest_price = price
    distance_pct = (abs(current_price - nearest_price) / current_price * 100) if nearest_price else 0
    return nearest_pct, nearest_price, round(distance_pct, 3)


# ══════════════════════════════════════════════════════════════════════════
# SWING HIGH / LOW DETECTION
# ══════════════════════════════════════════════════════════════════════════

def find_swing_highs(highs: pd.Series, period: int = 5) -> list:
    """Return list of indices where a swing high occurs."""
    indices = []
    for i in range(period, len(highs) - period):
        window = highs.iloc[i - period: i + period + 1]
        if highs.iloc[i] == window.max():
            indices.append(i)
    return indices


def find_swing_lows(lows: pd.Series, period: int = 5) -> list:
    """Return list of indices where a swing low occurs."""
    indices = []
    for i in range(period, len(lows) - period):
        window = lows.iloc[i - period: i + period + 1]
        if lows.iloc[i] == window.min():
            indices.append(i)
    return indices


def find_recent_swing_high(df: pd.DataFrame, lookback: int = 50) -> float:
    if df is None or len(df) < 10:
        return float("nan")
    subset = df.tail(lookback)
    idxs = find_swing_highs(subset["High"], period=3)
    if idxs:
        return float(subset["High"].iloc[idxs[-1]])
    return float(subset["High"].max())


def find_recent_swing_low(df: pd.DataFrame, lookback: int = 50) -> float:
    if df is None or len(df) < 10:
        return float("nan")
    subset = df.tail(lookback)
    idxs = find_swing_lows(subset["Low"], period=3)
    if idxs:
        return float(subset["Low"].iloc[idxs[-1]])
    return float(subset["Low"].min())


# ══════════════════════════════════════════════════════════════════════════
# EMA SLOPE
# ══════════════════════════════════════════════════════════════════════════

def ema_slope(series: pd.Series, period: int = 5) -> float:
    """Measure the slope of the last N points of a series."""
    if series is None or len(series) < period:
        return 0.0
    recent = series.dropna().iloc[-period:].tolist()
    if len(recent) < 2:
        return 0.0
    return (recent[-1] - recent[0]) / len(recent)


# ══════════════════════════════════════════════════════════════════════════
# CANDLESTICK PATTERN DETECTION
# ══════════════════════════════════════════════════════════════════════════

def detect_hammer(open_: float, high: float, low: float, close: float) -> bool:
    """Bullish hammer: small body near top, long lower wick >= 2x body."""
    body   = abs(close - open_)
    candle = high - low
    if candle <= 0 or body <= 0:
        return False
    lower_wick = min(open_, close) - low
    upper_wick = high - max(open_, close)
    return (lower_wick >= 2 * body) and (upper_wick <= 0.3 * body) and (close >= open_)


def detect_bullish_engulfing(prev_open: float, prev_close: float,
                              curr_open: float, curr_close: float) -> bool:
    """Current bullish candle body completely engulfs previous bearish candle body."""
    prev_bearish = prev_close < prev_open
    curr_bullish = curr_close > curr_open
    engulfs = curr_open <= prev_close and curr_close >= prev_open
    return prev_bearish and curr_bullish and engulfs


def detect_pin_bar(open_: float, high: float, low: float, close: float,
                   direction: str = "bullish") -> bool:
    """Pin bar: tiny body, long tail in direction of rejection."""
    body   = abs(close - open_)
    candle = high - low
    if candle <= 0:
        return False
    if direction == "bullish":
        lower_wick = min(open_, close) - low
        upper_wick = high - max(open_, close)
        return (lower_wick >= 0.6 * candle) and (body <= 0.3 * candle)
    else:
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        return (upper_wick >= 0.6 * candle) and (body <= 0.3 * candle)


def detect_strong_bullish_close(open_: float, high: float, low: float, close: float) -> bool:
    """Closes in top 30% of high-low range, bullish."""
    candle = high - low
    if candle <= 0:
        return False
    return close > open_ and (close - low) >= 0.7 * candle


def detect_shooting_star(open_: float, high: float, low: float, close: float) -> bool:
    """Bearish shooting star: small body near bottom, long upper wick >= 2x body."""
    body = abs(close - open_)
    candle = high - low
    if candle <= 0 or body <= 0:
        return False
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    return (upper_wick >= 2 * body) and (lower_wick <= 0.3 * body) and (close <= open_)


def detect_bearish_engulfing(prev_open: float, prev_close: float,
                              curr_open: float, curr_close: float) -> bool:
    """Current bearish candle body completely engulfs previous bullish candle body."""
    prev_bullish = prev_close > prev_open
    curr_bearish = curr_close < curr_open
    engulfs = curr_open >= prev_close and curr_close <= prev_open
    return prev_bullish and curr_bearish and engulfs


def detect_strong_bearish_close(open_: float, high: float, low: float, close: float) -> bool:
    """Closes in bottom 30% of high-low range, bearish."""
    candle = high - low
    if candle <= 0:
        return False
    return close < open_ and (high - close) >= 0.7 * candle


def has_bullish_candle_pattern(df: pd.DataFrame) -> bool:
    """
    Check if the trigger candle (last candle in df) shows at least ONE
    bullish pattern: Hammer, Bullish Engulfing, Strong Bullish Close, or Bullish Pin Bar.
    """
    if df is None or len(df) < 2:
        return False
    o = float(df["Open"].iloc[-1])
    h = float(df["High"].iloc[-1])
    l = float(df["Low"].iloc[-1])
    c = float(df["Close"].iloc[-1])
    po = float(df["Open"].iloc[-2])
    pc = float(df["Close"].iloc[-2])

    if detect_hammer(o, h, l, c):
        return True
    if detect_bullish_engulfing(po, pc, o, c):
        return True
    if detect_strong_bullish_close(o, h, l, c):
        return True
    if detect_pin_bar(o, h, l, c, direction="bullish"):
        return True
    return False


def has_bearish_candle_pattern(df: pd.DataFrame) -> bool:
    """
    Check if the trigger candle (last candle in df) shows at least ONE
    bearish pattern: Shooting Star, Bearish Engulfing, Strong Bearish Close, or Bearish Pin Bar.
    """
    if df is None or len(df) < 2:
        return False
    o = float(df["Open"].iloc[-1])
    h = float(df["High"].iloc[-1])
    l = float(df["Low"].iloc[-1])
    c = float(df["Close"].iloc[-1])
    po = float(df["Open"].iloc[-2])
    pc = float(df["Close"].iloc[-2])

    if detect_shooting_star(o, h, l, c):
        return True
    if detect_bearish_engulfing(po, pc, o, c):
        return True
    if detect_strong_bearish_close(o, h, l, c):
        return True
    if detect_pin_bar(o, h, l, c, direction="bearish"):
        return True
    return False


def check_trigger_candle_volume(df: pd.DataFrame, multiplier: float = 1.3,
                                 lookback: int = 20) -> bool:
    """
    Check that the trigger candle (last candle) has volume above
    multiplier * 20-period average volume.
    """
    if df is None or len(df) < lookback + 1:
        return False
    try:
        avg_vol = float(df["Volume"].iloc[-(lookback + 1):-1].mean())
        trigger_vol = float(df["Volume"].iloc[-1])
        if avg_vol <= 0:
            return True  # can't determine, pass
        return trigger_vol >= multiplier * avg_vol
    except Exception:
        return True  # fail open


def calculate_regime_quality_score(spy_indicators: dict, vix_value: float = 0.0) -> int:
    """
    Calculate regime quality score (0-100).
    - SPY ADX > 25: +25
    - SPY RSI between 45-65: +20
    - SPY above VWAP: +20
    - VIX < 20: +20
    - SPY volume above average: +15
    """
    score = 0

    # SPY ADX
    adx_data = spy_indicators.get("adx", {})
    adx_val = adx_data.get("value", adx_data.get("adx", 0))
    if isinstance(adx_val, (int, float)) and adx_val > 25:
        score += 25

    # SPY RSI between 45-65
    rsi_data = spy_indicators.get("rsi", {})
    rsi_val = rsi_data.get("value", rsi_data.get("rsi", 50))
    if isinstance(rsi_val, (int, float)) and 45 <= rsi_val <= 65:
        score += 20

    # SPY above VWAP
    vwap_data = spy_indicators.get("vwap", {})
    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
    price_data = spy_indicators.get("close", spy_indicators.get("price", 0))
    if isinstance(price_data, dict):
        price_data = price_data.get("value", 0)
    if isinstance(vwap_val, (int, float)) and isinstance(price_data, (int, float)):
        if vwap_val > 0 and price_data > vwap_val:
            score += 20

    # VIX below 20
    if isinstance(vix_value, (int, float)) and 0 < vix_value < 20:
        score += 20

    # SPY volume above average
    rvol_data = spy_indicators.get("rvol", spy_indicators.get("volume", {}))
    rvol_val = rvol_data.get("value", rvol_data.get("rvol", 1.0))
    if isinstance(rvol_val, (int, float)) and rvol_val > 1.0:
        score += 15

    return score


def detect_morning_star(candles: list) -> bool:
    """
    Morning star: 3-candle reversal pattern.
    candles: list of (open, high, low, close) tuples, length >= 3.
    """
    if len(candles) < 3:
        return False
    o1, h1, l1, c1 = candles[-3]
    o2, h2, l2, c2 = candles[-2]
    o3, h3, l3, c3 = candles[-1]
    # First: large bearish candle
    first_bearish  = c1 < o1 and (o1 - c1) > 0.6 * (h1 - l1)
    # Second: small body (indecision), gaps down from first
    second_small   = abs(c2 - o2) < 0.3 * (h2 - l2 + 0.001)
    # Third: large bullish candle closes into first candle's body
    third_bullish  = c3 > o3 and c3 >= (o1 + c1) / 2
    return first_bearish and second_small and third_bullish


# ══════════════════════════════════════════════════════════════════════════
# ASCII CHART GENERATOR
# ══════════════════════════════════════════════════════════════════════════

def ascii_chart(prices: list, vwap: float = None, ema21: list = None,
                width: int = 40, height: int = 10) -> str:
    """
    Simple ASCII candlestick-style chart.
    Uses █ for bullish candles, ░ for bearish candles, — for VWAP, ~ for EMA.
    """
    if not prices or len(prices) < 2:
        return "Not enough data for chart."

    # Use last `width` prices
    prices = prices[-width:]
    min_p  = min(prices)
    max_p  = max(prices)
    rng    = max_p - min_p
    if rng <= 0:
        rng = 1

    # Build grid
    grid = [[" "] * len(prices) for _ in range(height)]

    def price_to_row(p):
        row = int((max_p - p) / rng * (height - 1))
        return max(0, min(height - 1, row))

    # Plot candle bodies
    for i in range(len(prices)):
        row = price_to_row(prices[i])
        if i == 0:
            char = "█"
        elif prices[i] >= prices[i - 1]:
            char = "█"
        else:
            char = "░"
        grid[row][i] = char

    # Plot VWAP line
    if vwap is not None and min_p <= vwap <= max_p:
        vrow = price_to_row(vwap)
        for i in range(len(prices)):
            if grid[vrow][i] == " ":
                grid[vrow][i] = "—"

    # Plot EMA21
    if ema21 and len(ema21) >= len(prices):
        ema_slice = ema21[-len(prices):]
        for i, ep in enumerate(ema_slice):
            if ep is not None and min_p <= ep <= max_p:
                erow = price_to_row(ep)
                if grid[erow][i] == " ":
                    grid[erow][i] = "~"

    # Build string with price labels
    lines = []
    for r, row in enumerate(grid):
        price_at_row = max_p - (r / (height - 1)) * rng
        label = f"{price_at_row:>7.2f} |"
        lines.append(label + "".join(row))

    # X-axis
    x_axis = "        +" + "─" * len(prices)
    lines.append(x_axis)
    lines.append(f"         Last {len(prices)} candles  (█=up  ░=dn  —=VWAP  ~=EMA21)")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# COLORAMA CONSOLE LOGGING
# ══════════════════════════════════════════════════════════════════════════

def color_log(text: str, color: str) -> str:
    """Wrap text in colorama color code."""
    color_map = {
        "green":   Fore.GREEN,
        "yellow":  Fore.YELLOW,
        "red":     Fore.RED,
        "cyan":    Fore.CYAN,
        "magenta": Fore.MAGENTA,
        "white":   Fore.WHITE,
        "blue":    Fore.BLUE,
    }
    code = color_map.get(color.lower(), Fore.WHITE)
    return f"{code}{text}{Style.RESET_ALL}"


def log_scan_line(timestamp: str, scanned: int, above_70: int, alerts: int,
                  regime: str, spy_price: float):
    """Print formatted scan cycle log line with colors."""
    alerts_color = "green" if alerts > 0 else "white"
    regime_color = "red" if "HIGH" in regime else ("magenta" if "RANGING" in regime else "cyan")
    parts = [
        color_log(f"[{timestamp}]", "cyan"),
        color_log(f"Scanned: {scanned}", "white"),
        color_log(f"| Signals 70+: {above_70}", "yellow"),
        color_log(f"| Full Alerts: {alerts}", alerts_color),
        color_log(f"| Regime: {regime}", regime_color),
        color_log(f"| SPY: ${spy_price:.2f}", "white"),
    ]
    print("  ".join(parts))


# ══════════════════════════════════════════════════════════════════════════
# VOLUME PROFILE
# ══════════════════════════════════════════════════════════════════════════

def volume_profile(df: pd.DataFrame, bins: int = 10) -> dict:
    """Build a simple volume-by-price profile. Returns {price_midpoint: volume}."""
    if df is None or df.empty:
        return {}
    prices  = df["Close"].values
    volumes = df["Volume"].values
    min_p   = prices.min()
    max_p   = prices.max()
    if max_p == min_p:
        return {min_p: int(volumes.sum())}

    bin_edges = np.linspace(min_p, max_p, bins + 1)
    profile   = {}
    for i in range(bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mid    = round((lo + hi) / 2, 2)
        mask   = (prices >= lo) & (prices < hi)
        profile[mid] = int(volumes[mask].sum())
    return profile


# ══════════════════════════════════════════════════════════════════════════
# MISC
# ══════════════════════════════════════════════════════════════════════════

def safe_float(val, default: float = 0.0) -> float:
    """Convert to float, return default on failure."""
    try:
        f = float(val)
        return f if not math.isnan(f) else default
    except (TypeError, ValueError):
        return default


def safe_last(series: pd.Series, default=float("nan")):
    """Safely get the last non-NaN value from a pandas Series."""
    if series is None or len(series) == 0:
        return default
    clean = series.dropna()
    if clean.empty:
        return default
    return float(clean.iloc[-1])


# ══════════════════════════════════════════════════════════════════════════
# CLOSED-MARKET DETECTION
# ══════════════════════════════════════════════════════════════════════════

def is_after_hours(override_time: Optional[datetime] = None) -> bool:
    """True if the market is NOT currently open (evenings, weekends, holidays)."""
    return not is_market_open(override_time)


def get_after_hours_label(override_time: Optional[datetime] = None) -> str:
    """Return closed-market banner string, or empty if market is open."""
    if is_market_open(override_time):
        return ""
    return "MARKET CLOSED - no trade alerts until regular market hours\n"


def get_last_trading_date() -> str:
    """Return YYYY-MM-DD of the most recent weekday (trading day)."""
    now = get_et_now()
    candidate = now
    if candidate.hour < MARKET_OPEN_HOUR or (
        candidate.hour == MARKET_OPEN_HOUR and candidate.minute < MARKET_OPEN_MINUTE
    ):
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.strftime("%Y-%m-%d")


def get_session_label(override_time: Optional[datetime] = None) -> str:
    """Return human-readable session label."""
    now = override_time or get_et_now()
    if is_market_open(now):
        return "MARKET OPEN"
    if is_premarket(now):
        return "PRE-MARKET"
    return "MARKET CLOSED"


# ══════════════════════════════════════════════════════════════════════════
# CONFIDENCE BAR & FLAG EMOJI
# ══════════════════════════════════════════════════════════════════════════

def build_confidence_bar(score: int, max_score: int = 100) -> str:
    """Build visual confidence bar: '███████░░░ 73/100'."""
    score = max(0, min(score, max_score))
    filled = round(score / max_score * 10)
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    return f"{bar} {score}/{max_score}"


def get_flag_emoji(score: int) -> str:
    """Return flag emoji based on confidence score."""
    if score >= CONFIDENCE_HIGH_CONVICTION:
        return FLAG_HIGH_CONVICTION
    if score >= CONFIDENCE_STRONG:
        return FLAG_STRONG
    if score >= CONFIDENCE_CAUTION:
        return FLAG_CAUTION
    return FLAG_BELOW


def get_timeframe_emoji(status: str) -> str:
    """Return emoji for timeframe status: bullish/bearish/neutral."""
    s = status.lower() if status else "neutral"
    if s == "bullish":
        return "✅"
    if s == "bearish":
        return "❌"
    return "⚪"


# ══════════════════════════════════════════════════════════════════════════
# VOLUME FORMATTER
# ══════════════════════════════════════════════════════════════════════════

def format_volume(vol) -> str:
    """Format volume as human-readable string (1.2M, 456K, etc.)."""
    if vol is None:
        return "N/A"
    vol = float(vol)
    if vol >= 1_000_000_000:
        return f"{vol / 1_000_000_000:.1f}B"
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.1f}M"
    if vol >= 1_000:
        return f"{vol / 1_000:.0f}K"
    return str(int(vol))


# ══════════════════════════════════════════════════════════════════════════
# VIX FETCHER
# ══════════════════════════════════════════════════════════════════════════

def fetch_vix() -> dict:
    """Fetch current VIX value using yfinance. Blocking — run in executor."""
    result = {"value": 0.0, "label": "N/A", "high": False, "extreme": False}
    try:
        import yfinance as yf
        vix = yf.Ticker(VIX_TICKER)
        hist = vix.history(period="2d", interval="1d")
        if hist is not None and len(hist) > 0:
            val = float(hist["Close"].iloc[-1])
            result["value"] = round(val, 1)
            result["extreme"] = val >= VIX_THRESHOLD_EXTREME
            result["high"] = val >= VIX_THRESHOLD_HIGH
            if val >= VIX_THRESHOLD_EXTREME:
                result["label"] = "Extreme"
            elif val >= VIX_THRESHOLD_HIGH:
                result["label"] = "High"
            elif val >= 18:
                result["label"] = "Elevated"
            else:
                result["label"] = "Normal"
    except Exception as e:
        logger.debug(f"VIX fetch error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# EXCHANGE DETECTION
# ══════════════════════════════════════════════════════════════════════════

_NASDAQ_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "NFLX", "ADBE",
    "INTC", "AMD", "QCOM", "TXN", "SHOP", "SNAP", "PLTR", "RBLX", "PINS",
    "COIN", "MARA", "RIOT", "SOFI", "HOOD", "RIVN", "LCID", "DKNG", "PENN",
    "IONQ", "QUBT", "RGTI", "BBAI", "SOUN", "KULR", "SMCI", "ACMR",
    "PYPL", "SQ", "UPST", "AFRM", "COST", "ABNB", "UBER", "LYFT", "DASH",
    "MSTR", "CLSK", "BTBT", "HUT", "CIFR", "WULF", "BITF", "CORZ",
    "NVAX", "MRNA", "BNTX", "SRPT", "EDIT", "BEAM", "CRSP", "BYND",
    "ASTS", "RKLB", "LUNR", "SNDL", "TLRY", "ORCL", "CRM",
}

_NYSE_TICKERS = {
    "JPM", "BAC", "V", "MA", "GS", "MS", "BLK", "SPGI", "AXP",
    "UNH", "JNJ", "ABBV", "MRK", "LLY", "TMO", "ABT",
    "XOM", "CVX", "NEE", "DUK", "SO",
    "WMT", "HD", "PG", "KO", "PEP", "TGT",
    "HON", "MMM", "CAT", "DE", "GE", "BA", "RTX", "LMT",
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLE", "XLV",
    "ARKK", "SOXL", "TQQQ", "SQQQ", "UVXY", "VXX",
}


def get_exchange(ticker: str) -> str:
    """Return exchange name for a ticker."""
    t = ticker.upper().strip()
    if t in _NASDAQ_TICKERS:
        return "NASDAQ"
    if t in _NYSE_TICKERS:
        return "NYSE"
    return "NASDAQ"  # default assumption


# ══════════════════════════════════════════════════════════════════════════
# MESSAGE SPLITTING
# ══════════════════════════════════════════════════════════════════════════

def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list:
    """Split long text into Discord-safe chunks."""
    if not text or len(text) <= max_len:
        return [text] if text else [""]
    parts = []
    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        parts.append(text)
    return parts
