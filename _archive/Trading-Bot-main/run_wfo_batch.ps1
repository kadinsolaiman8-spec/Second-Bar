# WFO Batch — Run all tickers sequentially with in-sample gate + permutation test.
# Usage: .\run_wfo_batch.ps1
# Each run: in-sample gate (1000 sims) + WFO + bar-permutation OOS test (500 sims).
#
# Wall time (realistic, single-threaded Python, ~10y daily ~2500 bars):
#   Each full-history backtest in the gate/perm loops is often ~2–3+ minutes on a typical PC.
#   Rough order per ticker: 1000 gate + 500 OOS perm = 1500 full backtests → often ~2–3+ DAYS
#   for one MR ticker (SPY/QQQ/IWM), plus WFO grid work. TF tickers (GLD/XLE) have a small
#   param grid but the same 1000+500 full-history passes — still often many hours each.
# Full five-ticker batch: plan for multi-day runs, not ~90 minutes. See docs/wfo_batches/
# BEGINNER_EXACT_STEPS.md ("Runtime") for faster exploratory flag combinations.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$commands = @(
    @{ Ticker = "SPY"; Strategy = "tf" },
    @{ Ticker = "QQQ"; Strategy = "tf" },
    @{ Ticker = "IWM"; Strategy = "tf" },
    @{ Ticker = "GLD"; Strategy = "tf" },
    @{ Ticker = "XLE"; Strategy = "tf" }
)

foreach ($cmd in $commands) {
    Write-Host "`n=== WFO $($cmd.Ticker) ($($cmd.Strategy)) ===" -ForegroundColor Cyan
    python -m src.run_wfo $cmd.Ticker --strategy $cmd.Strategy --period 10y --in-sample-gate --permutation-test --jobs 16
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WFO $($cmd.Ticker) failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host "`nAll WFO runs completed." -ForegroundColor Green
