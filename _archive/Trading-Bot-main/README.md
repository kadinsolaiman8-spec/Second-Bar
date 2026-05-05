# DTB

> **This project is archived.** After five rounds of rigorous statistical validation, no strategy produced a detectable edge over buy-and-hold. Development has stopped. The code and results are preserved here as a case study in honest quantitative strategy validation.

**DTB** was a Discord trading bot that scanned stocks and ETFs for technical signals using a weighted consensus of 11 indicators. It included walk-forward optimization, stationary bootstrap significance testing, and cross-sectional excess Sharpe analysis. The validation infrastructure worked exactly as intended — it correctly identified that the strategies had no edge.

---

## What We Learned

### Mean-Reversion (MR) on Large-Cap Indices

Weighted consensus of RSI, MACD, Bollinger Bands, SuperTrend, Stochastic, Williams %R, EMA, VWAP, OBV, and CMF. Tested on SPY, QQQ, IWM over 10 years of daily data.

- **Result: Genuine negative.** 340 effective independent observations, p=0.50, ~80% statistical power.
- The strategy's returns sit at the median of the bootstrap null distribution — positive Sharpe comes entirely from market drift, not timing skill.
- Reduced param grid from 48 to 4 combinations to minimize overfitting. Still no edge.

### Trend-Following (TF) Donchian Breakouts

55-period Donchian channel breakouts with ATR trailing stops. Tested on 23 ETFs across equities, commodities, bonds, currencies, and international markets.

- **Result: Significantly underperforms buy-and-hold.** Mean excess Sharpe: -0.177, t=-3.23, p=0.998.
- Only 6 of 23 tickers beat buy-and-hold. Winners were commodities (USO), EM (EEM), and bonds (IEF).
- Post-hoc filter to 12 non-equity tickers: mean excess Sharpe still negative (-0.058, t=-0.78).

### Cross-Sectional Momentum (XSMOM)

Researched but rejected before implementation. MTUM, a $17B professionally managed momentum ETF by BlackRock, has returned 15.62% annualized vs SPY's 15.83% over 10 years with higher volatility. If BlackRock can't extract meaningful alpha from momentum at the stock/ETF level, a retail indicator-based approach won't either.

### VIX Regime Filter

VIX-based regime detection (bull/bear/mixed) was implemented and tested. Re-running all validations with the regime filter applied made no material difference to any result.

### Why This Matters

Most trading bot repositories publish backtests that "work" because they were never subjected to proper statistical testing. This project built the testing infrastructure first, applied it honestly, and accepted the results. The validation pipeline — walk-forward optimization with plateau selection, stationary bootstrap on concatenated OOS bar returns, Deflated Sharpe Ratio, Probabilistic Sharpe Ratio, cross-sectional excess Sharpe t-tests, and bar-permutation Monte Carlo — is the most valuable part of this codebase.

---

## Features

The bot was fully functional as a Discord application with 10 slash commands:

| Command | Description |
|---------|-------------|
| `/recap` | S&P 100 market recap (top Buy/Sell by confidence) |
| `/market` | Recap for any index by name or country (S&P 500, DAX, FTSE 100, etc.) |
| `/stock` | Single-ticker rundown with plain-English indicator summary |
| `/stockchart` | Stock chart with signal overlay |
| `/daytrade` | Real-time ATR-based stop-loss and take-profit levels |
| `/news` | Market or ticker-specific news with VADER sentiment scoring |
| `/indicatorbacktest` | Backtest with Simple or Walk-forward mode (Daily/1W/1H) |
| `/watchlist` | Per-user ticker lists (add/remove/list/recap/news) |
| `/stop` | Cancel long-running operations |
| `/tutorial` | How the bot works |

Additional features:
- Auto-recap every 30 minutes during US market hours
- Multi-strategy system: mean-reversion, trend-following, hybrid
- VIX-based regime detection (bull/bear/mixed via ensemble)
- 3-tier config resolution (ticker > asset class > base defaults)
- Data redundancy (yfinance primary; Alpha Vantage and Polygon fallback)
- Async recap queue for rate limit management

---

## Architecture

### Signal Pipeline

```
src/data.py              Fetch OHLCV (yfinance, Alpha Vantage, Polygon fallback)
      |
src/indicators.py        Compute raw TA values (RSI, MACD, BB, SuperTrend, etc.)
      |
src/indicator_scores.py  Weighted buy/sell consensus (single source of truth)
      |
src/signals.py           Signal dataclass: Buy/Sell/Hold + confidence 1-100
      |
src/recap.py             Format Discord embeds for multi-stock recaps
src/stock.py             Format Discord embeds for single-stock commands
```

### Config Resolution

`src/config_resolver.py` implements a 3-tier hierarchy resolved on every signal evaluation:

1. **Ticker profile** — per-ticker overrides (e.g., SPY uses hybrid strategy)
2. **Asset-class profile** — per-class defaults (sector ETF, broad ETF, single stock)
3. **Base config** — `config.yaml` defaults

