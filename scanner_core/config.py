# config.py — All constants, thresholds, strategy definitions, timing configuration

import os
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════════════
# BOT IDENTITY
# ══════════════════════════════════════════════════════════════════════════

BOT_NAME    = "TradingBot"
VERSION     = "3.0.0"

# ══════════════════════════════════════════════════════════════════════════
# DISCORD
# ══════════════════════════════════════════════════════════════════════════

DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN", "")
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))
FINNHUB_API_KEY  = os.getenv("FINNHUB_API_KEY", "")

DISCORD_MESSAGE_DELAY_SECONDS = 1.0
MAX_MESSAGE_LENGTH            = 1990

# ══════════════════════════════════════════════════════════════════════════
# MARKET TIMING
# ══════════════════════════════════════════════════════════════════════════

MARKET_TIMEZONE        = "US/Eastern"
MARKET_OPEN_HOUR       = 9
MARKET_OPEN_MINUTE     = 30
MARKET_CLOSE_HOUR      = 16
MARKET_CLOSE_MINUTE    = 0
PREMARKET_START_HOUR   = 4
PREMARKET_START_MINUTE = 0

ORB_END_HOUR           = 9
ORB_END_MINUTE         = 45

# ══════════════════════════════════════════════════════════════════════════
# SCAN TIMING
# ══════════════════════════════════════════════════════════════════════════

SCAN_INTERVAL_SECONDS             = 60
REGIME_CHECK_INTERVAL_SECONDS     = 300
NEWS_REFRESH_INTERVAL_SECONDS     = 600
DIGEST_INTERVAL_MINUTES           = 30
HOURLY_SNAPSHOT_INTERVAL_MINUTES  = 60

# ══════════════════════════════════════════════════════════════════════════
# INDICATOR PARAMETERS
# ══════════════════════════════════════════════════════════════════════════

RSI_PERIOD        = 14
RSI_OVERBOUGHT    = 70
RSI_OVERSOLD      = 30

MACD_FAST         = 12
MACD_SLOW         = 26
MACD_SIGNAL       = 9

SUPERTREND_PERIOD = 10
SUPERTREND_MULT   = 3.0

STOCH_K_PERIOD    = 14
STOCH_D_PERIOD    = 3
STOCH_SMOOTH      = 3
STOCH_OVERBOUGHT  = 80
STOCH_OVERSOLD    = 20

EMA_FAST          = 9
EMA_MID           = 21
EMA_SLOW          = 50
EMA_TREND         = 200

VWAP_RESET_HOUR   = 9
VWAP_RESET_MINUTE = 30

ADX_PERIOD        = 14
ADX_TRENDING      = 25
ADX_STRONG        = 30
ADX_WEAK          = 20

ATR_PERIOD        = 14

BB_PERIOD         = 20
BB_STD            = 2.0

RVOL_PERIOD       = 20
RVOL_HIGH         = 1.5
RVOL_VERY_HIGH    = 2.0

WILLIAMS_PERIOD   = 14
WILLIAMS_OB       = -20
WILLIAMS_OS       = -80

# ══════════════════════════════════════════════════════════════════════════
# ATR-BASED STOP / TARGET MULTIPLIERS
# ══════════════════════════════════════════════════════════════════════════

ATR_STOP_LOSS_MULTIPLIER      = 1.0
ATR_TAKE_PROFIT_1_MULTIPLIER  = 2.0      # 2.0:1 R:R  Conservative
ATR_TAKE_PROFIT_2_MULTIPLIER  = 3.5      # 3.5:1 R:R  Standard
ATR_TAKE_PROFIT_3_MULTIPLIER  = 5.0      # 5.0:1 R:R  Stretch
ATR_TP3_MULTIPLIER            = ATR_TAKE_PROFIT_3_MULTIPLIER

# ══════════════════════════════════════════════════════════════════════════
# CONFIDENCE SCORING
# ══════════════════════════════════════════════════════════════════════════

CONFIDENCE_IGNORE          = 50
CONFIDENCE_WATCHLIST       = 50
CONFIDENCE_CAUTION         = 70
CONFIDENCE_STRONG          = 85
CONFIDENCE_HIGH_CONVICTION = 95

