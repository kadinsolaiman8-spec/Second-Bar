"""
Stock quote embed: user-friendly rundown with bullet points and one-line summary.
Uses shared indicator_scores for weighted net score and consensus display.
"""

from src.indicator_scores import WeightedScores, compute_weighted_scores
from src.market_hours import get_current_et
from src.news import format_headlines_for_embed
from src.signals import Signal


def _build_summary_line(
    signal: Signal | None,
    buy_count: int,
    sell_count: int,
    checkmarks: list[str],
    total: int = 7,
) -> str:
    """Build one-line summary. Avoids implying non-buy indicators = sell (they may be neutral)."""
    if buy_count > sell_count:
        key_points = []
        if any("oversold" in c for c in checkmarks):
            key_points.append("momentum is oversold and could bounce")
        if any("support" in c for c in checkmarks):
            key_points.append("price is near support")
        if any("bullish" in c.lower() or "trend up" in c.lower() for c in checkmarks):
            key_points.append("the trend is bullish")
        points = ", ".join(key_points) if key_points else "indicators lean bullish"
        if sell_count > 0:
            return f"{buy_count} suggest buying, {sell_count} suggest selling. {points.capitalize()}."
        return f"{buy_count} indicators suggest buying. {points.capitalize()}."
    elif sell_count > buy_count:
        key_points = []
        if any("overbought" in c for c in checkmarks):
            key_points.append("momentum is overbought and may pull back")
        if any("resistance" in c for c in checkmarks):
            key_points.append("price is near resistance")
        if any("bearish" in c.lower() or "trend down" in c.lower() for c in checkmarks):
            key_points.append("the trend is bearish")
        points = ", ".join(key_points) if key_points else "indicators lean bearish"
        if buy_count > 0:
            return f"{sell_count} suggest selling, {buy_count} suggest buying. {points.capitalize()}."
        return f"{sell_count} indicators suggest selling. {points.capitalize()}."
    else:
        return "Indicators are mixed. No strong buy or sell signal."


# Friendly names for indicator labels in breakdown
_LABEL_NAMES = {
    "RSI": "RSI",
    "BB": "Bollinger Bands",
    "Trend": "Trend",
    "Stoch": "Stochastic",
    "WillR": "Williams %R",
    "EMA": "EMA",
    "Vol": "Volatility",
    "News": "News",
    "Expert": "Expert",
}


def _split_checkmarks_by_side(checkmarks: list[str]) -> tuple[list[str], list[str]]:
    """Split checkmarks into sell-supporting and buy-supporting based on keywords."""
    sell_keywords = ("overbought", "resistance", "bearish", "trend down", "caution", "turning down")
    buy_keywords = ("oversold", "support", "bullish", "trend up", "breakout", "turning up")
    sell_cms, buy_cms = [], []
    for c in checkmarks:
        c_lower = c.lower()
        if any(k in c_lower for k in sell_keywords):
            sell_cms.append(c)
        elif any(k in c_lower for k in buy_keywords):
            buy_cms.append(c)
    return sell_cms, buy_cms


def _format_breakdown_sections(breakdown: list[tuple[str, float, str]]) -> tuple[list[str], list[str]]:
    """Return (sell_labels, buy_labels) with friendly names, deduped by label."""
    sell_labels = []
    buy_labels = []
    seen_sell = set()
    seen_buy = set()
    for label, w, side in breakdown:
        name = _LABEL_NAMES.get(label, label)
        if side == "sell" and name not in seen_sell:
            sell_labels.append(name)
            seen_sell.add(name)
        elif side == "buy" and name not in seen_buy:
            buy_labels.append(name)
            seen_buy.add(name)
    return sell_labels, buy_labels


def _rr_to_label(rr: float) -> str:
    """
    Map raw reward/risk ratio to a descriptive label.
    """
    if rr < 0.8:
        return "Poor"
    if rr < 1.2:
        return "Fair"
    if rr < 1.8:
        return "Decent"
    if rr < 2.5:
        return "Good"
    return "Excellent"


