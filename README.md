# Catastrophe Risk Modelling Platform

An end-to-end catastrophe model for tropical cyclone risk in the Bay of Bengal,
built to mirror the actual workflow of a Catastrophe Risk Analyst:

> **build exposure → simulate hazard → compute loss → QA and reconcile → explain to stakeholders**

Everything is synthetic and self-contained. No API keys, no internet, no
downloads. The whole pipeline runs in under a minute.

---

## Headline results

From a reference run (fully reproducible — all random draws are seeded):

| Metric | Value |
|---|---|
| Portfolio | 2,000 synthetic risks, USD 1,693m TIV |
| Simulation | 1,000 years, 2,465 cyclones, 1,671 affecting the portfolio |
| **AAL** | **USD 13.5m** (0.799% of TIV) |
| Annual loss volatility | SD USD 29.3m, coefficient of variation **2.16** |
| Loss-free years | 287 of 1,000 |
| **100-yr OEP** | **USD 128.0m** |
| **250-yr OEP** | **USD 168.7m** |
| Model A vs Model B | AAL differs by **+10.90%** |
| QA exceptions raised | 1,175 of 2,000 risks, split across 4 root causes |
| Break detection | 100% / 98.7% / 92.3% recall at 100% precision |

---

## What each phase demonstrates

| Phase | File | Skill demonstrated |
|---|---|---|
| 0 | `phase0_setup.py` | Environment control, reproducibility |
| 1 | `phase1_exposure.py` | Exposure data modelling, SQL schema, geospatial flagging |
| 2 | `phase2_hazard.py` | **Stochastic modelling** — Monte Carlo, correlated random walk, wind field physics |
| 3 | `phase3_loss.py` | **Loss outputs** — vulnerability curves, AAL, OEP/AEP, return periods |
| 4 | `phase4_reconciliation.py` | **Quality check / reconciliation** — SQL CTEs, window functions, exception reporting |
| 5 | `phase5_dashboard.py` | **Interpretation** — visualisation and explaining results to non-modellers |

Run order and Kaggle instructions: see [`RUN_ORDER.md`](RUN_ORDER.md).

---

## The model

### Hazard (Phase 2)

- **Frequency** — cyclones per year ~ Poisson(λ = 2.5).
- **Track** — a correlated random walk. Bearing and translation speed persist
  step to step with Gaussian perturbations, which produces realistic track
  bundles without fitting a full historical transition matrix.
- **Intensity** — over sea, Vmax relaxes exponentially toward a storm-specific
  potential intensity. Over land it decays on the Kaplan–DeMaria form
  `Vb + (V0 − Vb)·exp(−α·t)` with α = 0.095/hr.
- **Wind field** — parametric radial profile: linear inside the radius of
  maximum winds, decaying as `(Rmax/r)^0.6` outside it.

Each property's hazard for an event is its maximum wind across all track steps.

### Vulnerability and loss (Phase 3)

Logistic damage functions by construction class, rebased so damage is exactly
zero at the 25 m/s threshold:

```
MDR(v) = [σ(v) − σ(25)] / [1 − σ(25)],   σ(v) = 1 / (1 + e^(−k(v − v50)))
```

| Class | v50 (m/s) | Interpretation |
|---|---|---|
| RC_FRAME | 68 | Reinforced concrete, most resilient |
| MASONRY | 55 | Typical mid-range construction |
| LIGHT_METAL | 45 | Industrial sheds, vulnerable |
| TIMBER | 40 | Most vulnerable |

Financial terms: `net = clip(TIV × MDR − deductible, 0, TIV)` at a 5% deductible.

**The detail that matters:** loss-free years are carried into the EP
calculation. 287 of 1,000 years produce no loss; dropping them would inflate
every single return period. This is a common and material error.

### Reconciliation (Phase 4)

Model B is the same portfolio with four deliberately injected defects — the
four that genuinely cause reconciliation queries in practice:

| Defect | Injected | Effect |
|---|---|---|
| Vulnerability assumption | MASONRY & TIMBER v50 lowered 4 m/s | Systematic, many risks slightly |
| Coarse geocoding | 15% rounded to 1 dp (~11 km) | Risks move relative to the wind field |
| Stale FX rate | Non-USD risks +10% TIV | Overstated exposure |
| Failed load | 1% dropped entirely | Missing exposure |

