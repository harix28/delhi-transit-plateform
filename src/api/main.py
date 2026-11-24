"""Local API and frontend. Times are interpreted in Asia/Kolkata."""
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import ROOT, load_config, resolve
from src.routing.journeys import CONF_RANK, JourneyPlanner

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

cfg = load_config()
planner = None
startup_error = None
IST = ZoneInfo("Asia/Kolkata")

@asynccontextmanager
async def lifespan(app):
    init_planner()
    yield

app = FastAPI(title="Delhi Transit Intelligence", version="0.2.0", lifespan=lifespan)
from src.api.auth import router as auth_router
app.include_router(auth_router)

origins = os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"])

def init_planner():
    """Load exactly the selected dataset; failures remain visible to health checks."""
    global planner, startup_error
    planner = None
    try:
        planner = JourneyPlanner(resolve(cfg, "processed"), cfg)
        startup_error = None
    except Exception as exc:
        startup_error = str(exc)
        logging.exception("Transit dataset initialization failed")
    return planner

def ready():
    global planner
    if planner is None:
        planner = init_planner()
    if planner is None:
        raise HTTPException(503, "Transit data is not ready. Run the demo setup or real-data pipeline, then restart the API.")
    return planner

def records(df):
    return json.loads(df.to_json(orient="records"))

@app.get("/health")
def health(response: Response):
    p = planner
    if p is None:
        response.status_code = 503
    return {"status": "ok" if p is not None else "not_ready", "ready": p is not None,
            "dataset": cfg.get("dataset", "local"), "synthetic": cfg.get("synthetic", False),
            "timezone": "Asia/Kolkata", "direct_routes_only": False,
            "timetable_available": p is not None and p.schedule is not None,
            "speed_data_available": p is not None and bool(p.speed or p.city)}

@app.get("/stops")
def stops(q: str = "", limit: int = Query(200, ge=1, le=1000)):
    p = ready()
    df = p.stops
    if q:
        df = df[df.stop_name.str.contains(q, case=False, regex=False)]
    return {"stops": records(df[["stop_id", "stop_name", "stop_lat", "stop_lon"]].head(limit))}

@app.get("/network")
def network():
    p = ready()
    routes = []
    for (route_id, direction), frame in p.rs.groupby(["route_id", "direction_id"]):
        frame = frame.sort_values("seq")
        routes.append({"id": route_id, "name": str(frame.iloc[0].route_short), "direction": int(direction),
                       "stops": len(frame), "geometry": frame[["stop_lat", "stop_lon"]].values.tolist()})
    return {"routes": routes, "stop_count": len(p.stops), "dataset": cfg.get("dataset", "local"),
            "synthetic": cfg.get("synthetic", False)}

@app.get("/journeys")
def journeys(from_: str = Query(..., alias="from", min_length=1, max_length=200),
             to: str = Query(..., min_length=1, max_length=200), depart: datetime | None = None,
             mode: Literal["fastest", "comfortable"] = "fastest",
             max_options: int = Query(5, ge=1, le=10)):
    p = ready()
    depart = depart or datetime.now(IST)
    depart = depart.replace(tzinfo=IST) if depart.tzinfo is None else depart.astimezone(IST)
    try:
        origin, dest = p.resolve(from_), p.resolve(to)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    num_opts = int(getattr(max_options, "default", 5)) if not isinstance(max_options, int) else int(max_options)
    raw_options = p.plan_with_transfer(origin, dest, depart, mode, num_opts)
    for opt in raw_options:
        opt["route"] = clean_route_name(opt.get("route", ""))
        if "legs" in opt:
            for leg in opt["legs"]:
                leg["route"] = clean_route_name(leg.get("route", ""))
    return {"depart": depart.isoformat(), "mode": mode, "synthetic": cfg.get("synthetic", False),
            "origin": {"lat": origin[0], "lon": origin[1]},
            "destination": {"lat": dest[0], "lon": dest[1]},
            "options": raw_options}

