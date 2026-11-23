# Real ticket baseline

October ticket archive: 2,559,793 prepared records. Exact normalized route/stop matching: 1,238,237 (48.4%). Unmatched records are excluded, not treated as empty buses.

Fixed ZIP ingestion to ignore macOS metadata, and preserve direction-bearing route codes when matching GTFS. Current model uses ticket-gap trip grouping, fixed stop-time offsets, and uncalibrated bus capacities. Sample counts are not full vehicle occupancy. Estimated uncertainty excludes missing-rider bias.

Required before accuracy claims: investigate unmatched names and route variants, validate trip grouping against GPS or trip IDs, calibrate ticket sampling and capacities, and evaluate on held-out weeks. Current time estimates use a default speed and fixed wait when learned speeds are absent.
