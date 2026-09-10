"""PHASE 5 — Visualisation and explainability.

Produces the seven figures that carry the story, then prints a plain-English
interpretation of every headline number. The interpretation block is the part
that matters in an interview: it is the difference between "I ran a simulation"
and "I can explain what the output means to a broker."

All figures are written to the figures/ directory and also displayed inline
when run inside a notebook.
"""

import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import matplotlib

if not os.environ.get("DISPLAY") and "ipykernel" not in sys.modules:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon

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
VULN_A = {
    "RC_FRAME":    {"v50": 68.0, "k": 0.11},
    "MASONRY":     {"v50": 55.0, "k": 0.13},
    "LIGHT_METAL": {"v50": 45.0, "k": 0.15},
    "TIMBER":      {"v50": 40.0, "k": 0.16},
}
VULN_B = {
    "RC_FRAME":    {"v50": 68.0, "k": 0.11},
    "MASONRY":     {"v50": 51.0, "k": 0.13},
    "LIGHT_METAL": {"v50": 45.0, "k": 0.15},
    "TIMBER":      {"v50": 36.0, "k": 0.16},
}

INK = "#1f2933"
ACCENT = "#0b6e99"
WARM = "#c1440e"
GREY = "#8896a4"
PALETTE = ["#0b6e99", "#c1440e", "#4a7c59", "#b08968", "#6d597a"]

plt.rcParams.update({
    "figure.dpi": 110,
    "savefig.dpi": 140,
    "savefig.bbox": "tight",
    "axes.edgecolor": GREY,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 10,
})


def save(fig, name):
    path = os.path.join(FIGDIR, name)
    fig.savefig(path)
    print(f"  saved {name}")
    return path


def mdr_curve(wind, v50, k):
    sig = 1.0 / (1.0 + np.exp(-k * (wind - v50)))
    st = 1.0 / (1.0 + np.exp(-k * (WIND_THRESHOLD_MS - v50)))
    return np.clip((sig - st) / (1.0 - st), 0, 1)


def fig_exposure_map(con):
    exp = pd.read_sql("SELECT * FROM exposure", con)
    coast = pd.read_sql("SELECT * FROM coastline", con)
    poly = pd.read_sql("SELECT * FROM flood_zone_polygon ORDER BY vertex_order", con)

    fig, ax = plt.subplots(figsize=(7.5, 8))
    ax.add_patch(MplPolygon(poly[["longitude", "latitude"]].to_numpy(),
                            closed=True, facecolor="#7fb3d5", alpha=0.30,
                            edgecolor="#2e86c1", lw=1.2, zorder=1,
                            label="Delta inundation zone"))
    ax.plot(coast["longitude"], coast["latitude"], color=INK, lw=1.6,
            zorder=3, label="Coastline")

    size = 6 + 60 * (exp["tiv_usd"] / exp["tiv_usd"].quantile(0.99)).clip(0, 1)
    flooded = exp["in_flood_zone"] == 1
    ax.scatter(exp.loc[~flooded, "longitude"], exp.loc[~flooded, "latitude"],
               s=size[~flooded], c=ACCENT, alpha=0.45, lw=0, zorder=2,
               label="Wind-exposed only")
    ax.scatter(exp.loc[flooded, "longitude"], exp.loc[flooded, "latitude"],
               s=size[flooded], c=WARM, alpha=0.65, lw=0, zorder=4,
               label="Wind + flood exposed")

    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_title("Portfolio exposure — 2,000 synthetic risks\nmarker size ∝ TIV")
    ax.legend(loc="upper left", frameon=False, fontsize=8)
    ax.set_aspect(1.0)
    return save(fig, "fig1_exposure_map.png")


def fig_tracks(con):
    tracks = pd.read_sql("SELECT * FROM tracks ORDER BY event_id, step", con)
    coast = pd.read_sql("SELECT * FROM coastline", con)
    sample = pd.Series(tracks["event_id"].unique()).sample(
        min(200, tracks["event_id"].nunique()), random_state=1)

    fig, ax = plt.subplots(figsize=(7.5, 8))
    cmap = plt.get_cmap("YlOrRd")
    for eid in sample:
        g = tracks[tracks["event_id"] == eid]
        ax.plot(g["longitude"], g["latitude"],
                color=cmap(min(g["vmax_ms"].max() / 90.0, 1.0)),
                lw=0.8, alpha=0.55, zorder=2)
    ax.plot(coast["longitude"], coast["latitude"], color=INK, lw=1.8, zorder=3)

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(20, 90))
    fig.colorbar(sm, ax=ax, label="Peak Vmax (m/s)", shrink=0.75)
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_title(f"Stochastic cyclone catalog — {len(sample)} sampled tracks\n"
                 "colour = peak lifetime intensity")
    ax.set_aspect(1.0)
    return save(fig, "fig2_tracks.png")