@app.get("/crowding/route/{route}")
def route_crowding(route: str, day_type: Literal["weekday", "weekend"] | None = None,
                   direction: int = Query(0, ge=0, le=1), half_hour: int | None = Query(None, ge=0, le=47),
                   depart: datetime | None = None):
    ready()
    from src.transit import route_stop_table
    frame = route_stop_table(route, direction, cfg)
    rid = str(frame.iloc[0].route_id)
    actual_direction = int(frame.iloc[0].direction_id)
    depart = depart or datetime.now(IST)
    depart = depart.replace(tzinfo=IST) if depart.tzinfo is None else depart.astimezone(IST)
    day_type = day_type or ("weekend" if depart.weekday() >= 5 else "weekday")
    half_hour = half_hour if half_hour is not None else depart.hour * 2 + depart.minute // 30
    cpath = resolve(cfg, "processed") / "crowding.parquet"
    if not cpath.exists():
        raise HTTPException(503, "Crowding table not built yet")
    try:
        import pyarrow.parquet as pq
        import pandas as pd
        f = pq.read_table(cpath, filters=[("route_id", "==", rid), ("day_type", "==", day_type),
            ("direction_id", "==", actual_direction), ("half_hour", "==", half_hour)]).to_pandas()
    except Exception:
        logging.exception("Unable to read crowding data")
        raise HTTPException(503, "Crowding data is unavailable")
    # Include missing stretches explicitly instead of presenting partial data as complete.
    ordered = list(frame.itertuples())
    base = pd.DataFrame([{"seg": int(a.seq), "from": a.stop_name, "to": b.stop_name,
                         "from_stop_id": str(a.stop_id), "to_stop_id": str(b.stop_id)}
                        for a, b in zip(ordered, ordered[1:])])
    if base.empty:
        raise HTTPException(404, "Route has no stretches")
    cols = ["seg", "load_est", "load_low", "load_high", "ratio", "level", "confidence", "n_trips"]
    merged = base.merge(f[[c for c in cols if c in f]], on="seg", how="left")
    known = merged.level.notna()
    coverage = float(known.mean())
    merged["level"] = merged.level.fillna("unknown")
    merged["confidence"] = merged.confidence.fillna("low")
    merged["n_trips"] = merged.n_trips.fillna(0).astype(int)
    from src.routing.journeys import RANK
    level = max(merged.level, key=lambda v: RANK[v]) if coverage == 1 else "unknown"
    confidence = min(merged.confidence, key=lambda v: CONF_RANK[v]) if coverage == 1 else "low"
    return {"route": str(frame.iloc[0].route_short), "route_id": rid, "direction": actual_direction,
            "depart": depart.isoformat(), "day_type": day_type, "half_hour": half_hour,
            "crowding": level, "confidence": confidence, "coverage": round(coverage, 3),
            "basis": "Historical ticket-derived estimate; not live occupancy", "synthetic": cfg.get("synthetic", False),
            "segments": records(merged.round(2))}

@app.get("/crowding/top")
def top_crowded(day_type: Literal["weekday", "weekend"] = "weekday",
                limit: int = Query(20, ge=1, le=200), min_confidence: Literal["low", "medium", "high"] = "medium"):
    p = ready()
    cpath = ROOT / cfg.get("paths", {}).get("processed", "data/processed") / "crowding.parquet"
    if not cpath.exists():
        raise HTTPException(503, "Crowding table not built yet")
    try:
        import pyarrow.parquet as pq
        import pandas as pd
        table = pq.read_table(
            cpath,
            filters=[("day_type", "==", day_type)]
        )
        f = table.to_pandas()
        f = f[f.confidence.map(CONF_RANK) >= CONF_RANK[min_confidence]]
        f = f.sort_values("ratio", ascending=False).head(limit)
    except Exception:
        logging.exception("Unable to read crowding data")
        raise HTTPException(503, "Crowding data is unavailable")
    if f.empty:
        return {"day_type": day_type, "items": []}
    names = p.rs[["route_id", "direction_id", "seq", "stop_name", "stop_lat", "stop_lon"]].drop_duplicates(subset=["route_id", "direction_id", "seq"]).rename(columns={"seq": "seg", "stop_name": "from_stop"})
    f = f.merge(names, on=["route_id", "direction_id", "seg"], how="left")
    cols = ["route_short", "direction_id", "from_stop", "half_hour", "load_est", "ratio", "level", "confidence", "stop_lat", "stop_lon"]
    existing = [c for c in cols if c in f.columns]
    return {"day_type": day_type, "items": records(f[existing].round(4))}

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "frontend" / "index.html")

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(ROOT / "frontend" / "sw.js", media_type="application/javascript")

