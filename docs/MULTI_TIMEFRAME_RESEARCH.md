# Multi-Timeframe Signal Check – Research Summary

**Approach B:** When daily signal has high confidence (e.g., 70%+), fetch additional timeframes (1h, 1wk), compute signals on each, and display which timeframes align.

---

## (a) Data Flow Diagram

```
/stock ticker
    │
    ▼
run_stock(ticker)
    │
    ├─► fetch_ohlcv(symbols=[ticker], period=PERIOD, interval="1d")  [data.py]
    │       │
    │       └─► yf.download(batch, period, interval, ...) → dict[symbol → DataFrame]
    │
    ├─► df = ohlcv[ticker]   [DataFrame: Open, High, Low, Close, Volume]
    │
    ├─► get_latest_indicators(df, rsi_period=14, macd_*, bb_*, ...)  [indicators.py]
    │       │
    │       └─► Requires min_len = max(14,26,20,10,14,14,21,34) + 15 = 49 bars
    │       └─► Returns dict: rsi, macd_hist, bb_*, close, supertrend_*, stoch_*, williams_r, ema_*, atr_*
    │
    ├─► evaluate_signal(df, ticker, rsi_oversold=35, rsi_overbought=65, ...)  [signals.py]
    │       │
    │       └─► get_latest_indicators(df, ...)  [called again inside]
    │       └─► compute_weighted_scores(indicators, config)  [indicator_scores.py]
    │       └─► Returns Signal(symbol, signal_type, confidence, rsi, macd_hist, price, atr_pct, ...)
    │
    ├─► get_stock_exchange(ticker)  [data.py]
    │
    └─► format_stock_embed(display_ticker, signal, indicators, config, markets)  [stock.py]
            │
            └─► compute_weighted_scores(indicators, config)  [called again]
            └─► Returns dict: title, description, color, footer
```

**Interval/period dependencies:**
- `fetch_ohlcv`: `period`, `interval` → passed to `yf.download`
- `get_latest_indicators`: no interval param; only needs enough rows (min_len ≈ 49)
- `evaluate_signal`: no interval param; operates on any OHLCV DataFrame
- `format_stock_embed`: no interval param; receives Signal + indicators
- `main.py PERIOD`: from `PERIOD_MAP` based on `DATA_PERIOD_DAYS` (30→3mo, 60→3mo, 90→6mo)

---

## (b) yfinance Period/Interval Matrix

| Interval | Valid Periods | Max Bars (approx) | Notes |
|----------|--------------|-------------------|-------|
| **1d**   | 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max | ~252/year | Current default; PERIOD="3mo" → ~63 bars |
| **1h**   | 1d, 5d, 1mo, 3mo (capped at 60 days) | ~390 (60 days × 6.5h/day) | **Intraday max 60 days** |
| **1wk**  | 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max | ~52/year | No intraday limit |

**Valid period strings (yfinance 0.2.32+):** `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y`, `2y`, `5y`, `10y`, `ytd`, `max`

**Recommended pairs for multi-TF:**
- **1h:** yfinance valid periods exclude "60d". Use `start`/`end` with 60 days back, or `period="1mo"` → ~22 trading days × 6.5h ≈ **143 bars** (enough for min_len=49).
- **1wk:** `period="2y"` → ~104 bars (safer than 1y’s ~52 bars for warmup).

**Indicator warmup:** `min_len = 49` bars (from `indicators.py` line 226–230). All three timeframes need ≥49 bars.

---

## (c) File-by-File Change List

### `src/data.py`
- **No signature change** to `fetch_ohlcv`; it already accepts `period` and `interval`.
- **Optional:** Add `fetch_ohlcv_multi_interval(symbol, intervals: list[tuple[str, str]])` that returns `dict[(period, interval) → DataFrame]` for parallel fetches, or keep using `fetch_ohlcv` / `fetch_single` with different args.

### `main.py` – `run_stock()`
1. After daily `evaluate_signal`, add threshold check:
   ```python
   if signal.confidence >= MULTI_TF_THRESHOLD and signal.signal_type in ("Buy", "Sell"):
   ```
2. If true, fetch 1h and 1wk in parallel (e.g. `concurrent.futures` or `asyncio.gather`).
3. For each extra timeframe: `get_latest_indicators` → `evaluate_signal`.
4. Build `multi_tf_signals: list[tuple[str, Signal]]` e.g. `[("1h", sig_1h), ("1wk", sig_1wk)]`.
5. Pass `multi_tf_signals` to `format_stock_embed`.

### `src/signals.py`
- **No changes.** `evaluate_signal` is interval-agnostic; it works on any OHLCV DataFrame.

