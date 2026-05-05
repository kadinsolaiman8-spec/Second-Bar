# watchlist.py — Stock universe, sector mapping, personal watchlist, mutes

import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz
from scanner_core.config import MARKET_TIMEZONE, SECTOR_MAP

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)

# ── Stock Universe ─────────────────────────────────────────────────────────

STABLE_STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "BAC", "V",
    "MA", "UNH", "JNJ", "XOM", "CVX", "WMT", "HD", "PG", "KO", "PEP",
    "ABBV", "MRK", "LLY", "TMO", "ABT", "NEE", "DUK", "SO", "CRM", "ADBE",
    "ORCL", "INTC", "AMD", "QCOM", "TXN", "HON", "MMM", "CAT", "DE", "GE",
    "BA", "RTX", "LMT", "GS", "MS", "BLK", "SPGI", "AXP", "COST", "TGT",
    "NFLX", "PYPL", "SQ", "SHOP", "SNAP", "UBER", "LYFT", "DASH", "ABNB",
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLV",
]

VOLATILE_STOCKS = [
    "COIN", "MARA", "RIOT", "PLTR", "SOFI", "HOOD", "RIVN", "LCID",
    "UPST", "AFRM", "DKNG", "PENN", "RBLX", "PINS", "TLRY", "MSOS",
    "ARKK", "SOXL", "TQQQ", "SQQQ", "UVXY", "VXX", "SPXL", "LABU",
    "NAIL", "FAS", "FNGU", "WEBL", "ASTS", "RKLB", "LUNR", "IONQ",
    "QUBT", "RGTI", "BBAI", "SOUN", "KULR", "SMCI", "MSTR", "CLSK",
    "BTBT", "HUT", "CIFR", "WULF", "BITF", "CORZ", "NVAX", "MRNA",
    "BNTX", "SRPT", "EDIT", "BEAM", "CRSP", "OPEN", "ACMR", "BTAI",
    # Cybersecurity / Cloud (replaced worst performers)
    "CRWD", "PANW", "SNOW", "DDOG", "NET", "ZS", "MDB", "GTLB",
    "PATH", "BILL", "HUBS", "DOCN", "ESTC",
    "DT", "S", "TENB", "QLYS",
]

ALL_STOCKS = STABLE_STOCKS + VOLATILE_STOCKS

# ── Company Names (for autocomplete) ─────────────────────────────────────

