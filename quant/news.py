"""
Financial news: fetch from yfinance, sentiment scoring, format helpers.
"""

import logging
import re

import yfinance as yf

logger = logging.getLogger(__name__)

# VADER for sentiment (optional dependency)
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    VADER_AVAILABLE = True
except ImportError:
    _vader = None
    VADER_AVAILABLE = False


def fetch_news(ticker: str, count: int = 5) -> list[dict]:
    """
    Fetch recent news for a ticker from yfinance.

    Returns list of dicts with keys: title, publisher, link, providerPublishTime.
    Empty list on failure or no news.
    """
    try:
        t = yf.Ticker(ticker.upper())
        raw = t.get_news(count=count)
        if not raw:
            return []
        items = []
        for item in raw[:count]:
            if not isinstance(item, dict):
                continue
            # yfinance can return nested content (item["content"]) or flat structure
            content = item.get("content", item)
            if not isinstance(content, dict):
                content = item

            title = content.get("title") or content.get("headline") or ""
            link = ""
            for k in ("clickThroughUrl", "canonicalUrl", "link", "url"):
                v = content.get(k)
                if isinstance(v, dict) and v.get("url"):
                    link = v["url"]
                    break
                if isinstance(v, str):
                    link = v
                    break
            pub = content.get("provider") or content.get("publisher") or content.get("source") or "Unknown"
            if isinstance(pub, dict):
                pub = pub.get("displayName") or pub.get("name") or "Unknown"
            items.append({
                "title": str(title) if title else "",
                "publisher": str(pub) if pub else "Unknown",
                "link": str(link) if link else "",
                "providerPublishTime": content.get("providerPublishTime") or content.get("pubDate"),
            })
        return items
    except Exception as e:
        logger.warning("Failed to fetch news for %s: %s", ticker, e)
        return []


def _sentiment_vader(text: str) -> float:
    """VADER compound score -1 to +1. Returns 0 if VADER unavailable."""
    if not VADER_AVAILABLE or not text:
        return 0.0
    try:
        scores = _vader.polarity_scores(text)
        return float(scores.get("compound", 0))
    except Exception:
        return 0.0


def _sentiment_keyword(text: str) -> float:
    """Simple keyword-based sentiment. Returns -1 to +1. Uses word-boundary matching to avoid 'upgrade' in 'downgrade'."""
    if not text:
        return 0.0
    t = text.lower()
    # Use word boundaries so "upgrade" doesn't match "downgrade", "beat" doesn't match "beaten"
    def word_match(word: str) -> bool:
        return bool(re.search(r"\b" + re.escape(word) + r"\b", t))
    bullish = ("surge", "rally", "gain", "beat", "growth", "upgrade", "bullish", "profit", "record")
    bearish = ("drop", "fall", "loss", "miss", "decline", "downgrade", "bearish", "layoff", "cut")
    pos = sum(1 for w in bullish if word_match(w))
    neg = sum(1 for w in bearish if word_match(w))
    if pos == neg:
        return 0.0
    if pos > neg:
        return min(1.0, 0.2 + 0.2 * (pos - neg))
    return max(-1.0, -0.2 - 0.2 * (neg - pos))


def compute_sentiment(headlines: list[dict], provider: str = "vader") -> float:
    """
    Compute aggregate sentiment from headlines. Returns -1 (bearish) to +1 (bullish).

    provider: "vader" | "keyword" | "none"
    """
    if not headlines:
        return 0.0
    if provider == "none":
        return 0.0

    scores = []
    for h in headlines:
        title = h.get("title", "")
        if not title:
            continue
        if provider == "vader":
            s = _sentiment_vader(title)
        elif provider == "keyword":
            s = _sentiment_keyword(title)
        else:
            s = _sentiment_vader(title) if VADER_AVAILABLE else _sentiment_keyword(title)
        scores.append(s)

    if not scores:
        return 0.0
    avg = sum(scores) / len(scores)
    return max(-1.0, min(1.0, avg))


def format_headlines_for_embed(headlines: list[dict], ticker: str, max_items: int = 5) -> str:
    """
    Format headlines for Discord embed. Returns markdown string.
    ticker: used in header (e.g. "News about AAPL"). Use "Market" for general market news.
    """
    if not headlines:
        return ""
    header = "**Recent Market News**" if ticker == "Market" else f"**News about {ticker}:**"
    lines = [header]
    for h in headlines[:max_items]:
        title = (h.get("title") or "").strip()
        link = h.get("link", "")
        pub = h.get("publisher", "")
        if not title:
            continue
        # Discord markdown: [text](url), sanitize title length
        safe_title = title[:80] + "…" if len(title) > 80 else title
        if link:
            lines.append(f"• [{safe_title}]({link})" + (f" — {pub}" if pub else ""))
        else:
            lines.append(f"• {safe_title}" + (f" — {pub}" if pub else ""))
    return "\n".join(lines)


def sentiment_label(sentiment: float) -> str:
    """Convert sentiment -1..1 to Bullish/Bearish/Neutral."""
    if sentiment > 0.1:
        return "Bullish"
    if sentiment < -0.1:
        return "Bearish"
    return "Neutral"


# Severity keywords: high-impact market terms. Word-boundary matching.
_SEVERITY_KEYWORDS = (
    "earnings", "fed", "rate", "rates", "inflation", "recession", "rally", "crash",
    "surge", "plunge", "hike", "cut", "guidance", "forecast", "downgrade", "upgrade",
    "layoff", "layoffs", "miss", "beat", "record", "high", "low",
)


def _severity_score(title: str) -> float:
    """Score headline 0-1 by severity (market impact). Higher = more important."""
    if not title:
        return 0.0
    t = title.lower()
    matches = sum(
        1 for w in _SEVERITY_KEYWORDS
        if re.search(r"\b" + re.escape(w) + r"\b", t)
    )
    # 0 matches -> 0, 1 -> 0.3, 2 -> 0.5, 3+ -> 0.7-1.0
    if matches == 0:
        return 0.0
    return min(1.0, 0.3 + 0.2 * matches)


def filter_by_severity(headlines: list[dict], min_severity: float = 0.3, max_items: int = 5) -> list[dict]:
    """
    Filter and sort headlines by severity. Returns headlines with severity >= min_severity,
    sorted by severity desc, capped at max_items.
    """
    scored = [(h, _severity_score(h.get("title", ""))) for h in headlines]
    filtered = [(h, s) for h, s in scored if s >= min_severity]
    filtered.sort(key=lambda x: -x[1])
    return [h for h, _ in filtered[:max_items]]