FLAG_CAUTION               = "⚠️"
FLAG_STRONG                = "✅"
FLAG_HIGH_CONVICTION       = "🔥"
FLAG_BELOW                 = "⚪"

SCORE_NEWS_BULLISH_BONUS   = 5
SCORE_NEWS_SUPPRESS_WINDOW = 30
SCORE_NEWS_BONUS_WINDOW    = 120

RSI_HEALTHY_MIN            = 45
RSI_HEALTHY_MAX            = 65
RS_MIN_OUTPERFORMANCE      = 0.003   # 0.3% above SPY
MIN_INDICATORS_FULL_ALERT  = 9

# ══════════════════════════════════════════════════════════════════════════
# STRATEGY NAMES
# ══════════════════════════════════════════════════════════════════════════

STRATEGY_VWAP_BREAKOUT     = "VWAP Momentum Breakout"
STRATEGY_ORB               = "Opening Range Breakout"
STRATEGY_EMA_PULLBACK      = "EMA Pullback in Trend"
STRATEGY_FIBONACCI         = "Fibonacci Confluence Reversal"
STRATEGY_BB_SQUEEZE        = "Bollinger Band Squeeze Breakout"

ALL_STRATEGIES = [
    STRATEGY_VWAP_BREAKOUT,
    STRATEGY_ORB,
    STRATEGY_EMA_PULLBACK,
    STRATEGY_FIBONACCI,
    STRATEGY_BB_SQUEEZE,
]

# ══════════════════════════════════════════════════════════════════════════
# ANTI-LATE-ENTRY FILTERS
# ══════════════════════════════════════════════════════════════════════════

MAX_SIGNAL_AGE_CANDLES        = 2
MAX_PRICE_EXTENSION_ATR       = 1.5
RSI_EXHAUSTION_CANDLES        = 5
MAX_CONSECUTIVE_BULLISH_1M    = 12
VWAP_TRAP_COOLDOWN_MINUTES    = 15
MIN_DAILY_VOLUME              = 500_000
BB_OVEREXTENSION_CANDLES      = 3
MAX_IMPLIED_SPREAD_PCT        = 2.0

# ══════════════════════════════════════════════════════════════════════════
# COOLDOWN / MUTE
# ══════════════════════════════════════════════════════════════════════════

COOLDOWN_MINUTES                  = 15
COOLDOWN_REVERSAL_ATR_MULTIPLIER  = 1.5
VWAP_TRAP_SUPPRESS_MINUTES        = 15
REMINDER_MAX_MINUTES              = 120
PAPER_TRADE_MAX_HOLD_MINUTES      = 180

# ══════════════════════════════════════════════════════════════════════════
# REGIME
# ══════════════════════════════════════════════════════════════════════════

REGIME_TRENDING         = "TRENDING"
REGIME_RANGING          = "RANGING"
REGIME_HIGH_VOLATILITY  = "HIGH VOLATILITY"

REGIME_ADX_TRENDING            = 25
REGIME_ADX_RANGING             = 20
REGIME_ATR_EXPANSION           = 1.5
TEST_REGIME_OVERRIDE_MINUTES   = 30
REGIME_RANGING_MIN_CONFIDENCE  = 80
REGIME_HIGH_VOL_MIN_CONFIDENCE = 90

# ══════════════════════════════════════════════════════════════════════════
# BACKTEST
# ══════════════════════════════════════════════════════════════════════════

BACKTEST_MAX_DAYS       = 730
BACKTEST_MAX_HOLD_BARS  = 12
BACKTEST_MIN_SCORE      = 70

# ══════════════════════════════════════════════════════════════════════════
# GOODDAY
# ══════════════════════════════════════════════════════════════════════════

GOODDAY_LOOKBACK_DAYS = 180
GOODDAY_CANDIDATES = [
    "AAPL", "NVDA", "TSLA", "AMD", "COIN", "MARA", "PLTR", "META",
    "MSFT", "AMZN", "GOOGL", "NFLX", "SHOP", "SOFI", "HOOD", "RIVN",
    "MSTR", "ARKK", "SOXL", "TQQQ",
]

# ══════════════════════════════════════════════════════════════════════════
# FIBONACCI
# ══════════════════════════════════════════════════════════════════════════

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]

