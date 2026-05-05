# Survivorship Bias in Backtesting and Index Reconstruction

Research on survivorship bias, point-in-time data, data sources, and implementation for a Python trading bot.

---

## 1. The Problem: Survivorship Bias

**Survivorship bias** occurs when backtests use only **current** index constituents, ignoring companies that were removed due to:

- Bankruptcy (e.g., Lehman Brothers, Enron)
- Mergers and acquisitions
- Poor performance / delisting
- Ticker changes and restructurings

### Impact on Backtests

| Effect | Description |
|-------|-------------|
| **Inflated returns** | Excluding underperformers and bankruptcies overstates historical performance. |
| **Understated risk** | Volatility and drawdowns are underestimated. |
| **Invalid conclusions** | Strategy effectiveness may be misleading or invalid. |

**Example:** A strategy that buys and holds the current S&P 500 constituents from 2014 appears to beat RSP (equal-weight S&P 500 ETF) by a large margin—because it uses constituents known only in hindsight. In reality, you would not have known those constituents in 2014.

---

## 2. Historical Index Membership

To avoid survivorship bias, you need **point-in-time** index membership:

- **Additions:** When each stock entered the index (effective date).
- **Removals:** When each stock left (effective date and reason if available).
- **Re-entries:** Stocks that left and later returned.

Index composition changes:

- **Quarterly/annual rebalancing** (scheduled).
- **Ad-hoc changes** for mergers, acquisitions, bankruptcies.

Using today’s constituents to represent the past is incorrect; you must know who was in the index on each historical date.

---

## 3. Point-in-Time Data and Look-Ahead Bias

### Point-in-Time (PIT) vs. Lagged/Revised Data

| Concept | Description |
|--------|-------------|
| **PIT data** | Stamped with the date information was **publicly disclosed** (filing or press release). Answers: “When was this known?” and “What was known at that time?” |
| **Non-PIT data** | Stamped with fiscal period end date. Historical values are often overwritten with restatements. |

### Look-Ahead Bias

Using information that was not available at the time of the decision:

- **Revised data:** Using restated financials instead of originally reported values.
- **Index membership:** Including stocks added to the index after the backtest date.
- **Fundamentals:** Using data before it was filed and publicly available.

### Reporting Lag for Fundamentals

- **SEC 10-Q:** Typically 40–45 days after quarter end (varies by public float).
- **Common rule of thumb:** Assume fundamentals are usable **90 days after quarter end** to be conservative and cover late filers.
- **PIT providers** (e.g., Zacks) capture data within 24 hours of disclosure and keep “as originally reported” archives separate from restatements.

---

## 4. Data Sources

### Free / Low-Cost Sources

| Source | Coverage | Notes |
|--------|----------|-------|
| **iShares Core S&P 500 ETF (IVV)** | Monthly holdings since ~2006 | Scrape holdings from iShares; proxy for index membership. |
| **GitHub: fja05680/sp500** | Historical components since 1996 | MIT license; CSV “S&P 500 Historical Components & Changes”. |
| **GitHub: hanshof/sp500_constituents** | Jan 1996–present | CSV with current and historical membership. |
| **datasets/s-and-p-500-companies** | Current + dates added | `constituents.csv` with symbols, sectors, dates added. |
| **Analyzing Alpha** | CC license | Free CSVs: constituents list + historical changes. |
| **Wikipedia** | Current + some history | Scrape “List of S&P 500 companies”; limited historical depth. |

**Limitations of free sources:**

- Delisted/renamed tickers often missing.
- Ticker mapping (e.g., GOOG → GOOGL) may require manual mapping.
- ETF holdings are a proxy, not official index membership.
- No official S&P committee dates; may lag or differ slightly.

### Paid Sources

| Provider | Features | Pricing (approx.) |
|----------|----------|--------------------|
| **CRSP** | Academic/research; survivorship-free, PIT fundamentals | Subscription (often via university). |
| **Norgate Data** | Survivorship-free, delisted data, PIT index membership | Platinum/Diamond for full US survivorship-free. |
| **Siblis Research** | Historical index constituents & changes, global indices | $48/mo (annual) or $97/mo. |
| **S&P Capital IQ / Refinitiv** | PIT fundamentals, index membership | Enterprise. |
| **Zacks** | PIT fundamental data within 24h of disclosure | Subscription. |

---

## 5. Mitigation Strategies

### 5.1 Index Membership