def fig_vulnerability():
    wind = np.linspace(20, 95, 400)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)

    for i, (name, p) in enumerate(VULN_A.items()):
        axes[0].plot(wind, mdr_curve(wind, p["v50"], p["k"]),
                     color=PALETTE[i], lw=2, label=name)
    axes[0].set_title("Model A vulnerability curves")
    axes[0].set_xlabel("1-min sustained wind (m/s)")
    axes[0].set_ylabel("Mean damage ratio")
    axes[0].legend(frameon=False, fontsize=8)
    axes[0].axvline(WIND_THRESHOLD_MS, color=GREY, ls=":", lw=1)

    for i, name in enumerate(VULN_A):
        axes[1].plot(wind, mdr_curve(wind, VULN_A[name]["v50"], VULN_A[name]["k"]),
                     color=PALETTE[i], lw=2, alpha=0.45)
        axes[1].plot(wind, mdr_curve(wind, VULN_B[name]["v50"], VULN_B[name]["k"]),
                     color=PALETTE[i], lw=2, ls="--", label=f"{name} (B)")
    axes[1].set_title("Model A (solid) vs Model B (dashed)\nB is more damaging for MASONRY and TIMBER")
    axes[1].set_xlabel("1-min sustained wind (m/s)")
    axes[1].legend(frameon=False, fontsize=8)
    axes[1].axvline(WIND_THRESHOLD_MS, color=GREY, ls=":", lw=1)

    fig.suptitle("Vulnerability: how wind speed becomes damage", y=1.02, fontweight="bold")
    return save(fig, "fig3_vulnerability_curves.png")


def fig_ep_curve(con):
    oep = pd.read_sql("SELECT * FROM ep_full_oep_a", con)
    aep = pd.read_sql("SELECT * FROM ep_full_aep_a", con)
    rp_tab = pd.read_sql("SELECT * FROM ep_curve_a", con)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(aep["return_period"], aep["loss_usd"] / 1e6, color=WARM, lw=2,
            label="AEP (annual aggregate)")
    ax.plot(oep["return_period"], oep["loss_usd"] / 1e6, color=ACCENT, lw=2,
            label="OEP (largest single event)")

    for rp in [100, 250]:
        row = rp_tab[rp_tab["return_period"] == rp].iloc[0]
        ax.axvline(rp, color=GREY, ls=":", lw=1)
        ax.annotate(f"{rp}-yr OEP\nUSD {row['oep_usd'] / 1e6:,.1f}m",
                    xy=(rp, row["oep_usd"] / 1e6),
                    xytext=(rp * 0.30, row["oep_usd"] / 1e6 * 1.35),
                    fontsize=8, color=INK,
                    arrowprops=dict(arrowstyle="->", color=GREY, lw=0.9))

    ax.set_xscale("log")
    ax.set_xlabel("Return period (years, log scale)")
    ax.set_ylabel("Loss (USD millions)")
    ax.set_title("Exceedance probability curve — Model A")
    ax.legend(frameon=False)
    ax.grid(alpha=0.25, which="both", ls=":")
    return save(fig, "fig4_ep_curve.png")


