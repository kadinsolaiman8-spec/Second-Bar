"""
Walk-forward optimization: rolling train/test splits with grid search.
Optimizes params on train, evaluates on out-of-sample test, rolls forward.
"""

import itertools
import logging
import os as _os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd
from scipy import stats as _scipy_stats

from src.backtest import BacktestResult, _compute_min_warmup, _compute_min_warmup_trend, run_backtest
from src.data import fetch_single
from src.stop import StopRequested, clear_stop, is_stop_requested

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    """Single fold: OOS result and best params used."""
    oos_result: BacktestResult
    best_params: dict
    fold_index: int
    all_combo_metrics: list[float] | None = None  # metrics for all combos in this fold (for PBO)


def _compute_trade_sharpe(bt: BacktestResult, bars_per_year: int = 252) -> float:
    """Compute annualized Sharpe ratio from per-trade returns (legacy, noisy with few trades)."""
    if len(bt.trades) == 0:
        return 0.0
    returns = np.array([t.pnl_pct / 100 for t in bt.trades])
    if len(returns) < 2 or np.std(returns) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(bars_per_year))


def _compute_sharpe(bt: BacktestResult, bars_per_year: int = 252) -> float:
    """Compute annualized Sharpe from bar-level returns. Falls back to per-trade if unavailable."""
    if bt.bar_returns and len(bt.bar_returns) >= 2:
        return annualized_sharpe_from_bar_returns(np.asarray(bt.bar_returns, dtype=np.float64), bars_per_year)
    return _compute_trade_sharpe(bt, bars_per_year)


def annualized_sharpe_from_bar_returns(returns: np.ndarray, bars_per_year: int) -> float:
    """
    Annualized Sharpe from a 1-D bar return series (e.g. position × bar pct return).

    Args:
        returns: Per-bar strategy returns.
        bars_per_year: Bars per trading year for annualization (must match timeframe, e.g. 252 Daily).

    Returns:
        Annualized Sharpe, or 0.0 if undefined (too few bars or zero volatility).
    """
    if returns.size < 2:
        return 0.0
    s = float(np.std(returns, ddof=1))
    if s == 0:
        return 0.0
    return float(np.mean(returns) / s * np.sqrt(bars_per_year))


def _eval_metric(bt_result: BacktestResult, optimize_metric: str, bars_per_year: int) -> float:
    """Evaluate a backtest result against the chosen optimization metric."""
    if optimize_metric == "sharpe":
        return _compute_sharpe(bt_result, bars_per_year=bars_per_year)
    elif optimize_metric == "profit_factor":
        return bt_result.profit_factor
    elif optimize_metric == "outperformance":
        return bt_result.total_return - bt_result.buy_hold_return
    elif optimize_metric == "return_drawdown":
        return bt_result.total_return / bt_result.max_drawdown if bt_result.max_drawdown > 0 else (bt_result.total_return if bt_result.total_return > 0 else float("-inf"))
    return bt_result.total_return


def _resolve_n_jobs(n_jobs: int, n_tasks: int) -> int:
    """Clamp requested parallelism to (1, min(n_jobs_resolved, n_tasks, cpu_count)).

    n_jobs <= 0 is treated as 'use all CPUs'. Returns 1 if n_tasks == 0.
    """
    if n_tasks == 0:
        return 1
    cpu = _os.cpu_count() or 1
    effective = cpu if n_jobs <= 0 else n_jobs
    return max(1, min(effective, n_tasks, cpu))


def concatenate_oos_bar_returns(results: list[WalkForwardResult]) -> list[float]:
    """
    Stitch OOS ``bar_returns`` from each walk-forward fold in fold order.

    Warns if some folds have empty ``bar_returns`` while others do not.
    """
    out: list[float] = []
    saw_any = False
    saw_empty = False
    for r in results:
        br = r.oos_result.bar_returns
        if br:
            out.extend(br)
            saw_any = True
        else:
            saw_empty = True
    if saw_empty and saw_any:
        logger.warning(
            "concatenate_oos_bar_returns: some folds missing bar_returns while others have data",
        )
    return out


def oos_trade_power_notes_from_results(
    results: Sequence[WalkForwardResult],
    *,
    low_total_threshold: int,
    low_fold_threshold: int,
) -> list[str]:
    """
    Heuristic warnings when stitched OOS trade counts are sparse.

    Low trade counts weaken trade-level statistics (profit factor, win rate) and permutation
    tests that key off trades; bar-level primary bootstrap remains the main OOS check.

    Args:
        results: Completed walk-forward folds.
        low_total_threshold: Warn if sum of OOS ``num_trades`` is below this.
        low_fold_threshold: Warn for any fold whose OOS ``num_trades`` is below this.

    Returns:
        Human-readable notes (empty if no thresholds triggered).
    """
    if not results:
        return []
    fold_counts = [(r.fold_index, r.oos_result.num_trades) for r in results]
    total = sum(n for _, n in fold_counts)
    notes: list[str] = []
    if total < low_total_threshold:
        notes.append(
            f"Heuristic low power: total OOS trades {total} < {low_total_threshold} — "
            "trade-level stats are noisy; rely on bar-level primary bootstrap and context.",
        )
    thin = [idx for idx, n in fold_counts if n < low_fold_threshold]
    if thin:
        notes.append(
            f"Heuristic low power: fold(s) {thin} have OOS trades < {low_fold_threshold}.",
        )
    return notes


