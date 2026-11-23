"""Server-side Transport Stack feed; credentials never enter API responses."""
import os
import time
import threading
import re
from functools import lru_cache
from src.config import ROOT
import httpx
from fastapi import HTTPException
from google.transit import gtfs_realtime_pb2

URL = "https://dts-backend.transportstack.in/api/dataset/otd/get-file?agency=delhi-buses&category=realtime_gtfs&filename=VehiclePositions.pb"
_lock = threading.Lock()
_cache = None
_fetched = 0

@lru_cache(maxsize=1)
def vehicle_types():
    import pandas as pd
    path = ROOT / "data/processed/vehicle_types.parquet"
    if not path.exists():
        return {}
    return {str(r.vehicle_id): {"type": r.type, "observations": int(r.count), "agreement": float(r.agreement)} for r in pd.read_parquet(path).itertuples()}

def get_vehicles():
    global _cache, _fetched
    with _lock:
        now = time.time()
        if _cache is not None and now - _fetched < 30:
            return _cache
        key = os.getenv("TRANSPORTSTACK_API_KEY", "").strip()
        if not key:
            raise HTTPException(503, "Live feed key is not configured")
        try:
            response = httpx.get(URL, headers={"x-api-key": key}, timeout=20, follow_redirects=False)
            response.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            if not feed.IsInitialized():
                raise ValueError("Invalid feed")
        except Exception:
            raise HTTPException(502, "Live bus feed is temporarily unavailable") from None
        now = time.time()
        vehicles = []
        for entity in feed.entity:
            if not entity.HasField("vehicle") or not entity.vehicle.HasField("position"):
                continue
            v = entity.vehicle
            lat, lon = v.position.latitude, v.position.longitude
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            timestamp = int(v.timestamp) if v.HasField("timestamp") else None
            age = int(now - timestamp) if timestamp else None
            vehicles.append({"id": v.vehicle.id or entity.id, "route_id": v.trip.route_id,
                             "trip_id": v.trip.trip_id, "lat": lat, "lon": lon,
                             "timestamp": timestamp, "stale": age is None or age > 300 or age < -60,
                             "bearing": v.position.bearing if v.position.HasField("bearing") else None,
                             "bus_type": vehicle_types().get(re.sub(r"[^A-Z0-9]", "", (v.vehicle.id or entity.id).upper()), {}).get("type", "unknown"),
                             "bus_type_basis": "October 2026 tickets; not current fleet verification"})
        _cache = {"source": "Delhi Transport Stack", "synthetic": False,
                  "feed_timestamp": int(feed.header.timestamp), "fetched_at": int(now),
                  "vehicles": vehicles}
        _fetched = now
        return _cache
