# A.R.C. — Run Order

**Advanced Rupture & Catastrophe Intelligence**

Six modules, strictly sequential. Each phase reads what its predecessors wrote
into `outputs/catrisk.db`, so do not skip or reorder them.

| # | Module | Writes | Runtime |
|---|--------|--------|---------|
| 0 | `phases/phase0_setup.py` | Creates the database, verifies the environment | <1s |
| 1 | `phases/phase1_exposure.py` | `exposure`, `coastline`, `flood_zone_polygon` | ~1s |
| 2 | `phases/phase2_hazard.py` | `event_catalog`, `tracks`, `hazard_footprint` | ~5s |
| 3 | `phases/phase3_loss.py` | `model_a_losses`, `year_loss_a`, `ep_curve_a`, `property_aal_a` | ~2s |
| 4 | `phases/phase4_reconciliation.py` | `model_b_*`, `recon_property`, `recon_summary` | ~10s |
| 5 | `phases/phase5_dashboard.py` | Seven PNGs in `outputs/figures/` | ~5s |

**Total: under 30 seconds.** Final state is a 24-table SQLite database and
seven figures.

---

## Running locally

```bash
pip install -r requirements.txt
```

Then either run each phase explicitly:

```bash
python phases/phase0_setup.py
python phases/phase1_exposure.py
python phases/phase2_hazard.py
python phases/phase3_loss.py
python phases/phase4_reconciliation.py
python phases/phase5_dashboard.py
```

Or run the whole pipeline, stopping at the first failure:

```bash
for p in 0_setup 1_exposure 2_hazard 3_loss 4_reconciliation 5_dashboard; do
    python phases/phase${p}.py || break
done
```

---

## Running on Kaggle

Every phase is self-contained — there are no cross-file imports. Each one
re-derives its own paths via a small `catrisk_paths()` header that detects
`/kaggle/working` and writes there automatically. **No internet is required:**
A.R.C. generates all of its own data.

### Option A — one cell per phase (recommended)

Create six cells, paste one phase file into each in order, and run top to
bottom. Add a markdown cell above each explaining what that phase does — that
structure is what makes the notebook legible to a non-technical reviewer, and
it lets you walk an interviewer through the pipeline one stage at a time.

### Option B — upload as a dataset

Upload the `arc` folder as a Kaggle dataset, then run a single cell:

```python
import subprocess

DATASET = "/kaggle/input/arc"   # replace with your dataset's actual slug

for p in ["0_setup", "1_exposure", "2_hazard", "3_loss",
          "4_reconciliation", "5_dashboard"]:
    r = subprocess.run(["python", f"{DATASET}/phases/phase{p}.py"],
                       capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        break
```

Kaggle slugs dataset names to lowercase and hyphenates them, so confirm the
path under `/kaggle/input/` before running.

---

## Notes

- **Reproducibility.** Phase 0 deletes and recreates the database, so the
  pipeline rebuilds cleanly end to end. Every random draw is seeded — rerunning
  from Phase 0 always reproduces the exact figures quoted in the README.
- **Partial reruns.** If you change only the loss assumptions (vulnerability
  curves, deductibles), rerun from Phase 3 — there is no need to re-simulate
  the hazard catalog.
- **Deliberate duplication.** `catrisk_paths()` is copied into each phase
  rather than imported from `config.py`. That trade costs six copies of a
  six-line function and buys the ability to paste any phase straight into a
  Kaggle cell with zero setup. `config.py` documents the parameters in one
  place but is not imported by the pipeline.
- **Dependency footprint.** numpy, pandas and matplotlib only. `sqlite3` ships
  with Python. All three are preinstalled on Kaggle.
- **Version note.** The pipeline avoids deprecated pandas and numpy APIs, but
  Kaggle's versions may lag a local install. Run Phases 0–2 first and confirm
  they pass before building the rest of the notebook around them.
