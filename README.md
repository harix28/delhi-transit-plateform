# Delhi Transit: crowdedness-aware journey planner

A working **Phase 1 / Problem 2 research prototype** for Transport Stack Hackathon 2026. It combines historical ticket-derived crowding estimates, GTFS journey planning and a separate live GPS map. Crowding estimates are uncalibrated historical estimates, not live passenger counts or seat guarantees.

## Run locally on Windows

From this repository folder, create a Python 3.12 virtual environment and install dependencies if needed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start.ps1 -RealData
```

Open http://127.0.0.1:8000. For another device on the same network, use this computer's LAN address and port 8000. The default start script binds all interfaces. Windows Firewall must allow the connection.

For an explicitly synthetic demo:

```powershell
.\.venv\Scripts\python.exe scripts/create_demo.py
.\start.ps1
```

Demo fixtures contain 20 stops, eight illustrative routes in both directions and 6,048 synthetic tickets. They are not actual Delhi operations. Selected configuration is respected; missing data is not silently replaced by a demo.

## Features and limits

- Direct and bounded one-transfer bus journeys on GTFS, with an 800 m access-walking limit.
- Fastest or comfortable ranking among searched candidates; no global-optimum guarantee.
- Historical segment crowding, with explicit unknown stretches, coverage and sample confidence.
- Real GPS positions when provider access is configured. Browser checks every two seconds; shared upstream checks default to ten seconds. Marker animation does not increase GPS precision.
- No fabricated departures. If a timetable exists but has no matching service, the planner returns no journey. Without a timetable, journey timing is labelled estimated.
- Straight-line walking, heuristic ticket-trip grouping, uniform stop-time offsets, assumed capacity and unvalidated real-data uplift remain limitations.

## Data and reproducibility

Put authorized static GTFS under `data/raw/gtfs`, ticket CSV/ZIP under `data/raw/tickets`, and optional historical GPS SQLite files under `data/raw/gps`. See [the technical note](output/METHODOLOGY.md) for commands and assumptions. Private raw archives and `.env` are ignored by Git.

The saved evaluation reports 1,317,662 usable ticket records and 891,048 matched records (67.62% of usable records). In 78,588 qualified held-out bins, scaled ticket-load MAE is 7.53 and label agreement is 72.81%. These cover 9.25% of test bins and do **not** measure actual onboard occupancy accuracy. See [validation audit](outputs/validation_audit.json) and [uplift sensitivity](outputs/uplift_sensitivity.csv).

```powershell
.\.venv\Scripts\python.exe scripts/audit_crowding.py
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/vehicle-motion.test.cjs
```

Unit/API tests verify software behavior, not predictive accuracy on real buses.

## API

- `/health`: readiness, selected dataset and timetable/speed-data availability.
- `/journeys?from=...&to=...&depart=...&mode=fastest`: bus journey candidates.
- `/crowding/route/{route}?depart=...`: exact route/direction historical crowding and coverage; optional `direction`, `day_type`, `half_hour` filters.
- `/crowding/top`: historical load diagnostics.
- `/vehicles`: real GPS availability, timestamps and positions; conditional requests supported.
- `/transit/stops/{stop_id}/departures`: timetable departures, not live traffic ETAs.
- `/docs`: interactive API documentation.

Times without offsets are interpreted as Asia/Kolkata. Raw route IDs and public route codes are distinct; use `code:729` for an explicitly public code where IDs could collide.

## Hosting and submission

[DEPLOYMENT.md](DEPLOYMENT.md) explains Render and Docker/Railway deployment. Include the compressed timetable bundle and checksum in Git; the build restores the index. Add provider credentials to the host's environment settings. Supabase accounts are optional; guest journey planning works without them.

[Phase 1 proposal draft](output/PHASE1_PROPOSAL.md), [demo script](output/DEMO_SCRIPT.md), and [submission checklist](output/SUBMISSION_CHECKLIST.md) describe the remaining submission steps. The application has not been independently certified as production-ready.

## Credits

Uses FastAPI, Uvicorn, pandas, NumPy, PyArrow, SQLite, PyYAML, HTTPX, GTFS Realtime bindings, Leaflet and OpenStreetMap. Transit data: Delhi Transport Stack. Record team contributions and AI assistance accurately before submission. Dependencies and transit datasets retain their own licences/terms; select an explicit project source-code licence before calling the repository open-source.