# ══════════════════════════════════════════════════════════════════════════
# SECTOR MAP
# ══════════════════════════════════════════════════════════════════════════

SECTOR_MAP = {
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "tech",
    "NVDA": "tech", "META": "tech", "TSLA": "tech", "CRM": "tech",
    "ADBE": "tech", "ORCL": "tech", "INTC": "tech", "AMD": "tech",
    "QCOM": "tech", "TXN": "tech", "SHOP": "tech", "SNAP": "tech",
    "PLTR": "tech", "RBLX": "tech", "PINS": "tech",
    "IONQ": "tech", "QUBT": "tech", "RGTI": "tech", "BBAI": "tech",
    "SOUN": "tech", "KULR": "tech", "SMCI": "tech", "ACMR": "tech",
    "CRWD": "tech", "PANW": "tech", "SNOW": "tech", "DDOG": "tech",
    "NET": "tech", "ZS": "tech", "MDB": "tech", "GTLB": "tech",
    "PATH": "tech", "BILL": "tech", "HUBS": "tech",
    "DOCN": "tech", "ESTC": "tech",
    "DT": "tech", "S": "tech", "TENB": "tech", "QLYS": "tech",
    "JPM": "finance", "BAC": "finance", "V": "finance", "MA": "finance",
    "GS": "finance", "MS": "finance", "BLK": "finance", "SPGI": "finance",
    "AXP": "finance", "PYPL": "finance", "SQ": "finance",
    "COIN": "finance", "SOFI": "finance", "HOOD": "finance",
    "UPST": "finance", "AFRM": "finance",
    "XOM": "energy", "CVX": "energy", "NEE": "energy", "DUK": "energy",
    "SO": "energy",
    "UNH": "health", "JNJ": "health", "ABBV": "health", "MRK": "health",
    "LLY": "health", "TMO": "health", "ABT": "health",
    "NVAX": "health", "MRNA": "health", "BNTX": "health",
    "SRPT": "health", "EDIT": "health", "BEAM": "health", "CRSP": "health",
    "WMT": "consumer", "HD": "consumer", "PG": "consumer", "KO": "consumer",
    "PEP": "consumer", "COST": "consumer", "TGT": "consumer",
    "NFLX": "consumer", "UBER": "consumer", "LYFT": "consumer",
    "DASH": "consumer", "ABNB": "consumer",
    "HON": "consumer", "MMM": "consumer", "CAT": "consumer", "DE": "consumer",
    "GE": "consumer", "BA": "consumer", "RTX": "consumer", "LMT": "consumer",
    "DKNG": "consumer", "PENN": "consumer",
    "RIVN": "volatile", "LCID": "volatile", "OPEN": "volatile",
    "BTAI": "volatile", "ASTS": "volatile", "RKLB": "volatile",
    "LUNR": "volatile", "TLRY": "volatile", "MSOS": "volatile",
    "MARA": "crypto", "RIOT": "crypto", "MSTR": "crypto",
    "CLSK": "crypto", "BTBT": "crypto", "HUT": "crypto",
    "CIFR": "crypto", "WULF": "crypto", "BITF": "crypto", "CORZ": "crypto",
    "SPY": "etf", "QQQ": "etf", "IWM": "etf", "DIA": "etf",
    "XLF": "etf", "XLK": "etf", "XLE": "etf", "XLV": "etf",
    "ARKK": "etf", "SOXL": "etf", "TQQQ": "etf", "SQQQ": "etf",
    "UVXY": "etf", "VXX": "etf", "SPXL": "etf", "LABU": "etf",
    "NAIL": "etf", "FAS": "etf", "FNGU": "etf", "WEBL": "etf",
}

# ══════════════════════════════════════════════════════════════════════════
# MISC
# ══════════════════════════════════════════════════════════════════════════

TIMEFRAMES       = ["1m", "5m", "15m", "1h"]
SCAN_HISTORY_MAX = 500
MIN_RR_RATIO     = 1.8
VIX_TICKER       = "^VIX"
VIX_THRESHOLD_HIGH    = 25
VIX_THRESHOLD_EXTREME = 35

