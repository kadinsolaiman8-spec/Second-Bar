"""FastAPI entry: REST API + static site (no Discord)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import state  # noqa: E402
from backend.scheduler import build_scheduler, run_scheduled_scan  # noqa: E402
from quant.backtest import BacktestResult, run_backtest  # noqa: E402
from quant.config_resolver import get_config_for_ticker  # noqa: E402
from quant.tutorial import build_tutorial_payload  # noqa: E402
from scanner_core import scanner as scanner_mod  # noqa: E402
from scanner_core.config import SCAN_INTERVAL_SECONDS  # noqa: E402
from scanner_core.watchlist import ALL_STOCKS  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG: dict = {}
CONFIG_PATH = ROOT / "config.yaml"
_sched = None


def load_merged_config() -> dict:
    global CONFIG
    if not CONFIG_PATH.exists():
        CONFIG = {}
        return CONFIG
    with open(CONFIG_PATH, encoding="utf-8") as f:
        CONFIG = yaml.safe_load(f)
    profiles_path = ROOT / "data" / "ticker_profiles.yaml"
    if profiles_path.exists():
        try:
            with open(profiles_path, encoding="utf-8") as f:
                external = yaml.safe_load(f)
            if isinstance(external, dict) and "ticker_profiles" in external:
                ext_profiles = external.get("ticker_profiles") or {}
                cfg_profiles = CONFIG.get("ticker_profiles") or {}
                CONFIG["ticker_profiles"] = {**ext_profiles, **cfg_profiles}
        except Exception as exc:
            logger.warning("Could not load ticker_profiles.yaml: %s", exc)
    return CONFIG


def backtest_to_dict(result: BacktestResult) -> dict:
    return {
        "symbol": result.symbol,
        "total_return": result.total_return,
        "buy_hold_return": result.buy_hold_return,
        "num_trades": result.num_trades,
        "win_rate": result.win_rate,
        "max_drawdown": result.max_drawdown,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "profit_factor": result.profit_factor,
        "forced_skipped": result.forced_skipped,
        "trades": [
            {
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "side": t.side,
                "pnl_pct": t.pnl_pct,
                "pnl_abs": t.pnl_abs,
                "bars_held": t.bars_held,
            }
            for t in result.trades[:100]
        ],
    }


class BacktestBody(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    period: str = "1y"
    interval: str = "1d"


class ScanBody(BaseModel):
    tickers: list[str] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sched
    load_merged_config()
    _sched = build_scheduler()
    _sched.add_job(
        run_scheduled_scan,
        "interval",
        seconds=max(30, int(SCAN_INTERVAL_SECONDS)),
        id="scan_cycle",
        max_instances=1,
        replace_existing=True,
    )
    _sched.start()
    yield
    _sched.shutdown(wait=False)


app = FastAPI(title="Trading unified web backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    running = bool(_sched and getattr(_sched, "running", False))
    return {
        "status": "ok",
        "scheduler_running": running,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "config_loaded": bool(CONFIG),
    }


@app.get("/api/quant/tutorial")
def tutorial() -> dict:
    return build_tutorial_payload()


@app.post("/api/quant/backtest")
def api_backtest(body: BacktestBody) -> dict:
    load_merged_config()
    sym = body.ticker.upper().strip()
    t_cfg = get_config_for_ticker(sym, CONFIG)
    try:
        result = run_backtest(
            sym,
            period=body.period,
            interval=body.interval,
            config=t_cfg,
            timeframe=None,
        )
    except Exception as exc:
        logger.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=422, detail="Insufficient data for backtest")
    return {"result": backtest_to_dict(result)}


@app.post("/api/scan/run")
async def scan_run(body: ScanBody | None = None) -> dict:
    tickers = list(ALL_STOCKS)
    if body and body.tickers:
        tickers = [t.upper().strip() for t in body.tickers if t.strip()]
        if not tickers:
            raise HTTPException(status_code=400, detail="tickers list empty")
    ts = datetime.now(timezone.utc).isoformat()
    async with state.scan_lock:
        try:
            signals = await scanner_mod.scan_all_tickers(tickers, alert_channel=None)
            state.set_scan_result(signals, ts_iso=ts, error=None)
        except Exception as exc:
            logger.exception("Manual scan failed")
            state.set_scan_result([], ts_iso=ts, error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"count": len(state.snapshot()["signals"]), "last_scan_at": ts}


@app.get("/api/scan/latest")
def scan_latest() -> dict:
    snap = state.snapshot()
    hist = state.json_safe(scanner_mod.scan_history[-25:])
    regime = scanner_mod.get_current_regime()
    return {
        **snap,
        "scan_history": hist,
        "regime": state.json_safe(regime),
        "circuit_breaker_active": scanner_mod.is_circuit_breaker_active(),
        "circuit_resume_time": scanner_mod.get_circuit_breaker_resume_time(),
    }


static_dir = ROOT / "site"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="site")
