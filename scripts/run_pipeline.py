"""Rebuild all outputs from raw data.  Usage: python scripts/run_pipeline.py [gtfs|load|speed|all]"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.config import load_config, resolve  # noqa: E402
from src.ingest.gtfs import build_gtfs_tables  # noqa: E402
from src.ingest.tickets import load_tickets  # noqa: E402
from src.models.crowding import add_crowding  # noqa: E402
from src.models.load import build_segment_load  # noqa: E402
from src.models.speed import build_speed_tables  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("pipeline")


def stage_gtfs(cfg):
    raw, proc = resolve(cfg, "raw"), resolve(cfg, "processed")
    build_gtfs_tables(raw / "gtfs", proc)
    log.info("GTFS tables written")


def stage_load(cfg):
    raw, proc, out = resolve(cfg, "raw"), resolve(cfg, "processed"), resolve(cfg, "outputs")
    out.mkdir(parents=True, exist_ok=True)
    rs = pd.read_parquet(proc / "route_stops.parquet")
    tickets = load_tickets(raw / "tickets", cfg)
    log.info("Tickets prepared: %d rows, %d trips", len(tickets), tickets["trip_key"].nunique())
    seg = add_crowding(build_segment_load(tickets, rs, cfg), cfg)
    seg.to_parquet(proc / "crowding.parquet", index=False)
    seg.to_csv(out / "segment_load.csv", index=False)
    log.info("Segment load and crowding written (%d rows)", len(seg))


def stage_speed(cfg):
    raw, proc, out = resolve(cfg, "raw"), resolve(cfg, "processed"), resolve(cfg, "outputs")
    out.mkdir(parents=True, exist_ok=True)
    proc.mkdir(parents=True, exist_ok=True)
    route_speed, city_speed = build_speed_tables(raw / "gps", cfg)
    route_speed.to_parquet(proc / "segment_speed.parquet", index=False)
    city_speed.to_parquet(proc / "city_speed.parquet", index=False)
    route_speed.to_csv(out / "route_speed.csv", index=False)
    log.info("Speed tables written")


STAGES = {"gtfs": stage_gtfs, "load": stage_load, "speed": stage_speed}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=[*STAGES, "all"])
    args = ap.parse_args()
    cfg = load_config()
    for name in (STAGES if args.stage == "all" else [args.stage]):
        STAGES[name](cfg)
