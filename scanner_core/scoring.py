# scoring.py — 100-point confidence scoring: 5 categories × 20 points + news modifier

import math
import logging
from typing import Optional
import pandas as pd
import pytz

from scanner_core.config import (
    RSI_HEALTHY_MIN, RSI_HEALTHY_MAX,
    ADX_TRENDING, ADX_STRONG,
    RVOL_HIGH, RVOL_VERY_HIGH,
    RS_MIN_OUTPERFORMANCE,
    MIN_RR_RATIO,
    ATR_STOP_LOSS_MULTIPLIER, ATR_TAKE_PROFIT_1_MULTIPLIER, ATR_TAKE_PROFIT_2_MULTIPLIER,
    CONFIDENCE_CAUTION, CONFIDENCE_STRONG, CONFIDENCE_HIGH_CONVICTION,
    FLAG_CAUTION, FLAG_STRONG, FLAG_HIGH_CONVICTION, FLAG_BELOW,
    WILLIAMS_OB, WILLIAMS_OS,
    MARKET_TIMEZONE,
)
from scanner_core.news import get_news_score_modifier

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


# ── Optional adaptive weight import (fails gracefully if not yet enough trades) ──
def _get_adaptive_adjustment(indicators: dict, entry_price: float) -> int:
    """
    Returns an adaptive bonus/penalty (-5 to +10) based on which indicators
    are active and how accurate those indicators have been historically.
    Returns 0 if adaptive_scoring is unavailable or has <50 trades.
    """
    try:
        from scanner_core.adaptive_scoring import extract_indicator_states, get_weight, get_total_trades
        if get_total_trades() < 50:
            return 0  # Not enough data yet
        if not indicators or entry_price <= 0:
            return 0
        states = extract_indicator_states(indicators, float(entry_price))
        active = [get_weight(ind) for ind, val in states.items() if val]
        if not active:
            return 0
        avg = sum(active) / len(active)
        # avg=1.0 → 0 pts, avg=2.0 → +10, avg=0.5 → -5
        adj = int((avg - 1.0) * 10)
        return max(-5, min(10, adj))
    except ImportError:
        return 0


# ══════════════════════════════════════════════════════════════════════════
# SAFE VALUE HELPERS — NEVER return NaN, always return usable defaults
# ══════════════════════════════════════════════════════════════════════════