# ══════════════════════════════════════════════════════════════════════════
# SCANNER BATCH SETTINGS
# ══════════════════════════════════════════════════════════════════════════

BATCH_SIZE          = 10
BATCH_SLEEP_SECONDS = 0.5
SPY_TICKER          = "SPY"

# ══════════════════════════════════════════════════════════════════════════
# INDICATOR COUNTS
# ══════════════════════════════════════════════════════════════════════════

TOTAL_INDICATORS               = 12
MIN_INDICATORS_WATCHLIST       = 7
REGIME_RANGING_MIN_INDICATORS  = 5

# ══════════════════════════════════════════════════════════════════════════
# SCHEDULED JOBS
# ══════════════════════════════════════════════════════════════════════════

PREMARKET_BRIEFING_HOUR   = 9
PREMARKET_BRIEFING_MINUTE = 15
EOD_RECAP_HOUR            = 16
EOD_RECAP_MINUTE          = 15
BEST_BUYS_MIN_CONFIDENCE  = 85

# ══════════════════════════════════════════════════════════════════════════
# UX / INTERACTIVE
# ══════════════════════════════════════════════════════════════════════════

QUIET_MODE_DURATION_MINUTES  = 60
MAX_PRICE_ALERTS_PER_USER    = 5
BUTTON_EXPIRY_SECONDS        = 600       # 10 minutes
TIP_ROTATION_COUNT           = 30

# ── Embed colours (hex int) ──────────────────────────────────────────────
# ── Design System Colors ──────────────────────────────────────────────────
EMBED_COLOR_BUY              = 0x00D26A   # emerald green
EMBED_COLOR_SELL             = 0xED4245   # red (unused, BUY only)
EMBED_COLOR_HIGH_CONVICTION  = 0xFF8C00   # gold/orange
EMBED_COLOR_CAUTION          = 0xFEE75C   # yellow
EMBED_COLOR_INFO             = 0x5865F2   # brand blurple (legacy embed palette)
EMBED_COLOR_AFTER_HOURS      = 0x4F545C   # dark grey
EMBED_COLOR_TEST             = 0x5865F2   # blurple
EMBED_COLOR_GOOD_NEWS        = 0x57F287   # bright green
EMBED_COLOR_ERROR            = 0xED4245   # red
EMBED_COLOR_MARKET_CLOSED    = 0x36393F   # very dark
EMBED_COLOR_STRONG_TREND     = 0x57F287   # bright green
EMBED_COLOR_RANGING          = 0xFEE75C   # yellow
EMBED_COLOR_HIGH_VOL         = 0xED4245   # red

# ── Sector ETFs for sector confirmation ───────────────────────────────────
SECTOR_ETFS = {
    "tech": "XLK", "finance": "XLF", "energy": "XLE",
    "health": "XLV", "consumer": "XLY", "industrial": "XLI",
    "etf": "SPY", "crypto": "SPY", "cannabis": "SPY",
    "biotech": "XLV", "space": "XLK", "quantum": "XLK",
    "mining": "SPY", "ev": "XLY", "gaming": "XLY",
}

# ── Midday quiet hours ────────────────────────────────────────────────────
MIDDAY_QUIET_START_HOUR    = 11
MIDDAY_QUIET_START_MINUTE  = 30
MIDDAY_QUIET_END_HOUR      = 15
MIDDAY_QUIET_END_MINUTE    = 0

# ── Time decay multipliers ───────────────────────────────────────────────
TIME_DECAY_BEFORE_11       = 1.0
TIME_DECAY_MIDDAY          = 0.85
TIME_DECAY_2PM_TO_3PM      = 0.90
TIME_DECAY_POWER_HOUR      = 0.95

# ── Alert dedup and accuracy ─────────────────────────────────────────────
ALERT_DEDUP_MINUTES        = 60     # min minutes between alerts for same ticker
MIN_SCORE_INCREASE_TO_ALERT = 5     # score must rise by 5+ to re-alert
SECTOR_VWAP_PENALTY        = 8      # score reduction if sector ETF below VWAP
SECTOR_VWAP_SUPPRESS_PCT   = -1.0   # suppress alert if sector ETF this far below VWAP

# ══════════════════════════════════════════════════════════════════════════
# WIN-RATE FILTERS
# ══════════════════════════════════════════════════════════════════════════

