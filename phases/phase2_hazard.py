"""PHASE 2 — Stochastic hazard layer.

Generates a synthetic event catalog of tropical cyclones over the Bay of Bengal
and computes, for every property, the peak wind speed it experiences in each
event. This is the core stochastic modelling piece.

Structure of the model
----------------------
1. FREQUENCY  Number of cyclones per year ~ Poisson(lambda).
2. TRACK      A correlated random walk. Bearing and translation speed persist
              step to step with Gaussian perturbations, which is the standard
              first-order way to get realistic-looking track bundles without
              fitting a full historical transition matrix.
3. INTENSITY  Over sea, Vmax relaxes exponentially towards a storm-specific
              potential intensity. Over land it decays following the
              Kaplan-DeMaria form Vb + (V0-Vb)*exp(-alpha*t).
4. WIND FIELD A parametric radial profile: linear inside the radius of maximum
              winds, decaying as (Rmax/r)^0.6 outside it.

Each property's hazard for an event is the maximum wind it sees across all
track steps.

MODEL NOTE: parameters are chosen to be plausible for the basin, not fitted to
IBTrACS. The wind field is symmetric, so it ignores the asymmetry caused by
forward motion. Both are recorded in the README limitations.
"""

import os
import sqlite3
import time

import numpy as np
import pandas as pd

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

RANDOM_SEED = 20240
N_YEARS = 1000
LANDFALL_RATE = 2.5
TIMESTEP_HOURS = 6
MAX_TRACK_STEPS = 40
WIND_THRESHOLD_MS = 25.0

COAST_LAT = np.array([8.1, 10.3, 13.1, 15.9, 17.7, 19.8, 20.3, 21.6, 22.6])
COAST_LON = np.array([77.6, 79.9, 80.3, 80.8, 83.3, 85.8, 86.7, 88.1, 89.5])

# Kaplan-DeMaria inland decay: background wind and decay constant per hour.
DECAY_BACKGROUND_MS = 18.0
DECAY_ALPHA_PER_HR = 0.095


def coast_lon_at(lat):
    return np.interp(lat, COAST_LAT, COAST_LON)


def haversine_km(lat1, lon1, lat2, lon2):
    r1, r2 = np.radians(lat1), np.radians(lat2)
    dlat = r2 - r1
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(r1) * np.cos(r2) * np.sin(dlon / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def simulate_track(rng):
    """One cyclone: returns a list of (step, lat, lon, vmax, rmax, over_land)."""
    lat = rng.uniform(8.0, 16.0)
    lon = rng.uniform(86.0, 95.0)
    bearing = rng.normal(318.0, 30.0)          # compass degrees, NW-ish
    speed = float(np.clip(rng.normal(18.0, 5.0), 8.0, 35.0))  # km/h
    vmax = 20.0
    peak_intensity = float(np.clip(rng.normal(52.0, 18.0), 25.0, 85.0))
    intensification = rng.uniform(0.30, 0.90)

    points = []
    weak_over_land = 0

    for step in range(MAX_TRACK_STEPS):
        over_land = lon < coast_lon_at(lat)

        if over_land:
            vmax = DECAY_BACKGROUND_MS + (vmax - DECAY_BACKGROUND_MS) * np.exp(
                -DECAY_ALPHA_PER_HR * TIMESTEP_HOURS
            )
        else:
            vmax += intensification * (peak_intensity - vmax) * (TIMESTEP_HOURS / 24.0)
            vmax += rng.normal(0, 1.5)
        vmax = float(np.clip(vmax, 10.0, 95.0))

        # Intense storms have tighter eyewalls.
        rmax = float(
            np.clip(60.0 * np.exp(-0.012 * (vmax - 20.0)) * rng.lognormal(0, 0.18), 15.0, 90.0)
        )

        points.append((step, float(lat), float(lon), vmax, rmax, int(over_land)))

        if over_land and vmax < 18.5:
            weak_over_land += 1
            if weak_over_land >= 2:
                break
        if lat > 26.0 or lat < 5.0 or lon < 74.0:
            break

        bearing += rng.normal(0, 8.0)
        speed = float(np.clip(speed + rng.normal(0, 2.0), 8.0, 35.0))
        dist = speed * TIMESTEP_HOURS
        br = np.radians(bearing)
        lat += dist * np.cos(br) / 111.32
        lon += dist * np.sin(br) / (111.32 * np.cos(np.radians(lat)))

    return points


def event_footprint(track, plat, plon, pid, threshold=WIND_THRESHOLD_MS):
    """Peak wind per property for one event. Returns (ids, winds) or None."""
    arr = np.asarray([[p[1], p[2], p[3], p[4]] for p in track], dtype=float)
    tlat, tlon, tv, trm = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]

    keep = tv >= 20.0
    if not keep.any():
        return None
    tlat, tlon, tv, trm = tlat[keep], tlon[keep], tv[keep], trm[keep]

    # Cheap bounding-box prefilter before the full distance matrix.
    box = (
        (plat > tlat.min() - 4.0)
        & (plat < tlat.max() + 4.0)
        & (plon > tlon.min() - 4.0)
        & (plon < tlon.max() + 4.0)
    )
    if not box.any():
        return None

    slat, slon, spid = plat[box], plon[box], pid[box]
    r = haversine_km(tlat[:, None], tlon[:, None], slat[None, :], slon[None, :])
    rm = trm[:, None]
    vm = tv[:, None]
    wind = np.where(r <= rm, vm * (r / rm), vm * (rm / np.maximum(r, 1.0)) ** 0.6)
    peak = wind.max(axis=0)

    hit = peak >= threshold
    if not hit.any():
        return None
    return spid[hit], peak[hit]


