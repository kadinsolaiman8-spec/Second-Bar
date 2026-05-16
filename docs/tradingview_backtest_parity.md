# TradingView Strategy Tester parity with `quant/backtest.py`

This repo’s backtest engine documents fills at **next bar open** after a signal on the **prior bar’s close** (see module docstring in [`quant/backtest.py`](../quant/backtest.py)). Use this checklist so **15m–60m** runs on TradingView are comparable to Python.

## Fill and signal timing

| Concept | Python (`run_backtest`) | TradingView |
|--------|-------------------------|-------------|
| When signal is evaluated | After bar *i* closes (uses data through close) | Strategy settings: **On bar close** (not optimistic intrabar) |
| Entry fill price | Next bar (**i+1**) open, with costs | Properties → Order fills: align with **next open** if your Pine/logic mirrors bar-close signals |
| Costs | `backtest.commission_pct`, `backtest.slippage_pct` in [`config.yaml`](../config.yaml) | Strategy Tester → Commission / Slippage: enter **same %** as YAML for apples-to-apples |

## Session

- **Regular session:** 09:30–16:00 **America/New_York** matches `scanner_core.config` market hours used for scanning context.
- Extended hours: if you trade them on TV, Python scans **do not** automatically align — document an explicit override in your playbook.

## Prices

- yfinance downloads in this project typically use **adjusted** OHLC in batch paths; other vendors expose their own adjustment flags — match TV’s chart adjustment setting.
- TradingView: use **Adjusted chart** (or same dividend adjustment policy) when comparing to Python.

## Single source of truth for “what I typed in TV”

Keep a short note (appendix in [`intraday_tv_playbook.md`](intraday_tv_playbook.md) or a scratch file) listing: timezone, session template, commission %, slippage %, and fill model. That avoids drift between reruns.

## Scanner vs backtest

- **Scanner** uses live-style multi-interval data (`1m`, `5m`, `15m`, `1h` in the default setup); alert levels are derived from that pipeline (see [`scanner_core/scanner.py`](../scanner_core/scanner.py)).
- **Quant backtest** uses one `interval` per run (`POST /api/quant/backtest`). For strict 15m parity, run backtests with `interval: "15m"` once your data path matches TV ([`intraday_data_tv_reconciliation.md`](intraday_data_tv_reconciliation.md)).

## Quick verification

1. Pick **one symbol**, **one interval** (e.g. 15m), **60 trading days**.
2. Run `POST /api/quant/backtest` with the same period/interval and costs as TV.
3. Match **trade count ± tolerance** first; then compare **entry dates** bar-by-bar (timezone!).
4. If counts diverge, almost always **session**, **adjustment**, or **signal-on-close vs intrabar** mismatch — fix those before tuning parameters.
