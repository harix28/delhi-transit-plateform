"""Create deterministic synthetic Delhi fixtures, isolated from real raw data."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import yaml

from src.config import ROOT, load_config
from scripts.run_pipeline import stage_gtfs, stage_load, stage_speed


def create_demo():
    cfg = load_config(ROOT / "config.yaml")
    cfg.update(dataset="Synthetic Delhi demo", synthetic=True)
    cfg["load"]["uplift"] = 1.0
    cfg["paths"] = {key: f"data/demo/{key}" for key in ("raw", "processed", "outputs")}
    base = ROOT / "data/demo/raw"
    for directory in ("gtfs", "tickets", "gps"):
        (base / directory).mkdir(parents=True, exist_ok=True)
    stops = [
        ("ND", "New Delhi Railway Station", 28.6424, 77.2195),
        ("CP", "Connaught Place", 28.6315, 77.2167),
        ("PP", "Patel Chowk", 28.6228, 77.2146),
        ("IG", "India Gate", 28.6129, 77.2295),
        ("KH", "Khan Market", 28.6002, 77.2270),
        ("JL", "JLN Stadium", 28.5906, 77.2338),
        ("LG", "Lodhi Garden", 28.5933, 77.2197),
        ("AI", "AIIMS", 28.5686, 77.2077),
        ("KB", "Karol Bagh", 28.6527, 77.1908),
        ("RK", "Ramakrishna Ashram", 28.6390, 77.2086),
        ("KG", "Kashmere Gate ISBT", 28.6675, 77.2285),
        ("AV", "Anand Vihar ISBT", 28.6469, 77.3160),
        ("DK", "Dhaula Kuan", 28.5925, 77.1610),
        ("NP", "Nehru Place Terminal", 28.5482, 77.2514),
        ("UN", "Uttam Nagar Terminal", 28.6219, 77.0620),
        ("DW", "Dwarka Sector 10", 28.5815, 77.0570),
        ("RN", "Rohini Sector 16", 28.7320, 77.1230),
        ("BD", "Badarpur Border", 28.4980, 77.3010),
        ("SK", "Saket Crossing", 28.5204, 77.2130),
        ("AZ", "Azadpur Terminal", 28.7060, 77.1760),
    ]
    names = {row[0]: row[1] for row in stops}
    pd.DataFrame(stops, columns=["stop_id", "stop_name", "stop_lat", "stop_lon"]).to_csv(base / "gtfs/stops.txt", index=False)
    # Illustrative corridors and route identifiers across Delhi.
    routes = {
        "D1": ["ND", "CP", "PP", "IG", "KH", "JL"],
        "D2": ["ND", "CP", "PP", "LG", "KH", "JL"],
        "D3": ["KB", "RK", "ND", "CP", "PP", "IG", "KH", "JL"],
        "D4": ["CP", "PP", "LG", "AI", "SK"],
        "729": ["KG", "ND", "CP", "DK", "UN", "DW"],
        "502": ["KG", "CP", "AI", "SK", "NP", "BD"],
        "214": ["KG", "ND", "CP", "AV"],
        "901": ["RN", "AZ", "KB", "CP", "NP"],
    }
    pd.DataFrame([{"route_id": r, "route_short_name": r} for r in routes]).to_csv(base / "gtfs/routes.txt", index=False)
    trips, times, tickets, gps = [], [], [], []
    rng = np.random.default_rng(42)
    for route, sequence in routes.items():
        for direction in (0, 1):
            seq = sequence if direction == 0 else list(reversed(sequence))
            trip = f"{route}-{direction}"
            trips.append({"trip_id": trip, "route_id": route, "direction_id": direction})
            for i, stop in enumerate(seq):
                times.append({"trip_id": trip, "stop_id": stop, "stop_sequence": i + 1})
            for day in range(7):
                for hour in (7, 8, 9, 12, 15, 17, 18, 20, 23):
                    for sample in range(6):
                        ts = datetime(2026, 10, 5 + day, hour, sample * 5)
                        # Full-corridor tickets keep this tiny fixture interpretable.
                        load = {"D1": 45, "D2": 15, "D3": 80, "D4": 24, "729": 55, "502": 50, "214": 40, "901": 60}.get(route, 35)
                        if hour not in (8, 9, 17, 18):
                            load = int(load * .65)
                        tickets.append({"passenger_count": max(1, load + int(rng.integers(-4, 5))),
                                        "vehicle_number": f"DEMO-{route}-{direction}-{sample}",
                                        "start_stop_name": names[seq[0]], "end_stop_name": names[seq[-1]],
                                        "route": route + ("UP" if direction == 0 else "DOWN"),
                                        "created_at": ts.isoformat()})
                        speed = {"D1": 20, "D2": 15, "D3": 22, "D4": 17, "729": 19, "502": 18, "214": 21, "901": 20}.get(route, 20)
                        for ping in range(4):
                            gps.append((route, max(4, speed + float(rng.normal(0, 1.5))),
                                        int((ts.replace(tzinfo=ZoneInfo("Asia/Kolkata")) + timedelta(seconds=ping * 30)).timestamp())))
    pd.DataFrame(trips).to_csv(base / "gtfs/trips.txt", index=False)
    pd.DataFrame(times).to_csv(base / "gtfs/stop_times.txt", index=False)
    pd.DataFrame(tickets).to_csv(base / "tickets/demo.csv", index=False)
    with sqlite3.connect(base / "gps/demo.db") as con:
        con.execute("CREATE TABLE IF NOT EXISTS movements (route_id TEXT, speed REAL, timestamp INTEGER)")
        con.execute("DELETE FROM movements")
        con.executemany("INSERT INTO movements VALUES (?, ?, ?)", gps)
    (ROOT / "config.demo.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    stage_gtfs(cfg)
    stage_load(cfg)
    stage_speed(cfg)
    print(json.dumps({"synthetic": True, "stops": len(stops), "routes": len(routes), "tickets": len(tickets)}))


if __name__ == "__main__":
    create_demo()