def _format_pct_move(signal_type: str, pct: float, is_stop: bool) -> str:
    """
    Format percentage as price-move direction: ↑ = rises, ↓ = drops.
    Buy Stop: price drops → ↓5%. Buy TP: price rises → ↑8%.
    Sell Stop: price rises → ↑5%. Sell TP: price drops → ↓8%.
    """
    abs_pct = abs(pct)
    if signal_type == "Buy":
        if is_stop:
            return f"↓{abs_pct:.1f}%"  # price drops = stop hit
        return f"↑{abs_pct:.1f}%"  # price rises = TP hit
    else:
        if is_stop:
            return f"↑{abs_pct:.1f}%"  # price rises = stop hit (short)
        return f"↓{abs_pct:.1f}%"  # price drops = TP hit (short)


def _format_levels_block(signal: Signal) -> str | None:
    """Build Entry/Stop Loss/Take Profit block for Buy or Sell signals. Returns None if no levels."""
    if signal.signal_type not in ("Buy", "Sell") or signal.stop_price is None:
        return None

    stop_move = _format_pct_move(signal.signal_type, signal.stop_pct or 0, is_stop=True)
    lines = [
        f"**Entry:**        ${signal.price:.2f}",
        f"**Stop Loss:**    ${signal.stop_price:.2f}  ({stop_move})",
    ]
    if signal.take_profit_price is not None:
        tp_pct = (signal.take_profit_price - signal.price) / signal.price * 100 if signal.signal_type == "Buy" else (signal.price - signal.take_profit_price) / signal.price * 100
        tp_move = _format_pct_move(signal.signal_type, tp_pct, is_stop=False)
        lines.append(f"**Take Profit:**  ${signal.take_profit_price:.2f}  ({tp_move})")
        if signal.stop_pct is not None and signal.stop_pct != 0:
            risk_pct = abs(signal.stop_pct)
            reward_pct = abs(tp_pct)
            rr = reward_pct / risk_pct if risk_pct > 0 else 0.0
            rr_label = _rr_to_label(rr)
            lines.append(f"**Risk/Reward Ratio:** {rr_label}")
    return "\n".join(lines)


def format_signal_breakdown_line(
    weighted_scores: WeightedScores | None, signal_type: str
) -> str:
    """One-line breakdown for recap (e.g. 'RSI, BB, Trend')."""
    if weighted_scores is None:
        return ""
    sell_labels, buy_labels = _format_breakdown_sections(weighted_scores.breakdown)
    labels = buy_labels if signal_type == "Buy" else sell_labels
    return ", ".join(labels) if labels else ""


