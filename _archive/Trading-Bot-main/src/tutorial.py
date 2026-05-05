"""
Build tutorial embed explaining how the bot works and reasons.
"""

import discord


def build_tutorial_embed() -> discord.Embed:
    """Build a Discord embed explaining the bot's functionality and signal logic."""
    embed = discord.Embed(
        title="How the Trading Bot Works",
        description="A quick guide to understanding this bot's data flow, signal logic, and output.",
        color=0x3498DB,
    )

    embed.add_field(
        name="Commands",
        value=(
            "• **`/recap`** – S&P 100 market recap (top 15 Buy/Sell)\n"
            "• **`/market`** – Recap for any index (S&P 500, DAX, FTSE 100)\n"
            "• **`/stock`** – Single-ticker rundown; optional timeframe (1d, 1wk, 1h)\n"
            "• **`/supported`** – List markets, indexes, and stock count\n"
            "• **`/watchlist add/remove/list`** – Manage your watchlist\n"
            "• **Expert sentiment** – Per-ticker analyst view from config (config.yaml ticker_profiles)\n"
            "• **`/watchlist news`** – News for watchlisted tickers with sentiment\n"
            "• **`/watchlist recap`** – Recap of your watchlisted tickers\n"
            "• **`/news`** – Market or ticker-specific news with sentiment\n"
            "• **`/indicatorbacktest`** – Backtest (Simple or Walk-forward)\n"
            "• **`/stop`** – Interrupt current operation"
        ),
        inline=False,
    )
    embed.add_field(
        name="Options & Auto-recap",
        value=(
            "• **Volatility** (Considered / Not considered): ATR used in signals when Considered\n"
            "• Auto-recap every 30 min when market open (9:30 AM–4:00 PM ET, Mon–Fri), pings @everyone"
        ),
        inline=False,
    )

    embed.add_field(
        name="How it works",
        value=(
            "1. Fetches ~60 days of daily OHLCV from Yahoo Finance (or configurable period)\n"
            "2. Computes **RSI** (14), **MACD** (12/26/9), **Bollinger Bands** (20, 2 std), **SuperTrend** (10, 3), "
            "**Stochastic** (14, 3), **Williams %R** (14), **EMA crossover** (9/21), **ATR** (14) for volatility\n"
            "3. Evaluates each stock for Buy, Sell, or Hold using weighted consensus"
        ),
        inline=False,
    )

    embed.add_field(
        name="Signal logic (how it reasons)",
        value=(
            "**Buy** when net_score ≥ min_net_score (default 0.5):\n"
            "• RSI < 35 (oversold)\n"
            "• Price at or below lower Bollinger Band\n"
            "• MACD histogram > 0 + SuperTrend bullish\n"
            "• Stochastic %K < 20 (oversold)\n"
            "• Williams %R < -80 (oversold)\n"
            "• EMA fast > slow (bullish crossover)\n"
            "• ATR % below recent avg (low volatility, breakout setup) — when Volatility Considered\n"
            "• News sentiment bullish — when news enabled\n"
            "• Expert view bullish — when set in config ticker_profiles\n"
            "→ Each indicator contributes a weight; buy_weight − sell_weight = net_score. Confidence = 20 + 80×consensus + extremity bonus (RSI/MACD alignment)\n\n"
            "**Sell** when net_score ≤ −min_net_score:\n"
            "• RSI > 65 (overbought)\n"
            "• Price at or above upper Bollinger Band\n"
            "• MACD histogram < 0 + SuperTrend bearish\n"
            "• Stochastic %K > 80 (overbought)\n"
            "• Williams %R > -20 (overbought)\n"
            "• EMA fast < slow (bearish crossover)\n"
            "• ATR % above recent avg (high volatility, caution) — when Volatility Considered\n"
            "• News sentiment bearish — when news enabled\n"
            "• Expert view bearish — when set in config ticker_profiles\n\n"
            "**Hold**: Otherwise (net_score between −min_net_score and +min_net_score)"
        ),
        inline=False,
    )

    embed.add_field(
        name="Interpreting results",
        value=(
            "• **Confidence 1–100**: Weighted consensus plus extremity bonus (RSI/MACD alignment); scaled by timeframe (shorter TFs noisier)\n"
            "• **Vol %**: ATR as % of price (daily range) — shown only when Volatility is Considered\n"
            "• Auto-recap pings @everyone during market hours\n"
            "• Recap shows top 15 Buy and Sell signals by confidence"
        ),
        inline=False,
    )

    embed.set_footer(text="Tune indicator thresholds, weights, and recap interval in config.yaml")

    return embed
