# Live (embed) vs backtest stop / take-profit parity

This documents how [`src/signals.py`](src/signals.py) `_compute_stop_tp_levels` (Discord `/stock` display) relates to exit logic in [`src/backtest.py`](src/backtest.py) (`run_backtest` loop).

| Config key (`backtest.*`) | Backtest behavior | Live `_compute_stop_tp_levels` | Parity |
|---------------------------|-------------------|--------------------------------|--------|
| `stop_pct` | Hard stop: exit when unrealized P&amp;L &lt;= `-stop_pct` (percent from entry). | If no ATR trail (or ATR missing), stop price is `price × (1 ± stop_pct/100)` from **latest** close. | **Match** when the same resolved `backtest` dict is used. Default if key absent: **0** in both paths (no hard stop unless configured). |
| `take_profit_pct` | Exit when unrealized P&amp;L &gt;= `take_profit_pct`. | TP price from latest close: `price × (1 ± take_profit_pct/100)`. | **Match** for level math; backtest uses bar close vs threshold, fill at **next open** (see below). |
| `trailing_stop_atr_multiplier` | ATR trail: exit when `peak_close - current_close >= ATR[i] * mult` while long (`atr_period` from `config.indicators.atr_period`, default 14). | Initial trail distance: `latest_close ± ATR × mult` (ATR from same `atr_period` in indicator pipeline). | **Intentional difference**: live shows a **snapshot** stop from current bar’s ATR and price; backtest trails from **peak** price since entry and re-evaluates each bar. Live level is **not** guaranteed to equal the next backtest exit trigger. |
| `trailing_stop_pct` | Retracement from **peak**; used only if `trailing_stop_atr_multiplier <= 0`. | **Not implemented** in `_compute_stop_tp_levels` (only ATR trail and `stop_pct`). | **Gap**: if `trailing_stop_pct > 0` and ATR mult is 0, embed omits trail stop; backtest still applies % trail. |
| `max_hold_bars` | Force exit after N bars in trade. | Not shown on embed. | **Display-only gap** (not a math conflict). |

## Execution semantics (both paths)

- Backtest fills entries/exits at **next bar open** after the signal/bar where the rule triggers.
- Live embed is **informational**; it does not execute orders.

## Regression note

If `stop_pct` defaults ever diverge between modules, partial configs (without `backtest.stop_pct`) could show different behavior. Both code paths use **`0`** when the key is missing so “no hard stop” is consistent unless YAML supplies a value (e.g. `config.yaml` uses `stop_pct: 5`).
