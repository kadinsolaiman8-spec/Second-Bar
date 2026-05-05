# main.py — Bot entry point, NO COGS, register_commands pattern, APScheduler setup
#
# UX features integrated:
#   - All alerts sent as discord.Embed with AlertView buttons
#   - Best buys digest uses embeds + BestListView buttons
#   - Price alerts checked every scan cycle, triggered alerts sent as DM pings
#   - Pinned morning summary embed at 9:15 AM ET (unpins previous day)
#   - Tip of the day sent at 9:15 AM ET
#   - Quiet mode suppresses auto-alerts per-channel

import asyncio
import logging
import os
import warnings
from datetime import datetime

import discord
from discord import app_commands
from dotenv import load_dotenv
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from colorama import Fore, Style, init as colorama_init

load_dotenv()
colorama_init()

# ── Suppress noisy third-party logging ────────────────────────────────────
warnings.filterwarnings("ignore")
for _logger_name in ["yfinance", "urllib3", "requests", "peewee",
                      "asyncio", "discord.gateway", "discord.http",
                      "discord.client", "charset_normalizer"]:
    logging.getLogger(_logger_name).setLevel(logging.CRITICAL)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("discord").setLevel(logging.WARNING)

from config import (
    SCAN_INTERVAL_SECONDS, NEWS_INTERVAL_SECONDS, REGIME_INTERVAL_SECONDS,
    BEST_BUYS_INTERVAL_MINUTES, BEST_BUYS_MIN_CONFIDENCE, MARKET_TIMEZONE,
    DISCORD_MESSAGE_DELAY_SECONDS, BOT_NAME, VERSION,
    PREMARKET_BRIEFING_HOUR, PREMARKET_BRIEFING_MINUTE,
    EOD_RECAP_HOUR, EOD_RECAP_MINUTE,
)
from watchlist import ALL_STOCKS
from scanner import (
    scan_all_tickers, is_market_open, refresh_spy_cache,
    ticker_state, get_best_stocks, reset_orb_data,
    fetch_ohlcv_async, spy_cache,
    is_circuit_breaker_active, get_circuit_breaker_resume_time,
)
from regime import update_regime, get_current_regime
from news import refresh_all_news
from alerts import (
    build_alert_card, build_best_buys_card, build_morning_summary_embed,
    build_price_alert_embed, build_tip_embed,
    build_market_open_embed, build_market_close_embed, build_midday_quiet_embed,
)
from ui import AlertView, BestListView
from cooldown import cleanup_expired_cooldowns, cleanup_expired_mutes, set_cooldown
from commands import register_commands, check_all_price_alerts, is_quiet
from journal import check_trade_exits, get_open_trades, get_journal_summary, get_streak_celebration
from testing import log_scan_event, check_price_override_expiry
from tips import get_tip_of_the_day, get_tip_number

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

ET = pytz.timezone(MARKET_TIMEZONE)

# ── Load env vars ──────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set in .env")
if ALERT_CHANNEL_ID == 0:
    raise RuntimeError("ALERT_CHANNEL_ID is not set in .env")


# ── Discord Bot (NO COGS — single commands.py) ────────────────────────────
intents = discord.Intents.default()
intents.message_content = True


