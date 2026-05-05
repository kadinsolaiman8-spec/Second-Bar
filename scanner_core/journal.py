# journal.py — Paper trading journal, trade lifecycle, R-multiples, statistics

import uuid
import math
import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz

from scanner_core.config import (
    MARKET_TIMEZONE, PAPER_TRADE_MAX_HOLD_MINUTES,
    ATR_STOP_LOSS_MULTIPLIER,
    STRATEGY_VWAP_BREAKOUT, STRATEGY_ORB, STRATEGY_EMA_PULLBACK,
    STRATEGY_FIBONACCI, STRATEGY_BB_SQUEEZE, ALL_STRATEGIES,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


# ══════════════════════════════════════════════════════════════════════════
# DATA STORES
# ══════════════════════════════════════════════════════════════════════════

open_trades: dict = {}     # { trade_id: trade_dict }
closed_trades: list = []   # list of completed trade dicts

daily_stats: dict = {
    "total_alerts_today": 0,
    "total_trades_closed": 0,
    "wins": 0,
    "losses": 0,
    "time_exits": 0,
    "win_rate": 0.0,
    "avg_r": 0.0,
    "profit_factor": 0.0,
    "expectancy": 0.0,
    "current_streak": 0,
    "current_streak_type": "none",
    "longest_win_streak": 0,
    "longest_loss_streak": 0,
    "strategy_stats": {s: {"wins": 0, "losses": 0, "r_sum": 0.0} for s in ALL_STRATEGIES},
    "type_stats": {
        "stable": {"wins": 0, "losses": 0, "r_sum": 0.0},
        "volatile": {"wins": 0, "losses": 0, "r_sum": 0.0},
    },
    "total_r_gained": 0.0,
    "best_trade": None,
    "worst_trade": None,
}


# ══════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _calculate_r_multiple(entry: float, exit_price: float, stop: float,
                          direction: str) -> float:
    """Calculate R-multiple: how many R units gained/lost."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    if direction.upper() == "BUY":
        pnl = exit_price - entry
    else:
        pnl = entry - exit_price
    return round(pnl / risk, 2)


def _calculate_pnl_pct(entry: float, exit_price: float, direction: str) -> float:
    """Calculate percentage P/L."""
    if entry <= 0:
        return 0.0
    if direction.upper() == "BUY":
        return round((exit_price - entry) / entry * 100, 2)
    else:
        return round((entry - exit_price) / entry * 100, 2)


def _update_streaks(is_win: bool):
    """Update current and longest win/loss streaks."""
    if is_win:
        if daily_stats["current_streak_type"] == "win":
            daily_stats["current_streak"] += 1
        else:
            daily_stats["current_streak"] = 1
            daily_stats["current_streak_type"] = "win"
        daily_stats["longest_win_streak"] = max(
            daily_stats["longest_win_streak"], daily_stats["current_streak"]
        )
    else:
        if daily_stats["current_streak_type"] == "loss":
            daily_stats["current_streak"] += 1
        else:
            daily_stats["current_streak"] = 1
            daily_stats["current_streak_type"] = "loss"
        daily_stats["longest_loss_streak"] = max(
            daily_stats["longest_loss_streak"], daily_stats["current_streak"]
        )


def _update_strategy_stats(strategy: str, is_win: bool, r_multiple: float):
    """Update per-strategy statistics."""
    if strategy in daily_stats["strategy_stats"]:
        ss = daily_stats["strategy_stats"][strategy]
    else:
        ss = {"wins": 0, "losses": 0, "r_sum": 0.0}
        daily_stats["strategy_stats"][strategy] = ss
    if is_win:
        ss["wins"] += 1
    else:
        ss["losses"] += 1
    ss["r_sum"] += r_multiple


def _update_type_stats(stock_type: str, is_win: bool, r_multiple: float):
    """Update stable vs volatile statistics."""
    key = stock_type if stock_type in daily_stats["type_stats"] else "stable"
    ts = daily_stats["type_stats"][key]
    if is_win:
        ts["wins"] += 1
    else:
        ts["losses"] += 1
    ts["r_sum"] += r_multiple


def _recalculate_stats():
    """Recalculate rolling stats from daily counters."""
    w = daily_stats["wins"]
    l = daily_stats["losses"]
    total = w + l
    daily_stats["win_rate"] = round(w / total * 100, 1) if total > 0 else 0.0

    # Avg R
    if total > 0:
        daily_stats["avg_r"] = round(daily_stats["total_r_gained"] / total, 2)
    else:
        daily_stats["avg_r"] = 0.0

    # Profit factor
    win_r = sum(
        t.get("r_multiple", 0) for t in closed_trades if t.get("r_multiple", 0) > 0
    )
    loss_r = abs(sum(
        t.get("r_multiple", 0) for t in closed_trades if t.get("r_multiple", 0) < 0
    ))
    daily_stats["profit_factor"] = round(win_r / loss_r, 2) if loss_r > 0 else (
        99.9 if win_r > 0 else 0.0
    )

    # Expectancy
    if total > 0:
        avg_win = win_r / w if w > 0 else 0
        avg_loss = loss_r / l if l > 0 else 0
        wr = w / total
        daily_stats["expectancy"] = round(wr * avg_win - (1 - wr) * avg_loss, 2)
    else:
        daily_stats["expectancy"] = 0.0


# ══════════════════════════════════════════════════════════════════════════
# TRADE LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════

def open_trade(ticker: str, direction: str, strategy: str,
               entry_price: float, stop_loss: float, tp1: float, tp2: float,
               score: int, stock_type: str = "stable") -> str:
    """Open a new paper trade. Returns trade_id."""
    trade_id = str(uuid.uuid4())[:8]
    now = datetime.now(ET)

    trade = {
        "trade_id": trade_id,
        "ticker": ticker.upper(),
        "direction": direction.upper(),
        "strategy": strategy,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "entry_time": now,
        "score": score,
        "stock_type": stock_type,
    }
    open_trades[trade_id] = trade
    daily_stats["total_alerts_today"] += 1
    logger.info(f"Trade opened: {trade_id} {direction} {ticker} @ ${entry_price:.2f}")
    return trade_id


def close_trade(trade_id: str, exit_price: float, exit_reason: str,
                bars_held: int = 0) -> dict:
    """Close a trade, calculate R-multiple, update stats. Returns closed trade dict."""
    if trade_id not in open_trades:
        return {"error": f"Trade {trade_id} not found"}

    trade = open_trades.pop(trade_id)
    now = datetime.now(ET)

    r = _calculate_r_multiple(
        trade["entry_price"], exit_price, trade["stop_loss"], trade["direction"]
    )
    pnl_pct = _calculate_pnl_pct(trade["entry_price"], exit_price, trade["direction"])
    is_win = r > 0

    trade.update({
        "exit_price": exit_price,
        "exit_time": now,
        "exit_reason": exit_reason,
        "r_multiple": r,
        "pnl_pct": pnl_pct,
        "bars_held": bars_held,
    })

    closed_trades.append(trade)

    # Update stats
    daily_stats["total_trades_closed"] += 1
    daily_stats["total_r_gained"] += r
    if exit_reason == "time_exit":
        daily_stats["time_exits"] += 1
    if is_win:
        daily_stats["wins"] += 1
    else:
        daily_stats["losses"] += 1

    _update_streaks(is_win)
    _update_strategy_stats(trade["strategy"], is_win, r)
    _update_type_stats(trade["stock_type"], is_win, r)
    _recalculate_stats()

    # Best/worst
    if daily_stats["best_trade"] is None or r > daily_stats["best_trade"].get("r_multiple", -999):
        daily_stats["best_trade"] = trade
    if daily_stats["worst_trade"] is None or r < daily_stats["worst_trade"].get("r_multiple", 999):
        daily_stats["worst_trade"] = trade

    logger.info(f"Trade closed: {trade_id} {trade['ticker']} {exit_reason} "
                f"R={'+' if r >= 0 else ''}{r:.2f}")
    return trade


def check_trade_exits(trade_id: str, current_high: float, current_low: float,
                      current_close: float, bars_since_entry: int) -> Optional[dict]:
    """Check if trade should exit. Returns None or {exit_price, exit_reason}."""
    if trade_id not in open_trades:
        return None
    trade = open_trades[trade_id]
    direction = trade["direction"]

    # Check time exit first
    elapsed = (datetime.now(ET) - trade["entry_time"]).total_seconds() / 60
    if elapsed >= PAPER_TRADE_MAX_HOLD_MINUTES:
        return {"exit_price": current_close, "exit_reason": "time_exit"}

    if direction == "BUY":
        # Stop loss
        if current_low <= trade["stop_loss"]:
            return {"exit_price": trade["stop_loss"], "exit_reason": "stop_loss"}
        # TP2 first (check if hit in same candle)
        if current_high >= trade["tp2"]:
            return {"exit_price": trade["tp2"], "exit_reason": "tp2"}
        # TP1
        if current_high >= trade["tp1"]:
            return {"exit_price": trade["tp1"], "exit_reason": "tp1"}
    else:
        # Stop loss
        if current_high >= trade["stop_loss"]:
            return {"exit_price": trade["stop_loss"], "exit_reason": "stop_loss"}
        # TP2
        if current_low <= trade["tp2"]:
            return {"exit_price": trade["tp2"], "exit_reason": "tp2"}
        # TP1
        if current_low <= trade["tp1"]:
            return {"exit_price": trade["tp1"], "exit_reason": "tp1"}

    return None


def cancel_trade(trade_id: str) -> bool:
    """Remove from open trades without closing."""
    if trade_id in open_trades:
        del open_trades[trade_id]
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════
# QUERY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def get_open_trades() -> dict:
    return dict(open_trades)


def get_open_trade_count() -> int:
    return len(open_trades)


def get_recent_trades(n: int = 10) -> list:
    return closed_trades[-n:]


def get_trade_by_id(trade_id: str) -> Optional[dict]:
    if trade_id in open_trades:
        return open_trades[trade_id]
    for t in closed_trades:
        if t.get("trade_id") == trade_id:
            return t
    return None


def has_open_trade(ticker: str) -> bool:
    ticker = ticker.upper()
    return any(t.get("ticker") == ticker for t in open_trades.values())


# ══════════════════════════════════════════════════════════════════════════
# STATISTICS
# ══════════════════════════════════════════════════════════════════════════

def get_today_stats() -> dict:
    return dict(daily_stats)


def get_win_rate() -> float:
    return daily_stats["win_rate"]


def get_avg_r() -> float:
    return daily_stats["avg_r"]


def get_profit_factor() -> float:
    return daily_stats["profit_factor"]


def get_expectancy() -> float:
    return daily_stats["expectancy"]


def get_streak_info() -> dict:
    return {
        "current": daily_stats["current_streak"],
        "type": daily_stats["current_streak_type"],
        "longest_win": daily_stats["longest_win_streak"],
        "longest_loss": daily_stats["longest_loss_streak"],
    }


def get_streak_celebration() -> str | None:
    """Return a celebration/warning message if streak hits a milestone, else None."""
    streak = daily_stats["current_streak"]
    stype = daily_stats["current_streak_type"]
    if stype == "win" and streak == 3:
        return "\U0001f525 **3-Win Streak!** The strategy is working well right now. Current conditions are favorable."
    if stype == "win" and streak == 5:
        return "\U0001f525\U0001f525\U0001f525 **5-Win Streak!** Exceptional performance. Continue following signals carefully."
    if stype == "loss" and streak == 3:
        state = ""
        try:
            from scanner_core.market_state import get_current_market_state, get_state_label
            ms = get_current_market_state()
            state = get_state_label(ms)
        except Exception:
            state = "Unknown"
        return (
            f"\u26A0\uFE0F **3 consecutive losses detected.**\n"
            f"Reviewing market conditions...\n"
            f"Market State: **{state}**\n"
            f"Consider waiting for stronger setups."
        )
    return None


def get_strategy_breakdown() -> dict:
    """Win rate and avg R for each strategy."""
    result = {}
    for strat, data in daily_stats["strategy_stats"].items():
        total = data["wins"] + data["losses"]
        wr = round(data["wins"] / total * 100, 1) if total > 0 else 0.0
        avg = round(data["r_sum"] / total, 2) if total > 0 else 0.0
        result[strat] = {
            "wins": data["wins"],
            "losses": data["losses"],
            "total": total,
            "win_rate": wr,
            "avg_r": avg,
        }
    return result


def get_type_breakdown() -> dict:
    """Win rate for stable vs volatile."""
    result = {}
    for stype, data in daily_stats["type_stats"].items():
        total = data["wins"] + data["losses"]
        wr = round(data["wins"] / total * 100, 1) if total > 0 else 0.0
        result[stype] = {"wins": data["wins"], "losses": data["losses"],
                         "win_rate": wr}
    return result


# ══════════════════════════════════════════════════════════════════════════
# WINNERS / LOSERS
# ══════════════════════════════════════════════════════════════════════════

def get_winners(n: int = 5) -> list:
    """Top n trades by R-multiple."""
    sorted_trades = sorted(closed_trades, key=lambda t: t.get("r_multiple", 0), reverse=True)
    return sorted_trades[:n]


def get_losers(n: int = 5) -> list:
    """Bottom n trades by R-multiple."""
    sorted_trades = sorted(closed_trades, key=lambda t: t.get("r_multiple", 0))
    return sorted_trades[:n]


# ══════════════════════════════════════════════════════════════════════════
# EQUITY CURVE
# ══════════════════════════════════════════════════════════════════════════

def generate_equity_curve_ascii(width: int = 40, height: int = 8) -> str:
    """ASCII equity curve from cumulative R-multiples."""
    if not closed_trades:
        return "No closed trades yet."

    # Build cumulative R series
    cum_r = []
    running = 0.0
    for t in closed_trades:
        running += t.get("r_multiple", 0)
        cum_r.append(running)

    # Use last `width` points
    data = cum_r[-width:]
    if len(data) < 2:
        return f"Cumulative R: {'+' if running >= 0 else ''}{running:.2f}R (1 trade)"

    min_val = min(data)
    max_val = max(data)
    rng = max_val - min_val
    if rng <= 0:
        rng = 1

    # Build grid
    grid = [[" "] * len(data) for _ in range(height)]

    def val_to_row(v):
        row = int((max_val - v) / rng * (height - 1))
        return max(0, min(height - 1, row))

    for i, v in enumerate(data):
        row = val_to_row(v)
        grid[row][i] = "█" if v >= 0 else "░"

    # Zero line
    if min_val < 0 < max_val:
        zero_row = val_to_row(0)
        for i in range(len(data)):
            if grid[zero_row][i] == " ":
                grid[zero_row][i] = "─"

    lines = []
    for r, row in enumerate(grid):
        val_at_row = max_val - (r / (height - 1)) * rng
        label = f"{val_at_row:>+6.1f}R|"
        lines.append(label + "".join(row))

    lines.append("       +" + "─" * len(data))
    lines.append(f"        Last {len(data)} trades  (█=profit ░=loss)")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# SUMMARY FORMATTER
# ══════════════════════════════════════════════════════════════════════════

def get_journal_summary() -> str:
    """Multi-line Discord-formatted summary."""
    s = daily_stats
    w = s["wins"]
    l = s["losses"]
    total = w + l
    streak_emoji = "🔥" if s["current_streak_type"] == "win" and s["current_streak"] >= 3 else ""
    streak_type = f"{s['current_streak']}{'W' if s['current_streak_type'] == 'win' else 'L'}"

    best_str = "N/A"
    worst_str = "N/A"
    if s["best_trade"]:
        bt = s["best_trade"]
        best_str = f"{bt.get('ticker', '?')} {'+' if bt.get('r_multiple', 0) >= 0 else ''}{bt.get('r_multiple', 0):.1f}R"
    if s["worst_trade"]:
        wt = s["worst_trade"]
        worst_str = f"{wt.get('ticker', '?')} {wt.get('r_multiple', 0):.1f}R"

    lines = [
        "📊 Paper Trading Journal",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Today: {w}W / {l}L ({s['win_rate']:.1f}%)" if total > 0 else "Today: No trades yet",
        f"Avg R: {'+' if s['avg_r'] >= 0 else ''}{s['avg_r']:.2f}R | PF: {s['profit_factor']:.1f}",
        f"Streak: {streak_emoji} {streak_type}" if total > 0 else "Streak: N/A",
        f"Best: {best_str} | Worst: {worst_str}",
        f"Open trades: {len(open_trades)}",
        f"Total R: {'+' if s['total_r_gained'] >= 0 else ''}{s['total_r_gained']:.2f}R",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# RESET
# ══════════════════════════════════════════════════════════════════════════

def reset_daily_stats():
    """Reset daily stats for new day."""
    daily_stats.update({
        "total_alerts_today": 0, "total_trades_closed": 0,
        "wins": 0, "losses": 0, "time_exits": 0,
        "win_rate": 0.0, "avg_r": 0.0,
        "profit_factor": 0.0, "expectancy": 0.0,
        "current_streak": 0, "current_streak_type": "none",
        "longest_win_streak": 0, "longest_loss_streak": 0,
        "total_r_gained": 0.0,
        "best_trade": None, "worst_trade": None,
    })
    for s in daily_stats["strategy_stats"].values():
        s.update({"wins": 0, "losses": 0, "r_sum": 0.0})
    for s in daily_stats["type_stats"].values():
        s.update({"wins": 0, "losses": 0, "r_sum": 0.0})
    logger.info("Daily stats reset")


def reset_all_journal():
    """Clear everything."""
    open_trades.clear()
    closed_trades.clear()
    reset_daily_stats()
    logger.info("Journal fully reset")
