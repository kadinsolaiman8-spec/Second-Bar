"""Quick verification that the VIX 10y fetch fix works correctly."""

import sys
sys.path.insert(0, ".")

from src.data import fetch_vix_series

def verify(period, min_bars):
    series = fetch_vix_series(period=period, interval="1d")
    assert series is not None, f"VIX {period}: returned None"
    assert len(series) >= min_bars, f"VIX {period}: only {len(series)} bars (expected >= {min_bars})"
    assert series.index.tz is None, f"VIX {period}: index has tz={series.index.tz}, expected tz-naive"
    assert series.dtype == "float64", f"VIX {period}: dtype={series.dtype}, expected float64"
    print(f"VIX {period}: {len(series)} bars | {series.index[0].date()} to {series.index[-1].date()} | last={series.iloc[-1]:.1f}")

if __name__ == "__main__":
    try:
        verify("10y", 2000)
        verify("5y", 1000)
        print("\nAll VIX checks passed.")
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
