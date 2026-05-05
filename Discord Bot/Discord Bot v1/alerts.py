# alerts.py — Discord Embed builders for every bot response
#
# ALL formatters now return discord.Embed objects with proper colours.
# Plain-text fallbacks are gone — every output uses the embed system.

import logging
import math
from datetime import datetime
from typing import Optional

import discord
import pytz

from config import (
    MARKET_TIMEZONE, MAX_MESSAGE_LENGTH,
    CONFIDENCE_CAUTION, CONFIDENCE_STRONG, CONFIDENCE_HIGH_CONVICTION,
    FLAG_CAUTION, FLAG_STRONG, FLAG_HIGH_CONVICTION, FLAG_BELOW,
    ALL_STRATEGIES, BOT_NAME, VERSION,
    EMBED_COLOR_BUY, EMBED_COLOR_SELL, EMBED_COLOR_HIGH_CONVICTION,
    EMBED_COLOR_CAUTION, EMBED_COLOR_INFO, EMBED_COLOR_AFTER_HOURS,
    EMBED_COLOR_TEST, EMBED_COLOR_GOOD_NEWS, EMBED_COLOR_ERROR,
    EMBED_COLOR_MARKET_CLOSED, EMBED_COLOR_STRONG_TREND,
    EMBED_COLOR_RANGING, EMBED_COLOR_HIGH_VOL,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


# ══════════════════════════════════════════════════════════════════════════
# COLOUR HELPERS
# ══════════════════════════════════════════════════════════════════════════

def pick_embed_color(direction: str = "BUY", score: int = 0,
                     after_hours: bool = False, test_mode: bool = False) -> int:
    """Select the embed colour based on context."""
    if test_mode:
        return EMBED_COLOR_TEST
    if after_hours:
        return EMBED_COLOR_AFTER_HOURS
    if score >= 95:
        return EMBED_COLOR_HIGH_CONVICTION
    # SELL color removed — BUY only
    if score >= 70:
        return EMBED_COLOR_BUY
    if score >= 50:
        return EMBED_COLOR_CAUTION
    return EMBED_COLOR_INFO


# ══════════════════════════════════════════════════════════════════════════
# EMOJI CONFIDENCE BAR  (Change 8)
# ══════════════════════════════════════════════════════════════════════════

def emoji_confidence_bar(score: int, max_score: int = 100) -> str:
    """
    Build a coloured emoji progress bar.
    Score 85+: green 🟩 | 70-84: yellow 🟨 | 50-69: orange 🟧 | <50: red 🟥
    Empty slots: ⬜
    """
    # Null safety — never crash on bad input
    try:
        score = max(0, min(max_score, int(score) if score is not None else 0))
    except (TypeError, ValueError):
        score = 0
    filled = min(round(score / max_score * 10), 10)
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
    flag = _flag_emoji(score)
    return f"{bar} {score}/{max_score} {flag}"


def _flag_emoji(score: int) -> str:
    if score >= 95:
        return "🔥"
    if score >= 85:
        return "✅"
    if score >= 70:
        return "⚠️"
    return "⚪"


def signal_strength_label(score: int) -> str:
    """Plain English star rating for signal strength."""
    if score >= 95:
        return "\u2B50\u2B50\u2B50\u2B50\u2B50 Exceptional Setup"
    if score >= 85:
        return "\u2B50\u2B50\u2B50\u2B50 Strong Setup"
    if score >= 75:
        return "\u2B50\u2B50\u2B50 Good Setup"
    if score >= 65:
        return "\u2B50\u2B50 Moderate Setup"
    if score >= 50:
        return "\u2B50 Weak Setup"
    return "No Setup"


def _footer_text(state: str = "") -> str:
    """Consistent footer for every embed with market state emoji."""
    now_dt = datetime.now(ET)
    now = now_dt.strftime("%I:%M %p ET")
    if state:
        mstate = market_state_footer(state)
    else:
        from utils import is_market_open
        if is_market_open():
            mstate = "\U0001f4c8 Market Open"
        else:
            day = now_dt.strftime("%A")
            mstate = f"{day} - Market Closed"
    return f"{BOT_NAME} V{VERSION[0]} \u2022 {now} \u2022 {mstate}"


def _trend_arrow(current_score: int, prev_score: int = 0) -> str:
    """Show score trend direction."""
    if prev_score <= 0:
        return ""
    diff = current_score - prev_score
    if diff >= 5:
        return " \u2B06\uFE0F"
    if diff <= -5:
        return " \u2B07\uFE0F"
    return " \u27A1\uFE0F"


# ══════════════════════════════════════════════════════════════════════════
# INDICATOR LABEL HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _safe_get(d, *keys, default=0.0):
    v = d
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k, default)
    return v if v is not None else default