@app.get("/manifest.json", include_in_schema=False)
def manifest_json():
    return FileResponse(ROOT / "frontend" / "manifest.json", media_type="application/manifest+json")

app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")


@app.get("/vehicles")
def live_vehicles(request: Request, response: Response):
    import time
    from src.live import get_vehicles
    data = get_vehicles()
    # The freshness window is included so stale flags are periodically reevaluated.
    etag = f'"{data["fetched_at"]}-{data["feed_timestamp"]}-{int(time.time() // 10)}"'
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return data


@app.get("/transit/stops")
def transit_stops(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    from src.transit import nearby_stops
    return nearby_stops(lat, lon, cfg)

def clean_route_name(name: str) -> str:
    if not name:
        return ""
    import re
    return re.sub(r'(?i)[_\s-]*(?:UP|DOWN)\b', '', str(name)).strip()


@app.get("/transit/routes/search")
def search_transit_routes(q: str = Query(..., min_length=1, max_length=50)):
    import pandas as pd
    from src.transit import tables
    _, routes, _ = tables(cfg)
    clean_q = q.strip().upper()
    # Exclude DOWN routes so user only sees clean UP routes without confusion
    up_routes = routes.copy()
    m1 = up_routes[up_routes.route_short_name.str.upper().str.startswith(clean_q)]
    m2 = up_routes[up_routes.route_short_name.str.upper().str.contains(clean_q, regex=False)]
    m3 = up_routes[up_routes.route_desc.str.upper().str.contains(clean_q, regex=False)]
    combined = pd.concat([m1, m2, m3]).drop_duplicates(subset=["route_id"]).head(12)
    combined["display_name"] = combined["route_short_name"].apply(clean_route_name)
    cols = ["route_id", "route_short_name", "display_name", "route_long_name", "route_desc"]
    return {"routes": records(combined[cols])}


@app.get("/transit/routes/{route_id}")
def transit_route(route_id: str, direction_id: int = Query(0, ge=0, le=1)):
    from src.transit import route_geometry
    return route_geometry(route_id, direction_id, cfg=cfg)


@app.get("/transit/routes/{route_id}/stops")
def route_stops(route_id: str, direction_id: int = Query(0, ge=0, le=1)):
    from src.transit import route_stop_table
    matches = route_stop_table(route_id, direction_id, cfg)
    return {"route_id": str(matches.iloc[0].route_id),
            "direction_id": int(matches.iloc[0].direction_id),
            "route_short": str(matches.iloc[0].route_short),
            "stops": records(matches[["stop_id", "stop_name", "stop_lat", "stop_lon", "seq"]])}


@app.get("/transit/search")
def search_transit_stops(q: str = Query(..., min_length=2, max_length=100)):
    from src.transit import tables
    stops, _, _ = tables(cfg)
    matches = stops[stops.stop_name.str.contains(q.strip(), case=False, regex=False, na=False)].head(8)
    return {"stops": records(matches[["stop_id", "stop_name", "stop_lat", "stop_lon"]])}


@app.get("/evaluation")
def evaluation():
    path = resolve(cfg, "outputs") / "evaluation.json"
    if not path.exists():
        raise HTTPException(503, "Evaluation has not finished yet")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/transit/stops/{stop_id}/departures")
def scheduled_departures(stop_id: str):
    p = ready()
    candidates = p.rs[p.rs.stop_id.astype(str) == str(stop_id)]
    if candidates.empty:
        raise HTTPException(404, "Unknown stop")
    if p.schedule is None:
        return {"source": "Timetable unavailable", "available": False, "departures": []}
    now = datetime.now(IST)
    results = []
    for row in candidates.itertuples():
        tail = p.rs[(p.rs.route_id == row.route_id) & (p.rs.direction_id == row.direction_id)
                    & (p.rs.seq > row.seq)].sort_values("seq")
        if tail.empty:
            continue
        trip = p.schedule.next_trip(row.route_id, stop_id, tail.iloc[0].stop_id, now, 0)
        if trip:
            results.append({"route": row.route_short, "route_id": str(row.route_id),
                            "departure": trip["departure"], "minutes": round(trip["wait"], 1)})
    unique = {(r["route_id"], r["departure"]): r for r in results}
    return {"source": "GTFS timetable, not live ETA", "available": True,
            "departures": sorted(unique.values(), key=lambda r: r["departure"])[:10]}
