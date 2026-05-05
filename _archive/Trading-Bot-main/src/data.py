"""
Fetch OHLCV data from Yahoo Finance via yfinance.
Fallback to Alpha Vantage and Polygon when yfinance fails (fetch_single only).
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

import pandas as pd
import yfinance as yf

from src.stocks import get_sp100_tickers

logger = logging.getLogger(__name__)

BATCH_SIZE = 25  # Avoid rate limits

# Standard OHLCV columns expected by downstream
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# VIX cache: (timestamp, value_or_series)
_vix_cache: dict[str, tuple[float, object]] = {}
_VIX_CACHE_TTL = 900  # 15 minutes

# Provider rate limit: last call timestamp per provider
_provider_last_call: dict[str, float] = {}
_provider_lock = threading.Lock()
_PROVIDER_MIN_INTERVAL = 12.0  # default seconds between calls for Alpha Vantage / Polygon
_provider_rate_limits: dict[str, float] = {}  # provider -> min_interval_sec (from config)


def set_provider_config(config: dict) -> None:
    """Set rate limits from config.data_sources.rate_limits. Call at startup."""
    global _provider_rate_limits
    ds = config.get("data_sources", {}) or {}
    rl = ds.get("rate_limits", {}) or {}
    _provider_rate_limits = {k: float(v) for k, v in rl.items() if isinstance(v, (int, float)) and v > 0}


def fetch_ohlcv(
    symbols: list[str] | None = None,
    period: str = "2mo",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV data for given symbols.

    Args:
        symbols: List of ticker symbols. If None, uses S&P 100.
        period: yfinance period (e.g. "2mo", "3mo").
        interval: Candle interval ("1d" for daily).

    Returns:
        Dict mapping symbol -> DataFrame with columns Open, High, Low, Close, Volume.
        Failed symbols are omitted.
    """
    if symbols is None:
        symbols = get_sp100_tickers()

    result: dict[str, pd.DataFrame] = {}

    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i : i + BATCH_SIZE]
        try:
            df = yf.download(
                batch,
                period=period,
                interval=interval,
                group_by="ticker",
                progress=False,
                threads=False,
                auto_adjust=True,
            )
        except Exception as e:
            logger.warning("yfinance batch download failed: %s", e)
            continue

        if df.empty:
            continue

        # With group_by="ticker", columns are (Ticker, OHLCV) for any batch size
        for ticker in batch:
            if ticker not in df.columns.get_level_values(0):
                continue
            try:
                sub = df[ticker].copy()
                if sub is None or sub.empty:
                    continue
                required = ["Open", "High", "Low", "Close", "Volume"]
                if all(c in sub.columns for c in required):
                    result[ticker] = sub[required]
            except (KeyError, TypeError):
                continue

    return result


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame | None:
    """Map provider-specific columns to Open, High, Low, Close, Volume. Returns None if invalid."""
    aliases = {
        "Open": ["Open", "open", "o"],
        "High": ["High", "high", "h"],
        "Low": ["Low", "low", "l"],
        "Close": ["Close", "close", "c", "Adj Close", "adj_close"],
        "Volume": ["Volume", "volume", "v"],
    }
    cols = {}
    for std, alts in aliases.items():
        for alt in alts:
            if alt in df.columns:
                cols[std] = df[alt]
                break
        if std not in cols:
            return None
    out = pd.DataFrame(cols)
    if "Date" in df.columns:
        out.index = pd.to_datetime(df["Date"])
    elif "date" in df.columns:
        out.index = pd.to_datetime(df["date"])
    out = out.sort_index()
    return out[OHLCV_COLUMNS]


def _throttle_provider(provider: str) -> None:
    """Enforce min interval between calls for rate-limited providers."""
    interval = _provider_rate_limits.get(provider, _PROVIDER_MIN_INTERVAL)
    with _provider_lock:
        now = time.time()
        last = _provider_last_call.get(provider, 0)
        wait = max(0.0, interval - (now - last))
        if wait > 0:
            time.sleep(wait)
        _provider_last_call[provider] = time.time()


