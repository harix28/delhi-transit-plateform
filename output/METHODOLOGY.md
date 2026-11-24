# Phase 1 technical note: crowdedness-aware bus journey planner

Problem 2, Transport Stack Hackathon 2026. Status: working research prototype.

## Contribution

A reproducible pipeline estimates historical bus crowding by route, direction, stop-to-stop stretch, weekday/weekend and half-hour. A static-GTFS planner compares candidate journeys by travel time or historical crowding. Live GPS is a separate position feed; it does not measure passenger occupancy.

## Data and selection

The supplied October 2026 archive contains about 2.56 million ticket records. The saved evaluation retains **1,317,662 usable records**, including a usable vehicle ID and valid journey fields. **891,048** match GTFS route/stop sequences: **67.62% of usable records**, not of all raw records. Dropped and unmatched records are excluded, not counted as empty buses. Vehicle IDs are present, not independently verified.

Names are normalized and matched using aliases and route direction codes. Ambiguous matches currently select a candidate; repeated stop names and route variants require investigation. GTFS uses one representative stop sequence per route/direction, so short workings and branches may be lost.

## Load reconstruction

Tickets are grouped by vehicle, normalized route and leg. A gap over 45 minutes starts a new inferred trip. These groups are not verified operational trips; sparse sales can split one trip and successive journeys can merge. Tickets without a usable vehicle ID are dropped.

Each ticket contributes its passenger count to all segments between the matched boarding and alighting stops. Segment time uses an assumed 2.5-minute offset from the inferred trip's first observed stop. Means include only inferred trips with crossing tickets. Unobserved trips/segments are not measured zero loads; this conditional sampling can bias estimates.

The configured real-data estimate is ticket-derived mean load multiplied by **20**. This multiplier is an **unvalidated assumption**, not a measured correction for all passengers. Sensitivity is reported in `outputs/uplift_sensitivity.csv`. It must be calibrated against independent counts or defensible route/time-specific sampling evidence before occupancy accuracy claims.

## Capacity and confidence assumptions

- Seats class: scaled load at most **35**.
- Standing class: scaled load above 35 and at most **65**.
- Crowded class: scaled load above 65.
- Sample confidence: medium at **4** inferred trips; high at **15**.

These are configured common thresholds, not verified capacities for every bus. Historical AC evidence does not establish seating capacity. Confidence represents sample count, not a calibrated probability of getting a seat. Normal-approximation intervals omit missing-ticket bias, capacity uncertainty, trip reconstruction error and uncertain alighting.

## Journey integration

The planner enforces an 800 m access-walking limit, uses a timetable when present and compares direct and bounded one-transfer candidates. Transfer buffers are 2 minutes. Search is bounded and is not guaranteed globally optimal. Walking distances are straight lines and pedestrian accessibility is unverified.

No matching scheduled service produces no journey. Without a timetable, timings are labelled estimates. Timetable values are not traffic-aware live ETAs. Route crowding queries resolve an exact route/direction and the selected India-time departure; missing stretches are explicitly unknown. Journey cards show coverage and sample confidence. Current journey summaries use the boarding-time bin across the ride; exact per-segment passage times remain future work.

## Temporal evaluation and interpretation

The saved evaluator trains on October 1-24 and holds out October 25-31, 2026. It compares matching route/direction/segment/day-type/half-hour bins with at least five inferred trips in each split.

| Diagnostic | Value | Meaning |
| --- | --- | --- |
| Evaluated bins | 78,588 | Qualified overlapping bins |
| Total test bins | 849,259 | All held-out bins |
| Evaluated share of test bins | 9.25% | Selection coverage, not city-network coverage |
| MAE | 7.529 | Difference between scaled ticket-derived means |
| RMSE | 10.536 | Same target and units |
| Label agreement | 72.81% | Agreement with ticket-derived held-out labels |
| Always-seats baseline | 59.76% | Most frequent training label applied to every evaluated bin |

**These metrics are not measured occupancy accuracy.** The audit recomputes metrics from saved holdout artifacts; it does not repeat the complete raw-ticket evaluation. It verifies the sample rule and that saved scaling matches the current configuration, and records the comparison file's SHA-256 in `outputs/validation_audit.json`.

At uplift 1, all evaluated test bins fall in the seats class and label agreement is 100%. At uplift 20, agreement is 72.81%. This demonstrates why high class agreement alone does not validate crowding detection. Do not select a multiplier just to maximize this score.

## Pattern diagnostics

`outputs/pattern_diagnostics.csv` reports observed-bin conditional mean scaled ticket loads for morning 08:00-10:00, midday 12:00-15:00 and evening 17:00-20:00, separately by weekday/weekend. The weekday means are approximately 27.80, 24.96 and 26.96. These are descriptive sampled-bin means; they are not total ridership or independent validation against known peak conditions. Earlier claims of 38-46% reductions and 32% weekend reductions are unsupported and withdrawn.

`outputs/route_holdout_diagnostics.csv` provides route/direction/day-type diagnostics within the selected evaluation subset. Independent corridor counts and route/time-specific sampling calibration remain outstanding.

## Reproduction

```powershell
$env:TRANSIT_CONFIG = 'config.yaml'
.\.venv\Scripts\python.exe scripts/run_pipeline.py gtfs
.\.venv\Scripts\python.exe scripts/run_pipeline.py load
.\.venv\Scripts\python.exe scripts/evaluate_crowding.py
.\.venv\Scripts\python.exe scripts/audit_crowding.py
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/vehicle-motion.test.cjs
```

Authorized raw ticket and GTFS inputs must be available locally. Raw tickets and credentials are excluded from Git. The audit requires the saved holdout CSV, which the evaluator regenerates. See DEPLOYMENT.md for hosting.

## Remaining validation work

Calibrate sampling/capacity assumptions; audit excluded records and ambiguous stop matches; validate inferred trips against GPS/trip IDs; compare multiple weeks and route/time slices; obtain independent passenger counts; quantify sensitivity to trip gaps and stop-time offsets. Runtime memory and hosted concurrency also require measurement; no less-than-100-MB or production-readiness guarantee is claimed.
