"""Shared configuration for the catastrophe risk platform.

Every phase re-derives its paths from `catrisk_paths()`. That helper is
duplicated as a small header block inside each phase file so that any phase can
be pasted directly into a Kaggle cell without importing this module. This file
is the single source of truth when running locally.
"""

import os

# ---------------------------------------------------------------------------
# Model dimensions. Tuned so the whole pipeline runs in a few minutes on Kaggle.
# ---------------------------------------------------------------------------
N_YEARS = 1000          # simulated years in the stochastic catalog
LANDFALL_RATE = 2.5     # Poisson mean: cyclones per year entering the basin
N_PROPERTIES = 2000     # synthetic portfolio size
RANDOM_SEED = 42

# Wind speeds are 1-minute sustained, metres per second, throughout.
WIND_THRESHOLD_MS = 25.0   # below this, damage is treated as zero
TIMESTEP_HOURS = 6
MAX_TRACK_STEPS = 40

# Financial terms applied uniformly to the synthetic portfolio.
DEDUCTIBLE_FRACTION = 0.05

# Reconciliation tolerance: relative loss differences inside this band are not
# flagged as exceptions.
RECON_TOLERANCE = 0.01


def catrisk_paths():
    """Return (base_dir, db_path, figures_dir), creating them if needed."""
    if os.path.isdir("/kaggle/working"):
        base = "/kaggle/working/catrisk"
    else:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            here = os.getcwd()
        base = os.path.join(here, "outputs")
    figures = os.path.join(base, "figures")
    os.makedirs(figures, exist_ok=True)
    return base, os.path.join(base, "catrisk.db"), figures
