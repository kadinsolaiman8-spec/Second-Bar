"""Smoke tests for HTTP surface (FastAPI TestClient)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body.get("status") == "ok"
        for required_key in (
            "uptime_seconds",
            "ticker_count",
            "market_open",
            "last_scan_at",
            "regime",
            "market_state",
            "market_state_label",
            "circuit_breaker_active",
        ):
            assert required_key in body


def test_tutorial_payload_route() -> None:
    with TestClient(app) as client:
        response = client.get("/api/quant/tutorial")
        assert response.status_code == 200
        assert "title" in response.json()


def test_backtest_quant_strategies_route() -> None:
    with TestClient(app) as client:
        response = client.get("/api/quant/backtest/quant-strategies")
        assert response.status_code == 200
        strategies = response.json().get("strategies")
        assert isinstance(strategies, list) and len(strategies) == 3
        ids = {s["id"] for s in strategies}
        assert ids == {"hybrid", "mean_reversion", "trend_following"}
        for strat in strategies:
            assert isinstance(strat.get("wfo_supported"), bool)
        by_id = {s["id"]: s for s in strategies}
        assert by_id["hybrid"]["wfo_supported"] is False
        assert by_id["mean_reversion"]["wfo_supported"] is True
        assert by_id["trend_following"]["wfo_supported"] is True


def test_wfo_latest_and_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_WFO_RESULTS_DIR", tmp_path)
    slug = "SPY_mr_20260101_test"
    artifact = tmp_path / f"{slug}.json"
    payload = {
        "ticker": "SPY",
        "strategy": "mr",
        "optimize_metric": "sharpe",
        "n_folds": 3,
        "oos_sharpe_headline": 0.5,
        "stationary_bootstrap_pvalue": 0.12,
        "timestamp_iso": "2026-01-01T00:00:00+00:00",
    }
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with TestClient(app) as client:
        latest = client.get("/api/quant/wfo/latest")
        assert latest.status_code == 200
        results = latest.json().get("results") or []
        assert any(r.get("slug") == slug for r in results)
        detail = client.get(f"/api/quant/wfo/results/{slug}")
        assert detail.status_code == 200
        assert detail.json().get("data", {}).get("ticker") == "SPY"
        bad = client.get("/api/quant/wfo/results/x.y")
        assert bad.status_code == 400


def test_backtest_quant_rejects_unknown_strategy() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/quant/backtest",
            json={
                "ticker": "SPY",
                "period": "1mo",
                "interval": "1d",
                "trading_mode": "quant",
                "quant_strategy_id": "not_a_quant_strategy",
            },
        )
        assert response.status_code == 422


def test_backtest_quant_ignores_stray_day_strategy_id() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/quant/backtest",
            json={
                "ticker": "SPY",
                "period": "1y",
                "interval": "1d",
                "trading_mode": "quant",
                "quant_strategy_id": "hybrid",
                "day_strategy_id": "opening_range_breakout",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("trading_mode") == "quant"
        assert body.get("day_strategy_id") is None


def test_backtest_day_strategies_route() -> None:
    with TestClient(app) as client:
        response = client.get("/api/quant/backtest/day-strategies")
        assert response.status_code == 200
        strategies = response.json().get("strategies")
        assert isinstance(strategies, list) and len(strategies) >= 4


def test_backtest_day_trading_rejects_daily_interval() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/quant/backtest",
            json={
                "ticker": "SPY",
                "period": "1mo",
                "interval": "1d",
                "trading_mode": "day_trading",
                "day_strategy_id": "opening_range_breakout",
            },
        )
        assert response.status_code == 422


def test_backtest_day_trading_rejects_unknown_strategy() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/quant/backtest",
            json={
                "ticker": "SPY",
                "period": "1mo",
                "interval": "1h",
                "trading_mode": "day_trading",
                "day_strategy_id": "not_a_real_strategy",
            },
        )
        assert response.status_code == 422


def test_backtest_day_trading_reports_clipped_period_metadata() -> None:
    """Intraday Yahoo caps make long periods map to the same fetch; API should expose that."""
    with TestClient(app) as client:
        response = client.post(
            "/api/quant/backtest",
            json={
                "ticker": "SPY",
                "period": "10y",
                "interval": "15m",
                "trading_mode": "day_trading",
                "day_strategy_id": "opening_range_breakout",
            },
        )
        assert response.status_code == 200
        result = response.json().get("result") or {}
        assert result.get("fetch_period_clipped") is True
        assert result.get("requested_period") == "10y"
        assert result.get("effective_fetch_period") == "60d"


def test_yfinance_effective_caps_long_period_for_hourly() -> None:
    """Hourly bars: Yahoo ~730d — engine and UI rely on this."""
    from quant.data import yfinance_effective_period

    assert yfinance_effective_period("10y", "1h") == "730d"
    assert yfinance_effective_period("5y", "1h") == "730d"
    assert yfinance_effective_period("1y", "1h") == "365d"


def test_scan_latest_endpoint_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/api/scan/latest")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body.get("signals"), list)
        assert "last_scan_at" in body
        assert "last_error" in body
        for required_key in (
            "breadth",
            "best",
            "suggested_watchlist",
            "movers",
            "sectors",
            "stats",
            "squeeze",
            "orb_breakouts",
            "market_state",
        ):
            assert required_key in body


def test_journal_stats_includes_open_trade_count() -> None:
    with TestClient(app) as client:
        response = client.get("/api/journal/stats")
        assert response.status_code == 200
        body = response.json()
        assert "open_trade_count" in body


def test_scan_stream_route_exposed_in_openapi() -> None:
    """Sync TestClient tends to block draining long-lived SSE streams; OpenAPI proves the route."""
    spec = app.openapi()
    assert "/api/scan/stream" in spec.get("paths", {})
    get_op = spec["paths"]["/api/scan/stream"].get("get")
    assert isinstance(get_op, dict)


def test_scan_ticker_bars_rejects_unknown_interval() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/scan/ticker/AAPL/bars", params={"period": "5d", "interval": "9m"})
        assert resp.status_code == 422


def test_scan_ticker_bars_registered_in_openapi() -> None:
    spec = app.openapi()
    assert "/api/scan/ticker/{symbol}/bars" in spec.get("paths", {})
