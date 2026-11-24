from functools import lru_cache
import re
import pandas as pd
from fastapi import HTTPException
from src.config import ROOT
from src.utils import haversine_m

@lru_cache(maxsize=1)
def tables():
    base = ROOT / "data/raw/gtfs"
    stops_file = base / "stops.txt"
    routes_file = base / "routes.txt"
    if stops_file.exists() and routes_file.exists():
        stops = pd.read_csv(stops_file, dtype={"stop_id": str})
        routes = pd.read_csv(routes_file, dtype=str).fillna("")
    else:
        proc = ROOT / "data/processed"
        if not (proc / "stops.parquet").exists():
            proc = ROOT / "data/demo/processed"
        stops = pd.read_parquet(proc / "stops.parquet")
        routes = pd.DataFrame([{"route_id": str(s), "route_short_name": str(s), "route_long_name": str(s)} for s in stops.stop_id.unique()])
    
    rs_path = ROOT / "data/processed/route_stops.parquet"
    if not rs_path.exists():
        rs_path = ROOT / "data/demo/processed/route_stops.parquet"
    rs = pd.read_parquet(rs_path)
    return stops, routes, rs

def nearby_stops(lat, lon):
    stops, routes, rs = tables()
    df = stops.copy()
    df["distance_m"] = haversine_m(lat, lon, df.stop_lat, df.stop_lon)
    df = df[df.distance_m <= 2000].sort_values("distance_m").head(8)
    result = []
    for row in df.itertuples():
        ids = rs.loc[rs.stop_id.astype(str) == str(row.stop_id), "route_id"].astype(str).unique()
        serving = routes[routes.route_id.isin(ids)]
        result.append({"id": str(row.stop_id), "name": row.stop_name, "lat": row.stop_lat,
                       "lon": row.stop_lon, "distance_m": round(row.distance_m),
                       "routes": serving[["route_id", "route_short_name", "route_long_name"]].to_dict("records")})
    return {"stops": result, "source": "Delhi Transport Stack", "distance_type": "straight_line"}

@lru_cache(maxsize=128)
def route_geometry(route_id):
    base = ROOT / "data/raw/gtfs"
    clean_id = str(route_id).strip()

    routes_file = base / "routes.txt"
    trips_file = base / "trips.txt"
    parquet_path = base / "shapes.parquet"

    # If raw GTFS is present, resolve road shape
    if routes_file.exists() and trips_file.exists() and (parquet_path.exists() or (base / "shapes.txt").exists()):
        # 1. Resolve route code to GTFS route_id
        routes = pd.read_csv(routes_file, dtype=str)
        norm_r = routes["route_short_name"].astype(str).str.upper().str.replace(r"[_\s-]*(?:UP|DOWN)$", "", regex=True)
        clean_norm = re.sub(r"(?i)[_\s-]*(?:UP|DOWN)$", "", clean_id.upper())
        match = routes[
            (routes["route_id"] == clean_id) |
            (routes["route_short_name"].str.upper() == clean_id.upper()) |
            (routes["route_long_name"].str.upper() == clean_id.upper()) |
            (norm_r == clean_norm) |
            (routes["route_short_name"].str.upper().str.startswith(clean_norm))
        ]
        target_ids = set(match["route_id"]) if not match.empty else {clean_id}

        # 2. Find shape_ids from trips.txt
        trips = pd.read_csv(trips_file, dtype=str)
        ids = set(trips.loc[trips.route_id.isin(target_ids), "shape_id"].dropna())

        if ids or clean_id.startswith("shp_"):
            if not ids and clean_id.startswith("shp_"):
                ids = {clean_id}
            if parquet_path.exists():
                shapes = pd.read_parquet(parquet_path, filters=[("shape_id", "in", list(ids))])
            else:
                parts = []
                for chunk in pd.read_csv(base / "shapes.txt", dtype={"shape_id": str}, chunksize=100000):
                    parts.append(chunk[chunk.shape_id.isin(ids)])
                shapes = pd.concat(parts) if parts else pd.DataFrame()

            if not shapes.empty:
                paths = [g.sort_values("shape_pt_sequence")[["shape_pt_lat", "shape_pt_lon"]].values.tolist()
                         for _, g in shapes.groupby("shape_id")]
                return {"route_id": route_id, "paths": paths}

    # Fallback: connect stops from route_stops.parquet
    _, _, rs = tables()
    clean_upper = clean_id.upper()
    norm_rs = rs.route_short.astype(str).str.upper().str.replace(r"[_\s-]*(?:UP|DOWN)$", "", regex=True)
    clean_norm = re.sub(r"(?i)[_\s-]*(?:UP|DOWN)$", "", clean_upper)
    matching = rs[
        (rs.route_short.astype(str).str.upper() == clean_upper) |
        (norm_rs == clean_norm) |
        (rs.route_id.astype(str) == clean_id) |
        (rs.route_short.astype(str).str.upper().str.startswith(clean_norm))
    ].sort_values("seq")
    if not matching.empty:
        coords = matching.drop_duplicates(subset=["seq"])[["stop_lat", "stop_lon"]].values.tolist()
        return {"route_id": route_id, "paths": [coords]}

    raise HTTPException(404, f"No route geometry available for {route_id}")
