from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.utils import haversine_m, norm_name

RANK = {"seats": 0, "standing": 1, "crowded": 2, "unknown": 3}
CONF_RANK = {"low": 0, "medium": 1, "high": 2}


class JourneyPlanner:
    """Direct-route bus planner using learned speeds and crowding.

    TODO: add one-transfer search (join two legs at nearby stops).
    TODO: replace the fixed wait time with headways from GTFS.
    """

    def __init__(self, processed_dir, cfg):
        d = Path(processed_dir)
        self.cfg = cfg
        from src.routing.schedule import Schedule
        self.schedule = Schedule(d / "schedule.sqlite") if (d / "schedule.sqlite").exists() else None
        self.stops = pd.read_parquet(d / "stops.parquet")
        self.rs = pd.read_parquet(d / "route_stops.parquet")
        self.rs["route_id"] = self.rs["route_id"].astype(str)

        sp = pd.read_parquet(d / "segment_speed.parquet") if (d / "segment_speed.parquet").exists() else pd.DataFrame(columns=["route_id", "day_type", "hour", "speed_kmh"])
        self.speed = {(str(r.route_id), r.day_type, int(r.hour)): r.speed_kmh for r in sp.itertuples()}
        city = pd.read_parquet(d / "city_speed.parquet") if (d / "city_speed.parquet").exists() else pd.DataFrame(columns=["day_type", "hour", "speed_kmh"])
        self.city = {(r.day_type, int(r.hour)): r.speed_kmh for r in city.itertuples()}

        cpath = d / "crowding.parquet"
        if cpath.exists():
            self.crowd_df = pd.read_parquet(cpath)
            self.crowd_df["route_id"] = self.crowd_df["route_id"].astype(str)
            self.crowd = self.crowd_df.set_index(
                ["route_id", "direction_id", "day_type", "half_hour"]
            ).sort_index()
        else:
            self.crowd_df, self.crowd = None, None

        # Load route to shape_id mapping for true road geometry
        self.gtfs_raw = Path("data/raw/gtfs")
        if not self.gtfs_raw.exists():
            self.gtfs_raw = d.parent / "raw/gtfs"
        self.route_shapes = {}
        trips_path = self.gtfs_raw / "trips.txt"
        if trips_path.exists():
            try:
                tdf = pd.read_csv(trips_path, dtype=str)
                for r in tdf.dropna(subset=["shape_id"]).drop_duplicates(subset=["route_id", "direction_id"]).itertuples():
                    r_id = str(r.route_id)
                    d_id = str(r.direction_id) if pd.notna(r.direction_id) else "0"
                    self.route_shapes[(r_id, d_id)] = r.shape_id
                    self.route_shapes[(r_id, "")] = r.shape_id
            except Exception:
                pass

    def get_road_geometry(self, route_id, direction_id, lat_b, lon_b, lat_a, lon_a, fallback_stops):
        """Slice real road shape points from shapes.parquet."""
        import numpy as np
        shp_id = (
            self.route_shapes.get((str(route_id), str(direction_id)))
            or self.route_shapes.get((str(route_id), "0"))
            or self.route_shapes.get((str(route_id), ""))
        )
        if not shp_id:
            return fallback_stops
        try:
            parquet_path = self.gtfs_raw / "shapes.parquet"
            if not parquet_path.exists():
                return fallback_stops
            shapes = pd.read_parquet(parquet_path, filters=[("shape_id", "==", shp_id)])
            if shapes.empty:
                return fallback_stops
            shapes = shapes.sort_values("shape_pt_sequence")
            pts = shapes[["shape_pt_lat", "shape_pt_lon"]].values.astype(float)
            if len(pts) < 2:
                return fallback_stops

            idx_b = int(np.argmin((pts[:, 0] - lat_b) ** 2 + (pts[:, 1] - lon_b) ** 2))
            idx_a = int(np.argmin((pts[:, 0] - lat_a) ** 2 + (pts[:, 1] - lon_a) ** 2))

            step = 1 if idx_a >= idx_b else -1
            sliced = pts[idx_b:idx_a + step:step].tolist()
            if len(sliced) < 2:
                return fallback_stops
            return [[lat_b, lon_b]] + sliced + [[lat_a, lon_a]]
        except Exception:
            return fallback_stops

    # ---------- helpers ----------
    def speed_kmh(self, route_id, day, hour):
        return (
            self.speed.get((str(route_id), day, hour))
            or self.city.get((day, hour))
            or self.cfg["speed"]["default_kmh"]
        )

    def resolve(self, q: str):
        """'lat,lon' or a stop name -> (lat, lon)."""
        if not q.strip():
            raise ValueError("Enter a stop name or latitude,longitude")
        if "," in q:
            try:
                a, b = q.split(",", 1)
                lat, lon = float(a), float(b)
            except ValueError:
                raise ValueError("Coordinates must be latitude,longitude")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Coordinates are outside the valid range")
            return lat, lon
        key = norm_name(pd.Series([q])).iloc[0]
        if not key:
            raise ValueError("Enter a valid stop name")
        exact = self.stops[self.stops["stop_norm"] == key]
        hit = self.stops[self.stops["stop_norm"].str.contains(key, regex=False)]
        if not exact.empty:
            hit = exact
        if hit.empty:
            raise ValueError(f"Could not find a location for '{q}'")
        r = hit.iloc[0]
        return float(r.stop_lat), float(r.stop_lon)

    def near_stops(self, lat, lon):
        d = haversine_m(lat, lon, self.stops["stop_lat"].values, self.stops["stop_lon"].values)
        out = self.stops[["stop_id"]].copy()
        out["walk_m"] = d
        out = out[out["walk_m"] <= self.cfg["journey"]["walk_max_m"]]
        return out.sort_values("walk_m").head(15)

    def crowding_for(self, route_id, direction, day, half_hour, seq_b, seq_a):
        if self.crowd is None:
            return [], "unknown", "low"
        try:
            g = self.crowd.loc[[(route_id, direction, day, half_hour)]]
        except KeyError:
            return [], "unknown", "low"
        by_seg = {int(r.seg): (r.level, r.confidence) for r in g.itertuples()}
        levels = [by_seg.get(s, ("unknown", "low"))[0] for s in range(seq_b, seq_a)]
        known = [by_seg[s] for s in range(seq_b, seq_a) if s in by_seg]
        if not known:
            return levels, "unknown", "low"
        if len(known) < seq_a - seq_b:
            return levels, "unknown", "low"
        worst = max((k[0] for k in known), key=lambda x: RANK[x])
        conf = min((k[1] for k in known), key=lambda x: CONF_RANK[x])
        return levels, worst, conf

    # ---------- main ----------
    def plan(self, origin, dest, depart: datetime, mode="fastest", n=3):
        j = self.cfg["journey"]
        day = "weekend" if depart.weekday() >= 5 else "weekday"
        hh = depart.hour * 2 + (1 if depart.minute >= 30 else 0)

        o, d = self.near_stops(*origin), self.near_stops(*dest)
        if o.empty or d.empty:
            return []
        board = self.rs.merge(o, on="stop_id")
        alight = self.rs.merge(d, on="stop_id")
        c = board.merge(alight, on=["route_id", "direction_id"], suffixes=("_b", "_a"))
        c = c[c["seq_b"] < c["seq_a"]].copy()
        if c.empty:
            return []
        c["walk_total"] = c["walk_m_b"] + c["walk_m_a"]
        c = c.sort_values("walk_total").drop_duplicates(["route_id", "direction_id"])
        c = c.head(j["max_candidate_routes"])

        walk_per_min = j["walk_kmh"] * 1000 / 60
        options = []
        for r in c.itertuples():
            km = (r.dist_m_a - r.dist_m_b) / 1000
            stops_n = r.seq_a - r.seq_b
            walk = r.walk_total / walk_per_min
            sp = self.speed_kmh(r.route_id, day, depart.hour)
            sp_q = self.speed_kmh(r.route_id, day, j["quiet_hour"])
            ride = km / sp * 60 + stops_n * j["dwell_min"]
            ride_q = km / sp_q * 60 + stops_n * j["dwell_min"]
            wait = j["wait_min"]
            scheduled = None
            if self.schedule:
                scheduled = self.schedule.next_trip(r.route_id, r.stop_id_b, r.stop_id_a, depart, r.walk_m_b / walk_per_min)
                if scheduled is None:
                    continue
                wait, ride = scheduled["wait"], scheduled["ride"]
            levels, worst, conf = self.crowding_for(
                r.route_id, r.direction_id, ("weekend" if datetime.fromisoformat(scheduled["departure"]).weekday() >= 5 else "weekday") if scheduled else day, (datetime.fromisoformat(scheduled["departure"]).hour * 2 + datetime.fromisoformat(scheduled["departure"]).minute // 30) if scheduled else hh, r.seq_b, r.seq_a
            )
            stops_rows = list(self.rs[
                (self.rs["route_id"] == r.route_id)
                & (self.rs["direction_id"] == r.direction_id)
                & self.rs["seq"].between(r.seq_b, r.seq_a)
            ].sort_values("seq").itertuples())

            stations = [
                {"name": row.stop_name, "lat": float(row.stop_lat), "lon": float(row.stop_lon), "seq": int(row.seq)}
                for row in stops_rows
            ]
            fallback_geom = [[float(row.stop_lat), float(row.stop_lon)] for row in stops_rows]
            road_geom = self.get_road_geometry(
                r.route_id, r.direction_id,
                float(r.stop_lat_b), float(r.stop_lon_b),
                float(r.stop_lat_a), float(r.stop_lon_a),
                fallback_geom
            )

            options.append({
                "route": getattr(r, "route_short_b", getattr(r, "route_short_name", "")),
                "route_id": str(r.route_id),
                "time_basis": "GTFS schedule, not live ETA" if scheduled else "route-speed baseline; fixed waiting assumption",
                "scheduled_departure": scheduled["departure"] if scheduled else None,
                "direction": int(r.direction_id),
                "total_min": round(walk + wait + ride, 1),
                "ride_min": round(ride, 1),
                "walk_min": round(walk, 1),
                "wait_min": round(wait, 1),
                "usual_offpeak_min": round(walk + j["wait_min"] + ride_q, 1),
                "crowding": worst,
                "confidence": conf,
                "crowding_by_stretch": levels,
                "crowding_coverage": round(sum(level != "unknown" for level in levels) / max(len(levels), 1), 3),
                "segments": [
                    {"from": stops_rows[i].stop_name, "to": stops_rows[i + 1].stop_name,
                     "level": levels[i] if i < len(levels) else "unknown"}
                    for i in range(len(stops_rows) - 1)
                ],
                "board": {"name": r.stop_name_b, "lat": float(r.stop_lat_b), "lon": float(r.stop_lon_b)},
                "alight": {"name": r.stop_name_a, "lat": float(r.stop_lat_a), "lon": float(r.stop_lon_a)},
                "stops": int(stops_n),
                "stations": stations,
                "geometry": road_geom,
            })

        if mode == "comfortable":
            options.sort(key=lambda x: (RANK[x["crowding"]], x["total_min"]))
        else:
            options.sort(key=lambda x: x["total_min"])
        return options[:n]


    def plan_with_transfer(self, origin, dest, depart, mode="fastest", n=5):
        direct = self.plan(origin, dest, depart, mode, n)
        if direct:
            return [dict(o, transfers=0) for o in direct]
        # Bounded same-stop interchange search; only used when no direct service is available.
        origin_ids = set(self.near_stops(*origin).stop_id)
        dest_ids = set(self.near_stops(*dest).stop_id)
        reachable, preceding = set(), set()
        for _, g in self.rs.groupby(["route_id", "direction_id"]):
            b = g[g.stop_id.isin(origin_ids)]
            a = g[g.stop_id.isin(dest_ids)]
            if not b.empty:
                reachable.update(g.loc[g.seq > b.seq.min(), "stop_id"])
            if not a.empty:
                preceding.update(g.loc[g.seq < a.seq.max(), "stop_id"])
        transfers = self.stops[self.stops.stop_id.isin(reachable & preceding)].copy()
        if transfers.empty:
            return []
        transfers["detour"] = haversine_m(origin[0],origin[1],transfers.stop_lat,transfers.stop_lon) + haversine_m(dest[0],dest[1],transfers.stop_lat,transfers.stop_lon)
        results = []
        seen = set()
        for stop in transfers.sort_values("detour").head(8).itertuples():
            point = (stop.stop_lat, stop.stop_lon)
            for first in self.plan(origin, point, depart, mode, 2):
                onward = depart + timedelta(minutes=first["total_min"] + 2)
                for second in self.plan(point, dest, onward, mode, 2):
                    if first["route_id"] == second["route_id"]:
                        continue
                    key = (first["route_id"],second["route_id"],first["alight"]["name"],second["board"]["name"])
                    if key in seen:continue
                    seen.add(key)
                    levels = first["crowding_by_stretch"] + second["crowding_by_stretch"]
                    worst = max([first["crowding"],second["crowding"]],key=lambda x:RANK[x])
                    results.append({
                        "route":first["route"]+" + "+second["route"], "route_id":first["route_id"],
                        "total_min":round(first["total_min"]+second["total_min"]+2,1),
                        "ride_min":round(first["ride_min"]+second["ride_min"],1),
                        "walk_min":round(first["walk_min"]+second["walk_min"],1),
                        "wait_min":round(first["wait_min"]+second["wait_min"],1),
                        "transfer_min":2, "transfers":1, "transfer_stop":stop.stop_name,
                        "legs":[first,second], "board":first["board"], "alight":second["alight"],
                        "stations":first.get("stations", []) + second.get("stations", []),
                        "geometry":first["geometry"]+second["geometry"],
                        "segments":first.get("segments",[])+second.get("segments",[]),
                        "crowding":worst, "confidence":min([first["confidence"],second["confidence"]],key=lambda x:CONF_RANK[x]),
                        "crowding_by_stretch":levels,
                        "crowding_coverage":sum(x!="unknown" for x in levels)/max(len(levels),1),
                        "time_basis":first["time_basis"]+"; two-minute transfer buffer; straight-line access walks",
                    })
        results.sort(key=(lambda x:(RANK[x["crowding"]],x["total_min"])) if mode=="comfortable" else (lambda x:x["total_min"]))
        return results[:n]
