# Delhi Transit Intelligence: Crowdedness-Aware Journey Planner

A production-ready Delhi bus journey planner and passenger crowding intelligence platform built for **Transport Stack Hackathon 2026 (Phase 1 / Problem 2)**. Combining empirical segment load modeling from 2.56M October DTC ticket records with static GTFS and real-time transit streams, it empowers commuters to make informed travel choices between **Fastest** and **Most Comfortable (Higher Seat Probability)** routes.

## Current runtime behavior

The selected `TRANSIT_CONFIG` is respected; startup does not silently switch datasets. Missing selected data produces HTTP 503 on `/health`. Real-feed failures return an explicit unavailable state; synthetic positions are only generated in demo configuration. A timetable with no matching service produces no journey/departure. When no timetable index is installed, journey timings are explicitly labelled estimates and stop departure lists remain unavailable.

Walking access is limited by `journey.walk_max_m`. Transfer and direct candidates compete by the selected priority in a bounded search; an exhaustive optimum is not guaranteed. Unknown crowding is displayed as unobserved.

For hosting instructions and exact Render/Railway settings, see [DEPLOYMENT.md](DEPLOYMENT.md). The compressed timetable bundle must be committed with the code for a full real-data deployment.

## Run on Windows

Python 3.11+ is required. From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/create_demo.py
.\start.ps1
```

Open http://127.0.0.1:8000. Interactive API documentation is at http://127.0.0.1:8000/docs. The frontend and API share the same server; no Node build or external map download is required. Stop the server with Ctrl+C. If PowerShell blocks scripts, use the explicit commands below instead of changing execution policy.

```powershell
$env:TRANSIT_CONFIG = 'config.demo.yaml'
.\.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

The local environment created in this workspace uses the bundled Codex Python interpreter with a virtual environment. To move the project to another machine, recreate `.venv` using the commands above.

## Try the demo

Choose a pickup and destination on the Planner screen. Compare Fastest and Comfortable, select a journey card to highlight its corridor, swap endpoints, or choose stops on the schematic. The network tab shows route counts and the highest estimated segment loads. Stops also accept `latitude,longitude`.

Demo fixtures include 20 geographically located stops, 8 illustrative routes in both directions, 6,048 synthetic ticket records, and synthetic GPS speeds. Route names D1-D4 are invented. They are not actual scheduled Delhi services. Demo departures use weekday/weekend and hour patterns, not date-specific predictions. The initial 08:00 demo query is a preset example, not the current time.

All demo input and output files live under `data/demo/`; the generated configuration is `config.demo.yaml`. Running demo setup again replaces only these generated demo fixtures. Actual data under `data/raw/` and `data/processed/` stays separate.

## Use real data

Obtain the authorized datasets separately, then place them here:

```text
data/raw/gtfs/       stops.txt, routes.txt, trips.txt, stop_times.txt
data/raw/tickets/    ticket CSV or a ZIP containing one CSV
data/raw/gps/        one or more SQLite .db files
```

Ticket columns: `passenger_count`, `vehicle_number`, `start_stop_name`, `end_stop_name`, `route`, `created_at`. The baseline assumes route suffixes `UP`/`DOWN` and naive ticket timestamps already in India time. The GPS table needs `route_id`, `speed` in km/h and `timestamp` in Unix seconds. Verify these assumptions against the actual feed before importing.

```powershell
Remove-Item Env:TRANSIT_CONFIG -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe scripts/run_pipeline.py all
.\start.ps1 -RealData
```

Tune `config.yaml` after checking stop matching, units, bus capacities, service availability and ticket sampling. Restart the API after rebuilding outputs. Real GPS requires the server-side Transport Stack credential described in `.env.example`. Private ticket and GPS archives are not downloaded by the app.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Readiness, dataset label, synthetic flag and timezone |
| `GET /stops?q=...` | Stop search for autocomplete |
| `GET /network` | Route-direction geometry and counts |
| `GET /journeys?from=...&to=...&depart=...&mode=fastest` | Direct journeys; mode can also be `comfortable` |
| `GET /crowding/route/{route}?half_hour=16` | Per-segment estimates in a half-hour bin |
| `GET /crowding/top?min_confidence=low` | Highest estimated segment loads |

