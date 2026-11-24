# Hosting Delhi Transit

The frontend and FastAPI backend run as **one Python web service**. Open the service URL directly; no separate frontend host or CORS setup is needed.

## Render (recommended first deployment)

1. Commit and push this project to your GitHub repository, including `data/deploy/schedule.sqlite.gz`, its JSON checksum, the processed Parquet files, and the frontend assets. Do not commit `.env`, `.venv`, raw tickets, or raw GPS.
2. In Render choose **New > Blueprint**, connect that repository, and select its `render.yaml`. For an existing Render service, update its build and start commands to the values below, then deploy the new commit.
3. The repository root should be the folder containing `requirements.txt`. This checkout already has that layout. If you uploaded the outer `Public Transit` folder instead, set the service's **Root Directory** to `delhi-transit-platform` (and select the YAML in that folder when using a Blueprint).
4. Use these settings:

| Setting | Value |
| --- | --- |
| Service type | Web Service |
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt && python scripts/prepare_deploy.py` |
| Start command | `python -m uvicorn src.api.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check path | `/health` |
| `PYTHON_VERSION` | `3.12.14` |
| `TRANSIT_CONFIG` | `config.yaml` |

5. Add any credentials you need under the service's **Environment** settings. Values from your local `.env` are not uploaded by Git:

| Variable | When needed |
| --- | --- |
| `TRANSPORTSTACK_API_KEY` | Real bus GPS feed; without it the map reports GPS unavailable |
| `TRANSPORTSTACK_VEHICLE_URL` | Optional provider-approved feed URL; the documented default is built in |
| `SUPABASE_URL` | Optional verified rider accounts |
| `SUPABASE_PUBLISHABLE_KEY` | Optional verified rider accounts; use the publishable key, never a service-role key |
| `APP_ORIGIN` | Exact service origin, e.g. `https://your-service.onrender.com`, without a trailing slash; needed for account writes behind a proxy |

For Supabase accounts, enable the desired email/phone OTP provider in Supabase and configure its email/SMS delivery. Entering keys alone does not configure delivery. The app remains usable as a guest.

6. Open the public service URL after deployment. Check `/health`: `ready` and `timetable_available` should both be true for the real dataset. Check `/vehicles`: an unavailable feed returns an empty list with `available: false`, never simulated buses disguised as live GPS.

Render free services sleep after 15 minutes without traffic and can take about a minute to wake. For a responsive public app, choose an always-on paid instance. The Blueprint keeps the free plan so creating it does not silently select paid compute. See [Render FastAPI hosting](https://render.com/hosting/fastapi) and [free-service limits](https://render.com/docs/free).

Keep one worker for the bundled local tables. Additional workers each load their own dataset and increase memory use. Check Render's memory chart under realistic journey-search traffic before choosing the final instance size; a small-instance capacity guarantee has not been established.

## Why the timetable bundle matters

The full `schedule.sqlite` and raw `stop_times.txt` are intentionally Git-ignored. A fresh deployment previously lacked a timetable. The tracked compressed bundle contains the public static GTFS schedule tables; the build verifies its checksum, restores SQLite, and recreates query indexes. No private ticket or raw GPS archive is needed at runtime.

After updating your GTFS data, rebuild the timetable and its bundle locally before committing:

```powershell
$env:TRANSIT_CONFIG = 'config.yaml'
.\.venv\Scripts\python.exe scripts/build_schedule.py
.\.venv\Scripts\python.exe scripts/prepare_deploy.py --bundle-schedule
```

The current bundle is about 30 MB. Keep the `.gz` and `.json` files together. If the bundle is absent, the build can use an existing schedule or rebuild from raw `stop_times.txt`; otherwise it fails with a setup error instead of deploying fabricated schedules.

Files restored during the build are included in the deployment. No persistent disk is required for this read-only dataset. Render free instances lose files written at runtime on restart/redeploy; updates must go through a new build. See [Render filesystem limits](https://render.com/docs/free) and [Blueprint reference](https://render.com/docs/blueprint-spec).

## Railway or another Docker host

The included `Dockerfile` restores the same timetable during image build. The `.dockerignore` excludes local credentials and private raw data.

On Railway: create a project from GitHub, select this repository, let it detect the Dockerfile, add the environment variables above, and use **Settings > Networking > Generate Domain**. Set `APP_ORIGIN` to that domain for accounts. See [Railway's FastAPI guide](https://railway.com/guides/fastapi).

For another container host, expose its assigned `PORT`; the image defaults to 8000. Set the health path to `/health`. Build and test locally if Docker is installed:

```text
docker build -t delhi-transit .
docker run --rm -p 8000:8000 --env-file .env delhi-transit
```

## If something fails

- **Build cannot find requirements.txt:** correct the service Root Directory.
- **Timetable bundle missing/checksum mismatch:** regenerate it and push both bundle files.
- **`/health` returns 503:** inspect deployment logs for missing/invalid selected processed tables.
- **`timetable_available: false`:** confirm the build command includes `prepare_deploy.py` and the bundle is committed.
- **No GPS buses:** verify the provider credential/permission and inspect backend feed warnings. Valid empty feeds and stale positions are possible.
- **Account writes return 403:** set `APP_ORIGIN` to the exact HTTPS origin. All account requests should use that same app origin.
- **First visit is slow:** this is expected when a free Render instance wakes. Use an always-on instance if this delay is unacceptable.
- **Old UI after a deploy:** reload the installed app. The service worker now updates static resources from the network and never caches account or transit API responses.


Live-map responsiveness: the browser checks every 2 seconds using conditional requests, and the shared backend checks the provider every 10 seconds by default (`TRANSIT_FEED_REFRESH_SECONDS`, minimum 5; adjust to your provider quota). Unchanged responses return HTTP 304. Markers ease to newly reported coordinates over 700 ms and never extrapolate beyond confirmed GPS samples. This does not provide millisecond GPS measurements; accuracy and freshness depend on the provider and vehicle timestamps.
