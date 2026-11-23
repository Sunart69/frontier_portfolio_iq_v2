# Model assumptions and how to calibrate them

The optimization uses only the inputs in the intake workbook. The **Monte Carlo** additionally needs
uncertainty inputs. When the optional uncertainty sheets are absent (or rows are left as written by
`sample`), the engine uses the documented demonstration defaults below. Those defaults are **not
calibrated**: every output says so (the ESTIMATE badge on the dashboard, the footer and appendix A3 of
the CEO deck), and a production recommendation should not rely on them.

## Default initiative uncertainty

r = the initiative's Execution Risk (0–1), c = its Delivery Confidence (0–1).

| Variable | Default | Effect in the simulation |
|---|---|---|
| Effort (hours and cost) multiplier | pert(0.90, 1.00, 1 + 0.8r) | more hours of work, and proportionally more spend |
| Duration multiplier | pert(0.95, 1.00, 1 + 1.0r) | the same work is spread over more months |
| Benefit realization multiplier | pert(0.5 + 0.3c, 0.85 + 0.15c, 1.10) | share of the planned benefit realized |
| Start slip (months) | discrete(0: 1−q, 1: 0.7q, 2: 0.3q), q = min(0.5, 0.05 + 0.6r) | late start |
| Delivery failure | probability 0.5·r·(1−c); forfeits 75% of the benefit | cost is still spent |

## Default capacity uncertainty

| Variable | Default |
|---|---|
| Availability multiplier | pert(0.88, 0.98, 1.03); temporary pools (contractors, pools with an end month or later start) pert(0.75, 0.95, 1.00) |
| Hiring delay (months) | pools that start after month 1: discrete(0: 0.6, 1: 0.25, 2: 0.15) |
| Productivity multiplier | pert(0.92, 1.00, 1.04) |

## Default shared drivers and correlations

| Driver | Distribution | Effect |
|---|---|---|
| INFL — cost inflation | lognormal(1.0, 0.025) | multiplies every initiative's cost |
| CAP-<capability> — shortage | pert(0.85, 1.0, 1.03) | multiplies availability of every pool of that capability |
| REG — regulatory change | bernoulli(0.15) | compliance initiatives take 25% longer when it occurs |
| PLAT — shared platform delay | discrete(0: 0.75, 1: 0.15, 2: 0.10) months | delays shared-platform initiatives (propagates to dependents) |
| VEND-<x> — vendor delay | discrete(0: 0.7, 1: 0.2, 3: 0.1) months | delays every initiative tagged `vendor:<x>` |

Correlations: capability shortages 0.4 with each other (one labour market), 0.2 with inflation.

## Other simulation settings (Simulation Settings sheet or config/decision.yaml)

| Setting | Default | Meaning |
|---|---|---|
| iterations | 10,000 | 1,000 / 5,000 / 25,000 / 50,000 or any number ≥ 100 |
| seed | 20270101 | reproducible; same seed → identical results |
| mode | fixed | fixed plan or adaptive execution |
| budget_tolerance | 5% | management reserve before a cost overrun counts |
| date_grace_months | 1 | grace on planned finish for committed dates (deadlines are hard) |
| min_value_fraction | 80% | minimum acceptable business value, as a share of the plan |
| var_alpha | 95% | VaR / CVaR confidence level |
| contingency_pct / contingency_rate | 10% / $150 per hour | adaptive mode's approved contingency |
| start_gate / max_defer_months | 50% / 3 | adaptive mode's start deferral rule |

## Calibrating for production

1. **Effort and duration.** Fit the ratio actual ÷ planned hours and months from closed initiatives
   (ideally by delivery type and LOB). With 20+ observations use `empirical(...)`; otherwise fit PERT
   min / mode / max to the 5th, 50th and 95th percentiles.
2. **Benefits.** Compare benefits realized 12 months after go-live with the business case.
3. **Failure.** Share of initiatives cancelled or delivered without their primary benefit.
4. **Availability and hiring.** Attrition, vacancy fill times and contractor ramp-up from HR data.
5. **Drivers.** One driver per real shared exposure (a vendor, a platform, a regulator) with its own
   history; estimate correlations from co-movement of overruns, and keep the matrix valid (the engine
   checks it).
6. Put the values in the `Uncertainty Projects`, `Uncertainty Capacity`, `Risk Drivers`,
   `Risk Correlations` and `Simulation Settings` sheets. Rows whose Notes mention "default" stay flagged
   as uncalibrated; replace the note with the data source.

Running more iterations reduces sampling error; it does not make these assumptions more accurate.