# Circuit breaker — pause after consecutive losses
CIRCUIT_BREAKER_CONSECUTIVE_LOSSES = 4
CIRCUIT_BREAKER_PAUSE_MINUTES      = 60

# Regime quality score (0-100)
REGIME_QUALITY_MIN_SCORE           = 60
REGIME_QUALITY_SPY_ADX_THRESHOLD   = 25
REGIME_QUALITY_SPY_RSI_MIN         = 45
REGIME_QUALITY_SPY_RSI_MAX         = 65
REGIME_QUALITY_VIX_MAX             = 20

# Hourly bullish confirmation
HOURLY_BULLISH_MIN_CONDITIONS      = 2

# Trigger candle volume filter
TRIGGER_VOLUME_MULTIPLIER          = 1.3
TRIGGER_VOLUME_LOOKBACK            = 20

# Stock ADX on 15m
MIN_ADX_15M                        = 15

# Minimum price and volume filters
MIN_STOCK_PRICE                    = 10.0     # Below $10 = unreliable signals
MIN_AVG_DAILY_VOLUME               = 500_000   # 500K shares/day min

# BB Squeeze minimum candles before breakout
BB_SQUEEZE_MIN_CANDLES             = 10

# Signal quality score (0-10)
SIGNAL_QUALITY_MIN                 = 5

# Backtest timeout
BACKTEST_TIMEOUT_SECONDS           = 120

# Trend consistency candles
TREND_CONSISTENCY_CANDLES          = 3

# RSI momentum minimum delta
RSI_MOMENTUM_MIN_DELTA             = 1.0

# ══════════════════════════════════════════════════════════════════════════
# MARKET STATE (market_state.py)
# ══════════════════════════════════════════════════════════════════════════

MS_ADX_STRONG_TREND                = 28
MS_ADX_WEAK_TREND                  = 20
MS_ADX_RANGING                     = 20
MS_RSI_STRONG_MIN                  = 52
MS_RSI_STRONG_MAX                  = 75
MS_RSI_RANGING_MIN                 = 40
MS_RSI_RANGING_MAX                 = 60
MS_ATR_HIGH_VOL_RATIO              = 1.6
MS_SPY_MOVE_HIGH_VOL               = 1.5     # % move in 30m = high vol
MS_VIX_HIGH_VOL                    = 25
MS_UPDATE_INTERVAL_SECONDS         = 300
MS_STRONG_TREND_MAX_ALERTS_PER_HOUR  = 5
MS_WEAK_TREND_MAX_ALERTS_PER_HOUR    = 3
MS_RANGING_MAX_ALERTS_PER_HOUR       = 3
MS_STRONG_TREND_MIN_CONFIDENCE     = 65
MS_WEAK_TREND_MIN_CONFIDENCE       = 75
MS_RANGING_MIN_CONFIDENCE          = 70

# ══════════════════════════════════════════════════════════════════════════
# MEAN REVERSION (mean_reversion.py)
# ══════════════════════════════════════════════════════════════════════════

MR_RSI_OVERSOLD                    = 32
MR_RVOL_MIN                        = 1.2
MR_DOWNTREND_MAX_CANDLES           = 20
MR_VWAP_ATR_DISTANCE               = 2.0
MR_VWAP_RSI_MAX                    = 38
MR_VWAP_RVOL_MIN                   = 1.5
MR_EMA_RSI_MIN                     = 35
MR_EMA_RSI_MAX                     = 50
MR_EMA_LOOKBACK_CANDLES            = 24
MR_WILLIAMS_OS                     = -80

# ══════════════════════════════════════════════════════════════════════════
# ALIASES
# ══════════════════════════════════════════════════════════════════════════

STRATEGY_VWAP     = STRATEGY_VWAP_BREAKOUT
STRATEGY_PULLBACK = STRATEGY_EMA_PULLBACK

NEWS_INTERVAL_SECONDS      = NEWS_REFRESH_INTERVAL_SECONDS
REGIME_INTERVAL_SECONDS    = REGIME_CHECK_INTERVAL_SECONDS
BEST_BUYS_INTERVAL_MINUTES = DIGEST_INTERVAL_MINUTES
