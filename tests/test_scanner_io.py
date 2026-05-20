"""Unit tests for scanner I/O batching and scan-cycle cache."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from scanner_core import scanner as scanner_module


def _sample_ohlcv(rows: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2026-05-19 09:30", periods=rows, freq="5min", tz="US/Eastern")
    return pd.DataFrame(
        {
            "Open": [100.0] * rows,
            "High": [101.0] * rows,
            "Low": [99.0] * rows,
            "Close": [100.5] * rows,
            "Volume": [1_000_000] * rows,
        },
        index=idx,
    )


def test_extract_batch_ticker_frame_multi_index() -> None:
    aapl = _sample_ohlcv()
    msft = _sample_ohlcv()
    batch_df = pd.concat({"AAPL": aapl, "MSFT": msft}, axis=1)
    out = scanner_module._extract_batch_ticker_frame(batch_df, "AAPL", ["AAPL", "MSFT"])
    assert out is not None
    assert len(out) == len(aapl)


def test_extract_batch_ticker_frame_single_ticker() -> None:
    frame = _sample_ohlcv()
    out = scanner_module._extract_batch_ticker_frame(frame, "AAPL", ["AAPL"])
    assert out is not None
    assert "Close" in out.columns


def test_fetch_ohlcv_uses_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = _sample_ohlcv()
    scanner_module._scan_ohlcv_cache = {"ZZZ": {"5m": cached}}
    called = {"download": 0}

    def _fake_download(*_args, **_kwargs):
        called["download"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(scanner_module.yf, "download", _fake_download)
    result = scanner_module._fetch_ohlcv("ZZZ", "5m")
    assert result is not None
    assert len(result) == len(cached)
    assert called["download"] == 0
    scanner_module.clear_scan_ohlcv_cache()


def test_build_scan_ohlcv_cache_sync_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _sample_ohlcv()

    def _fake_download(tickers, **_kwargs):
        if isinstance(tickers, str):
            tickers = [tickers]
        if len(tickers) == 1:
            return frame.copy()
        return pd.concat({t: frame.copy() for t in tickers}, axis=1)

    monkeypatch.setattr(scanner_module.yf, "download", _fake_download)
    scanner_module._delisted_cache.discard("AAA")
    scanner_module._delisted_cache.discard("BBB")
    scanner_module._fetch_fail_count.pop("AAA", None)
    scanner_module._fetch_fail_count.pop("BBB", None)

    cache = scanner_module._build_scan_ohlcv_cache_sync(["AAA", "BBB"], ["5m"])
    assert "5m" in cache["AAA"]
    assert cache["AAA"]["5m"] is not None
    assert "5m" in cache["BBB"]


def test_prime_and_clear_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _sample_ohlcv()

    def _fake_download(tickers, **_kwargs):
        if isinstance(tickers, str):
            tickers = [tickers]
        if len(tickers) == 1:
            return frame.copy()
        return pd.concat({t: frame.copy() for t in tickers}, axis=1)

    monkeypatch.setattr(scanner_module.yf, "download", _fake_download)

    async def _run() -> None:
        await scanner_module.prime_scan_ohlcv_cache(["XYZ"], ["5m"])
        assert scanner_module._scan_ohlcv_cache is not None
        assert scanner_module._scan_ohlcv_cache["XYZ"]["5m"] is not None
        scanner_module.clear_scan_ohlcv_cache()
        assert scanner_module._scan_ohlcv_cache is None

    asyncio.run(_run())
