import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def _weighted(df, keys):
    df = df.assign(wm=df["median"] * df["n"])
    g = df.groupby(keys).agg(wm=("wm", "sum"), n_obs=("n", "sum")).reset_index()
    g["speed_kmh"] = g["wm"] / g["n_obs"]
    return g.drop(columns="wm")


def build_speed_tables(gps_dir, cfg):
    """BASELINE speed model: median GPS speed by route, day type, hour.

    Returns (route_speed, city_speed). ASSUMES the `speed` column is km/h;
    verify on real data. TODO: replace with map-matched stretch speeds.
    """
    files = sorted(Path(gps_dir).glob("*.db"))
    if not files:
        raise FileNotFoundError(f"No .db files in {gps_dir}")
    lo, hi = cfg["speed"]["min_kmh"], cfg["speed"]["max_kmh"]

    route_parts, city_parts = [], []
    for f in files:
        con = sqlite3.connect(f)
        tables = pd.read_sql_query(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name", con
        )["name"]
        tbl = None
        for name in tables:
            safe = name.replace('"', '""')
            columns = pd.read_sql_query(f'pragma table_info("{safe}")', con)["name"]
            if {"route_id", "speed", "timestamp"}.issubset(set(columns)):
                tbl = safe
                break
        if tbl is None:
            con.close()
            raise ValueError(f"{f.name}: no table has route_id, speed and timestamp columns")
        df = pd.read_sql_query(
            f'select route_id, speed, timestamp from "{tbl}" where speed between ? and ?',
            con, params=(lo, hi),
        )
        con.close()
        if df.empty:
            continue
        ts = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df["hour"] = ts.dt.hour
        df["day_type"] = np.where(ts.dt.dayofweek >= 5, "weekend", "weekday")
        df["route_id"] = df["route_id"].astype(str)
        route_parts.append(
            df.groupby(["route_id", "day_type", "hour"])["speed"].agg(median="median", n="size").reset_index()
        )
        city_parts.append(
            df.groupby(["day_type", "hour"])["speed"].agg(median="median", n="size").reset_index()
        )

    if not route_parts:
        raise ValueError("No usable GPS speeds remain after filtering; check speed units and timestamps")
    route_speed = _weighted(pd.concat(route_parts), ["route_id", "day_type", "hour"])
    city_speed = _weighted(pd.concat(city_parts), ["day_type", "hour"])
    return route_speed, city_speed
