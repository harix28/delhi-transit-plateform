import pandas as pd
import pytest
from fastapi import HTTPException
from src import transit, live


@pytest.fixture
def route_tables(monkeypatch):
    routes = pd.DataFrame([
        dict(route_id="73", route_short_name="116ADOWN2"),
        dict(route_id="2205", route_short_name="73DOWN"),
        dict(route_id="2206", route_short_name="73UP"),
        dict(route_id="other", route_short_name="730UP"),
    ])
    rs = pd.concat([pd.DataFrame(dict(route_id=[r.route_id]*2, route_short=[r.route_short_name]*2,
        direction_id=[0,0], seq=[0,1], stop_id=["a","b"], stop_lat=[28.6,28.63], stop_lon=[77.2,77.2]))
        for r in routes.itertuples()], ignore_index=True)
    monkeypatch.setattr(transit, "tables", lambda cfg=None: (pd.DataFrame(), routes, rs))


def test_route_ids_and_public_codes_are_unambiguous(route_tables):
    assert set(transit.route_stop_table("73").route_short) == {"116ADOWN2"}
    assert set(transit.route_stop_table("code:73").route_short) == {"73UP"}
    assert set(transit.route_stop_table("73DOWN").route_short) == {"73DOWN"}
    assert set(transit.route_stop_table("code:73", 1).route_short) == {"73DOWN"}
    with pytest.raises(HTTPException):
        transit.route_stop_table("code:7")


def test_missing_feed_key_never_simulates_real_data(monkeypatch):
    monkeypatch.setattr(live, "_cache", None)
    monkeypatch.setenv("TRANSPORTSTACK_API_KEY", "")
    monkeypatch.setattr(live, "load_config", lambda: {"synthetic": False})
    result = live.get_vehicles()
    assert result["vehicles"] == []
    assert result["synthetic"] is False
    assert result["available"] is False


def test_demo_fleet_is_explicitly_synthetic(monkeypatch):
    monkeypatch.setattr(live, "_cache", None)
    monkeypatch.setenv("TRANSPORTSTACK_API_KEY", "")
    monkeypatch.setattr(live, "load_config", lambda: {"synthetic": True})
    monkeypatch.setattr(live, "generate_simulated_vehicles", lambda now: [{"id": "demo"}])
    result = live.get_vehicles()
    assert result["synthetic"] is True
    assert result["vehicles"] == [{"id": "demo"}]


def test_feed_failure_returns_unavailable_and_uses_cache(monkeypatch):
    monkeypatch.setattr(live, "_cache", None)
    monkeypatch.setenv("TRANSPORTSTACK_API_KEY", "example")
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("upstream failed")
    monkeypatch.setattr(live.httpx, "get", fail)
    assert live.get_vehicles()["available"] is False
    assert live.get_vehicles()["vehicles"] == []
    assert len(calls) == 1
