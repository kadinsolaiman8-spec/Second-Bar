# WFO Commands Reference

Run from project root. Use `python -m src.run_wfo` (not `python src/run_wfo.py`) to avoid `ModuleNotFoundError: No module named 'src'`.

## Batch Run

```powershell
.\run_wfo_batch.ps1
```

Runs 4 tickers (GLD, XLE, GDX, SPY). Each takes ~5–10 min (48 combos × 7 folds).

## Individual Commands

### MR tickers — re-optimize with expanded grid

```bash
python -m src.run_wfo SPY --strategy mr --period 5y --save-profile
python -m src.run_wfo QQQ --strategy mr --period 5y --save-profile
python -m src.run_wfo IWM --strategy mr --period 5y --save-profile
```

### TF ticker — re-confirm GLD profile

```bash
python -m src.run_wfo GLD --strategy tf --period 5y --save-profile
```

### New TF candidates — commodities

```bash
python -m src.run_wfo XLE --strategy tf --period 5y --save-profile
python -m src.run_wfo USO --strategy tf --period 5y --save-profile
python -m src.run_wfo GDX --strategy tf --period 5y --save-profile
```

Profiles are saved to `config.yaml` under `ticker_profiles`. Optional `data/ticker_profiles.yaml` can hold external profiles; merged at startup with config.yaml overrides taking precedence.