def _fetch_alpha_vantage(symbol: str, period: str) -> pd.DataFrame | None:
    """Fetch daily OHLCV from Alpha Vantage. Returns None on failure or missing key."""
    key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not key or not key.strip():
        return None
    _throttle_provider("alpha_vantage")
    try:
        url = "https://www.alphavantage.co/query?" + urlencode({
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "apikey": key,
            "outputsize": "compact",
        })
        req = Request(url, headers={"User-Agent": "DiscordTradingBot/1.0"})
        with urlopen(req, timeout=20) as resp:
            data = __import__("json").load(resp)
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            return None
        rows = []
        for date_str, v in ts.items():
            rows.append({
                "Date": date_str,
                "Open": float(v.get("1. open", 0)),
                "High": float(v.get("2. high", 0)),
                "Low": float(v.get("3. low", 0)),
                "Close": float(v.get("5. adjusted close", v.get("4. close", 0))),
                "Volume": int(float(v.get("6. volume", 0))),
            })
        df = pd.DataFrame(rows)
        df = df.sort_values("Date").tail(_period_to_bars(period))
        return _normalize_ohlcv(df)
    except (HTTPError, URLError, KeyError, ValueError) as e:
        logger.warning("Alpha Vantage failed for %s: %s", symbol, e)
        return None