### `src/stock.py` – `format_stock_embed()`
1. Add optional param: `multi_tf_signals: list[tuple[str, Signal]] | None = None`.
2. If `multi_tf_signals`:
   - Build multi-TF line (see embed layout below).
   - Add to embed via new field or append to description.

### `main.py` – embed construction
- If `format_stock_embed` returns `fields`, iterate and call `embed.add_field(...)` for each.

---

## (d) Proposed Embed Layout

**Option A – New embed field (recommended)**  
Add a field so the main description stays focused:

```
┌─ Multi-Timeframe Alignment ─────────────────────┐
│ Daily: Buy 78%  |  1W: Buy 65%  |  1H: Hold     │
└──────────────────────────────────────────────────┘
```

- **Name:** `"Multi-Timeframe Alignment"`
- **Value:** `"Daily: Buy 78% | 1W: Buy 65% | 1H: Hold"`
- **Inline:** `False`

**Option B – Append to description**  
Add a line before the checkmarks:

```
...Net score: +2.1 (buy-leaning)
Daily range: 1.2%
Buy: RSI (1.5), BB (1.0) | Sell: ...

**Timeframes:** Daily: Buy 78% | 1W: Buy 65% | 1H: Hold

• Momentum oversold (may bounce)
...
```

**Option C – Footer**  
Append to footer: `" | Daily 78% | 1W 65% | 1H Hold"` — may get crowded with timestamp and exchange.

**Recommendation:** Option A (new field). Keeps structure clear and avoids long descriptions.

**Format string:**  
`"{tf}: {signal_type} {confidence}%"` for Buy/Sell, `"{tf}: Hold"` for Hold.  
Example: `"Daily: Buy 78% | 1W: Buy 65% | 1H: Hold"`.

---

## (e) Latency and Parallelization

**Current flow:** 1 fetch (daily) → indicators → signal → embed.  
**Multi-TF flow:** 1 fetch (daily) → indicators → signal → **if threshold:** 2 more fetches (1h, 1wk) → indicators → signals → embed.

**Latency:**
- 3 sequential fetches: ~1.5–3 s (yfinance ~0.5–1 s per request).
- 2 parallel fetches (1h + 1wk): ~0.5–1 s extra.
- Total: ~1.5–2 s for high-confidence signals.

**Parallelization:**
- Use `concurrent.futures.ThreadPoolExecutor` or `asyncio.gather` for 1h and 1wk.
- `run_stock` already runs in `_executor`; inside it, use `ThreadPoolExecutor.submit` for both fetches, then `future.result()` for each.
- Example:
  ```python
  with ThreadPoolExecutor(max_workers=2) as ex:
      f_1h = ex.submit(fetch_ohlcv, symbols=[ticker], period="60d", interval="1h")
      f_1wk = ex.submit(fetch_ohlcv, symbols=[ticker], period="2y", interval="1wk")
      ohlcv_1h = f_1h.result()
      ohlcv_1wk = f_1wk.result()
  ```

**Rate limits:** yfinance has no documented hard limit; 3 requests per `/stock` (1 daily + 2 extra) is reasonable. Avoid batching many symbols for multi-TF.

---

## (f) Config Additions

Add to `config.yaml`:

```yaml
# Multi-timeframe: when daily confidence >= this, fetch 1h and 1wk and show alignment
multi_tf_confidence_threshold: 70

# Only trigger multi-TF for Buy/Sell (not Hold)
multi_tf_signal_types: ["Buy", "Sell"]  # optional; default both
```

**Validation (in `_validate_config`):**
- `multi_tf_confidence_threshold`: 1–100, default 70
- `multi_tf_signal_types`: optional list; if present, must be subset of `["Buy", "Sell"]`

**Threshold logic:**
- Trigger when: `signal.confidence >= multi_tf_confidence_threshold` AND `signal.signal_type in ("Buy", "Sell")`.
- Hold is excluded because multi-TF adds little value when daily is already neutral.

---

## Summary Checklist

| Item | Status |
|------|--------|
| Data flow mapped | ✓ |
| yfinance period/interval matrix | ✓ |
| Min bars for indicators (49) | ✓ |
| 1h: period 60d or 1mo | ✓ |
| 1wk: period 2y | ✓ |
| data.py changes | Minimal (reuse existing) |
| main.py run_stock changes | Add threshold, parallel fetch, pass multi_tf |
| signals.py changes | None |
| stock.py format_stock_embed changes | Add multi_tf param, new field |
| Embed layout | New field "Multi-Timeframe Alignment" |
| Parallelization | 1h + 1wk in parallel |
| Config | multi_tf_confidence_threshold, optional multi_tf_signal_types |
