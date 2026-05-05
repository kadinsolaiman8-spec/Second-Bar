"""
Daytrade module: real-time stop-loss and take-profit suggestions.
Uses ATR-based volatility and config backtest params.
"""

from typing import Any

from src.market_hours import get_current_et
from src.signals import Signal


def compute_daytrade_levels(
    price: float,
    atr: float,
    config: dict[str, Any],
) -> dict[str, float | None]:
    """
    Compute stop-loss and take-profit levels for daytrading.

    Args:
        price: Current price (last close).
        atr: Average True Range (absolute).
        config: Bot config with daytrade and backtest sections.

    Returns:
        Dict with stop_atr, stop_atr_pct, tp_atr, tp_atr_pct,
        stop_config, tp_config, trailing_pct. None for unavailable values.
    """
    dt_cfg = config.get("daytrade", {})
    bt_cfg = config.get("backtest", {})

    stop_mult = float(dt_cfg.get("atr_stop_multiplier", 2.0))
    tp_mult = float(dt_cfg.get("atr_tp_multiplier", 2.0))

    stop_pct = bt_cfg.get("stop_pct") or 0
    take_profit_pct = bt_cfg.get("take_profit_pct") or 0
    trailing_stop_pct = bt_cfg.get("trailing_stop_pct") or 0

    result: dict[str, float | None] = {
        "stop_atr": None,
        "stop_atr_pct": None,
        "tp_atr": None,
        "tp_atr_pct": None,
        "stop_config": None,
        "tp_config": None,
        "trailing_pct": None,
    }

    # ATR-based levels (skip if ATR invalid)
    if atr is not None and atr > 0 and price > 0:
        stop_atr = price - (stop_mult * atr)
        tp_atr = price + (tp_mult * atr)
        if stop_atr > 0:
            result["stop_atr"] = stop_atr
            result["stop_atr_pct"] = (stop_atr - price) / price * 100
        result["tp_atr"] = tp_atr
        result["tp_atr_pct"] = (tp_atr - price) / price * 100

    # Config-based % levels
    if stop_pct > 0:
        result["stop_config"] = price * (1 - stop_pct / 100)
    if take_profit_pct > 0:
        result["tp_config"] = price * (1 + take_profit_pct / 100)
    if trailing_stop_pct > 0:
        result["trailing_pct"] = trailing_stop_pct

    return result


def format_daytrade_embed(
    ticker: str,
    signal: Signal | None,
    indicators: dict[str, float] | None,
    levels: dict[str, float | None],
    config: dict[str, Any],
) -> dict[str, Any]:
    """
    Build Discord embed dict for daytrade suggestions.

    Args:
        ticker: Display ticker symbol.
        signal: Evaluated signal (Buy/Sell/Hold).
        indicators: Latest indicator values including atr, atr_pct.
        levels: Output from compute_daytrade_levels.
        config: Bot config for daytrade multipliers.

    Returns:
        Dict with title, description, color, footer, fields.
    """
    dt_cfg = config.get("daytrade", {})
    stop_mult = dt_cfg.get("atr_stop_multiplier", 2.0)
    tp_mult = dt_cfg.get("atr_tp_multiplier", 2.0)

    now_et = get_current_et()
    timestamp_str = now_et.strftime("%Y-%m-%d %I:%M %p ET")
    footer_text = f"{timestamp_str} | 1H intraday | Market open"

    if signal is None or indicators is None:
        return {
            "title": f"{ticker} – No data",
            "description": f"No intraday data for '{ticker}'. Check the symbol and try again.",
            "color": 0x808080,
            "footer": {"text": footer_text},
            "fields": [],
        }

    price = signal.price
    atr_pct = indicators.get("atr_pct", 0.0)

    body_parts: list[str] = []

    # Signal line
    if signal.signal_type == "Hold":
        sig_line = "**HOLD** — no strong signal"
    else:
        sig_line = f"**{signal.signal_type.upper()}** ({signal.confidence}% confidence)"
    body_parts.append(sig_line)
    body_parts.append("")

    # Stop loss section
    stop_lines: list[str] = []
    if levels.get("stop_atr") is not None:
        stop_atr = levels["stop_atr"]
        stop_atr_pct = levels.get("stop_atr_pct") or 0
        stop_lines.append(f"• **ATR ({stop_mult}×):** ${stop_atr:.2f} ({stop_atr_pct:.1f}%)")
    if levels.get("stop_config") is not None:
        stop_config = levels["stop_config"]
        stop_lines.append(f"• **Config:** ${stop_config:.2f}")
    if stop_lines:
        body_parts.append("**Stop Loss**")
        body_parts.extend(stop_lines)
        body_parts.append("")

    # Take profit section
    tp_lines: list[str] = []
    if levels.get("tp_atr") is not None:
        tp_atr = levels["tp_atr"]
        tp_atr_pct = levels.get("tp_atr_pct") or 0
        tp_lines.append(f"• **ATR ({tp_mult}×):** ${tp_atr:.2f} (+{tp_atr_pct:.1f}%)")
    if levels.get("tp_config") is not None:
        tp_config = levels["tp_config"]
        tp_lines.append(f"• **Config:** ${tp_config:.2f}")
    if tp_lines:
        body_parts.append("**Take Profit**")
        body_parts.extend(tp_lines)
        body_parts.append("")

    # Trailing stop
    if levels.get("trailing_pct") is not None:
        body_parts.append(
            f"**Trailing:** Exit when price retraces {levels['trailing_pct']:.1f}% from peak."
        )
        body_parts.append("")

    # Daily range (ATR %)
    body_parts.append(f"**1H range (ATR %):** {atr_pct:.1f}%")

    description = "\n".join(body_parts).strip()

    # Colors
    if signal.signal_type == "Buy":
        color = 0x2E7D32  # Material green
    elif signal.signal_type == "Sell":
        color = 0xC62828  # Material red
    else:
        color = 0x616161  # Grey 700

    title = f"{ticker}  ·  ${price:.2f}  ·  Daytrade"

    return {
        "title": title,
        "description": description,
        "color": color,
        "footer": {"text": footer_text},
        "fields": [],
    }
