# commands.py — All slash commands with autocomplete, embeds, buttons, new features
#
# No cogs — all commands registered via register_commands(tree, bot).
# Trade-scan commands only produce opportunities during regular market hours.

import logging
import asyncio
import os
import traceback
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
import pandas as pd
import pytz

from config import (
    BOT_NAME, VERSION, MARKET_TIMEZONE, CONFIDENCE_CAUTION, CONFIDENCE_STRONG,
    CONFIDENCE_HIGH_CONVICTION, ALL_STRATEGIES, TIMEFRAMES,
    BACKTEST_MAX_DAYS, MAX_MESSAGE_LENGTH,
    QUIET_MODE_DURATION_MINUTES, MAX_PRICE_ALERTS_PER_USER,
    EMBED_COLOR_INFO, EMBED_COLOR_ERROR, EMBED_COLOR_CAUTION,
    EMBED_COLOR_BUY, EMBED_COLOR_AFTER_HOURS,
    STRATEGY_VWAP_BREAKOUT, STRATEGY_ORB, STRATEGY_EMA_PULLBACK,
    STRATEGY_FIBONACCI, STRATEGY_BB_SQUEEZE,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)

# ── Pre-load watchlist data at module level (never fetched inside autocomplete) ──
from watchlist import ALL_STOCKS, COMPANY_NAMES

# Default tickers shown when nothing is typed yet
_DEFAULT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "GOOGL",
    "AMZN", "META", "AMD", "COIN", "PLTR",
    "SPY", "QQQ", "MARA", "RIOT", "SOFI",
    "HOOD", "MSTR", "IONQ", "CRWD", "SNOW",
    "PANW", "DDOG", "NET", "JPM", "BAC",
]

# Fallback choices if autocomplete errors out
_FALLBACK_CHOICES = [
    app_commands.Choice(name="AAPL — Apple Inc.", value="AAPL"),
    app_commands.Choice(name="TSLA — Tesla Inc.", value="TSLA"),
    app_commands.Choice(name="NVDA — NVIDIA Corporation", value="NVDA"),
    app_commands.Choice(name="SPY — SPDR S&P 500 ETF", value="SPY"),
    app_commands.Choice(name="MSFT — Microsoft Corporation", value="MSFT"),
]


# ══════════════════════════════════════════════════════════════════════════
# SESSION STATE  (Change 10: last ticker, Change 11: quiet, Change 12: alerts,
#                 Change 15: plain english)
# ══════════════════════════════════════════════════════════════════════════

last_ticker: dict = {}          # {user_id: "AAPL"}
quiet_channels: dict = {}       # {channel_id: expiry_datetime}
price_alerts: dict = {}         # {user_id: [{"ticker", "target", "direction", "set_at"}]}
plain_english_channels: set = set()  # {channel_id}


def _set_last(user_id: int, ticker: str):
    last_ticker[user_id] = ticker.upper().strip()


def _get_last(user_id: int) -> Optional[str]:
    return last_ticker.get(user_id)


def is_quiet(channel_id: int) -> bool:
    """Check if channel is in quiet mode (auto-expire after duration)."""
    exp = quiet_channels.get(channel_id)
    if exp is None:
        return False
    if datetime.now(ET) >= exp:
        del quiet_channels[channel_id]
        return False
    return True


def is_plain_english(channel_id: int) -> bool:
    return channel_id in plain_english_channels


def get_price_alerts_for_user(user_id: int) -> list:
    return price_alerts.get(user_id, [])


def check_all_price_alerts(ticker_state: dict) -> list:
    """Check all price alerts against current prices. Returns list of triggered alerts."""
    triggered = []
    for user_id, alerts in list(price_alerts.items()):
        remaining = []
        for alert in alerts:
            t = alert["ticker"]
            state = ticker_state.get(t, {})
            price = state.get("price", 0)
            if price <= 0:
                remaining.append(alert)
                continue
            if alert["direction"] == "above" and price >= alert["target"]:
                triggered.append({"user_id": user_id, "alert": alert, "price": price})
            elif alert["direction"] == "below" and price <= alert["target"]:
                triggered.append({"user_id": user_id, "alert": alert, "price": price})
            else:
                remaining.append(alert)
        if remaining:
            price_alerts[user_id] = remaining
        elif user_id in price_alerts:
            del price_alerts[user_id]
    return triggered


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

async def _safe_defer(interaction: discord.Interaction):
    try:
        await interaction.response.defer(thinking=True)
    except Exception:
        pass


async def _safe_send(interaction: discord.Interaction, content=None,
                     embed=None, view=None, ephemeral=False):
    """Send followup with embed/content support."""
    try:
        kwargs = {"ephemeral": ephemeral}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        await interaction.followup.send(**kwargs)
    except Exception as e:
        logger.error(f"_safe_send error: {e}")


def _is_after_hours() -> bool:
    from utils import is_after_hours
    return is_after_hours()


def _resolve_ticker(interaction: discord.Interaction, ticker: Optional[str]) -> Optional[str]:
    """Resolve ticker — use provided, or fall back to last_ticker memory."""
    if ticker and ticker.strip():
        t = ticker.upper().strip()
        _set_last(interaction.user.id, t)
        return t
    last = _get_last(interaction.user.id)
    return last


# ══════════════════════════════════════════════════════════════════════════
# AUTOCOMPLETE FUNCTIONS — all wrapped in try/except, no awaits, pre-loaded data
# ══════════════════════════════════════════════════════════════════════════

async def ticker_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Ticker autocomplete from pre-loaded watchlist. Never awaits, never crashes."""
    try:
        if not current or not current.strip():
            return [
                app_commands.Choice(
                    name=f"{t} — {COMPANY_NAMES.get(t, t)}"[:100],
                    value=t,
                )
                for t in _DEFAULT_TICKERS
            ][:25]

        current_upper = current.strip().upper()
        current_lower = current.strip().lower()
        matches = []

        for ticker in ALL_STOCKS:
            company = COMPANY_NAMES.get(ticker, "")
            if current_upper in ticker or current_lower in company.lower():
                display = f"{ticker} — {company}" if company else ticker
                matches.append(
                    app_commands.Choice(name=display[:100], value=ticker)
                )
                if len(matches) >= 25:
                    break

        return matches

    except Exception as e:
        logger.error(f"ticker_autocomplete error: {e}")
        return _FALLBACK_CHOICES


async def strategy_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Strategy name autocomplete. Never crashes."""
    try:
        choices = [
            ("VWAP Momentum Breakout", "vwap"),
            ("Opening Range Breakout", "orb"),
            ("EMA Pullback in Trend", "pullback"),
            ("Fibonacci Confluence Reversal", "fibonacci"),
            ("Bollinger Band Squeeze", "squeeze"),
        ]
        q = current.lower() if current else ""
        return [
            app_commands.Choice(name=name, value=val)
            for name, val in choices
            if not q or q in name.lower() or q in val
        ][:25]
    except Exception as e:
        logger.error(f"strategy_autocomplete error: {e}")
        return [app_commands.Choice(name="VWAP Momentum Breakout", value="vwap")]


