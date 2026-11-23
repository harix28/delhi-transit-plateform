import pandas as pd

from src.config import load_config
from src.ingest.tickets import prepare_tickets
from src.models.load import build_segment_load


def make_route_stops():
    names = ["a", "b", "c", "d"]
    return pd.DataFrame({
        "route_id": "1", "route_short": "R1", "direction_id": 0,
        "seq": range(4), "stop_norm": names,
    })


def make_tickets():
    return pd.DataFrame({
        "passenger_count": [2, 1],
        "vehicle_number": ["V1", "V1"],
        "start_stop_name": ["A", "B"],
        "end_stop_name": ["C", "D"],
        "route": ["R1UP", "R1UP"],
        "created_at": ["2026-10-05 08:00:00", "2026-10-05 08:05:00"],
    })


def test_running_count_matches_hand_calculation():
    cfg = load_config()
    tickets = prepare_tickets(make_tickets(), cfg)
    assert tickets["trip_key"].nunique() == 1
    out = build_segment_load(tickets, make_route_stops(), cfg).set_index("seg")
    assert out.loc[0, "load_mean"] == 2   # A-B: ticket 1 only
    assert out.loc[1, "load_mean"] == 3   # B-C: both tickets
    assert out.loc[2, "load_mean"] == 1   # C-D: ticket 2 only
    assert (out["day_type"] == "weekday").all()


def test_gap_splits_trips():
    cfg = load_config()
    raw = make_tickets()
    raw.loc[1, "created_at"] = "2026-10-05 10:00:00"
    tickets = prepare_tickets(raw, cfg)
    assert tickets["trip_key"].nunique() == 2


def test_weekend_flag():
    cfg = load_config()
    raw = make_tickets()
    raw["created_at"] = ["2026-10-04 08:00:00", "2026-10-04 08:05:00"]  # Sunday
    tickets = prepare_tickets(raw, cfg)
    assert (tickets["day_type"] == "weekend").all()


def test_direction_suffix_matches_real_gtfs_codes():
    cfg = load_config()
    tickets = prepare_tickets(make_tickets(), cfg)
    rs = make_route_stops()
    rs["route_short"] = "R1UP"
    out = build_segment_load(tickets, rs, cfg).set_index("seg")
    assert out.loc[1, "load_mean"] == 3


def test_missing_vehicle_is_not_grouped_as_a_bus():
    raw = make_tickets()
    raw.loc[0, "vehicle_number"] = None
    assert len(prepare_tickets(raw, load_config())) == 1