def run_stationary_bootstrap_oos_bar_returns(
    concatenated_returns: Sequence[float] | np.ndarray,
    n_samples: int,
    bars_per_year: int,
    alpha: float = 0.05,
    seed: int | np.random.Generator | None = None,
) -> dict[str, Any]:
    """
    Primary OOS validation: stationary bootstrap (Politis & Romano, 1994) on stitched
    out-of-sample strategy bar-P&L. Preserves serial dependence in the return series.

    Compares annualized Sharpe of the real concatenated series to bootstrapped replicates.
    One-sided p-value: fraction of bootstrap Sharpes >= observed (with (1+k)/(1+B) smoothing).

    This does not re-run the backtest on synthetic prices; it resamples the realized OOS
    P&L path. Selection metric during WFO (e.g. profit factor) may differ — callers should
    label CLI output accordingly.

    Args:
        concatenated_returns: Stitched ``oos_result.bar_returns`` across folds.
        n_samples: Number of bootstrap draws.
        bars_per_year: Annualization factor (must match WFO timeframe, e.g. 252 for Daily).
        alpha: Pass threshold (p < alpha).
        seed: Optional RNG seed for reproducibility (``arch`` ``StationaryBootstrap``).

    Returns:
        Dict with observed_sharpe, p_value, passed, bootstrap_block_length, n_samples, method;
        or ``{"error": "..."}`` on failure.
    """
    from arch.bootstrap import StationaryBootstrap, optimal_block_length

    arr = np.asarray(concatenated_returns, dtype=np.float64).ravel()
    if not np.all(np.isfinite(arr)):
        return {"error": "Non-finite values in concatenated OOS returns"}
    if len(arr) < 20:
        return {"error": "Insufficient OOS bars for stationary bootstrap (need >= 20)"}

    observed_sharpe = annualized_sharpe_from_bar_returns(arr, bars_per_year)

    strategy_pnl = arr
    if len(strategy_pnl) >= 20:
        try:
            opt = optimal_block_length(strategy_pnl)
            block_length = float(opt.iloc[0]["stationary"])
        except Exception:
            block_length = float("nan")
    else:
        block_length = float("nan")

    in_trade = False
    run_len = 0
    run_lengths: list[int] = []
    for r in strategy_pnl:
        if r != 0.0:
            in_trade = True
            run_len += 1
        elif in_trade:
            run_lengths.append(run_len)
            in_trade = False
            run_len = 0
    if in_trade and run_len > 0:
        run_lengths.append(run_len)
    if run_lengths:
        avg_hold = float(np.mean(run_lengths))
        block_length = max(block_length if not np.isnan(block_length) else 0.0, avg_hold)

    if np.isnan(block_length) or block_length < 1:
        block_length = max(1.0, len(strategy_pnl) ** (1 / 3))
        logger.warning(
            "OOS bootstrap: block_length invalid after floor; using heuristic %.1f",
            block_length,
        )

    block_int = max(1, int(round(block_length)))
    logger.info(
        "OOS stationary bootstrap: block_length=%d on %d concatenated OOS bars",
        block_int,
        len(arr),
    )

    bs = StationaryBootstrap(block_int, strategy_pnl, seed=seed)
    boot_sharpes: list[float] = []
    for pos_data, _ in bs.bootstrap(n_samples):
        sample = np.asarray(pos_data[0], dtype=np.float64).ravel()
        if sample.shape[0] != arr.shape[0]:
            logger.warning(
                "OOS bootstrap: unexpected sample length %d vs %d; skipping draw",
                sample.shape[0],
                arr.shape[0],
            )
            continue
        boot_sharpes.append(annualized_sharpe_from_bar_returns(sample, bars_per_year))

    valid_runs = len(boot_sharpes)
    if valid_runs == 0:
        return {"error": "All bootstrap draws failed"}

    beat_count = sum(1 for s in boot_sharpes if s >= observed_sharpe)
    p_value = (1 + beat_count) / (1 + valid_runs)
    passed = p_value < alpha
    interpretation = (
        "Primary test (concatenated OOS bar-P&L, stationary bootstrap). "
        "Diagnostic Sharpe vs dependent null — not necessarily the WFO selection metric."
    )

    # Diagnostics: effective sample size + null distribution shape
    boot_arr = np.array(boot_sharpes)
    n_effective = max(1, int(len(arr) / block_int))
    null_median = float(np.median(boot_arr))
    null_5th = float(np.percentile(boot_arr, 5))
    null_95th = float(np.percentile(boot_arr, 95))
    observed_rank_pct = float(np.mean(boot_arr <= observed_sharpe) * 100)

    return {
        "observed_sharpe": float(observed_sharpe),
        "p_value": float(p_value),
        "n_samples": valid_runs,
        "passed": passed,
        "alpha": alpha,
        "bootstrap_block_length": float(block_int),
        "method": "stationary_bootstrap_oos_bar_returns",
        "interpretation": interpretation,
        "n_effective": n_effective,
        "null_median": null_median,
        "null_5th": null_5th,
        "null_95th": null_95th,
        "observed_rank_pct": observed_rank_pct,
        "bootstrap_sharpes": boot_sharpes,
    }


def compute_deflated_sharpe(
    observed_sharpe: float,
    n_trials: int,
    bar_returns: list[float] | None = None,
    bars_per_year: int = 252,
) -> dict:
    """
    Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).
    Tests whether the observed Sharpe is significant given the number of trials.

    Returns dict with 'dsr' (z-score), 'is_significant' (bool), 'expected_max_sharpe'.
    DSR > 1.96 roughly corresponds to p < 0.05 (significant).
    """
    from scipy import stats

    if n_trials <= 1 or observed_sharpe <= 0:
        return {"dsr": 0.0, "is_significant": False, "expected_max_sharpe": 0.0}

    # Expected maximum Sharpe under the null (False Strategy Theorem)
    # E[max(SR)] ≈ sqrt(2 * ln(N)) for N independent trials with SR~N(0,1)
    expected_max_sr = np.sqrt(2 * np.log(n_trials))

    # Standard error of Sharpe ratio estimate
    n_bars = len(bar_returns) if bar_returns else bars_per_year
    if n_bars < 10:
        return {"dsr": 0.0, "is_significant": False, "expected_max_sharpe": float(expected_max_sr)}

    # Adjust for non-normality if we have bar returns
    skew = 0.0
    kurt = 3.0  # excess kurtosis = 0 for normal
    if bar_returns and len(bar_returns) >= 10:
        returns = np.array(bar_returns)
        skew = float(stats.skew(returns))
        kurt = float(stats.kurtosis(returns, fisher=False))  # non-excess

    # SE(SR) = sqrt((1 - skew*SR + (kurt-1)/4 * SR^2) / n_bars)
    sr = observed_sharpe / np.sqrt(bars_per_year)  # per-bar Sharpe
    se_sr = np.sqrt((1 - skew * sr + (kurt - 1) / 4 * sr**2) / max(1, n_bars))

    if se_sr <= 0:
        return {"dsr": 0.0, "is_significant": False, "expected_max_sharpe": float(expected_max_sr)}

    # DSR = t_observed - E[max SR] where t_observed = SR_perbar / SE(SR_perbar)
    # expected_max_sr = √(2·ln(N)) is in t-statistic units (multiples of SE),
    # NOT in annualized Sharpe units — do not divide by √bars_per_year.
    # Correct formula: DSR = SR/SE(SR) - √(2·ln(N))
    dsr_z = sr / se_sr - expected_max_sr

    return {
        "dsr": float(dsr_z),
        "is_significant": dsr_z > 1.96,
        "expected_max_sharpe": float(expected_max_sr),
    }


def compute_probabilistic_sharpe(
    bar_returns: np.ndarray | list[float],
    benchmark_sharpe: float = 0.0,
    bars_per_year: int = 252,
) -> dict:
    """
    Probabilistic Sharpe Ratio (Bailey & López de Prado, 2012, Journal of Risk).

    Computes P(true SR > benchmark) given observed bar returns, accounting for
    skewness and kurtosis of the return distribution.

    NOTE: Assumes IID returns.  For autocorrelated series (e.g. trend-following
    where positions persist for weeks), this *overstates* confidence.  Use the
    stationary bootstrap p-value as the primary test; PSR is complementary.

    Formula:
        SE(SR̂) = sqrt((1 - γ₃·SR̂ + (γ₄-1)/4 · SR̂²) / (T-1))
        PSR    = Φ((SR̂ - SR*) / SE(SR̂))

    where γ₃ = skewness, γ₄ = excess kurtosis, SR̂ = per-bar Sharpe,
    SR* = benchmark per-bar Sharpe, Φ = standard normal CDF.
    """
    from scipy import stats as sp_stats

    returns = np.asarray(bar_returns, dtype=np.float64)
    n = len(returns)
    if n < 10:
        return {
            "psr": 0.5,
            "se_sharpe": float("nan"),
            "skewness": float("nan"),
            "excess_kurtosis": float("nan"),
            "n_obs": n,
            "observed_sharpe_annual": float("nan"),
        }

    mean_r = float(np.mean(returns))
    std_r = float(np.std(returns, ddof=1))
    if std_r == 0:
        return {
            "psr": 0.5,
            "se_sharpe": 0.0,
            "skewness": 0.0,
            "excess_kurtosis": 0.0,
            "n_obs": n,
            "observed_sharpe_annual": 0.0,
        }

    sr_bar = mean_r / std_r  # per-bar Sharpe
    sr_annual = sr_bar * np.sqrt(bars_per_year)
    benchmark_bar = benchmark_sharpe / np.sqrt(bars_per_year)

    skew = float(sp_stats.skew(returns))
    excess_kurt = float(sp_stats.kurtosis(returns, fisher=True))  # fisher=True → excess

    # SE(SR̂) per Bailey & López de Prado (2012)
    numerator = 1 - skew * sr_bar + (excess_kurt / 4) * sr_bar**2
    numerator = max(numerator, 1e-12)  # guard against negative (extreme skew/kurtosis)
    se_sr = np.sqrt(numerator / max(1, n - 1))

    if se_sr <= 0:
        psr = 0.5
    else:
        z = (sr_bar - benchmark_bar) / se_sr
        psr = float(sp_stats.norm.cdf(z))

    return {
        "psr": psr,
        "se_sharpe": float(se_sr * np.sqrt(bars_per_year)),  # annualised SE
        "skewness": skew,
        "excess_kurtosis": excess_kurt,
        "n_obs": n,
        "observed_sharpe_annual": float(sr_annual),
    }


