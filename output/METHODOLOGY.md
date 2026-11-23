# Delhi Transit: P2 baseline methodology

## Reproduction
Use Python 3.11+ and install requirements.txt. Place the authorized GTFS files under data/raw/gtfs and the October ticket archive under data/raw/tickets. Run:

    python scripts/run_pipeline.py gtfs
    python scripts/run_pipeline.py load
    python scripts/evaluate_crowding.py
    python -m pytest -q

Start with TRANSIT_CONFIG=config.yaml. GPS credentials remain in the ignored .env. Source attribution: Delhi Transport Stack; map tiles: OpenStreetMap contributors.

## Matching and trip reconstruction
Names are normalized by case, punctuation and whitespace. Ticket route base plus UP/DOWN is matched to the direction-bearing GTFS code when present. A representative sequence uses the longest GTFS trip for each route/direction. Boarding must precede alighting. Duplicate candidate matches currently select the first match; repeated stop names and route variants remain an ambiguity requiring further investigation.

Tickets are grouped by vehicle, route and leg with a 45-minute gap heuristic. Sparse ticket issuance can fragment a real bus trip; successive real trips may also be merged. These are inferred groups, not verified operational trips.

## Segment load
Each ticket contributes its passenger count to the segments between its matched boarding and alighting stops. Values are aggregated per inferred trip, segment, weekday/weekend and half-hour. Segment timestamps use a 2.5-minute-per-stop offset. Only trips with observed crossing tickets enter each bin. No-observation bins are unknown, not zero.

## Interpretation and uncertainty
The source is an e-ticket sample, not complete ridership. The default uplift of 1.0 is an uncalibrated assumption. Thresholds of 35 seated and 70 total passengers are placeholders, not measured capacity per vehicle. Labels describe the modeled ticket sample and must not be advertised as verified seat availability. Confidence labels indicate sample count only (5 medium, 20 high). Normal-approximation intervals exclude missing-rider bias, capacity variation, trip reconstruction error and alighting uncertainty. A one-observation interval is not evidence of certainty.

## Temporal validation
The evaluator fits October 1â€“24 and holds out October 25â€“31. It joins route/direction/segment/day-type/half-hour bins and evaluates only overlapping bins with at least five inferred trips in each partition. MAE and RMSE compare held-out ticket-derived mean loads, not actual occupancy. Level agreement compares assumed capacity labels. Report evaluated-bin coverage alongside metrics; non-overlapping bins are excluded.

Artifacts: evaluation.json, route_match_quality.csv, unmatched_stop_pairs.csv, holdout_comparison.csv, train_time_patterns.csv and holdout_time_patterns.csv. Aggregated time patterns weight segment bins equally and are descriptive diagnostics, not total city ridership.

## Journey interface
Users choose pickup, destination, departure time and fastest/comfortable priority. Results show walking/waiting/riding breakdowns, historical crowding, data coverage and each stretch. Unknown coverage downgrades the overall label. Timetable-aware direct journeys and a bounded one-transfer fallback are supported. Service calendars and exceptions are checked when the schedule index exists. Walking distances remain straight-line, transfer search is not exhaustive, and timetables are not live ETA predictions. Route geometries on journey cards connect stops; the network route view separately uses GTFS shapes.

## Completion gates
Before claiming final PS compliance: validate unmatched aliases and ambiguous sequences; verify operational trips; calibrate ticket sampling and bus capacities; assess known corridor patterns with external evidence; validate on real journeys; and confirm original organizer requirements. ETA service approval is independent of the P2 crowding model.


## Observed evaluation result
Current values are generated in outputs/evaluation.json. Earlier metrics included tickets without vehicle IDs and are superseded. Missing vehicle IDs are excluded from trip grouping.

Corrected run: 1,317,663 prepared records, 657,753 matched (49.92% of prepared records). The held-out week contains 311,169 records. Of 628,724 test bins, 500,081 overlap training and 57,431 have at least five inferred trips in each split. Ticket-load MAE = 0.3905, RMSE = 0.5467. All evaluated bins are still in the seats class under placeholder thresholds, so label agreement is not evidence of crowding discrimination.