def fig_aal_breakdown(con):
    n_years = int(pd.read_sql("SELECT value FROM sim_meta WHERE key='n_years'", con).iloc[0, 0])
    losses = pd.read_sql("SELECT region, construction, net_loss_usd FROM model_a_losses", con)
    exp = pd.read_sql("SELECT region, construction, tiv_usd FROM exposure", con)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))

    reg = losses.groupby("region")["net_loss_usd"].sum().div(n_years).sort_values()
    axes[0].barh(reg.index, reg.to_numpy() / 1e3, color=ACCENT)
    axes[0].set_xlabel("AAL (USD thousands)")
    axes[0].set_title("AAL by region")

    cons = losses.groupby("construction")["net_loss_usd"].sum().div(n_years).sort_values()
    axes[1].barh(cons.index, cons.to_numpy() / 1e3, color=WARM)
    axes[1].set_xlabel("AAL (USD thousands)")
    axes[1].set_title("AAL by construction class")

    # Loss cost normalises AAL by TIV, which is how you compare unequal books.
    tiv_r = exp.groupby("region")["tiv_usd"].sum()
    aal_r = losses.groupby("region")["net_loss_usd"].sum().div(n_years)
    cost = (1e4 * aal_r / tiv_r).sort_values()
    axes[2].barh(cost.index, cost.to_numpy(), color="#4a7c59")
    axes[2].set_xlabel("Loss cost (USD per 10,000 TIV)")
    axes[2].set_title("Loss cost by region\n(AAL normalised by exposure)")

    fig.suptitle("Where the risk actually sits", y=1.03, fontweight="bold")
    return save(fig, "fig5_aal_breakdown.png")


def fig_reconciliation(con):
    summary = pd.read_sql("SELECT * FROM recon_summary", con)
    recon = pd.read_sql(
        "SELECT abs_diff, cumulative_pct_of_diff FROM recon_property ORDER BY abs_diff DESC", con)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))

    s = summary.sort_values("abs_diff_usd")
    colors = ["#4a7c59" if c == "WITHIN_TOLERANCE" else WARM
              for c in s["exception_category"]]
    axes[0].barh(s["exception_category"], s["abs_diff_usd"], color=colors)
    axes[0].set_xlabel("Absolute AAL difference (USD)")
    axes[0].set_title("Reconciliation breaks by root cause")
    for y, (v, n) in enumerate(zip(s["abs_diff_usd"], s["n_properties"])):
        axes[0].text(v, y, f"  {int(n)} risks", va="center", fontsize=8, color=INK)

    rank = np.arange(1, len(recon) + 1)
    axes[1].plot(rank, recon["cumulative_pct_of_diff"], color=ACCENT, lw=2)
    axes[1].axhline(80, color=WARM, ls="--", lw=1)
    n80 = int((recon["cumulative_pct_of_diff"] <= 80).sum())
    axes[1].axvline(n80, color=GREY, ls=":", lw=1)
    axes[1].annotate(f"top {n80} risks = 80% of variance",
                     xy=(n80, 80), xytext=(n80 * 2.2, 55), fontsize=9,
                     arrowprops=dict(arrowstyle="->", color=GREY, lw=0.9))
    axes[1].set_xscale("log")
    axes[1].set_xlabel("Risks ranked by absolute difference (log scale)")
    axes[1].set_ylabel("Cumulative % of total difference")
    axes[1].set_title("Variance concentration (Pareto)")
    axes[1].grid(alpha=0.25, ls=":")

    fig.suptitle("Model A vs Model B — QA exception analysis", y=1.03, fontweight="bold")
    return save(fig, "fig6_reconciliation.png")


def fig_ep_comparison(con):
    a = pd.read_sql("SELECT * FROM ep_full_oep_a", con)
    b = pd.read_sql("SELECT * FROM ep_full_oep_b", con)
    ra = pd.read_sql("SELECT * FROM ep_curve_a", con)
    rb = pd.read_sql("SELECT * FROM ep_curve_b", con)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    axes[0].plot(a["return_period"], a["loss_usd"] / 1e6, color=ACCENT, lw=2, label="Model A")
    axes[0].plot(b["return_period"], b["loss_usd"] / 1e6, color=WARM, lw=2, ls="--", label="Model B")
    axes[0].set_xscale("log")
    axes[0].set_xlabel("Return period (years, log scale)")
    axes[0].set_ylabel("OEP loss (USD millions)")
    axes[0].set_title("OEP curve — Model A vs Model B")
    axes[0].legend(frameon=False)
    axes[0].grid(alpha=0.25, which="both", ls=":")

    m = ra.merge(rb, on="return_period", suffixes=("_a", "_b"))
    delta = 100 * (m["oep_usd_b"] - m["oep_usd_a"]) / m["oep_usd_a"]
    axes[1].bar(m["return_period"].astype(str), delta, color=WARM)
    axes[1].axhline(0, color=INK, lw=1)
    axes[1].set_xlabel("Return period (years)")
    axes[1].set_ylabel("Model B vs A (%)")
    axes[1].set_title("Loss difference by return period\n(does the gap widen in the tail?)")
    axes[1].grid(alpha=0.25, axis="y", ls=":")

    fig.suptitle("Reconciling modelled loss outputs across return periods",
                 y=1.03, fontweight="bold")
    return save(fig, "fig7_ep_comparison.png")


