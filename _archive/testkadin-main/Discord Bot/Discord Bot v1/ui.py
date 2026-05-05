# ui.py — All interactive UI components: Views, Buttons, Select menus
#
# Views:
#   ScanResultView  — Refresh, More News, Risk Calculator, Compare buttons
#   AlertView       — Full Analysis, Calculate Risk buttons
#   BestListView    — One "Scan" button per stock (up to 5)
#   RiskConfirmView — Single "I Understand the Risk" button (one-shot)

import asyncio
import logging
from datetime import datetime
from typing import Optional

import discord
from discord import ui
import pytz

from config import (
    BUTTON_EXPIRY_SECONDS, MARKET_TIMEZONE,
    EMBED_COLOR_BUY, EMBED_COLOR_SELL, EMBED_COLOR_INFO,
    EMBED_COLOR_ERROR, EMBED_COLOR_CAUTION,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _embed_color_for_direction(direction: str, score: int = 0) -> int:
    """Pick embed colour based on direction and score."""
    if score >= 95:
        return 0xFF8800  # high conviction
    if direction == "BUY":
        return EMBED_COLOR_BUY
    # SELL color removed — BUY only
    return EMBED_COLOR_INFO


async def _safe_respond(interaction: discord.Interaction, **kwargs):
    """Respond or follow-up, whichever is valid."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except Exception as e:
        logger.debug(f"_safe_respond error: {e}")


# ══════════════════════════════════════════════════════════════════════════
# SCAN RESULT VIEW  (4 buttons: Refresh, More News, Risk Calculator, Compare)
# ══════════════════════════════════════════════════════════════════════════

class ScanResultView(ui.View):
    """Button row attached to /scan results. Expires after 10 minutes."""

    def __init__(self, ticker: str, direction: str, entry_price: float,
                 stop_price: float, bot=None, atr: float = 0,
                 indicators: dict = None, strategy: str = "", regime: str = ""):
        super().__init__(timeout=BUTTON_EXPIRY_SECONDS)
        self.ticker = ticker.upper()
        self.direction = direction
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.bot = bot
        self.atr = atr or (entry_price * 0.01 if entry_price else 0)
        self.indicators = indicators or {}
        self.strategy = strategy
        self.regime = regime

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if hasattr(self, "message") and self.message:
                await self.message.edit(view=self)
        except Exception:
            pass

    # ── Refresh ─────────────────────────────────────────────────────────
    @ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
    async def btn_refresh(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        try:
            from scanner import scan_ticker
            from alerts import build_scan_embed

            result = await scan_ticker(self.ticker)
            if result:
                embed = build_scan_embed(result)
                view = ScanResultView(
                    self.ticker, result.get("direction", self.direction),
                    result.get("entry_price", self.entry_price),
                    result.get("stop", self.stop_price), self.bot,
                )
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                from alerts import build_ticker_embed
                embed = build_ticker_embed(self.ticker)
                await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Refresh button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Refresh failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)

    # ── More News ───────────────────────────────────────────────────────
    @ui.button(label="More News", emoji="📰", style=discord.ButtonStyle.secondary, row=0)
    async def btn_more_news(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=False)
        try:
            from news import fetch_news, get_cached_news, classify_sentiment, get_sentiment_emoji
            headlines = get_cached_news(self.ticker)
            if not headlines:
                await fetch_news(self.ticker)
                headlines = get_cached_news(self.ticker)

            embed = discord.Embed(
                title=f"📰 News — {self.ticker}",
                color=EMBED_COLOR_INFO,
            )
            if not headlines:
                embed.description = "No recent headlines found."
            else:
                for i, item in enumerate(headlines[:5]):
                    headline = item.get("headline", "No headline")
                    source = item.get("source", "Unknown")
                    sentiment = classify_sentiment(headline)
                    emoji = get_sentiment_emoji(sentiment)
                    ts = item.get("datetime", 0)
                    time_str = datetime.fromtimestamp(ts, tz=ET).strftime("%I:%M %p ET") if ts else ""
                    embed.add_field(
                        name=f"{emoji} {headline[:80]}",
                        value=f"*{source}* {time_str}",
                        inline=False,
                    )
            embed.set_footer(text=f"{datetime.now(ET).strftime('%I:%M %p ET')}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"More News button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"News fetch failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)

    # ── Risk Calculator ─────────────────────────────────────────────────
    @ui.button(label="Risk Calculator", emoji="💰", style=discord.ButtonStyle.secondary, row=0)
    async def btn_risk_calc(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=False)
        try:
            from utils import calculate_position_size
            result = calculate_position_size(
                account_size=10_000, risk_pct=1.0,
                entry=self.entry_price, stop=self.stop_price,
            )
            shares = result.get("shares", 0)
            dollar_risk = result.get("dollar_risk", 0)
            position_value = result.get("position_value", 0)

            embed = discord.Embed(
                title=f"💰 Risk Calculator — {self.ticker}",
                color=EMBED_COLOR_CAUTION,
            )
            embed.add_field(name="Account Size", value="$10,000", inline=True)
            embed.add_field(name="Risk %", value="1.0%", inline=True)
            embed.add_field(name="Max Risk", value=f"${dollar_risk:.2f}", inline=True)
            embed.add_field(name="Entry Price", value=f"${self.entry_price:.2f}", inline=True)
            embed.add_field(name="Stop Loss", value=f"${self.stop_price:.2f}", inline=True)
            embed.add_field(name="Risk/Share",
                            value=f"${abs(self.entry_price - self.stop_price):.2f}", inline=True)
            embed.add_field(name="Position Size", value=f"{shares} shares", inline=True)
            embed.add_field(name="Position Value", value=f"${position_value:.2f}", inline=True)
            embed.set_footer(text=f"Adjust with /risk {self.ticker} [account] [risk%]")

            view = RiskConfirmView(self.ticker, self.stop_price)
            await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Risk calc button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Risk calculation failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)

    # ── Compare ─────────────────────────────────────────────────────────
    @ui.button(label="Compare", emoji="⚔️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_compare(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            f"Which stock do you want to compare **{self.ticker}** against? "
            f"Type a ticker below (30 second timeout):",
            ephemeral=True,
        )
        try:
            def check(m):
                return (m.author.id == interaction.user.id
                        and m.channel.id == interaction.channel.id
                        and len(m.content.strip()) <= 6)

            msg = await self.bot.wait_for("message", check=check, timeout=30.0)
            other = msg.content.strip().upper()

            from scanner import get_ticker_state
            from alerts import build_compare_embed

            s1 = get_ticker_state(self.ticker)
            s2 = get_ticker_state(other)
            if not s1 and not s2:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"No data cached for {self.ticker} or {other}. "
                                    f"Try /scan on both first.",
                        color=EMBED_COLOR_ERROR),
                    ephemeral=True)
                return

            embed = build_compare_embed(self.ticker, s1, other, s2)
            await interaction.channel.send(embed=embed)

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Compare timed out — no ticker received within 30 seconds.",
                ephemeral=True)
        except Exception as e:
            logger.error(f"Compare button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Compare failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)

    # ── Trade Plan ─────────────────────────────────────────────────────
    @ui.button(label="Trade Plan", emoji="📋", style=discord.ButtonStyle.success, row=1)
    async def btn_trade_plan(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        try:
            from alerts import build_trade_plan_embed
            embed = build_trade_plan_embed(
                ticker=self.ticker,
                entry_price=self.entry_price,
                stop=self.stop_price,
                atr=self.atr,
                indicators=self.indicators,
                strategy=self.strategy,
                regime=self.regime,
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Trade plan button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Trade plan failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════
# ALERT VIEW  (2 buttons: Full Analysis, Calculate Risk)
# ══════════════════════════════════════════════════════════════════════════

class AlertView(ui.View):
    """Button row attached to automatic trade alerts."""

    def __init__(self, ticker: str, direction: str, entry_price: float,
                 stop_price: float, bot=None, atr: float = 0,
                 indicators: dict = None, strategy: str = "", regime: str = ""):
        super().__init__(timeout=BUTTON_EXPIRY_SECONDS)
        self.ticker = ticker.upper()
        self.direction = direction
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.bot = bot
        self.atr = atr or (entry_price * 0.01 if entry_price else 0)
        self.indicators = indicators or {}
        self.strategy = strategy
        self.regime = regime

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if hasattr(self, "message") and self.message:
                await self.message.edit(view=self)
        except Exception:
            pass

    @ui.button(label="Full Analysis", emoji="📊", style=discord.ButtonStyle.primary, row=0)
    async def btn_full_analysis(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        try:
            from scanner import scan_ticker
            from alerts import build_scan_embed

            result = await scan_ticker(self.ticker)
            if result:
                embed = build_scan_embed(result)
                view = ScanResultView(
                    self.ticker, result.get("direction", self.direction),
                    result.get("entry_price", self.entry_price),
                    result.get("stop", self.stop_price), self.bot,
                )
                await interaction.followup.send(embed=embed, view=view)
            else:
                from alerts import build_ticker_embed
                embed = build_ticker_embed(self.ticker)
                await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Full analysis button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Analysis failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)

    @ui.button(label="Calculate Risk", emoji="💰", style=discord.ButtonStyle.secondary, row=0)
    async def btn_calc_risk(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=False)
        try:
            from utils import calculate_position_size
            result = calculate_position_size(
                account_size=10_000, risk_pct=1.0,
                entry=self.entry_price, stop=self.stop_price,
            )
            shares = result.get("shares", 0)
            dollar_risk = result.get("dollar_risk", 0)
            position_value = result.get("position_value", 0)

            embed = discord.Embed(
                title=f"💰 Quick Risk — {self.ticker} {self.direction}",
                color=EMBED_COLOR_CAUTION,
            )
            embed.add_field(name="Shares", value=str(shares), inline=True)
            embed.add_field(name="Risk $", value=f"${dollar_risk:.2f}", inline=True)
            embed.add_field(name="Position", value=f"${position_value:.2f}", inline=True)
            embed.add_field(name="Entry", value=f"${self.entry_price:.2f}", inline=True)
            embed.add_field(name="Stop", value=f"${self.stop_price:.2f}", inline=True)
            embed.set_footer(
                text=f"Defaults: $10K account, 1% risk. Adjust with /risk {self.ticker}")
            view = RiskConfirmView(self.ticker, self.stop_price)
            await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Risk calc button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Risk calculation failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)

    @ui.button(label="Trade Plan", emoji="📋", style=discord.ButtonStyle.success, row=1)
    async def btn_trade_plan(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        try:
            from alerts import build_trade_plan_embed
            embed = build_trade_plan_embed(
                ticker=self.ticker,
                entry_price=self.entry_price,
                stop=self.stop_price,
                atr=self.atr,
                indicators=self.indicators,
                strategy=self.strategy,
                regime=self.regime,
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Trade plan button error: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Trade plan failed: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════
# BEST LIST VIEW  (up to 5 "Scan" buttons, one per stock)
# ══════════════════════════════════════════════════════════════════════════

class _BestScanButton(ui.Button):
    """Single scan button for one stock in the /best list."""

    def __init__(self, ticker: str, score: int = 0, row: int = 0):
        label = f"\U0001f50d {ticker}"
        if score > 0:
            label += f" {score}/100"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"best_scan_{ticker}",
            row=row,
        )
        self.ticker = ticker

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            from scanner import scan_ticker
            from alerts import build_scan_embed, build_ticker_embed

            result = await scan_ticker(self.ticker)
            if result:
                embed = build_scan_embed(result)
                view = ScanResultView(
                    self.ticker,
                    result.get("direction", "BUY"),
                    result.get("entry_price", 0),
                    result.get("stop", 0),
                    self.view.bot if hasattr(self.view, "bot") else None,
                )
                await interaction.followup.send(embed=embed, view=view)
            else:
                embed = build_ticker_embed(self.ticker)
                await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Best scan button error for {self.ticker}: {e}")
            await interaction.followup.send(
                embed=discord.Embed(description=f"Scan failed for {self.ticker}: {e}",
                                    color=EMBED_COLOR_ERROR), ephemeral=True)


class BestListView(ui.View):
    """Button row for /best — one scan button per stock (up to 5) with scores."""

    def __init__(self, tickers: list, bot=None, scores: list = None):
        super().__init__(timeout=BUTTON_EXPIRY_SECONDS)
        self.bot = bot
        scores = scores or [0] * len(tickers)
        for i, ticker in enumerate(tickers[:5]):
            sc = scores[i] if i < len(scores) else 0
            self.add_item(_BestScanButton(ticker, score=sc, row=0 if i < 3 else 1))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if hasattr(self, "message") and self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
# RISK CONFIRM VIEW  (single-use "I Understand the Risk" button)
# ══════════════════════════════════════════════════════════════════════════

class RiskConfirmView(ui.View):
    """Single confirm button for /risk results. Disables after one click."""

    def __init__(self, ticker: str, stop_price: float):
        super().__init__(timeout=BUTTON_EXPIRY_SECONDS)
        self.ticker = ticker
        self.stop_price = stop_price

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if hasattr(self, "message") and self.message:
                await self.message.edit(view=self)
        except Exception:
            pass

    @ui.button(label="I Understand the Risk", emoji="✅",
               style=discord.ButtonStyle.success, row=0)
    async def btn_confirm(self, interaction: discord.Interaction, button: ui.Button):
        button.disabled = True
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
        embed = discord.Embed(
            description=(
                f"Good. Remember: never risk more than you are comfortable losing. "
                f"Set your stop loss at **${self.stop_price:.2f}** before entering. "
                f"If the trade goes against you, exit immediately."
            ),
            color=0x00FF88,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
