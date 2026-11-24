"""Dataset-aware stop and route lookup. Internal IDs and public codes stay distinct."""
from functools import lru_cache
import re
import pandas as pd
from fastapi import HTTPException
from src.config import load_config, resolve
from src.utils import haversine_m


def tables(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    return _tables(str(resolve(cfg, "raw") / "gtfs"), str(resolve(cfg, "processed")))


@lru_cache(maxsize=4)
def _tables(raw_dir, processed_dir):
    from pathlib import Path
    base, proc = Path(raw_dir), Path(processed_dir)
    stops = pd.read_parquet(proc / "stops.parquet")
    stops["stop_id"] = stops.stop_id.astype(str)
    rs = pd.read_parquet(proc / "route_stops.parquet")
    rs["route_id"] = rs.route_id.astype(str)
    rs["stop_id"] = rs.stop_id.astype(str)
    if (base / "routes.txt").exists():
        routes = pd.read_csv(base / "routes.txt", dtype=str).fillna("")
    else:
        routes = rs[["route_id", "route_short"]].drop_duplicates().rename(columns={"route_short": "route_short_name"})
    for col in ["route_long_name", "route_desc"]:
        if col not in routes:
            routes[col] = ""
    return stops, routes, rs


def nearby_stops(lat, lon, cfg=None):
    stops, routes, rs = tables(cfg)
    df = stops.assign(distance_m=haversine_m(lat, lon, stops.stop_lat, stops.stop_lon))
    result = []
    for row in df[df.distance_m <= 2000].sort_values("distance_m").head(8).itertuples():
        ids = rs.loc[rs.stop_id == str(row.stop_id), "route_id"].unique()
        serving = routes[routes.route_id.isin(ids)]
        result.append({"id": str(row.stop_id), "name": row.stop_name, "lat": row.stop_lat,
                       "lon": row.stop_lon, "distance_m": round(row.distance_m),
                       "routes": serving[["route_id", "route_short_name", "route_long_name"]].to_dict("records")})
    return {"stops": result, "source": "Static GTFS", "distance_type": "straight_line"}


def route_stop_table(route_id, direction_id=0, cfg=None):
    _, routes, rs = tables(cfg)
    key = str(route_id).strip()
    is_code = key.startswith("code:")
    key = key[5:] if is_code else key
    # IDs supplied by route search are exact; human shortcuts use code:729.
    matching = routes[routes.route_id == key] if not is_code else routes.iloc[:0]
    if matching.empty:
        matching = routes[routes.route_short_name.str.upper() == key.upper()]
    if matching.empty:
        base = routes.route_short_name.str.upper().str.replace(r"[_\s-]*(?:UP|DOWN)$", "", regex=True)
        matching = routes[base == key.upper()]
        suffix = "DOWN" if direction_id == 1 else "UP"
        preferred = matching[matching.route_short_name.str.upper().str.endswith(suffix)]
        if not preferred.empty:
            matching = preferred
    if matching.empty:
        raise HTTPException(404, "Unknown route")
    rid = str(matching.iloc[0].route_id)
    frame = rs[rs.route_id == rid]
    directed = frame[frame.direction_id == direction_id]
    if directed.empty:
        # Some Delhi feeds encode UP/DOWN as separate route IDs, both with direction_id=0.
        if frame.empty or (direction_id != 0 and not is_code):
            raise HTTPException(404, "No stops for this route direction")
        directed = frame[frame.direction_id == frame.iloc[0].direction_id]
    return directed.sort_values("seq")


def route_geometry(route_id, direction_id=0, cfg=None):
    from pathlib import Path
    cfg = cfg if cfg is not None else load_config()
    frame = route_stop_table(route_id, direction_id, cfg)
    rid, direction = str(frame.iloc[0].route_id), int(frame.iloc[0].direction_id)
    base = resolve(cfg, "raw") / "gtfs"
    return _geometry(str(base), rid, direction, tuple(map(tuple, frame[["stop_lat", "stop_lon"]].values.tolist())))


@lru_cache(maxsize=128)
def _geometry(raw_dir, route_id, direction_id, fallback):
    from pathlib import Path
    base = Path(raw_dir)
    paths = []
    if (base / "trips.txt").exists() and (base / "shapes.parquet").exists():
        trips = pd.read_csv(base / "trips.txt", dtype=str)
        trips = trips[trips.route_id == route_id]
        if "direction_id" in trips:
            trips = trips[trips.direction_id.fillna("0") == str(direction_id)]
        if "shape_id" in trips:
            ids = trips.shape_id.dropna().unique().tolist()
            if ids:
                shapes = pd.read_parquet(base / "shapes.parquet", filters=[("shape_id", "in", ids)])
                paths = [g.sort_values("shape_pt_sequence")[["shape_pt_lat", "shape_pt_lon"]].values.tolist()
                         for _, g in shapes.groupby("shape_id")]
    return {"route_id": route_id, "direction_id": direction_id,
            "paths": paths or [[list(point) for point in fallback]],
            "geometry_basis": "GTFS road shape" if paths else "Straight lines between stops"}
