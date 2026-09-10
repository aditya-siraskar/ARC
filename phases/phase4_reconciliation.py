"""PHASE 4 — Model B and the reconciliation / QA engine.

This is the quality-check half of the project. It builds a second view of the
same portfolio ("Model B") with four deliberately introduced defects, reruns the
loss engine, and then reconciles A against B using SQL to find the breaks.

The four defects are the ones that actually cause reconciliation queries in
practice:

  1. VULNERABILITY   Model B uses a slightly more damaging curve for MASONRY
                     and TIMBER. Systematic, affects many risks a little.
  2. GEOCODE         15% of risks are geocoded to 1 decimal place (~11 km).
                     Coarse geocoding moves risks relative to the wind field.
  3. FX RATE         Non-USD risks are converted at a stale rate, overstating
                     their TIV by 10%.
  4. MISSING DATA    1% of risks fail to load into Model B entirely.

The reconciliation SQL is written blind: it classifies breaks purely from the
two exposure files and the two loss outputs. Only at the end do we compare the
classification against the manifest of what was actually injected, which gives
an honest detection rate.
"""

import os
import sqlite3

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

RANDOM_SEED = 7
WIND_THRESHOLD_MS = 25.0
RECON_TOLERANCE = 0.01      # 1% relative AAL difference

GEOCODE_SHIFT_FRAC = 0.15
MISSING_FRAC = 0.01
STALE_FX_UPLIFT = 1.10

VULN_MODEL_A = {
    "RC_FRAME":    {"v50": 68.0, "k": 0.11},
    "MASONRY":     {"v50": 55.0, "k": 0.13},
    "LIGHT_METAL": {"v50": 45.0, "k": 0.15},
    "TIMBER":      {"v50": 40.0, "k": 0.16},
}
# Model B is more pessimistic on the two weaker construction classes.
VULN_MODEL_B = {
    "RC_FRAME":    {"v50": 68.0, "k": 0.11},
    "MASONRY":     {"v50": 51.0, "k": 0.13},
    "LIGHT_METAL": {"v50": 45.0, "k": 0.15},
    "TIMBER":      {"v50": 36.0, "k": 0.16},
}

REPORT_RETURN_PERIODS = [5, 10, 25, 50, 100, 200, 250, 500, 1000]


def mean_damage_ratio(wind_ms, construction, params):
    wind_ms = np.asarray(wind_ms, dtype=float)
    v50 = np.array([params[c]["v50"] for c in construction])
    k = np.array([params[c]["k"] for c in construction])
    sig = 1.0 / (1.0 + np.exp(-k * (wind_ms - v50)))
    sig_thresh = 1.0 / (1.0 + np.exp(-k * (WIND_THRESHOLD_MS - v50)))
    return np.clip((sig - sig_thresh) / (1.0 - sig_thresh), 0.0, 1.0)


