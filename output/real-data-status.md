# Real Ticket Baseline Status

- **October Ticket Records**: 1,317,662 usable records with verified vehicle and trip attributes.
- **Exact Normalized Route/Stop Matching**: 891,048 records (**67.62%** match rate across GTFS stops and routes). Unmatched records are safely excluded from load aggregation.
- **Temporal Held-Out Partition**: 1,006,493 training records (Oct 1–24), 311,169 test records (Oct 25–31).
- **Model Accuracy (78,588 evaluated bins)**:
  - **Mean Absolute Error (MAE)**: 7.53 passengers
  - **Root Mean Squared Error (RMSE)**: 10.54 passengers
  - **Crowding Tier Agreement**: 72.81%
- **Trip Grouping**: 45-minute idle vehicle gap heuristic.
- **Capacity Thresholds**: Standard DTC 12m low-floor specifications (35 seated, 70 crush).
- **Corridor Peaks**: Corroborates known morning (08:00–10:30) and evening (17:30–20:30) peak congestion on major Delhi arterial corridors.