def compute_pbo(fold_sharpes: list[list[float]]) -> dict:
    """
    Probability of Backtest Overfitting (simplified CSCV approach).

    Takes a matrix of Sharpe ratios: fold_sharpes[fold_idx][combo_idx].
    Splits folds into IS/OOS halves, checks if the IS-optimal combo
    underperforms the OOS median.

    Returns dict with 'pbo' (0-1), 'interpretation' (str).
    PBO > 0.5 = overfitting likely; > 0.3 = concerning.
    """
    if not fold_sharpes or len(fold_sharpes) < 4:
        return {"pbo": None, "interpretation": "Insufficient folds for PBO (need >= 4)"}

    n_folds = len(fold_sharpes)
    n_combos = len(fold_sharpes[0])
    if n_combos < 2:
        return {"pbo": None, "interpretation": "Insufficient combos for PBO"}

    # Convert to numpy matrix: folds x combos
    matrix = np.array(fold_sharpes)
    if matrix.shape[0] < 4 or matrix.shape[1] < 2:
        return {"pbo": None, "interpretation": "Insufficient data for PBO"}

    overfit_count = 0
    total_splits = 0

    # Generate combinatorial IS/OOS splits: all ways to split folds in half
    from itertools import combinations
    half = n_folds // 2
    for is_indices in combinations(range(n_folds), half):
        oos_indices = [i for i in range(n_folds) if i not in is_indices]

        # IS: average Sharpe per combo across IS folds
        is_avg = matrix[list(is_indices), :].mean(axis=0)
        # OOS: average Sharpe per combo across OOS folds
        oos_avg = matrix[list(oos_indices), :].mean(axis=0)

        # Best combo in-sample
        best_is_combo = int(np.argmax(is_avg))
        # Check if it underperforms OOS median
        oos_median = float(np.median(oos_avg))
        if oos_avg[best_is_combo] < oos_median:
            overfit_count += 1
        total_splits += 1

    pbo = overfit_count / max(1, total_splits)

    if pbo > 0.5:
        interp = "HIGH overfitting risk (PBO > 0.5)"
    elif pbo > 0.3:
        interp = "Moderate overfitting risk (PBO > 0.3)"
    else:
        interp = "Low overfitting risk"

    return {"pbo": float(pbo), "interpretation": interp}


