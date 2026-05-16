# Intraday playbook — 15m–60m TradingView day-trading

Use this document **before** you change parameters or interpret scan/backtest results. It mirrors the spirit of [`wfo_batches/`](wfo_batches/) pre-registration for **live/discretionary** and **intraday** work.

## Target product shape (documentation only)

End state you’re aiming for:

1. **Always-on service** while the US regular session is open: periodic full-watchlist scans (interval is an ops choice — e.g. tens of seconds to a few minutes).
2. **Opportunity surfacing:** ranked alerts with symbol, direction, strategy tag, levels (entry / stop / targets), and context (regime, session quality).
3. **TradingView handoff:** for each alert, you manually mirror on TV — open symbol, set timezone/session, pick execution timeframe from your preregistered playbook, place/manage orders per your risk rules. The bot recommends; **you** execute.

Nothing in this section prescribes a particular codebase layout; implement when you’re ready.

## Commitment

- Date drafted (pre-run): **________**
- Author: **________**
- Git commit / tag frozen for this study: **________**

## Pilot universe (5–15 names)

| Ticker | **Official mode** (one only: `mr` \| `tf` \| `hybrid`) | **Official execution TF** (e.g. 15m) | **Optional bias TF** (e.g. 60m; exploratory if not in prereg) | Max trades/day | Risk % per trade |
|--------|------------------------------------------------------|-------------------------------------|---------------------------------------------------------------|----------------|------------------|
|        |                                                      |                                     |                                                               |                |                  |
|        |                                                      |                                     |                                                               |                |                  |

**Exploratory only (never mixed into pass/fail scorecard):** tickers or modes you run “for curiosity” after the official row is locked.

## Session and platform rules

- **Session:** Regular hours (RTH) **9:30–16:00 ET** unless you document extended hours explicitly.
- **Platform:** TradingView chart interval must match **official execution TF** for entries you attribute to this playbook.
- **Bar semantics:** Signals apply to **completed** bars only; fills assumed **next bar open** (see [`tradingview_backtest_parity.md`](tradingview_backtest_parity.md) and [`quant/backtest.py`](../quant/backtest.py)).

## Scanner vs TradingView

- The scanner stack in this repo commonly uses **1m / 5m / 15m / 1h** OHLCV (see `scanner_core.config.TIMEFRAMES`). **Alert reference prices are tied to the short-interval pipeline** (typically 5m context); 15m/1h are used for confirmation and filters — verify in code when you wire execution.
- For strict **15m-only** or **60m-only** discretion on TV, treat automated output as **advisory** until bar reconciliation passes ([`intraday_data_tv_reconciliation.md`](intraday_data_tv_reconciliation.md)).

## Gates (frozen before you judge results)

- Primary research gate: stationary bootstrap on **concatenated OOS** (see [`CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md`](CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md)).
- **One official hypothesis per ticker:** do not pick MR vs TF vs hybrid after seeing equity curves.
- **Multiplicity:** if you test many tickers, apply BH/Bonferroni when combining *p*-values ([`scripts/apply_multiple_testing_correction.py`](../scripts/apply_multiple_testing_correction.py)).

## Pass / fail (operational)

Define numeric thresholds **here** before the month:

- Minimum sample: **N** closed trades (paper or small size) before a “go / no-go” review.
- Max drawdown (R or %): **________**
- Minimum profit factor (live/paper): **________**
- Slippage budget vs theoretical entry: **________** (track in a journal spreadsheet or doc)

## Post-month appendix

- Month: **________**
- Trades closed: **________**
- Link or path to trade log / journal: **________**
- Brief note on live vs backtested assumptions (slippage, fills): **________**
- Config / Pine revision *after* month-end (yes/no; if yes, link PR or commit): **________**
