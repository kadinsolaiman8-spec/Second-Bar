"""Run walk-forward optimization from the command line."""
import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from src.data import fetch_vix_series
from src.walk_forward import (
    WalkForwardResult,
    compute_deflated_sharpe,
    compute_pbo,
    compute_probabilistic_sharpe,
    concatenate_oos_bar_returns,
    oos_trade_power_notes_from_results,
    run_bar_permutation_oos_test,
    run_bar_permutation_test,
    run_stationary_bootstrap_oos_bar_returns,
    run_walk_forward_optimization,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WEIGHT_MAP = {"rsi_weight": "rsi", "trend_weight": "trend"}

_TIMEFRAME_INTERVAL = {"Daily": "1d", "1W": "1wk", "1H": "1h"}


def _bars_per_year_for_timeframe(timeframe: str) -> int:
    """Bars per trading year for Sharpe annualization (must match DSR / WFO timeframe)."""
    return 252 if timeframe == "Daily" else 52 if timeframe == "1W" else 1638


def _print_bar_perm_strategy_caveat(strategy: str) -> None:
    """Roadmap-aligned interpretation for bar permutation (secondary test)."""
    if strategy == "tf":
        print(
            "Interpretation (TF): bar-permutation FAIL is often expected (null destroys "
            "autocorrelation). If primary OOS bootstrap passed, treat as pass with caveat; "
            "if both fail, no statistical edge under these tests.",
        )
    else:
        print(
            "Interpretation (MR): bar permutation is a stricter secondary check; "
            "primary vs secondary use different statistics (Sharpe vs profit factor) — "
            "they can disagree.",
        )


def _aggregate_best_params(results: list[WalkForwardResult]) -> dict[str, Any]:
    """Aggregate best params across folds: median for numeric, mode for discrete."""
    if not results:
        return {}
    import statistics
    all_params: dict[str, list[Any]] = {}
    for r in results:
        for k, v in r.best_params.items():
            all_params.setdefault(k, []).append(v)
    out: dict[str, Any] = {}
    for k, vals in all_params.items():
        if not vals:
            continue
        try:
            if all(isinstance(v, (int, float)) for v in vals):
                out[k] = round(statistics.median(vals), 2) if isinstance(vals[0], float) else int(statistics.median(vals))
            else:
                out[k] = max(set(vals), key=vals.count)
        except (statistics.StatisticsError, TypeError):
            out[k] = vals[0]
    return out


def _params_to_profile(agg: dict[str, Any], strategy: str = "mr") -> dict[str, Any]:
    """Convert aggregated params to ticker profile format (indicator_weights, indicators, etc.)."""
    profile: dict[str, Any] = {}
    ind_overrides: dict[str, Any] = {}
    for k, v in agg.items():
        if k in WEIGHT_MAP:
            profile.setdefault("indicator_weights", {})[WEIGHT_MAP[k]] = v
        elif k in ("rsi_oversold", "rsi_overbought", "stoch_oversold", "stoch_overbought",
                  "willr_oversold", "willr_overbought", "rsi_period", "bb_period", "bb_std"):
            ind_overrides[k] = v
        elif k in ("min_net_score", "min_confidence"):
            profile[k] = v
        elif strategy == "tf":
            if k in ("donchian_period", "adx_threshold", "adx_period"):
                profile.setdefault("trend_following", {})[k] = v
            elif k == "atr_multiplier":
                profile.setdefault("backtest", {})["trailing_stop_atr_multiplier"] = v
            elif k == "max_hold_bars":
                profile.setdefault("backtest", {})["max_hold_bars"] = v
    if ind_overrides:
        profile["indicators"] = ind_overrides
    if strategy == "tf":
        profile["strategy"] = "tf"
    return profile


def _save_ticker_profile(ticker: str, profile: dict[str, Any], config_path: Path) -> None:
    """Write profile to config.yaml ticker_profiles section."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("ticker_profiles", {})
    cfg["ticker_profiles"][ticker.upper()] = profile
    with open(config_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run walk-forward optimization")
    parser.add_argument("ticker", nargs="?", default="SPY", help="Ticker symbol (e.g. XLE, SPY)")
    parser.add_argument(
        "--timeframe",
        "-t",
        default="Daily",
        choices=["Daily", "1W", "1H"],
        help="Bar timeframe (default: Daily)",
    )
    parser.add_argument(
        "--period",
        "-p",
        default="10y",
        help="Data period (e.g. 5y, 3y, 1y)",
    )
    parser.add_argument(
        "--save-profile",
        action="store_true",
        help="Save aggregated best params to ticker_profiles in config.yaml",
    )
    parser.add_argument(
        "--strategy",
        "-s",
        default="mr",
        choices=["mr", "tf"],
        help="Strategy: mr (mean-reversion) or tf (trend-following)",
    )
    parser.add_argument(
        "--permutation-test",
        action="store_true",
        help="Run Monte Carlo permutation test after WFO to validate edge (adds ~2-5 min)",
    )
    parser.add_argument(
        "--permutation-samples",
        type=int,
        default=500,
        help="Number of permutation samples (default: 500)",
    )
    parser.add_argument(
        "--in-sample-gate",
        action="store_true",
        help="Run in-sample bar-permutation test before WFO (1000 sims). "
             "Abandon strategy if p >= 0.01 — no edge exists in the data.",
    )
    parser.add_argument(
        "--in-sample-sims",
        type=int,
        default=1000,
        help="Number of bar-permutation simulations for in-sample gate (default: 1000)",
    )
    parser.add_argument(
        "--no-oos-bootstrap",
        action="store_true",
        help="Skip primary stationary bootstrap on concatenated OOS bar-P&L after WFO",
    )
    parser.add_argument(
        "--oos-bootstrap-samples",
        type=int,
        default=500,
        help="Bootstrap draws for primary OOS stationary test (default: 500)",
    )
    parser.add_argument(
        "--oos-bootstrap-seed",
        type=int,
        default=None,
        help="Optional RNG seed for reproducible OOS bootstrap",
    )
    parser.add_argument(
        "--print-resolved-config",
        action="store_true",
        help="Print fully resolved config from get_config_for_ticker (MR) or base+tf (TF) and exit; no fetch/WFO",
    )
    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=1,
        help="Parallel workers for WFO folds and permutation tests. 1=serial (default), -1=all CPUs.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    ticker = args.ticker.lstrip("$")  # Allow $XLE or XLE

    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    wf_cfg = cfg.get("walk_forward", {})
    tf_overrides = wf_cfg.get("timeframe_overrides", {}).get(args.timeframe, {})
    train_bars = tf_overrides.get("train_bars", wf_cfg.get("train_bars", 252))
    test_bars = tf_overrides.get("test_bars", wf_cfg.get("test_bars", 63))
    step_bars = tf_overrides.get("step_bars", wf_cfg.get("step_bars", 63))
    embargo_bars = wf_cfg.get("embargo_bars", 5)
    # TF: tf_train_bars lengthens train (native bar units per timeframe). Root walk_forward
    # tf_train_bars applies only to Daily so weekly/hourly are not inflated by a daily count.
    if args.strategy == "tf":
        tf_only = tf_overrides.get("tf_train_bars")
        if tf_only is None and args.timeframe == "Daily":
            raw_global = wf_cfg.get("tf_train_bars")
            tf_only = int(raw_global) if raw_global is not None else None
        if tf_only is not None:
            train_bars = max(train_bars, int(tf_only))
        elif "train_bars" not in tf_overrides and args.timeframe == "Daily":
            train_bars = max(train_bars, 504)
    if args.strategy == "tf":
        param_grid = tf_overrides.get("trend_following_param_grid", wf_cfg.get("trend_following_param_grid"))
        resolved_cfg = dict(cfg)
        resolved_cfg["strategy"] = "tf"
    else:
        param_grid = tf_overrides.get("param_grid", wf_cfg.get("param_grid"))
        try:
            from src.config_resolver import get_config_for_ticker

            resolved_cfg = get_config_for_ticker(ticker, cfg, timeframe=args.timeframe)
        except Exception:
            resolved_cfg = cfg

    if args.print_resolved_config:
        print(f"=== Resolved config: {ticker} | timeframe={args.timeframe} | strategy={args.strategy} ===")
        yaml.safe_dump(
            resolved_cfg,
            sys.stdout,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        sys.exit(0)

    print(f"Starting WFO: {ticker} | {args.timeframe} | {args.period} | strategy={args.strategy}")

    interval = _TIMEFRAME_INTERVAL.get(args.timeframe, "1d")

    print(
        f"WFO windows: train={train_bars} test={test_bars} step={step_bars} embargo={embargo_bars} "
        f"({args.timeframe}, strategy={args.strategy})",
    )

    # Fetch VIX series for regime-aware backtesting
    vix_series = fetch_vix_series(period=args.period, interval=interval)
    if vix_series is not None:
        print(f"VIX data loaded: {len(vix_series)} bars (regime: vix_ensemble)")
    else:
        print(
            "Regime: SMA-200-only path (VIX series unavailable) — see src/regime.py classify_regime when vix is None",
        )

    if args.in_sample_gate:
        from src.data import fetch_single
        print()
        print(f"=== In-Sample Bar-Permutation Gate ({args.in_sample_sims} sims) ===")
        print("Role: secondary test | statistic: profit factor | null destroys bar autocorrelation")
        if args.strategy == "tf":
            print("TF note: FAIL here is often expected; not the primary OOS validation.")
        df_gate = fetch_single(ticker, period=args.period, interval=interval)
        if df_gate is None or df_gate.empty:
            print("ERROR: Could not fetch data for in-sample gate — skipping gate, proceeding with WFO")
        else:
            gate_result = run_bar_permutation_test(
                df=df_gate,
                best_config=resolved_cfg,
                symbol=ticker,
                strategy=args.strategy,
                n_simulations=args.in_sample_sims,
                timeframe=args.timeframe,
                n_jobs=args.jobs,
            )
            if "error" in gate_result:
                print(f"ERROR: {gate_result['error']} — skipping gate, proceeding with WFO")
            else:
                status = "PASS" if gate_result["passed"] else "FAIL"
                print(f"Real profit factor: {gate_result['real_profit_factor']:.3f}  |  Trades: {gate_result['real_trades']}")
                print(f"p-value: {gate_result['p_value']:.4f}  (n={gate_result['n_simulations']})")
                print(f"Result: {status} — {'in-sample edge detected (p < 0.01), proceeding with WFO' if gate_result['passed'] else 'NO in-sample edge (p >= 0.01) — strategy cannot beat random bars; WFO results will not be meaningful'}")
                if not gate_result["passed"]:
                    print("WARNING: In-sample gate FAILED. Continuing WFO for diagnostic purposes only.")

    results = run_walk_forward_optimization(
        ticker,
        config=resolved_cfg,
        period=args.period,
        interval=interval,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        embargo_bars=embargo_bars,
        param_grid=param_grid,
        optimize_metric=wf_cfg.get("optimize_metric", "sharpe"),
        timeframe=args.timeframe,
        strategy=args.strategy,
        vix_series=vix_series,
        n_jobs=args.jobs,
    )

    if args.save_profile and results:
        agg = _aggregate_best_params(results)
        profile = _params_to_profile(agg, strategy=args.strategy)
        if profile:
            _save_ticker_profile(ticker, profile, config_path)
            print(f"Saved profile for {ticker} to config.yaml ticker_profiles")

    print()
    print('=== WFO Results ===')
    print(f'Folds: {len(results)}')
    if results:
        avg_ret = sum(r.oos_result.total_return for r in results) / len(results)
        avg_out = sum(r.oos_result.total_return - r.oos_result.buy_hold_return for r in results) / len(results)
        fold_trades = [r.oos_result.num_trades for r in results]
        total_trades = sum(fold_trades)
        avg_trades_fold = total_trades / len(results)
        print(f'Avg OOS return: {avg_ret:+.1f}%')
        print(f'Avg vs buy & hold: {avg_out:+.1f}%')
        print(
            f'OOS trades — total: {total_trades}  |  per fold (avg): {avg_trades_fold:.1f}  |  '
            f'by fold: {fold_trades}',
        )
        print()
        low_total = int(wf_cfg.get("low_power_oos_trades_total", 20))
        low_fold = int(wf_cfg.get("low_power_oos_trades_per_fold", 3))
        power_notes = oos_trade_power_notes_from_results(
            results,
            low_total_threshold=low_total,
            low_fold_threshold=low_fold,
        )
        if power_notes:
            print('=== OOS trade-count / power (heuristic) ===')
            print(f'Thresholds: total < {low_total}, any fold < {low_fold}')
            for line in power_notes:
                print(line)
            print()
        for r in results:
            bt = r.oos_result
            print(f'Fold {r.fold_index}: {bt.start_date} to {bt.end_date} | {bt.num_trades} trades | {bt.total_return:+.1f}% | best {r.best_params}')

    # --- Deflated Sharpe Ratio ---
    if results:
        # Combine all OOS bar returns for DSR
        all_oos_returns = []
        for r in results:
            if r.oos_result.bar_returns:
                all_oos_returns.extend(r.oos_result.bar_returns)
        bars_per_year = _bars_per_year_for_timeframe(args.timeframe)
        combined_sharpe = 0.0
        if len(all_oos_returns) >= 2:
            import numpy as np
            rets = np.array(all_oos_returns)
            s = np.std(rets, ddof=1)
            if s > 0:
                combined_sharpe = float(np.mean(rets) / s * np.sqrt(bars_per_year))

        n_combos = 1
        pg = wf_cfg.get("param_grid", {}) if args.strategy != "tf" else wf_cfg.get("trend_following_param_grid", {})
        for v in pg.values():
            n_combos *= len(v) if isinstance(v, list) else 1

        dsr = compute_deflated_sharpe(
            observed_sharpe=combined_sharpe,
            n_trials=n_combos,
            bar_returns=all_oos_returns if all_oos_returns else None,
            bars_per_year=bars_per_year,
        )
        print()
        print("=== Deflated Sharpe Ratio ===")
        print(f"OOS Sharpe (combined): {combined_sharpe:.3f}")
        print(f"Param combos tested: {n_combos}")
        print(f"Expected max Sharpe (noise): {dsr['expected_max_sharpe']:.3f}")
        print(f"DSR z-score: {dsr['dsr']:.3f}")
        print(f"Significant (p < 0.05): {'YES' if dsr['is_significant'] else 'NO'}")

    # --- Probabilistic Sharpe Ratio (complementary to bootstrap) ---
    if results and all_oos_returns and len(all_oos_returns) >= 10:
        psr_result = compute_probabilistic_sharpe(
            bar_returns=all_oos_returns,
            benchmark_sharpe=0.0,
            bars_per_year=bars_per_year,
        )
        print()
        print("=== Probabilistic Sharpe Ratio (assumes IID; bootstrap p-value is primary test) ===")
        print(f"P(true Sharpe > 0): {psr_result['psr']:.1%}")
        print(f"Observed Sharpe (annualized): {psr_result['observed_sharpe_annual']:.3f}")
        print(f"SE(Sharpe): {psr_result['se_sharpe']:.3f}")
        print(f"Skewness: {psr_result['skewness']:.3f}  |  Excess kurtosis: {psr_result['excess_kurtosis']:.3f}")
        print(f"N observations: {psr_result['n_obs']} OOS daily returns")

    optimize_metric = wf_cfg.get("optimize_metric", "sharpe")

    # --- Primary: stationary bootstrap on concatenated OOS bar-P&L ---
    if results and not args.no_oos_bootstrap:
        oos_returns = concatenate_oos_bar_returns(results)
        bars_per_year = _bars_per_year_for_timeframe(args.timeframe)
        print()
        print("=== Stationary bootstrap (primary) — concatenated OOS bar-P&L ===")
        print(f"WFO selection metric: {optimize_metric}")
        print(
            "Primary (OOS stationary bootstrap): Sharpe-based on concatenated OOS bar-P&L — "
            "diagnostic, not the selection objective",
        )
        if len(oos_returns) < 20:
            print("Skipped: insufficient concatenated OOS bars for bootstrap (need >= 20).")
        else:
            seed = args.oos_bootstrap_seed
            boot = run_stationary_bootstrap_oos_bar_returns(
                oos_returns,
                n_samples=args.oos_bootstrap_samples,
                bars_per_year=bars_per_year,
                alpha=0.05,
                seed=seed,
            )
            if "error" in boot:
                print(f"ERROR: {boot['error']}")
            else:
                print(f"Observed OOS Sharpe (annualized): {boot['observed_sharpe']:.3f}")
                print(
                    f"Bootstrap block length: {boot['bootstrap_block_length']:.0f}  |  "
                    f"p-value: {boot['p_value']:.4f}  (n={boot['n_samples']}, one-sided, smoothed)",
                )
                status = "PASS" if boot["passed"] else "FAIL"
                print(f"Result: {status} — {'p < 0.05' if boot['passed'] else 'p >= 0.05'}")
                if "n_effective" in boot:
                    print(
                        f"Effective independent obs: {boot['n_effective']}  "
                        f"(N={len(oos_returns)} / block={boot['bootstrap_block_length']:.0f})"
                    )
                    print(
                        f"Null distribution: median {boot['null_median']:.3f}  |  "
                        f"5th pctl {boot['null_5th']:.3f}  |  95th pctl {boot['null_95th']:.3f}"
                    )
                    print(f"Observed Sharpe rank: {boot['observed_rank_pct']:.0f}th percentile of null")
                print(boot.get("interpretation", ""))
        print(
            "Cross-ticker multiplicity: apply BH/Bonferroni in analysis; see Roadmap Validation Practices.",
        )

    # --- Probability of Backtest Overfitting (suppressed unless show_pbo) ---
    if wf_cfg.get("show_pbo", False) and results and len(results) >= 4:
        fold_sharpes = []
        for r in results:
            if r.all_combo_metrics is not None:
                fold_sharpes.append(r.all_combo_metrics)
        if len(fold_sharpes) >= 4:
            pbo_result = compute_pbo(fold_sharpes)
            print()
            print("=== Probability of Backtest Overfitting (PBO) ===")
            if pbo_result["pbo"] is not None:
                print(f"PBO: {pbo_result['pbo']:.3f}")
                print(f"Interpretation: {pbo_result['interpretation']}")
            else:
                print(f"Could not compute PBO: {pbo_result['interpretation']}")
        else:
            print()
            print(f"=== PBO: insufficient folds with combo data ({len(fold_sharpes)}/4 needed) ===")
    elif results:
        print()
        print(
            "=== PBO: suppressed (walk_forward.show_pbo=false) — IS matrix uses optimize_metric "
            "while compute_pbo assumes Sharpe-like CSCV; see docs/CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md ===",
        )

    if args.permutation_test and results:
        agg = _aggregate_best_params(results)
        profile = _params_to_profile(agg, strategy=args.strategy)
        perm_config = dict(resolved_cfg)
        # Apply aggregated params to perm_config
        ind_overrides = profile.pop("indicators", {})
        perm_config.setdefault("indicators", {})
        perm_config["indicators"] = {**perm_config.get("indicators", {}), **ind_overrides}
        for k, v in profile.items():
            if isinstance(v, dict):
                perm_config.setdefault(k, {})
                perm_config[k] = {**perm_config.get(k, {}), **v}
            else:
                perm_config[k] = v
        profile["indicators"] = ind_overrides  # restore for save_profile

        from src.data import fetch_single
        print()
        print('Fetching full period data...')
        df_full = fetch_single(ticker, period=args.period, interval=interval)
        if df_full is None or df_full.empty:
            print('ERROR: Could not fetch data for permutation test')
        else:
            print(f'=== Bar-Permutation OOS Significance Test ({args.permutation_samples} simulations) ===')
            print(f"WFO selection metric: {optimize_metric}")
            print(
                "Secondary (bar permutation): test statistic = profit factor (full-history backtest); "
                "null destroys bar autocorrelation",
            )
            print(
                "Note: primary (Sharpe on concatenated OOS) vs secondary (profit factor) can conflict — "
                "different statistics; interpret per roadmap (TF caveat).",
            )
            if args.no_oos_bootstrap:
                print(
                    "Warning: --no-oos-bootstrap — primary OOS stationary test was skipped; "
                    "TF reads are easier to misread.",
                )
            print(f'Running {args.permutation_samples} permutations on {len(df_full)} bars...')
            result = run_bar_permutation_oos_test(
                df=df_full,
                best_config=perm_config,
                symbol=ticker,
                strategy=args.strategy,
                n_simulations=args.permutation_samples,
                timeframe=args.timeframe,
                n_jobs=args.jobs,
            )
            if "error" in result:
                print(f'ERROR: {result["error"]}')
            else:
                status = "PASS" if result["passed"] else "FAIL"
                print(f'Method: {result["method"]}  |  Trades: {result["real_trades"]}  |  Win rate: {result["real_win_rate"]:.1%}')
                print(f'Real profit factor: {result["real_profit_factor"]:.3f}')
                print(f'p-value: {result["p_value"]:.3f}  (n={result["n_simulations"]})')
                if result.get("power") is not None:
                    print(f'Test power to detect 2% mean trade return: {result["power"]:.1%}')
                if result.get("bayes"):
                    b = result["bayes"]
                    print(
                        f'Bayesian (skeptical prior): posterior mean {b["posterior_mean"]*100:+.2f}%  '
                        f'95% CI [{b["ci_95"][0]*100:+.1f}%, {b["ci_95"][1]*100:+.1f}%]  '
                        f'P(edge>0%)={b["p_edge_positive"]:.0%}  P(edge>2%)={b["p_edge_meaningful"]:.0%}'
                    )
                print(
                    f'Result: {status} — '
                    f'{"edge is statistically significant (p < 0.05)" if result["passed"] else "edge not significant (p >= 0.05)"}',
                )
                _print_bar_perm_strategy_caveat(args.strategy)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # no-op normally; required for frozen Windows executables
    main()