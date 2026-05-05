"""
CLI entry point for daily paper trading check.

Usage:
    python scripts/run_paper.py
    python scripts/run_paper.py --tickers GLD SLV DBA USO TLT
    python scripts/run_paper.py --status   # Show current state only, no signal check

Designed to run once daily (manually or via cron/Task Scheduler).
"""
import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paper_trade import (
    compute_portfolio_stats,
    load_eval_criteria,
    load_state,
    run_paper_check,
    save_state,
)

# 12-ticker non-equity/EM universe for TF paper trading.
# See data/paper_eval_criteria.yaml for rationale.
DEFAULT_TICKERS = [
    "GLD", "SLV", "DBA", "DBC", "USO",  # Commodities
    "TLT", "IEF",                         # Bonds
    "UUP", "FXE",                         # Currencies
    "EEM", "FXI", "EWZ",                  # EM equity
]


def print_status(state: dict) -> None:
    """Print current paper portfolio status."""
    stats = compute_portfolio_stats(state)
    print("=" * 60)
    print("PAPER PORTFOLIO STATUS")
    print("=" * 60)
    print(f"Started: {state.get('started', 'unknown')}")
    print(f"Closed trades: {stats['total_trades']}")
    print(f"Open positions: {stats['open_positions']}")

    if stats["total_trades"] > 0:
        print(f"Win rate: {stats['win_rate']}%")
        print(f"Avg P&L per trade: {stats['avg_pnl_pct']}%")
        pf = stats["profit_factor"]
        print(f"Profit factor: {pf}")

    if state["positions"]:
        print()
        print("--- Open Positions ---")
        for ticker, pos in state["positions"].items():
            print(
                f"  {ticker}: entry {pos['entry_price']:.2f} on {pos['entry_date']}"
                f"  |  stop {pos.get('stop_price', 'N/A')}"
            )

    # Check eval criteria
    criteria = load_eval_criteria()
    if criteria:
        print()
        print(f"--- Evaluation Criteria (committed {criteria['committed_date']}) ---")
        print(f"Min period: {criteria['evaluation_period_months']} months")
        print(f"Min trades: {criteria['min_trades_to_evaluate']}")
        print(f"Pass: PF > {criteria['pass_criteria']['portfolio_profit_factor']}, "
              f"Sharpe rank >= {criteria['pass_criteria']['observed_sharpe_vs_null_percentile']}th pctl, "
              f"DD > {criteria['pass_criteria']['max_drawdown_pct']}%")

    print()


def main():
    parser = argparse.ArgumentParser(description="Daily paper trading check")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--status", action="store_true",
                        help="Show status only, no signal check")
    args = parser.parse_args()

    tickers = args.tickers or DEFAULT_TICKERS

    state = load_state()

    if args.status:
        print_status(state)
        return

    # Load config
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Load ticker profiles
    tp_path = Path(__file__).resolve().parent.parent / "data" / "ticker_profiles.yaml"
    if tp_path.exists():
        with open(tp_path) as f:
            tp = yaml.safe_load(f) or {}
        for k, v in tp.get("ticker_profiles", {}).items():
            config.setdefault("ticker_profiles", {}).setdefault(k, {}).update(v)

    print(f"Checking signals for: {', '.join(tickers)}")
    print()

    actions = run_paper_check(tickers, config, state)

    if actions:
        print("--- Actions ---")
        for a in actions:
            if a["action"] == "BUY":
                print(f"  BUY  {a['ticker']} @ {a['price']:.2f}  (stop: {a.get('stop_price', 'N/A')})")
            elif a["action"] in ("SELL", "STOP"):
                print(f"  {a['action']}  {a['ticker']} @ {a['price']:.2f}  (P&L: {a['pnl_pct']:+.1f}%)")
    else:
        print("No signals triggered today.")

    save_state(state)
    print()
    print_status(state)


if __name__ == "__main__":
    main()
