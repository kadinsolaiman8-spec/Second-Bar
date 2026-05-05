# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python main.py

# Tests (pytest)
python -m pytest tests/ -v
```

**WFO batch wall time:** `run_wfo_batch.ps1` runs **1000** in-sample + **500** OOS bar-permutation full-history backtests **per ticker** (plus WFO). That is often **multi-day** for the full five-ticker script on a typical PC — not ~15–25 min/ticker. See `docs/wfo_batches/BEGINNER_EXACT_STEPS.md` section **Runtime (WFO batch wall time)**.

There is no linter configuration in-repo. The bot requires a `.env` file with `DISCORD_BOT_TOKEN`. Optional: `SUPABASE_URL` / `SUPABASE_KEY` (watchlist), `ALPHA_VANTAGE_API_KEY`, `POLYGON_API_KEY` (data fallback).

## Architecture

### Entry Point

`main.py` is the entire Discord layer. It registers all slash commands (`/recap`, `/market`, `/stock`, `/watchlist`, `/news`, `/indicatorbacktest`, `/daytrade`, `/stop`, `/tutorial`), manages the auto-recap scheduler (every 30 min when US market is open), and wires up the recap queue worker. All blocking work (yfinance, indicator math) runs in a `ThreadPoolExecutor` via `loop.run_in_executor`.

### Signal Pipeline

Data flows in one direction through these layers:

```
src/data.py          → fetch OHLCV via yfinance; fetch_single falls back to Alpha Vantage/Polygon when yfinance fails
src/indicators.py    → compute raw TA values (RSI, MACD, BB, SuperTrend, Stoch, Williams %R, EMA, ATR)
src/indicator_scores.py → WeightedScores: weighted buy/sell consensus (single source of truth for scoring)
src/signals.py       → Signal dataclass: Buy/Sell/Hold + confidence 1-100
src/recap.py         → format Discord embed for multi-stock recaps
src/stock.py         → format Discord embed for single-stock /stock command
```

`indicator_scores.py` is the canonical place for indicator weighting logic. `signals.py` consumes `WeightedScores` and computes confidence via consensus ratio + extremity bonus (RSI/MACD alignment, capped at +5).

### Config Resolution

`src/config_resolver.py` implements a 3-tier hierarchy resolved on every signal evaluation:

1. **Ticker profile** — explicit per-ticker overrides (`ticker_profiles` in `config.yaml` or set by WFO `--save-profile`)
2. **Asset-class profile** — `asset_class_profiles` keyed by class (`sector_etf`, `broad_etf`, `us_large_cap`, `us_tech`, `european_index`, `single_stock`)
3. **Base config** — `config.yaml` defaults

All callers should use `get_config_for_ticker(symbol, base_config, timeframe, index_id)` rather than reading `config` directly. Nested dicts (e.g. `indicator_weights`) are deep-merged; top-level keys override.

### Weights and Regime Logic

Indicator weights are resolved in `indicator_scores._get_weights()` with this priority:
- Regime weights (`regime_indicator_weights.bull/bear`) when `regime_filter: true` and regime is determined by 200 SMA
- Timeframe weights (`timeframe_indicator_weights.Daily/1W/1H`)
- `indicator_weights` from resolved config

Bear regime suppresses mean-reversion indicators (RSI, Stoch, Williams %R, BB) and boosts trend.

### Concurrency & Cancellation

- All recap/market/watchlist recap jobs go through `src/recap_queue.py` — an `asyncio.Queue` that processes jobs sequentially to avoid rate limits. Initialized at startup via `init_recap_queue()`; worker runs as a background task.
- Long-running operations check `src/stop.py` (`is_stop_requested()`) and raise `StopRequested` to cancel cleanly. `/stop` command calls `request_stop()`; callers clear it with `clear_stop()` after handling.

### Other Modules

- `src/backtest.py` — simple bar-by-bar backtest engine; `run_backtest()` returns `BacktestResult`
- `src/walk_forward.py` — walk-forward optimization (WFO); folds over train/test windows, optimizes across `param_grid`
- `src/signals_trend.py` — separate trend-following strategy (Donchian + ATR trailing stop); used when `strategy: tf`
- `src/charts.py` — mplfinance chart generation for `/stockchart`, `/indicatorbacktest`, `/daytrade`
- `src/news.py` — yfinance headlines + VADER sentiment scoring
- `src/indices.py` — index constituent lists; `get_constituents()` / `resolve_input()` for `/market`
- `src/watchlist.py` — Supabase-backed per-user/per-guild ticker lists
- `src/expert.py` — Expert sentiment (per-ticker); config ticker_profiles only; indicator like news, weighted in consensus
- `src/daytrade.py` — intraday ATR-based stop/take-profit levels
- `src/market_hours.py` — US market open check (9:30–16:00 ET, Mon–Fri)

### Configuration File

`config.yaml` is loaded once at startup and passed as `config` dict throughout. Key tunable sections:
- `indicators` — TA parameter values (RSI periods, thresholds, etc.)
- `indicator_weights` — base weights per indicator slot
- `regime_indicator_weights` — bull/bear weight overrides
- `timeframe_indicator_weights` — per-timeframe weight overrides
- `timeframe_confidence_factors` — confidence scaling per timeframe (1H is noisiest)
- `asset_class_profiles` / `ticker_profiles` — per-class or per-ticker config overrides
- `walk_forward` / `backtest` — backtesting parameters and WFO param grid
- `charts` — mplfinance display settings

### Data Directory

- `data/ticker_profiles.yaml` — saved WFO profiles (keyed by ticker symbol); merged into `config["ticker_profiles"]` at startup
- `docs/indices/` — index constituent data files
