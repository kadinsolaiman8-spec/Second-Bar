# Quant stack (`quant/`)

**Path:** [`quant/`](../quant/)  
**Nature:** Vendored quantitative engine from the archived Trading-Bot project — walk-forward optimization, stationary bootstrap / DSR paths, pool scripts. **No Discord layer** here.

## Operator flow

```text
pip install -r requirements.txt
pytest tests/
python -m quant.run_wfo --help
```

[`scripts/pool_validation.py`](../scripts/pool_validation.py) and [`scripts/apply_multiple_testing_correction.py`](../scripts/apply_multiple_testing_correction.py) expect repo-root `PYTHONPATH` (same as pytest).

## HTTP hooks

[`backend/app.py`](../backend/app.py) exposes `POST /api/quant/backtest` using [`quant/backtest.py`](../quant/backtest.py) and merged [`config.yaml`](../config.yaml).

## Methodology docs

Prefer [`docs/STRATEGY_AND_EDGE.md`](../docs/STRATEGY_AND_EDGE.md), [`docs/WFO_COMMANDS.md`](../docs/WFO_COMMANDS.md), and [`README.md`](../README.md) honesty framing before changing defaults.

## Tests

[`tests/`](../tests/) imports use the `quant.` package prefix.