COMPANY_NAMES = {
    # ── Stable / Large Cap ────────────────────────────────────────────────
    "AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "GOOGL": "Alphabet Inc.",
    "AMZN": "Amazon.com Inc.", "NVDA": "NVIDIA Corporation", "META": "Meta Platforms Inc.",
    "TSLA": "Tesla Inc.", "JPM": "JPMorgan Chase & Co.", "BAC": "Bank of America Corp.",
    "V": "Visa Inc.", "MA": "Mastercard Inc.", "UNH": "UnitedHealth Group Inc.",
    "JNJ": "Johnson & Johnson", "XOM": "Exxon Mobil Corp.", "CVX": "Chevron Corp.",
    "WMT": "Walmart Inc.", "HD": "Home Depot Inc.", "PG": "Procter & Gamble Co.",
    "KO": "Coca-Cola Co.", "PEP": "PepsiCo Inc.", "ABBV": "AbbVie Inc.",
    "MRK": "Merck & Co. Inc.", "LLY": "Eli Lilly and Co.", "TMO": "Thermo Fisher Scientific",
    "ABT": "Abbott Laboratories", "NEE": "NextEra Energy Inc.", "DUK": "Duke Energy Corp.",
    "SO": "Southern Company", "CRM": "Salesforce Inc.", "ADBE": "Adobe Inc.",
    "ORCL": "Oracle Corporation", "INTC": "Intel Corporation", "AMD": "Advanced Micro Devices",
    "QCOM": "Qualcomm Inc.", "TXN": "Texas Instruments", "HON": "Honeywell International",
    "MMM": "3M Company", "CAT": "Caterpillar Inc.", "DE": "Deere & Company",
    "GE": "GE Aerospace", "BA": "Boeing Company", "RTX": "RTX Corporation",
    "LMT": "Lockheed Martin Corp.", "GS": "Goldman Sachs Group", "MS": "Morgan Stanley",
    "BLK": "BlackRock Inc.", "SPGI": "S&P Global Inc.", "AXP": "American Express Co.",
    "COST": "Costco Wholesale Corp.", "TGT": "Target Corporation",
    "NFLX": "Netflix Inc.", "PYPL": "PayPal Holdings Inc.", "SQ": "Block Inc.",
    "SHOP": "Shopify Inc.", "SNAP": "Snap Inc.", "UBER": "Uber Technologies Inc.",
    "LYFT": "Lyft Inc.", "DASH": "DoorDash Inc.", "ABNB": "Airbnb Inc.",
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ Trust", "IWM": "iShares Russell 2000 ETF",
    "DIA": "SPDR Dow Jones ETF", "XLF": "Financial Select Sector SPDR",
    "XLK": "Technology Select Sector SPDR", "XLV": "Health Care Select Sector SPDR",
    # ── Volatile / Small-Mid Cap ──────────────────────────────────────────
    "COIN": "Coinbase Global Inc.", "MARA": "Marathon Digital Holdings",
    "RIOT": "Riot Platforms Inc.", "PLTR": "Palantir Technologies Inc.",
    "SOFI": "SoFi Technologies Inc.", "HOOD": "Robinhood Markets Inc.",
    "RIVN": "Rivian Automotive Inc.", "LCID": "Lucid Group Inc.",
    "UPST": "Upstart Holdings Inc.", "AFRM": "Affirm Holdings Inc.",
    "DKNG": "DraftKings Inc.", "PENN": "PENN Entertainment Inc.",
    "RBLX": "Roblox Corporation", "PINS": "Pinterest Inc.",
    "TLRY": "Tilray Brands Inc.",
    "MSOS": "AdvisorShares Pure US Cannabis ETF",
    "ARKK": "ARK Innovation ETF", "SOXL": "Direxion Semiconductor Bull 3X",
    "TQQQ": "ProShares UltraPro QQQ", "SQQQ": "ProShares UltraPro Short QQQ",
    "UVXY": "ProShares Ultra VIX Short-Term", "VXX": "Barclays iPath VIX ETN",
    "SPXL": "Direxion Daily S&P 500 Bull 3X", "LABU": "Direxion Daily Biotech Bull 3X",
    "NAIL": "Direxion Daily Homebuilders Bull 3X",
    "FAS": "Direxion Daily Financial Bull 3X", "FNGU": "MicroSectors FANG+ Bull 3X",
    "WEBL": "Direxion Daily Dow Jones Internet Bull 3X",
    "ASTS": "AST SpaceMobile Inc.", "RKLB": "Rocket Lab USA Inc.",
    "LUNR": "Intuitive Machines Inc.", "IONQ": "IonQ Inc.",
    "QUBT": "Quantum Computing Inc.", "RGTI": "Rigetti Computing Inc.",
    "BBAI": "BigBear.ai Holdings", "SOUN": "SoundHound AI Inc.",
    "KULR": "KULR Technology Group", "SMCI": "Super Micro Computer Inc.",
    "MSTR": "Strategy (MicroStrategy)", "CLSK": "CleanSpark Inc.",
    "BTBT": "Bit Digital Inc.", "HUT": "Hut 8 Corp.",
    "CIFR": "Cipher Mining Inc.", "WULF": "TeraWulf Inc.",
    "BITF": "Bitfarms Ltd.", "CORZ": "Core Scientific Inc.",
    "NVAX": "Novavax Inc.", "MRNA": "Moderna Inc.",
    "BNTX": "BioNTech SE", "SRPT": "Sarepta Therapeutics",
    "EDIT": "Editas Medicine Inc.", "BEAM": "Beam Therapeutics",
    "CRSP": "CRISPR Therapeutics AG", "OPEN": "Opendoor Technologies",
    "ACMR": "ACM Research Inc.", "BTAI": "BioXcel Therapeutics",
    # Cybersecurity / Cloud
    "CRWD": "CrowdStrike Holdings", "PANW": "Palo Alto Networks",
    "SNOW": "Snowflake Inc.", "DDOG": "Datadog Inc.", "NET": "Cloudflare Inc.",
    "ZS": "Zscaler Inc.", "MDB": "MongoDB Inc.", "GTLB": "GitLab Inc.",
    "PATH": "UiPath Inc.", "BILL": "BILL Holdings Inc.",
    "HUBS": "HubSpot Inc.", "DOCN": "DigitalOcean Holdings", "ESTC": "Elastic N.V.",
    "DT": "Dynatrace Inc.",
    "S": "SentinelOne Inc.", "TENB": "Tenable Holdings", "QLYS": "Qualys Inc.",
    # ── Additional ETFs in XLE/etc ────────────────────────────────────────
    "XLE": "Energy Select Sector SPDR",
}

# Pre-built search list: [(ticker, name_lower, display)]
_AUTOCOMPLETE_CACHE = [
    (t, COMPANY_NAMES.get(t, t).lower(), f"{t} — {COMPANY_NAMES.get(t, t)}")
    for t in ALL_STOCKS
]


