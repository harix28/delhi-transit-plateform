from contextlib import closing
import sqlite3
import pytest
from scripts import prepare_deploy


def test_timetable_bundle_roundtrip_and_checksum(tmp_path, monkeypatch):
    proc = tmp_path / "processed"
    proc.mkdir()
    for name in ["stops.parquet", "route_stops.parquet"]:
        (proc / name).touch()
    db = proc / "schedule.sqlite"
    with closing(sqlite3.connect(db)) as con, con:
        con.executescript("CREATE TABLE times(trip_id,stop_id,stop_sequence,arrival_time,departure_time); CREATE TABLE trips(trip_id,route_id,service_id); CREATE TABLE calendar(service_id); INSERT INTO times VALUES ('t','a',0,10,10); CREATE INDEX times_stop ON times(stop_id,departure_time);")
    monkeypatch.setattr(prepare_deploy, "ROOT", tmp_path)
    monkeypatch.setattr(prepare_deploy, "load_config", lambda: {"paths": {"processed": str(proc)}})
    prepare_deploy.prepare(bundle=True)
    db.unlink()
    prepare_deploy.prepare()
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("SELECT trip_id FROM times").fetchone()[0] == "t"
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='times_stop'").fetchone()
    archive = tmp_path / "data/deploy/schedule.sqlite.gz"
    with archive.open("ab") as stream:
        stream.write(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        prepare_deploy.prepare()
