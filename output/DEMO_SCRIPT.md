# Two-minute Phase 1 demo script

1. Introduce Problem 2: compare bus travel time with historical crowding. State that GPS is live but crowding is estimated from historical ticket samples.
2. Show the running real-data app and `/health` with `synthetic: false` and timetable availability. Select pickup, destination and departure time.
3. Compare Fastest and Comfortable options. Show travel time, crowding label, stretch coverage and sample confidence. If the selected query has insufficient coverage, explain the unknown result rather than replacing it with synthetic data.
4. Open a route timeline to show stop-to-stop estimates. Show the selected time and state these are historical estimates, not measured live occupancy.
5. Show `outputs/validation_audit.json`: explain 67.62% matching among usable records and 9.25% evaluated test-bin coverage. State 72.81% is ticket-derived class agreement, not passenger-count accuracy.
6. Show `outputs/uplift_sensitivity.csv` and the 20x assumption. Explain that perfect agreement at uplift 1 is trivial because all bins are seats. Identify independent calibration as the next milestone.
7. End with repository, technical note and reproducible commands. Credit the team, data, libraries and AI assistance accurately. Record the video and add its link to the proposal.

Use real-data examples for the main demonstration. Label any synthetic fixture explicitly. Do not display `.env` or provider credentials during recording.
