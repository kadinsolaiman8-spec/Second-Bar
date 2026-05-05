# tips.py — 30 trading tips that rotate daily, tip selector function

from datetime import datetime
import pytz
from config import MARKET_TIMEZONE

ET = pytz.timezone(MARKET_TIMEZONE)

TIPS = [
    # Tip 1
    "Never enter a trade without a stop loss already set. The stop loss is not "
    "optional — it is the one thing that protects your account from a single bad "
    "trade wiping out weeks of gains.",

    # Tip 2
    "VWAP is the institutional benchmark. When price is above VWAP, big money is "
    "generally bullish. When it is below, they are defensive. Always note which "
    "side of VWAP price is sitting on before entering.",

    # Tip 3
    "Volume confirms everything. A breakout on low volume is a trap. A breakout "
    "on 2x or more average volume is a real signal with institutional backing.",

    # Tip 4
    "The first 15 minutes after market open are the most volatile of the day. "
    "Prices are reacting to overnight news and pre-market orders. The Opening "
    "Range Breakout strategy waits for this chaos to settle before acting.",

    # Tip 5
    "Risk only 1 to 2 percent of your account per trade. If you have a $10,000 "
    "account, that means a maximum of $100 to $200 at risk per trade. This keeps "
    "a losing streak from being catastrophic.",

    # Tip 6
    "A 1.5:1 risk-reward ratio means your winners need to be 50% larger than "
    "your losers to break even at a 40% win rate. Always calculate this before "
    "entering.",

    # Tip 7
    "The market regime matters more than any individual stock signal. A great "
    "setup in a choppy ranging market has a much lower probability of working "
    "than the same setup in a trending market. Always check /regime first.",

    # Tip 8
    "Do not chase breakouts that have already moved more than 1.5x the ATR from "
    "the trigger candle. The easy money is gone and you are now buying someone "
    "else's profit.",

    # Tip 9
    "RSI above 70 does not automatically mean sell. In a strong trend RSI can "
    "stay above 70 for many candles. It means be cautious about new entries, "
    "not that the trend is over.",

    # Tip 10
    "The confidence score is a probability estimate, not a guarantee. An 85/100 "
    "score means conditions are very favorable, not that the trade will "
    "definitely work. Always use your stop loss regardless of the score.",

    # Tip 11
    "Bollinger Band squeezes often precede explosive moves. When the bands "
    "narrow for 8 or more candles the market is building energy. The direction "
    "of the breakout is what matters, not the squeeze itself.",

    # Tip 12
    "EMA9 above EMA21 above EMA50 is the cleanest trend stack in day trading. "
    "When all three are aligned in order it means buyers are in control on short, "
    "medium, and longer timeframes simultaneously.",

    # Tip 13
    "Paper trade every strategy for at least two weeks before using real money. "
    "The /backtest and /goodday commands exist specifically for this purpose. "
    "Know how a strategy performs before risking capital.",

    # Tip 14
    "The best trades often look obvious in hindsight. When scanning with /best "
    "and all five timeframe checks show bullish, and the confidence is above 85, "
    "that is the bot telling you conditions are as good as they get.",

    # Tip 15
    "Cut losses quickly and let winners run. This sounds simple but is "
    "psychologically very hard. Setting your stop loss the moment you enter "
    "forces this discipline automatically.",

    # Tip 16
    "High volume at the open followed by declining volume during a pullback is "
    "one of the clearest signs that the trend is healthy and likely to continue. "
    "This is what the EMA Pullback strategy looks for.",

    # Tip 17
    "News moves stocks faster than any indicator. A strong earnings beat can "
    "make every technical signal irrelevant. Always check /news before entering "
    "any trade to know what catalyst if any is driving the move.",

    # Tip 18
    "The opening range (first 15 minutes) high and low act as support and "
    "resistance for the rest of the day. Even if you do not trade the ORB "
    "strategy, knowing these levels helps with every other trade you take.",

    # Tip 19
    "Relative volume is one of the most underrated indicators. A stock trading "
    "at 2x its average volume is telling you that something unusual is happening "
    "and institutional money is likely involved.",

    # Tip 20
    "ADX above 25 means a real trend exists. ADX below 20 means the market is "
    "choppy and trend-following strategies will get chopped up. This is why the "
    "regime detection adjusts which strategies are active.",

    # Tip 21
    "The Fibonacci 61.8% level is called the golden ratio for a reason. In a "
    "healthy uptrend a pullback to this level followed by a reversal signal is "
    "one of the highest probability setups in technical analysis.",

    # Tip 22
    "Never add to a losing position. If a trade is going against you, do not "
    "buy more hoping it will recover. Exit at your stop loss and reassess.",

    # Tip 23
    "The best day traders are selective. They wait for A-plus setups with "
    "multiple confirmations and pass on everything else. More trades does not "
    "mean more profit. Better trades do.",

    # Tip 24
    "RVOL above 1.5 combined with price above VWAP and a bullish MACD crossover "
    "is the core of the VWAP Momentum strategy. When all three line up at the "
    "same time it is a high-quality signal.",

    # Tip 25
    "Market open and market close are the two highest volume and highest "
    "volatility periods of the day. The middle of the day (11:30 AM to 2:00 PM "
    "ET) is often slow and choppy. Many day traders only trade the first two "
    "and last two hours.",

    # Tip 26
    "A bearish divergence happens when price makes a new high but RSI or "
    "Williams %R makes a lower high. This is the market telling you momentum "
    "is weakening even as price rises. The bot checks for this automatically.",

    # Tip 27
    "SuperTrend flipping direction is one of the clearest trend change signals. "
    "When it flips from bearish to bullish on both the 5-minute and 15-minute "
    "charts simultaneously, it is a powerful confirmation.",

    # Tip 28
    "Always know your maximum loss for the day before you start trading. Many "
    "professional traders stop trading after losing 2 to 3 percent of their "
    "account in a single day regardless of how many trades they have left.",

    # Tip 29
    "The bot does not generate trade alerts when the market is closed. Review "
    "your watchlist after the open, when spreads, volume, and price action are "
    "tradeable again.",

    # Tip 30
    "Consistency beats occasional big wins. A trader with a 55% win rate and a "
    "1.5:1 reward-risk ratio will outperform a trader who wins 80% but risks "
    "5:1 on every trade. The math always wins in the long run.",
]


def get_tip_of_the_day() -> str:
    """Return today's tip, rotating by day-of-year."""
    day = datetime.now(ET).timetuple().tm_yday
    index = (day - 1) % len(TIPS)
    return TIPS[index]


def get_tip_number() -> int:
    """Return today's tip number (1-based)."""
    day = datetime.now(ET).timetuple().tm_yday
    return ((day - 1) % len(TIPS)) + 1
