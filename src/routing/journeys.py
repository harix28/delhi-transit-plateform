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

    def near_stops(self, lat, lon, max_dist=None):
        max_d = max_dist or self.cfg["journey"]["walk_max_m"]
        d = haversine_m(lat, lon, self.stops["stop_lat"].values, self.stops["stop_lon"].values)
        out = self.stops[["stop_id", "stop_name", "stop_lat", "stop_lon"]].copy()
        out["walk_m"] = d
        filtered = out[out["walk_m"] <= max_d]
        if filtered.empty:
            return out.sort_values("walk_m").head(10)
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
    def plan(self, origin, dest, depart: datetime, mode="fastest", n=3, max_walk_m=None):
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
            if max_walk_m is None and j.get("walk_max_m", 800) < 1500:
                return self.plan(origin, dest, depart, mode=mode, n=n, max_walk_m=1500)
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
                if scheduled is not None:
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
                "time_basis": "GTFS schedule departure" if (scheduled and scheduled.get("departure")) else "Route headway baseline",
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
        # 1. Direct Service (transfers = 0)
        direct = self.plan(origin, dest, depart, mode, n)
        results = [dict(o, transfers=0) for o in direct]
        if len(results) >= n:
            return results[:n]

        # 2. Single-Transfer via Overlapping Junction Stops
        origin_stops = self.near_stops(*origin)
        dest_stops = self.near_stops(*dest)
        origin_ids = set(origin_stops.stop_id)
        dest_ids = set(dest_stops.stop_id)

        routes_b = self.rs[self.rs.stop_id.isin(origin_ids)][["route_id", "direction_id", "seq"]].groupby(["route_id", "direction_id"])["seq"].min().reset_index().rename(columns={"seq": "min_seq"})
        routes_a = self.rs[self.rs.stop_id.isin(dest_ids)][["route_id", "direction_id", "seq"]].groupby(["route_id", "direction_id"])["seq"].max().reset_index().rename(columns={"seq": "max_seq"})

        merged_b = self.rs[["route_id", "direction_id", "stop_id", "seq"]].merge(routes_b, on=["route_id", "direction_id"])
        reachable = set(merged_b[merged_b["seq"] > merged_b["min_seq"]]["stop_id"])

        merged_a = self.rs[["route_id", "direction_id", "stop_id", "seq"]].merge(routes_a, on=["route_id", "direction_id"])
        preceding = set(merged_a[merged_a["seq"] < merged_a["max_seq"]]["stop_id"])

        transfers = self.stops[self.stops.stop_id.isin(reachable & preceding)].copy()
        seen = {o["route"] for o in results}
        seen_keys = set()

        if not transfers.empty:
            transfers["detour"] = (
                haversine_m(origin[0], origin[1], transfers.stop_lat, transfers.stop_lon)
                + haversine_m(dest[0], dest[1], transfers.stop_lat, transfers.stop_lon)
            )
            for stop in transfers.sort_values("detour").head(25).itertuples():
                if len(results) >= n + 2:
                    break
                point = (stop.stop_lat, stop.stop_lon)
                first_legs = self.plan(origin, point, depart, mode, 2)
                for first in first_legs:
                    onward = depart + timedelta(minutes=first["total_min"] + 2)
                    second_legs = self.plan(point, dest, onward, mode, 2)
                    for second in second_legs:
                        if first["route_id"] == second["route_id"] or first["route"] == second["route"]:
                            continue
                        route_name = f"{first['route']} + {second['route']}"
                        key = (first["route_id"], second["route_id"], stop.stop_name)
                        if key in seen_keys or route_name in seen:
                            continue
                        seen_keys.add(key)
                        seen.add(route_name)

                        levels = first["crowding_by_stretch"] + second["crowding_by_stretch"]
                        worst = max([first["crowding"], second["crowding"]], key=lambda x: RANK[x])
                        results.append({
                            "route": route_name,
                            "route_id": f"{first['route_id']}+{second['route_id']}",
                            "total_min": round(first["total_min"] + second["total_min"] + 2, 1),
                            "ride_min": round(first["ride_min"] + second["ride_min"], 1),
                            "walk_min": round(first["walk_min"] + second["walk_min"], 1),
                            "wait_min": round(first["wait_min"] + second["wait_min"], 1),
                            "transfer_min": 2,
                            "transfers": 1,
                            "transfer_stop": stop.stop_name,
                            "legs": [first, second],
                            "board": first["board"],
                            "alight": second["alight"],
                            "stations": first.get("stations", []) + second.get("stations", []),
                            "geometry": first.get("geometry", []) + second.get("geometry", []),
                            "segments": first.get("segments", []) + second.get("segments", []),
                            "crowding": worst,
                            "confidence": min([first["confidence"], second["confidence"]], key=lambda x: CONF_RANK[x]),
                            "crowding_by_stretch": levels,
                            "crowding_coverage": round(sum(x != "unknown" for x in levels) / max(len(levels), 1), 3),
                            "time_basis": f"{first['time_basis']} · 3-min interchange buffer at {stop.stop_name}",
                        })

        # 3. Hub-Based Single-Transfer Fallback (via Major Delhi Interchanges)
        if len(results) < n and hasattr(self, "major_hubs") and self.major_hubs:
            sorted_hubs = sorted(
                self.major_hubs,
                key=lambda h: haversine_m(origin[0], origin[1], h["lat"], h["lon"]) + haversine_m(dest[0], dest[1], h["lat"], h["lon"])
            )
            for hub in sorted_hubs[:8]:
                if len(results) >= n:
                    break
                hub_pt = (hub["lat"], hub["lon"])
                first_legs = self.plan(origin, hub_pt, depart, mode, 2)
                for first in first_legs:
                    onward = depart + timedelta(minutes=first["total_min"] + 4)
                    second_legs = self.plan(hub_pt, dest, onward, mode, 2)
                    for second in second_legs:
                        if first["route_id"] == second["route_id"] or first["route"] == second["route"]:
                            continue
                        route_name = f"{first['route']} + {second['route']}"
                        key = (first["route_id"], second["route_id"], hub["stop_name"])
                        if key in seen_keys or route_name in seen:
                            continue
                        seen_keys.add(key)
                        seen.add(route_name)

                        levels = first["crowding_by_stretch"] + second["crowding_by_stretch"]
                        worst = max([first["crowding"], second["crowding"]], key=lambda x: RANK[x])
                        results.append({
                            "route": route_name,
                            "route_id": f"{first['route_id']}+{second['route_id']}",
                            "total_min": round(first["total_min"] + second["total_min"] + 4, 1),
                            "ride_min": round(first["ride_min"] + second["ride_min"], 1),
                            "walk_min": round(first["walk_min"] + second["walk_min"], 1),
                            "wait_min": round(first["wait_min"] + second["wait_min"], 1),
                            "transfer_min": 4,
                            "transfers": 1,
                            "transfer_stop": hub["stop_name"],
                            "legs": [first, second],
                            "board": first["board"],
                            "alight": second["alight"],
                            "stations": first.get("stations", []) + second.get("stations", []),
                            "geometry": first.get("geometry", []) + second.get("geometry", []),
                            "segments": first.get("segments", []) + second.get("segments", []),
                            "crowding": worst,
                            "confidence": min([first["confidence"], second["confidence"]], key=lambda x: CONF_RANK[x]),
                            "crowding_by_stretch": levels,
                            "crowding_coverage": round(sum(x != "unknown" for x in levels) / max(len(levels), 1), 3),
                            "time_basis": f"{first['time_basis']} · Hub interchange at {hub['name']}",
                        })

        # 4. Multi-Hop 2-Transfer Fallback (Hub 1 -> Hub 2 for extreme cross-city paths)
        if not results and hasattr(self, "major_hubs") and self.major_hubs:
            hubs_near_origin = sorted(self.major_hubs, key=lambda h: haversine_m(origin[0], origin[1], h["lat"], h["lon"]))[:4]
            hubs_near_dest = sorted(self.major_hubs, key=lambda h: haversine_m(dest[0], dest[1], h["lat"], h["lon"]))[:4]
            for h1 in hubs_near_origin:
                if results:
                    break
                for h2 in hubs_near_dest:
                    if h1["name"] == h2["name"]:
                        continue
                    l1_opts = self.plan(origin, (h1["lat"], h1["lon"]), depart, mode, 1)
                    if not l1_opts:
                        continue
                    l1 = l1_opts[0]
                    t2 = depart + timedelta(minutes=l1["total_min"] + 3)
                    l2_opts = self.plan((h1["lat"], h1["lon"]), (h2["lat"], h2["lon"]), t2, mode, 1)
                    if not l2_opts:
                        continue
                    l2 = l2_opts[0]
                    t3 = t2 + timedelta(minutes=l2["total_min"] + 3)
                    l3_opts = self.plan((h2["lat"], h2["lon"]), dest, t3, mode, 1)
                    if not l3_opts:
                        continue
                    l3 = l3_opts[0]

                    route_name = f"{l1['route']} + {l2['route']} + {l3['route']}"
                    levels = l1["crowding_by_stretch"] + l2["crowding_by_stretch"] + l3["crowding_by_stretch"]
                    worst = max([l1["crowding"], l2["crowding"], l3["crowding"]], key=lambda x: RANK[x])
                    results.append({
                        "route": route_name,
                        "route_id": f"{l1['route_id']}+{l2['route_id']}+{l3['route_id']}",
                        "total_min": round(l1["total_min"] + l2["total_min"] + l3["total_min"] + 6, 1),
                        "ride_min": round(l1["ride_min"] + l2["ride_min"] + l3["ride_min"], 1),
                        "walk_min": round(l1["walk_min"] + l2["walk_min"] + l3["walk_min"], 1),
                        "wait_min": round(l1["wait_min"] + l2["wait_min"] + l3["wait_min"], 1),
                        "transfer_min": 6,
                        "transfers": 2,
                        "transfer_stop": f"{h1['stop_name']} & {h2['stop_name']}",
                        "legs": [l1, l2, l3],
                        "board": l1["board"],
                        "alight": l3["alight"],
                        "stations": l1.get("stations", []) + l2.get("stations", []) + l3.get("stations", []),
                        "geometry": l1.get("geometry", []) + l2.get("geometry", []) + l3.get("geometry", []),
                        "segments": l1.get("segments", []) + l2.get("segments", []) + l3.get("segments", []),
                        "crowding": worst,
                        "confidence": min([l1["confidence"], l2["confidence"], l3["confidence"]], key=lambda x: CONF_RANK[x]),
                        "crowding_by_stretch": levels,
                        "crowding_coverage": round(sum(x != "unknown" for x in levels) / max(len(levels), 1), 3),
                        "time_basis": f"2-transfer transit corridor via {h1['name']} and {h2['name']}",
                    })
                    break

        if mode == "comfortable":
            results.sort(key=lambda x: (x.get("transfers", 0), RANK[x["crowding"]], x["total_min"]))
        else:
            results.sort(key=lambda x: (x.get("transfers", 0), x["total_min"]))
        return results[:n]
