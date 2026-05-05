"""
Portfolio-level pooled validation for trend-following and mean-reversion strategies.

Runs backtests on multiple tickers, aligns daily returns by date, computes
equal-weight portfolio returns, and reports per-asset + portfolio Sharpe/PSR/bootstrap.

Usage:
    python scripts/pool_validation.py --period 10y --strategy tf
    python scripts/pool_validation.py --period 10y --strategy mr
    python scripts/pool_validation.py --period 10y --strategy tf --tickers GLD XLE SPY
    python scripts/pool_validation.py --period 10y --strategy tf --expanded
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scipy import stats as sp_stats

from src.backtest import run_backtest
from src.config_resolver import get_config_for_ticker
from src.data import fetch_single, fetch_vix_series
from src.walk_forward import (
    compute_probabilistic_sharpe,
    run_stationary_bootstrap_oos_bar_returns,
)

DEFAULT_TF_TICKERS = ["GLD", "XLE", "SPY", "QQQ", "IWM"]
DEFAULT_MR_TICKERS = ["SPY", "QQQ", "IWM"]

# Expanded TF universe (23 tickers) for pooled portfolio-level validation.
# Designed for diverse asset classes to maximize effective independent observations.
# Excludes: COPX (equity-like miners), UNG (contango decay), HYG/LQD (credit mean-reverts).
EXPANDED_TF_TICKERS = [
    # Original 5
    "GLD", "XLE", "SPY", "QQQ", "IWM",
    # Commodities (4)
    "SLV", "DBA", "DBC", "USO",
    # Bonds/Rates (2)
    "TLT", "IEF",
    # Currencies (2)
    "UUP", "FXE",
    # International equity (5)
    "EFA", "EEM", "FXI", "EWJ", "EWZ",
    # Sector/Thematic (5)
    "XLF", "XLK", "XLV", "XLU", "VNQ",
]


def _bars_per_year(timeframe: str) -> int:
    return {"1H": 252 * 7, "Daily": 252, "1W": 52}.get(timeframe, 252)


def _run_single_ticker(
    ticker: str,
    strategy: str,
    period: str,
    timeframe: str,
    base_config: dict,
    vix_series,
    bars_yr: int,
    df: pd.DataFrame | None = None,
    gold_macro_filter=None,
) -> tuple[str, pd.Series | None, str]:
    """Run backtest for a single ticker. Returns (ticker, returns_series, summary_line)."""
    cfg = get_config_for_ticker(ticker, base_config, timeframe=timeframe)
    if strategy == "tf":
        cfg["strategy"] = "tf"
    elif strategy == "mr":
        cfg["strategy"] = "mr"

    if df is None:
        df = fetch_single(ticker, period=period)
    if df is None or df.empty:
        return ticker, None, f"  {ticker}: SKIP — no data"

    result = run_backtest(
        ticker, period=period, config=cfg, df=df, vix_series=vix_series,
        gold_macro_filter_series=gold_macro_filter,
    )
    if result is None or not result.bar_returns:
        return ticker, None, f"  {ticker}: SKIP — backtest returned no bar returns"

    n_bars = len(result.bar_returns)
    dates = df.index[-n_bars:]
    returns_series = pd.Series(result.bar_returns, index=dates, name=ticker)

    psr = compute_probabilistic_sharpe(result.bar_returns, bars_per_year=bars_yr)
    summary = (
        f"  {ticker}: {result.num_trades} trades | "
        f"return {result.total_return:+.1f}% | PF {result.profit_factor:.2f} | "
        f"Sharpe {psr['observed_sharpe_annual']:.3f} | "
        f"PSR P(SR>0) {psr['psr']:.1%} | "
        f"N={psr['n_obs']} bars"
    )
    return ticker, returns_series, summary


def run_pool_validation(
    tickers: list[str],
    strategy: str,
    period: str,
    timeframe: str,
    jobs: int = 1,
) -> None:
    with open(Path(__file__).resolve().parent.parent / "config.yaml") as f:
        base_config = yaml.safe_load(f)

    # Load ticker profiles
    tp_path = Path(__file__).resolve().parent.parent / "data" / "ticker_profiles.yaml"
    if tp_path.exists():
        with open(tp_path) as f:
            tp = yaml.safe_load(f) or {}
        for k, v in tp.get("ticker_profiles", {}).items():
            base_config.setdefault("ticker_profiles", {}).setdefault(k, {}).update(v)

    bars_yr = _bars_per_year(timeframe)
    vix_series = fetch_vix_series(period=period)

    # --- Pre-fetch all data sequentially (yfinance is not thread-safe) ---
    print(f"Strategy: {strategy.upper()}  |  Period: {period}  |  Tickers: {', '.join(tickers)}")
    if jobs > 1:
        print(f"Parallel: {jobs} workers (data fetched sequentially)")
    print("Fetching data...", end="", flush=True)
    ticker_data: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = fetch_single(ticker, period=period)
        if df is not None and not df.empty:
            ticker_data[ticker] = df
        print(".", end="", flush=True)

    # Pre-fetch gold macro filter (DXY/TIP) if GLD is in the list and strategy is TF
    gold_macro_filter = None
    if strategy == "tf" and "GLD" in tickers:
        from src.backtest import _get_gold_macro_filter
        if "GLD" in ticker_data:
            gold_macro_filter = _get_gold_macro_filter(
                ticker_data["GLD"], period, "1d"
            )
    print(f" done ({len(ticker_data)}/{len(tickers)} tickers)")
    print("=" * 70)

    # --- Run backtests and collect daily return series ---
    per_asset: dict[str, pd.Series] = {}

    if jobs <= 1:
        # Sequential
        for ticker in tickers:
            if ticker not in ticker_data:
                print(f"  {ticker}: SKIP — no data")
                continue
            t, series, summary = _run_single_ticker(
                ticker, strategy, period, timeframe, base_config, vix_series, bars_yr,
                df=ticker_data[ticker],
                gold_macro_filter=gold_macro_filter if ticker == "GLD" else None,
            )
            print(summary)
            if series is not None:
                per_asset[t] = series
    else:
        # Parallel (backtests only — no network calls)
        results_map: dict[str, tuple[pd.Series | None, str]] = {}
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {
                executor.submit(
                    _run_single_ticker,
                    ticker, strategy, period, timeframe, base_config, vix_series, bars_yr,
                    ticker_data[ticker],
                    gold_macro_filter if ticker == "GLD" else None,
                ): ticker
                for ticker in tickers
                if ticker in ticker_data
            }
            for future in as_completed(futures):
                t, series, summary = future.result()
                results_map[t] = (series, summary)

        # Print in original ticker order
        for ticker in tickers:
            if ticker not in ticker_data:
                print(f"  {ticker}: SKIP — no data")
            elif ticker in results_map:
                series, summary = results_map[ticker]
                print(summary)
                if series is not None:
                    per_asset[ticker] = series

    if len(per_asset) < 2:
        print("\nInsufficient tickers with data for portfolio analysis.")
        return

    # --- Align and compute equal-weight portfolio returns ---
    # Cash earns zero: flat positions contribute 0. Portfolio = sum(returns) / N_tickers.
    n_tickers = len(per_asset)
    returns_df = pd.DataFrame(per_asset).fillna(0.0)  # missing dates → 0 return
    portfolio_returns = returns_df.sum(axis=1) / n_tickers
    port_array = portfolio_returns.values

    print()
    print("=" * 70)
    print(f"PORTFOLIO ({n_tickers} assets, equal-weight, cash = 0)")
    print(f"Date range: {returns_df.index[0].date()} to {returns_df.index[-1].date()}")
    print(f"Total bars: {len(port_array)}")
    print()

    # Portfolio PSR
    port_psr = compute_probabilistic_sharpe(port_array, bars_per_year=bars_yr)
    print("--- Probabilistic Sharpe Ratio (assumes IID; bootstrap is primary) ---")
    print(f"Observed Sharpe (annualized): {port_psr['observed_sharpe_annual']:.3f}")
    print(f"P(true Sharpe > 0): {port_psr['psr']:.1%}")
    print(f"SE(Sharpe): {port_psr['se_sharpe']:.3f}")
    print(f"Skewness: {port_psr['skewness']:.3f}  |  Excess kurtosis: {port_psr['excess_kurtosis']:.3f}")
    print(f"N observations: {port_psr['n_obs']} daily returns")

    # Portfolio stationary bootstrap
    if len(port_array) >= 20:
        print()
        print("--- Stationary Bootstrap (primary test) ---")
        boot = run_stationary_bootstrap_oos_bar_returns(
            port_array,
            n_samples=5000,
            bars_per_year=bars_yr,
            alpha=0.05,
            seed=42,
        )
        if "error" in boot:
            print(f"ERROR: {boot['error']}")
        else:
            print(f"Observed Sharpe: {boot['observed_sharpe']:.3f}")
            print(
                f"Block length: {boot['bootstrap_block_length']:.0f}  |  "
                f"p-value: {boot['p_value']:.4f}  (n=5000, one-sided)"
            )
            status = "PASS" if boot["passed"] else "FAIL"
            print(f"Result: {status}")
            if "n_effective" in boot:
                print(
                    f"Effective independent obs: {boot['n_effective']}  "
                    f"(N={len(port_array)} / block={boot['bootstrap_block_length']:.0f})"
                )
                print(
                    f"Null distribution: median {boot['null_median']:.3f}  |  "
                    f"5th pctl {boot['null_5th']:.3f}  |  95th pctl {boot['null_95th']:.3f}"
                )
                print(f"Observed Sharpe rank: {boot['observed_rank_pct']:.0f}th percentile of null")

    # --- Cross-sectional test on EXCESS Sharpe (TF minus buy-and-hold) ---
    # Tests whether TF timing adds value beyond simply being long.
    # Buy-and-hold Sharpe = annualized Sharpe of raw daily returns over the same window.
    print()
    print("=" * 70)
    print("CROSS-SECTIONAL TEST (excess Sharpe: TF minus buy-and-hold)")
    print("=" * 70)

    rows: list[dict] = []
    for ticker, ret_series in per_asset.items():
        tf_psr = compute_probabilistic_sharpe(ret_series.values, bars_per_year=bars_yr)
        tf_sr = tf_psr["observed_sharpe_annual"]
        if np.isnan(tf_sr):
            continue

        # Buy-and-hold Sharpe over the same date window
        ticker_df = returns_df[[ticker]].dropna()
        if ticker in ticker_data:
            bh_df = ticker_data[ticker]
        else:
            bh_df = None
        if bh_df is not None and not bh_df.empty:
            # Align to same date range as strategy returns
            bh_aligned = bh_df.loc[bh_df.index.isin(returns_df.index)]
            if len(bh_aligned) > 10:
                bh_daily = bh_aligned["Close"].pct_change().dropna().values
                bh_mean = np.mean(bh_daily)
                bh_std = np.std(bh_daily, ddof=1)
                bh_sr = (bh_mean / bh_std * np.sqrt(bars_yr)) if bh_std > 0 else 0.0
            else:
                bh_sr = float("nan")
        else:
            bh_sr = float("nan")

        if not np.isnan(bh_sr):
            rows.append({
                "ticker": ticker,
                "tf_sharpe": tf_sr,
                "bh_sharpe": bh_sr,
                "excess": tf_sr - bh_sr,
            })

    n_assets = len(rows)
    if n_assets < 3:
        print("Insufficient assets for cross-sectional test.")
    else:
        # Print per-asset breakdown sorted by excess Sharpe
        print(f"\n{'Ticker':>6s}  {'TF':>7s}  {'B&H':>7s}  {'Excess':>7s}")
        print(f"{'------':>6s}  {'-------':>7s}  {'-------':>7s}  {'-------':>7s}")
        for r in sorted(rows, key=lambda x: x["excess"], reverse=True):
            marker = "+" if r["excess"] > 0 else " "
            print(f"  {r['ticker']:6s}  {r['tf_sharpe']:+.3f}   {r['bh_sharpe']:+.3f}   {marker}{r['excess']:.3f}")

        excess_values = np.array([r["excess"] for r in rows])
        positive_count = int(np.sum(excess_values > 0))
        mean_ex = float(np.mean(excess_values))
        median_ex = float(np.median(excess_values))
        std_ex = float(np.std(excess_values, ddof=1))

        print(f"\nMean excess Sharpe:   {mean_ex:.3f}")
        print(f"Median excess Sharpe: {median_ex:.3f}")
        print(f"Std excess Sharpe:    {std_ex:.3f}")
        print(f"TF beats B&H: {positive_count}/{n_assets} ({positive_count/n_assets:.0%})")

        # One-sample t-test: H0: mean excess Sharpe = 0
        t_stat, p_two_sided = sp_stats.ttest_1samp(excess_values, 0.0)
        p_one_sided = p_two_sided / 2 if t_stat > 0 else 1 - p_two_sided / 2
        se_mean = std_ex / np.sqrt(n_assets)
        ci_lo = mean_ex - 1.96 * se_mean
        ci_hi = mean_ex + 1.96 * se_mean

        print(f"\nOne-sample t-test (H0: mean excess Sharpe = 0):")
        print(f"  t-statistic: {t_stat:.3f}  (df={n_assets - 1})")
        print(f"  p-value (one-sided, excess > 0): {p_one_sided:.4f}")
        print(f"  95% CI for mean excess Sharpe: [{ci_lo:.3f}, {ci_hi:.3f}]")
        status = "PASS" if p_one_sided < 0.05 else "FAIL"
        print(f"  Result: {status} (a=0.05)")

        # Wilcoxon signed-rank (non-parametric)
        if n_assets >= 6:
            try:
                w_stat, w_p = sp_stats.wilcoxon(excess_values, alternative="greater")
                print(f"\nWilcoxon signed-rank (non-parametric, H1: median excess > 0):")
                print(f"  W-statistic: {w_stat:.0f}  |  p-value: {w_p:.4f}")
                w_status = "PASS" if w_p < 0.05 else "FAIL"
                print(f"  Result: {w_status} (a=0.05)")
            except ValueError:
                pass

    # --- Sanity check: zero-mean returns PSR should be ~50% ---
    print()
    print("--- Sanity check: zero-mean returns ---")
    zero_mean = port_array - np.mean(port_array)
    zero_psr = compute_probabilistic_sharpe(zero_mean, bars_per_year=bars_yr)
    print(f"Zero-mean PSR P(SR>0): {zero_psr['psr']:.1%} (expect ~50%)")


def main():
    parser = argparse.ArgumentParser(description="Portfolio-level pooled validation")
    parser.add_argument("--strategy", choices=["tf", "mr"], default="tf")
    parser.add_argument("--period", default="10y")
    parser.add_argument("--timeframe", default="Daily")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="Override default ticker list")
    parser.add_argument("--expanded", action="store_true",
                        help="Use expanded 23-ticker TF universe (ignored for MR)")
    parser.add_argument("--jobs", type=int, default=1,
                        help="Number of parallel workers for per-ticker backtests")
    args = parser.parse_args()

    tickers = args.tickers
    if tickers is None:
        if args.expanded and args.strategy == "tf":
            tickers = EXPANDED_TF_TICKERS
        else:
            tickers = DEFAULT_TF_TICKERS if args.strategy == "tf" else DEFAULT_MR_TICKERS

    run_pool_validation(tickers, args.strategy, args.period, args.timeframe, jobs=args.jobs)


if __name__ == "__main__":
    main()