def autocomplete_tickers(query: str, limit: int = 25) -> list:
    """Return autocomplete choices matching *query* (ticker or company name).
    Always returns list of (ticker, display) 2-tuples."""
    q = query.strip().upper()
    ql = query.strip().lower()
    if not q:
        return [(t, d) for t, _, d in _AUTOCOMPLETE_CACHE[:limit]]
    matches = []
    for ticker, name_lower, display in _AUTOCOMPLETE_CACHE:
        if ticker.startswith(q) or ql in name_lower:
            matches.append((ticker, display))
            if len(matches) >= limit:
                break
    return matches


# ── In-memory state ────────────────────────────────────────────────────────

personal_watchlist: set = set()

# {ticker: {expiry: datetime, reason: str}}
muted_tickers: dict = {}


# ── Classification helpers ─────────────────────────────────────────────────

def is_stable(ticker: str) -> bool:
    return ticker.upper() in STABLE_STOCKS


def is_volatile(ticker: str) -> bool:
    return ticker.upper() in VOLATILE_STOCKS


def in_universe(ticker: str) -> bool:
    return ticker.upper() in ALL_STOCKS


def get_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker.upper(), "other")


def get_sector_stocks(sector: str) -> list:
    """Return all tickers in the given sector (from ALL_STOCKS only)."""
    sector = sector.lower()
    return [t for t in ALL_STOCKS if SECTOR_MAP.get(t, "") == sector]


def validate_ticker_format(ticker: str) -> bool:
    """Basic format check: 1-6 uppercase alpha characters."""
    if not ticker:
        return False
    t = ticker.upper().strip()
    return t.isalpha() and 1 <= len(t) <= 6


def get_stock_type_label(ticker: str) -> str:
    if is_stable(ticker):
        return "STABLE"
    if is_volatile(ticker):
        return "VOLATILE"
    return "EXTERNAL"


# ── Personal extra-attention watchlist ────────────────────────────────────

def add_personal(ticker: str) -> bool:
    """Add ticker to personal watchlist. Returns True if newly added."""
    t = ticker.upper().strip()
    if not validate_ticker_format(t):
        return False
    if t in personal_watchlist:
        return False
    personal_watchlist.add(t)
    logger.info(f"Personal watchlist: added {t}")
    return True


def remove_personal(ticker: str) -> bool:
    """Remove ticker from personal watchlist. Returns True if removed."""
    t = ticker.upper().strip()
    if t in personal_watchlist:
        personal_watchlist.discard(t)
        logger.info(f"Personal watchlist: removed {t}")
        return True
    return False


def get_personal() -> list:
    return sorted(personal_watchlist)


def is_in_personal(ticker: str) -> bool:
    return ticker.upper().strip() in personal_watchlist


# ── Mute system ────────────────────────────────────────────────────────────

def add_mute(ticker: str, minutes: int, reason: str = "Manual mute") -> datetime:
    """Mute a ticker for N minutes. Returns expiry datetime."""
    t = ticker.upper().strip()
    expiry = datetime.now(ET) + timedelta(minutes=minutes)
    muted_tickers[t] = {"expiry": expiry, "reason": reason}
    logger.info(f"Muted {t} for {minutes}m ({reason}). Expires {expiry.strftime('%H:%M ET')}")
    return expiry


def remove_mute(ticker: str) -> bool:
    """Remove mute. Returns True if was muted."""
    t = ticker.upper().strip()
    if t in muted_tickers:
        del muted_tickers[t]
        logger.info(f"Unmuted {t}")
        return True
    return False


def is_muted(ticker: str) -> bool:
    t = ticker.upper().strip()
    if t not in muted_tickers:
        return False
    if datetime.now(ET) >= muted_tickers[t]["expiry"]:
        del muted_tickers[t]
        return False
    return True


def get_muted() -> dict:
    """Return all active mutes with time remaining."""
    now = datetime.now(ET)
    active = {}
    expired = []
    for ticker, data in muted_tickers.items():
        if now < data["expiry"]:
            remaining = data["expiry"] - now
            mins = int(remaining.total_seconds() / 60)
            secs = int(remaining.total_seconds() % 60)
            active[ticker] = {
                "expiry":    data["expiry"],
                "reason":    data["reason"],
                "remaining": f"{mins}m {secs}s",
            }
        else:
            expired.append(ticker)
    for t in expired:
        del muted_tickers[t]
    return active


def cleanup_mutes():
    """Remove expired mutes."""
    now = datetime.now(ET)
    expired = [t for t, d in muted_tickers.items() if now >= d["expiry"]]
    for t in expired:
        del muted_tickers[t]
    if expired:
        logger.debug(f"Cleaned up expired mutes: {expired}")
