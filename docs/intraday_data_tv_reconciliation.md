# Intraday data: providers vs TradingView — reconciliation checklist

Goal: **15m–60m** bars in your research stack match what you see on TradingView within acceptable tolerance before you trust scans or backtests.

## Source options

| Source | Strengths | Caveats |
|--------|-----------|---------|
| **yfinance** (typical in this repo’s scanner path) | No extra key; easy batch | Intraday history length limits; occasional gaps; timezone handling varies by symbol |
| **Polygon** (`POLYGON_API_KEY`) | Long intraday history; aggregates API | Rate limits; `adjusted` flag vs TV “adjustment” may differ |
| **Alpha Vantage** | Useful for daily fallbacks in many setups | Intraday may need a separate integration; confirm docs for your build |
| **TradingView export** | Ground truth for *your* chart settings | Manual; use same symbol, session, timezone as live |

## What to wire later (not assumptions about current code)

When you implement intraday research beside TradingView, you’ll typically want:

- **REST or file import for Polygon minute aggregates** (15 / 30 / 60 multiplier) over explicit calendar windows.
- **A small CSV loader** for TradingView “Export chart data” so you can diff OHLC row-by-row.
- **Scanner / bot intervals** declared in one place (e.g. `1m`, `5m`, `15m`, `1h`) and extended only after reconciliation passes.

Until those exist, treat TV export + manual comparison as the authority.

## Reconciliation procedure (do once per symbol before trusting automation)

1. **Freeze chart settings on TV:** Symbol, exchange, session template (**Regular trading hours** unless you explicitly trade extended), timeframe (**15m** or **60m**), timezone (**America/New_York** recommended).
2. **Pick 10–20 consecutive bars** (same calendar span in Python and TV).
3. **Export from TradingView** (chart → Export chart data…) or note OHLC **timestamp (bar close)** + O/H/L/C for those bars.
4. **Pull the same window from Python** using whatever intraday feed you adopt (yfinance, Polygon, etc.) — same adjustment policy as the chart.
5. **Join on UTC-normalized bar-close time** (TV often prints exchange-local; convert explicitly).
6. **Tolerance:**
   - **Timestamps:** must align exactly after TZ normalization (if off by one bar, check DST/session).
   - **Prices:** adjusted charts vs adjusted APIs should match within rounding (**≤ 0.02%** or **1 tick** is a reasonable first tolerance for equities; document exceptions).
   - **Volume:** may differ across vendors; note vendor policy — don’t tune exits on volume if feeds disagree.

## Checklist (copy into playbook appendix)

- [ ] TV session = RTH (or documented extended).
- [ ] TV timezone documented (America/New_York).
- [ ] Python provider documented (yfinance / Polygon / export-only).
- [ ] 10–20 bars compared; largest OHLC deviation recorded: **____**
- [ ] Adjustment policy (adjusted vs unadjusted) matched: **____**