def interpretation(con):
    m = pd.read_sql("SELECT * FROM metrics_a", con).set_index("metric")["value"]
    rp = pd.read_sql("SELECT * FROM ep_curve_a", con).set_index("return_period")
    summary = pd.read_sql("SELECT * FROM recon_summary", con)
    aal_b = float(pd.read_sql("SELECT SUM(aal_usd) s FROM property_aal_b", con).iloc[0, 0] or 0)

    tiv, aal = m["total_tiv_usd"], m["aal_usd"]
    print("\n" + "=" * 70)
    print("INTERPRETATION — what these numbers mean")
    print("=" * 70)

    print(f"""
PORTFOLIO
  {int(m['n_years']):,} simulated years against USD {tiv / 1e6:,.0f}m of insured value.
  {int(m['loss_free_years']):,} years produced no loss at all. Those zero years are
  included in the EP calculation; excluding them is a classic error that
  inflates every return period.

AVERAGE ANNUAL LOSS
  AAL = USD {aal:,.0f}, i.e. {m['aal_pct_of_tiv']:.3f}% of TIV.
  This is the pure technical premium for this peril: the amount you would
  need to collect each year, on average, to break even before expenses,
  profit and the cost of capital.

VOLATILITY
  Standard deviation of annual loss is USD {m['annual_loss_sd_usd']:,.0f},
  a coefficient of variation of {m['coeff_of_variation']:.2f}. A CV above 1 means the
  year-to-year swing exceeds the mean, which is exactly why catastrophe
  risk needs reinsurance rather than being carried on the balance sheet.

RETURN PERIODS (OEP — largest single event in a year)
  1-in-100  : USD {rp.loc[100, 'oep_usd'] / 1e6:,.1f}m
  1-in-250  : USD {rp.loc[250, 'oep_usd'] / 1e6:,.1f}m
  A 1-in-250 year does not mean "once every 250 years". It means a {1 / 250:.1%}
  chance in any given year. Over a 10-year treaty the chance of at least
  one such event is about {100 * (1 - (1 - 1 / 250) ** 10):.1f}%.

  The gap between AEP and OEP shows how much of the risk comes from
  multiple events in one season rather than a single large one.

MODEL A vs MODEL B
  Portfolio AAL moves from USD {aal:,.0f} to USD {aal_b:,.0f}
  ({100 * (aal_b - aal) / aal:+.2f}%) once Model B's assumptions are applied.""")

    flagged = summary[summary["exception_category"] != "WITHIN_TOLERANCE"]
    print("  The reconciliation attributes that movement to:")
    for _, r in flagged.iterrows():
        print(f"    - {r['exception_category']:<24} {int(r['n_properties']):>5} risks, "
              f"{r['pct_of_abs_diff']:>5.1f}% of absolute difference")
    n_defect = int(flagged["exception_category"].isin(
        ["GEOCODE_SHIFT", "FX_RATE_BREAK", "MISSING_IN_B", "TIV_MISMATCH"]).sum())
    print(f"""
  This is the answer a broker actually wants when two models disagree:
  not "the models differ by {100 * (aal_b - aal) / aal:.1f}%" but "here are the {len(flagged)} reasons,
  ranked, and {n_defect} of them are data defects we can fix rather than
  genuine model uncertainty."
""")


def main():
    print("=" * 70)
    print("PHASE 5: VISUALISATION AND EXPLAINABILITY")
    print("=" * 70)
    con = sqlite3.connect(DB)

    print("\ngenerating figures:")
    fig_exposure_map(con)
    fig_tracks(con)
    fig_vulnerability()
    fig_ep_curve(con)
    fig_aal_breakdown(con)
    fig_reconciliation(con)
    fig_ep_comparison(con)

    interpretation(con)
    con.close()

    print(f"figures written to: {FIGDIR}")
    print("\nPipeline complete.")

    if "ipykernel" in sys.modules:
        plt.show()


if __name__ == "__main__":
    main()