def format_stock_embed(
    ticker: str,
    signal: Signal | None,
    indicators: dict | None,
    config: dict | None = None,
    markets: str | None = None,
    ignore_volatility: bool = False,
    news_headlines: list[dict] | None = None,
    timeframe: str = "Daily",
    show_breakdown: bool = False,
) -> dict:
    """
    Build Discord embed for single-stock quote.
    If no data: error embed. Otherwise: title, short summary, net score, then bullet points.
    markets: Exchange name(s) for footer (e.g. 'NASDAQ').
    ignore_volatility: When True, omit volatility bullet and daily range display.
    """
    config = config or {}
    now_et = get_current_et()
    timestamp_str = now_et.strftime("%Y-%m-%d %I:%M %p ET")
    footer_parts = [timestamp_str]
    if markets:
        footer_parts.append(markets)
    footer_parts.append(f"All recommendations based on {timeframe} timeframe")
    if signal and signal.regime:
        footer_parts.append(f"Regime: {signal.regime}")
    footer_text = " | ".join(footer_parts)

    if indicators is None or signal is None:
        return {
            "title": f"{ticker} – No data",
            "description": f"No data found for '{ticker}'. Check the symbol and try again.",
            "color": 0x808080,
            "footer": {"text": footer_text},
        }

    # TF signals: simplified display (no MR indicator breakdown)
    is_tf = signal.weighted_scores is None and signal.net_score is None

    if is_tf:
        if signal.signal_type == "Hold":
            sig_line = "**HOLD** — no breakout detected"
        else:
            sig_line = f"**{signal.signal_type.upper()}** ({signal.confidence}% confidence)"

        body_parts = [
            sig_line,
            "",
            "Trend-following signal (Donchian channel breakout).",
        ]
        if not ignore_volatility:
            atr_pct = indicators.get("atr_pct", 0.0)
            body_parts.append(f"**Daily range:** {atr_pct:.1f}%")
        levels_block = _format_levels_block(signal)
        if levels_block:
            body_parts.append("")
            body_parts.append(levels_block)
        body_parts.append("")
    else:
        # MR signals: full indicator breakdown
        if signal.weighted_scores is not None:
            weighted = signal.weighted_scores
        else:
            weighted = compute_weighted_scores(indicators, config, ignore_volatility=ignore_volatility)
        checkmarks = weighted.checkmarks
        buy_count = weighted.buy_count
        sell_count = weighted.sell_count

        total = 8 if not ignore_volatility else 7
        summary = _build_summary_line(signal, buy_count, sell_count, checkmarks, total=total)

        net = weighted.net_score

        if signal.signal_type == "Hold":
            sig_line = "**HOLD** — no strong signal"
        else:
            sig_line = f"**{signal.signal_type.upper()}** ({signal.confidence}% confidence)"

        body_parts = [
            sig_line,
            "",
            summary,
        ]

        if net > 0:
            body_parts.append(f"**Score:** +{net:.1f} (buy-leaning)")
        elif net < 0:
            body_parts.append(f"**Score:** {net:.1f} (sell-leaning)")
        else:
            body_parts.append("**Score:** 0 (mixed)")
        if not ignore_volatility:
            atr_pct = indicators.get("atr_pct", 0.0)
            body_parts.append(f"**Daily range:** {atr_pct:.1f}%")
        levels_block = _format_levels_block(signal)
        if levels_block:
            body_parts.append("")
            body_parts.append(levels_block)
        body_parts.append("")

        if show_breakdown:
            sell_labels, buy_labels = _format_breakdown_sections(weighted.breakdown)
            if sell_labels or buy_labels:
                if sell_labels:
                    body_parts.append(f"**Sell signals:** {', '.join(sell_labels)}")
                if buy_labels:
                    body_parts.append(f"**Buy signals:** {', '.join(buy_labels)}")
                body_parts.append("")

            sell_cms, buy_cms = _split_checkmarks_by_side(checkmarks)
            if sell_cms or buy_cms:
                if sell_cms:
                    body_parts.append("**Supporting sell:**")
                    for c in sell_cms:
                        body_parts.append(c)
                    if buy_cms:
                        body_parts.append("")
                if buy_cms:
                    body_parts.append("**Supporting buy:**")
                    for c in buy_cms:
                        body_parts.append(c)

    # Intrinsic value (Graham) — display-only, does not affect signal
    if signal and signal.intrinsic_value is not None:
        iv = signal.intrinsic_value
        label = signal.valuation_label or "N/A"
        mos = signal.margin_of_safety or 0.0
        if body_parts and body_parts[-1] != "":
            body_parts.append("")
        mos_sign = "+" if mos >= 0 else ""
        body_parts.append(f"**Intrinsic Value (Graham):** ${iv:.2f}")
        body_parts.append(f"Margin of Safety: {mos_sign}{mos:.1f}% ({label})")

    # News about the stock
    if news_headlines:
        if body_parts and body_parts[-1] != "":
            body_parts.append("")
        news_block = format_headlines_for_embed(news_headlines, ticker, max_items=5)
        if news_block:
            body_parts.append(news_block)

    description = "\n".join(body_parts).strip()

    # Softer colors for better readability
    if signal.signal_type == "Buy":
        color = 0x2E7D32  # Material green
    elif signal.signal_type == "Sell":
        color = 0xC62828  # Material red
    else:
        color = 0x616161  # Grey 700

    price_str = f"${signal.price:.2f}"
    title = f"{ticker}  ·  {price_str}"

    result: dict = {
        "title": title,
        "description": description,
        "color": color,
        "footer": {"text": footer_text},
    }
    return result
