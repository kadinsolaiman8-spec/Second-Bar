# Strategy Architecture and Edge — Response to Critique

This document describes the bot’s entry/exit logic in detail and addresses the critique that WFO is correctly showing there is no edge on SPY. It also outlines planned improvements to better search for a genuine edge.

---

## Entry/Exit Logic (Beyond RSI)

The strategy is **not** RSI-only. It uses a **weighted consensus** of eight indicator slots:

| Slot | Buy condition | Sell condition |
|------|---------------|-----------------|
| **RSI** | RSI < oversold (35) | RSI > overbought (65) |
| **Bollinger Bands** | Price ≤ lower band | Price ≥ upper band |
| **Trend** (MACD + SuperTrend) | MACD hist > 0 and SuperTrend bullish | MACD hist < 0 and SuperTrend bearish |
| **Stochastic** | %K < 20 (oversold) | %K > 80 (overbought) |
| **Williams %R** | < -80 (oversold) | > -20 (overbought) |
| **EMA** | Fast > slow (bullish) | Fast < slow (bearish) |
| **Volatility** (optional) | ATR % below recent avg (low vol) | ATR % above recent avg (high vol) |
| **News** (optional) | Sentiment > 0.1 | Sentiment < -0.1 |

**Net score:** `buy_weight - sell_weight` across all slots. Each slot contributes its configured weight (e.g. RSI 1.5, trend 1.5, etc.).

**Entry:** When `net_score ≥ min_net_score` (default 0.5) → **Buy**. Fill at **next bar open**.

**Exit:** When `net_score ≤ -min_net_score` → **Sell**. Fill at **next bar open**.

There are **no stops, no targets, no time-based exits**. The only exit is the opposite signal (Sell). Positions are long-only.

**Confidence (1–100):** `20 + 80 × consensus + extremity_bonus`, where consensus = winning_weight / max_possible_weight, and extremity bonus (up to +5) comes from RSI/MACD alignment. `min_confidence` filters signals in recap; backtest/WFO can use `min_confidence=0` to include all signals.

---

## Why WFO Says There Is No Edge

The critique is correct: **WFO is doing its job**. If `min_confidence=0` wins every fold and still underperforms, the strategy architecture—not just the parameters—likely lacks edge on that asset.

**Core issue:** SPY is one of the most efficient, heavily-traded assets. RSI mean-reversion on daily bars is well-known and largely arbitraged away. The bot is effectively stress-testing on one of the hardest instruments.

---

## Highest-Leverage Changes (Planned / Recommended)

### 1. Change the asset

- Try mid/small-cap stocks, sector ETFs (XLE, XBI), or crypto.
- Less efficient markets give more room for technical signals.
- SPY is a stress test; passing it is unlikely.

### 2. Fix the signal logic

- `min_confidence=0` winning every fold suggests the confidence filter is noisy or inversely useful.
- **Action:** Strip it out and test raw RSI + net_score.
- Fewer, cleaner signals often outperform complex ones.

### 3. Add a regime filter

- Only trade when price is above its 200-day MA (bull regime).
- RSI oversold bounces tend to work better in uptrends; in downtrends they often catch falling knives.

### 4. Change the optimization target

- Currently: optimize for **outperformance** (strategy return − buy & hold return) or **total_return**.
- **Action:** Add options to optimize for **Sharpe ratio** or **return / max drawdown**.
- High-return params in-sample are often high-risk; OOS collapse is common.

### 5. Lengthen the OOS window

- 63 bars (~3 months) is short; 5–8 trades per fold means one bad trade can dominate.
- **Action:** Increase `test_bars` (e.g. 126 or 189) for more statistically meaningful OOS results.

### 6. Rethink the strategy architecture

- If the same params win every fold and still underperform, the architecture may need redesign.
- WFO is correctly showing that the strategy does not generalize; that is useful information.

---

## Current WFO Configuration

- **Train:** 504 bars (~2 years daily)
- **Test:** 63 bars (~3 months)
- **Step:** 63 bars (roll forward 3 months)
- **Param grid:** `rsi_oversold`, `rsi_overbought`, `min_net_score`, `min_confidence`
- **Optimization metric:** outperformance (strategy return − buy & hold return)

---

## WFO validation hierarchy (`python -m src.run_wfo`)

After a walk-forward run, significance testing follows [CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md](CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md):

| Role | Test | Statistic / null |
|------|------|-------------------|
| **Primary** | Stationary bootstrap on **concatenated OOS** strategy bar-P&amp;L | Annualized **Sharpe**; null preserves serial dependence (Politis–Romano) |
| **Secondary** | Bar permutation on **full-history** OHLCV | **Profit factor**; null destroys bar autocorrelation |

**CLI always prints** `walk_forward.optimize_metric` (WFO selection) vs **diagnostic Sharpe** for the primary bootstrap so a bootstrap pass is not mistaken for validating profit-factor selection. **Primary (Sharpe) and secondary (PF) can disagree** — different quantities. For **trend-following**, a bar-permutation **FAIL** is often *expected*; interpret with the primary result and roadmap TF caveat.

**PBO** output is **off** unless `walk_forward.show_pbo: true` in `config.yaml`, because the simplified CSCV implementation assumes a Sharpe-like matrix while folds store `optimize_metric` scores.

---

## Summary

| Topic | Current state | Recommendation |
|-------|---------------|-----------------|
| **Entry** | net_score ≥ min_net_score; fill next bar open | Consider regime filter (e.g. price > 200 MA) |
| **Exit** | net_score ≤ −min_net_score; fill next bar open | No stops/targets; opposite signal only |
| **Confidence** | Used in recap; WFO can use 0 | Test without confidence filter |
| **Asset** | Default SPY / S&P 100 | Try less efficient assets |
| **Optimization target** | Outperformance | Add Sharpe, return/drawdown |
| **OOS window** | 63 bars | Lengthen to 126+ bars |
