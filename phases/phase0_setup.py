"""PHASE 0 — Environment setup and database initialisation.

Run this first. It creates the output folder, the SQLite database, and prints a
short environment report so you can confirm the notebook is wired up correctly
before spending compute on the simulation.

Kaggle: paste this whole file into the first cell and run it.
"""

import os
import sqlite3
import sys

# --- paste-safe path header (duplicated in every phase) --------------------
def catrisk_paths():
    if os.path.isdir("/kaggle/working"):
        base = "/kaggle/working/catrisk"
    else:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            here = os.getcwd()
        base = os.path.join(here, "..", "outputs")
    base = os.path.abspath(base)
    figures = os.path.join(base, "figures")
    os.makedirs(figures, exist_ok=True)
    return base, os.path.join(base, "catrisk.db"), figures
# ---------------------------------------------------------------------------

BASE, DB, FIGDIR = catrisk_paths()


def main():
    print("=" * 70)
    print("CATASTROPHE RISK PLATFORM — PHASE 0: SETUP")
    print("=" * 70)

    import numpy
    import pandas
    import matplotlib

    print(f"python      : {sys.version.split()[0]}")
    print(f"numpy       : {numpy.__version__}")
    print(f"pandas      : {pandas.__version__}")
    print(f"matplotlib  : {matplotlib.__version__}")
    print(f"sqlite      : {sqlite3.sqlite_version}")

    # Window functions (used heavily in Phase 4) need SQLite >= 3.25.
    major, minor = (int(x) for x in sqlite3.sqlite_version.split(".")[:2])
    if (major, minor) < (3, 25):
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} is too old for window functions; "
            "Phase 4 requires >= 3.25."
        )
    print("window functions: supported")

    # A fresh database each run keeps the pipeline reproducible end to end.
    if os.path.exists(DB):
        os.remove(DB)
        print("removed previous database (clean rebuild)")

    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.close()

    print(f"\nbase dir    : {BASE}")
    print(f"database    : {DB}")
    print(f"figures     : {FIGDIR}")
    print("\nPhase 0 complete. Next: phase1_exposure.py")


if __name__ == "__main__":
    main()
