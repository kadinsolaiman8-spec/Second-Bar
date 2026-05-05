"""
Build market recap message/embed for Discord.
"""

from src.market_hours import get_current_et
from src.signals import Signal
from src.stock import format_signal_breakdown_line


def format_recap_embed(
    signals: list[Signal],
    include_hold: bool = False,
    min_confidence: int = 0,
    index_name: str | None = None,
    ignore_volatility: bool = False,
    include_rsi_in_hold: bool = True,
    timeframe: str = "Daily",
    news_labels: dict[str, str] | None = None,
    expert_labels: dict[str, str] | None = None,
    show_breakdown: bool = False,
) -> dict:
    """
    Build a Discord embed for the market recap.
    By default only includes Buy and Sell signals (omit Hold for conciseness).
    Only shows signals with confidence >= min_confidence.
    index_name: If set, used in title and footer (e.g. "S&P 500"); otherwise "S&P 100".
    ignore_volatility: When True, footer omits ATR and Vol % is hidden in signal lines.
    include_rsi_in_hold: When False, HOLD section shows price instead of RSI.
    """
    now_et = get_current_et()
    timestamp_str = now_et.strftime("%Y-%m-%d %I:%M %p ET")
    label = index_name if index_name else "S&P 100"

    filtered = [s for s in signals if s.signal_type != "Hold"] if not include_hold else signals
    filtered = [s for s in filtered if s.confidence >= min_confidence]

    # Sort: Buy first (by confidence desc), then Sell (by confidence desc)
    buys = sorted([s for s in filtered if s.signal_type == "Buy"], key=lambda x: (-x.confidence, x.symbol))
    sells = sorted([s for s in filtered if s.signal_type == "Sell"], key=lambda x: (-x.confidence, x.symbol))
    holds = [s for s in filtered if s.signal_type == "Hold"] if include_hold else []

    news_labels = news_labels or {}
    expert_labels = expert_labels or {}
    lines = []
    if buys:
        lines.append("**BUY**")
        for s in buys[:15]:  # Top 15 buys
            line = f"`{s.symbol:6}` | Confidence: {s.confidence}/100 | ${s.price:.2f}"
            if not ignore_volatility:
                line += f" | Vol: {s.atr_pct:.1f}%"
            if s.stop_pct is not None:
                line += f" | Stop: {s.stop_pct:+.0f}%"
            if s.symbol in news_labels:
                line += f" | News: {news_labels[s.symbol]}"
            if s.symbol in expert_labels:
                line += f" | Expert: {expert_labels[s.symbol]}"
            lines.append(line)
            if show_breakdown:
                breakdown = format_signal_breakdown_line(s.weighted_scores, s.signal_type)
                if breakdown:
                    lines.append(f"  → {breakdown}")
        if len(buys) > 15:
            lines.append(f"_...and {len(buys) - 15} more_")
        lines.append("")

    if sells:
        lines.append("**SELL**")
        for s in sells[:15]:  # Top 15 sells
            line = f"`{s.symbol:6}` | Confidence: {s.confidence}/100 | ${s.price:.2f}"
            if not ignore_volatility:
                line += f" | Vol: {s.atr_pct:.1f}%"
            if s.stop_pct is not None:
                line += f" | Stop: {s.stop_pct:+.0f}%"
            if s.symbol in news_labels:
                line += f" | News: {news_labels[s.symbol]}"
            if s.symbol in expert_labels:
                line += f" | Expert: {expert_labels[s.symbol]}"
            lines.append(line)
            if show_breakdown:
                breakdown = format_signal_breakdown_line(s.weighted_scores, s.signal_type)
                if breakdown:
                    lines.append(f"  → {breakdown}")
        if len(sells) > 15:
            lines.append(f"_...and {len(sells) - 15} more_")
        lines.append("")

    if include_hold and holds:
        lines.append("**HOLD** (no strong signal)")
        for s in holds[:5]:
            if include_rsi_in_hold:
                lines.append(f"`{s.symbol:6}` | RSI: {s.rsi:.1f}")
            else:
                lines.append(f"`{s.symbol:6}` | ${s.price:.2f}")
        if len(holds) > 5:
            lines.append(f"_...and {len(holds) - 5} more_")

    body = "\n".join(lines).strip() if lines else "_No strong Buy or Sell signals at this time._"

    footer_indicators = "RSI, MACD, BB, SuperTrend, Stoch, WillR, EMA, VWAP, OBV, CMF"
    if not ignore_volatility:
        footer_indicators += ", ATR"
    if news_labels:
        footer_indicators += ", News (high-vol)"
    if expert_labels:
        footer_indicators += ", Expert"
    # Show regime from first signal (shared VIX across all symbols)
    regime_label = ""
    for s in signals:
        if s.regime:
            regime_label = f" | Regime: {s.regime}"
            break
    footer_text = f"{label} | {footer_indicators} | {timeframe}{regime_label}"
    embed = {
        "title": f"Market Recap - {label} - {timestamp_str}",
        "description": body,
        "color": 0x00AA00 if buys and not sells else (0xAA0000 if sells and not buys else 0x808080),
        "footer": {"text": footer_text},
    }
    return embed


def build_recap_content(
    signals: list[Signal],
    include_hold: bool = False,
    min_confidence: int = 0,
) -> str:
    """Build plain text recap (fallback)."""
    filtered = [s for s in signals if s.signal_type != "Hold"] if not include_hold else signals
    filtered = [s for s in filtered if s.confidence >= min_confidence]
    if not filtered:
        return "No strong Buy or Sell signals at this time."

    lines = [f"{s.symbol:6} | {s.signal_type:4} | Confidence: {s.confidence}/100" for s in filtered]
    return "```\n" + "\n".join(lines) + "\n```"
