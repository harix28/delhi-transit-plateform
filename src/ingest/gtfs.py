from pathlib import Path

import pandas as pd

from src.utils import haversine_m, norm_name


def build_gtfs_tables(gtfs_dir, out_dir):
    """Build stops.parquet and route_stops.parquet.

    For each (route, direction) the trip with the most stops is used as the
    representative stop sequence.
    """
    gtfs_dir, out_dir = Path(gtfs_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stops = pd.read_csv(
        gtfs_dir / "stops.txt",
        usecols=["stop_id", "stop_name", "stop_lat", "stop_lon"],
        dtype={"stop_id": str},
    )
    routes = pd.read_csv(
        gtfs_dir / "routes.txt",
        usecols=["route_id", "route_short_name"],
        dtype={"route_id": str, "route_short_name": str},
    )
    trips = pd.read_csv(gtfs_dir / "trips.txt", dtype=str)
    if "direction_id" not in trips.columns:
        trips["direction_id"] = "0"
    trips["direction_id"] = trips["direction_id"].fillna("0").astype(int)

    st = pd.read_csv(
        gtfs_dir / "stop_times.txt",
        usecols=["trip_id", "stop_id", "stop_sequence"],
        dtype={"trip_id": str, "stop_id": str},
    )

    n_stops = st.groupby("trip_id").size().rename("n_stops")
    trips = trips[["route_id", "trip_id", "direction_id"]].join(n_stops, on="trip_id")
    trips = trips.dropna(subset=["n_stops"])
    rep = trips.sort_values("n_stops", ascending=False).drop_duplicates(
        ["route_id", "direction_id"]
    )

    rs = st.merge(rep[["trip_id", "route_id", "direction_id"]], on="trip_id")
    rs["stop_sequence"] = pd.to_numeric(rs["stop_sequence"])
    rs = rs.sort_values(["route_id", "direction_id", "stop_sequence"])
    rs["seq"] = rs.groupby(["route_id", "direction_id"]).cumcount()
    rs = rs.merge(stops, on="stop_id").merge(routes, on="route_id", how="left")
    rs = rs.sort_values(["route_id", "direction_id", "seq"]).reset_index(drop=True)

    rs["route_short"] = (
        rs["route_short_name"].fillna(rs["route_id"]).astype(str).str.strip().str.upper()
    )
    rs["stop_norm"] = norm_name(rs["stop_name"])

    keys = [rs["route_id"], rs["direction_id"]]
    prev_lat = rs.groupby(keys)["stop_lat"].shift()
    prev_lon = rs.groupby(keys)["stop_lon"].shift()
    seg = pd.Series(
        haversine_m(prev_lat, prev_lon, rs["stop_lat"], rs["stop_lon"]), index=rs.index
    ).fillna(0.0)
    rs["dist_m"] = seg.groupby(keys).cumsum()

    rs = rs[
        [
            "route_id", "route_short", "direction_id", "seq", "stop_id",
            "stop_name", "stop_norm", "stop_lat", "stop_lon", "dist_m",
        ]
    ]

    stops = stops.copy()
    stops["stop_norm"] = norm_name(stops["stop_name"])
    stops.to_parquet(out_dir / "stops.parquet", index=False)
    rs.to_parquet(out_dir / "route_stops.parquet", index=False)
    return stops, rs