class TradingBot(discord.Client):
    """
    Main bot class — extends discord.Client directly (not commands.Bot).
    All slash commands are registered via register_commands(tree, bot).
    """

    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.scheduler = AsyncIOScheduler(timezone=ET)
        self._alert_channel: discord.TextChannel | None = None
        self._scan_running = False
        self.start_time = datetime.now(ET)
        self._last_pinned_summary_id: int | None = None

    async def setup_hook(self):
        """Register commands, sync tree, start scheduler."""
        # ── Register all slash commands ────────────────────────────
        register_commands(self.tree, self)
        logger.info(f"{Fore.GREEN}All slash commands registered.{Style.RESET_ALL}")

        # ── Sync slash commands globally ───────────────────────────
        await self.tree.sync()
        logger.info(f"{Fore.GREEN}Slash commands synced globally.{Style.RESET_ALL}")

        # ── Interval jobs ──────────────────────────────────────────
        self.scheduler.add_job(
            self._run_scan_cycle,
            "interval",
            seconds=SCAN_INTERVAL_SECONDS,
            id="scan_cycle",
            max_instances=1,
        )
        self.scheduler.add_job(
            self._run_regime_update,
            "interval",
            seconds=REGIME_INTERVAL_SECONDS,
            id="regime_update",
            max_instances=1,
        )
        self.scheduler.add_job(
            self._run_news_refresh,
            "interval",
            seconds=NEWS_INTERVAL_SECONDS,
            id="news_refresh",
            max_instances=1,
        )
        self.scheduler.add_job(
            self._send_best_buys_digest,
            "interval",
            minutes=BEST_BUYS_INTERVAL_MINUTES,
            id="best_buys",
            max_instances=1,
        )
        self.scheduler.add_job(
            self._cleanup_cycle,
            "interval",
            minutes=5,
            id="cleanup",
        )
        self.scheduler.add_job(
            self._check_open_trades,
            "interval",
            seconds=SCAN_INTERVAL_SECONDS,
            id="trade_check",
            max_instances=1,
        )

        # ── Morning summary + tip of the day (daily at 9:15 AM ET) ─
        self.scheduler.add_job(
            self._send_morning_summary,
            "cron",
            hour=PREMARKET_BRIEFING_HOUR,
            minute=PREMARKET_BRIEFING_MINUTE,
            day_of_week="mon-fri",
            id="morning_summary",
            max_instances=1,
        )
        self.scheduler.add_job(
            self._send_tip_of_the_day,
            "cron",
            hour=PREMARKET_BRIEFING_HOUR,
            minute=PREMARKET_BRIEFING_MINUTE,
            day_of_week="mon-fri",
            id="tip_of_day",
            max_instances=1,
        )

        # ── EOD recap (daily at 4:15 PM ET) ───────────────────────
        self.scheduler.add_job(
            self._send_eod_recap,
            "cron",
            hour=EOD_RECAP_HOUR,
            minute=EOD_RECAP_MINUTE,
            day_of_week="mon-fri",
            id="eod_recap",
            max_instances=1,
        )

        # ── Market open announcement at 9:30 AM ET ─────────────────
        self.scheduler.add_job(
            self._send_market_open,
            "cron",
            hour=9,
            minute=30,
            day_of_week="mon-fri",
            id="market_open",
            max_instances=1,
        )

        # ── Midday quiet notification at 11:30 AM ET ─────────────
        self.scheduler.add_job(
            self._send_midday_quiet,
            "cron",
            hour=11,
            minute=30,
            day_of_week="mon-fri",
            id="midday_quiet",
            max_instances=1,
        )

        # ── Market close announcement at 4:00 PM ET ──────────────
        self.scheduler.add_job(
            self._send_market_close,
            "cron",
            hour=16,
            minute=0,
            day_of_week="mon-fri",
            id="market_close",
            max_instances=1,
        )

        # ── Daily ORB reset at 9:29 AM ET ─────────────────────────
        self.scheduler.add_job(
            self._reset_daily_data,
            "cron",
            hour=9,
            minute=29,
            day_of_week="mon-fri",
            id="daily_reset",
        )

        self.scheduler.start()
        logger.info(f"APScheduler started with {len(self.scheduler.get_jobs())} jobs.")

    async def on_ready(self):
        # ── Startup banner ─────────────────────────────────────────
        cmd_count = len(self.tree.get_commands())
        banner = (
            f"\n{Fore.CYAN}"
            f"╔══════════════════════════════════════════╗\n"
            f"║  {BOT_NAME} v{VERSION:<34} ║\n"
            f"║  Logged in as {str(self.user):<26} ║\n"
            f"╠══════════════════════════════════════════╣\n"
            f"║  Strategies:  5                          ║\n"
            f"║  Watchlist:   {len(ALL_STOCKS):<3} stocks                  ║\n"
            f"║  Commands:    {cmd_count:<3}                          ║\n"
            f"║  Scheduler:   {len(self.scheduler.get_jobs()):<3} jobs                    ║\n"
            f"╚══════════════════════════════════════════╝"
            f"{Style.RESET_ALL}"
        )
        logger.info(banner)

        self._alert_channel = self.get_channel(ALERT_CHANNEL_ID)
        if self._alert_channel is None:
            logger.error(f"{Fore.RED}Could not find channel ID {ALERT_CHANNEL_ID}. "
                         f"Check ALERT_CHANNEL_ID in .env{Style.RESET_ALL}")
        else:
            logger.info(f"Alert channel: #{self._alert_channel.name}")

        # ── Verify alert system ────────────────────────────────────
        await self._verify_and_announce()

        # ── Startup: immediate full scan ───────────────────────────
        logger.info("Running startup scan...")
        await refresh_spy_cache()
        await self._run_regime_update()
        await self._run_news_refresh()
        await self._run_scan_cycle()
        logger.info(f"{Fore.GREEN}Startup scan complete. Bot is live.{Style.RESET_ALL}")

    # ══════════════════════════════════════════════════════════════════════
    # STARTUP VERIFICATION AND ANNOUNCEMENT
    # ══════════════════════════════════════════════════════════════════════

    async def _verify_and_announce(self):
        """Verify alert system is functional and send startup embed to alert channel."""
        # Verify scheduler
        jobs = self.scheduler.get_jobs()
        scan_job = next((j for j in jobs if "scan" in j.id), None)
        if scan_job:
            logger.info(f"Scanner scheduled. Next run: {scan_job.next_run_time}")
        else:
            logger.error("Scanner NOT scheduled!")

        if self._alert_channel is None:
            logger.error("Cannot send startup embed — alert channel not found.")
            return

        market_status = "Open" if is_market_open() else "Closed"
        embed = discord.Embed(
            title=f"\U0001f916 {BOT_NAME} V{VERSION} \u2014 Online",
            color=0x57F287,
        )
        embed.add_field(
            name="Status",
            value="\u2705 All systems operational",
            inline=False,
        )
        embed.add_field(name="Watchlist", value=f"`{len(ALL_STOCKS)}` stocks", inline=True)
        embed.add_field(name="Scan Speed", value=f"Every `{SCAN_INTERVAL_SECONDS}s`", inline=True)
        embed.add_field(name="Strategies", value="`5` active", inline=True)
        embed.add_field(
            name="Alert Conditions",
            value=(
                "\u2705 Minimum confidence: `65/100`\n"
                "\u2705 Market state: Strong/Weak Trend only\n"
                "\u2705 Trading hours: `9:45AM \u2014 11:30AM` & `3:00PM \u2014 3:50PM` ET\n"
                "\u2705 Cooldown: `45 min` per ticker\n"
                "\u2705 BUY signals only"
            ),
            inline=False,
        )
        embed.add_field(
            name="Market",
            value=f"`{market_status}` \u2014 Type `/help` to see all commands.",
            inline=False,
        )
        now = datetime.now(ET).strftime("%I:%M %p ET")
        embed.set_footer(text=f"{BOT_NAME} V{VERSION[0]} \u2022 {now}")

        try:
            await self._alert_channel.send(embed=embed)
            logger.info("Startup announcement sent to alert channel.")
        except Exception as e:
            logger.error(f"Failed to send startup embed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # SCAN CYCLE — embeds, AlertView buttons, price alert dispatch
    # ══════════════════════════════════════════════════════════════════════

    async def _run_scan_cycle(self):
        """Main scan loop — runs every SCAN_INTERVAL_SECONDS."""
        if self._scan_running:
            logger.debug("Scan already in progress, skipping cycle.")
            return
        if not is_market_open():
            logger.debug("Market closed. Skipping scan.")
            return

        self._scan_running = True
        try:
            await refresh_spy_cache()

            now_str = datetime.now(ET).strftime("%H:%M:%S ET")
            signals = await scan_all_tickers(ALL_STOCKS)
            regime = get_current_regime()

            # Log scan event
            log_scan_event({
                "stocks_scanned": len(ALL_STOCKS),
                "signals_above_70": sum(
                    1 for s in signals if s.get("score_data", {}).get("total", 0) >= 70
                ),
                "alerts_sent": len(signals),
                "regime": regime.get("label", "UNKNOWN"),
            })

            logger.info(
                f"[{now_str}] Scan complete — "
                f"{len(ALL_STOCKS)} stocks | "
                f"{len(signals)} signals | "
                f"Regime: {regime.get('label', 'UNKNOWN')}"
            )

            # ── Send trade alerts as embeds with buttons ───────────
            if self._alert_channel and signals:
                # Check quiet mode for this channel
                channel_quiet = is_quiet(self._alert_channel.id)

                for signal in signals:
                    if channel_quiet:
                        logger.debug(f"Quiet mode — suppressing alert for {signal['ticker']}")
                        # Still set cooldown even when quiet
                        set_cooldown(
                            signal["ticker"],
                            signal["direction"],
                            signal["entry_price"],
                            signal.get("indicators", {}).get("atr", {}).get("value", 0),
                        )
                        continue

                    try:
                        embed = build_alert_card(
                            ticker=signal["ticker"],
                            direction=signal["direction"],
                            strategy=signal.get("strategy", "Unknown"),
                            entry_price=signal["entry_price"],
                            stop=signal.get("stop", 0),
                            tp1=signal.get("tp1", 0),
                            tp2=signal.get("tp2", 0),
                            rr=signal.get("rr", 0),
                            score_data=signal.get("score_data", {}),
                            indicators=signal.get("indicators", {}),
                            regime_label=signal.get("regime", "TRENDING"),
                            bullish_count=signal.get("bullish_count", 0),
                            bearish_count=signal.get("bearish_count", 0),
                            conditions_met=signal.get("conditions_met", []),
                            conditions_failed=signal.get("conditions_failed", []),
                            tp3=signal.get("tp3", 0),
                            regime_quality=signal.get("regime_quality", 0),
                        )

                        # Attach AlertView with Full Analysis + Trade Plan buttons
                        sig_indicators = signal.get("indicators", {})
                        sig_atr = sig_indicators.get("atr", {}).get("value", 0) if sig_indicators else 0
                        view = AlertView(
                            ticker=signal["ticker"],
                            direction=signal["direction"],
                            entry_price=signal["entry_price"],
                            stop_price=signal.get("stop", 0),
                            bot=self,
                            atr=sig_atr,
                            indicators=sig_indicators,
                            strategy=signal.get("strategy", ""),
                            regime=signal.get("regime", ""),
                        )

                        msg = await self._alert_channel.send(embed=embed, view=view)
                        view.message = msg  # for on_timeout button disable

                        # Auto-cooldown after alert
                        set_cooldown(
                            signal["ticker"],
                            signal["direction"],
                            signal["entry_price"],
                            signal.get("indicators", {}).get("atr", {}).get("value", 0),
                        )

                        await asyncio.sleep(DISCORD_MESSAGE_DELAY_SECONDS)
                    except Exception as e:
                        logger.error(f"Alert send error for {signal['ticker']}: {e}")

            # ── Check and dispatch price alerts ────────────────────
            await self._dispatch_price_alerts()

        except Exception as e:
            logger.error(f"Scan cycle error: {e}")
        finally:
            self._scan_running = False

    async def _dispatch_price_alerts(self):
        """Check all user price alerts against current prices and send notifications."""
        try:
            triggered = check_all_price_alerts(ticker_state)
            if not triggered:
                return

            for item in triggered:
                user_id = item["user_id"]
                alert = item["alert"]
                price = item["price"]

                embed = build_price_alert_embed(
                    ticker=alert["ticker"],
                    current_price=price,
                    target_price=alert["target"],
                    direction=alert["direction"],
                )

                # Try to DM the user; fall back to alert channel
                try:
                    user = await self.fetch_user(user_id)
                    if user:
                        await user.send(embed=embed)
                        logger.info(f"Price alert DM sent to {user_id} for {alert['ticker']}")
                except Exception:
                    # DMs might be disabled — send to alert channel with mention
                    if self._alert_channel:
                        await self._alert_channel.send(
                            content=f"<@{user_id}>",
                            embed=embed,
                        )
                        logger.info(f"Price alert sent to channel for user {user_id}, {alert['ticker']}")

        except Exception as e:
            logger.error(f"Price alert dispatch error: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # BEST BUYS DIGEST — embed + BestListView buttons
    # ══════════════════════════════════════════════════════════════════════

    async def _send_best_buys_digest(self):
        """Send periodic best-buys digest to alert channel."""
        if not is_market_open() or self._alert_channel is None:
            return
        try:
            top = get_best_stocks(min_score=BEST_BUYS_MIN_CONFIDENCE, limit=5)
            if not top:
                return

            embed = build_best_buys_card(top)
            tickers = [s.get("ticker", "") for s in top if s.get("ticker")]
            scores = [s.get("score_data", {}).get("total", s.get("score", 0)) for s in top]
            view = BestListView(tickers, bot=self, scores=scores)

            msg = await self._alert_channel.send(embed=embed, view=view)
            view.message = msg  # for on_timeout button disable
        except Exception as e:
            logger.error(f"Best buys digest error: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # MORNING SUMMARY — pinned embed + tip of the day
    # ══════════════════════════════════════════════════════════════════════

    async def _send_morning_summary(self):
        """Pin a clean morning summary embed at 9:15 AM ET. Unpins previous day's pin."""
        channel = self._alert_channel
        if channel is None:
            return
        try:
            # Unpin previous day's summary if we have one
            if self._last_pinned_summary_id:
                try:
                    old_msg = await channel.fetch_message(self._last_pinned_summary_id)
                    await old_msg.unpin()
                    logger.debug("Unpinned previous morning summary.")
                except Exception:
                    pass  # message deleted or already unpinned

            # Gather pre-market data
            regime = get_current_regime()
            regime_label = regime.get("label", "UNKNOWN")

            # SPY data from cache
            spy_price = 0.0
            spy_change = 0.0
            spy_state = ticker_state.get("SPY", {})
            if spy_state:
                spy_price = spy_state.get("price", 0.0)
                spy_change = spy_state.get("change_pct", 0.0)

            # VIX — try to get from ticker state or default
            vix_val = 0.0
            vix_state = ticker_state.get("VIX", {})
            if vix_state:
                vix_val = vix_state.get("price", 0.0)

            # Gap-ups from pre-market data
            gap_ups = []
            for ticker, state in ticker_state.items():
                chg = state.get("change_pct", 0)
                if chg > 2.0:
                    gap_ups.append({"ticker": ticker, "change": chg})
            gap_ups.sort(key=lambda x: x["change"], reverse=True)

            embed = build_morning_summary_embed(
                spy_price=spy_price,
                spy_change=spy_change,
                vix_val=vix_val,
                regime_label=regime_label,
                gap_ups=gap_ups[:3],
            )

            msg = await channel.send(embed=embed)

            # Pin the morning summary
            try:
                await msg.pin()
                self._last_pinned_summary_id = msg.id
                logger.info("Morning summary pinned.")
            except Exception as e:
                logger.warning(f"Could not pin morning summary: {e}")

        except Exception as e:
            logger.error(f"Morning summary error: {e}")

    async def _send_tip_of_the_day(self):
        """Send the tip of the day embed at 9:15 AM ET."""
        channel = self._alert_channel
        if channel is None:
            return
        try:
            tip_text = get_tip_of_the_day()
            tip_num = get_tip_number()
            embed = build_tip_embed(tip_text, tip_num)
            await channel.send(embed=embed)
            logger.info(f"Tip of the day #{tip_num} sent.")
        except Exception as e:
            logger.error(f"Tip of the day error: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # OTHER SCHEDULED JOBS
    # ══════════════════════════════════════════════════════════════════════

    async def _run_regime_update(self):
        """Update market regime from SPY data."""
        try:
            spy_df = await fetch_ohlcv_async("SPY", "15m")
            if spy_df is not None and not spy_df.empty:
                update_regime(spy_df)
        except Exception as e:
            logger.error(f"Regime update error: {e}")

    async def _run_news_refresh(self):
        """Refresh news for all watched tickers."""
        if not is_market_open():
            return
        try:
            await refresh_all_news(ALL_STOCKS)
            logger.debug("News refreshed for all tickers.")
        except Exception as e:
            logger.error(f"News refresh error: {e}")

    async def _check_open_trades(self):
        """Check open paper trades for exits and send streak celebrations."""
        if not is_market_open():
            return
        try:
            open_trades = get_open_trades()
            for trade_id, trade in open_trades.items():
                ticker = trade.get("ticker", "")
                state = ticker_state.get(ticker, {})
                if not state:
                    continue
                high = state.get("price", 0) * 1.001  # approximate
                low = state.get("price", 0) * 0.999
                current = state.get("price", 0)
                check_trade_exits(trade_id, high, low, current)

            # Check for streak milestone celebrations
            celebration = get_streak_celebration()
            if celebration and self._alert_channel:
                embed = discord.Embed(description=celebration, color=0xFF8C00)
                try:
                    await self._alert_channel.send(embed=embed)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Trade check error: {e}")

    async def _cleanup_cycle(self):
        """Periodic cleanup of expired cooldowns, mutes, and price overrides."""
        cleanup_expired_cooldowns()
        cleanup_expired_mutes()
        check_price_override_expiry()

    async def _send_market_open(self):
        """Send market open announcement at 9:30 AM ET."""
        channel = self._alert_channel
        if channel is None:
            return
        try:
            embed = build_market_open_embed()
            await channel.send(embed=embed)
            logger.info("Market open announcement sent.")
        except Exception as e:
            logger.error(f"Market open announcement error: {e}")

    async def _send_midday_quiet(self):
        """Send midday quiet hours notification at 11:30 AM ET."""
        channel = self._alert_channel
        if channel is None:
            return
        try:
            embed = build_midday_quiet_embed()
            await channel.send(embed=embed)
            logger.info("Midday quiet hours notification sent.")
        except Exception as e:
            logger.error(f"Midday quiet notification error: {e}")

    async def _send_market_close(self):
        """Send market close announcement at 4:00 PM ET."""
        channel = self._alert_channel
        if channel is None:
            return
        try:
            # Gather today's stats
            alerts_today = len([t for t, st in ticker_state.items()
                               if st.get("last_alert_today")])
            summary = get_journal_summary()
            win_rate = 0.0
            best_ticker = ""
            best_pct = 0.0
            if isinstance(summary, dict):
                win_rate = summary.get("win_rate", 0.0)
                best_ticker = summary.get("best_ticker", "")
                best_pct = summary.get("best_pct", 0.0)

            embed = build_market_close_embed(
                alerts_today=alerts_today,
                win_rate=win_rate,
                best_ticker=best_ticker,
                best_pct=best_pct,
            )
            await channel.send(embed=embed)
            logger.info("Market close announcement sent.")
        except Exception as e:
            logger.error(f"Market close announcement error: {e}")

    async def _send_eod_recap(self):
        """Send automated end-of-day recap as embed."""
        channel = self._alert_channel
        if channel is None:
            return
        try:
            summary = get_journal_summary()
            regime = get_current_regime()

            embed = discord.Embed(
                title=f"🌙 End-of-Day Recap — {datetime.now(ET).strftime('%A %b %d')}",
                color=0x4444FF,
            )
            embed.add_field(name="Regime", value=regime.get("label", "UNKNOWN"), inline=True)
            embed.add_field(name="Stocks Scanned", value=str(len(ALL_STOCKS)), inline=True)

            # Add journal summary
            if isinstance(summary, str) and summary:
                embed.description = summary
            elif isinstance(summary, dict):
                if summary.get("total_trades", 0) > 0:
                    embed.add_field(
                        name="Trades Today",
                        value=str(summary.get("total_trades", 0)),
                        inline=True,
                    )
                    embed.add_field(
                        name="Win Rate",
                        value=f"{summary.get('win_rate', 0):.0f}%",
                        inline=True,
                    )
                    embed.add_field(
                        name="P&L",
                        value=f"${summary.get('total_pnl', 0):+.2f}",
                        inline=True,
                    )
                else:
                    embed.description = "No trades recorded today."

            embed.set_footer(text=datetime.now(ET).strftime("%I:%M %p ET"))
            await channel.send(embed=embed)
            logger.info("EOD recap sent.")
        except Exception as e:
            logger.error(f"EOD recap error: {e}")

    async def _reset_daily_data(self):
        """Reset daily data at start of trading day."""
        reset_orb_data()
        from journal import reset_daily_stats
        reset_daily_stats()
        logger.info("Daily data reset (ORB, journal daily stats).")


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    bot = TradingBot()
    logger.info(f"{Fore.CYAN}Starting {BOT_NAME} v{VERSION}...{Style.RESET_ALL}")
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
