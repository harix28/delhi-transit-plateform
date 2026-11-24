# Proposal draft: historical crowding-aware Delhi bus journeys

**Selected challenge:** Phase 1, Problem 2.  
**Team details:** Add the registered team name, members and contact before submitting.

## Problem and intended users

Commuters need to compare expected crowding alongside travel time. Operators also need transparent route/time diagnostics. A GPS map shows bus positions but does not reveal how many passengers are onboard.

## Proposed contribution

Reconstruct approximate bus trips from October ticket records, match boarding/alighting stops to GTFS, and aggregate observed segment loads by route, direction, day type and half-hour. Present historical crowding beside journey alternatives, including missing coverage and sample confidence. Users can prioritize travel time or lower historical crowding.

## Working prototype

The repository includes GTFS/ticket pipelines, CSV/Parquet outputs, a FastAPI backend, a commuter interface and live GPS integration. Direct and bounded one-transfer candidates are supported. The model is a transparent baseline; no verified seat probability or live occupancy prediction is claimed.

## Evidence and limitations

Saved evaluation: 1,317,662 usable records, 891,048 matched records; 78,588 qualified held-out bins (9.25% of test bins). Ticket-derived scaled-load MAE is 7.53 and class agreement is 72.81%, compared with a 59.76% always-seats baseline in the same selected bins. These metrics do not establish true occupancy accuracy.

The current 20x uplift and 35/65 thresholds are assumptions. Uplift sensitivity, sample-selection coverage and descriptive time patterns are exported. Missing vehicle IDs, ambiguous stop matches, heuristic trips and missing crossing tickets can bias results.

## Next development

Calibrate sampling and capacity against independent evidence; improve trip identification and stop matching; evaluate additional week/corridor slices; improve passage-time alignment. If shortlisted, continue strengthening Problem 2 or investigate Problem 5 after obtaining metro AFC/network data.

## Deliverables

Source: https://github.com/harix28/delhi-transit-plateform  
Technical note: output/METHODOLOGY.md  
Validation artifacts: outputs/validation_audit.json, uplift_sensitivity.csv, route_holdout_diagnostics.csv and pattern_diagnostics.csv.  
Demo video and hosted URL: add the actual tested links before submission. Latest local changes must be committed/pushed before linking them as submitted work.