def _rsi_label(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "RSI: N/A"
    if v >= 70:
        return f"RSI {v:.0f} (overbought)"
    if v <= 30:
        return f"RSI {v:.0f} (oversold)"
    return f"RSI {v:.0f}"


def _macd_label(indicators: dict) -> str:
    macd = indicators.get("macd", {})
    bull = macd.get("bullish", False)
    hist = macd.get("histogram", 0)
    if bull:
        return f"MACD bullish (hist {hist:+.3f})"
    return f"MACD bearish (hist {hist:+.3f})"


def _vwap_label(entry_price, indicators: dict) -> str:
    vwap = indicators.get("vwap", {})
    vwap_val = vwap.get("value", vwap.get("vwap", 0))
    if vwap_val <= 0:
        return "VWAP: N/A"
    diff_pct = (entry_price - vwap_val) / vwap_val * 100
    side = "above" if diff_pct > 0 else "below"
    return f"VWAP ${vwap_val:.2f} ({abs(diff_pct):.1f}% {side})"


def _rvol_label(indicators: dict) -> str:
    rvol = indicators.get("rvol", {})
    val = rvol.get("rvol", rvol.get("value", 1.0))
    return f"RVOL {val:.1f}x"


def _supertrend_label(indicators: dict) -> str:
    st = indicators.get("supertrend", {})
    if st.get("bullish"):
        return "SuperTrend ↑ Bullish"
    if st.get("bearish"):
        return "SuperTrend ↓ Bearish"
    return "SuperTrend — Neutral"


def _tf_emoji(status: str) -> str:
    s = str(status).lower()
    if s in ("bullish", "bull", "true", "1", "buy"):
        return "✅"
    if s in ("bearish", "bear", "false", "0", "sell"):
        return "❌"
    return "⚪"


def _dir_emoji(direction: str) -> str:
    return "🟢" if direction.upper() == "BUY" else "⚪"


def _stop_pct(entry, stop):
    if entry <= 0:
        return "N/A"
    return f"{abs(entry - stop) / entry * 100:.2f}%"


def _target_pct(entry, target):
    if entry <= 0:
        return "N/A"
    return f"{abs(target - entry) / entry * 100:.2f}%"


def _rr_str(entry, stop, target):
    risk = abs(entry - stop)
    if risk <= 0:
        return "N/A"
    reward = abs(target - entry)
    return f"{reward / risk:.1f}:1"


# ══════════════════════════════════════════════════════════════════════════
# PLAIN ENGLISH TRANSLATIONS  (Change 15)
# ══════════════════════════════════════════════════════════════════════════

PLAIN_ENGLISH_MAP = {
    "rsi": lambda v: f"Buying momentum: {v:.0f}/100" if v else "Momentum: N/A",
    "macd_bull": "Buyers just took control of the trend",
    "macd_bear": "Sellers are in control of the trend",
    "supertrend_bull": "Overall direction is upward",
    "supertrend_bear": "Overall direction is downward",
    "adx": lambda v: (f"Trend is strong and clear ({v:.0f})" if v >= 25
                       else f"Trend is weak or choppy ({v:.0f})") if v else "Trend strength: N/A",
    "rvol": lambda v: f"Volume is {(v-1)*100:.0f}% {'higher' if v>1 else 'lower'} than normal" if v else "Volume: N/A",
    "ema_stack_bull": "Short-term trend is stronger than medium-term (bullish alignment)",
    "ema_stack_bear": "Short-term trend is weaker than medium-term (bearish alignment)",
    "bb_squeeze": "Stock has been quiet and may be about to make a big move",
    "fib_618": "Price has pulled back to a historically significant support level",
    "stoch_bull": "Short-term momentum is turning positive",
    "stoch_bear": "Short-term momentum is turning negative",
    "atr_stop": "Stop loss placed at a safe distance based on normal price movement",
}


def plain_english_indicator(key: str, value=None) -> str:
    """Translate a technical indicator key into plain English."""
    entry = PLAIN_ENGLISH_MAP.get(key)
    if callable(entry):
        return entry(value)
    if isinstance(entry, str):
        return entry
    return str(value) if value is not None else key


def plain_english_score(score: int) -> str:
    """Translate confidence score to plain English."""
    if score >= 95:
        return f"Rating: {score}/100 — Excellent setup, very strong conditions"
    if score >= 85:
        return f"Rating: {score}/100 — Strong setup worth watching closely"
    if score >= 70:
        return f"Rating: {score}/100 — Moderate setup, proceed with caution"
    if score >= 50:
        return f"Rating: {score}/100 — Weak conditions, not ideal for trading"
    return f"Rating: {score}/100 — Poor conditions, best to wait"


# ══════════════════════════════════════════════════════════════════════════
# MAIN ALERT EMBED  (trade alert card)
# ══════════════════════════════════════════════════════════════════════════

def build_alert_card(ticker: str, direction: str, strategy: str,
                     entry_price: float, stop: float, tp1: float, tp2: float,
                     rr: float, score_data: dict, indicators: dict,
                     regime_label: str = "TRENDING",
                     bullish_count: int = 0, bearish_count: int = 0,
                     conditions_met: list = None, conditions_failed: list = None,
                     after_hours: bool = False, plain_english: bool = False,
                     tp3: float = 0.0, regime_quality: int = 0,
                     **kwargs) -> discord.Embed:
    """Build the premium trade alert embed."""
    total = score_data.get("total", 0)
    color = pick_embed_color(direction, total, after_hours)

    strat_display = strategy if strategy and strategy != "None" else "No Active Setup"

    title = f"Trade Alert \u2014 ${ticker}"
    if after_hours:
        title = f"Market Closed - ${ticker}"

    embed = discord.Embed(title=title, color=color)

    # ── Row 1: Direction, Strategy, Confidence (inline) ───────────
    embed.add_field(name="Direction", value=f"{_dir_emoji(direction)} **{direction}**", inline=True)
    embed.add_field(name="Strategy", value=f"**{strat_display}**", inline=True)

    bar = emoji_confidence_bar(total)
    prev_score = kwargs.get("prev_score", 0)
    arrow = _trend_arrow(total, prev_score)
    strength = signal_strength_label(total)
    embed.add_field(name="Confidence", value=f"{bar}{arrow}\n{strength}", inline=True)

    # ── Row 2: ENTRY + LEVELS (prominent SL/TP first) ──────────
    embed.add_field(name="Entry", value=f"**`{fmt_price(entry_price)}`**", inline=True)
    sl_pct = ((stop - entry_price) / entry_price * 100) if entry_price else 0
    embed.add_field(
        name="\U0001f6d1 Stop Loss",
        value=f"`{fmt_price(stop)}` `{sl_pct:+.1f}%`",
        inline=True,
    )
    embed.add_field(name="R:R Ratio", value=f"`{_rr_str(entry_price, stop, tp1)}`", inline=True)

    # ── Row 3: Targets (inline) ──────────────────────────────────
    embed.add_field(
        name="\U0001f3af TP1",
        value=f"`{fmt_price(tp1)}` `+{_target_pct(entry_price, tp1)}` (1.5R)",
        inline=True,
    )
    embed.add_field(
        name="\U0001f3af\U0001f3af TP2",
        value=f"`{fmt_price(tp2)}` `+{_target_pct(entry_price, tp2)}` (2.5R)",
        inline=True,
    )
    if tp3 != 0:
        embed.add_field(
            name="\U0001f680 TP3",
            value=f"`{fmt_price(tp3)}` `+{_target_pct(entry_price, tp3)}` (4.0R)",
            inline=True,
        )

    # ── Row 4: Key Indicators with context labels ────────────────
    rsi_val = _safe_get(indicators, "rsi", "value")
    rvol_val = _safe_get(indicators, "rvol", "rvol")
    if rvol_val <= 0:
        rvol_val = _safe_get(indicators, "rvol", "value")

    embed.add_field(name="RSI", value=rsi_context(rsi_val), inline=True)
    embed.add_field(name="RVOL", value=rvol_context(rvol_val), inline=True)

    vwap_data = indicators.get("vwap", {})
    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
    if vwap_val and vwap_val > 0:
        vp = (entry_price - vwap_val) / vwap_val * 100
        embed.add_field(name="VWAP", value=vwap_context(vp), inline=True)
    else:
        embed.add_field(name="VWAP", value="\u26AA N/A", inline=True)

    # ── Row 5: Score Breakdown (not inline) ──────────────────────
    t = score_data.get("trend", 0)
    m = score_data.get("momentum", 0)
    v = score_data.get("volume", 0)
    rs = score_data.get("rs", 0)
    rk = score_data.get("risk", 0)
    score_text = f"Trend `{t}/20` \u2022 Momentum `{m}/20` \u2022 Volume `{v}/20`\nRel. Strength `{rs}/20` \u2022 Risk `{rk}/20`"

    extras = []
    news_mod = score_data.get("news_mod", 0)
    if news_mod != 0:
        extras.append(f"News `{news_mod:+d}`")
    flow_bonus = score_data.get("flow_bonus", 0)
    if flow_bonus > 0:
        extras.append(f"Flow `+{flow_bonus}`")
    adaptive_adj = score_data.get("adaptive_adj", 0)
    if adaptive_adj != 0:
        extras.append(f"Adaptive `{adaptive_adj:+d}`")
    if extras:
        score_text += "\n" + " \u2022 ".join(extras)
    embed.add_field(name="Score Breakdown", value=score_text, inline=False)

    # ── Row 6: News headline ─────────────────────────────────────
    headline = score_data.get("headline", "")
    if headline and headline not in ("No major news", "Limited data", "Market closed - no trade score", "Limited data available"):
        embed.add_field(name="\U0001f4f0 News", value=headline, inline=False)

    # ── Row 7: Market context ────────────────────────────────────
    market_state_label = kwargs.get("market_state_label", "")
    prev_close_info = kwargs.get("prev_close_info", "")
    mkt_parts = []
    if market_state_label:
        mkt_parts.append(f"\U0001f4e1 {market_state_label}")
    else:
        mkt_parts.append(f"\U0001f4e1 {regime_label}")
    if regime_quality > 0:
        mkt_parts.append(f"Regime Q: `{regime_quality}/100`")
    if prev_close_info:
        mkt_parts.append(prev_close_info)
    embed.add_field(name="Market", value=" \u2022 ".join(mkt_parts), inline=False)

    # ── Footer ───────────────────────────────────────────────────
    embed.set_footer(text=_footer_text())

    return embed


# ══════════════════════════════════════════════════════════════════════════
# SCAN RESULT EMBED  (returned by /scan and Refresh button)
# ══════════════════════════════════════════════════════════════════════════

def build_scan_embed(signal: dict, after_hours: bool = False,
                     plain_english: bool = False) -> discord.Embed:
    """Build embed from a scan_ticker result dict."""
    return build_alert_card(
        ticker=signal["ticker"],
        direction=signal["direction"],
        strategy=signal.get("strategy", "Unknown"),
        entry_price=signal["entry_price"],
        stop=signal.get("stop", 0),
        tp1=signal.get("tp1", 0),
        tp2=signal.get("tp2", 0),
        rr=signal.get("rr", 0),
        score_data=signal.get("score_data", {}),
        indicators=signal.get("indicators", {}),
        regime_label=signal.get("regime", "TRENDING"),
        bullish_count=signal.get("bullish_count", 0),
        bearish_count=signal.get("bearish_count", 0),
        conditions_met=signal.get("conditions_met", []),
        conditions_failed=signal.get("conditions_failed", []),
        after_hours=after_hours,
        plain_english=plain_english,
        tp3=signal.get("tp3", 0),
        regime_quality=signal.get("regime_quality", 0),
        signal_quality=signal.get("signal_quality", 0),
        market_state_label=signal.get("market_state_label", ""),
        stop_type=signal.get("stop_type", ""),
        flow_summary=(signal.get("flow_result") or {}).get("flow_summary", ""),
    )


# ══════════════════════════════════════════════════════════════════════════
# TICKER CARD  (quick overview when full scan has no signal)
# ══════════════════════════════════════════════════════════════════════════

def build_ticker_embed(ticker: str, state: dict = None,
                       after_hours: bool = False) -> discord.Embed:
    """Build a quick ticker overview embed from cached state.
    If score is 0 and we have indicator data, re-score using fallback."""
    if state is None:
        from scanner import get_ticker_state
        state = get_ticker_state(ticker)

    if not state:
        closed_text = (
            f"Market is closed. No trade scan is active for {ticker}; try `/scan {ticker}` during regular market hours."
            if after_hours else
            f"No cached data for {ticker}. Try `/scan {ticker}` during market hours."
        )
        embed = discord.Embed(
            title=f"Analysis \u2014 ${ticker}",
            description=closed_text,
            color=EMBED_COLOR_INFO,
        )
        embed.set_footer(text=_footer_text())
        return embed

    price = state.get("price", 0)
    score = state.get("score", 0)
    direction = state.get("direction", "NEUTRAL")
    change = state.get("change_pct", 0)
    strategy = state.get("strategy", "None")
    indicators = state.get("indicators", {})
    prev_score = state.get("prev_score", 0)

    if after_hours:
        score = 0
        direction = "NEUTRAL"
        strategy = "Market Closed"

    # ── Re-score if score is 0 but we have indicator data ──
    if not after_hours and score <= 5 and indicators:
        try:
            from scoring import get_score
            score, _ = get_score(ticker, indicators=indicators, entry_price=price)
        except Exception:
            score = max(score, 20)

    # Strategy label fix — never show "None"
    if strategy in ("None", "none", "", None):
        strategy = "No Active Setup"

    color = pick_embed_color(direction, score, False)
    embed = discord.Embed(
        title=f"Analysis \u2014 ${ticker}",
        color=color,
    )
    if after_hours:
        embed.description = "Market closed. This is cached reference data only; no trade alert is active."

    # Row 1: Direction, Strategy, Price (inline)
    embed.add_field(name="Direction", value=f"{_dir_emoji(direction)} **{direction}**", inline=True)
    embed.add_field(name="Strategy", value=f"**{strategy}**", inline=True)
    embed.add_field(name="Price", value=f"`${price:.2f}` `{change:+.2f}%`", inline=True)

    # Confidence with trend arrow and strength label
    bar = emoji_confidence_bar(score)
    arrow = _trend_arrow(score, prev_score)
    strength = signal_strength_label(score)
    embed.add_field(name="Confidence", value=f"{bar}{arrow}\n{strength}", inline=False)

    # Indicator grid (inline)
    rsi_val = _safe_get(indicators, "rsi", "value")
    adx_val = _safe_get(indicators, "adx", "value")
    rvol_val = _safe_get(indicators, "rvol", "rvol")
    if rvol_val <= 0:
        rvol_val = _safe_get(indicators, "rvol", "value")

    macd = indicators.get("macd", {})
    macd_bull = macd.get("bullish", False)
    macd_hist = macd.get("histogram", 0)
    st = indicators.get("supertrend", {})
    st_bull = st.get("bullish", False)
    ema = indicators.get("ema", indicators.get("ema_stack", {}))
    ema9 = ema.get("ema9", ema.get("ema_9", 0))
    ema21 = ema.get("ema21", ema.get("ema_21", 0))
    ema50 = ema.get("ema50", ema.get("ema_50", 0))
    bb = indicators.get("bbands", indicators.get("bollinger", {}))
    bb_pct = bb.get("pct_b", bb.get("percent_b", 0.5))
    stoch = indicators.get("stochastic", {})
    stoch_k = stoch.get("k", stoch.get("slowk", 0))
    stoch_d = stoch.get("d", stoch.get("slowd", 0))
    williams = indicators.get("williams", {})
    wr_val = williams.get("value", williams.get("williams_r", 0))
    obv = indicators.get("obv", {})
    obv_rising = obv.get("rising", obv.get("bullish", False))
    atr_data = indicators.get("atr", {})
    atr_val = atr_data.get("value", atr_data.get("atr", 0))

    # RSI
    rsi_e = "\u2705" if 40 <= rsi_val <= 70 else ("\u26a0\ufe0f" if rsi_val > 70 else "\U0001f4c9")
    rsi_lbl = "Healthy" if 40 <= rsi_val <= 70 else ("Overbought" if rsi_val > 70 else "Oversold")
    # MACD
    macd_e = "\u2705 Bullish" if macd_bull else "\u274c Bearish"
    # SuperTrend
    st_e = "\u2705 Bullish" if st_bull else "\u274c Bearish"
    # EMA Stack
    ema_aligned = (ema9 > ema21 > ema50 > 0) if all(isinstance(x, (int, float)) for x in [ema9, ema21, ema50]) else False
    ema_e = "\u2705 9>21>50 Aligned" if ema_aligned else "\u274c Not Aligned"
    # VWAP
    vwap_data = indicators.get("vwap", {})
    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
    if vwap_val and vwap_val > 0 and price > 0:
        vp = (price - vwap_val) / vwap_val * 100
        vwap_e = f"\u2705 `{vp:+.1f}%` Above" if vp > 0 else f"\u274c `{vp:+.1f}%` Below"
    else:
        vwap_e = "N/A"
    # ADX
    adx_e = "\u2705 Trending" if adx_val >= 25 else ("\u26a0\ufe0f Weak" if adx_val >= 15 else "\u274c Flat")
    # ATR
    atr_pct = (atr_val / price * 100) if price > 0 and atr_val > 0 else 0
    # Bollinger
    if isinstance(bb_pct, (int, float)):
        bb_e = "\u2705 Mid-band" if 0.3 <= bb_pct <= 0.7 else ("\u26a0\ufe0f Extended" if bb_pct > 0.8 else "\U0001f4c9 Low")
    else:
        bb_e = "N/A"
    # Stochastic
    stoch_txt = f"`{stoch_k:.0f}/{stoch_d:.0f}`" if stoch_k else "N/A"
    stoch_e = "\u26a0\ufe0f Nearing OB" if stoch_k > 75 else ("\u2705" if stoch_k > 20 else "\U0001f4c9 OS")
    # Williams %R
    wr_e = "\u2705 Positive" if wr_val > -50 else "\u274c Negative"
    # OBV
    obv_e = "\u2705 Rising" if obv_rising else "\u274c Falling"
    # RVOL
    rvol_e = "\U0001f525 High" if rvol_val >= 2.0 else ("\u2705" if rvol_val >= 1.2 else "\u26aa Low")

    ind_col1 = (
        f"RSI: `{rsi_val:.1f}` {rsi_e} {rsi_lbl}\n"
        f"MACD: `{macd_hist:+.3f}` {macd_e}\n"
        f"SuperTrend: {st_e}\n"
        f"Stochastic: {stoch_txt} {stoch_e}\n"
        f"EMA Stack: {ema_e}\n"
        f"VWAP: {vwap_e}"
    )
    ind_col2 = (
        f"ADX: `{adx_val:.1f}` {adx_e}\n"
        f"ATR: `${atr_val:.2f}` ({atr_pct:.2f}%)\n"
        f"Bollinger: {bb_e}\n"
        f"RVOL: `{rvol_val:.1f}x` {rvol_e}\n"
        f"Williams %R: `{wr_val:.0f}` {wr_e}\n"
        f"OBV: {obv_e}"
    )
    embed.add_field(name="Indicators", value=ind_col1, inline=True)
    embed.add_field(name="\u200b", value=ind_col2, inline=True)

    embed.set_footer(text=_footer_text())
    return embed


# ══════════════════════════════════════════════════════════════════════════
# COMPARE EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_compare_embed(t1: str, s1: dict, t2: str, s2: dict) -> discord.Embed:
    """Side-by-side comparison of two tickers."""
    embed = discord.Embed(title=f"⚔️ {t1} vs {t2}", color=EMBED_COLOR_INFO)

    def _better(v1, v2, higher_better=True):
        if higher_better:
            return ("✅", "") if v1 > v2 else (("", "✅") if v2 > v1 else ("", ""))
        return ("✅", "") if v1 < v2 else (("", "✅") if v2 < v1 else ("", ""))

    metrics = [
        ("Price", s1.get("price", 0), s2.get("price", 0), False),
        ("Change %", s1.get("change_pct", 0), s2.get("change_pct", 0), True),
        ("Score", s1.get("score", 0), s2.get("score", 0), True),
    ]

    ind1 = s1.get("indicators", {})
    ind2 = s2.get("indicators", {})
    rsi1 = _safe_get(ind1, "rsi", "value")
    rsi2 = _safe_get(ind2, "rsi", "value")
    rvol1 = _safe_get(ind1, "rvol", "rvol")
    rvol2 = _safe_get(ind2, "rvol", "rvol")
    adx1 = _safe_get(ind1, "adx", "value")
    adx2 = _safe_get(ind2, "adx", "value")

    left_lines = []
    right_lines = []

    for name, v1, v2, hb in metrics:
        b1, b2 = _better(v1, v2, hb)
        if isinstance(v1, float):
            left_lines.append(f"**{name}:** {v1:.2f} {b1}")
            right_lines.append(f"**{name}:** {v2:.2f} {b2}")
        else:
            left_lines.append(f"**{name}:** {v1} {b1}")
            right_lines.append(f"**{name}:** {v2} {b2}")

    b1, b2 = _better(rsi1, rsi2, True)
    left_lines.append(f"**RSI:** {rsi1:.0f} {b1}")
    right_lines.append(f"**RSI:** {rsi2:.0f} {b2}")
    b1, b2 = _better(rvol1, rvol2, True)
    left_lines.append(f"**RVOL:** {rvol1:.1f}x {b1}")
    right_lines.append(f"**RVOL:** {rvol2:.1f}x {b2}")
    b1, b2 = _better(adx1, adx2, True)
    left_lines.append(f"**ADX:** {adx1:.0f} {b1}")
    right_lines.append(f"**ADX:** {adx2:.0f} {b2}")

    left_lines.append(f"**Direction:** {s1.get('direction', 'N/A')}")
    right_lines.append(f"**Direction:** {s2.get('direction', 'N/A')}")
    left_lines.append(f"**Strategy:** {s1.get('strategy', 'None')}")
    right_lines.append(f"**Strategy:** {s2.get('strategy', 'None')}")

    embed.add_field(name=f"${t1}", value="\n".join(left_lines), inline=True)
    embed.add_field(name=f"${t2}", value="\n".join(right_lines), inline=True)
    embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# MARKET OVERVIEW EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_market_overview_embed(regime_data: dict, spy_data: dict,
                                breadth: dict, after_hours: bool = False,
                                sector_data: dict = None) -> discord.Embed:
    """Build the /market overview embed with sector heatmap."""
    color = EMBED_COLOR_AFTER_HOURS if after_hours else EMBED_COLOR_INFO
    regime_label = regime_data.get("label", "UNKNOWN")
    embed = discord.Embed(title="\U0001f3db\uFE0F Market Overview", color=color)
    if after_hours:
        embed.description = "MARKET CLOSED - showing most recent reference data only"

    embed.add_field(
        name="Regime",
        value=f"**{regime_label}** {market_state_footer(regime_label)}",
        inline=True,
    )
    spy_price = spy_data.get("price", 0)
    spy_change = spy_data.get("change_pct", 0)
    spy_emoji = "\u2705" if spy_change >= 0 else "\u274C"
    embed.add_field(
        name="SPY",
        value=f"`{fmt_price(spy_price)}` `{spy_change:+.1f}%` {spy_emoji}" if spy_price else "N/A",
        inline=True,
    )

    total = breadth.get("total", 0)
    embed.add_field(
        name="Market Breadth",
        value=(
            f"Bullish: `{breadth.get('bullish', 0)}/{total}` "
            f"(`{breadth.get('bullish_pct', 0):.0f}%`)\n"
            f"Bearish: `{breadth.get('bearish', 0)}/{total}` "
            f"(`{breadth.get('bearish_pct', 0):.0f}%`)\n"
            f"A/D Ratio: `{breadth.get('ad_ratio', 0):.2f}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Scores",
        value=(
            f"Avg: `{breadth.get('avg_score', 0):.0f}` | "
            f"70+: `{breadth.get('above_70', 0)}` | "
            f"85+: `{breadth.get('above_85', 0)}`"
        ),
        inline=True,
    )

    # ── Sector Heatmap ───────────────────────────────────────────
    if sector_data:
        sector_names = {
            "XLK": "Tech", "XLF": "Finance", "XLE": "Energy",
            "XLV": "Health", "XLY": "Consumer", "XLI": "Industrial",
            "XLP": "Staples", "XLU": "Utilities", "XLRE": "Real Estate",
        }
        sector_lines = []
        best_etf, best_pct = "", -999
        worst_etf, worst_pct = "", 999
        for etf, pct in sorted(sector_data.items(), key=lambda x: x[1], reverse=True):
            name = sector_names.get(etf, etf)
            emoji = "\u2705" if pct > 0.3 else "\u274C" if pct < -0.3 else "\u26AA"
            sector_lines.append(f"{etf} ({name}): `{pct:+.1f}%` {emoji}")
            if pct > best_pct:
                best_etf, best_pct = etf, pct
            if pct < worst_pct:
                worst_etf, worst_pct = etf, pct
        if sector_lines:
            sector_text = "\n".join(sector_lines)
            if best_etf:
                sector_text += f"\n\nBest: **{best_etf}** `{best_pct:+.1f}%` \U0001f525"
            if worst_etf:
                sector_text += f"\nWorst: **{worst_etf}** `{worst_pct:+.1f}%` \u274C"
            embed.add_field(name="Sector Heatmap", value=sector_text, inline=False)

    embed.set_footer(text=_footer_text(regime_label))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# SCAN SUMMARY EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_scan_summary(results: list, regime_label: str) -> discord.Embed:
    """Summary embed for multiple scan results."""
    embed = discord.Embed(title="📊 Scan Summary", color=EMBED_COLOR_INFO)
    embed.description = f"**Regime:** {regime_label} | **Signals:** {len(results)}"

    for sig in results[:10]:
        ticker = sig.get("ticker", "?")
        score = sig.get("score_data", {}).get("total", 0)
        direction = sig.get("direction", "?")
        strategy = sig.get("strategy", "?")
        bar = emoji_confidence_bar(score)
        embed.add_field(
            name=f"{_dir_emoji(direction)} ${ticker}",
            value=f"{bar}\n{strategy}",
            inline=True,
        )

    if len(results) > 10:
        embed.set_footer(text=f"Showing 10 of {len(results)} signals")
    return embed


# ══════════════════════════════════════════════════════════════════════════
# BEST BUYS EMBED  (Change 16 — always show top 5)
# ══════════════════════════════════════════════════════════════════════════

def _best_label(score: int) -> str:
    if score >= 85:
        return "✅ Strong Setup — Good conditions to consider"
    if score >= 70:
        return "⚠️ Moderate Setup — Worth watching but not ideal"
    if score >= 50:
        return "🔶 Weak Setup — Conditions not favorable right now"
    return "❌ Poor Setup — Not recommended for trading"


def build_best_buys_card(stocks: list, after_hours: bool = False,
                         last_trading_date: str = "") -> discord.Embed:
    """Build /best embed — premium design with mini stock cards."""
    top_score = stocks[0].get("score", 0) if stocks else 0
    if top_score >= 85:
        color = EMBED_COLOR_BUY
    elif top_score >= 70:
        color = EMBED_COLOR_CAUTION
    else:
        color = EMBED_COLOR_AFTER_HOURS

    embed = discord.Embed(title="Best Opportunities Right Now", color=color)
    if after_hours:
        embed.description = f"Market closed - no live opportunities. Last regular-session data: {last_trading_date}"

    medals = ["\U0001f947", "\U0001f948", "\U0001f949", "4\ufe0f\u20e3", "5\ufe0f\u20e3"]
    for i, stock in enumerate(stocks[:5]):
        ticker = stock.get("ticker", "?")
        score = stock.get("score", 0)
        price = stock.get("price", 0)
        change = stock.get("change_pct", 0)
        strategy = stock.get("strategy", "") or ""
        if not strategy or strategy == "None":
            strategy = "No Active Setup"

        bar = emoji_confidence_bar(score)
        strength = signal_strength_label(score)
        flag = _flag_emoji(score)

        embed.add_field(
            name=f"{medals[i]} ${ticker} \u2014 `{score}/100` {flag}",
            value=(
                f"`${price:.2f}` \u2022 `{change:+.1f}%` \u2022 {strategy}\n"
                f"{bar}\n{strength}"
            ),
            inline=False,
        )

    # Market context footer
    try:
        from market_state import get_current_market_state, get_state_label
        ms = get_state_label()
    except Exception:
        ms = ""
    if ms:
        embed.add_field(name="\u200b", value=f"\U0001f4e1 Market: **{ms}**", inline=False)

    embed.set_footer(text=_footer_text())
    return embed


# ══════════════════════════════════════════════════════════════════════════
# NEWS EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_news_embed(ticker: str, headlines: list) -> discord.Embed:
    """Build news card embed."""
    embed = discord.Embed(title=f"📰 News — ${ticker}", color=EMBED_COLOR_INFO)
    if not headlines:
        embed.description = "No recent headlines found."
        return embed

    from news import classify_sentiment, get_sentiment_emoji
    for item in headlines[:5]:
        headline = item.get("headline", "No headline")
        source = item.get("source", "Unknown")
        sentiment = classify_sentiment(headline)
        emoji = get_sentiment_emoji(sentiment)
        ts = item.get("datetime", 0)
        time_str = datetime.fromtimestamp(ts, tz=ET).strftime("%I:%M %p") if ts else ""
        embed.add_field(
            name=f"{emoji} {headline[:80]}",
            value=f"*{source}* {time_str}",
            inline=False,
        )
    embed.set_footer(text=_footer_text())
    return embed


# ══════════════════════════════════════════════════════════════════════════
# STATS EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_stats_embed(stats: dict) -> discord.Embed:
    """Build premium stats dashboard embed with grade."""
    wr = stats.get("win_rate", 0)
    color = EMBED_COLOR_BUY if wr >= 50 else EMBED_COLOR_CAUTION if wr >= 40 else EMBED_COLOR_ERROR

    # Grade system (Detail 13)
    if wr > 55:
        grade = "A \u2705"
    elif wr >= 45:
        grade = "B \u2705"
    elif wr >= 35:
        grade = "C \u26A0\uFE0F"
    elif wr >= 25:
        grade = "D \u274C"
    else:
        grade = "F \u274C"

    embed = discord.Embed(title=f"Today's Performance \u2014 Grade **{grade}**", color=color)

    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    time_exits = stats.get("time_exits", 0)
    total = stats.get("total_trades", 0)
    avg_r = stats.get("avg_r", 0)
    total_r = stats.get("total_r", 0)
    pf = stats.get("profit_factor", 0)

    wr_e = "\u2705" if wr >= 50 else "\u26a0\ufe0f"
    ar_e = "\u2705" if avg_r > 0 else "\u274c"
    pf_e = "\u2705" if pf >= 1.5 else ("\u26a0\ufe0f" if pf >= 1.0 else "\u274c")

    record = f"**{wins}W / {losses}L"
    if time_exits:
        record += f" / {time_exits} TIME"
    record += "**"
    embed.add_field(name="Today's Record", value=record, inline=True)
    embed.add_field(name="Win Rate", value=f"`{wr:.1f}%` {wr_e}", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer
    embed.add_field(name="Avg R", value=f"`{avg_r:+.2f}R` {ar_e}", inline=True)
    embed.add_field(name="Total R", value=f"`{total_r:+.2f}R`", inline=True)
    embed.add_field(name="Profit Factor", value=f"`{pf:.2f}` {pf_e}", inline=True)

    # Strategy leaderboard
    strat_stats = stats.get("strategy_breakdown", {})
    if strat_stats:
        medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
        lines = []
        sorted_strats = sorted(strat_stats.items(),
                               key=lambda x: x[1].get("win_rate", 0), reverse=True)
        for idx, (sname, sdata) in enumerate(sorted_strats[:3]):
            medal = medals[idx] if idx < 3 else "\u2022"
            sw = sdata.get("wins", 0)
            sl = sdata.get("losses", 0)
            swr = sdata.get("win_rate", 0)
            sar = sdata.get("avg_r", 0)
            lines.append(f"{medal} **{sname}:** {sw}W/{sl}L  `{swr:.0f}%` WR  `{sar:+.2f}R` avg")
        if lines:
            embed.add_field(name="Strategy Leaderboard", value="\n".join(lines), inline=False)

    # Streaks
    streak = stats.get("streak_info", {})
    if streak:
        current = streak.get("current", 0)
        streak_label = f"{abs(current)} {'wins' if current > 0 else 'losses'}"
        if current > 0:
            streak_label += " \U0001f525"
        embed.add_field(name="Current Streak", value=streak_label, inline=True)

    # Best/worst trade
    best = stats.get("best_trade", {})
    worst = stats.get("worst_trade", {})
    if best:
        embed.add_field(
            name="Best Trade",
            value=f"${best.get('ticker', '?')} `{best.get('r_multiple', 0):+.2f}R` ({best.get('strategy', '')})",
            inline=True,
        )
    if worst:
        embed.add_field(
            name="Worst Trade",
            value=f"${worst.get('ticker', '?')} `{worst.get('r_multiple', 0):+.2f}R` ({worst.get('strategy', '')})",
            inline=True,
        )

    embed.set_footer(text=_footer_text())
    return embed


# ══════════════════════════════════════════════════════════════════════════
# HISTORY EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_history_embed(trades: list) -> discord.Embed:
    """Build trade history embed."""
    embed = discord.Embed(title="📜 Trade History", color=EMBED_COLOR_INFO)
    if not trades:
        embed.description = "No trades recorded yet."
        return embed

    for t in trades[:10]:
        result_emoji = "✅" if t.get("result") == "WIN" else "❌"
        ticker = t.get("ticker", "?")
        direction = t.get("direction", "?")
        r_mult = t.get("r_multiple", 0)
        strategy = t.get("strategy", "?")
        embed.add_field(
            name=f"{result_emoji} {ticker} {direction}",
            value=f"{r_mult:+.2f}R | {strategy}",
            inline=True,
        )
    if len(trades) > 10:
        embed.set_footer(text=f"Showing 10 of {len(trades)} trades")
    return embed


# ══════════════════════════════════════════════════════════════════════════
# WATCHLIST EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_watchlist_embed(watchlist_data: list) -> discord.Embed:
    """Build watchlist overview embed with tiered grouping."""
    embed = discord.Embed(title="👀 Watchlist", color=EMBED_COLOR_INFO)
    if not watchlist_data:
        embed.description = "No stocks in watchlist cache. Run a scan first."
        return embed

    # Sort by score descending (should already be sorted)
    data = sorted(watchlist_data, key=lambda x: x.get("score", 0), reverse=True)

    # Tier grouping
    high = []    # 85+
    watch = []   # 70-84
    weak = []    # 50-69
    low = []     # <50

    for item in data:
        score = item.get("score", 0)
        ticker = item.get("ticker", "?")
        change = item.get("change_pct", 0)
        strategy = item.get("strategy", "")
        if not strategy or strategy == "None":
            strategy = ""
        bar = emoji_confidence_bar(score)
        line = f"**{ticker}** ({change:+.1f}%) {bar}"
        if strategy:
            line += f"\n> Strategy: {strategy}"

        if score >= 85:
            high.append(line)
        elif score >= 70:
            watch.append(line)
        elif score >= 50:
            weak.append(line)
        else:
            low.append(line)

    parts = []
    if high:
        parts.append("**\U0001F525 HIGH CONFIDENCE (85+)**\n" + "\n".join(high[:8]))
    if watch:
        parts.append("**\u2705 WATCHING (70-84)**\n" + "\n".join(watch[:8]))
    if weak:
        parts.append("**\u26A0\uFE0F WEAK (50-69)**\n" + "\n".join(weak[:6]))
    if low and not high and not watch and not weak:
        parts.append("**\u26AA BELOW THRESHOLD (<50)**\n" + "\n".join(low[:5]))

    total_shown = len(high[:8]) + len(watch[:8]) + len(weak[:6])
    if not parts:
        parts.append("No stocks scored above 50. Market may be in a low-confidence state.")

    embed.description = "\n\n".join(parts)
    footer = f"Showing top {total_shown} of {len(data)} stocks"
    embed.set_footer(text=f"{footer} | {datetime.now(ET).strftime('%I:%M %p ET')}")
    return embed


# ══════════════════════════════════════════════════════════════════════════
# REGIME EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_regime_embed(regime_data: dict) -> discord.Embed:
    """Build regime status embed."""
    label = regime_data.get("label", "UNKNOWN")
    embed = discord.Embed(title=f"🏛️ Market Regime: {label}", color=EMBED_COLOR_INFO)

    embed.add_field(name="ADX", value=f"{regime_data.get('adx', 0):.1f}", inline=True)
    embed.add_field(name="ATR", value=f"{regime_data.get('atr', 0):.2f}", inline=True)
    embed.add_field(name="ATR Avg", value=f"{regime_data.get('atr_avg', 0):.2f}", inline=True)
    embed.add_field(name="EMA200 Slope", value=regime_data.get("ema200_slope", "N/A"), inline=True)

    strategies = regime_data.get("active_strategies", [])
    if strategies:
        embed.add_field(name="Active Strategies", value="\n".join(strategies), inline=False)

    history = regime_data.get("history", [])
    if history:
        hist_lines = [f"{h.get('time', '?')}: {h.get('label', '?')}" for h in history[-5:]]
        embed.add_field(name="Recent Changes", value="\n".join(hist_lines), inline=False)

    embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# RISK EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_risk_embed(ticker: str, position_data: dict) -> discord.Embed:
    """Build position sizing / risk embed."""
    embed = discord.Embed(
        title=f"💰 Position Sizing — ${ticker}",
        color=EMBED_COLOR_CAUTION,
    )
    embed.add_field(name="Account", value=f"${position_data.get('account_size', 0):,.0f}", inline=True)
    embed.add_field(name="Risk %", value=f"{position_data.get('risk_pct', 0):.1f}%", inline=True)
    embed.add_field(name="Max Risk $", value=f"${position_data.get('dollar_risk', 0):.2f}", inline=True)
    embed.add_field(name="Entry", value=f"${position_data.get('entry', 0):.2f}", inline=True)
    embed.add_field(name="Stop", value=f"${position_data.get('stop', 0):.2f}", inline=True)
    embed.add_field(name="Risk/Share", value=f"${position_data.get('risk_per_share', 0):.2f}", inline=True)
    embed.add_field(name="Shares", value=str(position_data.get("shares", 0)), inline=True)
    embed.add_field(name="Position $", value=f"${position_data.get('position_value', 0):,.2f}", inline=True)
    embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# INDICATORS EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_indicators_embed(ticker: str, indicators: dict,
                           plain_english: bool = False) -> discord.Embed:
    """Build full indicators breakdown embed."""
    embed = discord.Embed(title=f"📊 Indicators — ${ticker}", color=EMBED_COLOR_INFO)

    ind_keys = [
        ("rsi", "RSI"), ("macd", "MACD"), ("supertrend", "SuperTrend"),
        ("stochastic", "Stochastic"), ("ema_stack", "EMA Stack"),
        ("vwap", "VWAP"), ("adx", "ADX"), ("atr", "ATR"),
        ("bollinger_bands", "Bollinger"), ("rvol", "RVOL"),
        ("williams_r", "Williams %R"), ("obv", "OBV"),
    ]

    for key, label in ind_keys:
        ind = indicators.get(key, {})
        if not ind:
            ind = indicators.get(key.replace("_bands", ""), {})

        val = ind.get("value", ind.get("k", ind.get("rvol", ind.get("vwap", "N/A"))))
        bullish = ind.get("bullish", False)
        bearish = ind.get("bearish", False)
        emoji = "✅" if bullish else ("❌" if bearish else "⚪")

        if plain_english and key == "rsi":
            text = plain_english_indicator("rsi", val)
        elif plain_english and key == "macd":
            text = plain_english_indicator("macd_bull" if bullish else "macd_bear")
        elif plain_english and key == "rvol":
            text = plain_english_indicator("rvol", val)
        elif plain_english and key == "adx":
            text = plain_english_indicator("adx", val)
        else:
            if isinstance(val, float):
                text = f"{val:.2f}"
            else:
                text = str(val)

        embed.add_field(name=f"{emoji} {label}", value=text, inline=True)

    embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# HELP EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_help_embed() -> discord.Embed:
    """Build the /help embed."""
    embed = discord.Embed(title="📖 Bot Commands", color=EMBED_COLOR_INFO)
    commands = [
        ("/scan [ticker]", "Full analysis of a stock"),
        ("/best", "Top 5 stocks right now"),
        ("/market", "Market overview with regime and breadth"),
        ("/news [ticker]", "Latest headlines and sentiment"),
        ("/price [ticker]", "Quick price check"),
        ("/watchlist", "View watchlist rankings"),
        ("/stats", "Paper trading statistics"),
        ("/regime", "Current market regime"),
        ("/risk [ticker] [account] [risk%]", "Position sizing calculator"),
        ("/indicators [ticker]", "Full indicator breakdown"),
        ("/tutorial", "8-part bot tutorial"),
        ("/test", "System health check"),
        ("/backtest [ticker] [days]", "Historical backtest"),
        ("/replay [ticker] [date]", "Replay a trading day"),
        ("/goodday", "Find the best trading day"),
        ("/simulate [ticker]", "Simulate a strategy"),
        ("/mockmarket", "Toggle mock market mode"),
        ("/history", "Recent trade history"),
        ("/alert [ticker] [price] [direction]", "Set a price alert"),
        ("/myalerts", "View your price alerts"),
        ("/cancelalert [ticker]", "Cancel a price alert"),
        ("/quiet", "Toggle quiet mode (suppress auto-alerts)"),
        ("/plainenglish", "Toggle plain English mode"),
        ("/ping", "Bot latency check"),
        ("/help", "This help message"),
    ]
    for cmd, desc in commands:
        embed.add_field(name=cmd, value=desc, inline=True)
    return embed


# ══════════════════════════════════════════════════════════════════════════
# PING EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_ping_embed(latency: float, bot_data: dict = None) -> discord.Embed:
    """Build enhanced ping response embed."""
    bot_data = bot_data or {}
    lat_emoji = "\u2705" if latency < 100 else "\u26A0\uFE0F" if latency < 200 else "\u274C"
    embed = discord.Embed(title="\U0001f3d3 Pong!", color=EMBED_COLOR_INFO)
    embed.add_field(name="Bot Latency", value=f"`{latency:.0f}ms` {lat_emoji}", inline=True)

    # Status fields
    scanner_ok = bot_data.get("scanner_running", True)
    channel_ok = bot_data.get("channel_ok", True)
    mock_active = bot_data.get("mock_market", False)
    from utils import is_market_open
    if is_market_open():
        market_str = "\U0001f4c8 Open"
    else:
        now = datetime.now(ET)
        market_str = f"\U0001f319 Closed ({now.strftime('%A')})"

    status_lines = [
        f"Scanner: {'\u2705 Running' if scanner_ok else '\u274C Stopped'}",
        f"Alert Channel: {'\u2705 Connected' if channel_ok else '\u274C Disconnected'}",
        f"Market: {market_str}",
        f"Mock Market: {'\u2705 Active' if mock_active else '\u274C Inactive'}",
    ]
    embed.add_field(name="Status", value="\n".join(status_lines), inline=False)

    # Today's stats
    alerts_today = bot_data.get("alerts_today", 0)
    scans_run = bot_data.get("scans_run", 0)
    if alerts_today or scans_run:
        today_lines = [f"Alerts Sent: `{alerts_today}`"]
        if scans_run:
            today_lines.append(f"Scans Run: `{scans_run}`")
        embed.add_field(name="Today", value="\n".join(today_lines), inline=True)

    embed.set_footer(text=_footer_text())
    return embed


# ══════════════════════════════════════════════════════════════════════════
# TIP OF THE DAY EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_tip_embed(tip_text: str, tip_number: int) -> discord.Embed:
    """Build the tip-of-the-day embed."""
    embed = discord.Embed(
        title=f"💡 Tip of the Day #{tip_number}",
        description=tip_text,
        color=EMBED_COLOR_CAUTION,
    )
    embed.set_footer(text=datetime.now(ET).strftime("%A, %B %d"))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# MORNING SUMMARY EMBED  (pinned card)
# ══════════════════════════════════════════════════════════════════════════

def build_morning_summary_embed(spy_price: float, spy_change: float,
                                vix_val: float, regime_label: str,
                                gap_ups: list) -> discord.Embed:
    """Build the pinned morning summary embed."""
    embed = discord.Embed(
        title=f"☀️ Morning Summary — {datetime.now(ET).strftime('%A %b %d')}",
        color=EMBED_COLOR_BUY if spy_change >= 0 else EMBED_COLOR_CAUTION,
    )
    embed.add_field(name="Market Opens In", value="~15 minutes", inline=True)
    embed.add_field(name="SPY Pre-Market", value=f"${spy_price:.2f} ({spy_change:+.2f}%)", inline=True)
    embed.add_field(name="VIX", value=f"{vix_val:.1f}" if vix_val > 0 else "N/A", inline=True)
    embed.add_field(name="Regime", value=regime_label, inline=True)

    if gap_ups:
        gap_str = ", ".join([f"${g['ticker']} ({g['change']:+.1f}%)" for g in gap_ups[:3]])
        embed.add_field(name="Top Gap-Ups", value=gap_str, inline=False)

    bias = "BULLISH" if spy_change >= 0 else "BEARISH"
    embed.add_field(
        name="Market Bias",
        value=f"Futures suggest a **{bias}** open. Watch for ORB setups in the first 30 minutes.",
        inline=False,
    )
    embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# PRICE ALERT TRIGGER EMBED
# ══════════════════════════════════════════════════════════════════════════

def build_price_alert_embed(ticker: str, current_price: float,
                            target_price: float, direction: str) -> discord.Embed:
    """Build the price alert trigger embed."""
    color = EMBED_COLOR_BUY if direction.lower() == "above" else EMBED_COLOR_CAUTION
    embed = discord.Embed(
        title=f"⚡ PRICE ALERT: ${ticker}",
        description=(
            f"**{ticker}** has reached **${current_price:.2f}**\n"
            f"Your target was **${target_price:.2f}** ({direction})"
        ),
        color=color,
    )
    embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
    return embed


# ══════════════════════════════════════════════════════════════════════════
# ERROR / GENERIC EMBEDS
# ══════════════════════════════════════════════════════════════════════════

def error_embed(message: str) -> discord.Embed:
    """Friendly error embed — never shows raw Python tracebacks."""
    # Strip technical details for user-facing message
    clean = str(message)
    if "Error" in clean and "line" in clean:
        clean = "Could not complete this request. Try again in a few minutes or use a different stock."
    if "KeyError" in clean or "IndexError" in clean or "TypeError" in clean:
        clean = "Could not fetch data for this ticker right now. This usually means the market is between sessions or the ticker has limited data."
    embed = discord.Embed(
        title="\u26a0\ufe0f Something went wrong",
        description=clean,
        color=EMBED_COLOR_ERROR,
    )
    embed.set_footer(text=_footer_text())
    return embed


def info_embed(title: str, description: str = "") -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=EMBED_COLOR_INFO)
    embed.set_footer(text=_footer_text())
    return embed