async def days_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Days preset autocomplete. Never crashes."""
    try:
        return [
            app_commands.Choice(name="1 Week (7 days)", value=7),
            app_commands.Choice(name="2 Weeks (14 days)", value=14),
            app_commands.Choice(name="1 Month (30 days)", value=30),
            app_commands.Choice(name="2 Months (60 days)", value=60),
            app_commands.Choice(name="3 Months (90 days)", value=90),
            app_commands.Choice(name="6 Months (180 days)", value=180),
            app_commands.Choice(name="1 Year (365 days)", value=365),
            app_commands.Choice(name="2 Years (730 days)", value=730),
        ]
    except Exception:
        return [app_commands.Choice(name="30 days", value=30)]


async def direction_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Price alert direction autocomplete. Never crashes."""
    try:
        return [
            app_commands.Choice(name="Above (price goes up to target)", value="above"),
            app_commands.Choice(name="Below (price drops to target)", value="below"),
        ]
    except Exception:
        return [app_commands.Choice(name="Above", value="above")]


# ══════════════════════════════════════════════════════════════════════════
# REGISTER ALL COMMANDS
# ══════════════════════════════════════════════════════════════════════════

def register_commands(tree: app_commands.CommandTree, bot):
    """Register all slash commands on the tree."""

    # ──────────────────────────────────────────────────────────────────────
    # 1. /scan <ticker>
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="scan", description="Full analysis scan of a single ticker")
    @app_commands.describe(ticker="Stock ticker symbol (e.g. AAPL)")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_scan(interaction: discord.Interaction, ticker: str):
        await _safe_defer(interaction)
        try:
            from scanner import scan_ticker
            from alerts import build_scan_embed, build_ticker_embed, info_embed
            from ui import ScanResultView

            t = ticker.upper().strip()
            _set_last(interaction.user.id, t)
            after_hours = _is_after_hours()
            pe = is_plain_english(interaction.channel_id)

            if after_hours:
                embed = info_embed(
                    "Market Closed - No Trade Scans",
                    "Trade scans only run during regular market hours: 9:30 AM-4:00 PM ET, Monday-Friday.",
                )
                await _safe_send(interaction, embed=embed)
                return

            result = await scan_ticker(t)
            if result:
                embed = build_scan_embed(result, after_hours=after_hours, plain_english=pe)
                indicators = result.get("indicators", {})
                atr_val = indicators.get("atr", {}).get("value", 0) if indicators else 0
                view = ScanResultView(
                    t, result.get("direction", "BUY"),
                    result.get("entry_price", 0),
                    result.get("stop", 0), bot,
                    atr=atr_val,
                    indicators=indicators,
                    strategy=result.get("strategy", ""),
                    regime=result.get("regime", ""),
                )
                await _safe_send(interaction, embed=embed, view=view)
            else:
                embed = build_ticker_embed(t, after_hours=after_hours)
                await _safe_send(interaction, embed=embed)

        except Exception as e:
            logger.error(f"/scan error: {e}\n{traceback.format_exc()}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Scan error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 2. /best  (Change 16: always show top 5)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="best", description="Top 5 stocks ranked by confidence score")
    async def cmd_best(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from scanner import get_all_watchlist_stocks
            from alerts import build_best_buys_card, info_embed
            from ui import BestListView

            after_hours = _is_after_hours()
            if after_hours:
                embed = info_embed(
                    "Market Closed - No Live Opportunities",
                    "Best opportunities are only shown during regular market hours: 9:30 AM-4:00 PM ET, Monday-Friday.",
                )
                await _safe_send(interaction, embed=embed)
                return

            all_stocks = get_all_watchlist_stocks()
            top5 = all_stocks[:5] if all_stocks else []

            if not top5:
                from alerts import info_embed
                embed = info_embed("📊 Live Rankings",
                                   "No stock data cached yet. The scanner needs to run at least once.")
                await _safe_send(interaction, embed=embed)
                return

            embed = build_best_buys_card(top5)
            tickers = [s["ticker"] for s in top5]
            scores = [s.get("score_data", {}).get("total", s.get("score", 0)) for s in top5]
            view = BestListView(tickers, bot, scores=scores)
            await _safe_send(interaction, embed=embed, view=view)

        except Exception as e:
            logger.error(f"/best error: {e}\n{traceback.format_exc()}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Best error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 3. /market
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="market", description="Full market overview with regime, breadth, VIX")
    async def cmd_market(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from scanner import get_market_breadth, spy_cache, get_ticker_state
            from regime import get_current_regime
            from alerts import build_market_overview_embed

            after_hours = _is_after_hours()
            regime = get_current_regime()
            breadth = get_market_breadth()
            spy_5m = spy_cache.get("5m")
            spy_data = {"price": 0, "change_pct": 0}
            if spy_5m is not None and not spy_5m.empty:
                spy_data["price"] = float(spy_5m["Close"].iloc[-1])
                if len(spy_5m) > 1:
                    first = float(spy_5m["Close"].iloc[0])
                    if first > 0:
                        spy_data["change_pct"] = round((spy_data["price"] - first) / first * 100, 2)

            # Fetch sector ETF data
            sector_data = {}
            try:
                import yfinance as yf
                sector_etfs = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU"]
                for etf in sector_etfs:
                    try:
                        df = yf.download(etf, period="1d", interval="5m", progress=False)
                        if df is not None and len(df) >= 2:
                            first_price = float(df["Close"].iloc[0])
                            last_price = float(df["Close"].iloc[-1])
                            if first_price > 0:
                                sector_data[etf] = round((last_price - first_price) / first_price * 100, 1)
                    except Exception:
                        pass
            except Exception:
                pass

            embed = build_market_overview_embed(regime, spy_data, breadth, after_hours, sector_data)
            await _safe_send(interaction, embed=embed)

        except Exception as e:
            logger.error(f"/market error: {e}\n{traceback.format_exc()}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Market error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 4. /news <ticker>
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="news", description="Latest news headlines and sentiment")
    @app_commands.describe(ticker="Stock ticker (optional — uses last ticker)")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_news(interaction: discord.Interaction, ticker: str = None):
        await _safe_defer(interaction)
        try:
            t = _resolve_ticker(interaction, ticker)
            if not t:
                from alerts import error_embed
                await _safe_send(interaction, embed=error_embed(
                    "Please provide a ticker. Example: /news AAPL — I'll remember it for next time!"))
                return

            from news import fetch_news, get_cached_news
            from alerts import build_news_embed

            await fetch_news(t)
            headlines = get_cached_news(t)
            embed = build_news_embed(t, headlines)
            await _safe_send(interaction, embed=embed)

        except Exception as e:
            logger.error(f"/news error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"News error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 5. /price <ticker>
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="price", description="Quick price check")
    @app_commands.describe(ticker="Stock ticker (optional — uses last ticker)")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_price(interaction: discord.Interaction, ticker: str = None):
        await _safe_defer(interaction)
        try:
            t = _resolve_ticker(interaction, ticker)
            if not t:
                from alerts import error_embed
                await _safe_send(interaction, embed=error_embed(
                    "Please provide a ticker. Example: /price AAPL"))
                return

            from scanner import fetch_ticker_data
            from alerts import build_ticker_embed

            after_hours = _is_after_hours()

            df = await asyncio.get_event_loop().run_in_executor(
                None, lambda: fetch_ticker_data(t, period="1d", interval="1m"))

            state = {}
            if df is not None and not df.empty:
                price = float(df["Close"].iloc[-1])
                change = 0.0
                if len(df) > 1:
                    first = float(df["Close"].iloc[0])
                    if first > 0:
                        change = round((price - first) / first * 100, 2)
                state = {"price": price, "change_pct": change, "score": 0,
                         "direction": "NEUTRAL", "strategy": "None", "indicators": {}}

            embed = build_ticker_embed(t, state, after_hours)
            note = ""
            if ticker is None and t:
                note = f"📌 Using your last ticker: {t}"
            if note:
                embed.set_author(name=note)
            await _safe_send(interaction, embed=embed)

        except Exception as e:
            logger.error(f"/price error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Price error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 6. /watchlist
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="watchlist", description="Show all stocks currently on the watchlist")
    async def cmd_watchlist(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from scanner import get_all_watchlist_stocks
            from alerts import build_watchlist_embed
            stocks = get_all_watchlist_stocks()
            embed = build_watchlist_embed(stocks)
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/watchlist error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Watchlist error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 7. /stats
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="stats", description="Paper trading journal statistics")
    async def cmd_stats(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from journal import (get_win_rate, get_avg_r, get_profit_factor,
                                 get_expectancy, get_streak_info, get_open_trade_count)
            from alerts import build_stats_embed
            stats = {
                "win_rate": get_win_rate(),
                "avg_r": get_avg_r(),
                "profit_factor": get_profit_factor(),
                "expectancy": get_expectancy(),
                "total_trades": get_open_trade_count(),
                "wins": 0, "losses": 0,
                "streak_info": get_streak_info(),
            }
            embed = build_stats_embed(stats)
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/stats error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Stats error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 8. /regime
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="regime", description="Current market regime and active strategies")
    async def cmd_regime(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from regime import get_current_regime, get_active_strategies, get_regime_history
            from alerts import build_regime_embed
            regime = get_current_regime()
            regime["active_strategies"] = get_active_strategies()
            regime["history"] = get_regime_history(5)
            embed = build_regime_embed(regime)
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/regime error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Regime error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 9. /risk <ticker> [account] [risk_pct]
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="risk", description="Position sizing calculator")
    @app_commands.describe(ticker="Stock ticker", account="Account size in dollars",
                          risk_pct="Risk percentage per trade")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_risk(interaction: discord.Interaction, ticker: str = None,
                       account: float = 10000.0, risk_pct: float = 1.0):
        await _safe_defer(interaction)
        try:
            t = _resolve_ticker(interaction, ticker)
            if not t:
                from alerts import error_embed
                await _safe_send(interaction, embed=error_embed(
                    "Please provide a ticker. Example: /risk AAPL 10000 1.0"))
                return

            from scanner import get_ticker_state
            from utils import calculate_position_size
            from alerts import build_risk_embed
            from ui import RiskConfirmView

            state = get_ticker_state(t)
            entry = state.get("price", 0)
            indicators = state.get("indicators", {})
            atr = indicators.get("atr", {}).get("value", indicators.get("atr", {}).get("atr", 0))
            stop = entry - atr * 1.5 if atr > 0 and entry > 0 else entry * 0.98

            if entry <= 0:
                from scanner import fetch_ticker_data
                df = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: fetch_ticker_data(t, period="1d", interval="1m"))
                if df is not None and not df.empty:
                    entry = float(df["Close"].iloc[-1])
                    stop = entry * 0.98

            result = calculate_position_size(account, risk_pct, entry, stop)
            result["account_size"] = account
            result["risk_pct"] = risk_pct
            result["entry"] = entry
            result["stop"] = stop

            embed = build_risk_embed(t, result)
            view = RiskConfirmView(t, stop)
            await _safe_send(interaction, embed=embed, view=view)

        except Exception as e:
            logger.error(f"/risk error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Risk error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 10. /tutorial
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="tutorial", description="Learn how to use the trading bot")
    async def cmd_tutorial(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            msgs = _build_tutorial_messages()
            for i, msg in enumerate(msgs):
                await _safe_send(interaction, content=msg)
                if i < len(msgs) - 1:
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"/tutorial error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Tutorial error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 11. /test <ticker>
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="test", description="Test scan a ticker (system health check)")
    @app_commands.describe(ticker="Stock ticker to test scan")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_test(interaction: discord.Interaction, ticker: str = None):
        await _safe_defer(interaction)
        try:
            if ticker:
                from scanner import scan_ticker, fetch_all_timeframes, _run_indicators_safe
                from alerts import build_scan_embed, info_embed
                from alerts import emoji_confidence_bar, _safe_get
                from alerts import _rsi_label, _rvol_label, _vwap_label, _macd_label, _supertrend_label
                from scoring import get_final_score
                from utils import is_market_open

                t = ticker.upper().strip()
                _set_last(interaction.user.id, t)
                market_open = is_market_open()
                if not market_open:
                    embed = info_embed(
                        "Market Closed - Test Trade Scan Disabled",
                        "Ticker test scans only build trade output during regular market hours: 9:30 AM-4:00 PM ET, Monday-Friday.",
                    )
                    await _safe_send(interaction, embed=embed)
                    return

                # Try normal scan first
                result = await scan_ticker(t)
                if result:
                    embed = build_scan_embed(result)
                    embed.color = 0xFF00FF  # magenta for test mode
                    embed.set_author(name="\U0001f9ea TEST MODE \u2014 No alert triggered")
                    await _safe_send(interaction, embed=embed)
                    return

                # ── scan_ticker returned None — build our own scored embed ──
                print(f"[TEST] {t}: scan_ticker returned None, building scored embed")

                # Fetch data ourselves
                try:
                    data = await fetch_all_timeframes(t)
                    df_5m = data.get("5m")
                except Exception:
                    df_5m = None

                indicators = {}
                entry_price = 0.0
                change_pct = 0.0

                if df_5m is not None and len(df_5m) >= 5:
                    indicators = _run_indicators_safe(df_5m)
                    entry_price = float(df_5m["Close"].iloc[-1])
                    prev_close = float(df_5m["Close"].iloc[0])
                    if prev_close > 0:
                        change_pct = round((entry_price - prev_close) / prev_close * 100, 2)

                # ── Score using fallback pipeline ──
                # Priority 1: Try direct indicator scoring if we have real data
                score_data = None
                if indicators:
                    rsi_val = _safe_get(indicators, "rsi", "value")
                    rvol_data = indicators.get("rvol", {})
                    rvol_val = rvol_data.get("rvol", rvol_data.get("value", 0))
                    vwap_data = indicators.get("vwap", {})
                    vwap_val = vwap_data.get("value", vwap_data.get("vwap", 0))
                    vwap_pct = 0.0
                    if vwap_val and entry_price > 0:
                        try:
                            vwap_pct = (entry_price - float(vwap_val)) / float(vwap_val) * 100
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass

                    print(f"[TEST] {t}: Have indicators RSI={rsi_val} RVOL={rvol_val} VWAP%={vwap_pct:.1f} Change={change_pct}%")

                    # Try full scorer first (in case it works with the data)
                    score_data = get_final_score(t, indicators, entry_price)

                # Priority 2: If no indicators or score still 0, keep test output neutral.
                if not score_data or score_data.get("total", 0) <= 5:
                    print(f"[TEST] {t}: No regular-hours score available")
                    score_data = {
                        "total": 20,
                        "trend": 4,
                        "momentum": 4,
                        "volume": 4,
                        "rs": 4,
                        "risk": 4,
                        "stop_loss": 0.0,
                        "tp1": 0.0,
                        "tp2": 0.0,
                    }

                # Ensure minimum score of 20
                if score_data.get("total", 0) < 20:
                    score_data["total"] = 20

                total_score = score_data.get("total", 20)

                # ── Strategy label ──
                strategy_label = "No Active Setup"

                # ── Build embed ──
                color = 0xFF00FF  # magenta for test mode
                if entry_price > 0:
                    title = f"${t} \u2014 ${entry_price:.2f} ({change_pct:+.2f}%)"
                else:
                    title = f"${t}"

                embed = discord.Embed(title=title, color=color)
                embed.set_author(name="\U0001f9ea TEST MODE")

                # Direction
                embed.add_field(name="Direction", value="\U0001f7e2 BUY" if total_score >= 50 else "\u26aa NEUTRAL", inline=True)
                embed.add_field(name="Strategy", value=strategy_label, inline=True)

                # Confidence bar
                bar = emoji_confidence_bar(total_score)
                conf_value = bar
                embed.add_field(name="Confidence", value=conf_value, inline=False)

                # Score breakdown
                score_lines = (
                    f"Trend: {score_data.get('trend', 0)}/20\n"
                    f"Momentum: {score_data.get('momentum', 0)}/20\n"
                    f"Volume: {score_data.get('volume', 0)}/20\n"
                    f"RS: {score_data.get('rs', 0)}/20\n"
                    f"Risk: {score_data.get('risk', 0)}/20"
                )
                embed.add_field(name="\U0001f4c8 Score Breakdown", value=score_lines, inline=True)

                # Indicators (if available)
                if indicators:
                    rsi_val = _safe_get(indicators, "rsi", "value")
                    ind_lines = "\n".join([
                        _rsi_label(rsi_val),
                        _macd_label(indicators),
                        _supertrend_label(indicators),
                        _rvol_label(indicators),
                        _vwap_label(entry_price, indicators),
                    ])
                    embed.add_field(name="\U0001f4ca Indicators", value=ind_lines, inline=True)

                # Trade setup if we have stops/targets
                if score_data.get("stop_loss", 0) > 0:
                    stop = score_data["stop_loss"]
                    t1 = score_data.get("tp1", 0)
                    t2 = score_data.get("tp2", 0)
                    setup_lines = f"Stop: **${stop:.2f}**\nTP1: **${t1:.2f}**\nTP2: **${t2:.2f}**"
                    embed.add_field(name="\U0001f4d0 Trade Setup", value=setup_lines, inline=False)

                # Footer
                now_str = datetime.now(ET).strftime("%I:%M %p ET")
                embed.set_footer(text=now_str)

                await _safe_send(interaction, embed=embed)
            else:
                from testing import run_health_check, format_health_check
                results = await run_health_check(bot)
                card = format_health_check(results)
                await _safe_send(interaction, content=f"```\n{card}\n```")
        except Exception as e:
            logger.error(f"/test error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Test error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 12. /backtest <ticker> [days]  (Change 3: days autocomplete)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="backtest", description="Backtest strategies on historical data")
    @app_commands.describe(ticker="Stock ticker", days="Number of days (1-730)")
    @app_commands.autocomplete(ticker=ticker_autocomplete, days=days_autocomplete)
    async def cmd_backtest(interaction: discord.Interaction, ticker: str,
                           days: int = 7):
        await _safe_defer(interaction)
        try:
            from testing import run_backtest
            from alerts import build_backtest_embeds
            t = ticker.upper().strip()
            _set_last(interaction.user.id, t)
            days = max(1, min(days, BACKTEST_MAX_DAYS))

            # Send loading message with interval info
            interval_label = "5m" if days <= 7 else "30m" if days <= 14 else "1h" if days <= 60 else "daily"
            loading_embed = discord.Embed(
                title=f"\u23F3 Crunching {days} days of ${t} data...",
                description=f"Running `{interval_label}` candle backtest. This may take a moment.",
                color=EMBED_COLOR_INFO,
            )
            await interaction.followup.send(embed=loading_embed)

            result = await run_backtest(t, days)
            embeds = build_backtest_embeds(result)

            # Send first embed as edit, rest as followups
            await interaction.edit_original_response(
                embed=embeds[0], content=None
            )
            for extra_embed in embeds[1:]:
                await interaction.followup.send(embed=extra_embed)

        except Exception as e:
            logger.error(f"/backtest error: {e}")
            from alerts import error_embed
            try:
                await interaction.edit_original_response(
                    embed=error_embed(f"Backtest error: {e}"), content=None
                )
            except Exception:
                await _safe_send(interaction, embed=error_embed(f"Backtest error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 13. /replay <ticker> <date>
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="replay", description="Replay a specific trading day")
    @app_commands.describe(ticker="Stock ticker", date="Date in YYYY-MM-DD format")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_replay(interaction: discord.Interaction, ticker: str,
                         date: str):
        await _safe_defer(interaction)
        try:
            from testing import run_replay, format_replay_results
            t = ticker.upper().strip()
            _set_last(interaction.user.id, t)

            result = await run_replay(t, date)
            card = format_replay_results(result)
            await _safe_send(interaction, content=f"```\n{card}\n```")
        except Exception as e:
            logger.error(f"/replay error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Replay error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 14. /goodday
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="goodday", description="Find the best historical trading day to backtest")
    async def cmd_goodday(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from testing import find_best_day

            # Loading message
            loading = discord.Embed(
                title="\U0001f50d Searching for the perfect trading day...",
                description="Scanning candidates over 6 months. This takes 30-60 seconds.",
                color=EMBED_COLOR_INFO,
            )
            await interaction.followup.send(embed=loading)

            result = await asyncio.get_event_loop().run_in_executor(
                None, find_best_day
            )

            tk = result.get("ticker", "?")
            dt = result.get("date", "?")
            day_score = result.get("day_score", 0)
            bd = result.get("breakdown", {})
            range_pct = result.get("day_range_pct", 0)

            if tk == "?" or day_score == 0:
                err_embed = discord.Embed(
                    title="No qualifying day found",
                    description="Could not find a strong trading day in the last 6 months.",
                    color=EMBED_COLOR_CAUTION,
                )
                await interaction.edit_original_response(embed=err_embed, content=None)
                return

            # Build main result embed
            embed = discord.Embed(
                title=f"Best Trading Day Found",
                color=EMBED_COLOR_BUY,
            )
            embed.add_field(name="Stock", value=f"**${tk}**", inline=True)
            embed.add_field(name="Date", value=dt, inline=True)
            embed.add_field(name="Day Score", value=f"**{day_score}/100**", inline=True)
            embed.add_field(name="Day Range", value=f"{range_pct:+.1f}%", inline=True)
            embed.add_field(
                name="Score Breakdown",
                value=(
                    f"{'✅' if bd.get('adx', 0) > 0 else '❌'} ADX > 30 ({bd.get('adx', 0)}/20)\n"
                    f"{'✅' if bd.get('rvol', 0) > 0 else '❌'} RVOL > 2.0 ({bd.get('rvol', 0)}/20)\n"
                    f"{'✅' if bd.get('rsi', 0) > 0 else '❌'} RSI 50-70 ({bd.get('rsi', 0)}/20)\n"
                    f"{'✅' if bd.get('range', 0) > 0 else '❌'} Range > 3x ATR ({bd.get('range', 0)}/20)\n"
                    f"{'✅' if bd.get('macd', 0) > 0 else '❌'} MACD Crossover ({bd.get('macd', 0)}/20)"
                ),
                inline=False,
            )

            # Attempt intraday replay of the best day
            replay_text = ""
            try:
                import yfinance as yf
                from datetime import timedelta as _td
                _best_date = datetime.strptime(dt, "%Y-%m-%d")
                _end_date = _best_date + _td(days=1)

                def _fetch_intraday():
                    return yf.download(
                        tk, start=dt,
                        end=_end_date.strftime("%Y-%m-%d"),
                        interval="5m", progress=False, auto_adjust=True,
                    )

                day_df = await asyncio.get_event_loop().run_in_executor(None, _fetch_intraday)
                if day_df is not None and hasattr(day_df, 'columns') and isinstance(day_df.columns, pd.MultiIndex):
                    day_df.columns = day_df.columns.get_level_values(0)

                if day_df is not None and len(day_df) > 10:
                    open_p = float(day_df["Close"].iloc[0])
                    high_p = float(day_df["High"].max())
                    close_p = float(day_df["Close"].iloc[-1])
                    day_ret = (close_p - open_p) / open_p * 100 if open_p > 0 else 0

                    # Find signal moments (volume spike + price up)
                    signals_found = 0
                    signal_lines = []
                    for j in range(5, min(25, len(day_df))):
                        candle = day_df.iloc[j]
                        prev = day_df.iloc[j-5:j]
                        price = float(candle["Close"])
                        vol = float(candle["Volume"])
                        avg_vol = float(prev["Volume"].mean()) if len(prev) > 0 else 0
                        price_up = price > float(prev["Close"].mean()) if len(prev) > 0 else False
                        high_vol = vol > avg_vol * 1.5 if avg_vol > 0 else False

                        if price_up and high_vol and signals_found < 3:
                            try:
                                time_str = candle.name.strftime("%I:%M %p")
                            except Exception:
                                time_str = str(j)
                            signal_lines.append(f"✅ {time_str}: BUY @ ${price:.2f}")
                            signals_found += 1

                    replay_text = f"Open: ${open_p:.2f} | High: ${high_p:.2f} | Close: ${close_p:.2f} | Move: {day_ret:+.1f}%"
                    if signal_lines:
                        replay_text += "\n" + "\n".join(signal_lines)
                    else:
                        replay_text += "\nSignals would have fired during strong momentum periods"

            except Exception as e:
                replay_text = f"Replay unavailable: {e}"

            if replay_text:
                embed.add_field(
                    name=f"Intraday Replay — {dt}",
                    value=replay_text,
                    inline=False,
                )

            embed.add_field(
                name="Next Step",
                value=f"Run `/replay {tk} {dt}` for full candle-by-candle analysis",
                inline=False,
            )
            embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
            await interaction.edit_original_response(embed=embed, content=None)

        except Exception as e:
            logger.error(f"/goodday error: {e}\n{traceback.format_exc()}")
            from alerts import error_embed
            try:
                await interaction.edit_original_response(
                    embed=error_embed(f"Goodday error: {e}"), content=None
                )
            except Exception:
                await _safe_send(interaction, embed=error_embed(f"Goodday error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 15. /simulate <ticker> [strategy]  (Change 2: strategy autocomplete)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="simulate", description="Simulate a strategy trade on current data")
    @app_commands.describe(ticker="Stock ticker", strategy="Strategy to simulate")
    @app_commands.autocomplete(ticker=ticker_autocomplete, strategy=strategy_autocomplete)
    async def cmd_simulate(interaction: discord.Interaction, ticker: str,
                           strategy: str = None):
        await _safe_defer(interaction)
        try:
            from testing import simulate_strategy
            from alerts import build_backtest_embeds
            t = ticker.upper().strip()
            strat_name = strategy or "all"
            _set_last(interaction.user.id, t)

            loading = discord.Embed(
                title=f"\U0001f9ea Simulating {strat_name} on ${t}...",
                color=EMBED_COLOR_INFO,
            )
            await interaction.followup.send(embed=loading)

            result = await simulate_strategy(t, strategy or "", days=5)
            embeds = build_backtest_embeds(result)
            await interaction.edit_original_response(embed=embeds[0], content=None)
            for extra in embeds[1:]:
                await interaction.followup.send(embed=extra)
        except Exception as e:
            logger.error(f"/simulate error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Simulate error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 16. /mockmarket
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="mockmarket", description="Toggle mock market mode for testing")
    async def cmd_mockmarket(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            embed = discord.Embed(
                title="Mock Market Disabled",
                description="Closed-market trade testing is turned off. Trade scans and best opportunities only run during regular market hours.",
                color=EMBED_COLOR_INFO,
            )
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/mockmarket error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Mock market error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 17. /indicators <ticker>
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="indicators", description="Show all technical indicators for a ticker")
    @app_commands.describe(ticker="Stock ticker")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_indicators(interaction: discord.Interaction, ticker: str = None):
        await _safe_defer(interaction)
        try:
            t = _resolve_ticker(interaction, ticker)
            if not t:
                from alerts import error_embed
                await _safe_send(interaction, embed=error_embed(
                    "Please provide a ticker. Example: /indicators AAPL"))
                return

            from scanner import get_ticker_state, scan_ticker
            from alerts import build_indicators_embed

            state = get_ticker_state(t)
            indicators = state.get("indicators", {})
            if not indicators:
                result = await scan_ticker(t)
                if result:
                    indicators = result.get("indicators", {})

            pe = is_plain_english(interaction.channel_id)
            embed = build_indicators_embed(t, indicators, plain_english=pe)
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/indicators error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Indicators error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 18. /history
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="history", description="Show recent paper trade history")
    @app_commands.describe(count="Number of recent trades to show")
    async def cmd_history(interaction: discord.Interaction, count: int = 10):
        await _safe_defer(interaction)
        try:
            from journal import get_recent_trades
            from alerts import build_history_embed
            trades = get_recent_trades(count)
            embed = build_history_embed(trades)
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/history error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"History error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 19. /ping
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="ping", description="Check bot latency")
    async def cmd_ping(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from alerts import build_ping_embed
            from testing import _mock_market_active
            latency = bot.latency * 1000
            channel_ok = bot.get_channel(int(os.getenv("ALERT_CHANNEL_ID", "0"))) is not None
            bot_data = {
                "scanner_running": True,
                "channel_ok": channel_ok,
                "mock_market": _mock_market_active,
                "alerts_today": 0,
                "scans_run": 0,
            }
            embed = build_ping_embed(latency, bot_data)
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/ping error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Ping error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 20. /help
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="help", description="Show all available commands")
    async def cmd_help(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from alerts import build_help_embed
            embed = build_help_embed()
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/help error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Help error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 21. /quiet  (Change 11)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="quiet", description="Toggle quiet mode — suppress auto-alerts for 60 minutes")
    async def cmd_quiet(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            cid = interaction.channel_id
            if is_quiet(cid):
                del quiet_channels[cid]
                embed = discord.Embed(
                    title="🔔 Quiet Mode Disabled",
                    description="Alerts are now active in this channel.",
                    color=EMBED_COLOR_BUY,
                )
            else:
                expiry = datetime.now(ET) + timedelta(minutes=QUIET_MODE_DURATION_MINUTES)
                quiet_channels[cid] = expiry
                embed = discord.Embed(
                    title="🔕 Quiet Mode Enabled",
                    description=(
                        f"Auto-alerts suppressed for {QUIET_MODE_DURATION_MINUTES} minutes.\n"
                        f"Expires at {expiry.strftime('%I:%M %p ET')}.\n"
                        f"All commands still work normally.\n"
                        f"Use /quiet again to re-enable alerts."
                    ),
                    color=EMBED_COLOR_AFTER_HOURS,
                )
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/quiet error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Quiet mode error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 22. /plainenglish  (Change 15)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="plainenglish", description="Toggle plain English mode for this channel")
    async def cmd_plainenglish(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            cid = interaction.channel_id
            if cid in plain_english_channels:
                plain_english_channels.discard(cid)
                embed = discord.Embed(
                    title="🔤 Plain English Mode OFF",
                    description="Analysis will now use standard technical language.",
                    color=EMBED_COLOR_INFO,
                )
            else:
                plain_english_channels.add(cid)
                embed = discord.Embed(
                    title="🔤 Plain English Mode ON",
                    description="All analysis will now use simple language that anyone can understand.",
                    color=EMBED_COLOR_CAUTION,
                )
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/plainenglish error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Plain English error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 23. /alert <ticker> <price> <direction>  (Change 12)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="alert", description="Set a price alert for a stock")
    @app_commands.describe(ticker="Stock ticker", price="Target price",
                          direction="Alert when price goes above or below target")
    @app_commands.autocomplete(ticker=ticker_autocomplete, direction=direction_autocomplete)
    async def cmd_alert(interaction: discord.Interaction, ticker: str,
                        price: float, direction: str):
        await _safe_defer(interaction)
        try:
            t = ticker.upper().strip()
            _set_last(interaction.user.id, t)
            d = direction.lower()
            if d not in ("above", "below"):
                from alerts import error_embed
                await _safe_send(interaction, embed=error_embed(
                    "Direction must be 'above' or 'below'."))
                return

            uid = interaction.user.id
            user_alerts = price_alerts.get(uid, [])
            if len(user_alerts) >= MAX_PRICE_ALERTS_PER_USER:
                from alerts import error_embed
                await _safe_send(interaction, embed=error_embed(
                    f"You already have {MAX_PRICE_ALERTS_PER_USER} active alerts. "
                    f"Cancel one with /cancelalert first."))
                return

            # Check for duplicate
            for a in user_alerts:
                if a["ticker"] == t and a["direction"] == d:
                    from alerts import error_embed
                    await _safe_send(interaction, embed=error_embed(
                        f"You already have a {d} alert for {t}. Cancel it first."))
                    return

            alert_entry = {
                "ticker": t, "target": price, "direction": d,
                "set_at": datetime.now(ET),
                "channel_id": interaction.channel_id,
            }
            if uid not in price_alerts:
                price_alerts[uid] = []
            price_alerts[uid].append(alert_entry)

            embed = discord.Embed(
                title=f"⚡ Price Alert Set — ${t}",
                description=(
                    f"You'll be pinged when **{t}** goes **{d}** **${price:.2f}**\n"
                    f"Checked every 60 seconds during market hours."
                ),
                color=EMBED_COLOR_BUY if d == "above" else EMBED_COLOR_CAUTION,
            )
            embed.set_footer(text=f"Active alerts: {len(price_alerts.get(uid, []))}/{MAX_PRICE_ALERTS_PER_USER}")
            await _safe_send(interaction, embed=embed)

        except Exception as e:
            logger.error(f"/alert error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Alert error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 24. /myalerts  (Change 12)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="myalerts", description="View your active price alerts")
    async def cmd_myalerts(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            uid = interaction.user.id
            alerts = price_alerts.get(uid, [])
            embed = discord.Embed(title="⚡ Your Price Alerts", color=EMBED_COLOR_INFO)
            if not alerts:
                embed.description = "No active price alerts. Set one with /alert."
            else:
                for a in alerts:
                    set_str = a["set_at"].strftime("%b %d %I:%M %p") if a.get("set_at") else "?"
                    embed.add_field(
                        name=f"${a['ticker']} — {a['direction'].title()} ${a['target']:.2f}",
                        value=f"Set: {set_str}",
                        inline=True,
                    )
            embed.set_footer(text=f"{len(alerts)}/{MAX_PRICE_ALERTS_PER_USER} alerts active")
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/myalerts error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"My alerts error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 25. /cancelalert <ticker>  (Change 12)
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="cancelalert", description="Cancel a price alert for a ticker")
    @app_commands.describe(ticker="Ticker to cancel alert for")
    @app_commands.autocomplete(ticker=ticker_autocomplete)
    async def cmd_cancelalert(interaction: discord.Interaction, ticker: str):
        await _safe_defer(interaction)
        try:
            t = ticker.upper().strip()
            uid = interaction.user.id
            alerts = price_alerts.get(uid, [])
            before = len(alerts)
            price_alerts[uid] = [a for a in alerts if a["ticker"] != t]
            after = len(price_alerts.get(uid, []))

            if before > after:
                embed = discord.Embed(
                    title=f"✅ Alert Cancelled — ${t}",
                    description=f"Removed {before - after} alert(s) for {t}.",
                    color=EMBED_COLOR_BUY,
                )
            else:
                embed = discord.Embed(
                    title=f"No Alert Found — ${t}",
                    description=f"You don't have an active alert for {t}.",
                    color=EMBED_COLOR_AFTER_HOURS,
                )
            await _safe_send(interaction, embed=embed)
        except Exception as e:
            logger.error(f"/cancelalert error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Cancel alert error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 26. /wfo <ticker> [total_days]  — Walk Forward Optimization
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="wfo", description="Run walk forward optimization on a ticker")
    @app_commands.describe(ticker="Stock ticker", total_days="Total days for WFO (default 30)")
    @app_commands.autocomplete(ticker=ticker_autocomplete, total_days=days_autocomplete)
    async def cmd_wfo(interaction: discord.Interaction, ticker: str,
                      total_days: int = 30):
        await _safe_defer(interaction)
        try:
            from testing import run_wfo, format_wfo_results
            t = ticker.upper().strip()
            _set_last(interaction.user.id, t)
            total_days = max(10, min(total_days, BACKTEST_MAX_DAYS))

            loading = discord.Embed(
                title=f"\U0001f4ca Running walk forward analysis on ${t}...",
                description=f"Testing {total_days} days of data in rolling windows.",
                color=EMBED_COLOR_INFO,
            )
            await interaction.followup.send(embed=loading)

            result = await run_wfo(t, total_days)
            card = format_wfo_results(result)
            await _safe_send(interaction, content=f"```\n{card}\n```")
        except Exception as e:
            logger.error(f"/wfo error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"WFO error: {e}"))

    # ──────────────────────────────────────────────────────────────────────
    # 27. /scanbacktest [days]  — Backtest all watchlist stocks in parallel
    # ──────────────────────────────────────────────────────────────────────
    @tree.command(name="scanbacktest", description="Backtest all watchlist stocks in parallel")
    @app_commands.describe(days="Number of days per stock (default 14)")
    @app_commands.autocomplete(days=days_autocomplete)
    async def cmd_scanbacktest(interaction: discord.Interaction, days: int = 14):
        await _safe_defer(interaction)
        try:
            from testing import run_scan_backtest, format_scan_backtest_results
            days = max(1, min(days, BACKTEST_MAX_DAYS))

            await interaction.followup.send("⏳ Scanning all stocks... this may take a few minutes.")

            result = await run_scan_backtest(days)
            card = format_scan_backtest_results(result)

            # Split if too long for Discord
            if len(card) > 1900:
                chunks = card.split("\n")
                buf = ""
                for line in chunks:
                    if len(buf) + len(line) + 5 > 1900:
                        await _safe_send(interaction, content=f"```\n{buf}\n```")
                        buf = line
                    else:
                        buf += "\n" + line if buf else line
                if buf:
                    await _safe_send(interaction, content=f"```\n{buf}\n```")
            else:
                await _safe_send(interaction, content=f"```\n{card}\n```")
        except Exception as e:
            logger.error(f"/scanbacktest error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Scan backtest error: {e}"))

    @tree.command(name="state", description="Current market state (Trend/Ranging/Volatile) with active strategy guide")
    async def cmd_state(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from market_state import format_state_card
            card = format_state_card()
            await _safe_send(interaction, content=f"```\n{card}\n```")
        except Exception as e:
            logger.error(f"/state error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"State error: {e}"))

    @tree.command(name="weights", description="Adaptive indicator weights — which indicators predict wins most accurately")
    async def cmd_weights(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            from adaptive_scoring import format_weights_card
            card = format_weights_card()
            await _safe_send(interaction, content=f"```\n{card}\n```")
        except Exception as e:
            logger.error(f"/weights error: {e}")
            from alerts import error_embed
            await _safe_send(interaction, embed=error_embed(f"Weights error: {e}"))

    logger.info(f"Registered 29 slash commands on command tree")


# ══════════════════════════════════════════════════════════════════════════
# TUTORIAL MESSAGES (8 sequential messages)
# ══════════════════════════════════════════════════════════════════════════

def _build_tutorial_messages() -> list:
    """Build the 8 tutorial messages."""
    msgs = []

    msgs.append(
        f"```\n"
        f"📚 {BOT_NAME} TUTORIAL (1/8) — Welcome\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Welcome to {BOT_NAME} v{VERSION}!\n\n"
        f"This bot scans 130+ stocks every 60 seconds\n"
        f"using 12 technical indicators across 4 timeframes\n"
        f"to find high-confidence trading opportunities.\n\n"
        f"It runs 5 proven strategies:\n"
        f"  1. VWAP Momentum Breakout\n"
        f"  2. Opening Range Breakout (ORB)\n"
        f"  3. EMA Pullback in Trend\n"
        f"  4. Fibonacci Confluence Reversal\n"
        f"  5. Bollinger Band Squeeze Breakout\n\n"
        f"Trade alerts only run during regular market hours.\n"
        f"Closed-market commands show status or cached reference data only.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    msgs.append(
        f"```\n"
        f"📚 TUTORIAL (2/8) — How Alerts Work\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"When a stock meets all criteria, you get\n"
        f"a rich embed alert with:\n\n"
        f"  🟩🟩🟩🟩🟩🟩🟩🟩⬜⬜ 82/100 ⚠️\n\n"
        f"The confidence score is 0-100 based on:\n"
        f"  • Trend Alignment   (0-20 pts)\n"
        f"  • Momentum          (0-20 pts)\n"
        f"  • Volume            (0-20 pts)\n"
        f"  • Relative Strength (0-20 pts)\n"
        f"  • Risk Structure    (0-20 pts)\n\n"
        f"Flags:\n"
        f"  🔥 95+  High Conviction\n"
        f"  ✅ 85+  Strong Signal\n"
        f"  ⚠️ 70+  Caution\n"
        f"  ⚪ <70  Below threshold\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    msgs.append(
        f"```\n"
        f"📚 TUTORIAL (3/8) — Timeframe Alignment\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Each alert shows alignment across 4 timeframes:\n\n"
        f"  📊 Timeframe Alignment:\n"
        f"    1m   ✅ BULLISH\n"
        f"    5m   ✅ BULLISH\n"
        f"    15m  ⚪ NEUTRAL\n"
        f"    1h   ✅ BULLISH\n\n"
        f"  ✅ = Bullish   ⚪ = Neutral   ❌ = Bearish\n\n"
        f"The more ✅ you see, the stronger the setup.\n"
        f"Ideally you want 3-4 timeframes aligned.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    msgs.append(
        f"```\n"
        f"📚 TUTORIAL (4/8) — Trade Setup\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Every alert includes exact trade levels:\n\n"
        f"  📐 Trade Setup:\n"
        f"    Entry:     $135.42\n"
        f"    Stop Loss: $133.20 (-1.64%)\n"
        f"    Target 1:  $137.85 (+1.79%)  [1.5:1]\n"
        f"    Target 2:  $140.10 (+3.46%)  [2.5:1]\n\n"
        f"Use /risk to calculate position size based\n"
        f"on your account and risk tolerance.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    msgs.append(
        f"```\n"
        f"📚 TUTORIAL (5/8) — Market Regime\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"The bot detects 3 market regimes from SPY:\n\n"
        f"  📈 TRENDING (ADX >= 25)\n"
        f"     All 5 strategies active\n\n"
        f"  ↔️ RANGING (ADX < 20)\n"
        f"     Fib, BB Squeeze, EMA Pullback\n\n"
        f"  ⚡ HIGH VOLATILITY (ATR > 1.5x avg)\n"
        f"     VWAP Breakout, ORB only\n\n"
        f"Use /regime to check the current state.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    msgs.append(
        f"```\n"
        f"📚 TUTORIAL (6/8) — Backtesting\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  /backtest NVDA 30\n"
        f"    → Runs strategies on 30 days of data\n\n"
        f"  /replay NVDA 2024-01-15\n"
        f"    → Replay a specific trading day\n\n"
        f"  /goodday\n"
        f"    → Auto-find the best day from 6 months\n\n"
        f"  /simulate NVDA\n"
        f"    → Run current signal forward 12 candles\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    msgs.append(
        f"```\n"
        f"📚 TUTORIAL (7/8) — Risk Management\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  /risk NVDA 25000 1.0\n"
        f"    → $25k account, 1% risk per trade\n"
        f"    → Shows exact share count and risk\n\n"
        f"  /stats    → Win rate, avg R, streak\n"
        f"  /history  → Recent closed trades\n\n"
        f"R-multiples: +1.0R = gained 1x risk\n"
        f"             -1.0R = lost stop distance\n"
        f"Aim for: 50%+ win rate, avg R > 0.5\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    msgs.append(
        f"```\n"
        f"📚 TUTORIAL (8/8) — All Commands\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 /scan /best /indicators /price\n"
        f"🌍 /market /news /regime /watchlist\n"
        f"🧪 /test /backtest /replay /goodday\n"
        f"   /simulate /mockmarket\n"
        f"📈 /risk /stats /history\n"
        f"⚡ /alert /myalerts /cancelalert\n"
        f"🔧 /quiet /plainenglish\n"
        f"ℹ️ /tutorial /ping /help\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n```"
    )

    return msgs