### Validation Infrastructure

| Component | Location | Purpose |
|-----------|----------|---------|
| Walk-forward optimization | `src/walk_forward.py` | Train/test fold splitting, param grid optimization |
| Plateau selection | `src/walk_forward.py` | Selects stable param combos (best neighbor-average, not best absolute) |
| Stationary bootstrap | `src/walk_forward.py` | Primary significance test on OOS bar returns |
| Deflated Sharpe Ratio | `src/walk_forward.py` | Adjusts for multiple testing within WFO |
| Probabilistic Sharpe Ratio | `src/walk_forward.py` | IID-assuming complement to bootstrap |
| Bar-permutation Monte Carlo | `src/run_wfo.py` | Secondary test: shuffled-label null distribution |
| Cross-sectional excess Sharpe | `scripts/pool_validation.py` | Portfolio-level t-test: TF Sharpe minus B&H Sharpe |
| Multiple testing correction | `src/stats_utils.py` | Benjamini-Hochberg FDR + Bonferroni |
| Pool validation | `scripts/pool_validation.py` | Multi-ticker portfolio bootstrap + per-asset breakdown |

### Other Modules

| Module | Purpose |
|--------|---------|
| `src/backtest.py` | Bar-by-bar backtest engine with stops and take-profits |
| `src/signals_trend.py` | Donchian + ATR trailing stop (trend-following strategy) |
| `src/hybrid.py` | Combined MR+TF signal voting |
| `src/regime.py` | VIX-based regime classification |
| `src/charts.py` | mplfinance chart generation |
| `src/news.py` | yfinance headlines + VADER sentiment |
| `src/expert.py` | Config-driven expert sentiment indicator |
| `src/watchlist.py` | Supabase-backed per-user ticker lists |
| `src/daytrade.py` | Intraday ATR-based stop/take-profit levels |
| `src/indices.py` | Index constituent lists |
| `src/recap_queue.py` | Async queue for sequential recap processing |
| `src/stop.py` | Cancellation via `StopRequested` exception |

---

## Setup

If you want to explore or learn from the codebase:

### Requirements

```bash
pip install -r requirements.txt
```

### Environment

```bash
cp .env.example .env
# Edit .env:
#   DISCORD_BOT_TOKEN=your_token_here       (required)
#   SUPABASE_URL=...                         (optional, for watchlist)
#   SUPABASE_KEY=...                         (optional, for watchlist)
#   ALPHA_VANTAGE_API_KEY=...                (optional, data fallback)
#   POLYGON_API_KEY=...                      (optional, data fallback)
```

### Run

```bash
python main.py
```

### Tests

```bash
python -m pytest tests/ -v
```

### Walk-Forward Optimization

```bash
# Single ticker
python src/run_wfo.py SPY --period 10y --timeframe Daily

# Pool validation (23-ticker cross-sectional test)
python scripts/pool_validation.py --strategy tf --expanded --period 10y
```

See `docs/WFO_COMMANDS.md` and `docs/wfo_batches/BEGINNER_EXACT_STEPS.md` for full usage.

---

## Documentation

| Document | Description |
|----------|-------------|
| `docs/STRATEGY_AND_EDGE.md` | Entry/exit logic, signal consensus, edge analysis |
| `docs/WALK_FORWARD_OPTIMIZATION_RESEARCH.md` | WFO implementation: plateau selection, DSR, PBO, bootstrap |
| `docs/BAR_LEVEL_RETURNS_RESEARCH.md` | How bar-level P&L is computed for Sharpe ratios |
| `docs/MONTE_CARLO_PERMUTATION_RESEARCH.md` | Bar-permutation labeling approach |
| `docs/MULTI_TIMEFRAME_RESEARCH.md` | Multi-timeframe indicator logic |
| `docs/SURVIVORSHIP_BIAS_RESEARCH.md` | Point-in-time data considerations |
| `docs/EXPERT_SENTIMENT_SETUP.md` | Expert indicator configuration |
| `docs/LIVE_BACKTEST_STOP_PARITY.md` | Stop/TP parity between backtest and live signals |
| `docs/CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md` | External AI review of methodology |
| `docs/wfo_batches/` | Pre-registration templates and validation batch methodology |
| `Roadmap` | Full 7-phase development plan and where it stopped |

---

## Tech Stack

- **Python 3.13** with asyncio
- **py-cord** — Discord bot with slash commands
- **yfinance** — OHLCV data (Alpha Vantage and Polygon as fallbacks)
- **ta** — Technical indicators
- **mplfinance** — Candlestick charts with indicator overlays
- **vaderSentiment** — News sentiment scoring
- **scipy** — Statistical tests (bootstrap, t-tests, Wilcoxon)
- **arch** — Stationary bootstrap block length estimation
- **supabase** — Watchlist persistence (optional)

---

## Disclaimer

This bot was for educational and informational purposes only. It does not constitute financial advice. The validation results demonstrate that the strategies tested have no detectable edge over buy-and-hold.
