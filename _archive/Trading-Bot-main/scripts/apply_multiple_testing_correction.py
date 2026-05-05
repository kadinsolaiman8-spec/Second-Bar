#!/usr/bin/env python3
"""Apply Benjamini--Hochberg and Bonferroni correction to a CSV of p-values.

Expected CSV columns: ticker, test_name, p_value (header row required).

One row per **official** hypothesis per ticker; do not duplicate the same ticker
for exploratory modes. See ``docs/wfo_batches/README.md``.

Usage (from repository root)::

    python scripts/apply_multiple_testing_correction.py pvalues.csv --q 0.05

On Windows PowerShell::

    $env:PYTHONPATH = '.'
    python scripts/apply_multiple_testing_correction.py pvalues.csv --q 0.05
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.stats_utils import (  # noqa: E402
    benjamini_hochberg_adjusted_pvalues,
    bonferroni_per_test_alpha,
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Load CSV rows as dicts with string values."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            msg = "CSV has no header row"
            raise ValueError(msg)
        required = {"ticker", "test_name", "p_value"}
        header_lower = {h.strip().lower() for h in reader.fieldnames}
        if not required <= header_lower:
            msg = f"CSV must include columns {sorted(required)}; got {list(reader.fieldnames)}"
            raise ValueError(msg)
        rows = []
        for raw in reader:
            row = {k.lower().strip(): (raw[k] or "").strip() for k in raw}
            rows.append(row)
        return rows


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args, print BH-adjusted and Bonferroni diagnostics."""
    parser = argparse.ArgumentParser(
        description="Benjamini-Hochberg + Bonferroni for WFO batch p-value CSV.",
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="CSV with columns ticker, test_name, p_value",
    )
    parser.add_argument(
        "--q",
        type=float,
        default=0.05,
        help="Target FDR for BH rejections (default: 0.05)",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.05,
        help="Family-wise alpha for Bonferroni (default: 0.05)",
    )
    args = parser.parse_args(argv)

    rows = _read_rows(args.csv_path)
    if not rows:
        print("No data rows.", file=sys.stderr)
        return 1

    p_values: list[float] = []
    for row in rows:
        try:
            p_values.append(float(row["p_value"]))
        except (KeyError, ValueError) as exc:
            print(f"Invalid row {row!r}: {exc}", file=sys.stderr)
            return 1

    adj = benjamini_hochberg_adjusted_pvalues(p_values)
    bonf = bonferroni_per_test_alpha(args.alpha, len(p_values))

    print("ticker\ttest_name\tp_value\tBH_adj\treject_BH(q)\treject_Bonf(alpha/m)")
    m = len(rows)
    for i, row in enumerate(rows):
        ticker = row.get("ticker", "")
        tname = row.get("test_name", "")
        p = p_values[i]
        a = adj[i]
        rej_bh = "yes" if a <= args.q else "no"
        rej_b = "yes" if p <= bonf else "no"
        print(f"{ticker}\t{tname}\t{p:.6g}\t{a:.6g}\t{rej_bh}\t{rej_b}")

    print(f"\nBonferroni per-test alpha: {bonf:.6g} (m={m}, FWER={args.alpha})")
    print(f"BH FDR q: {args.q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
