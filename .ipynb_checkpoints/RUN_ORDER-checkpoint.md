# Run order

Six phases, strictly sequential. Each phase reads what the previous one wrote
into `outputs/catrisk.db`, so do not skip or reorder.

| # | File | Writes | Runtime |
|---|------|--------|---------|
| 0 | `phases/phase0_setup.py` | creates DB, checks environment | <1s |
| 1 | `phases/phase1_exposure.py` | `exposure`, `coastline`, `flood_zone_polygon` | ~1s |
| 2 | `phases/phase2_hazard.py` | `event_catalog`, `tracks`, `hazard_footprint` | ~5s |
| 3 | `phases/phase3_loss.py` | `model_a_losses`, `year_loss_a`, `ep_curve_a`, `property_aal_a` | ~2s |
| 4 | `phases/phase4_reconciliation.py` | `model_b_*`, `recon_property`, `recon_summary` | ~10s |
| 5 | `phases/phase5_dashboard.py` | seven PNGs in `outputs/figures/` | ~5s |

Total: well under a minute.

## Running locally

```bash
pip install -r requirements.txt
python phases/phase0_setup.py
python phases/phase1_exposure.py
python phases/phase2_hazard.py
python phases/phase3_loss.py
python phases/phase4_reconciliation.py
python phases/phase5_dashboard.py
```

Or all at once:

```bash
for p in 0_setup 1_exposure 2_hazard 3_loss 4_reconciliation 5_dashboard; do
    python phases/phase${p}.py || break
done
```

## Running on Kaggle

Each phase file is self-contained and safe to paste directly into a notebook
cell — there are no cross-file imports. Every phase re-derives its own paths
via a small `catrisk_paths()` header, which detects `/kaggle/working` and
writes there automatically.

**Option A — one cell per phase (recommended for a walkthrough).**
Create six cells. Paste the contents of one phase file into each, in order,
and run them top to bottom. Add a markdown cell above each one explaining what
that phase does; that structure is what makes the notebook readable to a
non-technical reviewer.

**Option B — upload as a dataset.**
Upload the `catrisk-platform` folder as a Kaggle dataset, then in one cell:

```python
import subprocess
for p in ["0_setup", "1_exposure", "2_hazard", "3_loss", "4_reconciliation", "5_dashboard"]:
    print(subprocess.run(
        ["python", f"/kaggle/input/catrisk-platform/phases/phase{p}.py"],
        capture_output=True, text=True).stdout)
```

Kaggle needs no internet for any of this — the model is fully self-contained
and generates its own data.

## Notes

- Phase 0 deletes and recreates the database, so the pipeline is reproducible
  end to end. Re-running from Phase 0 always gives identical numbers (all
  random draws are seeded).
- If you only change the loss assumptions, you can rerun from Phase 3 without
  re-simulating the hazard.
- `catrisk_paths()` is duplicated in each phase rather than imported. That is a
  deliberate trade: it costs six copies of a six-line function and buys the
  ability to paste any phase into a Kaggle cell with no setup.
