"""Build an atomic, portable GTFS timetable index for the selected dataset."""
from contextlib import closing
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.config import load_config, resolve


def build_schedule(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    base = resolve(cfg, "raw") / "gtfs"
    out = resolve(cfg, "processed") / "schedule.sqlite"
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".building.sqlite")
    try:
        with closing(sqlite3.connect(temp)) as con, con:
            for name in ["trips", "calendar"]:
                pd.read_csv(base / (name + ".txt"), dtype=str).to_sql(name, con, if_exists="replace", index=False)
            first = True
            for df in pd.read_csv(base / "stop_times.txt", dtype={"trip_id": str, "stop_id": str}, chunksize=100000):
                for name in ["arrival_time", "departure_time"]:
                    parts = df[name].str.split(":", expand=True).astype(int)
                    df[name] = parts[0] * 3600 + parts[1] * 60 + parts[2]
                df[["trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"]].to_sql(
                    "times", con, if_exists="replace" if first else "append", index=False)
                first = False
            if first:
                raise ValueError("GTFS contains no stop times")
            con.execute("CREATE INDEX times_stop ON times(stop_id, departure_time)")
            con.execute("CREATE INDEX times_trip ON times(trip_id, stop_id, stop_sequence)")
            con.execute("CREATE INDEX trips_id ON trips(trip_id)")
            if (base / "calendar_dates.txt").exists():
                pd.read_csv(base / "calendar_dates.txt", dtype=str).to_sql("calendar_dates", con, if_exists="replace", index=False)
        temp.replace(out)
    finally:
        if temp.exists():
            temp.unlink()
    return out


if __name__ == "__main__":
    print("Schedule index ready:", build_schedule())