The reconciliation SQL classifies breaks **blind** — purely from the two
exposure files and the two loss outputs, using CTEs, `RANK()`, and windowed
cumulative sums for Pareto concentration. Only afterwards is the classification
scored against the manifest of what was actually injected.

Result: **100% precision** across all three detectable defect types, with
recall of 100% (missing), 98.7% (FX) and 92.3% (geocode).

Both shortfalls are explainable rather than bugs, which is the point:
- 23 of 300 regeocoded risks already sat on a 1-decimal coordinate, so rounding
  moved them nowhere and left no detectable trace.
- 2 of 149 FX risks were also in the dropped set, so they surface under
  `MISSING_IN_B` — the classifier reports root cause by precedence.

---

## A finding worth discussing

`LIGHT_METAL` is only **9.9% of the portfolio by count** but drives
**70% of total AAL** (USD 9.45m of 13.5m).

That is not a bug — it is the interaction of two factors: light-metal
construction is concentrated in industrial occupancy (high TIV) *and* it is the
second most vulnerable class. It is a compact demonstration of why AAL must be
decomposed by class and normalised by exposure (the loss-cost panel in
`fig5`) rather than read as a single portfolio number.

---

## Figures

| File | Shows |
|---|---|
| `fig1_exposure_map.png` | Portfolio with flood zone and coastline, sized by TIV |
| `fig2_tracks.png` | 200 sampled stochastic tracks coloured by peak intensity |
| `fig3_vulnerability_curves.png` | Damage functions, and Model A vs B |
| `fig4_ep_curve.png` | OEP and AEP curves with 100/250-yr markers |
| `fig5_aal_breakdown.png` | AAL by region, by construction, and loss cost |
| `fig6_reconciliation.png` | Breaks by root cause, and Pareto concentration |
| `fig7_ep_comparison.png` | Model A vs B across return periods |

---

## Limitations

Stated deliberately — interrogating model uncertainty is core to the role, and
a model whose weaknesses you cannot name is one you do not understand.

1. **Not calibrated to observations.** Track and intensity parameters are
   plausible for the basin but are not fitted to IBTrACS or any historical
   record. Absolute loss numbers should not be read as real risk estimates.
2. **Vulnerability curves are illustrative.** They are smooth logistic
   functions chosen by construction class, not derived from claims data. In a
   real engagement this is the single largest source of loss uncertainty.
3. **Symmetric wind field.** The radial profile ignores the asymmetry caused by
   the storm's forward motion, which in reality makes the right-front quadrant
   materially more damaging in this hemisphere.
4. **No storm surge or rainfall.** The flood layer is a static illustrative
   polygon used for exposure flagging — it is not a hazard model and produces
   no loss. Only wind loss is modelled.
5. **Simplified financial terms.** A flat 5% deductible with no limits,
   sub-limits, reinsurance structures or demand surge.
6. **Distance-to-coast is an east-west approximation**, which degrades where
   the coastline turns sharply near Odisha.
7. **Sampling uncertainty.** 1,000 years means the 1-in-1000 return period rests
   on a single simulated year and is not credible. Treat anything beyond about
   the 250-year return period as indicative only.

---

## Resume line

> Built an end-to-end catastrophe risk modelling platform in Python and SQL:
> 1,000-year stochastic cyclone simulation (2,465 events) over a 2,000-property
> portfolio, producing EP curves, AAL and 250-year OEP, plus a SQL
> reconciliation engine using CTEs and window functions that classifies loss
> discrepancies between model variants by root cause at 100% precision.

## Interview talking points

- Why loss-free years must stay in the EP calculation, and what happens if they don't.
- Why a coefficient of variation of 2.16 is the actual argument for buying reinsurance.
- Why "1-in-250" is an annual probability, not a schedule — and that it implies
  roughly a 3.9% chance across a 10-year treaty.
- Why the reconciliation separates *data defects* (fixable) from *model
  uncertainty* (genuine), and why that distinction is what a broker actually wants.
- Why AAL concentrated in 9.9% of risks by count is a portfolio-management finding,
  not a modelling error.
