"""
Lightweight paper trading tracker for trend-following strategy.

Fetches latest data, generates TF signals via evaluate_breakout_signal(),
tracks hypothetical positions, and persists state to JSON.

No Discord integration — CLI only via scripts/run_paper.py.
"""

import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.config_resolver import get_config_for_ticker
from src.data import fetch_single
from src.signals_trend import evaluate_breakout_signal

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "paper_trades.json"
EVAL_CRITERIA_PATH = Path(__file__).resolve().parent.parent / "data" / "paper_eval_criteria.yaml"


def _json_serial(obj: Any) -> str:
    """JSON serializer for date/datetime objects."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    """Load paper trading state from JSON. Returns empty state if file missing."""
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {
        "started": datetime.now().isoformat(),
        "positions": {},
        "trades": [],
        "daily_portfolio_values": [],
    }


def save_state(state: dict, path: Path = DEFAULT_STATE_PATH) -> None:
    """Persist paper trading state to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=_json_serial)


def load_eval_criteria(path: Path = EVAL_CRITERIA_PATH) -> dict | None:
    """Load pre-committed evaluation criteria."""
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f)
    return None


def _get_signal_for_ticker(
    ticker: str, config: dict, period: str = "1y"
) -> tuple[str | None, float | None, float | None]:
    """
    Fetch latest data and evaluate TF signal for a single ticker.

    Returns:
        (signal_type, price, stop_price) — signal_type is "Buy", "Sell", or None
    """
    df = fetch_single(ticker, period=period)
    if df is None or df.empty:
        return None, None, None

    cfg = get_config_for_ticker(ticker, config)
    cfg["strategy"] = "tf"
    tf_cfg = cfg.get("trend_following", {})

    signal = evaluate_breakout_signal(
        df,
        ticker,
        donchian_period=tf_cfg.get("donchian_period", 55),
        atr_period=tf_cfg.get("atr_period", 14),
        adx_period=tf_cfg.get("adx_period", 14),
        adx_threshold=tf_cfg.get("adx_threshold", None),
        config=cfg,
    )

    if signal is None:
        return None, None, None
    return signal.signal_type, signal.price, signal.stop_price


def run_paper_check(
    tickers: list[str],
    config: dict,
    state: dict,
) -> list[dict]:
    """
    Check signals for all tickers and update positions.

    Returns list of actions taken (for display).
    """
    today = date.today().isoformat()
    actions = []

    for ticker in tickers:
        signal_type, price, stop_price = _get_signal_for_ticker(ticker, config)
        in_position = ticker in state["positions"]

        if signal_type == "Buy" and not in_position and price is not None:
            state["positions"][ticker] = {
                "entry_date": today,
                "entry_price": price,
                "stop_price": stop_price,
            }
            state["trades"].append({
                "ticker": ticker,
                "action": "BUY",
                "date": today,
                "price": price,
                "stop_price": stop_price,
            })
            actions.append({
                "ticker": ticker,
                "action": "BUY",
                "price": price,
                "stop_price": stop_price,
            })

        elif signal_type == "Sell" and in_position and price is not None:
            entry = state["positions"].pop(ticker)
            pnl_pct = (price - entry["entry_price"]) / entry["entry_price"] * 100
            state["trades"].append({
                "ticker": ticker,
                "action": "SELL",
                "date": today,
                "price": price,
                "entry_price": entry["entry_price"],
                "entry_date": entry["entry_date"],
                "pnl_pct": round(pnl_pct, 2),
            })
            actions.append({
                "ticker": ticker,
                "action": "SELL",
                "price": price,
                "pnl_pct": round(pnl_pct, 2),
            })

        elif in_position and price is not None:
            # Check trailing stop
            pos = state["positions"][ticker]
            if pos.get("stop_price") and price < pos["stop_price"]:
                entry = state["positions"].pop(ticker)
                pnl_pct = (price - entry["entry_price"]) / entry["entry_price"] * 100
                state["trades"].append({
                    "ticker": ticker,
                    "action": "STOP",
                    "date": today,
                    "price": price,
                    "entry_price": entry["entry_price"],
                    "entry_date": entry["entry_date"],
                    "pnl_pct": round(pnl_pct, 2),
                })
                actions.append({
                    "ticker": ticker,
                    "action": "STOP",
                    "price": price,
                    "pnl_pct": round(pnl_pct, 2),
                })

    return actions


def compute_portfolio_stats(state: dict) -> dict:
    """Compute summary stats from trade history."""
    closed_trades = [t for t in state["trades"] if t["action"] in ("SELL", "STOP")]
    if not closed_trades:
        return {
            "total_trades": 0,
            "open_positions": len(state["positions"]),
            "profit_factor": None,
            "win_rate": None,
            "avg_pnl_pct": None,
        }

    pnls = [t["pnl_pct"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0

    return {
        "total_trades": len(closed_trades),
        "open_positions": len(state["positions"]),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
        "win_rate": round(len(wins) / len(closed_trades) * 100, 1),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 2),
    }
