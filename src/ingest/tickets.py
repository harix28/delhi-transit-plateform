import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from src.utils import norm_name

REQUIRED = [
    "passenger_count", "vehicle_number", "start_stop_name",
    "end_stop_name", "route", "created_at",
]


def prepare_tickets(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Clean raw ticket rows and group them into bus trips.

    Trip rule (APPROXIMATION): consecutive tickets of one vehicle on one route
    (and one UP/DOWN leg) belong to the same trip until a gap longer than
    cfg['tickets']['trip_gap_min'] minutes.
    """
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Ticket data missing columns: {missing}")

    df["ts"] = pd.to_datetime(df["created_at"], errors="coerce")  # already IST
    df = df.dropna(subset=["ts", "route", "start_stop_name", "end_stop_name", "vehicle_number"])
    df = df[df["vehicle_number"].astype(str).str.strip().ne("")]
    df["pax"] = pd.to_numeric(df["passenger_count"], errors="coerce").fillna(1).clip(lower=1)

    # Route looks like "8UP", "433DOWN", "522BDOWN2", or non-directional "DW-5"
    parts = df["route"].astype(str).str.strip().str.extract(
        r"^(.*?)(UP|DOWN)(\d*)$", flags=re.IGNORECASE
    )
    no_match = parts[0].isna()
    if no_match.any():
        parts.loc[no_match, 0] = df.loc[no_match, "route"].astype(str).str.strip()
        parts.loc[no_match, 1] = ""
        parts.loc[no_match, 2] = ""
    df["route_short"] = parts[0].str.strip().str.upper()
    df["leg"] = (parts[1].fillna("") + parts[2].fillna("")).str.upper()

    df["start_norm"] = norm_name(df["start_stop_name"])
    df["end_norm"] = norm_name(df["end_stop_name"])
    df = df[df["start_norm"] != df["end_norm"]]
    df["vehicle_number"] = df["vehicle_number"].astype(str)

    df = df.sort_values(["vehicle_number", "route_short", "leg", "ts"]).reset_index(drop=True)
    df["ticket_id"] = df.index

    gap_min = df["ts"].diff().dt.total_seconds().div(60)
    new_trip = (
        (df["vehicle_number"] != df["vehicle_number"].shift())
        | (df["route_short"] != df["route_short"].shift())
        | (df["leg"] != df["leg"].shift())
        | (gap_min > cfg["tickets"]["trip_gap_min"])
    )
    df["trip_key"] = new_trip.cumsum()
    df["trip_start"] = df.groupby("trip_key")["ts"].transform("min")
    df["day_type"] = np.where(df["trip_start"].dt.dayofweek >= 5, "weekend", "weekday")

    return df[
        [
            "ticket_id", "trip_key", "trip_start", "ts", "day_type", "route_short",
            "leg", "start_norm", "end_norm", "pax", "vehicle_number",
        ]
    ]


def load_tickets(tickets_dir, cfg) -> pd.DataFrame:
    tickets_dir = Path(tickets_dir)
    files = sorted(list(tickets_dir.glob("*.csv")) + list(tickets_dir.glob("*.zip")))
    if not files:
        raise FileNotFoundError(f"No ticket CSV/ZIP found in {tickets_dir}")
    if files[0].suffix.lower() == ".zip":
        with zipfile.ZipFile(files[0]) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith(".csv") and not n.startswith("__MACOSX/")]
            if len(members) != 1:
                raise ValueError("Expected exactly one ticket CSV in the archive")
            with archive.open(members[0]) as stream:
                raw = pd.read_csv(stream, low_memory=False)
    else:
        raw = pd.read_csv(files[0], low_memory=False)
    return prepare_tickets(raw, cfg)
