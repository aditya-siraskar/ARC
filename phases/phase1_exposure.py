"""PHASE 1 — Exposure layer.

Builds a synthetic property portfolio along the east coast of India (Bay of
Bengal basin) and writes it to SQLite. Also defines an illustrative coastal and
delta inundation zone, giving the portfolio a second peril view alongside wind.

Everything downstream keys off `property_id`, so this phase must run before
Phases 2-5.

MODEL NOTE: the portfolio is entirely synthetic. Locations are sampled around
real cities, but no real client data is used anywhere in this project.
"""

import os
import sqlite3

import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath

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

RANDOM_SEED = 42
N_PROPERTIES = 2000
DEDUCTIBLE_FRACTION = 0.05

# Approximate east coast of India, used as the land/sea boundary throughout the
# project. Land is west of this line at a given latitude.
COAST_LAT = np.array([8.1, 10.3, 13.1, 15.9, 17.7, 19.8, 20.3, 21.6, 22.6])
COAST_LON = np.array([77.6, 79.9, 80.3, 80.8, 83.3, 85.8, 86.7, 88.1, 89.5])

# Illustrative inundation extent over the Ganges-Brahmaputra delta. In a real
# workflow this polygon would come from a flood hazard vendor or a Copernicus
# EMS rapid-mapping product; here it is hand-drawn to keep the project offline.
FLOOD_POLYGON = [
    (87.60, 21.30),
    (89.60, 21.40),
    (89.90, 23.00),
    (88.00, 23.20),
]
SURGE_BAND_KM = 3.0  # everything within this distance of the coast is exposed

CITIES = [
    # name,           region,          lat,    lon,   weight, spread_deg
    ("Chennai",       "Tamil Nadu",    13.08,  80.27, 0.28,   0.35),
    ("Nellore",       "Andhra Pradesh", 14.44, 80.00, 0.12,   0.30),
    ("Visakhapatnam", "Andhra Pradesh", 17.69, 83.21, 0.20,   0.30),
    ("Puri",          "Odisha",        19.81,  85.83, 0.15,   0.30),
    ("Kolkata",       "West Bengal",   22.57,  88.36, 0.25,   0.40),
]

CONSTRUCTION_BY_OCCUPANCY = {
    "RESIDENTIAL": (["MASONRY", "RC_FRAME", "TIMBER"],     [0.55, 0.35, 0.10]),
    "COMMERCIAL":  (["RC_FRAME", "MASONRY", "LIGHT_METAL"], [0.65, 0.25, 0.10]),
    "INDUSTRIAL":  (["LIGHT_METAL", "RC_FRAME", "MASONRY"], [0.55, 0.35, 0.10]),
}
OCCUPANCY_MIX = (["RESIDENTIAL", "COMMERCIAL", "INDUSTRIAL"], [0.60, 0.28, 0.12])

# Median TIV and lognormal sigma by occupancy, in USD.
TIV_PARAMS = {
    "RESIDENTIAL": (120_000, 0.70),
    "COMMERCIAL": (900_000, 0.85),
    "INDUSTRIAL": (2_500_000, 0.90),
}

FX_TO_USD = {"USD": 1.0, "INR": 0.012}


def coast_lon_at(lat):
    """Longitude of the coastline at a given latitude."""
    return np.interp(lat, COAST_LAT, COAST_LON)


def distance_to_coast_km(lat, lon):
    """East-west distance inland from the coastline.

    The coast here runs roughly north-south, so an east-west offset is a fair
    approximation of the true perpendicular distance. It degrades where the
    coast turns sharply (the Odisha bend); flagged in the README limitations.
    """
    return (coast_lon_at(lat) - lon) * 111.32 * np.cos(np.radians(lat))


