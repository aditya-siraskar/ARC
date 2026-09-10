"""PHASE 3 — Loss engine (Model A).

Converts hazard into money. For every (event, property) pair in the footprint:

    MDR   = vulnerability_curve(peak_wind, construction_class)
    gross = TIV * MDR
    net   = clip(gross - deductible, 0, TIV)

then aggregates upward into the standard cat modelling outputs:

    AAL   Average Annual Loss = total simulated loss / number of simulated years
    OEP   Occurrence EP  - distribution of the largest single event in a year
    AEP   Aggregate EP   - distribution of total annual loss

The critical detail is that loss-free years must be included in the EP
calculation. Around 20% of simulated years produce no loss at all, and dropping
them would bias every return period materially.

MODEL NOTE: the vulnerability curves are smooth logistic functions chosen to be
plausible by construction class. They are NOT calibrated to claims data. In a
real engagement these come from the vendor model and are the single largest
source of loss uncertainty.
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

WIND_THRESHOLD_MS = 25.0

# Vulnerability parameters: v50 is the wind speed at which the underlying
# logistic reaches half damage; k controls how sharply damage ramps up.
VULN_MODEL_A = {
    "RC_FRAME":    {"v50": 68.0, "k": 0.11},
    "MASONRY":     {"v50": 55.0, "k": 0.13},
    "LIGHT_METAL": {"v50": 45.0, "k": 0.15},
    "TIMBER":      {"v50": 40.0, "k": 0.16},
}

REPORT_RETURN_PERIODS = [5, 10, 25, 50, 100, 200, 250, 500, 1000]


def mean_damage_ratio(wind_ms, construction, params):
    """Vectorised MDR. Rebased so damage is exactly zero at the threshold."""
    wind_ms = np.asarray(wind_ms, dtype=float)
    v50 = np.array([params[c]["v50"] for c in construction])
    k = np.array([params[c]["k"] for c in construction])

    sig = 1.0 / (1.0 + np.exp(-k * (wind_ms - v50)))
    sig_thresh = 1.0 / (1.0 + np.exp(-k * (WIND_THRESHOLD_MS - v50)))
    mdr = (sig - sig_thresh) / (1.0 - sig_thresh)
    return np.clip(mdr, 0.0, 1.0)


def compute_losses(footprint, exposure, params):
    """Join hazard to exposure and apply the damage and financial terms."""
    df = footprint.merge(exposure, on="property_id", how="inner")
    df["mdr"] = mean_damage_ratio(
        df["peak_wind_ms"].to_numpy(), df["construction"].tolist(), params
    )
    df["gross_loss_usd"] = df["tiv_usd"] * df["mdr"]
    deductible = df["tiv_usd"] * df["deductible_frac"]
    df["net_loss_usd"] = np.clip(df["gross_loss_usd"] - deductible, 0.0, df["tiv_usd"])
    return df[df["net_loss_usd"] > 0].copy()


def build_year_loss_table(losses, catalog, n_years):
    """Annual aggregate and annual maximum loss, including loss-free years."""
    event_loss = (
        losses.groupby("event_id")
        .agg(event_loss_usd=("net_loss_usd", "sum"),
             n_risks_affected=("property_id", "count"))
        .reset_index()
        .merge(catalog[["event_id", "year"]], on="event_id", how="left")
    )

    per_year = (
        event_loss.groupby("year")
        .agg(agg_loss_usd=("event_loss_usd", "sum"),
             max_event_loss_usd=("event_loss_usd", "max"),
             n_events=("event_id", "count"))
        .reset_index()
    )

    # Reindex onto the full simulated period so zero-loss years are present.
    all_years = pd.DataFrame({"year": np.arange(1, n_years + 1)})
    per_year = all_years.merge(per_year, on="year", how="left").fillna(0.0)
    return event_loss, per_year


def ep_curve(values, n_years):
    """Standard EP curve. Returns (return_period, loss) sorted descending."""
    v = np.sort(np.asarray(values, dtype=float))[::-1]
    ranks = np.arange(1, len(v) + 1)
    exceedance = ranks / (n_years + 1.0)
    return 1.0 / exceedance, v


def loss_at_return_period(rp_arr, loss_arr, target_rp):
    """Interpolate the EP curve at a target return period (log-space in RP)."""
    order = np.argsort(rp_arr)
    return float(np.interp(np.log(target_rp), np.log(rp_arr[order]), loss_arr[order]))


def main():
    print("=" * 70)
    print("PHASE 3: LOSS ENGINE (MODEL A)")
    print("=" * 70)

    con = sqlite3.connect(DB)
    exposure = pd.read_sql(
        "SELECT property_id, region, city, construction, occupancy, "
        "tiv_usd, deductible_frac FROM exposure", con
    )
    footprint = pd.read_sql("SELECT * FROM hazard_footprint", con)
    catalog = pd.read_sql("SELECT event_id, year FROM event_catalog", con)
    n_years = int(pd.read_sql(
        "SELECT value FROM sim_meta WHERE key='n_years'", con
    ).iloc[0, 0])

    print(f"footprint rows     : {len(footprint):,}")
    print(f"simulated years    : {n_years:,}")

    losses = compute_losses(footprint, exposure, VULN_MODEL_A)
    event_loss, year_loss = build_year_loss_table(losses, catalog, n_years)

    total_tiv = float(exposure["tiv_usd"].sum())
    aal = float(year_loss["agg_loss_usd"].mean())
    sd = float(year_loss["agg_loss_usd"].std())

    oep_rp, oep_loss = ep_curve(year_loss["max_event_loss_usd"], n_years)
    aep_rp, aep_loss = ep_curve(year_loss["agg_loss_usd"], n_years)

    rp_rows = []
    for rp in REPORT_RETURN_PERIODS:
        rp_rows.append(
            {
                "return_period": rp,
                "exceedance_prob": 1.0 / rp,
                "oep_usd": loss_at_return_period(oep_rp, oep_loss, rp),
                "aep_usd": loss_at_return_period(aep_rp, aep_loss, rp),
            }
        )
    rp_table = pd.DataFrame(rp_rows)

    metrics = pd.DataFrame(
        [
            {"metric": "total_tiv_usd", "value": total_tiv},
            {"metric": "aal_usd", "value": aal},
            {"metric": "aal_pct_of_tiv", "value": 100.0 * aal / total_tiv},
            {"metric": "annual_loss_sd_usd", "value": sd},
            {"metric": "coeff_of_variation", "value": sd / aal if aal else 0.0},
            {"metric": "n_years", "value": float(n_years)},
            {"metric": "loss_free_years", "value": float((year_loss["agg_loss_usd"] == 0).sum())},
        ]
    )

    # Per-property AAL is what Phase 4 reconciles against.
    prop_aal = (
        losses.groupby("property_id")["net_loss_usd"].sum().div(n_years).reset_index()
        .rename(columns={"net_loss_usd": "aal_usd"})
    )

    losses.to_sql("model_a_losses", con, index=False, if_exists="replace")
    event_loss.to_sql("event_loss_a", con, index=False, if_exists="replace")
    year_loss.to_sql("year_loss_a", con, index=False, if_exists="replace")
    rp_table.to_sql("ep_curve_a", con, index=False, if_exists="replace")
    metrics.to_sql("metrics_a", con, index=False, if_exists="replace")
    prop_aal.to_sql("property_aal_a", con, index=False, if_exists="replace")
    con.execute("CREATE INDEX IF NOT EXISTS ix_aal_a ON property_aal_a(property_id)")

    # Full EP curves stored for plotting in Phase 5.
    pd.DataFrame({"return_period": oep_rp, "loss_usd": oep_loss}).to_sql(
        "ep_full_oep_a", con, index=False, if_exists="replace"
    )
    pd.DataFrame({"return_period": aep_rp, "loss_usd": aep_loss}).to_sql(
        "ep_full_aep_a", con, index=False, if_exists="replace"
    )
    con.commit()
    con.close()

    print(f"loss records       : {len(losses):,}")
    print(f"\ntotal TIV          : USD {total_tiv:,.0f}")
    print(f"AAL                : USD {aal:,.0f}  ({100 * aal / total_tiv:.3f}% of TIV)")
    print(f"annual loss SD     : USD {sd:,.0f}")
    print(f"coeff of variation : {sd / aal:.2f}")
    print(f"loss-free years    : {int((year_loss['agg_loss_usd'] == 0).sum()):,} of {n_years:,}")

    print("\nReturn period losses (USD m):")
    print(f"  {'RP (yr)':>8}  {'Exceed.':>8}  {'OEP':>12}  {'AEP':>12}")
    for _, r in rp_table.iterrows():
        print(f"  {int(r['return_period']):>8}  {r['exceedance_prob']:>7.2%}  "
              f"{r['oep_usd'] / 1e6:>12,.2f}  {r['aep_usd'] / 1e6:>12,.2f}")

    print("\nAAL by region (USD):")
    # `losses` already carries region from the exposure join in compute_losses.
    reg = losses.groupby("region")["net_loss_usd"].sum().div(n_years).sort_values(ascending=False)
    for region, v in reg.items():
        print(f"  {region:<18} {v:>14,.0f}")

    print("\nAAL by construction (USD):")
    con_aal = losses.groupby("construction")["net_loss_usd"].sum().div(n_years).sort_values(ascending=False)
    for c, v in con_aal.items():
        print(f"  {c:<14} {v:>14,.0f}")

    print("\nPhase 3 complete. Next: phase4_reconciliation.py")


if __name__ == "__main__":
    main()
