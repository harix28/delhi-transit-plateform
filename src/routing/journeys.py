from datetime import datetime, timedelta
from pathlib import Path
import re

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
            try:
                # Optimized low-memory read: downcast types to prevent Render 512MB container OOM
                cols = ["route_id", "direction_id", "day_type", "half_hour", "seg", "level", "confidence"]
                c_df = pd.read_parquet(cpath, columns=cols)
                for col in ["route_id", "day_type", "level", "confidence"]:
                    c_df[col] = c_df[col].astype("category")
                c_df["direction_id"] = c_df["direction_id"].astype("int8")
                c_df["half_hour"] = c_df["half_hour"].astype("int8")
                c_df["seg"] = c_df["seg"].astype("int16")
                self.crowd = c_df.set_index(
                    ["route_id", "direction_id", "day_type", "half_hour"]
                ).sort_index()
                del c_df
            except Exception:
                self.crowd = None
            self.crowd_df = None
        else:
            self.crowd_df, self.crowd = None, None

        # Load route to shape_id mapping with minimal heap footprint using DictReader
        import csv
        from src.config import resolve
        self.gtfs_raw = resolve(cfg, "raw") / "gtfs"
        self.route_shapes = {}
        trips_path = self.gtfs_raw / "trips.txt"
        if trips_path.exists():
            try:
                with open(trips_path, "r", encoding="utf-8") as tf:
                    reader = csv.DictReader(tf)
                    for row in reader:
                        shp = row.get("shape_id")
                        if shp:
                            r_id = str(row.get("route_id", "")).strip()
                            d_id = str(row.get("direction_id", "0") or "0").strip()
                            if (r_id, d_id) not in self.route_shapes:
                                self.route_shapes[(r_id, d_id)] = shp
                                self.route_shapes[(r_id, "")] = shp
            except Exception:
                pass

        # Pre-resolve Major Delhi Transit Hubs for multi-hop fallback routing
        HUB_NAMES = [
            'Kashmere Gate', 'Anand Vihar', 'Sarai Kale Khan', 'Central Secretariat',
            'Shivaji Stadium', 'AIIMS', 'Dhaula Kuan', 'Azadpur', 'Peera Garhi',
            'Punjabi Bagh', 'Uttam Nagar', 'Nehru Place', 'Mehrauli', 'Jahangirpuri',
            'Tilak Nagar', 'Lajpat Nagar', 'Badarpur', 'Rohini', 'Dwarka', 'Najafgarh'
        ]
        self.major_hubs = []
        for hname in HUB_NAMES:
            m = self.stops[self.stops["stop_name"].str.contains(hname, case=False, na=False)]
            if not m.empty:
                r = m.iloc[0]
                self.major_hubs.append({
                    "name": hname,
                    "stop_name": r.stop_name,
                    "lat": float(r.stop_lat),
                    "lon": float(r.stop_lon)
                })

    def get_road_geometry(self, route_id, direction_id, lat_b, lon_b, lat_a, lon_a, fallback_stops):
        """Slice real road shape points from shapes.parquet."""
        import numpy as np
        shapes_map = getattr(self, "route_shapes", {})
        shp_id = (
            shapes_map.get((str(route_id), str(direction_id)))
            or shapes_map.get((str(route_id), "0"))
            or shapes_map.get((str(route_id), ""))
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

        q_clean = q.strip()
        hit = pd.DataFrame()

        # Step 1: Exact raw stop name match (highest priority, preserves exact names like "ISBT Anand Vihar")
        exact_raw = self.stops[self.stops["stop_name"].str.strip().str.lower() == q_clean.lower()]
        if not exact_raw.empty:
            hit = exact_raw

        # Step 2: Exact normalized stop name match
        if hit.empty:
            key = norm_name(pd.Series([q_clean])).iloc[0]
            if key:
                exact_norm = self.stops[self.stops["stop_norm"] == key]
                if not exact_norm.empty:
                    hit = exact_norm
                else:
                    hit = self.stops[self.stops["stop_norm"].str.contains(key, regex=False)]

        # Step 3: Raw case-insensitive substring match
        if hit.empty:
            raw_hit = self.stops[self.stops["stop_name"].str.contains(q_clean, case=False, regex=False, na=False)]
            if not raw_hit.empty:
                hit = raw_hit

        # Step 4: Token match for composite stop names
        if hit.empty:
            tokens = [t for t in re.sub(r'[^a-zA-Z0-9]', ' ', q_clean).split() if len(t) > 2]
            for t in tokens:
                token_hit = self.stops[self.stops["stop_name"].str.contains(t, case=False, regex=False, na=False)]
                if not token_hit.empty:
                    hit = token_hit
                    break

        if hit.empty:
            raise ValueError(f"Could not find a location for '{q}'")
        r = hit.iloc[0]
        return float(r.stop_lat), float(r.stop_lon)

    def near_stops(self, lat, lon, max_dist=None):
        max_d = min(max_dist if max_dist is not None else self.cfg["journey"]["walk_max_m"],
                    self.cfg["journey"]["walk_max_m"])
        d = haversine_m(lat, lon, self.stops["stop_lat"].values, self.stops["stop_lon"].values)
        out = self.stops[["stop_id", "stop_name", "stop_lat", "stop_lon"]].copy()
        out["walk_m"] = d
        filtered = out[out["walk_m"] <= max_d]
        return filtered.sort_values("walk_m").head(25)

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
    def plan(self, origin, dest, depart: datetime = None, mode="fastest", n=3, max_walk_m=None):
        if depart is None:
            from zoneinfo import ZoneInfo
            depart = datetime.now(ZoneInfo("Asia/Kolkata"))
        j = self.cfg["journey"]
        day = "weekend" if depart.weekday() >= 5 else "weekday"
        hh = depart.hour * 2 + (1 if depart.minute >= 30 else 0)

        o = self.near_stops(*origin, max_dist=max_walk_m)
        d = self.near_stops(*dest, max_dist=max_walk_m)
        if o.empty or d.empty:
            return []
        board = self.rs.merge(o[["stop_id", "walk_m"]], on="stop_id")
        alight = self.rs.merge(d[["stop_id", "walk_m"]], on="stop_id")
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

            dep_dt = None
            if scheduled and scheduled.get("departure"):
                try:
                    dep_dt = datetime.fromisoformat(scheduled["departure"])
                except Exception:
                    dep_dt = None
            dep_day = ("weekend" if dep_dt.weekday() >= 5 else "weekday") if dep_dt else day
            dep_hh = (dep_dt.hour * 2 + dep_dt.minute // 30) if dep_dt else hh

            levels, worst, conf = self.crowding_for(
                r.route_id, r.direction_id, dep_day, dep_hh, r.seq_b, r.seq_a
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
                "time_basis": "GTFS schedule departure" if scheduled else "Estimated timing; timetable unavailable",
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

    def _build_leg(self, route_id, direction_id, seq_b, seq_a, depart, walk_m_b=0.0, walk_m_a=0.0, stop_id_b=None, stop_id_a=None):
        j = self.cfg["journey"]
        day = "weekend" if depart.weekday() >= 5 else "weekday"
        hh = depart.hour * 2 + (1 if depart.minute >= 30 else 0)
        walk_per_min = j["walk_kmh"] * 1000 / 60

        sub = self.rs[
            (self.rs["route_id"] == str(route_id))
            & (self.rs["direction_id"] == int(direction_id))
            & self.rs["seq"].between(seq_b, seq_a)
        ].sort_values("seq")

        if sub.empty:
            return None

        stops_rows = list(sub.itertuples())
        b_row = stops_rows[0]
        a_row = stops_rows[-1]

        km = max(0.0, (a_row.dist_m - b_row.dist_m) / 1000)
        stops_n = max(1, a_row.seq - b_row.seq)
        walk = (walk_m_b + walk_m_a) / walk_per_min
        sp = self.speed_kmh(route_id, day, depart.hour)
        sp_q = self.speed_kmh(route_id, day, j["quiet_hour"])
        ride = km / sp * 60 + stops_n * j["dwell_min"]
        ride_q = km / sp_q * 60 + stops_n * j["dwell_min"]
        wait = j["wait_min"]

        scheduled = None
        sid_b = stop_id_b or b_row.stop_id
        sid_a = stop_id_a or a_row.stop_id
        if self.schedule:
            scheduled = self.schedule.next_trip(route_id, sid_b, sid_a, depart, walk_m_b / walk_per_min)
            if scheduled is None:
                return None
            wait, ride = scheduled["wait"], scheduled["ride"]

        dep_dt = None
        if scheduled and scheduled.get("departure"):
            try:
                dep_dt = datetime.fromisoformat(scheduled["departure"])
            except Exception:
                dep_dt = None
        dep_day = ("weekend" if dep_dt.weekday() >= 5 else "weekday") if dep_dt else day
        dep_hh = (dep_dt.hour * 2 + dep_dt.minute // 30) if dep_dt else hh

        levels, worst, conf = self.crowding_for(
            str(route_id), int(direction_id), dep_day, dep_hh, seq_b, seq_a
        )

        stations = [
            {"name": row.stop_name, "lat": float(row.stop_lat), "lon": float(row.stop_lon), "seq": int(row.seq)}
            for row in stops_rows
        ]
        fallback_geom = [[float(row.stop_lat), float(row.stop_lon)] for row in stops_rows]
        road_geom = self.get_road_geometry(
            str(route_id), int(direction_id),
            float(b_row.stop_lat), float(b_row.stop_lon),
            float(a_row.stop_lat), float(a_row.stop_lon),
            fallback_geom
        )

        route_short = getattr(b_row, "route_short", getattr(b_row, "route_short_name", str(route_id)))

        return {
            "route": route_short,
            "route_id": str(route_id),
            "time_basis": "GTFS schedule departure" if scheduled else "Estimated timing; timetable unavailable",
            "scheduled_departure": scheduled["departure"] if scheduled else None,
            "direction": int(direction_id),
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
            "board": {"name": b_row.stop_name, "lat": float(b_row.stop_lat), "lon": float(b_row.stop_lon)},
            "alight": {"name": a_row.stop_name, "lat": float(a_row.stop_lat), "lon": float(a_row.stop_lon)},
            "board_stop_name": b_row.stop_name,
            "alight_stop_name": a_row.stop_name,
            "stops": int(stops_n),
            "stations": stations,
            "geometry": road_geom,
        }

    def plan_with_transfer(self, origin, dest, depart=None, mode="fastest", n=5):
        if depart is None:
            from zoneinfo import ZoneInfo
            depart = datetime.now(ZoneInfo("Asia/Kolkata"))
        # 1. Direct Service (transfers = 0)
        direct = self.plan(origin, dest, depart, mode, n)
        results = [dict(o, transfers=0) for o in direct]

        seen_route_pairs = {o["route"] for o in results}
        seen_keys = set()

        def add_transfer_journey(leg1, leg2, transfer_name, buffer_min=2, basis_note=None):
            pair_name = f"{leg1['route']} + {leg2['route']}"
            t_key = (leg1['route_id'], leg2['route_id'], transfer_name)
            if t_key in seen_keys or pair_name in seen_route_pairs:
                return False
            seen_keys.add(t_key)
            seen_route_pairs.add(pair_name)

            levels = leg1.get("crowding_by_stretch", []) + leg2.get("crowding_by_stretch", [])
            worst = max([leg1.get("crowding", "unknown"), leg2.get("crowding", "unknown")], key=lambda x: RANK.get(x, 3))
            conf = min([leg1.get("confidence", "low"), leg2.get("confidence", "low")], key=lambda x: CONF_RANK.get(x, 0))

            results.append({
                "route": pair_name,
                "route_id": f"{leg1['route_id']}+{leg2['route_id']}",
                "total_min": round(leg1["total_min"] + leg2["total_min"] + buffer_min, 1),
                "ride_min": round(leg1["ride_min"] + leg2["ride_min"], 1),
                "walk_min": round(leg1["walk_min"] + leg2["walk_min"], 1),
                "wait_min": round(leg1["wait_min"] + leg2["wait_min"], 1),
                "transfer_min": buffer_min,
                "transfers": 1,
                "transfer_stop": transfer_name,
                "legs": [leg1, leg2],
                "board": leg1["board"],
                "alight": leg2["alight"],
                "stations": leg1.get("stations", []) + leg2.get("stations", []),
                "geometry": leg1.get("geometry", []) + leg2.get("geometry", []),
                "segments": leg1.get("segments", []) + leg2.get("segments", []),
                "crowding": worst,
                "confidence": conf,
                "crowding_by_stretch": levels,
                "crowding_coverage": round(sum(x != "unknown" for x in levels) / max(len(levels), 1), 3),
                "time_basis": basis_note or f"Transfer at {transfer_name} · {buffer_min}-min connection buffer",
            })
            return True

        # 2. Vectorized 1-Transfer via Connecting Stops
        search_radii = [self.cfg["journey"]["walk_max_m"]]
        for rad in search_radii:
            o_stops = self.near_stops(*origin, max_dist=rad)
            d_stops = self.near_stops(*dest, max_dist=rad)
            if o_stops.empty or d_stops.empty:
                continue

            r1 = self.rs.merge(o_stops[["stop_id", "walk_m"]], on="stop_id")
            r2 = self.rs.merge(d_stops[["stop_id", "walk_m"]], on="stop_id")
            if r1.empty or r2.empty:
                continue

            r1 = r1[["route_id", "direction_id", "seq", "dist_m", "stop_name", "stop_lat", "stop_lon", "walk_m", "stop_id"]].rename(
                columns={"seq": "seq_b", "dist_m": "dist_m_b", "stop_name": "stop_name_b", "stop_lat": "stop_lat_b", "stop_lon": "stop_lon_b", "walk_m": "walk_m_b", "stop_id": "stop_id_b"}
            )
            r2 = r2[["route_id", "direction_id", "seq", "dist_m", "stop_name", "stop_lat", "stop_lon", "walk_m", "stop_id"]].rename(
                columns={"seq": "seq_a", "dist_m": "dist_m_a", "stop_name": "stop_name_a", "stop_lat": "stop_lat_a", "stop_lon": "stop_lon_a", "walk_m": "walk_m_a", "stop_id": "stop_id_a"}
            )

            downstream = self.rs.merge(r1, on=["route_id", "direction_id"])
            downstream = downstream[downstream["seq"] > downstream["seq_b"]]

            upstream = self.rs.merge(r2, on=["route_id", "direction_id"])
            upstream = upstream[upstream["seq"] < upstream["seq_a"]]

            if downstream.empty or upstream.empty:
                continue

            # Join on stop_id
            transfers = downstream.merge(upstream, on="stop_id", suffixes=("_1", "_2"))
            transfers = transfers[transfers["route_id_1"] != transfers["route_id_2"]]

            if not transfers.empty:
                km1 = (transfers["dist_m_1"] - transfers["dist_m_b"]) / 1000
                km2 = (transfers["dist_m_a"] - transfers["dist_m_2"]) / 1000
                stops1 = transfers["seq_1"] - transfers["seq_b"]
                stops2 = transfers["seq_a"] - transfers["seq_2"]
                est_time = (km1 + km2) / 16.0 * 60 + (stops1 + stops2) * 0.4 + (transfers["walk_m_b"] + transfers["walk_m_a"]) / (5000 / 60)
                transfers = transfers.assign(est_time=est_time)

                best_transfers = transfers.sort_values("est_time").drop_duplicates(subset=["route_id_1", "route_id_2"]).head(n * 2)
                for row in best_transfers.itertuples():
                    if len(results) >= n + 2:
                        break
                    leg1 = self._build_leg(row.route_id_1, row.direction_id_1, row.seq_b, row.seq_1, depart, walk_m_b=row.walk_m_b, walk_m_a=0.0, stop_id_b=row.stop_id_b, stop_id_a=row.stop_id)
                    if not leg1:
                        continue
                    onward = depart + timedelta(minutes=leg1["total_min"] + 2)
                    leg2 = self._build_leg(row.route_id_2, row.direction_id_2, row.seq_2, row.seq_a, onward, walk_m_b=0.0, walk_m_a=row.walk_m_a, stop_id_b=row.stop_id, stop_id_a=row.stop_id_a)
                    if not leg2:
                        continue
                    add_transfer_journey(leg1, leg2, row.stop_name_1, buffer_min=2)

            # If still needed, join on stop_norm
            if len(results) < n and "stop_norm" in self.rs.columns:
                transfers_norm = downstream.merge(upstream, on="stop_norm", suffixes=("_1", "_2"))
                transfers_norm = transfers_norm[transfers_norm["route_id_1"] != transfers_norm["route_id_2"]]
                # Equal names can belong to opposite platforms or distant stops.
                transfers_norm = transfers_norm[
                    haversine_m(transfers_norm.stop_lat_1, transfers_norm.stop_lon_1,
                                transfers_norm.stop_lat_2, transfers_norm.stop_lon_2) <= 1.0
                ]
                if not transfers_norm.empty:
                    km1 = (transfers_norm["dist_m_1"] - transfers_norm["dist_m_b"]) / 1000
                    km2 = (transfers_norm["dist_m_a"] - transfers_norm["dist_m_2"]) / 1000
                    stops1 = transfers_norm["seq_1"] - transfers_norm["seq_b"]
                    stops2 = transfers_norm["seq_a"] - transfers_norm["seq_2"]
                    est_time = (km1 + km2) / 16.0 * 60 + (stops1 + stops2) * 0.4 + (transfers_norm["walk_m_b"] + transfers_norm["walk_m_a"]) / (5000 / 60)
                    transfers_norm = transfers_norm.assign(est_time=est_time)

                    best_norm = transfers_norm.sort_values("est_time").drop_duplicates(subset=["route_id_1", "route_id_2"]).head(n)
                    for row in best_norm.itertuples():
                        if len(results) >= n + 2:
                            break
                        leg1 = self._build_leg(row.route_id_1, row.direction_id_1, row.seq_b, row.seq_1, depart, walk_m_b=row.walk_m_b, walk_m_a=0.0, stop_id_b=row.stop_id_b, stop_id_a=row.stop_id_1)
                        if not leg1:
                            continue
                        onward = depart + timedelta(minutes=leg1["total_min"] + 2)
                        leg2 = self._build_leg(row.route_id_2, row.direction_id_2, row.seq_2, row.seq_a, onward, walk_m_b=0.0, walk_m_a=row.walk_m_a, stop_id_b=row.stop_id_2, stop_id_a=row.stop_id_a)
                        if not leg2:
                            continue
                        add_transfer_journey(leg1, leg2, row.stop_name_1, buffer_min=2)

        # 3. Vectorized Major Hub Fallback
        if len(results) < n and hasattr(self, "major_hubs") and self.major_hubs:
            sorted_hubs = sorted(
                self.major_hubs,
                key=lambda h: haversine_m(origin[0], origin[1], h["lat"], h["lon"]) + haversine_m(dest[0], dest[1], h["lat"], h["lon"])
            )
            for hub in sorted_hubs[:6]:
                if len(results) >= n:
                    break
                hub_pt = (hub["lat"], hub["lon"])
                first_legs = self.plan(origin, hub_pt, depart, mode, 2)
                for first in first_legs:
                    onward = depart + timedelta(minutes=first["total_min"] + 2)
                    second_legs = self.plan(hub_pt, dest, onward, mode, 2)
                    for second in second_legs:
                        if first["route_id"] == second["route_id"] or first["route"] == second["route"]:
                            continue
                        add_transfer_journey(first, second, hub["stop_name"], buffer_min=2, basis_note=f"Hub interchange at {hub['name']}")

        if mode == "fastest":
            results.sort(key=lambda x: x.get("total_min", 999))
        elif mode == "comfortable":
            results.sort(key=lambda x: (RANK.get(x.get("crowding", "unknown"), 3), x.get("total_min", 999)))
        else:
            results.sort(key=lambda x: (x.get("transfers", 0), x.get("total_min", 999)))

        return results[:n]
