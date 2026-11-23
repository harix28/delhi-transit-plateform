from functools import lru_cache
import pandas as pd
from fastapi import HTTPException
from src.config import ROOT
from src.utils import haversine_m

@lru_cache(maxsize=1)
def tables():
    base = ROOT / "data/raw/gtfs"
    stops = pd.read_csv(base / "stops.txt", dtype={"stop_id": str})
    routes = pd.read_csv(base / "routes.txt", dtype=str).fillna("")
    rs = pd.read_parquet(ROOT / "data/processed/route_stops.parquet")
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

    # 1. Resolve route code to GTFS route_id
    routes = pd.read_csv(base / "routes.txt", dtype=str)
    match = routes[
        (routes["route_id"] == clean_id) |
        (routes["route_short_name"].str.upper() == clean_id.upper()) |
        (routes["route_long_name"].str.upper() == clean_id.upper())
    ]
    target_ids = set(match["route_id"]) if not match.empty else {clean_id}

    # 2. Find shape_ids from trips.txt
    trips = pd.read_csv(base / "trips.txt", dtype=str)
    ids = set(trips.loc[trips.route_id.isin(target_ids), "shape_id"].dropna())

    if not ids:
        if clean_id.startswith("shp_"):
            ids = {clean_id}
        else:
            raise HTTPException(404, f"No route shape available for {route_id}")

    # 3. Read shape points from shapes.parquet (fast road coordinates)
    parquet_path = base / "shapes.parquet"
    if parquet_path.exists():
        shapes = pd.read_parquet(parquet_path, filters=[("shape_id", "in", list(ids))])
    else:
        parts = []
        for chunk in pd.read_csv(base / "shapes.txt", dtype={"shape_id": str}, chunksize=100000):
            parts.append(chunk[chunk.shape_id.isin(ids)])
        shapes = pd.concat(parts) if parts else pd.DataFrame()

    if shapes.empty:
        raise HTTPException(404, f"Shape coordinates not found for {route_id}")

    paths = [g.sort_values("shape_pt_sequence")[["shape_pt_lat", "shape_pt_lon"]].values.tolist()
             for _, g in shapes.groupby("shape_id")]
    return {"route_id": route_id, "paths": paths}
