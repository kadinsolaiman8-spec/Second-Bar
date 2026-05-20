"""FastAPI entry: REST API + static site (no Discord)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

import yaml
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import state  # noqa: E402
from backend.persistence import (  # noqa: E402
    init_db,
    load_journal_snapshot,
    load_scan_snapshot,
    load_ticker_state,
    save_journal_snapshot,
)
from backend.realtime import (  # noqa: E402
    broadcast_scan_event,
    register_sse_subscriber,
    unregister_sse_subscriber,
)
from backend.market_session import build_market_session  # noqa: E402
from backend.scan_bundle import (  # noqa: E402
    build_latest_scan_bundle,
    enrich_ticker_detail_state,
)
from backend.scan_publish import finalize_scan_publish_async  # noqa: E402
from backend.scheduler import build_scheduler, run_daily_reset, run_scheduled_scan  # noqa: E402
from quant.backtest import BacktestResult, run_backtest  # noqa: E402
from quant.data import set_provider_config, yfinance_effective_period  # noqa: E402
from quant.intraday_backtest_strategies import (  # noqa: E402
    ALLOWED_DAY_STRATEGY_IDS,
    DAY_TRADING_STRATEGY_LABELS,
    INTRADAY_INTERVALS,
)
from quant.quant_strategies import (  # noqa: E402
    ALLOWED_QUANT_STRATEGY_IDS,
    QUANT_STRATEGY_LABELS,
    apply_quant_strategy_to_config,
    wfo_supported_for_quant_strategy_api_id,
)
from quant.config_resolver import get_config_for_ticker  # noqa: E402
from quant.tutorial import build_tutorial_payload  # noqa: E402
from scanner_core import journal as journal_mod  # noqa: E402
from scanner_core import scanner as scanner_mod  # noqa: E402
from scanner_core.config import SCAN_INTERVAL_SECONDS  # noqa: E402
from scanner_core.market_state import get_current_market_state, get_state_label  # noqa: E402
from scanner_core.watchlist import ALL_STOCKS  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_APP_BOOT_MONO = time.perf_counter()

CONFIG: dict = {}
CONFIG_PATH = ROOT / "config.yaml"
_sched = None

_ALLOWED_BAR_PERIODS = frozenset({"5d", "7d", "14d", "28d"})
_ALLOWED_BAR_INTERVALS = frozenset({"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "1wk"})

_WFO_RESULTS_DIR = ROOT / "data" / "wfo-results"
_WFO_FILE_SIZE_LIMIT = 512 * 1024  # 512 KB
_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_HEARTBEAT_SENTINEL = ": sse-heartbeat"


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


def _ohlc_rows_from_dataframe(chart_df: pd.DataFrame) -> list[dict[str, float | int]]:
    candles_out: list[dict[str, float | int]] = []
    df_work = chart_df.sort_index()
    for time_index, candle_row in df_work.iterrows():
        ts_candidate = pd.Timestamp(time_index)
        if pd.isna(ts_candidate):
            continue
        utc_ts = (
            ts_candidate.tz_convert(timezone.utc)
            if ts_candidate.tzinfo is not None
            else ts_candidate.tz_localize("UTC")
        )
        unix_t = int(utc_ts.timestamp())
        candles_out.append(
            {
                "time": unix_t,
                "open": float(candle_row["Open"]),
                "high": float(candle_row["High"]),
                "low": float(candle_row["Low"]),
                "close": float(candle_row["Close"]),
                "volume": int(float(candle_row["Volume"]))
                if pd.notna(candle_row.get("Volume"))
                else 0,
            }
        )
    return candles_out


def backtest_to_dict(result: BacktestResult) -> dict:
    out = {
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
    if result.requested_data_period is not None:
        out["requested_period"] = result.requested_data_period
    if result.effective_data_period is not None:
        out["effective_fetch_period"] = result.effective_data_period
    out["fetch_period_clipped"] = bool(
        result.requested_data_period is not None
        and result.effective_data_period is not None
        and result.requested_data_period != result.effective_data_period
    )
    return out


class BacktestBody(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    period: str = "1y"
    interval: str = "1d"
    trading_mode: Literal["quant", "day_trading"] = "quant"
    day_strategy_id: str | None = None
    quant_strategy_id: str | None = None


class ScanBody(BaseModel):
    tickers: list[str] | None = None


class JournalOpenBody(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    direction: str = Field(default="BUY", max_length=8)
    strategy: str = Field(default="Manual", max_length=64)
    entry_price: float = Field(...)
    stop_loss: float = Field(...)
    tp1: float = Field(...)
    tp2: float = Field(...)
    score: int = Field(default=0)
    stock_type: str = Field(default="stable", max_length=16)


class JournalCloseBody(BaseModel):
    exit_price: float = Field(...)
    exit_reason: str = Field(default="manual", max_length=64)
    bars_held: int = Field(default=0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sched
    load_merged_config()
    if isinstance(CONFIG, dict):
        set_provider_config(CONFIG)
    init_db()
    loaded_journal = load_journal_snapshot()
    loaded_scan = load_scan_snapshot()
    loaded_ticker_state = load_ticker_state()
    if loaded_ticker_state:
        scanner_mod.ticker_state.update(loaded_ticker_state)
    logger.info(
        "Persistence: journal=%s last_scan=%s ticker_state_rows=%s",
        loaded_journal,
        loaded_scan,
        len(loaded_ticker_state),
    )
    try:
        await scanner_mod.refresh_spy_cache()
    except Exception as exc:
        logger.warning(
            "SPY cache warmup failed — starting without cache: %s",
            exc,
            exc_info=False,
        )
    _sched = build_scheduler()
    _sched.add_job(
        run_scheduled_scan,
        "interval",
        seconds=max(30, int(SCAN_INTERVAL_SECONDS)),
        id="scan_cycle",
        max_instances=1,
        replace_existing=True,
    )
    _sched.add_job(
        run_daily_reset,
        CronTrigger(hour=9, minute=25, timezone="America/New_York"),
        id="daily_reset",
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


@app.get("/api/market/session")
def market_session() -> dict:
    return build_market_session()


@app.get("/api/health")
def health() -> dict:
    running = bool(_sched and getattr(_sched, "running", False))
    regime_blob = scanner_mod.get_current_regime()
    market_state_identifier = get_current_market_state()
    scan_statistics = scanner_mod.get_scan_stats()
    snapshot_blob = state.snapshot()
    uptime_seconds_value = round(time.perf_counter() - _APP_BOOT_MONO, 3)
    return {
        "status": "ok",
        "scheduler_running": running,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "config_loaded": bool(CONFIG),
        "ticker_count": int(scan_statistics.get("total_cached", 0)),
        "market_open": bool(scanner_mod.is_market_open()),
        "last_scan_at": snapshot_blob.get("last_scan_at"),
        "regime": regime_blob.get("label"),
        "regime_quality": scanner_mod.get_regime_quality(),
        "market_state": market_state_identifier,
        "market_state_label": get_state_label(market_state_identifier),
        "circuit_breaker_active": scanner_mod.is_circuit_breaker_active(),
        "circuit_resume_time": scanner_mod.get_circuit_breaker_resume_time(),
        "uptime_seconds": uptime_seconds_value,
    }


@app.get("/api/quant/tutorial")
def tutorial() -> dict:
    return build_tutorial_payload()


@app.get("/api/quant/backtest/quant-strategies")
def backtest_quant_strategies() -> dict:
    ordered = [
        {
            "id": k,
            "label": QUANT_STRATEGY_LABELS[k],
            "wfo_supported": wfo_supported_for_quant_strategy_api_id(k),
        }
        for k in ("hybrid", "mean_reversion", "trend_following")
    ]
    return {"strategies": ordered}


@app.get("/api/quant/backtest/day-strategies")
def backtest_day_strategies() -> dict:
    ordered = [
        {"id": k, "label": DAY_TRADING_STRATEGY_LABELS[k]}
        for k in (
            "opening_range_breakout",
            "vwap_mean_reversion",
            "momentum_pullback",
            "range_breakout",
        )
    ]
    return {"strategies": ordered}


@app.get("/api/quant/wfo/latest")
def wfo_latest() -> dict:
    _WFO_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_files = sorted(
        _WFO_RESULTS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    results = []
    for path in json_files:
        slug = path.stem
        stat = path.stat()
        parts = re.split(r"[-_]", slug, maxsplit=1)
        ticker = parts[0].upper() if parts and parts[0].isalpha() else None
        ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        results.append({"slug": slug, "ticker": ticker, "ts": ts, "size_bytes": stat.st_size})
    return {"results": results}


@app.get("/api/quant/wfo/results/{slug}")
def wfo_result(slug: str) -> dict:
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid slug.")
    _WFO_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = _WFO_RESULTS_DIR / f"{slug}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Result not found.")
    size = json_path.stat().st_size
    if size > _WFO_FILE_SIZE_LIMIT:
        raise HTTPException(status_code=413, detail="Result exceeds size limit.")
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("WFO result read failed for %s: %s", slug, exc)
        raise HTTPException(status_code=500, detail="Could not read result.") from exc
    return {"slug": slug, "data": data}


@app.post("/api/quant/backtest")
def api_backtest(body: BacktestBody) -> dict:
    load_merged_config()
    sym = body.ticker.upper().strip()
    t_cfg = get_config_for_ticker(sym, CONFIG)
    day_strategy_for_run: str | None = None
    qid = ""
    if body.trading_mode == "day_trading":
        if body.interval not in INTRADAY_INTERVALS:
            raise HTTPException(
                status_code=422,
                detail="Day-trading mode requires an intraday interval (5m, 15m, 30m, 60m, 1h).",
            )
        sid = (body.day_strategy_id or "").strip()
        if sid not in ALLOWED_DAY_STRATEGY_IDS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid day_strategy_id. Expected one of: {sorted(ALLOWED_DAY_STRATEGY_IDS)}.",
            )
        day_strategy_for_run = sid
    else:
        qid = (body.quant_strategy_id or "").strip()
        if qid:
            if qid not in ALLOWED_QUANT_STRATEGY_IDS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid quant_strategy_id. Expected one of: {sorted(ALLOWED_QUANT_STRATEGY_IDS)}.",
                )
            try:
                t_cfg = apply_quant_strategy_to_config(t_cfg, qid)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        period_raw = body.period.strip().lower()
        period_for_fetch = yfinance_effective_period(period_raw, body.interval.strip().lower())
        result = run_backtest(
            sym,
            period=period_for_fetch,
            interval=body.interval,
            config=t_cfg,
            timeframe=None,
            trading_mode=body.trading_mode,
            day_strategy_id=day_strategy_for_run,
            requested_period_display=period_raw,
        )
    except Exception as exc:
        logger.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=422, detail="Insufficient data for backtest")
    out = {"result": backtest_to_dict(result), "trading_mode": body.trading_mode}
    if body.trading_mode == "day_trading":
        out["day_strategy_id"] = day_strategy_for_run
        out["quant_strategy_id"] = None
    else:
        out["day_strategy_id"] = None
        out["quant_strategy_id"] = qid or None
    return out


@app.post("/api/scan/run")
async def scan_run(body: ScanBody | None = None) -> dict:
    tickers = list(ALL_STOCKS)
    if body and body.tickers:
        tickers = [t.upper().strip() for t in body.tickers if t.strip()]
        if not tickers:
            raise HTTPException(status_code=400, detail="tickers list empty")
    ts = datetime.now(timezone.utc).isoformat()

    async def _on_scan_progress(progress: dict[str, object]) -> None:
        await broadcast_scan_event({"type": "scan_progress", "data": progress})

    async with state.scan_lock:
        try:
            signals = await scanner_mod.scan_all_tickers(
                tickers,
                alert_channel=None,
                on_scan_progress=_on_scan_progress,
            )
            state.set_scan_result(signals, ts_iso=ts, error=None)
        except Exception as exc:
            logger.exception("Manual scan failed")
            state.set_scan_error(ts_iso=ts, error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            await finalize_scan_publish_async()
    return {"count": len(state.snapshot()["signals"]), "last_scan_at": ts}


@app.get("/api/scan/stream")
async def scan_stream():
    async def event_generator():
        sse_queue = await register_sse_subscriber()

        async def _heartbeat_pump() -> None:
            while True:
                await asyncio.sleep(15)
                try:
                    sse_queue.put_nowait(_HEARTBEAT_SENTINEL)
                except asyncio.QueueFull:
                    pass

        heartbeat_task = asyncio.create_task(_heartbeat_pump())
        try:
            opener = {"type": "snapshot", "data": build_latest_scan_bundle()}
            yield "data: " + json.dumps(opener, default=str) + "\n\n"
            while True:
                message_line = await sse_queue.get()
                if message_line == _HEARTBEAT_SENTINEL:
                    yield ": sse-heartbeat\n\n"
                else:
                    yield "data: " + message_line + "\n\n"
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await unregister_sse_subscriber(sse_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/scan/latest")
def scan_latest() -> dict:
    return build_latest_scan_bundle()


@app.get("/api/scan/watchlist")
def scan_watchlist() -> dict:
    return {"stocks": state.json_safe(scanner_mod.get_all_watchlist_stocks())}


@app.get("/api/scan/best")
def scan_best(limit: int = Query(default=8, ge=1, le=80)) -> dict:
    return {"best": state.json_safe(scanner_mod.get_best_stocks(min_score=0, limit=limit))}


@app.get("/api/scan/breadth")
def scan_breadth() -> dict:
    return state.json_safe(scanner_mod.get_market_breadth())


@app.get("/api/scan/movers")
def scan_movers(n: int = Query(default=12, ge=1, le=100)) -> dict:
    return {"movers": state.json_safe(scanner_mod.get_movers(n))}


@app.get("/api/scan/leaders")
def scan_leaders(
    vs_spy_min: float = Query(default=0.005), n: int = Query(default=8, ge=1, le=80)
) -> dict:
    return {"leaders": state.json_safe(scanner_mod.get_leaders(vs_spy_min=vs_spy_min, n=n))}


@app.get("/api/scan/laggards")
def scan_laggards(n: int = Query(default=8, ge=1, le=80)) -> dict:
    return {"laggards": state.json_safe(scanner_mod.get_laggards(n=n))}


@app.get("/api/scan/squeeze")
def scan_squeeze() -> dict:
    return {"squeeze": state.json_safe(scanner_mod.get_squeeze_stocks())}


@app.get("/api/scan/sectors")
def scan_sectors() -> dict:
    return state.json_safe(scanner_mod.get_sector_rotation())


@app.get("/api/scan/orb")
def scan_orb() -> dict:
    return {"orb_breakouts": state.json_safe(scanner_mod.get_orb_breakouts())}


@app.get("/api/scan/stats")
def scan_stats() -> dict:
    return state.json_safe(scanner_mod.get_scan_stats())


@app.get("/api/scan/strategies")
def scan_strategies(
    strategy_name: str | None = Query(default=None),
) -> dict:
    return {
        "hits": state.json_safe(
            scanner_mod.get_strategy_hits(strategy_name=strategy_name)
        )
    }


@app.get("/api/scan/volatile")
def scan_volatile(n: int = Query(default=10, ge=1, le=100)) -> dict:
    return {"volatile": state.json_safe(scanner_mod.get_volatile_by_atr(n=n))}


@app.get("/api/scan/ticker/{symbol}")
def scan_ticker_detail(symbol: str) -> dict:
    symbol_upper = symbol.upper().strip()
    ticker_blob = scanner_mod.get_ticker_state(symbol_upper)
    if not ticker_blob:
        raise HTTPException(status_code=404, detail=f"Unknown or uncached ticker: {symbol_upper}")
    merged_state = dict(ticker_blob)
    for sig in state.snapshot().get("signals") or []:
        if not isinstance(sig, dict):
            continue
        if str(sig.get("ticker", "")).upper() != symbol_upper:
            continue
        for merge_key, merge_val in sig.items():
            if merge_key == "ticker":
                continue
            merged_state[merge_key] = merge_val
        break
    snap = state.snapshot()
    market_code = get_current_market_state()
    market_state_blob = {"code": market_code, "label": get_state_label(market_code)}
    context = {
        "last_scan_at": snap.get("last_scan_at"),
        "last_error": snap.get("last_error"),
        "market_state": market_state_blob,
        "regime": scanner_mod.get_current_regime(),
    }
    merged_state = enrich_ticker_detail_state(
        merged_state,
        symbol_upper,
        snap.get("signals") or [],
        market_state_blob,
    )
    return {
        "ticker": symbol_upper,
        "state": state.json_safe(merged_state),
        "context": state.json_safe(context),
    }


@app.get("/api/scan/ticker/{symbol}/bars")
def scan_ticker_ohlc_bars(
    symbol: str,
    period: str = Query(default="5d", min_length=2, max_length=12),
    interval: str = Query(default="15m", min_length=1, max_length=8),
) -> dict:
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="Symbol required.")
    normalized_period = period.strip().lower()
    normalized_interval = interval.strip().lower()
    if normalized_period not in _ALLOWED_BAR_PERIODS:
        raise HTTPException(
            status_code=422,
            detail="Pick a supported period.",
        )
    if normalized_interval not in _ALLOWED_BAR_INTERVALS:
        raise HTTPException(
            status_code=422,
            detail="Pick a supported interval.",
        )
    effective_period = yfinance_effective_period(normalized_period, normalized_interval)
    try:
        fetched = scanner_mod.fetch_ticker_data(sym, period=effective_period, interval=normalized_interval)
    except Exception as exc:
        logger.warning("Bars fetch raised for %s: %s", sym, exc)
        raise HTTPException(status_code=503, detail="Price feed unavailable.") from exc
    if fetched is None or getattr(fetched, "empty", True):
        raise HTTPException(status_code=404, detail=f"No OHLC bars returned for {sym}.")
    try:
        rows = _ohlc_rows_from_dataframe(fetched)
    except Exception as exc:
        logger.warning("Bars normalization failed for %s: %s", sym, exc)
        raise HTTPException(status_code=500, detail="Could not normalize bar data.") from exc
    if not rows:
        raise HTTPException(status_code=404, detail=f"No valid bars after normalization for {sym}.")
    return {
        "ticker": sym,
        "period_requested": normalized_period,
        "period_effective": effective_period,
        "interval": normalized_interval,
        "candles": state.json_safe(rows),
    }


@app.get("/api/scan/compare")
def scan_compare(a: str = Query(...), b: str = Query(...)) -> dict:
    return state.json_safe(
        scanner_mod.compare_tickers(a.strip().upper(), b.strip().upper())
    )


@app.get("/api/scan/halts")
def scan_halts() -> dict:
    return {"halts": state.json_safe(scanner_mod.get_halts())}


@app.get("/api/journal/stats")
def journal_stats() -> dict:
    stats_plain = journal_mod.get_today_stats()
    enriched = dict(stats_plain)
    enriched["open_trade_count"] = journal_mod.get_open_trade_count()
    return state.json_safe(enriched)


@app.get("/api/journal/trades/open")
def journal_open_trades() -> dict:
    blob = journal_mod.get_open_trades()
    return {"open_trades": state.json_safe(blob)}


@app.get("/api/journal/trades/recent")
def journal_recent_trades(limit: int = Query(default=20, ge=1, le=500)) -> dict:
    return {"recent": state.json_safe(journal_mod.get_recent_trades(n=limit))}


@app.post("/api/journal/trades/open")
def journal_trade_open_endpoint(body: JournalOpenBody) -> dict:
    trade_identifier = journal_mod.open_trade(
        body.ticker,
        body.direction,
        body.strategy,
        body.entry_price,
        body.stop_loss,
        body.tp1,
        body.tp2,
        body.score,
        body.stock_type,
    )
    save_journal_snapshot()
    return {"trade_id": trade_identifier}


@app.post("/api/journal/trades/{trade_id}/close")
def journal_trade_close_endpoint(trade_id: str, body: JournalCloseBody) -> dict:
    done = journal_mod.close_trade(
        trade_id.strip(),
        body.exit_price,
        body.exit_reason,
        bars_held=body.bars_held,
    )
    save_journal_snapshot()
    if isinstance(done, dict) and done.get("error"):
        raise HTTPException(status_code=404, detail=done["error"])
    return {"closed": state.json_safe(done)}


@app.get("/api/journal/strategies")
def journal_strategy_breakdown() -> dict:
    return state.json_safe(journal_mod.get_strategy_breakdown())


@app.get("/api/journal/equity")
def journal_equity_curve() -> dict:
    return {"curve": state.json_safe(journal_mod.equity_curve_series())}


static_dir = ROOT / "site"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="site")
