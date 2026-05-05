"""
Index registry: resolve index/country input, get constituent tickers.
Excludes small cap indices. Constituent data from data/indices/*.json.
"""

from dataclasses import dataclass
from pathlib import Path
import json
import re

# Valid ticker: alphanumeric and hyphen only (e.g. BRK-B), length 1-10
_TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,9}$")

# Index ID -> (display name, constituent file)
# Excludes Small Cap 2000 and other small cap indices
# Add more entries as constituent JSON files are added to data/indices/
INDEX_REGISTRY: dict[str, tuple[str, str]] = {
    "dow_jones": ("Dow Jones", "dow_jones.json"),
    "sp500": ("S&P 500", "sp500.json"),
    "nasdaq": ("Nasdaq", "nasdaq100.json"),
    "dax": ("DAX", "dax.json"),
    "ftse100": ("FTSE 100", "ftse100.json"),
    "cac40": ("CAC 40", "cac40.json"),
}

# Country/region -> list of index IDs (for multi-index countries, user chooses)
# Only includes indices with constituent data (data/indices/*.json)
COUNTRY_TO_INDICES: dict[str, list[str]] = {
    "usa": ["dow_jones", "sp500", "nasdaq"],
    "america": ["dow_jones", "sp500", "nasdaq"],
    "us": ["dow_jones", "sp500", "nasdaq"],
    "united states": ["dow_jones", "sp500", "nasdaq"],
    "germany": ["dax"],
    "uk": ["ftse100"],
    "united kingdom": ["ftse100"],
    "britain": ["ftse100"],
    "france": ["cac40"],
}

# Index name (normalized) -> index ID for direct lookup
def _build_name_to_id() -> dict[str, str]:
    out: dict[str, str] = {}
    for idx_id, (name, _) in INDEX_REGISTRY.items():
        key = re.sub(r"[^\w]", "", name.lower())
        out[key] = idx_id
        # Also add common aliases
        if idx_id == "nasdaq":
            out["nasdaq100"] = idx_id
        if idx_id == "sp500":
            out["sp500"] = idx_id
            out["snp500"] = idx_id
    return out

NAME_TO_ID = _build_name_to_id()

DATA_DIR = Path(__file__).parent.parent / "data" / "indices"


@dataclass
class IndexInfo:
    id: str
    name: str


def _is_valid_ticker(symbol: str) -> bool:
    """Validate ticker: alphanumeric, hyphen allowed (e.g. BRK-B), length 1-10."""
    return isinstance(symbol, str) and bool(_TICKER_RE.match(symbol.strip()))


def get_constituents(index_id: str) -> list[str] | None:
    """
    Return constituent tickers for the given index, or None if no data.
    Invalid tickers from JSON are filtered out.
    """
    if index_id not in INDEX_REGISTRY:
        return None
    _, filename = INDEX_REGISTRY[index_id]
    path = DATA_DIR / filename
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return [t for t in data if _is_valid_ticker(t)]
        return None
    except (json.JSONDecodeError, OSError):
        return None


def get_supported_summary() -> dict:
    """
    Return markets, indexes with counts, and total unique ticker count.
    Used by /supported command.
    """
    from src.stocks import get_sp100_tickers

    indexes: list[tuple[str, int]] = []
    all_tickers: set[str] = set(get_sp100_tickers())

    for idx_id, (name, _) in INDEX_REGISTRY.items():
        constituents = get_constituents(idx_id)
        count = len(constituents) if constituents else 0
        indexes.append((name, count))
        if constituents:
            all_tickers.update(constituents)

    # Deduplicate markets by unique index-id sets; use first country as display name
    seen: set[frozenset[str]] = set()
    markets: list[tuple[str, list[str]]] = []
    display_names: dict[str, str] = {
        "usa": "USA", "america": "USA", "us": "USA", "united states": "USA",
        "germany": "Germany", "uk": "United Kingdom", "united kingdom": "UK",
        "britain": "United Kingdom", "france": "France",
    }
    for country, ids in COUNTRY_TO_INDICES.items():
        key = frozenset(ids)
        if key not in seen:
            seen.add(key)
            index_names = [INDEX_REGISTRY[i][0] for i in ids]
            display = display_names.get(country, country.replace("_", " ").title())
            markets.append((display, index_names))

    return {
        "markets": markets,
        "indexes": indexes,
        "total_unique": len(all_tickers),
    }


def resolve_input(query: str) -> list[IndexInfo]:
    """
    Resolve user input (index name or country) to matching indices.
    Returns 1+ IndexInfo. Case-insensitive, supports partial match.
    """
    q = query.strip().lower()
    if not q:
        return []

    # 1. Exact country match
    if q in COUNTRY_TO_INDICES:
        ids = COUNTRY_TO_INDICES[q]
        return [IndexInfo(i, INDEX_REGISTRY[i][0]) for i in ids]

    # 2. Country partial match
    for country, ids in COUNTRY_TO_INDICES.items():
        if country.startswith(q) or q in country:
            return [IndexInfo(i, INDEX_REGISTRY[i][0]) for i in ids]

    # 3. Index name match - try normalized name
    q_clean = re.sub(r"[^\w]", "", q)
    for idx_id, (name, _) in INDEX_REGISTRY.items():
        name_clean = re.sub(r"[^\w]", "", name.lower())
        if q_clean in name_clean or name_clean in q_clean:
            return [IndexInfo(idx_id, name)]

    # 4. Index ID match
    if q in INDEX_REGISTRY:
        name = INDEX_REGISTRY[q][0]
        return [IndexInfo(q, name)]

    # 5. Partial index ID
    for idx_id, (name, _) in INDEX_REGISTRY.items():
        if q in idx_id or idx_id.startswith(q):
            return [IndexInfo(idx_id, name)]

    return []