1. **Use historical constituent lists** with entry/exit dates, not only current constituents.
2. **Filter by date:** For each backtest date `t`, use only stocks that were in the index on `t`.
3. **Handle ticker changes:** Map old symbols (e.g., GOOG) to current symbols for price lookups.

### 5.2 Price Data

1. **Include delisted stocks:** Use data that includes bankruptcies and acquisitions.
2. **Adjust for corporate actions:** Splits, dividends, spin-offs.
3. **Ticker mapping:** Maintain a mapping table for renames (e.g., BF.B → BF_B for Yahoo).

### 5.3 Fundamentals (if used)

1. **Apply reporting lag:** Use fundamentals only 90 days after quarter end (or provider’s filing date).
2. **Prefer PIT data:** Use “as originally reported” when available.
3. **Avoid restatements:** Do not overwrite historical values with later corrections.

### 5.4 Backtest Logic

1. **Point-in-time universe:** At each bar, universe = constituents as of that date.
2. **No future information:** No signals or data from dates after the current bar.
3. **Realistic execution:** Account for announcement vs. effective date of index changes.

---

## 6. Implementation Approach for a Python Trading Bot

### 6.1 Data Layer

```
data/
├── indices/
│   ├── sp500.json              # Current constituents (existing)
│   └── sp500_historical/       # NEW: point-in-time membership
│       ├── constituents.csv   # ticker, date_added, date_removed
│       └── changes.csv         # date, added[], removed[]
├── ticker_mapping.json         # Old ticker -> current ticker for price lookup
└── ...
```

### 6.2 Constituent Schema (Historical)

```python
# constituents.csv
# ticker,date_added,date_removed
# AAPL,1982-11-30,
# LEH,1991-05-31,2008-09-12
# GOOG,2006-04-03,2014-04-03  # Class A split to GOOGL
```

### 6.3 Core Functions

```python
def get_constituents_as_of(date: pd.Timestamp, index_id: str = "sp500") -> list[str]:
    """Return tickers that were in the index on the given date."""
    # Load historical constituents, filter: date_added <= date < date_removed
    ...

def resolve_ticker_for_price(ticker: str, as_of_date: pd.Timestamp) -> str:
    """Map historical ticker to symbol used for price lookup (handles renames)."""
    # Use ticker_mapping.json
    ...
```

### 6.4 Backtest Integration

1. **Universe selection:** For each bar date `t`, call `get_constituents_as_of(t)`.
2. **Price fetch:** Use `resolve_ticker_for_price(ticker, t)` before fetching OHLCV.
3. **Signal generation:** Only use stocks in the universe for that bar.
4. **Existing backtest:** `src/backtest.py` already iterates bar-by-bar; add universe filter at each step.

### 6.5 Data Acquisition Options

| Option | Effort | Quality | Best For |
|--------|--------|---------|----------|
| **A. GitHub CSVs** | Low | Good | Quick start, 1996+ |
| **B. iShares IVV scrape** | Medium | Proxy | 2006+; no official S&P dates |
| **C. Siblis / Norgate** | Low (paid) | High | Production, full history |
| **D. CRSP** | Medium (academic) | Highest | Research, PIT fundamentals |

### 6.6 Suggested Phased Approach

1. **Phase 1:** Add `get_constituents_as_of()` using a free CSV (e.g., fja05680/sp500). Use current `sp500.json` as fallback when date is after last historical date.
2. **Phase 2:** Add ticker mapping for common renames; handle missing/delisted tickers gracefully (skip or use last known price).
3. **Phase 3:** If using fundamentals, add 90-day lag and consider PIT provider.
4. **Phase 4:** For production, evaluate Siblis or Norgate for official membership and delisted data.

---

## 7. References

- [Teddy Koker: Creating a Survivorship Bias-Free S&P 500 Dataset with Python](https://teddykoker.com/2019/05/creating-a-survivorship-bias-free-sp-500-dataset-with-python/) — IVV holdings + Quandl WIKI + Yahoo; free approach.
- [S&P Global: Point-In-Time vs. Lagged Fundamentals](https://www.spglobal.com/market-intelligence/en/news-insights/research/point-in-time-vs-lagged-fundamentals)
- [Siblis Research: Historical Index Constituents & Component Changes](https://siblisresearch.com/data/historical-component-changes/)
- [Norgate Data: Survivorship bias-free backtesting](https://norgatedata.com/)
- [GitHub: fja05680/sp500](https://github.com/fja05680/sp500) — S&P 500 Historical Components & Changes CSV
