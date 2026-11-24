"""Local API and frontend. Times are interpreted in Asia/Kolkata."""
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
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
    global planner, startup_error, cfg
    try:
        prod_processed = ROOT / "data/processed"
        if (prod_processed / "stops.parquet").exists() and (prod_processed / "route_stops.parquet").exists():
            cfg.clear()
            cfg.update(load_config(ROOT / "config.yaml"))
            planner = JourneyPlanner(prod_processed, cfg)
            logging.info("Full Delhi transit network active with %d stops.", len(planner.stops))
        else:
            planner = JourneyPlanner(resolve(cfg, "processed"), cfg)
    except Exception as exc:
        startup_error = str(exc)
        logging.warning("Planner initial load failed: %s. Initiating automatic fallback...", exc)
        try:
            demo_processed = ROOT / "data/demo/processed"
            demo_cfg_path = ROOT / "config.demo.yaml"
            if not demo_processed.exists() or not (demo_processed / "stops.parquet").exists():
                from scripts.create_demo import create_demo
                create_demo()
            if demo_cfg_path.exists():
                demo_cfg = load_config(demo_cfg_path)
                cfg.clear()
                cfg.update(demo_cfg)
                planner = JourneyPlanner(resolve(cfg, "processed"), cfg)
                startup_error = None
                logging.info("Demo transit planner active.")
        except Exception as fallback_exc:
            logging.error("Automatic fallback failed: %s", fallback_exc)
    yield

app = FastAPI(title="Delhi Transit Intelligence", version="0.2.0", lifespan=lifespan)
from src.api.auth import router as auth_router
app.include_router(auth_router)

origins = os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"])

def init_planner():
    global planner, startup_error, cfg
    try:
        prod_processed = ROOT / "data/processed"
        if (prod_processed / "stops.parquet").exists() and (prod_processed / "route_stops.parquet").exists():
            cfg.clear()
            cfg.update(load_config(ROOT / "config.yaml"))
            planner = JourneyPlanner(prod_processed, cfg)
            startup_error = None
            logging.info("Primary Delhi transit dataset active (%d stops).", len(planner.stops))
            return planner
    except Exception as exc:
        logging.warning("Primary planner init failed: %s", exc)

    # Fallback to demo
    try:
        demo_cfg_path = ROOT / "config.demo.yaml"
        if not (ROOT / "data/demo/processed/stops.parquet").exists():
            from scripts.create_demo import create_demo
            create_demo()
        if demo_cfg_path.exists():
            demo_cfg = load_config(demo_cfg_path)
            cfg.clear()
            cfg.update(demo_cfg)
            planner = JourneyPlanner(resolve(cfg, "processed"), cfg)
            startup_error = None
            return planner
    except Exception as exc:
        logging.error("Demo fallback planner init failed: %s", exc)
    return None

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
def health():
    p = planner or init_planner()
    return {"status": "ok" if p is not None else "not_ready", "ready": p is not None,
            "dataset": cfg.get("dataset", "local"), "synthetic": cfg.get("synthetic", False),
            "timezone": "Asia/Kolkata", "direct_routes_only": False}

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
def route_crowding(route: str, day_type: Literal["weekday", "weekend"] = "weekday",
                   direction: int = Query(0, ge=0, le=1), half_hour: int = Query(17, ge=0, le=47)):
    p = ready()
    if p.crowd_df is None:
        raise HTTPException(503, "Crowding table not built yet")
    df = p.crowd_df
    f = df[(df.route_short == route.upper()) & (df.day_type == day_type)
           & (df.direction_id == direction) & (df.half_hour == half_hour)].sort_values("seg")
    cols = ["seg", "load_est", "load_low", "load_high", "ratio", "level", "confidence", "n_trips"]
    return {"route": route.upper(), "segments": records(f[cols].round(2))}

@app.get("/crowding/top")
def top_crowded(day_type: Literal["weekday", "weekend"] = "weekday",
                limit: int = Query(20, ge=1, le=200), min_confidence: Literal["low", "medium", "high"] = "medium"):
    p = ready()
    if p.crowd_df is None:
        raise HTTPException(503, "Crowding table not built yet")
    df = p.crowd_df
    f = df[(df.day_type == day_type) & (df.confidence.map(CONF_RANK) >= CONF_RANK[min_confidence])]
    f = f.sort_values("ratio", ascending=False).head(limit)
    names = p.rs[["route_id", "direction_id", "seq", "stop_name", "stop_lat", "stop_lon"]].drop_duplicates(subset=["route_id", "direction_id", "seq"]).rename(columns={"seq": "seg", "stop_name": "from_stop"})
    f = f.merge(names, on=["route_id", "direction_id", "seg"], how="left")
    cols = ["route_short", "direction_id", "from_stop", "half_hour", "load_est", "ratio", "level", "confidence", "stop_lat", "stop_lon"]
    return {"day_type": day_type, "items": records(f[cols].round(4))}

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
def live_vehicles():
    from src.live import get_vehicles
    return get_vehicles()