Times without an offset are treated as Asia/Kolkata; offset-aware inputs are converted to Asia/Kolkata. Half-hour bins run from 0 through 47. If data is missing, the UI and health route remain available and data endpoints return a setup message with HTTP 503.

## Validate

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check frontend/rider.js
node --check frontend/mobile-flow.js
```

Tests cover load arithmetic, crowding thresholds, trip splitting, journey ranking, geometry, timezone conversion, malformed inputs, unavailable routes, partial/missing crowding and startup without data. They use small synthetic fixtures. Passing them establishes software behavior, not predictive accuracy on Delhi transit.

## Current limits and next work

- Direct and bounded one-transfer bus journeys are supported. GTFS calendars apply when the timetable index exists; metro and exhaustive network optimization are not implemented.
- Route/hour GPS speed medians are a baseline. They exclude speeds below the configured minimum and are not map-matched travel times. Multi-file aggregation is a weighted average of per-file medians, not a global median.
- Walking uses straight-line distance; ride distances connect GTFS stops. The offline visualization is a corridor schematic, not a road map.
- Waiting time, dwell, seated/total capacity, missing-rider uplift and per-stop time offsets are configurable placeholders.
- Ticket trip grouping uses a gap heuristic. Direction and duplicate stop-name matching can be ambiguous. Load means include only observed trips crossing each segment; absence of tickets does not establish zero load.
- Confidence labels describe sample size, not model calibration. Sampling intervals exclude capacity uncertainty, missing-ticket bias and model error. Missing coverage is shown as unknown with low confidence.
- Real-data quality checks, ground-truth validation, one-transfer routing, GTFS service/headways and verified feed adapters are the next engineering milestones.
- The supplied source and PDF drafts were generated by Claude. Organizer rules and deliverables require independent confirmation from the original briefs.

## Layout

`src/ingest` reads GTFS and tickets; `src/models` builds loads/crowding and speeds; `src/routing` ranks journeys; `src/api` serves FastAPI and the UI; `frontend` contains the dependency-free responsive interface; `scripts` runs real or synthetic pipelines; `tests` contains regression tests.


## Real-data P2 evaluation

Run `python scripts/evaluate_crowding.py` after importing October tickets and GTFS. This writes route matching diagnostics, unmatched pairs, a chronological held-out comparison and time-pattern CSVs under `outputs/`. See `output/METHODOLOGY.md` for assumptions and interpretation. The UI exposes the evaluation under Explore > Model quality & coverage. Journey results now include per-stretch labels and coverage.

Current real-data matching is 48.4%; this is not complete network coverage. The held-out metric measures agreement with ticket-derived loads and must not be presented as measured occupancy accuracy. Real-data startup uses `start.ps1 -RealData`.


## Schedule-based journey timing
Run `python scripts/build_schedule.py` after importing static GTFS, then restart the API in real-data mode. Journey search checks service calendars and optional calendar exceptions, supports GTFS times beyond 24:00, and finds departures within two hours of reaching a boarding stop. Waiting and riding times come from the timetable when the index exists. These are scheduled times, not live ETA predictions. Nearby stand cards expose scheduled departures on demand.


## One-transfer fallback
When no direct journey is found, the planner searches up to eight shared-stop interchange candidates, ranked by geographic detour. Both legs use the normal timetable-aware planner, with a two-minute transfer buffer and straight-line access walks. Results show both bus numbers and draw the legs in separate colors. This bounded fallback is not an exhaustive network optimum, does not compare transfers against available direct journeys, and does not guarantee pedestrian accessibility.


## Historical vehicle type evidence
Run `python scripts/build_vehicle_types.py` to aggregate October `is_ac` values by normalized vehicle ID. At least 20 observations and 98% agreement are required; otherwise the type is unknown. The live map AC/Non-AC selector filters only buses with matching historical evidence; it does not verify current vehicle equipment or filter journey options. Tickets without vehicle IDs are excluded from trip-based load reconstruction. Older evaluation metrics must be regenerated after this safeguard.
