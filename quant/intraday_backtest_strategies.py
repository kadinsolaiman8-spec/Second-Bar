"""
Intraday / day-trading rule sets for `run_backtest` when `trading_mode=day_trading`.

Rule definitions follow common practitioner descriptions (ORB, VWAP stretch fade,
pullback to short EMA in a trend, Donchian-style range breakout). They share the
same event-driven fill model as the quant engine (signal on bar i → fill next open).
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from quant.indicators import compute_atr
from quant.signals import Signal
from scanner_core.session_calendar import is_nyse_session

DAY_TRADING_STRATEGY_LABELS: dict[str, str] = {
    "opening_range_breakout": "Opening range breakout (first 2 session bars)",
    "vwap_mean_reversion": "VWAP mean reversion (long fade)",
    "momentum_pullback": "Momentum pullback (9 EMA reclaim)",
    "range_breakout": "Range breakout (Donchian-style)",
}

ALLOWED_DAY_STRATEGY_IDS: frozenset[str] = frozenset(DAY_TRADING_STRATEGY_LABELS.keys())

INTRADAY_INTERVALS: frozenset[str] = frozenset({"5m", "15m", "30m", "60m", "1h"})


def precompute_intraday(df: pd.DataFrame, strategy_id: str) -> dict:
    """Build arrays aligned to df rows for fast per-bar evaluation."""
    n = len(df)
    high = df["High"].to_numpy(dtype=float, copy=False)
    low = df["Low"].to_numpy(dtype=float, copy=False)
    close = df["Close"].to_numpy(dtype=float, copy=False)
    vol = df["Volume"].to_numpy(dtype=float, copy=False)

    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        dates = idx.strftime("%Y-%m-%d").to_numpy()
    else:
        dt_idx = pd.to_datetime(pd.Index(idx), errors="coerce")
        dates = dt_idx.strftime("%Y-%m-%d").to_numpy()

    # Build the set of valid NYSE session dates to filter out any non-trading day bars
    # (e.g. holidays or weekend artefacts that may appear in some data sources).
    # Falls back to treating all dates as valid if the calendar lookup is unavailable.
    _unique_dates = np.unique(dates)
    _valid_sessions: set[str] = set()
    for _d in _unique_dates:
        try:
            _date_obj = datetime.date.fromisoformat(str(_d))
            if is_nyse_session(_date_obj):
                _valid_sessions.add(str(_d))
        except (ValueError, TypeError):
            pass
    # Graceful degradation: if the set is empty (calendar unavailable), treat all dates as valid.
    if not _valid_sessions:
        _valid_sessions = {str(_d) for _d in _unique_dates}

    bar_of_day = np.zeros(n, dtype=np.int32)
    for i in range(1, n):
        bar_of_day[i] = bar_of_day[i - 1] + 1 if dates[i] == dates[i - 1] else 0

    typical = (high + low + close) / 3.0
    vwap = np.zeros(n, dtype=float)
    cum_tp_v = 0.0
    cum_v = 0.0
    last_date: str | None = None
    for i in range(n):
        d_i = str(dates[i])
        if d_i != last_date:
            # Only reset cumulative VWAP state at the start of a valid session.
            # Non-session bars carry forward the previous session's VWAP value.
            if d_i in _valid_sessions:
                cum_tp_v = 0.0
                cum_v = 0.0
            last_date = d_i
        if d_i in _valid_sessions:
            cum_tp_v += typical[i] * vol[i]
            cum_v += vol[i]
        vwap[i] = cum_tp_v / cum_v if cum_v > 0 else close[i]

    atr_series = compute_atr(df["High"], df["Low"], df["Close"], window=14)
    atr = atr_series.to_numpy(dtype=float, copy=False)

    vol_sma = pd.Series(vol, copy=False).rolling(20, min_periods=5).mean().to_numpy()

    orb_high = np.full(n, np.nan, dtype=float)
    orb_low = np.full(n, np.nan, dtype=float)
    current_date: str | None = None
    session_h: list[float] = []
    session_l: list[float] = []
    for i in range(n):
        d = str(dates[i])
        # Skip bars on non-trading days: leave orb_high/orb_low as NaN.
        if d not in _valid_sessions:
            continue
        if d != current_date:
            current_date = d
            session_h = []
            session_l = []
        session_h.append(float(high[i]))
        session_l.append(float(low[i]))
        if len(session_h) >= 2:
            orb_high[i] = max(session_h[0], session_h[1])
            orb_low[i] = min(session_l[0], session_l[1])

    ema9 = pd.Series(close, copy=False).ewm(span=9, adjust=False).mean().to_numpy()
    ema21 = pd.Series(close, copy=False).ewm(span=21, adjust=False).mean().to_numpy()

    lookback = 8
    prior_roll_max = np.full(n, np.nan, dtype=float)
    for i in range(lookback, n):
        prior_roll_max[i] = float(np.max(high[i - lookback : i]))

    min_warmup = max(50, lookback + 5, 22)
    min_warmup = min(min_warmup, max(n - 3, 0))

    return {
        "dates": dates,
        "bar_of_day": bar_of_day,
        "vwap": vwap,
        "atr": atr,
        "vol_sma": vol_sma,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "ema9": ema9,
        "ema21": ema21,
        "prior_roll_max": prior_roll_max,
        "lookback": lookback,
        "strategy_id": strategy_id,
        "min_warmup": int(min_warmup),
    }


def day_trade_signal_at(i: int, precomp: dict, symbol: str, df: pd.DataFrame) -> Signal | None:
    """Return a Buy signal for bar index i, or None."""
    sid = precomp["strategy_id"]
    close = df["Close"].to_numpy(dtype=float, copy=False)
    vol = df["Volume"].to_numpy(dtype=float, copy=False)

    price = float(close[i])
    atr_i = float(precomp["atr"][i]) if i < len(precomp["atr"]) and np.isfinite(precomp["atr"][i]) else 0.0

    if sid == "opening_range_breakout":
        if int(precomp["bar_of_day"][i]) < 2:
            return None
        oh = precomp["orb_high"][i]
        if not np.isfinite(oh):
            return None
        vs = precomp["vol_sma"][i]
        if not np.isfinite(vs) or vs <= 0:
            return None
        if close[i] > oh and vol[i] > 1.15 * vs:
            return Signal(symbol, "Buy", 72, 50.0, 0.0, price)
        return None

    if sid == "vwap_mean_reversion":
        vw = float(precomp["vwap"][i])
        if atr_i <= 0:
            return None
        stretch = (vw - close[i]) / atr_i
        if stretch > 0.55 and close[i] < vw:
            return Signal(symbol, "Buy", 68, 35.0, 0.0, price)
        return None

    if sid == "momentum_pullback":
        if i < 1:
            return None
        e9 = precomp["ema9"]
        e21 = precomp["ema21"]
        if close[i] <= e21[i] or not np.isfinite(e9[i]) or not np.isfinite(e21[i]):
            return None
        if close[i] > e9[i] and close[i - 1] <= e9[i - 1]:
            return Signal(symbol, "Buy", 75, 55.0, 0.0, price)
        return None

    if sid == "range_breakout":
        ph = precomp["prior_roll_max"][i]
        if not np.isfinite(ph):
            return None
        vs = precomp["vol_sma"][i]
        if not np.isfinite(vs) or vs <= 0:
            return None
        if close[i] > ph and vol[i] > 1.2 * vs:
            return Signal(symbol, "Buy", 70, 50.0, 0.0, price)
        return None

    return None