# ══════════════════════════════════════════════════════════════════════════
# MARKET OPEN / CLOSE ANNOUNCEMENT EMBEDS
# ══════════════════════════════════════════════════════════════════════════

def build_market_open_embed() -> discord.Embed:
    """Daily market open announcement at 9:30 AM ET."""
    now = datetime.now(ET)
    day_name = now.strftime("%A, %B %d %Y")

    embed = discord.Embed(
        title="Market Open \u2014 Good Morning",
        color=EMBED_COLOR_STRONG_TREND,
    )
    embed.add_field(name="Date", value=f"**{day_name}**", inline=False)
    embed.add_field(
        name="Today's Windows",
        value=(
            "ORB Window: `9:30 \u2014 9:45 AM`\n"
            "Best Window: `9:45 \u2014 11:30 AM`\n"
            "Midday Quiet: `11:30 AM \u2014 3:00 PM` (alerts paused)\n"
            "Power Hour: `3:00 \u2014 4:00 PM`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Status",
        value="Market regime: Calculating...\nFirst scan completes in 60 seconds.",
        inline=False,
    )
    embed.set_footer(text=_footer_text())
    return embed


def build_market_close_embed(alerts_today: int = 0, win_rate: float = 0,
                             best_ticker: str = "", best_pct: float = 0) -> discord.Embed:
    """Daily market close announcement at 4:00 PM ET."""
    embed = discord.Embed(
        title="Market Closed",
        color=EMBED_COLOR_MARKET_CLOSED,
    )
    lines = []
    lines.append(f"Alerts sent today: **{alerts_today}**")
    if alerts_today > 0:
        lines.append(f"Win rate today: **{win_rate:.1f}%**")
    if best_ticker:
        lines.append(f"Best performer: **${best_ticker}** `{best_pct:+.1f}%`")
    lines.append(f"\nNext open: Tomorrow 9:30 AM ET")
    lines.append("Trade scans resume during regular market hours.")
    embed.description = "\n".join(lines)
    embed.set_footer(text=_footer_text())
    return embed


def build_midday_quiet_embed() -> discord.Embed:
    """Midday quiet hours notification."""
    embed = discord.Embed(
        title="Midday Quiet Hours \u2014 11:30 AM to 3:00 PM",
        description=(
            "Scanning continues but alerts are paused.\n"
            "Midday signals have historically lower win rates.\n"
            "Alerts resume at **3:00 PM ET**."
        ),
        color=EMBED_COLOR_CAUTION,
    )
    embed.set_footer(text=_footer_text())
    return embed


# ══════════════════════════════════════════════════════════════════════════
# CONTEXT LABEL HELPERS (Detail 1-7)
# ══════════════════════════════════════════════════════════════════════════

def fmt_price(val) -> str:
    """Format a price with $ and 2 decimal places."""
    try:
        return f"${float(val):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def fmt_pct(val, plus: bool = True) -> str:
    """Format a percentage with 1 decimal place."""
    try:
        v = float(val)
        sign = "+" if plus and v > 0 else ""
        return f"{sign}{v:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def fmt_r(val) -> str:
    """Format an R-multiple with 2 decimal places."""
    try:
        v = float(val)
        return f"{v:+.2f}R"
    except (TypeError, ValueError):
        return "0.00R"


def pnl_emoji(val) -> str:
    """Return emoji for positive/negative values."""
    try:
        v = float(val)
        if v > 0:
            return "\u2705"
        elif v < 0:
            return "\u274C"
        return "\u26AA"
    except (TypeError, ValueError):
        return "\u26AA"


def rsi_context(val) -> str:
    """RSI value with context label (Detail 4)."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "`—` \u26AA"
    v_str = f"`{v:.1f}`"
    if v < 30:
        return f"{v_str} \U0001f4c9 Oversold"
    if v < 45:
        return f"{v_str} \u2B07\uFE0F Below average"
    if v < 55:
        return f"{v_str} \u27A1\uFE0F Neutral"
    if v < 65:
        return f"{v_str} \u2B06\uFE0F Healthy momentum"
    if v < 75:
        return f"{v_str} \U0001f525 Strong momentum"
    return f"{v_str} \u26A0\uFE0F Approaching overbought"


def rvol_context(val) -> str:
    """RVOL value with context label (Detail 3)."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "`—` \u26AA"
    v_str = f"`{v:.1f}x`"
    if v >= 5.0:
        return f"{v_str} \U0001f525\U0001f525\U0001f525 Exceptional volume"
    if v >= 3.0:
        return f"{v_str} \U0001f525\U0001f525 Very high volume"
    if v >= 2.0:
        return f"{v_str} \U0001f525 High volume"
    if v >= 1.5:
        return f"{v_str} \U0001f4c8 Above average"
    return f"{v_str} Normal volume"


def vwap_context(pct_from_vwap) -> str:
    """VWAP distance with context label (Detail 5)."""
    try:
        v = float(pct_from_vwap)
    except (TypeError, ValueError):
        return "\u26AA N/A"
    v_str = f"`{v:+.1f}%`"
    if v > 3.0:
        return f"{v_str} \U0001f525 Extended above VWAP"
    if v > 1.0:
        return f"{v_str} \u2705 Above VWAP"
    if v > 0:
        return f"{v_str} \u26AA Just above VWAP"
    if v > -1.0:
        return f"{v_str} \u26A0\uFE0F Just below VWAP"
    if v > -3.0:
        return f"{v_str} \u274C Below VWAP"
    return f"{v_str} \U0001f480 Far below VWAP"


def market_state_footer(state: str = "") -> str:
    """Market state emoji for footer (Detail 7)."""
    s = state.upper() if state else ""
    if "STRONG" in s:
        return "\U0001f4c8 Strong Trend"
    if "WEAK" in s:
        return "\U0001f4ca Weak Trend"
    if "RANG" in s:
        return "\u2194\uFE0F Ranging"
    if "HIGH" in s or "VOL" in s:
        return "\u26A1 High Volatility"
    return "Market Closed"


def _divider() -> str:
    """Visual section divider for embeds (Detail 20)."""
    return "\u25AC" * 20


# ══════════════════════════════════════════════════════════════════════════
# BACKTEST EMBED BUILDER (3 embeds)
# ══════════════════════════════════════════════════════════════════════════

def _backtest_grade(pf: float, wr: float) -> tuple:
    """Return (letter, label, color) for backtest grade."""
    if pf > 2.0 and wr > 55:
        return "A", "\U0001f3c6 Grade A", 0x00D26A
    if pf > 1.5 and wr > 45:
        return "B", "\u2705 Grade B", 0x57F287
    if pf > 1.2 and wr > 38:
        return "C", "\u26A0\uFE0F Grade C", 0xFEE75C
    if pf > 1.0 and wr > 30:
        return "D", "\U0001f4c9 Grade D", 0xFF9900
    return "F", "\u274C Grade F", 0xED4245


def build_backtest_embeds(results: dict) -> list:
    """Build 3 Discord embeds for a backtest report."""
    ticker = results.get("ticker", "?")
    days = results.get("days", "?")
    interval = results.get("interval", "")
    tc = results.get("trade_count", 0)
    wins = results.get("wins", 0)
    losses = results.get("losses", 0)
    wr = results.get("win_rate", 0)
    avg_r = results.get("avg_r", 0)
    total_r = results.get("total_r", 0)
    pf = results.get("profit_factor", 0)

    # Error case
    if results.get("error"):
        embed = discord.Embed(
            title=f"\U0001f4ca Backtest Report \u2014 ${ticker}",
            description=f"Error: {results['error']}",
            color=EMBED_COLOR_ERROR,
        )
        embed.set_footer(text=_footer_text())
        return [embed]

    letter, grade_label, grade_color = _backtest_grade(pf, wr)
    candle_desc = f"`{days}d` \u2022 `{interval} candles`" if interval else f"`{days} days`"
    strat = results.get("strategy_filter")
    if strat:
        candle_desc += f" \u2022 `{strat}`"
    candle_desc += " \u2022 `BUY signals only`"

    # ── EMBED 1: HEADER ──────────────────────────────────────────
    e1 = discord.Embed(
        title=f"\U0001f4ca Backtest Report \u2014 ${ticker}",
        description=candle_desc,
        color=grade_color,
    )
    wr_emoji = "\u2705" if wr >= 50 else "\u274C"
    r_emoji = "\u2705" if avg_r > 0 else "\u274C"
    e1.add_field(name="Trades", value=f"`{tc}` ({wins}W / {losses}L)", inline=True)
    e1.add_field(name="Win Rate", value=f"`{wr:.1f}%` {wr_emoji}", inline=True)
    e1.add_field(name="Avg R", value=f"`{avg_r:+.2f}R` {r_emoji}", inline=True)
    e1.add_field(name="Total R", value=f"`{total_r:+.2f}R`", inline=True)
    e1.add_field(name="Profit Factor", value=f"`{pf:.2f}`", inline=True)
    e1.add_field(name="Grade", value=f"**{grade_label}**", inline=True)
    e1.set_footer(text=_footer_text())

    # ── EMBED 2: SIGNAL ANALYSIS ─────────────────────────────────
    e2 = discord.Embed(
        title="\U0001f50d Signal Analysis",
        color=EMBED_COLOR_INFO,
    )

    fs = results.get("filter_stats", {})
    if fs and fs.get("raw_signals", 0) > 0:
        raw = fs.get("raw_signals", 0)
        fms = fs.get("failed_market_state", 0)
        frsi = fs.get("failed_rsi_gate", 0)
        fadx = fs.get("failed_adx_gate", 0)
        frr = fs.get("failed_rr_gate", 0)
        fsf = fs.get("failed_strategy_filter", 0)
        funnel_lines = [f"Raw signals detected: `{raw}`"]
        if fms > 0:
            funnel_lines.append(f"\u251C\u2500 Removed by state: `{fms}`")
        if frsi > 0:
            funnel_lines.append(f"\u251C\u2500 Removed by RSI gate: `{frsi}`")
        if fadx > 0:
            funnel_lines.append(f"\u251C\u2500 Removed by ADX gate: `{fadx}`")
        if frr > 0:
            funnel_lines.append(f"\u251C\u2500 Removed by R:R gate: `{frr}`")
        if fsf > 0:
            funnel_lines.append(f"\u251C\u2500 Removed by strategy: `{fsf}`")
        funnel_lines.append(f"\u2514\u2500 **Final trades: `{tc}`**")
        e2.add_field(name="Signal Funnel", value="\n".join(funnel_lines), inline=False)

    # By Market State
    mkt = results.get("market_condition_summary", {})
    state_labels = {"STRONG_TREND": "Strong Trend", "WEAK_TREND": "Weak Trend",
                    "RANGING": "Ranging", "HIGH_VOLATILITY": "High Vol"}
    if mkt:
        mkt_lines = []
        for ms in ["STRONG_TREND", "WEAK_TREND", "RANGING", "HIGH_VOLATILITY"]:
            data = mkt.get(ms)
            if data:
                label = state_labels.get(ms, ms)
                mkt_lines.append(f"{label}: `{data['count']} trades` \u2022 `{data['win_rate']:.0f}% WR`")
            elif ms in ("RANGING", "HIGH_VOLATILITY"):
                label = state_labels.get(ms, ms)
                mkt_lines.append(f"{label}: `0` (paused)")
        if mkt_lines:
            e2.add_field(name="By Market State", value="\n".join(mkt_lines), inline=True)

    # By Strategy
    strat_lines = []
    if results.get("trend_count"):
        strat_lines.append(
            f"Trend: `{results['trend_count']} trades` \u2022 "
            f"`{results.get('trend_win_rate', 0):.0f}% WR` \u2022 "
            f"`{results.get('trend_avg_r', 0):+.2f}R avg`")
    if results.get("mr_count"):
        strat_lines.append(
            f"Mean Rev: `{results['mr_count']} trades` \u2022 "
            f"`{results.get('mr_win_rate', 0):.0f}% WR` \u2022 "
            f"`{results.get('mr_avg_r', 0):+.2f}R avg`")
    if strat_lines:
        e2.add_field(name="By Strategy", value="\n".join(strat_lines), inline=True)

    e2.set_footer(text=_footer_text())

    # ── EMBED 3: TRADES & VERDICT ────────────────────────────────
    e3 = discord.Embed(
        title="\U0001f4cb Trade Log & Verdict",
        color=grade_color,
    )

    best = results.get("best_trades", [])
    if best:
        best_lines = []
        medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
        for idx, t in enumerate(best[:3]):
            m = medals[idx] if idx < 3 else "\u2022"
            reason = t.get("reason", "")
            reason_emoji = {"TP1": "\U0001f3af", "TP2": "\U0001f3af\U0001f3af",
                            "TP3": "\U0001f680", "TP": "\U0001f3af",
                            "SL": "\u274C", "TIME": "\u23F1", "EOD": "\U0001f319"}.get(reason, "")
            best_lines.append(f"{m} `{t['r_multiple']:+.2f}R` {t['strategy']} \u2014 {reason} {reason_emoji}")
        e3.add_field(name="Best Trades", value="\n".join(best_lines), inline=False)

    worst = results.get("worst_trades", [])
    if worst:
        worst_lines = []
        for t in worst[:3]:
            reason = t.get("reason", "")
            reason_emoji = {"SL": "\u274C", "TIME": "\u23F1", "EOD": "\U0001f319",
                            "TP1": "\U0001f3af"}.get(reason, "")
            worst_lines.append(f"\U0001f480 `{t['r_multiple']:+.2f}R` {t['strategy']} \u2014 {reason} {reason_emoji}")
        e3.add_field(name="Worst Trades", value="\n".join(worst_lines), inline=False)

    # Verdict
    if tc == 0:
        verdict = (
            "\u274C No trades found in this period.\n\n"
            "Suggestions:\n"
            f"\u2022 Try a longer period: `/backtest {ticker} 30`\n"
            "\u2022 Try a different stock: `/backtest AAPL 7`\n"
            "\u2022 Check market conditions: `/regime`"
        )
    elif tc < 5:
        verdict = (
            f"\u26A0\uFE0F Only `{tc}` trades \u2014 not statistically significant.\n"
            f"Try `/backtest {ticker} 30` for more reliable results."
        )
    elif letter == "A":
        verdict = "\u2705 **Strong edge detected.** This stock suits the current strategies well."
    elif letter == "B":
        verdict = f"\u2705 Positive expectancy. Run `/wfo {ticker}` to validate consistency."
    elif letter == "F":
        verdict = (
            f"\u274C This stock did not perform well over this period.\n\n"
            f"Suggestions:\n"
            f"\u2022 Try a longer period: `/backtest {ticker} 30`\n"
            f"\u2022 Try a different stock: `/backtest AAPL 7`\n"
            f"\u2022 Check market conditions: `/regime`"
        )
    else:
        verdict = f"Mixed results. Run `/wfo {ticker}` for deeper analysis."
    e3.add_field(name="Verdict", value=verdict, inline=False)

    # Permutation test
    perm = results.get("permutation", {})
    if perm and not perm.get("error"):
        real_pf = perm.get("real_pf", 0)
        avg_rand = perm.get("avg_random_pf", 0)
        p_val = perm.get("p_value", 1)
        conf = perm.get("confidence", 0)
        label = perm.get("label", "")
        perm_text = (
            f"Real PF: `{real_pf:.2f}` vs Random PF: `{avg_rand:.2f}`\n"
            f"p-value: `{p_val:.2f}` \u2192 **{label}**\n"
            f"Confidence: `{conf:.0f}%` better than random"
        )
        e3.add_field(name="Permutation Test", value=perm_text, inline=False)
    elif perm and perm.get("error"):
        e3.add_field(
            name="Permutation Test",
            value=f"\u26A0\uFE0F {perm['error']}",
            inline=False,
        )

    e3.set_footer(text=_footer_text())

    return [e1, e2, e3]


# ══════════════════════════════════════════════════════════════════════════
# TRADE PLAN EMBED (for /scan Trade Plan button)
# ══════════════════════════════════════════════════════════════════════════

def build_trade_plan_embed(ticker: str, entry_price: float, stop: float,
                           atr: float, indicators: dict = None,
                           strategy: str = "", regime: str = "") -> discord.Embed:
    """Build detailed trade plan with SL, TP, time estimates, and position sizing."""
    indicators = indicators or {}
    entry = float(entry_price) if entry_price else 0
    stop_val = float(stop) if stop else entry * 0.99
    atr_val = float(atr) if atr else entry * 0.01

    # Calculate levels
    risk_per_share = abs(entry - stop_val)
    tp1 = entry + 1.5 * atr_val
    tp2 = entry + 2.5 * atr_val
    tp3 = entry + 4.0 * atr_val

    rr = round((tp1 - entry) / risk_per_share, 2) if risk_per_share > 0 else 0

    embed = discord.Embed(
        title=f"\U0001f4cb Trade Plan \u2014 ${ticker}",
        color=EMBED_COLOR_BUY,
    )

    # Entry Plan
    entry_low = entry - atr_val * 0.1
    entry_high = entry + atr_val * 0.1
    embed.add_field(
        name="Entry Plan",
        value=(
            f"Entry Zone: `{fmt_price(entry_low)} \u2014 {fmt_price(entry_high)}`\n"
            f"Best Entry: `{fmt_price(entry)}` (current)\n"
            f"Entry Type: Buy on confirmation candle"
        ),
        inline=False,
    )

    # Levels
    sl_pct = ((stop_val - entry) / entry * 100) if entry else 0
    tp1_pct = ((tp1 - entry) / entry * 100) if entry else 0
    tp2_pct = ((tp2 - entry) / entry * 100) if entry else 0
    tp3_pct = ((tp3 - entry) / entry * 100) if entry else 0
    sl_dollar = stop_val - entry
    tp1_dollar = tp1 - entry
    tp2_dollar = tp2 - entry
    tp3_dollar = tp3 - entry

    levels_text = (
        f"\U0001f6d1 Stop Loss: `{fmt_price(stop_val)}` `{sl_pct:+.1f}%` `{fmt_price(sl_dollar)}/share`\n"
        f"\U0001f3af TP1: `{fmt_price(tp1)}` `{tp1_pct:+.1f}%` `+{fmt_price(tp1_dollar)}/share` (1.5R)\n"
        f"\U0001f3af\U0001f3af TP2: `{fmt_price(tp2)}` `{tp2_pct:+.1f}%` `+{fmt_price(tp2_dollar)}/share` (2.5R)\n"
        f"\U0001f680 TP3: `{fmt_price(tp3)}` `{tp3_pct:+.1f}%` `+{fmt_price(tp3_dollar)}/share` (4.0R)\n"
        f"R:R Ratio: `{rr:.2f}:1`"
    )
    embed.add_field(name="Levels", value=levels_text, inline=False)

    # Time estimates (based on ATR per hour approximation)
    hourly_move = atr_val  # ATR approximates hourly range for 5m candles * 12
    if hourly_move > 0 and entry > 0:
        def _est_time(target_price):
            dist = abs(target_price - entry)
            hours = dist / hourly_move if hourly_move > 0 else 0
            mins = int(hours * 60)
            if mins < 60:
                return f"~`{mins} min`"
            return f"~`{hours:.1f} hr`"

        time_text = (
            f"To Stop Loss: {_est_time(stop_val)}\n"
            f"To TP1: {_est_time(tp1)}\n"
            f"To TP2: {_est_time(tp2)}\n"
            f"To TP3: {_est_time(tp3)}\n"
            f"\n_Based on ATR of `{fmt_price(atr_val)}/hr`. Strong momentum may hit targets faster._"
        )
        embed.add_field(name="\u23F1 Time Estimates", value=time_text, inline=False)

    # Position sizing for $1,000 at 1% risk
    account = 1000
    risk_budget = account * 0.01
    shares = max(1, int(risk_budget / risk_per_share)) if risk_per_share > 0 else 1
    pos_value = shares * entry
    profit_tp1 = shares * tp1_dollar
    profit_tp2 = shares * tp2_dollar
    profit_tp3 = shares * tp3_dollar

    sizing_text = (
        f"For $1,000 account at 1% risk:\n"
        f"Shares: `{shares}`\n"
        f"Position Value: `{fmt_price(pos_value)}`\n"
        f"Max Risk: `{fmt_price(risk_budget)}`\n"
        f"Profit at TP1: `{fmt_price(profit_tp1)}`\n"
        f"Profit at TP2: `{fmt_price(profit_tp2)}`\n"
        f"Profit at TP3: `{fmt_price(profit_tp3)}`"
    )
    embed.add_field(name="Position Sizing", value=sizing_text, inline=False)

    # Context
    rsi_val = indicators.get("rsi", {}).get("value", 0)
    vwap_pct = indicators.get("vwap", {}).get("distance_pct", 0)
    ctx_lines = []
    if strategy:
        ctx_lines.append(f"Strategy: {strategy}")
    if rsi_val:
        ctx_lines.append(f"RSI: {rsi_context(rsi_val)}")
    if vwap_pct:
        ctx_lines.append(f"VWAP: {vwap_context(vwap_pct)}")
    if regime:
        ctx_lines.append(f"Market: {market_state_footer(regime)}")
    if ctx_lines:
        embed.add_field(name="Context", value="\n".join(ctx_lines), inline=False)

    embed.set_footer(text=_footer_text())
    return embed
