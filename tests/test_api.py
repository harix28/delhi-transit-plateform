import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.config import load_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.update(synthetic=True, dataset="Test fixture")
    cfg["paths"]["processed"] = str(tmp_path)
    stops = pd.DataFrame([
        {"stop_id": "A", "stop_name": "Alpha", "stop_norm": "alpha", "stop_lat": 28.6, "stop_lon": 77.2},
        {"stop_id": "B", "stop_name": "Bravo", "stop_norm": "bravo", "stop_lat": 28.63, "stop_lon": 77.2},
        {"stop_id": "C", "stop_name": "Charlie", "stop_norm": "charlie", "stop_lat": 28.66, "stop_lon": 77.2},
    ])
    stops.to_parquet(tmp_path / "stops.parquet", index=False)
    route_frames = []
    for route in ("R1", "R2"):
        route_frames.append(stops.assign(route_id=route, route_short=route, direction_id=0,
                                         seq=[0, 1, 2], dist_m=[0., 3300., 6600.]))
    pd.concat(route_frames).to_parquet(tmp_path / "route_stops.parquet", index=False)
    pd.DataFrame([{"route_id": r, "day_type": "weekday", "hour": 8, "speed_kmh": speed}
                  for r, speed in [("R1", 30.), ("R2", 15.)]]).to_parquet(tmp_path / "segment_speed.parquet", index=False)
    pd.DataFrame([{"day_type": "weekday", "hour": 8, "speed_kmh": 20.}]).to_parquet(tmp_path / "city_speed.parquet", index=False)
    rows = []
    for route, level in [("R1", "crowded"), ("R2", "seats")]:
        for seg in [0, 1]:
            rows.append(dict(route_id=route, route_short=route, direction_id=0, seg=seg,
                             day_type="weekday", half_hour=16, load_est=80. if route == "R1" else 10.,
                             load_low=8., load_high=90., ratio=1., level=level, confidence="high", n_trips=30))
    pd.DataFrame(rows).to_parquet(tmp_path / "crowding.parquet", index=False)
    monkeypatch.setattr(main, "cfg", cfg)
    monkeypatch.setattr(main, "planner", None)
    with TestClient(main.app) as c:
        yield c


def query(client, **kwargs):
    params = {"from": "Alpha", "to": "Charlie", "depart": "2026-11-20T08:00"}
    params.update(kwargs)
    return client.get("/journeys", params=params)


def test_fastest_and_comfortable_change_ranking(client):
    fast = query(client).json()
    comfort = query(client, mode="comfortable").json()
    assert fast["options"][0]["route"] == "R1"
    assert comfort["options"][0]["route"] == "R2"
    assert len(fast["options"][0]["geometry"]) == 3
    assert fast["options"][0]["crowding_coverage"] == 1
    assert fast["options"][0]["segments"] == [
        {"from": "Alpha", "to": "Bravo", "level": "crowded"},
        {"from": "Bravo", "to": "Charlie", "level": "crowded"},
    ]
    assert fast["synthetic"] is True
    assert fast["depart"].endswith("+05:30")


def test_explicit_timezone_is_converted_to_india(client):
    data = query(client, depart="2026-11-20T02:30:00Z").json()
    assert data["depart"] == "2026-11-20T08:00:00+05:30"
    assert data["options"][0]["crowding"] == "crowded"


@pytest.mark.parametrize("value", ["   ", "???", "999,200", "nan,77", "Infinity,77", "28,abc", "not a stop"])
def test_invalid_locations_are_rejected(client, value):
    assert query(client, **{"from": value}).status_code == 400


def test_no_direct_route_and_missing_crowding(client):
    assert query(client, **{"from": "Charlie", "to": "Alpha"}).json()["options"] == []
    options = query(client, depart="2026-11-20T02:00").json()["options"]
    assert all(o["crowding"] == "unknown" and o["confidence"] == "low" for o in options)


def test_partial_crowding_does_not_claim_full_confidence(client):
    p = main.planner
    p.crowd = p.crowd[p.crowd["seg"] == 0]
    options = query(client).json()["options"]
    assert all(o["crowding"] == "unknown" and o["confidence"] == "low" for o in options)


def test_api_validation_and_frontend(client):
    assert query(client, mode="invalid").status_code == 422
    assert client.get("/crowding/route/R1?half_hour=48").status_code == 422
    assert client.get("/crowding/top?min_confidence=invalid").status_code == 422
    assert len(client.get("/crowding/top?min_confidence=low").json()["items"]) == 4
    assert len(client.get("/network").json()["routes"]) == 2
    assert client.get("/stops?q=Alpha").json()["stops"][0]["stop_name"] == "Alpha"
    assert "Delhi Transit Intelligence" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200


def test_missing_data_keeps_setup_screen_available(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["paths"]["processed"] = str(tmp_path / "missing")
    monkeypatch.setattr(main, "cfg", cfg)
    monkeypatch.setattr(main, "planner", None)
    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").json()["ready"] is False
        assert query(client).status_code == 503
