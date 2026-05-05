"""Smoke tests for HTTP surface (FastAPI TestClient)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body.get("status") == "ok"


def test_tutorial_payload_route() -> None:
    with TestClient(app) as client:
        response = client.get("/api/quant/tutorial")
        assert response.status_code == 200
        assert "title" in response.json()