def _fetch_polygon(symbol: str, period: str) -> pd.DataFrame | None:
    """Fetch daily OHLCV from Polygon. Returns None on failure or missing key."""
    key = os.getenv("POLYGON_API_KEY")
    if not key or not key.strip():
        return None
    _throttle_provider("polygon")
    try:
        end = datetime.now()
        start = end - timedelta(days=_period_to_days(period))
        url = (
            f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/"
            f"{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
        )
        url += "?" + urlencode({"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key})
        req = Request(url, headers={"User-Agent": "DiscordTradingBot/1.0"})
        with urlopen(req, timeout=20) as resp:
            data = __import__("json").load(resp)
        results = data.get("results", [])
        if not results:
            return None
        rows = []
        for r in results:
            ts_ms = r.get("t", 0)
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            rows.append({
                "Date": dt.strftime("%Y-%m-%d"),
                "o": float(r.get("o", 0)),
                "h": float(r.get("h", 0)),
                "l": float(r.get("l", 0)),
                "c": float(r.get("c", 0)),
                "v": int(r.get("v", 0)),
            })
        df = pd.DataFrame(rows)
        return _normalize_ohlcv(df)
    except (HTTPError, URLError, KeyError, ValueError) as e:
        logger.warning("Polygon failed for %s: %s", symbol, e)
        return None


def _period_to_days(period: str) -> int:
    """Convert yfinance period string to approximate days."""
    m = {"1mo": 30, "2mo": 60, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}
    return m.get(period, 90)


def _period_to_bars(period: str) -> int:
    """Return max bars to keep for period."""
    return min(_period_to_days(period), 500)


def fetch_single(symbol: str, period: str = "2mo", interval: str = "1d") -> pd.DataFrame | None:
    """
    Fetch OHLCV for a single symbol.
    Tries yfinance first; on failure, falls back to Alpha Vantage, then Polygon.
    Returns None if all providers fail.
    """
    data = fetch_ohlcv([symbol], period=period, interval=interval)
    df = data.get(symbol)
    if df is not None and not df.empty:
        return df

    # Fallback only for daily data (Alpha Vantage and Polygon daily endpoints)
    if interval != "1d":
        return None

    for name, fetcher in [("alpha_vantage", _fetch_alpha_vantage), ("polygon", _fetch_polygon)]:
        try:
            df = fetcher(symbol, period)
            if df is not None and not df.empty:
                logger.info("Fallback provider %s succeeded for %s", name, symbol)
                return df
        except Exception as e:
            logger.warning("Fallback %s failed for %s: %s", name, symbol, e)
    return None


# Exchange code -> display name for stock embed
_EXCHANGE_NAMES = {
    "NMS": "NASDAQ",
    "NYQ": "NYSE",
    "NCM": "NASDAQ CM",
    "PCX": "NYSE Arca",
    "NGM": "NASDAQ GM",
    "ASE": "NYSE American",
    "BTS": "BATS",
    "XETRA": "XETRA",
    "LSE": "London",
    "L": "London",
    "PA": "Euronext Paris",
    "TO": "Toronto",
    "SW": "SIX Swiss",
    "MC": "Madrid",
    "MI": "Milan",
    "AS": "Euronext Amsterdam",
    "BR": "Euronext Brussels",
}


def get_stock_exchange(symbol: str) -> str:
    """
    Get the exchange/market name for a stock ticker.
    Returns display name (e.g. 'NASDAQ', 'NYSE') or 'Unknown' if not found.
    """
    # Infer from ticker suffix (e.g. AAPL.L -> London)
    if "." in symbol:
        suffix = symbol.split(".")[-1].upper()
        return _EXCHANGE_NAMES.get(suffix, suffix)
    try:
        t = yf.Ticker(symbol)
        info = t.info
        if not info:
            return "Unknown"
        # Try exchange or exchangeShortName
        code = info.get("exchange") or info.get("exchangeShortName") or ""
        if code:
            return _EXCHANGE_NAMES.get(str(code).upper(), str(code))
        return "Unknown"
    except Exception:
        return "Unknown"


def fetch_vix_current() -> float | None:
    """Fetch current VIX value. Cached for 15 minutes."""
    now = time.time()
    cached = _vix_cache.get("current")
    if cached and (now - cached[0]) < _VIX_CACHE_TTL:
        return cached[1]
    try:
        # Use Ticker.history() — more robust than yf.download() for index symbols
        # (avoids intermittent KeyError('chart') from the download endpoint)
        hist = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if hist is None or hist.empty:
            logger.warning("VIX current: empty or missing history from yfinance (^VIX 5d)")
            return None
        val = float(hist["Close"].dropna().iloc[-1])
        _vix_cache["current"] = (now, val)
        return val
    except Exception as e:
        logger.warning("Failed to fetch VIX: %s", e)
        return None


def _vix_ticker_history(period: str, interval: str) -> "pd.DataFrame":
    """
    Fetch ^VIX via Ticker.history() using explicit start/end for year-based periods.

    yf.Ticker("^VIX").history(period="10y") silently returns empty data for long
    period strings on index symbols. Converting to explicit start/end dates bypasses
    this while keeping Ticker.history() (which avoids the KeyError('chart') bug in
    yf.download()).
    """
    import datetime

    _YEAR_PERIODS = {"1y": 1, "2y": 2, "3y": 3, "5y": 5, "10y": 10}
    years = _YEAR_PERIODS.get(period)
    if years:
        today = datetime.date.today()
        try:
            start = today.replace(year=today.year - years)
        except ValueError:  # handles Feb 29 in non-leap source year
            start = today.replace(year=today.year - years, day=28)
        return yf.Ticker("^VIX").history(
            start=str(start), end=str(today), interval=interval
        )
    return yf.Ticker("^VIX").history(period=period, interval=interval)


def fetch_vix_series(period: str = "5y", interval: str = "1d") -> pd.Series | None:
    """Fetch historical VIX close series for backtesting. Cached for 15 minutes."""
    cache_key = f"series_{period}_{interval}"
    now = time.time()
    cached = _vix_cache.get(cache_key)
    if cached and (now - cached[0]) < _VIX_CACHE_TTL:
        return cached[1]
    try:
        hist = _vix_ticker_history(period, interval)
        if hist is None or hist.empty:
            logger.warning(
                "VIX series: empty or missing history (period=%s interval=%s)",
                period,
                interval,
            )
            return None
        series = hist["Close"].dropna()
        if series.index.tz is not None:
            series.index = series.index.tz_localize(None)
        series.name = "VIX"
        _vix_cache[cache_key] = (now, series)
        return series
    except Exception as e:
        logger.warning("Failed to fetch VIX series: %s", e)
        return None


# ---------------------------------------------------------------------------
# Fundamentals & bond yield (for Graham intrinsic value display)
# ---------------------------------------------------------------------------

_bond_yield_cache: tuple[float, float] | None = None  # (timestamp, yield_decimal)
_BOND_YIELD_CACHE_TTL = 3600  # 1 hour


def fetch_fundamentals(symbol: str) -> dict | None:
    """Fetch EPS and growth rate from yfinance info.

    Returns {'eps': float, 'growth_rate': float} or None.
    """
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        eps = info.get("trailingEps")
        if eps is None or not isinstance(eps, (int, float)) or eps <= 0:
            return None
        # Prefer earningsGrowth (trailing), fall back to revenueGrowth
        growth = info.get("earningsGrowth")
        if growth is None or not isinstance(growth, (int, float)):
            growth = info.get("revenueGrowth")
        if growth is None or not isinstance(growth, (int, float)):
            growth = 0.0
        return {"eps": float(eps), "growth_rate": float(growth)}
    except Exception as e:
        logger.debug("Failed to fetch fundamentals for %s: %s", symbol, e)
        return None


def fetch_bond_yield() -> float | None:
    """Fetch current 10-year Treasury yield as a proxy for AAA bond yield.

    Returns decimal (e.g. 0.045 for 4.5%) or None if unavailable.
    Cached for 1 hour.
    """
    global _bond_yield_cache
    now = time.time()
    if _bond_yield_cache and (now - _bond_yield_cache[0]) < _BOND_YIELD_CACHE_TTL:
        return _bond_yield_cache[1]
    try:
        t = yf.Ticker("^TNX")
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return None
        last_close = float(hist["Close"].dropna().iloc[-1])
        yield_decimal = last_close / 100.0  # ^TNX is in percentage points
        _bond_yield_cache = (now, yield_decimal)
        return yield_decimal
    except Exception as e:
        logger.debug("Failed to fetch bond yield: %s", e)
        return None