def safe_val(value, default=0.0):
    """Return value if it's a valid number, otherwise return default."""
    if value is None:
        return default
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def safe_bool(value, default=False):
    """Return value if it's a bool, otherwise return default."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    # Handle truthy/falsy numeric values
    try:
        return bool(value)
    except (TypeError, ValueError):
        return default


def _gf(d, *keys, default=0.0):
    """Safely traverse nested dict, return float. NEVER returns NaN."""
    v = d
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k, None)
    return safe_val(v, default)


def _gb(d, *keys, default=False):
    """Safely traverse nested dict, return bool."""
    v = d
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k, None)
    return safe_bool(v, default)


# ══════════════════════════════════════════════════════════════════════════
# CATEGORY 1: TREND ALIGNMENT (0–20)
# ══════════════════════════════════════════════════════════════════════════

def score_trend_alignment(indicators: dict) -> tuple:
    """Score trend alignment across indicators. Returns (score, breakdown)."""
    score = 0
    bd = {}

    # SuperTrend bullish: 5m (+4) or both 5m+15m (+8)
    st_5m = _gb(indicators, "supertrend", "bullish", default=False)
    st_15m = _gb(indicators, "supertrend_15m", "bullish", default=st_5m)
    if st_5m and st_15m:
        score += 8
        bd["supertrend_both_tf"] = 8
    elif st_5m:
        score += 4
        bd["supertrend_5m_only"] = 4
    else:
        bd["supertrend_not_bullish"] = 0

    # EMA stack: EMA9 > EMA21 > EMA50 (+7)
    e9 = _gf(indicators, "ema_stack", "ema9", default=0.0)
    e21 = _gf(indicators, "ema_stack", "ema21", default=0.0)
    e50 = _gf(indicators, "ema_stack", "ema50", default=0.0)
    if e9 > 0 and e21 > 0 and e50 > 0 and e9 > e21 > e50:
        score += 7
        bd["ema_stack_aligned"] = 7
    elif e9 > 0 and e21 > 0 and e9 > e21:
        # Partial credit: at least 9 > 21
        score += 3
        bd["ema_partial_aligned"] = 3
    else:
        bd["ema_stack_not_aligned"] = 0

    # ADX >= 25 on trending (+5)
    adx_val = _gf(indicators, "adx", "value", default=0.0)
    if adx_val >= ADX_TRENDING:
        score += 5
        bd["adx_trending"] = 5
    elif adx_val >= 15:
        # Partial credit for moderate trend
        score += 2
        bd["adx_moderate"] = 2
    else:
        bd["adx_not_trending"] = 0

    return min(score, 20), bd


# ══════════════════════════════════════════════════════════════════════════
# CATEGORY 2: MOMENTUM (0–20)
# ══════════════════════════════════════════════════════════════════════════

def score_momentum(indicators: dict) -> tuple:
    """Score momentum indicators. Returns (score, breakdown)."""
    score = 0
    bd = {}

    # RSI in healthy zone (45–65): +7, or partial credit for 35-75
    rsi = _gf(indicators, "rsi", "value", default=0.0)
    if rsi > 0:  # Only score if we have a real RSI value
        if RSI_HEALTHY_MIN <= rsi <= RSI_HEALTHY_MAX:
            score += 7
            bd["rsi_healthy"] = 7
        elif 35 <= rsi <= 75:
            score += 4
            bd["rsi_moderate"] = 4
        else:
            bd["rsi_extreme"] = 0
    else:
        bd["rsi_missing"] = 0

    # MACD bullish + recent crossover: +7 (or just bullish: +4)
    macd_bull = _gb(indicators, "macd", "bullish", default=False)
    candles_since = _gf(indicators, "macd", "candles_since_crossover", default=999)
    candles_since = int(candles_since)
    if macd_bull and candles_since <= 3:
        score += 7
        bd["macd_recent_crossover"] = 7
    elif macd_bull:
        score += 4
        bd["macd_bullish"] = 4
    else:
        # Check if MACD histogram is at least positive
        hist = _gf(indicators, "macd", "histogram", default=0.0)
        if hist > 0:
            score += 2
            bd["macd_hist_positive"] = 2
        else:
            bd["macd_not_bullish"] = 0

    # Stochastic bullish and not overbought: +6
    stoch_bull = _gb(indicators, "stochastic", "bullish", default=False)
    stoch_ob = _gb(indicators, "stochastic", "overbought", default=False)
    if stoch_bull and not stoch_ob:
        score += 6
        bd["stochastic_bullish"] = 6
    elif stoch_bull:
        score += 3
        bd["stochastic_overbought_but_bull"] = 3
    else:
        bd["stochastic_not_bullish"] = 0

    return min(score, 20), bd


# ══════════════════════════════════════════════════════════════════════════
# CATEGORY 3: VOLUME (0–20)
# ══════════════════════════════════════════════════════════════════════════

def score_volume(indicators: dict) -> tuple:
    """Score volume confirmation. Returns (score, breakdown)."""
    score = 0
    bd = {}

    # RVOL >= 1.5: +10, or partial for >= 1.0
    rvol = _gf(indicators, "rvol", "rvol", default=0.0)
    # Also try alternate key paths
    if rvol <= 0:
        rvol = _gf(indicators, "rvol", "value", default=0.0)
    if rvol >= RVOL_HIGH:
        score += 10
        bd["rvol_high"] = 10
    elif rvol >= 1.0:
        score += 5
        bd["rvol_moderate"] = 5
    else:
        bd["rvol_low"] = 0

    # Volume increasing last 3 candles: +5
    inc3 = _gb(indicators, "rvol", "increasing_3", default=False)
    if inc3:
        score += 5
        bd["volume_increasing"] = 5
    else:
        bd["volume_flat"] = 0

    # OBV trending up: +5
    obv_up = _gb(indicators, "obv", "trending_up", default=False)
    if obv_up:
        score += 5
        bd["obv_up"] = 5
    else:
        bd["obv_flat"] = 0

    return min(score, 20), bd


# ══════════════════════════════════════════════════════════════════════════
# CATEGORY 4: RELATIVE STRENGTH (0–20)
# ══════════════════════════════════════════════════════════════════════════

def score_relative_strength(ticker: str, df_5m, df_15m, spy_5m, spy_15m,
                            indicators: dict) -> tuple:
    """Score relative strength vs SPY. Returns (score, breakdown)."""
    score = 0
    bd = {}

    # Stock beats SPY on 5m by >= 0.3%: +10
    try:
        if (df_5m is not None and len(df_5m) >= 2 and
                spy_5m is not None and len(spy_5m) >= 2):
            s_ret = (float(df_5m["Close"].iloc[-1]) - float(df_5m["Close"].iloc[-2])) / float(df_5m["Close"].iloc[-2])
            q_ret = (float(spy_5m["Close"].iloc[-1]) - float(spy_5m["Close"].iloc[-2])) / float(spy_5m["Close"].iloc[-2])
            diff = s_ret - q_ret
            if diff >= RS_MIN_OUTPERFORMANCE:
                score += 10
                bd["beats_spy"] = 10
            elif diff >= 0:
                score += 4
                bd["matches_spy"] = 4
            else:
                bd["lags_spy"] = 0
        else:
            # No SPY data — give partial credit to avoid 0 when data unavailable
            score += 3
            bd["spy_data_missing_partial"] = 3
    except Exception:
        score += 3
        bd["rs_error_partial"] = 3

    # Stock/SPY ratio trending up on 15m: +5
    try:
        if (df_15m is not None and len(df_15m) >= 5 and
                spy_15m is not None and len(spy_15m) >= 5):
            ratio_now = float(df_15m["Close"].iloc[-1]) / float(spy_15m["Close"].iloc[-1])
            ratio_prev = float(df_15m["Close"].iloc[-5]) / float(spy_15m["Close"].iloc[-5])
            if ratio_now > ratio_prev:
                score += 5
                bd["spy_ratio_rising"] = 5
            else:
                bd["spy_ratio_falling"] = 0
        else:
            bd["15m_missing"] = 0
    except Exception:
        bd["ratio_error"] = 0

    # Williams %R > -50: +5
    wr_val = _gf(indicators, "williams_r", "value", default=-999)
    if wr_val > -999 and wr_val > -50:
        score += 5
        bd["williams_r_positive"] = 5
    elif wr_val > -999 and wr_val > -80:
        score += 2
        bd["williams_r_moderate"] = 2
    else:
        bd["williams_r_weak"] = 0

    return min(score, 20), bd


# ══════════════════════════════════════════════════════════════════════════
# CATEGORY 5: RISK STRUCTURE (0–20) — BUY ONLY
# ══════════════════════════════════════════════════════════════════════════

def score_risk_structure(indicators: dict, entry_price: float,
                         direction: str) -> tuple:
    """Score risk/reward structure. Returns (score, bd, stop, tp1, tp2, rr). BUY only."""
    score = 0
    bd = {}
    stop = tp1 = tp2 = rr = 0.0

    try:
        atr_val = _gf(indicators, "atr", "value", default=0.0)
        # Also try alternate key
        if atr_val <= 0:
            atr_val = _gf(indicators, "atr", "atr", default=0.0)

        entry_price = safe_val(entry_price, 0.0)

        if atr_val <= 0 or entry_price <= 0:
            # Can't calculate risk structure — give partial credit
            score += 5
            bd["atr_unavailable_partial"] = 5
            return score, bd, 0.0, 0.0, 0.0, 0.0

        # Always BUY direction
        stop = round(entry_price - ATR_STOP_LOSS_MULTIPLIER * atr_val, 2)
        tp1 = round(entry_price + ATR_TAKE_PROFIT_1_MULTIPLIER * atr_val, 2)
        tp2 = round(entry_price + ATR_TAKE_PROFIT_2_MULTIPLIER * atr_val, 2)

        # Clean stop: +10
        if stop > 0:
            score += 10
            bd["clean_stop"] = 10
        else:
            bd["stop_invalid"] = 0

        # R:R >= 1.5: +10
        risk = abs(entry_price - stop)
        reward = abs(tp1 - entry_price)
        rr = round(reward / risk, 2) if risk > 0 else 0.0
        if rr >= MIN_RR_RATIO:
            score += 10
            bd["rr_good"] = 10
        elif rr >= 1.0:
            score += 5
            bd["rr_acceptable"] = 5
        else:
            bd["rr_poor"] = 0
    except Exception as e:
        logger.debug(f"Risk structure error: {e}")
        score += 5
        bd["risk_calc_error_partial"] = 5

    return min(score, 20), bd, stop, tp1, tp2, rr


# ══════════════════════════════════════════════════════════════════════════
# FULL CONFIDENCE SCORE
# ══════════════════════════════════════════════════════════════════════════

def calculate_full_score(ticker: str, indicators: dict, entry_price: float,
                         direction: str, df_5m=None, df_15m=None,
                         spy_5m=None, spy_15m=None,
                         flow_result: dict = None) -> dict:
    """Calculate complete 100-point confidence score with news modifier,
    institutional flow bonus, and adaptive weight adjustment."""
    # Force direction to BUY
    direction = "BUY"

    trend_s, trend_bd = score_trend_alignment(indicators)
    mom_s, mom_bd = score_momentum(indicators)
    vol_s, vol_bd = score_volume(indicators)
    rs_s, rs_bd = score_relative_strength(ticker, df_5m, df_15m, spy_5m, spy_15m, indicators)
    risk_s, risk_bd, stop, tp1, tp2, rr = score_risk_structure(indicators, entry_price, direction)

    base_score = trend_s + mom_s + vol_s + rs_s + risk_s

    # Institutional flow bonus (0-25 pts, pre-computed by caller or skipped)
    flow_bonus = 0
    flow_summary = ""
    if flow_result and isinstance(flow_result, dict):
        flow_bonus = min(25, int(flow_result.get("flow_bonus", 0)))
        flow_summary = flow_result.get("flow_summary", "")

    # Adaptive weight adjustment (-5 to +10)
    adaptive_adj = _get_adaptive_adjustment(indicators, entry_price)

    # Console debug output
    print(f"  [SCORE] {ticker}: Trend={trend_s}/20 Mom={mom_s}/20 Vol={vol_s}/20 "
          f"RS={rs_s}/20 Risk={risk_s}/20 Base={base_score}/100 "
          f"Flow=+{flow_bonus} Adaptive={adaptive_adj:+d}")

    # News modifier
    news_result = get_news_score_modifier(ticker)
    news_mod = news_result.get("modifier", 0)
    suppress = news_result.get("suppress", False)
    headline = news_result.get("headline", "No major news")

    total = max(0, min(100, base_score + news_mod + flow_bonus + adaptive_adj))
    flag = get_confidence_flag(total)

    print(f"  [SCORE] {ticker}: News={news_mod:+d} Total={total}/100 Flag={flag}")

    explanation = _generate_explanation(
        trend_s, trend_bd, mom_s, mom_bd, vol_s, vol_bd,
        rs_s, rs_bd, risk_s, risk_bd, news_mod, suppress,
    )

    return {
        "total": total,
        "trend": trend_s,
        "momentum": mom_s,
        "volume": vol_s,
        "rs": rs_s,
        "risk": risk_s,
        "flow_bonus": flow_bonus,
        "flow_summary": flow_summary,
        "adaptive_adj": adaptive_adj,
        "news_mod": news_mod,
        "suppress": suppress,
        "headline": headline,
        "flag": flag,
        "stop_loss": stop,
        "tp1": tp1,
        "tp2": tp2,
        "rr_ratio": rr,
        "breakdown": {
            "trend": trend_bd,
            "momentum": mom_bd,
            "volume": vol_bd,
            "rs": rs_bd,
            "risk": risk_bd,
        },
        "explanation": explanation,
    }


# ══════════════════════════════════════════════════════════════════════════
# CLOSED-MARKET SCORER - no trade output outside regular hours
# ══════════════════════════════════════════════════════════════════════════

def closed_market_score(ticker: str) -> dict:
    """Return a neutral score object when the market is closed."""
    return {
        "total": 0,
        "trend": 0,
        "momentum": 0,
        "volume": 0,
        "rs": 0,
        "risk": 0,
        "news_mod": 0,
        "suppress": True,
        "headline": "Market closed - no trade score",
        "flag": FLAG_BELOW,
        "stop_loss": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "rr_ratio": 0.0,
        "breakdown": {
            "trend": 0,
            "momentum": 0,
            "volume": 0,
            "relative_strength": 0,
            "risk_structure": 0,
        },
        "explanation": f"{ticker}: market closed; no trade score generated",
        "after_hours": False,
    }


def calculate_afterhours_score(ticker: str, indicators: dict = None,
                               entry_price: float = 0.0) -> dict:
    """
    Legacy closed-market entry point. Kept for compatibility, but disabled.
    Returns a neutral no-trade score.
    """
    print(f"  [SCORE-CLOSED] {ticker}: closed-market scoring disabled")
    return closed_market_score(ticker)

    print(f"  [SCORE-CLOSED] {ticker}: legacy closed-market scorer disabled")

    score = 25  # Always start with base score of 25
    breakdown = {"trend": 5, "momentum": 5, "volume": 5,
                 "relative_strength": 5, "risk_structure": 5}
    stop = tp1 = tp2 = 0.0
    rr = 0.0

    try:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_daily():
            stock = yf.Ticker(ticker)
            return stock.history(period="60d", interval="1d")

        with ThreadPoolExecutor(max_workers=1) as pool:
            hist = pool.submit(_fetch_daily).result(timeout=15)

        if hist is not None and len(hist) >= 3:
            close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2])
            volume = float(hist["Volume"].iloc[-1])

            # Safe rolling calculations
            if len(hist) >= 20:
                sma20 = float(hist["Close"].rolling(20).mean().iloc[-1])
            else:
                sma20 = close

            if len(hist) >= 50:
                sma50 = float(hist["Close"].rolling(50).mean().iloc[-1])
            else:
                sma50 = close

            vol_avg = float(hist["Volume"].rolling(
                min(20, len(hist))).mean().iloc[-1])
            if vol_avg <= 0:
                vol_avg = volume if volume > 0 else 1.0

            # Use close as entry if not provided
            if entry_price <= 0:
                entry_price = close

            # ── Trend (0-25 additional points) ────────────────────────
            if close > sma20 > sma50:
                score += 25
                breakdown["trend"] = 20
            elif close > sma20:
                score += 15
                breakdown["trend"] = 14
            elif close > sma50:
                score += 8
                breakdown["trend"] = 10
            else:
                breakdown["trend"] = 5

            # ── Daily change (0-20 additional points) ─────────────────
            if prev_close > 0:
                change = (close - prev_close) / prev_close * 100
                if change > 3.0:
                    score += 20
                    breakdown["momentum"] = 18
                elif change > 2.0:
                    score += 16
                    breakdown["momentum"] = 15
                elif change > 1.0:
                    score += 12
                    breakdown["momentum"] = 12
                elif change > 0.3:
                    score += 8
                    breakdown["momentum"] = 9
                elif change > 0:
                    score += 4
                    breakdown["momentum"] = 7
                else:
                    breakdown["momentum"] = 5

            # ── Volume (0-15 additional points) ───────────────────────
            if vol_avg > 0:
                vol_ratio = volume / vol_avg
                if vol_ratio >= 3.0:
                    score += 15
                    breakdown["volume"] = 18
                elif vol_ratio >= 2.0:
                    score += 12
                    breakdown["volume"] = 15
                elif vol_ratio >= 1.5:
                    score += 8
                    breakdown["volume"] = 12
                elif vol_ratio >= 1.0:
                    score += 4
                    breakdown["volume"] = 8
                else:
                    breakdown["volume"] = 5

            # ── 5-day momentum (0-10 additional points) ───────────────
            if len(hist) >= 5:
                five_ago = float(hist["Close"].iloc[-5])
                if five_ago > 0:
                    five_return = (close - five_ago) / five_ago * 100
                    if five_return > 5:
                        score += 10
                        breakdown["relative_strength"] = 18
                    elif five_return > 2:
                        score += 7
                        breakdown["relative_strength"] = 14
                    elif five_return > 0:
                        score += 4
                        breakdown["relative_strength"] = 10
                    else:
                        breakdown["relative_strength"] = 5

            # ── Risk structure ────────────────────────────────────────
            if close > sma20 > sma50:
                breakdown["risk_structure"] = 15
                score += 5
            elif close > sma20:
                breakdown["risk_structure"] = 10
                score += 3
            else:
                breakdown["risk_structure"] = 5

            # ── ATR-based stops (if possible) ─────────────────────────
            if len(hist) >= 14:
                try:
                    highs = hist["High"].tail(14)
                    lows = hist["Low"].tail(14)
                    closes = hist["Close"].tail(14)
                    tr = pd.concat([
                        highs - lows,
                        (highs - closes.shift(1)).abs(),
                        (lows - closes.shift(1)).abs(),
                    ], axis=1).max(axis=1)
                    atr_daily = float(tr.mean())
                    if atr_daily > 0:
                        stop = round(close - 1.0 * atr_daily, 2)
                        tp1 = round(close + 2.0 * atr_daily, 2)
                        tp2 = round(close + 3.5 * atr_daily, 2)
                        risk = abs(close - stop)
                        reward = abs(tp1 - close)
                        rr = round(reward / risk, 2) if risk > 0 else 0.0
                except Exception:
                    pass

    except Exception as data_error:
        print(f"  [SCORE-AH] {ticker}: Data fetch failed: {data_error}")
        # Even if data fetch fails, return the base score of 25

    # Cap score — NEVER below 20
    score = max(20, min(100, score))

    # Normalize breakdown to match score
    total_bd = sum(breakdown.values())
    if total_bd > 0:
        factor = score / total_bd
        breakdown = {k: min(20, max(1, int(v * factor)))
                     for k, v in breakdown.items()}

    flag = get_confidence_flag(score)

    print(f"  [SCORE-CLOSED] {ticker}: closed-market score = {score}/100")
    print(f"  [SCORE-AH] {ticker}: Breakdown = {breakdown}")

    return {
        "total": score,
        "trend": breakdown.get("trend", 5),
        "momentum": breakdown.get("momentum", 5),
        "volume": breakdown.get("volume", 5),
        "rs": breakdown.get("relative_strength", 5),
        "risk": breakdown.get("risk_structure", 5),
        "news_mod": 0,
        "suppress": False,
        "headline": "Market closed - no trade score",
        "flag": flag,
        "stop_loss": stop,
        "tp1": tp1,
        "tp2": tp2,
        "rr_ratio": rr,
        "breakdown": breakdown,
        "explanation": f"Market closed score: {score}/100",
        "after_hours": False,
    }


# ══════════════════════════════════════════════════════════════════════════
# DIRECT INDICATOR SCORER — scores from raw indicator values
# ══════════════════════════════════════════════════════════════════════════

def score_from_available_data(ticker: str, rsi=None, rvol=None,
                              vwap_pct=None, price_change=None) -> dict:
    """
    Score directly from available indicator values (RSI, RVOL, VWAP%, change%).
    Used when we have indicator data displayed but the full scorer returned 0.
    Minimum score is 20. Returns dict compatible with calculate_full_score.
    """
    score = 20  # Base score
    breakdown = {"trend": 5, "momentum": 5, "volume": 5,
                 "relative_strength": 5, "risk_structure": 5}

    try:
        rsi = float(rsi) if rsi is not None else 50.0
    except (TypeError, ValueError):
        rsi = 50.0
    try:
        rvol = float(rvol) if rvol is not None else 1.0
    except (TypeError, ValueError):
        rvol = 1.0
    try:
        vwap_pct = float(vwap_pct) if vwap_pct is not None else 0.0
    except (TypeError, ValueError):
        vwap_pct = 0.0
    try:
        price_change = float(price_change) if price_change is not None else 0.0
    except (TypeError, ValueError):
        price_change = 0.0

    # RSI scoring (0-18 additional)
    if 45 < rsi < 65:
        score += 18
        breakdown["momentum"] = 18
    elif 40 < rsi < 70:
        score += 12
        breakdown["momentum"] = 14
    elif 35 < rsi < 75:
        score += 6
        breakdown["momentum"] = 10

    # RVOL scoring (0-20 additional)
    if rvol >= 5.0:
        score += 20
        breakdown["volume"] = 20
    elif rvol >= 3.0:
        score += 16
        breakdown["volume"] = 18
    elif rvol >= 2.0:
        score += 12
        breakdown["volume"] = 15
    elif rvol >= 1.5:
        score += 8
        breakdown["volume"] = 12
    elif rvol >= 1.0:
        score += 4
        breakdown["volume"] = 8

    # VWAP position scoring (0-15 additional)
    if vwap_pct > 1.0:
        score += 15
        breakdown["relative_strength"] = 16
    elif vwap_pct > 0.3:
        score += 10
        breakdown["relative_strength"] = 13
    elif vwap_pct > 0:
        score += 5
        breakdown["relative_strength"] = 10
    elif vwap_pct > -1.0:
        score += 2
        breakdown["relative_strength"] = 7

    # Price change scoring (0-15 additional)
    if price_change > 2.0:
        score += 15
        breakdown["trend"] = 16
    elif price_change > 1.0:
        score += 10
        breakdown["trend"] = 13
    elif price_change > 0.3:
        score += 6
        breakdown["trend"] = 10
    elif price_change > 0:
        score += 3
        breakdown["trend"] = 7

    # Risk structure based on overall picture
    if score > 70:
        breakdown["risk_structure"] = 16
        score += 5
    elif score > 50:
        breakdown["risk_structure"] = 12
        score += 3

    score = max(20, min(100, score))

    print(f"  [SCORE-DIRECT] {ticker}: Direct indicator score = {score}/100")
    print(f"  [SCORE-DIRECT] {ticker}: RSI={rsi} RVOL={rvol} VWAP%={vwap_pct} Change={price_change}%")

    flag = get_confidence_flag(score)

    return {
        "total": score,
        "trend": breakdown.get("trend", 5),
        "momentum": breakdown.get("momentum", 5),
        "volume": breakdown.get("volume", 5),
        "rs": breakdown.get("relative_strength", 5),
        "risk": breakdown.get("risk_structure", 5),
        "news_mod": 0,
        "suppress": False,
        "headline": "Indicator-based estimate",
        "flag": flag,
        "stop_loss": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "rr_ratio": 0.0,
        "breakdown": breakdown,
        "explanation": f"Direct indicator score: RSI {rsi:.0f}, RVOL {rvol:.1f}x, VWAP {vwap_pct:+.1f}%",
        "after_hours": False,
    }


# ══════════════════════════════════════════════════════════════════════════
# MASTER SCORE ROUTER - regular-hours scorer with closed-market neutral output
# ══════════════════════════════════════════════════════════════════════════

def get_final_score(ticker: str, indicators: dict, entry_price: float = 0.0,
                    direction: str = "BUY", df_5m=None, df_15m=None,
                    spy_5m=None, spy_15m=None,
                    flow_result: dict = None) -> dict:
    """
    Master scoring function. Tries normal scorer first, then fallbacks.
    Returns a neutral 0 score when the market is closed.
    """
    try:
        from scanner_core.utils import is_market_open
        if not is_market_open():
            return closed_market_score(ticker)
    except Exception:
        pass

    try:
        # Priority 1: Try the full scorer
        result = calculate_full_score(
            ticker, indicators, entry_price, direction,
            df_5m=df_5m, df_15m=df_15m,
            spy_5m=spy_5m, spy_15m=spy_15m,
            flow_result=flow_result,
        )
        if result.get("total", 0) > 5:
            return result
        print(f"  [SCORE] {ticker}: Normal scorer returned {result.get('total', 0)}, trying fallbacks")
    except Exception as e:
        print(f"  [SCORE] {ticker}: Normal scorer crashed: {e}")

    try:
        # Priority 2: Try direct indicator scoring if we have indicator data
        rsi = _gf(indicators, "rsi", "value", default=0.0)
        rvol = _gf(indicators, "rvol", "rvol", default=0.0)
        if rvol <= 0:
            rvol = _gf(indicators, "rvol", "value", default=0.0)
        if rsi > 0 or rvol > 0:
            vwap_data = indicators.get("vwap", {}) if isinstance(indicators, dict) else {}
            vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
            vwap_pct = 0.0
            if vwap_val and entry_price > 0:
                try:
                    vwap_pct = (entry_price - float(vwap_val)) / float(vwap_val) * 100
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            result = score_from_available_data(ticker, rsi=rsi, rvol=rvol,
                                              vwap_pct=vwap_pct)
            if result.get("total", 0) > 5:
                return result
    except Exception as e:
        print(f"  [SCORE] {ticker}: Direct scorer failed: {e}")

    # Priority 4: Absolute last resort — never return 0
    print(f"  [SCORE] {ticker}: All scorers failed, returning default 30")
    return {
        "total": 30,
        "trend": 6, "momentum": 6, "volume": 6, "rs": 6, "risk": 6,
        "news_mod": 0, "suppress": False,
        "headline": "Limited data available",
        "flag": FLAG_BELOW,
        "stop_loss": 0.0, "tp1": 0.0, "tp2": 0.0, "rr_ratio": 0.0,
        "breakdown": {"trend": 6, "momentum": 6, "volume": 6,
                      "relative_strength": 6, "risk_structure": 6},
        "explanation": "Score estimated — limited data available",
        "after_hours": False,
    }


# ══════════════════════════════════════════════════════════════════════════
# SCORE ROUTER - full scorer during market hours, neutral when closed
# ══════════════════════════════════════════════════════════════════════════

def get_stock_score(ticker: str, indicators: dict, entry_price: float,
                    direction: str = "BUY", df_5m=None, df_15m=None,
                    spy_5m=None, spy_15m=None) -> dict:
    """
    Smart score router: uses full scorer during market hours and returns
    a neutral no-trade score when the market is closed.
    """
    try:
        from scanner_core.utils import is_market_open
        market_open = is_market_open()
    except ImportError:
        market_open = True  # Default to full scorer if import fails

    if market_open:
        return calculate_full_score(
            ticker, indicators, entry_price, direction,
            df_5m=df_5m, df_15m=df_15m,
            spy_5m=spy_5m, spy_15m=spy_15m,
        )
    else:
        return closed_market_score(ticker)


# ══════════════════════════════════════════════════════════════════════════
# EXPLANATION GENERATOR
# ══════════════════════════════════════════════════════════════════════════

def _generate_explanation(ts, tbd, ms, mbd, vs, vbd, rs, rbd, rsk, rkbd, nm, sup) -> str:
    parts = []
    if ts >= 16:
        parts.append("Strong trend alignment across timeframes")
    elif ts >= 10:
        parts.append("Moderate trend alignment")
    else:
        parts.append("Weak trend alignment")

    if ms >= 16:
        parts.append("momentum confirming")
    elif ms >= 10:
        parts.append("moderate momentum")
    else:
        parts.append("weak momentum")

    if vs >= 16:
        parts.append("volume strongly elevated")
    elif vs >= 10:
        parts.append("volume above average")
    else:
        parts.append("volume weak")

    if rs >= 16:
        parts.append("outperforming SPY")
    elif rs >= 10:
        parts.append("slightly beating SPY")
    else:
        parts.append("underperforming SPY")

    if rsk >= 16:
        parts.append("clean R:R structure")
    elif rsk >= 10:
        parts.append("acceptable risk")
    else:
        parts.append("poor risk structure")

    if nm > 0:
        parts.append(f"news +{nm}")
    if sup:
        parts.append("SUPPRESSED by bearish news")

    return "; ".join(parts[:4]) + "."


# ══════════════════════════════════════════════════════════════════════════
# FLAG & THRESHOLD HELPERS
# ══════════════════════════════════════════════════════════════════════════

def get_confidence_flag(score: int) -> str:
    if score >= CONFIDENCE_HIGH_CONVICTION:
        return FLAG_HIGH_CONVICTION
    if score >= CONFIDENCE_STRONG:
        return FLAG_STRONG
    if score >= CONFIDENCE_CAUTION:
        return FLAG_CAUTION
    return FLAG_BELOW


def classify_threshold(score: int) -> str:
    if score >= CONFIDENCE_HIGH_CONVICTION:
        return "high_conviction"
    if score >= CONFIDENCE_STRONG:
        return "strong"
    if score >= CONFIDENCE_CAUTION:
        return "caution"
    if score >= 50:
        return "watchlist"
    return "no_alert"


# ══════════════════════════════════════════════════════════════════════════
# UNIVERSAL SCORING ENTRY POINT — NEVER RETURNS 0
# ══════════════════════════════════════════════════════════════════════════

def get_score(ticker: str, indicators: dict = None,
              direction: str = "BUY", entry_price: float = 0.0,
              df_5m=None, df_15m=None,
              spy_5m=None, spy_15m=None,
              flow_result: dict = None) -> tuple:
    """
    Single public scoring entry point. Returns (score: int, score_data: dict).
    Returns 0 with a neutral no-trade object when the market is closed.

    Try order:
      1. Full scorer (if indicators provided)
      2. Hard-coded minimum 25
    """
    try:
        from scanner_core.utils import is_market_open
        if not is_market_open():
            result = closed_market_score(ticker)
            return 0, result
    except Exception:
        pass

    try:
        # Priority 1: Full scorer with indicators
        if indicators is not None and isinstance(indicators, dict) and indicators:
            result = get_final_score(
                ticker, indicators, entry_price, direction,
                df_5m=df_5m, df_15m=df_15m,
                spy_5m=spy_5m, spy_15m=spy_15m,
                flow_result=flow_result,
            )
            score = result.get("total", 0)
            if score > 5:
                return score, result

    except Exception as e:
        print(f"  [SCORE] {ticker}: get_score crashed: {e}")

    # Priority 3: Hard-coded minimum
    fallback = {
        "total": 25, "trend": 5, "momentum": 5, "volume": 5, "rs": 5, "risk": 5,
        "news_mod": 0, "suppress": False, "headline": "Limited data",
        "flag": FLAG_BELOW, "stop_loss": 0.0, "tp1": 0.0, "tp2": 0.0,
        "rr_ratio": 0.0, "flow_bonus": 0, "flow_summary": "", "adaptive_adj": 0,
        "breakdown": {"trend": 5, "momentum": 5, "volume": 5,
                      "relative_strength": 5, "risk_structure": 5},
        "explanation": "Minimum estimate — limited data",
        "after_hours": False,
    }
    return 25, fallback


# ── Backward-compatibility aliases ───────────────────────────────────────
calculate_confidence_score = calculate_full_score
