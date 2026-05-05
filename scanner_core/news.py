# news.py — Finnhub API integration, headline cache, keyword sentiment analysis

import logging
import os
import asyncio
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pytz

from scanner_core.config import (
    FINNHUB_API_KEY, MARKET_TIMEZONE,
    SCORE_NEWS_SUPPRESS_WINDOW, SCORE_NEWS_BONUS_WINDOW,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)

# ══════════════════════════════════════════════════════════════════════════
# FINNHUB CLIENT
# ══════════════════════════════════════════════════════════════════════════

_finnhub_client = None


def get_finnhub_client():
    """Lazy-init the Finnhub client."""
    global _finnhub_client
    if _finnhub_client is not None:
        return _finnhub_client
    if not FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not set — news features disabled")
        return None
    try:
        import finnhub
        _finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)
        logger.info("Finnhub client initialized")
        return _finnhub_client
    except ImportError:
        logger.warning("finnhub-python not installed — news features disabled")
        return None
    except Exception as e:
        logger.error(f"Finnhub init error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════
# NEWS CACHE
# ══════════════════════════════════════════════════════════════════════════

# { TICKER: [{"headline": str, "source": str, "time": str, "sentiment": str,
#              "datetime": datetime, "raw_time": int}] }
_news_cache: dict = {}
_cache_expiry: dict = {}   # { TICKER: datetime of last refresh }
CACHE_TTL_MINUTES = 10


def _is_cache_fresh(ticker: str) -> bool:
    exp = _cache_expiry.get(ticker)
    if exp is None:
        return False
    return datetime.now(ET) < exp


# ══════════════════════════════════════════════════════════════════════════
# SENTIMENT KEYWORDS
# ══════════════════════════════════════════════════════════════════════════

BULLISH_KEYWORDS = [
    "beat", "beats", "exceeded", "surpass", "record", "upgrade", "upgrades",
    "buy", "outperform", "raise", "raises", "bull", "bullish", "surge",
    "surges", "soar", "soars", "jump", "jumps", "rally", "rallies",
    "breakout", "positive", "growth", "strong", "profit", "optimistic",
    "upside", "boost", "boosts", "accelerate", "accelerates",
    "momentum", "higher", "gain", "gains", "doubles", "triples",
    "revenue beat", "eps beat", "new high", "all-time high",
]

BEARISH_KEYWORDS = [
    "miss", "misses", "missed", "downgrade", "downgrades", "sell",
    "underperform", "cut", "cuts", "bear", "bearish", "crash",
    "crashes", "plunge", "plunges", "drop", "drops", "decline",
    "declines", "negative", "loss", "losses", "weak", "warning",
    "downside", "slump", "slumps", "layoff", "layoffs",
    "recall", "recalls", "investigation", "lawsuit", "fraud",
    "bankruptcy", "default", "lower", "fall", "falls",
    "revenue miss", "eps miss", "new low", "52-week low",
]


def classify_sentiment(headline: str) -> str:
    """Classify a headline as 'bullish', 'bearish', or 'neutral'."""
    if not headline:
        return "neutral"
    lower = headline.lower()
    bull_score = sum(1 for kw in BULLISH_KEYWORDS if kw in lower)
    bear_score = sum(1 for kw in BEARISH_KEYWORDS if kw in lower)
    if bull_score > bear_score:
        return "bullish"
    if bear_score > bull_score:
        return "bearish"
    return "neutral"


def get_sentiment_emoji(sentiment: str) -> str:
    if sentiment == "bullish":
        return "📈"
    if sentiment == "bearish":
        return "📉"
    return "⚪"


# ══════════════════════════════════════════════════════════════════════════
# FETCH NEWS
# ══════════════════════════════════════════════════════════════════════════

def _fetch_news_sync(ticker: str) -> list:
    """Synchronous Finnhub news fetch — runs in executor."""
    client = get_finnhub_client()
    if client is None:
        return []
    try:
        now = datetime.now(ET)
        from_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")

        raw = client.company_news(ticker, _from=from_date, to=to_date)
        if not raw:
            return []

        headlines = []
        for item in raw[:20]:  # Keep up to 20 for cache
            ts = item.get("datetime", 0)
            dt = datetime.fromtimestamp(ts, tz=ET) if ts else now
            headline_text = item.get("headline", "")
            source = item.get("source", "Unknown")
            sentiment = classify_sentiment(headline_text)
            time_str = dt.strftime("%I:%M %p") if ts else ""

            headlines.append({
                "headline": headline_text,
                "source": source,
                "time": time_str,
                "sentiment": sentiment,
                "datetime": dt,
                "raw_time": ts,
            })

        # Sort by timestamp descending (most recent first)
        headlines.sort(key=lambda x: x["raw_time"], reverse=True)
        return headlines

    except Exception as e:
        logger.debug(f"Finnhub news fetch error for {ticker}: {e}")
        return []


async def fetch_news(ticker: str) -> list:
    """Async wrapper for news fetch."""
    ticker = ticker.upper()
    if _is_cache_fresh(ticker):
        return _news_cache.get(ticker, [])

    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        headlines = await loop.run_in_executor(executor, _fetch_news_sync, ticker)
        _news_cache[ticker] = headlines
        _cache_expiry[ticker] = datetime.now(ET) + timedelta(minutes=CACHE_TTL_MINUTES)
        return headlines
    except Exception as e:
        logger.debug(f"Async news fetch error: {e}")
        return _news_cache.get(ticker, [])


async def refresh_all_news(tickers: list):
    """Refresh news cache for a batch of tickers (rate limited)."""
    client = get_finnhub_client()
    if client is None:
        return

    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=3)

    # Only refresh stale tickers
    stale = [t for t in tickers if not _is_cache_fresh(t)]
    # Limit to 30 per cycle to respect API limits
    for ticker in stale[:30]:
        try:
            headlines = await loop.run_in_executor(executor, _fetch_news_sync, ticker)
            _news_cache[ticker] = headlines
            _cache_expiry[ticker] = datetime.now(ET) + timedelta(minutes=CACHE_TTL_MINUTES)
        except Exception as e:
            logger.debug(f"Batch news refresh error for {ticker}: {e}")
        await asyncio.sleep(0.2)  # Rate limit