def main():
    rng = np.random.default_rng(RANDOM_SEED)
    t0 = time.time()

    print("=" * 70)
    print("PHASE 2: STOCHASTIC HAZARD LAYER")
    print("=" * 70)

    con = sqlite3.connect(DB)
    exposure = pd.read_sql("SELECT property_id, latitude, longitude FROM exposure", con)
    plat = exposure["latitude"].to_numpy()
    plon = exposure["longitude"].to_numpy()
    pid = exposure["property_id"].to_numpy()
    print(f"portfolio loaded   : {len(exposure):,} properties")
    print(f"simulating         : {N_YEARS:,} years at lambda={LANDFALL_RATE}")

    counts = rng.poisson(LANDFALL_RATE, size=N_YEARS)
    catalog, track_rows, foot_ids, foot_winds, foot_events = [], [], [], [], []
    event_id = 0

    for year, n in enumerate(counts, start=1):
        for _ in range(n):
            event_id += 1
            track = simulate_track(rng)

            for step, la, lo, vm, rm, ol in track:
                track_rows.append((event_id, step, la, lo, vm, rm, ol))

            fp = event_footprint(track, plat, plon, pid)
            n_affected = 0
            if fp is not None:
                ids, winds = fp
                n_affected = len(ids)
                foot_events.append(np.full(n_affected, event_id, dtype=np.int64))
                foot_ids.append(ids)
                foot_winds.append(winds)

            catalog.append(
                {
                    "event_id": event_id,
                    "year": int(year),
                    "n_steps": len(track),
                    "max_vmax_ms": max(p[3] for p in track),
                    "made_landfall": int(any(p[5] for p in track)),
                    "n_properties_affected": n_affected,
                }
            )

    catalog_df = pd.DataFrame(catalog)
    tracks_df = pd.DataFrame(
        track_rows,
        columns=["event_id", "step", "latitude", "longitude", "vmax_ms", "rmax_km", "over_land"],
    )

    if foot_ids:
        footprint_df = pd.DataFrame(
            {
                "event_id": np.concatenate(foot_events),
                "property_id": np.concatenate(foot_ids),
                "peak_wind_ms": np.concatenate(foot_winds).round(2),
            }
        )
    else:
        footprint_df = pd.DataFrame(columns=["event_id", "property_id", "peak_wind_ms"])

    catalog_df.to_sql("event_catalog", con, index=False, if_exists="replace")
    tracks_df.to_sql("tracks", con, index=False, if_exists="replace")
    footprint_df.to_sql("hazard_footprint", con, index=False, if_exists="replace")
    con.execute("CREATE INDEX IF NOT EXISTS ix_fp_event ON hazard_footprint(event_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_fp_prop ON hazard_footprint(property_id)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS sim_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    con.executemany(
        "INSERT OR REPLACE INTO sim_meta (key, value) VALUES (?, ?)",
        [("n_years", str(N_YEARS)), ("landfall_rate", str(LANDFALL_RATE)),
         ("wind_threshold_ms", str(WIND_THRESHOLD_MS))],
    )
    con.commit()
    con.close()

    hit_events = catalog_df[catalog_df["n_properties_affected"] > 0]
    print(f"\nevents simulated   : {len(catalog_df):,}")
    print(f"made landfall      : {int(catalog_df['made_landfall'].sum()):,}")
    print(f"affected portfolio : {len(hit_events):,} "
          f"({100 * len(hit_events) / len(catalog_df):.1f}% of events)")
    print(f"footprint rows     : {len(footprint_df):,}")
    print(f"years with >=1 hit : {hit_events['year'].nunique():,} of {N_YEARS:,}")

    if len(footprint_df):
        print(f"\npeak wind observed : {footprint_df['peak_wind_ms'].max():.1f} m/s")
        print(f"mean affected wind : {footprint_df['peak_wind_ms'].mean():.1f} m/s")
        biggest = catalog_df.nlargest(3, "n_properties_affected")
        print("\nlargest footprints:")
        for _, r in biggest.iterrows():
            print(f"  event {int(r['event_id']):>5} (year {int(r['year']):>4}): "
                  f"{int(r['n_properties_affected']):>5} risks, "
                  f"Vmax {r['max_vmax_ms']:.1f} m/s")

    print(f"\nelapsed: {time.time() - t0:.1f}s")
    print("Phase 2 complete. Next: phase3_loss.py")


if __name__ == "__main__":
    main()