def build_portfolio(rng):
    names = [c[0] for c in CITIES]
    weights = np.array([c[4] for c in CITIES])
    weights = weights / weights.sum()

    idx = rng.choice(len(CITIES), size=N_PROPERTIES, p=weights)
    rows = []
    for i, city_i in enumerate(idx):
        name, region, clat, clon, _, spread = CITIES[city_i]
        lat = clat + rng.normal(0, spread)
        lon = clon + rng.normal(0, spread)

        # Force every risk onshore: if the sampled point fell into the sea,
        # reflect it inland of the coastline.
        cl = coast_lon_at(lat)
        if lon > cl - 0.02:
            lon = cl - 0.02 - abs(rng.normal(0, 0.15))

        occ = rng.choice(OCCUPANCY_MIX[0], p=OCCUPANCY_MIX[1])
        con_opts, con_p = CONSTRUCTION_BY_OCCUPANCY[occ]
        con = rng.choice(con_opts, p=con_p)

        median, sigma = TIV_PARAMS[occ]
        tiv_usd = float(median * rng.lognormal(0, sigma))

        # A small slice of the book is written in local currency. Phase 4 uses
        # this to reproduce a classic FX reconciliation break.
        currency = "INR" if rng.random() < 0.08 else "USD"
        fx = FX_TO_USD[currency]
        tiv_local = tiv_usd / fx

        rows.append(
            {
                "property_id": i + 1,
                "city": name,
                "region": region,
                "latitude": round(float(lat), 5),
                "longitude": round(float(lon), 5),
                "construction": con,
                "occupancy": occ,
                "currency": currency,
                "fx_to_usd": fx,
                "tiv_local_ccy": round(tiv_local, 2),
                "tiv_usd": round(tiv_usd, 2),
                "deductible_frac": DEDUCTIBLE_FRACTION,
            }
        )

    df = pd.DataFrame(rows)
    df["dist_to_coast_km"] = distance_to_coast_km(
        df["latitude"].to_numpy(), df["longitude"].to_numpy()
    ).round(2)
    return df


def flag_flood_zone(df):
    poly = MplPath(np.array(FLOOD_POLYGON))
    pts = np.column_stack([df["longitude"].to_numpy(), df["latitude"].to_numpy()])
    in_delta = poly.contains_points(pts)
    in_surge = df["dist_to_coast_km"].to_numpy() <= SURGE_BAND_KM
    df["in_flood_zone"] = (in_delta | in_surge).astype(int)
    df["flood_zone_source"] = np.where(
        in_delta, "DELTA_POLYGON", np.where(in_surge, "COASTAL_SURGE_BAND", "NONE")
    )
    return df


def main():
    rng = np.random.default_rng(RANDOM_SEED)

    print("=" * 70)
    print("PHASE 1: EXPOSURE LAYER")
    print("=" * 70)

    df = build_portfolio(rng)
    df = flag_flood_zone(df)

    con = sqlite3.connect(DB)
    df.to_sql("exposure", con, index=False, if_exists="replace")
    con.execute("CREATE INDEX IF NOT EXISTS ix_exposure_pid ON exposure(property_id)")

    poly_df = pd.DataFrame(FLOOD_POLYGON, columns=["longitude", "latitude"])
    poly_df["vertex_order"] = range(len(poly_df))
    poly_df.to_sql("flood_zone_polygon", con, index=False, if_exists="replace")

    coast_df = pd.DataFrame({"latitude": COAST_LAT, "longitude": COAST_LON})
    coast_df.to_sql("coastline", con, index=False, if_exists="replace")

    con.commit()
    con.close()

    total_tiv = df["tiv_usd"].sum()
    print(f"properties written : {len(df):,}")
    print(f"total insured value: USD {total_tiv:,.0f}")
    print(f"mean TIV           : USD {df['tiv_usd'].mean():,.0f}")
    print(f"in flood zone      : {int(df['in_flood_zone'].sum()):,} "
          f"({100 * df['in_flood_zone'].mean():.1f}%)")
    print(f"non-USD risks      : {int((df['currency'] != 'USD').sum()):,}")

    print("\nTIV by region (USD m):")
    by_region = (
        df.groupby("region")["tiv_usd"].agg(["count", "sum"]).sort_values("sum", ascending=False)
    )
    for region, row in by_region.iterrows():
        print(f"  {region:<18} {int(row['count']):>5} risks   {row['sum'] / 1e6:>10,.1f}")

    print("\nConstruction mix:")
    for con_type, n in df["construction"].value_counts().items():
        print(f"  {con_type:<14} {n:>5} ({100 * n / len(df):.1f}%)")

    print("\nPhase 1 complete. Next: phase2_hazard.py")


if __name__ == "__main__":
    main()
