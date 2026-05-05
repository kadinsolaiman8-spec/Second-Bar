"""
Watchlist persistence via Supabase.
Per-user, per-guild ticker lists for /watchlist and /watchlist-recap.
"""

import logging
import os
from typing import Literal

from src.data import fetch_ohlcv

logger = logging.getLogger(__name__)

MAX_WATCHLIST_SIZE = 20
_client = None


def get_client():
    """Return Supabase client (lazy init from env). Returns None if env not configured."""
    global _client
    if _client is not None:
        return _client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except Exception as e:
        logger.warning("Failed to create Supabase client: %s", e)
        return None


def _normalize_ticker(ticker: str) -> str:
    return (ticker or "").upper().strip()[:10]


def _guild_key(guild_id: int | None) -> str:
    """Convert guild_id to string for storage. DMs use 'dm'."""
    return str(guild_id) if guild_id is not None else "dm"


def add_ticker(
    user_id: int,
    guild_id: int | None,
    ticker: str,
) -> tuple[Literal[True], None] | tuple[Literal[False], str]:
    """
    Add a ticker to the user's watchlist.
    Returns (True, None) on success, (False, error_message) on failure.
    """
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return (False, "Please provide a valid ticker symbol.")

    client = get_client()
    if not client:
        return (False, "Watchlist is not configured. Contact the bot owner.")

    # Validate ticker exists via yfinance
    ohlcv = fetch_ohlcv(symbols=[ticker], period="3mo", interval="1d")
    if not ohlcv or ticker not in ohlcv:
        return (False, f"No data found for '{ticker}'. Check the symbol and try again.")

    user_str = str(user_id)
    guild_str = _guild_key(guild_id)

    try:
        # Check current size
        resp = client.table("watchlist").select("id").eq("user_id", user_str).eq("guild_id", guild_str).execute()
        if resp.data and len(resp.data) >= MAX_WATCHLIST_SIZE:
            return (False, f"Watchlist is full (max {MAX_WATCHLIST_SIZE} tickers). Remove some first.")

        # Insert (unique constraint will catch duplicates)
        client.table("watchlist").insert({
            "user_id": user_str,
            "guild_id": guild_str,
            "ticker": ticker,
        }).execute()
        return (True, None)
    except Exception as e:
        err_msg = str(e).lower()
        if "duplicate" in err_msg or "unique" in err_msg or "conflict" in err_msg:
            return (False, f"'{ticker}' is already in your watchlist.")
        logger.exception("Watchlist add failed: %s", e)
        return (False, "Failed to add ticker. Please try again later.")


def remove_ticker(
    user_id: int,
    guild_id: int | None,
    ticker: str,
) -> tuple[Literal[True], None] | tuple[Literal[False], str]:
    """
    Remove a ticker from the user's watchlist.
    Returns (True, None) on success, (False, error_message) on failure.
    """
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return (False, "Please provide a valid ticker symbol.")

    client = get_client()
    if not client:
        return (False, "Watchlist is not configured. Contact the bot owner.")

    user_str = str(user_id)
    guild_str = _guild_key(guild_id)

    try:
        resp = client.table("watchlist").delete().eq("user_id", user_str).eq("guild_id", guild_str).eq("ticker", ticker).execute()
        if resp.data and len(resp.data) > 0:
            return (True, None)
        return (False, f"'{ticker}' is not in your watchlist.")
    except Exception as e:
        logger.exception("Watchlist remove failed: %s", e)
        return (False, "Failed to remove ticker. Please try again later.")


def get_tickers(user_id: int, guild_id: int | None) -> list[str]:
    """Return list of ticker strings for the user's watchlist. Empty list on error or empty."""
    client = get_client()
    if not client:
        return []

    user_str = str(user_id)
    guild_str = _guild_key(guild_id)

    try:
        resp = client.table("watchlist").select("ticker").eq("user_id", user_str).eq("guild_id", guild_str).order("added_at").execute()
        if not resp.data:
            return []
        return [r["ticker"] for r in resp.data]
    except Exception as e:
        logger.exception("Watchlist get failed: %s", e)
        return []