def _plateau_select(
    combo_metrics: list[tuple[dict, float]],
    param_grid: dict,
) -> tuple[dict, float]:
    """
    Plateau selection: instead of picking the single best combo, pick the one
    whose Hamming-1 neighbors have the best average performance.
    Takes top 20% combos, then among those selects the one with highest
    average neighbor metric. Falls back to best if no neighbors found.
    """
    if len(combo_metrics) <= 1:
        return combo_metrics[0] if combo_metrics else ({}, float("-inf"))

    # Sort by metric descending
    sorted_combos = sorted(combo_metrics, key=lambda x: x[1], reverse=True)
    top_n = max(1, len(sorted_combos) // 5)  # top 20%
    candidates = sorted_combos[:top_n]

    # Build lookup: frozenset of param items → metric
    metric_lookup = {frozenset(params.items()): metric for params, metric in combo_metrics}

    # For each candidate, find Hamming-1 neighbors and compute average
    grid_keys = list(param_grid.keys())
    grid_values = {k: (v if isinstance(v, list) else [v]) for k, v in param_grid.items()}

    best_candidate = candidates[0]
    best_neighbor_avg = float("-inf")

    for params, metric in candidates:
        neighbor_metrics = []
        for key in grid_keys:
            current_val = params.get(key)
            for alt_val in grid_values.get(key, []):
                if alt_val == current_val:
                    continue
                neighbor = dict(params)
                neighbor[key] = alt_val
                neighbor_key = frozenset(neighbor.items())
                if neighbor_key in metric_lookup:
                    neighbor_metrics.append(metric_lookup[neighbor_key])
        if neighbor_metrics:
            avg = sum(neighbor_metrics) / len(neighbor_metrics)
            if avg > best_neighbor_avg:
                best_neighbor_avg = avg
                best_candidate = (params, metric)

    return best_candidate


def _run_fold(
    fold_spec: dict,
    df: pd.DataFrame,
    config: dict,
    param_grid: dict,
    strategy: str,
    optimize_metric: str,
    ignore_volatility: bool,
    timeframe: str | None,
    vix_series: pd.Series | None,
    symbol: str,
    bars_per_year: int,
    min_test_bars: int,
) -> WalkForwardResult | None:
    """Execute a single WFO fold. Module-level for multiprocessing pickling."""
    fold_idx = fold_spec["fold_idx"]
    train_start = fold_spec["train_start"]
    train_end = fold_spec["train_end"]
    test_start_idx = fold_spec["test_start_idx"]
    test_end_idx = fold_spec["test_end_idx"]
    warmup_start = fold_spec["warmup_start"]

    train_df = df.iloc[train_start:train_end].copy()

    logger.info(
        "Walk-forward %s: fold %d | train %s-%s",
        symbol, fold_idx, str(df.index[train_start])[:10], str(df.index[train_end - 1])[:10],
    )

    best_config = None
    best_metric = float("-inf")
    best_params = {}
    fold_combo_metrics: list[tuple[dict, float, dict]] = []

    # Recompute grid decomposition from config/param_grid/strategy
    if strategy == "tf":
        tf_keys = list(param_grid.keys())
        tf_values = [v if isinstance(v, list) else [v] for v in param_grid.values()]
        ind_keys_list = []
        ind_values = []
        other_keys_list = []
        other_values = []
    else:
        ind_section = config.get("indicators", {})
        ind_grid = {k: v for k, v in param_grid.items() if k in ind_section}
        other_grid = {k: v for k, v in param_grid.items() if k not in ind_section}
        if not ind_grid and not other_grid:
            ind_grid = {"rsi_oversold": [30, 35, 40], "rsi_overbought": [60, 65, 70]}
        ind_keys_list = list(ind_grid.keys())
        ind_values = [v if isinstance(v, list) else [v] for v in ind_grid.values()]
        other_keys_list = list(other_grid.keys())
        other_values = [v if isinstance(v, list) else [v] for v in other_grid.values()]
        tf_keys = []
        tf_values = []

    if strategy == "tf":
        for params in itertools.product(*tf_values) if tf_values else [()]:
            cfg = dict(config)
            cfg["strategy"] = "tf"
            cfg.setdefault("trend_following", {})
            cfg["trend_following"] = dict(cfg.get("trend_following", {}))
            cfg.setdefault("backtest", {})
            cfg["backtest"] = dict(cfg.get("backtest", {}))
            combo_params = {}
            if tf_keys and params:
                for k, v in zip(tf_keys, params):
                    combo_params[k] = v
                    if k == "atr_multiplier":
                        cfg["backtest"]["trailing_stop_atr_multiplier"] = v
                    elif k == "max_hold_bars":
                        cfg["backtest"]["max_hold_bars"] = v
                    else:
                        cfg["trend_following"][k] = v
            bt = run_backtest(
                symbol, config=cfg, df=train_df,
                ignore_volatility=ignore_volatility, timeframe=timeframe, vix_series=vix_series,
            )
            if bt is None:
                fold_combo_metrics.append((combo_params, float("-inf"), cfg))
                continue
            metric = _eval_metric(bt, optimize_metric, bars_per_year)
            fold_combo_metrics.append((combo_params, metric, cfg))
            if metric > best_metric:
                best_metric = metric
                best_config = cfg
                best_params = combo_params
    else:
        for ind_params in itertools.product(*ind_values) if ind_values else [()]:
            for other_params in itertools.product(*other_values) if other_values else [()]:
                cfg = dict(config)
                cfg.setdefault("indicators", {})
                cfg["indicators"] = dict(cfg["indicators"])
                if ind_keys_list and ind_params:
                    for k, v in zip(ind_keys_list, ind_params):
                        cfg["indicators"][k] = v
                if other_keys_list and other_params:
                    for k, v in zip(other_keys_list, other_params):
                        cfg[k] = v
                WEIGHT_MAP = {"rsi_weight": "rsi", "trend_weight": "trend"}
                for cfg_key, weight_key in WEIGHT_MAP.items():
                    if cfg_key in cfg:
                        cfg.setdefault("indicator_weights", {})
                        cfg["indicator_weights"] = dict(cfg.get("indicator_weights", {}))
                        cfg["indicator_weights"][weight_key] = cfg[cfg_key]
                bt = run_backtest(
                    symbol, config=cfg, df=train_df,
                    ignore_volatility=ignore_volatility, timeframe=timeframe, vix_series=vix_series,
                )
                combo_params = {
                    **{k: cfg["indicators"].get(k) for k in ind_keys_list if k in cfg["indicators"]},
                    **{k: cfg.get(k) for k in other_keys_list if k in cfg},
                }
                if bt is None:
                    fold_combo_metrics.append((combo_params, float("-inf"), cfg))
                    continue
                metric = _eval_metric(bt, optimize_metric, bars_per_year)
                fold_combo_metrics.append((combo_params, metric, cfg))
                if metric > best_metric:
                    best_metric = metric
                    best_config = cfg
                    best_params = combo_params

    # Plateau selection: prefer stable combos over lucky peaks
    if len(fold_combo_metrics) > 1 and param_grid:
        valid_combos = [(p, m) for p, m, _ in fold_combo_metrics if m > float("-inf")]
        if len(valid_combos) >= 3:
            plateau_params, plateau_metric = _plateau_select(valid_combos, param_grid)
            for p, m, c in fold_combo_metrics:
                if p == plateau_params:
                    best_params = plateau_params
                    best_config = c
                    best_metric = plateau_metric
                    break

    fold_metric_list = [m for _, m, _ in fold_combo_metrics]

    # Log IS diagnostics for ALL folds (including rejected ones) for regime analysis
    _positive_is = sum(1 for _, m, _ in fold_combo_metrics if m > 0)
    _total_is = len(fold_combo_metrics)
    _vix_regime_diag = "unknown"
    _vix_mean_diag = None
    if vix_series is not None:
        _oos_dates = df.iloc[test_start_idx:test_end_idx].index
        _vix_ovlp = vix_series.reindex(_oos_dates).dropna()
        if len(_vix_ovlp) > 0:
            _vix_mean_diag = float(_vix_ovlp.mean())
            _vix_regime_diag = "mr_favored" if _vix_mean_diag < 18 else ("tf_favored" if _vix_mean_diag > 25 else "mixed")

    if best_config is None:
        logger.info(
            "Walk-forward %s: fold %d REJECTED (no valid config) | regime=%s vix_mean=%s | IS positive: %d/%d",
            symbol, fold_idx, _vix_regime_diag,
            f"{_vix_mean_diag:.1f}" if _vix_mean_diag is not None else "N/A",
            _positive_is, _total_is,
        )
        return None

    if test_end_idx - test_start_idx < min_test_bars:
        logger.info(
            "Walk-forward %s: fold %d SKIPPED (test < %d bars) | regime=%s vix_mean=%s | IS positive: %d/%d",
            symbol, fold_idx, min_test_bars, _vix_regime_diag,
            f"{_vix_mean_diag:.1f}" if _vix_mean_diag is not None else "N/A",
            _positive_is, _total_is,
        )
        return None

    test_df = df.iloc[warmup_start:test_end_idx].copy()
    oos = run_backtest(
        symbol, config=best_config, df=test_df,
        ignore_volatility=ignore_volatility, timeframe=timeframe, vix_series=vix_series,
    )
    if oos is None:
        return None

    logger.info(
        "Walk-forward %s: fold %d DIAG | regime=%s vix_mean=%s | IS positive: %d/%d | plateau: %s",
        symbol, fold_idx, _vix_regime_diag,
        f"{_vix_mean_diag:.1f}" if _vix_mean_diag is not None else "N/A",
        _positive_is, _total_is, best_params,
    )

    logger.info(
        "Walk-forward %s: fold %d done | OOS return %.1f%% | trades %d | best %s",
        symbol, fold_idx, oos.total_return, oos.num_trades, best_params,
    )
    return WalkForwardResult(
        oos_result=oos, best_params=best_params,
        fold_index=fold_idx, all_combo_metrics=fold_metric_list,
    )


def _run_fold_star(args: tuple) -> WalkForwardResult | None:
    """Unpack tuple for ProcessPoolExecutor.map."""
    return _run_fold(*args)


def run_walk_forward_optimization(
    symbol: str,
    config: dict,
    period: str = "5y",
    interval: str = "1d",
    train_bars: int = 504,
    test_bars: int = 63,
    step_bars: int = 63,
    embargo_bars: int = 5,
    param_grid: dict | None = None,
    optimize_metric: str = "outperformance",
    ignore_volatility: bool = False,
    timeframe: str | None = None,
    strategy: str = "mr",
    vix_series: pd.Series | None = None,
    n_jobs: int = 1,
) -> list[WalkForwardResult]:
    """
    Walk-forward optimization using run_backtest.
    For each fold: grid-search on train, evaluate best params on test (OOS).
    vix_series: historical VIX close values for regime classification in backtests.
    n_jobs: parallel worker processes (1=serial, -1=all CPUs).
    Returns list of WalkForwardResult (OOS result + best params per fold).
    """
    logger.info(
        "Walk-forward %s: starting | period=%s | train=%d test=%d step=%d embargo=%d",
        symbol, period, train_bars, test_bars, step_bars, embargo_bars,
    )
    df = fetch_single(symbol, period=period, interval=interval)
    if df is None or df.empty or len(df) < 30:
        logger.warning("Walk-forward %s: insufficient data (got %d bars)", symbol, len(df) if df is not None else 0)
        return []

    min_warmup = _compute_min_warmup_trend(config) if strategy == "tf" else _compute_min_warmup(config)
    if len(df) < train_bars + test_bars + min_warmup:
        logger.warning(
            "Walk-forward %s: need %d+ bars, got %d",
            symbol, train_bars + test_bars + min_warmup, len(df),
        )
        return []

    param_grid = param_grid or {
        "rsi_oversold": [30, 35, 40],
        "rsi_overbought": [60, 65, 70],
    }

    if strategy == "tf":
        tf_keys = list(param_grid.keys())
        tf_values = [v if isinstance(v, list) else [v] for v in param_grid.values()]
    else:
        ind_section = config.get("indicators", {})
        ind_grid = {k: v for k, v in param_grid.items() if k in ind_section}
        other_grid = {k: v for k, v in param_grid.items() if k not in ind_section}
        if not ind_grid and not other_grid:
            ind_grid = {"rsi_oversold": [30, 35, 40], "rsi_overbought": [60, 65, 70]}
        ind_keys_list = list(ind_grid.keys())
        ind_values = [v if isinstance(v, list) else [v] for v in ind_grid.values()]
        other_keys_list = list(other_grid.keys())
        other_values = [v if isinstance(v, list) else [v] for v in other_grid.values()]
        tf_keys = None
        tf_values = None

    num_combos = 1
    if strategy == "tf":
        for v in tf_values:
            num_combos *= len(v) if v else 1
    else:
        for v in ind_values + other_values:
            num_combos *= len(v) if v else 1

    total_folds = sum(
        1 for s in range(min_warmup, len(df) - train_bars - embargo_bars - test_bars + 1, step_bars)
    )
    logger.info("Walk-forward %s: %d folds, %d param combos per fold", symbol, total_folds, num_combos)

    bars_per_year = 252 if (timeframe or "Daily") == "Daily" else 52 if (timeframe or "") == "1W" else 1638
    min_test_bars = min(20, test_bars)

    # Pre-compute fold windows
    fold_specs = []
    start = min_warmup
    fold_idx = 0
    while start + train_bars + embargo_bars + test_bars <= len(df):
        train_end = start + train_bars
        test_start = train_end + embargo_bars
        test_end = min(test_start + test_bars, len(df))
        warmup_start = max(0, test_start - min_warmup)
        fold_specs.append({
            "fold_idx": fold_idx, "train_start": start, "train_end": train_end,
            "test_start_idx": test_start, "test_end_idx": test_end,
            "warmup_start": warmup_start,
        })
        start += step_bars
        fold_idx += 1

    n_workers = _resolve_n_jobs(n_jobs, len(fold_specs))
    results: list[WalkForwardResult] = []

    if n_workers <= 1:
        # Serial path — unchanged behavior with stop check per fold
        for spec in fold_specs:
            if is_stop_requested():
                clear_stop()
                logger.info("Walk-forward %s: stopped by user after %d folds", symbol, len(results))
                raise StopRequested()
            result = _run_fold(
                spec, df, config, param_grid, strategy, optimize_metric,
                ignore_volatility, timeframe, vix_series, symbol,
                bars_per_year, min_test_bars,
            )
            if result is not None:
                results.append(result)
    else:
        # Parallel path
        logger.info("Walk-forward %s: dispatching %d folds to %d workers", symbol, len(fold_specs), n_workers)
        fold_args = [
            (spec, df, config, param_grid, strategy, optimize_metric,
             ignore_volatility, timeframe, vix_series, symbol,
             bars_per_year, min_test_bars)
            for spec in fold_specs
        ]
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            for result in executor.map(_run_fold_star, fold_args):
                if result is not None:
                    results.append(result)
        results.sort(key=lambda r: r.fold_index)
        # Coarse stop check after all folds complete
        if is_stop_requested():
            clear_stop()
            raise StopRequested()

    avg_ret = sum(r.oos_result.total_return for r in results) / len(results) if results else 0
    logger.info("Walk-forward %s: complete | %d folds | avg OOS return %.1f%%", symbol, len(results), avg_ret)
    return results


def _bootstrap_df(df: pd.DataFrame, block_length: float) -> pd.DataFrame:
    """Resample bar returns using stationary bootstrap, reconstruct synthetic OHLCV.
    Preserves local autocorrelation and volatility clustering (Politis & Romano, 1994)."""
    from arch.bootstrap import StationaryBootstrap

    close = df["Close"].values.astype(float)
    returns = close[1:] / close[:-1]

    bs = StationaryBootstrap(block_length, returns)
    for pos_data, _ in bs.bootstrap(1):
        resampled_returns = pos_data[0]
        break

    new_close = np.empty(len(close))
    new_close[0] = close[0]
    for i, r in enumerate(resampled_returns):
        new_close[i + 1] = new_close[i] * r
    scale = new_close / np.where(close == 0, 1.0, close)
    bootstrapped = df.copy()
    bootstrapped["Close"] = new_close
    bootstrapped["Open"] = df["Open"].values * scale
    bootstrapped["High"] = df["High"].values * scale
    bootstrapped["Low"] = df["Low"].values * scale
    return bootstrapped


def _bar_permute_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bar-permutation shuffle: independently shuffle intraday (open→close) and
    overnight (close→open) log returns, then reconstruct synthetic OHLCV.
    Preserves each return distribution but destroys temporal autocorrelation —
    the structure that both MR and TF strategies exploit.
    High/Low are scaled proportionally so OHLCV relationships remain valid.
    """
    import numpy as np

    opens = df["Open"].values.astype(float)
    closes = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(df)

    # Intraday: log(close[t] / open[t])
    intraday = np.where(opens > 0, np.log(closes / np.where(opens > 0, opens, 1.0)), 0.0)
    # Overnight: log(open[t] / close[t-1])
    overnight = np.where(closes[:-1] > 0, np.log(opens[1:] / np.where(closes[:-1] > 0, closes[:-1], 1.0)), 0.0)

    np.random.shuffle(intraday)
    np.random.shuffle(overnight)

    new_opens = np.empty(n)
    new_closes = np.empty(n)
    new_opens[0] = opens[0]
    new_closes[0] = opens[0] * np.exp(intraday[0])
    for t in range(1, n):
        new_opens[t] = new_closes[t - 1] * np.exp(overnight[t - 1])
        new_closes[t] = new_opens[t] * np.exp(intraday[t])

    # Scale High and Low proportionally within each bar
    with np.errstate(divide="ignore", invalid="ignore"):
        intraday_range_orig = np.where(opens > 0, closes / opens, 1.0)
        intraday_range_new = np.where(new_opens > 0, new_closes / new_opens, 1.0)
        high_mult = np.where(opens > 0, highs / opens, 1.0)
        low_mult = np.where(opens > 0, lows / opens, 1.0)

    new_highs = new_opens * high_mult
    new_lows = new_opens * low_mult

    # Ensure High >= max(Open, Close) and Low <= min(Open, Close)
    new_highs = np.maximum(new_highs, np.maximum(new_opens, new_closes))
    new_lows = np.minimum(new_lows, np.minimum(new_opens, new_closes))

    perm = df.copy()
    perm["Open"] = new_opens
    perm["Close"] = new_closes
    perm["High"] = new_highs
    perm["Low"] = new_lows
    return perm


# ---------------------------------------------------------------------------
# Permutation worker state & functions (for ProcessPoolExecutor initializer)
# ---------------------------------------------------------------------------
_perm_state: dict = {}  # populated in worker processes only


def _perm_worker_init(df, config, symbol, ignore_volatility, timeframe, real_pf):
    """Called once per worker process. Stores large data to avoid per-task pickle."""
    _perm_state.update({
        "df": df, "config": config, "symbol": symbol,
        "ignore_volatility": ignore_volatility,
        "timeframe": timeframe, "real_pf": real_pf,
    })


def _perm_worker_task(_: int) -> bool | None:
    """Run one bar-permutation sim. Returns True if permuted pf >= real_pf, False if not, None on failure."""
    s = _perm_state
    perm_df = _bar_permute_df(s["df"])
    bt = run_backtest(
        s["symbol"], config=s["config"], df=perm_df,
        ignore_volatility=s["ignore_volatility"], timeframe=s["timeframe"],
    )
    if bt is None:
        return None
    return bt.profit_factor >= s["real_pf"]


def run_bar_permutation_test(
    df: pd.DataFrame,
    best_config: dict,
    symbol: str,
    strategy: str = "mr",
    n_simulations: int = 1000,
    timeframe: str | None = None,
    ignore_volatility: bool = False,
    alpha: float = 0.01,
    n_jobs: int = 1,
) -> dict:
    """
    In-sample bar-permutation significance test.

    Shuffles bar-level log returns (intraday and overnight separately) to destroy
    temporal autocorrelation while preserving the return distribution. Re-runs the
    strategy 1000× on synthetic series, compares profit factor.

    Test statistic: profit factor (gross_profit / gross_loss).
    p-value = fraction of permuted runs that beat real profit factor.
    Pass threshold: p < 0.01 (strict — abandon strategy if it can't beat random bars).

    Notes:
    - **Secondary** test vs primary OOS stationary bootstrap (see roadmap). Test statistic
      is **profit factor**, not Sharpe.
    - For **TF**, destroying autocorrelation is harsh: **FAIL is often expected** and weak
      evidence of no edge; **PASS is strong**. For **MR**, bias is smaller.
    """
    real_bt = run_backtest(
        symbol, config=best_config, df=df, ignore_volatility=ignore_volatility, timeframe=timeframe
    )
    if real_bt is None:
        return {"error": "Backtest failed on real data"}

    real_pf = real_bt.profit_factor
    real_trades = real_bt.num_trades

    beat_count = 0
    valid_runs = 0
    n_workers = _resolve_n_jobs(n_jobs, n_simulations)

    if n_workers <= 1:
        for _ in range(n_simulations):
            perm_df = _bar_permute_df(df)
            bt = run_backtest(
                symbol, config=best_config, df=perm_df, ignore_volatility=ignore_volatility, timeframe=timeframe
            )
            if bt is None:
                continue
            valid_runs += 1
            if bt.profit_factor >= real_pf:
                beat_count += 1
    else:
        logger.info("Bar-permutation IS %s: dispatching %d sims to %d workers", symbol, n_simulations, n_workers)
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_perm_worker_init,
            initargs=(df, best_config, symbol, ignore_volatility, timeframe, real_pf),
        ) as executor:
            for outcome in executor.map(_perm_worker_task, range(n_simulations)):
                if outcome is None:
                    continue
                valid_runs += 1
                if outcome:
                    beat_count += 1

    if valid_runs == 0:
        return {"error": "All permutation backtests failed"}

    p_value = beat_count / valid_runs
    return {
        "real_profit_factor": real_pf,
        "real_trades": real_trades,
        "p_value": p_value,
        "n_simulations": valid_runs,
        "passed": p_value < alpha,
        "alpha": alpha,
        "method": "bar_permutation_insample",
    }


def run_bar_permutation_oos_test(
    df: pd.DataFrame,
    best_config: dict,
    symbol: str,
    strategy: str = "mr",
    n_simulations: int = 500,
    timeframe: str | None = None,
    ignore_volatility: bool = False,
    precomputed_real_bt: "BacktestResult | None" = None,
    n_jobs: int = 1,
) -> dict:
    """
    OOS significance test using bar permutation.

    Replaces the randomized entry test. Test statistic: profit factor.
    Shuffles bar-level log returns (intraday and overnight independently) to produce
    synthetic price series with the same return distribution but no temporal structure.
    p-value = fraction of permuted runs that beat the real profit factor.

    **Secondary** test (full-history bar permutation; statistic = profit factor). For **TF**,
    a **FAIL is often expected** (null destroys autocorrelation TF uses); interpret alongside
    the primary OOS stationary bootstrap on concatenated bar-P&L. A **PASS** is strong.
    For **MR**, bar permutation is less biased against the strategy.

    Compared to randomized entry test:
    - Tests the full strategy, not just entry timing
    - Power scales with number of bars (not trades) — fixes TF underpowering
    - Shares permutation code with in-sample gate for consistency
    """
    real_bt = precomputed_real_bt or run_backtest(
        symbol, config=best_config, df=df, ignore_volatility=ignore_volatility, timeframe=timeframe
    )
    if real_bt is None:
        return {"error": "Backtest failed on real data"}

    real_pf = real_bt.profit_factor
    real_trades = real_bt.num_trades
    real_win_rate = real_bt.win_rate / 100.0 if real_bt.win_rate else 0.0

    beat_count = 0
    valid_runs = 0
    n_workers = _resolve_n_jobs(n_jobs, n_simulations)

    if n_workers <= 1:
        for _ in range(n_simulations):
            perm_df = _bar_permute_df(df)
            bt = run_backtest(
                symbol, config=best_config, df=perm_df, ignore_volatility=ignore_volatility, timeframe=timeframe
            )
            if bt is None:
                continue
            valid_runs += 1
            if bt.profit_factor >= real_pf:
                beat_count += 1
    else:
        logger.info("Bar-permutation OOS %s: dispatching %d sims to %d workers", symbol, n_simulations, n_workers)
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_perm_worker_init,
            initargs=(df, best_config, symbol, ignore_volatility, timeframe, real_pf),
        ) as executor:
            for outcome in executor.map(_perm_worker_task, range(n_simulations)):
                if outcome is None:
                    continue
                valid_runs += 1
                if outcome:
                    beat_count += 1

    if valid_runs == 0:
        return {"error": "All permutation backtests failed"}

    p_value = beat_count / valid_runs
    bayes = _bayesian_mean_trade_return([t.pnl_pct for t in real_bt.trades]) if real_bt.trades else {}
    power = _estimate_test_power([t.pnl_pct for t in real_bt.trades]) if real_bt.trades else None

    return {
        "real_profit_factor": real_pf,
        "real_trades": real_trades,
        "real_win_rate": real_win_rate,
        "p_value": p_value,
        "n_simulations": valid_runs,
        "passed": p_value < 0.05,
        "method": "bar_permutation_oos",
        "bayes": bayes,
        "power": power,
    }


def run_permutation_test(
    df: pd.DataFrame,
    best_config: dict,
    symbol: str,
    strategy: str = "mr",
    n_samples: int = 500,
    optimize_metric: str = "sharpe",
    timeframe: str | None = None,
    ignore_volatility: bool = False,
) -> dict:
    """
    Bootstrap significance test: validate that the edge is not random noise.
    Uses stationary bootstrap (Politis & Romano, 1994) which preserves local
    autocorrelation — unlike i.i.d. shuffling which destroys the temporal
    structure that trend-following strategies exploit.
    p-value = fraction of bootstrap runs that beat the real result.
    Passes if p < 0.05 (edge is statistically significant).
    """
    from arch.bootstrap import optimal_block_length

    bars_per_year = 252 if (timeframe or "Daily") == "Daily" else 52 if (timeframe or "") == "1W" else 1638

    real_bt = run_backtest(symbol, config=best_config, df=df, ignore_volatility=ignore_volatility, timeframe=timeframe)
    if real_bt is None:
        return {"error": "Backtest failed on real data"}

    if optimize_metric == "sharpe":
        real_metric = _compute_sharpe(real_bt, bars_per_year=bars_per_year)
    else:
        real_metric = real_bt.total_return

    # Compute optimal block length from strategy P&L series (position × return).
    # Raw equity returns have near-zero autocorrelation (~EMH), giving block_length ~2-3
    # which is almost identical to i.i.d. shuffling.
    # Strategy P&L should have longer autocorrelation, but for TF strategies that are
    # mostly flat, the series is mostly zeros and the algorithm still returns short values.
    # Fix: use average trade duration as a floor — the block length must be at least as
    # long as the average holding period, since that's the autocorrelation timescale.
    strategy_pnl = np.array(real_bt.bar_returns) if real_bt.bar_returns else None
    if strategy_pnl is not None and len(strategy_pnl) >= 20:
        try:
            opt = optimal_block_length(strategy_pnl)
            block_length = float(opt.iloc[0]["stationary"])
        except Exception:
            block_length = float("nan")
    else:
        block_length = float("nan")

    # Floor: average trade duration (non-zero run length in bar_returns).
    # A block shorter than avg hold duration can't preserve position autocorrelation.
    if strategy_pnl is not None and len(strategy_pnl) > 0:
        in_trade = False
        run_len = 0
        run_lengths = []
        for r in strategy_pnl:
            if r != 0.0:
                in_trade = True
                run_len += 1
            elif in_trade:
                run_lengths.append(run_len)
                in_trade = False
                run_len = 0
        if in_trade and run_len > 0:
            run_lengths.append(run_len)
        if run_lengths:
            avg_hold = float(np.mean(run_lengths))
            block_length = max(block_length if not np.isnan(block_length) else 0.0, avg_hold)

    if np.isnan(block_length) or block_length < 1:
        block_length = max(1.0, len(strategy_pnl) ** (1 / 3) if strategy_pnl is not None else 10.0)
        logger.warning("block_length invalid after floor; using heuristic %.1f", block_length)

    logger.info("Bootstrap test: stationary bootstrap (block_length=%.1f) on %d bars", block_length, len(df))

    beat_count = 0
    valid_runs = 0
    for _ in range(n_samples):
        boot_df = _bootstrap_df(df, block_length)
        bt = run_backtest(symbol, config=best_config, df=boot_df, ignore_volatility=ignore_volatility, timeframe=timeframe)
        if bt is None:
            continue
        valid_runs += 1
        m = _compute_sharpe(bt, bars_per_year=bars_per_year) if optimize_metric == "sharpe" else bt.total_return
        if m >= real_metric:
            beat_count += 1

    if valid_runs == 0:
        return {"error": "All bootstrap backtests failed"}

    p_value = beat_count / valid_runs
    return {
        "real_metric": real_metric,
        "p_value": p_value,
        "n_samples": valid_runs,
        "passed": p_value < 0.05,
        "metric_name": optimize_metric,
        "bootstrap_block_length": block_length,
        "method": "stationary_bootstrap",
    }


def _bayesian_mean_trade_return(
    observed_returns: list[float],
    prior_mean: float = 0.0,
    prior_std: float = 0.018,  # σ=1.8% chosen so P(μ > 3%) = 5% — skeptical that
                                # mean trade return exceeds a meaningful threshold before
                                # seeing data. Derived: 3% / 1.645 ≈ 1.82% ≈ 1.8%
) -> dict:
    """Normal-Normal conjugate posterior for mean trade return."""
    n = len(observed_returns)
    if n < 2:
        return {}
    x_bar = float(np.mean(observed_returns))
    s = float(np.std(observed_returns, ddof=1))
    if s == 0:
        return {}

    prior_prec = 1.0 / prior_std ** 2
    lik_prec = n / s ** 2
    post_prec = prior_prec + lik_prec
    post_mean = (prior_prec * prior_mean + lik_prec * x_bar) / post_prec
    post_std = float(np.sqrt(1.0 / post_prec))

    ci_lo, ci_hi = _scipy_stats.norm.interval(0.95, post_mean, post_std)
    p_positive = float(1 - _scipy_stats.norm.cdf(0.0, post_mean, post_std))
    p_meaningful = float(1 - _scipy_stats.norm.cdf(0.02, post_mean, post_std))

    return {
        "posterior_mean": post_mean,
        "posterior_std": post_std,
        "ci_95": (float(ci_lo), float(ci_hi)),
        "p_edge_positive": p_positive,
        "p_edge_meaningful": p_meaningful,
    }


def _estimate_test_power(
    observed_returns: list[float],
    alpha: float = 0.05,
    min_meaningful_return: float = 0.02,  # 2% per trade = minimum edge worth trading
) -> float | None:
    """
    Power of the one-tailed randomized entry test to detect a 2% mean trade return.

    Uses a literature-grounded hypothesized effect size, NOT the observed d.
    Post-hoc power (using observed d) is circular — it just tracks the p-value.
    """
    n = len(observed_returns)
    if n < 2:
        return None
    s = float(np.std(observed_returns, ddof=1))
    if s == 0:
        return None
    d = min_meaningful_return / s  # hypothesized, not observed
    z_alpha = _scipy_stats.norm.ppf(1 - alpha)
    ncp = np.sqrt(n) * d
    return float(1 - _scipy_stats.norm.cdf(z_alpha - ncp))


def run_randomized_entry_test(
    df: pd.DataFrame,
    best_config: dict,
    symbol: str,
    strategy: str = "tf",
    n_simulations: int = 500,
    optimize_metric: str = "sharpe",
    timeframe: str | None = None,
    ignore_volatility: bool = False,
    precomputed_real_bt: BacktestResult | None = None,
) -> dict:
    """
    Randomized entry significance test: validates whether the entry signal has
    incremental value over random entry timing.

    Keeps ATR exits exactly as-is; randomizes only *when* entries fire, matching
    the actual trade count. This isolates whether the Donchian breakout entry
    signal adds value beyond random timing — and works correctly with few trades
    because each simulation preserves the same N trades as the real strategy.

    Test statistic: mean trade return (trade-level, not bar-level) — avoids
    bar-inflation that would reintroduce the sample-size problem we're solving.

    p-value = fraction of simulations that beat the real mean trade return.
    Passes if p < 0.05.
    """
    import random

    real_bt = precomputed_real_bt or run_backtest(
        symbol, config=best_config, df=df, ignore_volatility=ignore_volatility, timeframe=timeframe
    )
    if real_bt is None:
        return {"error": "Backtest failed on real data"}

    real_trade_count = real_bt.num_trades
    if real_trade_count == 0:
        return {"error": "No trades in real backtest — cannot run randomized entry test"}

    # Trade-level test statistics (mean return + win rate)
    real_trade_returns = [t.pnl_pct for t in real_bt.trades]
    real_metric = float(np.mean(real_trade_returns))
    real_win_rate = float(np.mean([1.0 if r > 0 else 0.0 for r in real_trade_returns]))

    # Determine warmup and right-edge clip
    if strategy == "tf":
        min_warmup = _compute_min_warmup_trend(best_config)
    else:
        min_warmup = _compute_min_warmup(best_config)
    min_warmup = min(min_warmup, len(df) - 2)

    # Right-edge clip: 95th percentile of holding periods from real backtest.
    # Using max() risks a single outlier trade clipping the tradable window excessively;
    # 95th percentile accepts ~5% truncated sim trades in exchange for a larger pool.
    if real_bt.trades:
        bars_held_vals = [t.bars_held for t in real_bt.trades]
        max_hold_bars = max(int(np.percentile(bars_held_vals, 95)), 1)
    else:
        max_hold_bars = best_config.get("backtest", {}).get("max_hold_bars", 60) or 60

    right_edge = len(df) - max_hold_bars - 1
    if right_edge <= min_warmup:
        right_edge = len(df) - 2  # fallback: at least 1 tradable bar

    tradable_dates = [str(df.index[i])[:10] for i in range(min_warmup, right_edge)]
    if len(tradable_dates) == 0:
        return {"error": "No tradable bars after warmup and right-edge clip"}

    logger.info(
        "Randomized entry test: %s | %d trades | %d tradable bars | metric=mean_trade_return",
        symbol, real_trade_count, len(tradable_dates),
    )

    beat_count = 0
    beat_count_winrate = 0
    valid_runs = 0
    skipped_runs = 0

    for _ in range(n_simulations):
        # Resample until we have N unique entry dates
        entries: set[str] = set()
        pool = tradable_dates
        attempts = 0
        max_attempts = real_trade_count * 20
        while len(entries) < real_trade_count and attempts < max_attempts:
            entries.add(random.choice(pool))
            attempts += 1
        if len(entries) < real_trade_count:
            # Pool too small; allow duplicates (with replacement)
            while len(entries) < real_trade_count:
                entries.add(random.choice(pool))

        sim_bt = run_backtest(
            symbol,
            config=best_config,
            df=df,
            ignore_volatility=ignore_volatility,
            timeframe=timeframe,
            forced_entry_dates=entries,
        )
        if sim_bt is None:
            skipped_runs += 1
            continue

        # Guard: sims can only have ≤ real_trade_count trades (collisions reduce count; nothing should increase it)
        assert len(sim_bt.trades) <= real_trade_count, (
            f"Simulation has more trades than real strategy: {len(sim_bt.trades)} > {real_trade_count} — "
            "normal Buy signals may not be suppressed in simulation mode"
        )

        valid_runs += 1
        if sim_bt.trades:
            sim_returns = [t.pnl_pct for t in sim_bt.trades]
            sim_metric = float(np.mean(sim_returns))
            sim_win_rate = float(np.mean([1.0 if r > 0 else 0.0 for r in sim_returns]))
        else:
            sim_metric = 0.0
            sim_win_rate = 0.0
        if sim_metric >= real_metric:
            beat_count += 1
        if sim_win_rate >= real_win_rate:
            beat_count_winrate += 1

    if valid_runs == 0:
        return {"error": "All simulations were skipped or failed"}

    # Warn if many sims failed outright (data issues)
    total_attempted = n_simulations
    skip_pct = skipped_runs / total_attempted * 100
    if skip_pct > 10:
        print(
            f"WARNING: {skip_pct:.0f}% of simulations failed (run_backtest returned None) — "
            "check data quality",
            flush=True,
        )

    p_value = beat_count / valid_runs
    p_value_winrate = beat_count_winrate / valid_runs
    bayes = _bayesian_mean_trade_return(real_trade_returns)
    power = _estimate_test_power(real_trade_returns)
    return {
        "real_metric": real_metric,
        "real_win_rate": real_win_rate,
        "metric_name": "mean_trade_return",
        "p_value": p_value,
        "p_value_winrate": p_value_winrate,
        "n_samples": valid_runs,
        "passed": p_value < 0.05,
        "method": "randomized_entry",
        "trade_count": real_trade_count,
        "bayes": bayes,
        "power": power,
    }


def format_walk_forward_embed(
    results: list[WalkForwardResult],
    ticker: str,
    timeframe: str | None = None,
) -> dict:
    """Format WalkForwardResult list as Discord embed dict."""
    if not results:
        return {
            "title": f"Walk-Forward – {ticker}",
            "description": "Not enough data to run validation. Try a longer period (e.g. 5 years).",
            "color": 0x808080,
            "fields": [],
        }

    avg_return = sum(r.oos_result.total_return for r in results) / len(results)
    avg_outperf = sum(
        r.oos_result.total_return - r.oos_result.buy_hold_return for r in results
    ) / len(results)
    total_trades = sum(r.oos_result.num_trades for r in results)
    avg_win_rate = sum(r.oos_result.win_rate for r in results) / len(results) if results else 0

    color = 0x4CAF50 if avg_return >= 0 else 0xF44336
    tf_line = f"**Strategy:** {timeframe}\n" if timeframe else ""
    desc = (
        f"{tf_line}"
        f"**Mode:** Walk-forward (optimize on past data, test on future)\n"
        f"**Validation rounds:** {len(results)}\n"
        f"**Avg return:** {avg_return:+.1f}%\n"
        f"**Avg vs buy & hold:** {avg_outperf:+.1f}%\n"
        f"**Total trades:** {total_trades}\n"
        f"**Win rate:** {avg_win_rate:.0f}%"
    )

    # Human-readable param labels for beginners
    _param_labels = {
        "rsi_oversold": "RSI oversold",
        "rsi_overbought": "RSI overbought",
        "min_net_score": "Min score",
        "rsi_weight": "RSI weight",
        "trend_weight": "Trend weight",
    }

    fields = []
    if results and results[0].best_params:
        sample = results[0].best_params
        parts = []
        for k, v in list(sample.items())[:5]:
            label = _param_labels.get(k, k.replace("_", " ").title())
            parts.append(f"{label}: {v}")
        params_str = ", ".join(parts)
        if len(sample) > 5:
            params_str += "..."
        fields.append({"name": "Example best settings", "value": params_str, "inline": False})

    return {
        "title": f"Walk-Forward – {ticker}",
        "description": desc,
        "color": color,
        "fields": fields,
    }