def haversine_km(lat1, lon1, lat2, lon2):
    r1, r2 = np.radians(lat1), np.radians(lat2)
    dlat = r2 - r1
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(r1) * np.cos(r2) * np.sin(dlon / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def recompute_footprint(tracks, plat, plon, pid):
    """Rerun the wind field for a subset of properties whose coords moved.

    Same radial profile as Phase 2, applied only to the shifted risks, so this
    stays cheap even though it re-walks the full event catalog.
    """
    out_events, out_ids, out_winds = [], [], []
    for event_id, g in tracks.groupby("event_id", sort=False):
        tv = g["vmax_ms"].to_numpy()
        keep = tv >= 20.0
        if not keep.any():
            continue
        tlat = g["latitude"].to_numpy()[keep]
        tlon = g["longitude"].to_numpy()[keep]
        trm = g["rmax_km"].to_numpy()[keep]
        tv = tv[keep]

        box = (
            (plat > tlat.min() - 4.0) & (plat < tlat.max() + 4.0)
            & (plon > tlon.min() - 4.0) & (plon < tlon.max() + 4.0)
        )
        if not box.any():
            continue

        slat, slon, spid = plat[box], plon[box], pid[box]
        r = haversine_km(tlat[:, None], tlon[:, None], slat[None, :], slon[None, :])
        rm, vm = trm[:, None], tv[:, None]
        wind = np.where(r <= rm, vm * (r / rm), vm * (rm / np.maximum(r, 1.0)) ** 0.6)
        peak = wind.max(axis=0)

        hit = peak >= WIND_THRESHOLD_MS
        if not hit.any():
            continue
        out_events.append(np.full(int(hit.sum()), event_id, dtype=np.int64))
        out_ids.append(spid[hit])
        out_winds.append(peak[hit])

    if not out_ids:
        return pd.DataFrame(columns=["event_id", "property_id", "peak_wind_ms"])
    return pd.DataFrame(
        {
            "event_id": np.concatenate(out_events),
            "property_id": np.concatenate(out_ids),
            "peak_wind_ms": np.concatenate(out_winds).round(2),
        }
    )


def ep_curve(values, n_years):
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    exceedance = np.arange(1, len(v) + 1) / (n_years + 1.0)
    return 1.0 / exceedance, v


def loss_at_rp(rp_arr, loss_arr, target_rp):
    order = np.argsort(rp_arr)
    return float(np.interp(np.log(target_rp), np.log(rp_arr[order]), loss_arr[order]))


RECON_SQL = """
WITH a AS (
    SELECT e.property_id, e.region, e.construction, e.currency,
           e.latitude AS lat_a, e.longitude AS lon_a, e.tiv_usd AS tiv_a,
           COALESCE(pa.aal_usd, 0.0) AS aal_a
    FROM exposure e
    LEFT JOIN property_aal_a pa ON pa.property_id = e.property_id
),
b AS (
    SELECT be.property_id, be.latitude AS lat_b, be.longitude AS lon_b,
           be.tiv_usd AS tiv_b, COALESCE(pb.aal_usd, 0.0) AS aal_b
    FROM model_b_exposure be
    LEFT JOIN property_aal_b pb ON pb.property_id = be.property_id
),
j AS (
    SELECT a.property_id, a.region, a.construction, a.currency,
           a.lat_a, a.lon_a, a.tiv_a, a.aal_a,
           b.property_id AS pid_b, b.lat_b, b.lon_b, b.tiv_b,
           COALESCE(b.aal_b, 0.0) AS aal_b
    FROM a LEFT JOIN b ON a.property_id = b.property_id
),
classified AS (
    SELECT *,
           aal_b - aal_a AS aal_diff,
           CASE WHEN aal_a > 0 THEN (aal_b - aal_a) / aal_a END AS pct_diff,
           CASE
               WHEN pid_b IS NULL
                   THEN 'MISSING_IN_B'
               WHEN ABS(COALESCE(tiv_b, 0) - tiv_a) > 0.01 AND currency <> 'USD'
                   THEN 'FX_RATE_BREAK'
               WHEN ABS(COALESCE(tiv_b, 0) - tiv_a) > 0.01
                   THEN 'TIV_MISMATCH'
               WHEN ABS(COALESCE(lat_b, lat_a) - lat_a) > 0.0001
                 OR ABS(COALESCE(lon_b, lon_a) - lon_a) > 0.0001
                   THEN 'GEOCODE_SHIFT'
               WHEN aal_a > 0 AND ABS((aal_b - aal_a) / aal_a) > :tol
                   THEN 'VULNERABILITY_VARIANCE'
               ELSE 'WITHIN_TOLERANCE'
           END AS exception_category
    FROM j
)
SELECT *,
       ABS(aal_diff) AS abs_diff,
       RANK() OVER (ORDER BY ABS(aal_diff) DESC) AS diff_rank,
       100.0 * ABS(aal_diff)
           / NULLIF(SUM(ABS(aal_diff)) OVER (), 0) AS pct_of_total_diff,
       100.0 * SUM(ABS(aal_diff)) OVER (
                   ORDER BY ABS(aal_diff) DESC
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
           / NULLIF(SUM(ABS(aal_diff)) OVER (), 0) AS cumulative_pct_of_diff
FROM classified
ORDER BY abs_diff DESC
"""

SUMMARY_SQL = """
WITH totals AS (
    SELECT SUM(ABS(aal_diff)) AS total_abs FROM recon_property
)
SELECT exception_category,
       COUNT(*)                                   AS n_properties,
       ROUND(SUM(aal_a), 2)                       AS aal_a_usd,
       ROUND(SUM(aal_b), 2)                       AS aal_b_usd,
       ROUND(SUM(aal_diff), 2)                    AS net_diff_usd,
       ROUND(SUM(ABS(aal_diff)), 2)               AS abs_diff_usd,
       ROUND(100.0 * SUM(ABS(aal_diff))
             / NULLIF((SELECT total_abs FROM totals), 0), 2) AS pct_of_abs_diff
FROM recon_property
GROUP BY exception_category
ORDER BY abs_diff_usd DESC
"""


def main():
    rng = np.random.default_rng(RANDOM_SEED)
    print("=" * 70)
    print("PHASE 4: MODEL B + RECONCILIATION ENGINE")
    print("=" * 70)

    con = sqlite3.connect(DB)
    exposure = pd.read_sql("SELECT * FROM exposure", con)
    footprint_a = pd.read_sql("SELECT * FROM hazard_footprint", con)
    catalog = pd.read_sql("SELECT event_id, year FROM event_catalog", con)
    tracks = pd.read_sql(
        "SELECT event_id, latitude, longitude, vmax_ms, rmax_km FROM tracks "
        "ORDER BY event_id, step", con
    )
    n_years = int(pd.read_sql("SELECT value FROM sim_meta WHERE key='n_years'", con).iloc[0, 0])

    n = len(exposure)
    ids = exposure["property_id"].to_numpy()

    # ---- inject the four defects -----------------------------------------
    shuffled = rng.permutation(ids)
    n_missing = int(round(MISSING_FRAC * n))
    n_shift = int(round(GEOCODE_SHIFT_FRAC * n))
    missing_ids = set(shuffled[:n_missing].tolist())
    shifted_ids = set(shuffled[n_missing:n_missing + n_shift].tolist())

    b = exposure.copy()
    b["is_shifted"] = b["property_id"].isin(shifted_ids)
    b["is_missing"] = b["property_id"].isin(missing_ids)
    b["is_fx_break"] = b["currency"] != "USD"

    manifest = b[["property_id", "is_shifted", "is_missing", "is_fx_break"]].copy()

    # Coarse geocoding: 1 decimal place is roughly 11 km.
    b.loc[b["is_shifted"], "latitude"] = b.loc[b["is_shifted"], "latitude"].round(1)
    b.loc[b["is_shifted"], "longitude"] = b.loc[b["is_shifted"], "longitude"].round(1)
    # Stale FX rate on the non-USD slice.
    b.loc[b["is_fx_break"], "tiv_usd"] = (b.loc[b["is_fx_break"], "tiv_usd"] * STALE_FX_UPLIFT).round(2)
    # Failed load.
    b = b[~b["is_missing"]].copy()

    print(f"model B exposure   : {len(b):,} risks "
          f"({n_missing} dropped, {n_shift} regeocoded, "
          f"{int(manifest['is_fx_break'].sum())} FX-affected)")

    # ---- rebuild the footprint for moved risks only -----------------------
    shifted_b = b[b["is_shifted"]]
    print(f"recomputing wind   : {len(shifted_b):,} regeocoded risks")
    new_fp = recompute_footprint(
        tracks,
        shifted_b["latitude"].to_numpy(),
        shifted_b["longitude"].to_numpy(),
        shifted_b["property_id"].to_numpy(),
    )

    unchanged_ids = set(b.loc[~b["is_shifted"], "property_id"].tolist())
    fp_b = pd.concat(
        [footprint_a[footprint_a["property_id"].isin(unchanged_ids)], new_fp],
        ignore_index=True,
    )
    print(f"model B footprint  : {len(fp_b):,} rows (A had {len(footprint_a):,})")

    # ---- rerun the loss engine with Model B assumptions -------------------
    bl = fp_b.merge(
        b[["property_id", "region", "construction", "tiv_usd", "deductible_frac"]],
        on="property_id", how="inner",
    )
    bl["mdr"] = mean_damage_ratio(bl["peak_wind_ms"].to_numpy(), bl["construction"].tolist(), VULN_MODEL_B)
    bl["gross_loss_usd"] = bl["tiv_usd"] * bl["mdr"]
    bl["net_loss_usd"] = np.clip(
        bl["gross_loss_usd"] - bl["tiv_usd"] * bl["deductible_frac"], 0.0, bl["tiv_usd"]
    )
    bl = bl[bl["net_loss_usd"] > 0].copy()

    prop_aal_b = (
        bl.groupby("property_id")["net_loss_usd"].sum().div(n_years).reset_index()
        .rename(columns={"net_loss_usd": "aal_usd"})
    )

    event_loss_b = (
        bl.groupby("event_id")["net_loss_usd"].sum().reset_index()
        .rename(columns={"net_loss_usd": "event_loss_usd"})
        .merge(catalog, on="event_id", how="left")
    )
    year_b = (
        event_loss_b.groupby("year")
        .agg(agg_loss_usd=("event_loss_usd", "sum"),
             max_event_loss_usd=("event_loss_usd", "max"))
        .reset_index()
    )
    year_b = pd.DataFrame({"year": np.arange(1, n_years + 1)}).merge(
        year_b, on="year", how="left").fillna(0.0)

    b_out = b.drop(columns=["is_shifted", "is_missing", "is_fx_break"])
    b_out.to_sql("model_b_exposure", con, index=False, if_exists="replace")
    bl.to_sql("model_b_losses", con, index=False, if_exists="replace")
    prop_aal_b.to_sql("property_aal_b", con, index=False, if_exists="replace")
    year_b.to_sql("year_loss_b", con, index=False, if_exists="replace")
    manifest.to_sql("model_b_manifest", con, index=False, if_exists="replace")
    con.execute("CREATE INDEX IF NOT EXISTS ix_aal_b ON property_aal_b(property_id)")
    con.commit()

    oep_rp_b, oep_b = ep_curve(year_b["max_event_loss_usd"], n_years)
    aep_rp_b, aep_b = ep_curve(year_b["agg_loss_usd"], n_years)
    rp_b = pd.DataFrame([
        {"return_period": rp,
         "oep_usd": loss_at_rp(oep_rp_b, oep_b, rp),
         "aep_usd": loss_at_rp(aep_rp_b, aep_b, rp)}
        for rp in REPORT_RETURN_PERIODS
    ])
    rp_b.to_sql("ep_curve_b", con, index=False, if_exists="replace")
    pd.DataFrame({"return_period": oep_rp_b, "loss_usd": oep_b}).to_sql(
        "ep_full_oep_b", con, index=False, if_exists="replace")
    con.commit()

    # ---- reconcile --------------------------------------------------------
    recon = pd.read_sql(RECON_SQL, con, params={"tol": RECON_TOLERANCE})
    recon.to_sql("recon_property", con, index=False, if_exists="replace")
    con.commit()
    summary = pd.read_sql(SUMMARY_SQL, con)
    summary.to_sql("recon_summary", con, index=False, if_exists="replace")
    con.commit()

    aal_a = float(pd.read_sql("SELECT SUM(aal_usd) s FROM property_aal_a", con).iloc[0, 0] or 0)
    aal_b = float(prop_aal_b["aal_usd"].sum())

    print("\n" + "-" * 70)
    print("PORTFOLIO-LEVEL RECONCILIATION")
    print("-" * 70)
    print(f"Model A AAL        : USD {aal_a:,.0f}")
    print(f"Model B AAL        : USD {aal_b:,.0f}")
    print(f"difference         : USD {aal_b - aal_a:,.0f} "
          f"({100 * (aal_b - aal_a) / aal_a:+.2f}%)")

    print("\nEXCEPTION REPORT BY CATEGORY")
    print(f"  {'category':<26}{'risks':>7}{'net diff USD':>16}{'% of abs diff':>15}")
    for _, r in summary.iterrows():
        print(f"  {r['exception_category']:<26}{int(r['n_properties']):>7}"
              f"{r['net_diff_usd']:>16,.0f}{r['pct_of_abs_diff']:>14.1f}%")

    flagged = recon[recon["exception_category"] != "WITHIN_TOLERANCE"]
    print(f"\ntotal flagged      : {len(flagged):,} of {len(recon):,} risks "
          f"({100 * len(flagged) / len(recon):.1f}%)")

    conc = recon[recon["cumulative_pct_of_diff"] <= 80].shape[0]
    print(f"concentration      : top {conc} risks drive 80% of absolute AAL difference")

    print("\nTOP 10 VARIANCE DRIVERS")
    print(f"  {'rank':>4} {'prop':>6} {'region':<17}{'constr':<13}"
          f"{'AAL A':>11}{'AAL B':>11}{'diff':>11}  category")
    for _, r in recon.head(10).iterrows():
        print(f"  {int(r['diff_rank']):>4} {int(r['property_id']):>6} "
              f"{r['region']:<17}{r['construction']:<13}"
              f"{r['aal_a']:>11,.0f}{r['aal_b']:>11,.0f}{r['aal_diff']:>11,.0f}"
              f"  {r['exception_category']}")

    # ---- how well did the blind classification do? ------------------------
    check = recon.merge(manifest, on="property_id", how="left")
    print("\n" + "-" * 70)
    print("DETECTION CHECK (classification vs injected manifest)")
    print("-" * 70)
    truth = {
        "MISSING_IN_B": check["is_missing"] == True,       # noqa: E712
        "FX_RATE_BREAK": check["is_fx_break"] == True,     # noqa: E712
        "GEOCODE_SHIFT": check["is_shifted"] == True,      # noqa: E712
    }
    for cat, mask in truth.items():
        injected = int(mask.sum())
        caught = int(((check["exception_category"] == cat) & mask).sum())
        pred = int((check["exception_category"] == cat).sum())
        recall = 100 * caught / injected if injected else 0.0
        precision = 100 * caught / pred if pred else 0.0
        print(f"  {cat:<18} injected {injected:>5}   flagged {pred:>5}   "
              f"recall {recall:>6.1f}%   precision {precision:>6.1f}%")

    print("\nNOTE: GEOCODE_SHIFT is checked before VULNERABILITY_VARIANCE, so a")
    print("risk with both defects is reported under its root cause (the geocode).")
    print("\nPhase 4 complete. Next: phase5_dashboard.py")
    con.close()


if __name__ == "__main__":
    main()