# ══════════════════════════════════════════════════════════════════════════
# NEWS QUERY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def get_cached_news(ticker: str) -> list:
    """Get cached news (no fetch). Returns list of headline dicts."""
    return _news_cache.get(ticker.upper(), [])


def get_last_5_headlines(ticker: str) -> list:
    """Return up to 5 most recent headlines from cache."""
    return get_cached_news(ticker)[:5]


def get_top_headline(ticker: str) -> str:
    """Return the most recent headline text, or empty string."""
    headlines = get_cached_news(ticker)
    if headlines:
        return headlines[0].get("headline", "")
    return ""


def get_news_summary(ticker: str) -> dict:
    """Return sentiment summary for cached news."""
    headlines = get_cached_news(ticker)
    bull = sum(1 for h in headlines if h["sentiment"] == "bullish")
    bear = sum(1 for h in headlines if h["sentiment"] == "bearish")
    neut = sum(1 for h in headlines if h["sentiment"] == "neutral")
    total = len(headlines)

    if bull > bear:
        overall = "bullish"
    elif bear > bull:
        overall = "bearish"
    else:
        overall = "neutral"

    return {
        "sentiment": overall,
        "bullish_count": bull,
        "bearish_count": bear,
        "neutral_count": neut,
        "total": total,
    }


def get_news_score_modifier(ticker: str) -> dict:
    """
    Determine news impact on confidence score.
    Returns: {"modifier": int, "suppress": bool, "headline": str}
    """
    headlines = get_cached_news(ticker)
    now = datetime.now(ET)
    modifier = 0
    suppress = False
    top_headline = ""

    if headlines:
        top_headline = headlines[0].get("headline", "No major news")

    for h in headlines:
        dt = h.get("datetime")
        if dt is None:
            continue

        age_minutes = (now - dt).total_seconds() / 60
        sentiment = h.get("sentiment", "neutral")

        # Bearish headline within 30 minutes → suppress
        if sentiment == "bearish" and age_minutes <= SCORE_NEWS_SUPPRESS_WINDOW:
            suppress = True
            break

        # Bullish headline within 2 hours → bonus
        if sentiment == "bullish" and age_minutes <= SCORE_NEWS_BONUS_WINDOW:
            modifier = max(modifier, 5)  # +5 points (don't stack)
            break

    return {
        "modifier": modifier,
        "suppress": suppress,
        "headline": top_headline[:100] if top_headline else "No major news",
    }


# ══════════════════════════════════════════════════════════════════════════
# EARNINGS
# ══════════════════════════════════════════════════════════════════════════

def get_earnings_info(ticker: str) -> Optional[dict]:
    """Fetch upcoming earnings for a ticker via Finnhub."""
    client = get_finnhub_client()
    if client is None:
        return None
    try:
        now = datetime.now(ET)
        from_date = now.strftime("%Y-%m-%d")
        to_date = (now + timedelta(days=30)).strftime("%Y-%m-%d")

        cal = client.earnings_calendar(_from=from_date, to=to_date, symbol=ticker)
        events = cal.get("earningsCalendar", [])
        if events:
            e = events[0]
            return {
                "date": e.get("date", "N/A"),
                "hour": e.get("hour", "N/A"),
                "eps_estimate": e.get("epsEstimate", "N/A"),
                "eps_actual": e.get("epsActual", "N/A"),
                "revenue_estimate": e.get("revenueEstimate", "N/A"),
                "revenue_actual": e.get("revenueActual", "N/A"),
            }
        return None
    except Exception as e:
        logger.debug(f"Earnings fetch error for {ticker}: {e}")
        return None


def has_earnings_today(ticker: str) -> bool:
    """Check if ticker has earnings today."""
    info = get_earnings_info(ticker)
    if info is None:
        return False
    today = datetime.now(ET).strftime("%Y-%m-%d")
    return info.get("date") == today


# ══════════════════════════════════════════════════════════════════════════
# ECONOMIC CALENDAR
# ══════════════════════════════════════════════════════════════════════════

def get_economic_events() -> list:
    """Fetch upcoming economic events via Finnhub."""
    client = get_finnhub_client()
    if client is None:
        return []
    try:
        now = datetime.now(ET)
        cal = client.economic_calendar(
            _from=now.strftime("%Y-%m-%d"),
            to=(now + timedelta(days=3)).strftime("%Y-%m-%d"),
        )
        events = cal.get("economicCalendar", []) if cal else []
        result = []
        for ev in events[:20]:
            result.append({
                "time": ev.get("time", "TBD"),
                "event": ev.get("event", "Unknown"),
                "impact": ev.get("impact", ""),
                "country": ev.get("country", "US"),
                "actual": ev.get("actual", ""),
                "estimate": ev.get("estimate", ""),
                "previous": ev.get("prev", ""),
            })
        return result
    except Exception as e:
        logger.debug(f"Economic calendar error: {e}")
        return []