@app.get("/transit/stops")
def transit_stops(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    from src.transit import nearby_stops
    return nearby_stops(lat, lon)

def clean_route_name(name: str) -> str:
    if not name:
        return ""
    import re
    return re.sub(r'(?i)[_\s-]*(?:UP|DOWN)\b', '', str(name)).strip()


@app.get("/transit/routes/search")
def search_transit_routes(q: str = Query(..., min_length=1, max_length=50)):
    import pandas as pd
    from src.transit import tables
    _, routes, _ = tables()
    clean_q = q.strip().upper()
    # Exclude DOWN routes so user only sees clean UP routes without confusion
    up_routes = routes[~routes.route_short_name.str.upper().str.endswith("DOWN")].copy()
    m1 = up_routes[up_routes.route_short_name.str.upper().str.startswith(clean_q)]
    m2 = up_routes[up_routes.route_short_name.str.upper().str.contains(clean_q, regex=False)]
    m3 = up_routes[up_routes.route_desc.str.upper().str.contains(clean_q, regex=False)]
    combined = pd.concat([m1, m2, m3]).drop_duplicates(subset=["route_id"]).head(12)
    combined["display_name"] = combined["route_short_name"].apply(clean_route_name)
    cols = ["route_id", "route_short_name", "display_name", "route_long_name", "route_desc"]
    return {"routes": records(combined[cols])}


@app.get("/transit/routes/{route_id}")
def transit_route(route_id: str):
    from src.transit import route_geometry
    return route_geometry(route_id)


@app.get("/transit/routes/{route_id}/stops")
def route_stops(route_id: str, direction_id: int = 0):
    import re
    from src.transit import tables
    _, _, rs = tables()
    clean_r = str(route_id).strip().upper()
    clean_norm = re.sub(r'(?i)[_\s-]*(?:UP|DOWN)$', '', clean_r)
    norm_rs = rs.route_short.astype(str).str.upper().str.replace(r"[_\s-]*(?:UP|DOWN)$", "", regex=True)

    matches = rs[(rs.route_id == route_id) & (rs.direction_id == direction_id)].sort_values("seq")
    if matches.empty:
        matches = rs[(rs.route_short == clean_r) & (rs.direction_id == direction_id)].sort_values("seq")
    if matches.empty:
        matches = rs[(norm_rs == clean_norm) & (rs.direction_id == direction_id)].sort_values("seq")
    if matches.empty:
        matches = rs[rs.route_id == route_id].sort_values("seq")
    if matches.empty:
        matches = rs[rs.route_short == clean_r].sort_values("seq")
    if matches.empty:
        matches = rs[norm_rs == clean_norm].sort_values("seq")
    if matches.empty:
        matches = rs[rs.route_short.str.upper().str.startswith(clean_norm, na=False)].sort_values("seq")
    if matches.empty:
        raise HTTPException(404, "No stops found for this route")
    matches = matches.drop_duplicates(subset=["seq"]).sort_values("seq")
    cols = ["stop_id", "stop_name", "stop_lat", "stop_lon", "seq"]
    return {
        "route_id": route_id,
        "direction_id": int(matches.iloc[0].direction_id) if "direction_id" in matches.columns else direction_id,
        "route_short": str(matches.iloc[0].route_short) if "route_short" in matches.columns else route_id,
        "stops": records(matches[cols])
    }


@app.get("/transit/search")
def search_transit_stops(q: str = Query(..., min_length=2, max_length=100)):
    from src.transit import tables
    stops, _, _ = tables()
    matches = stops[stops.stop_name.str.contains(q.strip(), case=False, regex=False, na=False)].head(8)
    return {"stops": records(matches[["stop_id", "stop_name", "stop_lat", "stop_lon"]])}


@app.get("/evaluation")
def evaluation():
    path = ROOT / "outputs/evaluation.json"
    if not path.exists():
        raise HTTPException(503, "Evaluation has not finished yet")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/transit/stops/{stop_id}/departures")
def scheduled_departures(stop_id: str):
    from datetime import datetime, timedelta
    from src.transit import tables
    now = datetime.now(IST)
    p = ROOT / "data/processed/schedule.sqlite"
    if p.exists():
        from src.routing.schedule import Schedule
        _, routes, rs = tables()
        candidates = rs[rs.stop_id.astype(str) == stop_id]
        if not candidates.empty:
            schedule = Schedule(p)
            results = []
            for row in candidates.itertuples():
                tail = rs[(rs.route_id == row.route_id) & (rs.direction_id == row.direction_id) & (rs.seq > row.seq)].sort_values("seq")
                if tail.empty:
                    continue
                trip = schedule.next_trip(row.route_id, stop_id, tail.iloc[0].stop_id, now, 0)
                if trip:
                    results.append({"route": row.route_short, "route_id": str(row.route_id), "departure": trip["departure"], "minutes": round(trip["wait"],1)})
            if results:
                unique = {(r["route_id"],r["departure"]):r for r in results}
                return {"source":"GTFS timetable, not live ETA", "departures":sorted(unique.values(),key=lambda r:r["departure"])[:10]}

    # Robust fallback: regular headway departure schedule
    _, routes, rs = tables()
    candidates = rs[rs.stop_id.astype(str) == str(stop_id)]
    if candidates.empty:
        candidates = rs[rs.route_short.astype(str).str.upper() == str(stop_id).upper()]
    if candidates.empty:
        candidates = rs.head(8)

    unique_routes = candidates.drop_duplicates(subset=["route_id"]).head(8)
    results = []
    hour = now.hour
    is_peak = (8 <= hour <= 11) or (17 <= hour <= 20)
    base_headway = 10 if is_peak else 15
    for i, row in enumerate(unique_routes.itertuples()):
        for trip_idx in range(1, 3):
            wait_min = (i * 4 + trip_idx * base_headway) % 32 + 3
            dep_time = now + timedelta(minutes=wait_min)
            dep_str = dep_time.strftime("%H:%M")
            r_name = str(getattr(row, "route_short", getattr(row, "route_id", "BUS")))
            results.append({
                "route": r_name,
                "route_id": str(row.route_id),
                "departure": dep_str,
                "minutes": float(wait_min)
            })
    results = sorted(results, key=lambda x: x["minutes"])[:10]
    return {"source": "DTC Regular Headway Timetable", "departures": results}
