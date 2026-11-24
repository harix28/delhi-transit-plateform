"""Prepare runtime data, or bundle an existing timetable for Git-based hosting."""
import argparse
import gzip
import hashlib
import json
import shutil
from contextlib import closing
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import ROOT, load_config, resolve


def prepare(bundle=False):
    cfg = load_config()
    proc = resolve(cfg, "processed")
    for name in ["stops.parquet", "route_stops.parquet"]:
        if not (proc / name).exists():
            raise FileNotFoundError(f"Missing {proc / name}; run the dataset pipeline before deploying")
    if cfg.get("synthetic"):
        print("Synthetic dataset selected; no real timetable required.")
        return
    schedule = proc / "schedule.sqlite"
    archive = ROOT / "data/deploy/schedule.sqlite.gz"
    manifest = archive.with_suffix(".json")
    if bundle:
        if not schedule.exists():
            from scripts.build_schedule import build_schedule
            build_schedule(cfg)
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Indexes compress poorly; recreate them during the hosting build instead.
        compact = archive.parent / "schedule.bundle.sqlite"
        try:
            with closing(sqlite3.connect(schedule)) as source, closing(sqlite3.connect(compact)) as dest:
                source.backup(dest)
                indexes = dest.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall()
                for (name,) in indexes:
                    dest.execute('DROP INDEX "' + name.replace('"', '""') + '"')
                dest.commit()
                dest.execute("VACUUM")
            with compact.open("rb") as source, gzip.open(archive, "wb", compresslevel=9) as dest:
                shutil.copyfileobj(source, dest, length=1024 * 1024)
            with archive.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest.write_text(json.dumps({"sha256": digest, "uncompressed_bytes": compact.stat().st_size}, indent=2))
        finally:
            if compact.exists():
                compact.unlink()
        print(f"Timetable bundle ready: {archive.name} ({archive.stat().st_size / 1024**2:.1f} MiB)")
        return
    if archive.exists():
        if not manifest.exists():
            raise ValueError("Timetable bundle checksum manifest is missing")
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != json.loads(manifest.read_text())["sha256"]:
            raise ValueError("Timetable bundle checksum mismatch; rebuild the bundle")
        temp = schedule.with_suffix(".restoring.sqlite")
        try:
            with gzip.open(archive, "rb") as source, temp.open("wb") as dest:
                shutil.copyfileobj(source, dest, length=1024 * 1024)
            with closing(sqlite3.connect(temp)) as con, con:
                for table in ["times", "trips", "calendar"]:
                    con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                con.execute("CREATE INDEX IF NOT EXISTS times_stop ON times(stop_id, departure_time)")
                con.execute("CREATE INDEX IF NOT EXISTS times_trip ON times(trip_id, stop_id, stop_sequence)")
                con.execute("CREATE INDEX IF NOT EXISTS trips_id ON trips(trip_id)")
            temp.replace(schedule)
        finally:
            if temp.exists():
                temp.unlink()
    elif not schedule.exists():
        from scripts.build_schedule import build_schedule
        build_schedule(cfg)
    print("Runtime transit data and timetable are ready.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-schedule", action="store_true")
    prepare(parser.parse_args().bundle_schedule)
