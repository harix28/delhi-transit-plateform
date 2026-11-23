import logging

import numpy as np
import pandas as pd

from src.utils import norm_name

log = logging.getLogger(__name__)


def remove_suffixes(s: pd.Series) -> pd.Series:
    pat = r"\b(terminal|village|border|depot|metro station|station|crossing|more|chowk|flyover|extension|extn|gate|circle)\b"
    return s.str.replace(pat, " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()


def match_stops(tickets: pd.DataFrame, route_stops: pd.DataFrame) -> pd.DataFrame:
    """Attach board/alight stop sequence numbers to each ticket.

    Matches normalised stop names and aliases within the route. Keeps only tickets where
    the boarding stop comes before the alighting stop in one direction.
    """
    rs = route_stops.copy()
    tickets = tickets.copy()

    # Expand depot-coded GTFS routes (e.g. 628STL-GHD-IDOWN -> 628STLDOWN)
    depot_pat = r"^([A-Z0-9]+)-[A-Z0-9]+(?:-[A-Z0-9]+)?-?(UP|DOWN.*)$"
    simplified = rs["route_short"].str.extract(depot_pat)
    has_sim = ~simplified[0].isna()
    if has_sim.any():
        sim_routes = rs[has_sim].copy()
        sim_routes["route_short"] = simplified.loc[has_sim, 0] + simplified.loc[has_sim, 1]
        rs = pd.concat([rs, sim_routes], ignore_index=True)

    # Prefer exact direction-bearing GTFS codes; retain base codes for feeds without suffixes.
    full = tickets["route_short"] + tickets["leg"]
    tickets["route_short"] = full.where(full.isin(rs["route_short"].unique()), tickets["route_short"])

    cols = ["route_short", "route_id", "direction_id", "seq", "stop_norm"]
    if "stop_name" in rs.columns:
        cols.append("stop_name")
    rs_base = rs[cols].drop_duplicates()

    alias_dfs = [
        rs_base[["route_short", "route_id", "direction_id", "seq"]].assign(match_name=rs_base["stop_norm"], priority=1)
    ]

    if "stop_name" in rs_base.columns:
        slash_parts = rs_base["stop_name"].astype(str).str.split("/")
        for i in range(4):
            part = slash_parts.str[i].dropna()
            if not part.empty:
                idx = part.index
                c_part = norm_name(part)
                alias_dfs.append(pd.DataFrame({
                    "route_short": rs_base.loc[idx, "route_short"],
                    "route_id": rs_base.loc[idx, "route_id"],
                    "direction_id": rs_base.loc[idx, "direction_id"],
                    "seq": rs_base.loc[idx, "seq"],
                    "match_name": c_part,
                    "priority": 2,
                }))
                no_parens = norm_name(part.str.replace(r"\(.*?\)", "", regex=True))
                alias_dfs.append(pd.DataFrame({
                    "route_short": rs_base.loc[idx, "route_short"],
                    "route_id": rs_base.loc[idx, "route_id"],
                    "direction_id": rs_base.loc[idx, "direction_id"],
                    "seq": rs_base.loc[idx, "seq"],
                    "match_name": no_parens,
                    "priority": 2,
                }))
                alias_dfs.append(pd.DataFrame({
                    "route_short": rs_base.loc[idx, "route_short"],
                    "route_id": rs_base.loc[idx, "route_id"],
                    "direction_id": rs_base.loc[idx, "direction_id"],
                    "seq": rs_base.loc[idx, "seq"],
                    "match_name": remove_suffixes(no_parens),
                    "priority": 3,
                }))

        no_parens_all = norm_name(rs_base["stop_name"].astype(str).str.replace(r"\(.*?\)", "", regex=True))
        alias_dfs.append(pd.DataFrame({
            "route_short": rs_base["route_short"],
            "route_id": rs_base["route_id"],
            "direction_id": rs_base["direction_id"],
            "seq": rs_base["seq"],
            "match_name": no_parens_all,
            "priority": 2,
        }))

    alias_dfs.append(pd.DataFrame({
        "route_short": rs_base["route_short"],
        "route_id": rs_base["route_id"],
        "direction_id": rs_base["direction_id"],
        "seq": rs_base["seq"],
        "match_name": remove_suffixes(rs_base["stop_norm"]),
        "priority": 3,
    }))

    rs_aliases = pd.concat(alias_dfs, ignore_index=True)
    rs_aliases = rs_aliases[rs_aliases["match_name"].astype(str).str.len() >= 1].sort_values("priority").drop_duplicates(
        subset=["route_short", "route_id", "direction_id", "match_name"]
    )

    t_cols = [c for c in ["ticket_id", "trip_key", "route_short", "start_norm", "end_norm", "pax", "trip_start", "day_type", "vehicle_number"] if c in tickets.columns]
    ticket_variants = [
        tickets[t_cols],
        tickets[t_cols].assign(start_norm=remove_suffixes(tickets["start_norm"])),
        tickets[t_cols].assign(end_norm=remove_suffixes(tickets["end_norm"])),
        tickets[t_cols].assign(start_norm=remove_suffixes(tickets["start_norm"]), end_norm=remove_suffixes(tickets["end_norm"])),
    ]

    matched_parts = []
    seen_ids = set()
    for t_var in ticket_variants:
        unm = t_var[~t_var["ticket_id"].isin(seen_ids)]
        if unm.empty:
            break
        b = unm.merge(
            rs_aliases.rename(columns={"match_name": "start_norm", "seq": "board_seq"}),
            on=["route_short", "start_norm"],
            how="inner",
        )
        a = rs_aliases.rename(columns={"match_name": "end_norm", "seq": "alight_seq"})
        m = b.merge(a, on=["route_short", "route_id", "direction_id", "end_norm"], how="inner")
        m = m[m["board_seq"] < m["alight_seq"]].drop_duplicates(subset="ticket_id")
        if not m.empty:
            seen_ids.update(m["ticket_id"])
            matched_parts.append(m)

    if not matched_parts:
        return pd.DataFrame(columns=list(tickets.columns) + ["route_id", "direction_id", "board_seq", "alight_seq"])

    result = pd.concat(matched_parts, ignore_index=True).drop_duplicates(subset="ticket_id")
    return result.reset_index(drop=True)


def _explode_segments(m: pd.DataFrame, minutes_per_stop: float) -> pd.DataFrame:
    """One row per (ticket, segment crossed)."""
    n = (m["alight_seq"] - m["board_seq"]).to_numpy()
    idx = np.repeat(np.arange(len(m)), n)
    offs = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    out = m.iloc[idx][
        ["trip_key", "route_id", "route_short", "direction_id", "pax",
         "trip_start", "first_seq", "day_type"]
    ].copy()
    out["seg"] = m["board_seq"].to_numpy()[idx] + offs
    seg_ts = out["trip_start"] + pd.to_timedelta(
        (out["seg"] - out["first_seq"]) * minutes_per_stop, unit="m"
    )
    out["half_hour"] = seg_ts.dt.hour * 2 + (seg_ts.dt.minute >= 30).astype(int)
    return out


def build_segment_load(tickets: pd.DataFrame, route_stops: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Running-count load per route, direction, segment, day type, half hour.

    Segment `seg` is the stretch between stop `seg` and stop `seg + 1`.
    Load per trip is the sum of passengers on tickets crossing the segment.
    Averages are over trips that sold at least one ticket crossing the segment
    (slight upward bias; documented limitation).
    """
    m = match_stops(tickets, route_stops)
    rate = len(m) / max(len(tickets), 1)
    log.info("Tickets matched to stops: %d of %d (%.1f%%)", len(m), len(tickets), 100 * rate)
    if m.empty:
        raise ValueError("No tickets matched GTFS stops. Check route and stop name formats.")

    m["first_seq"] = m.groupby("trip_key")["board_seq"].transform("min")
    mps = cfg["load"]["minutes_per_stop"]

    parts = []
    for _, chunk in m.groupby(m["trip_start"].dt.date):
        exp = _explode_segments(chunk, mps)
        grp = exp.groupby(
            ["trip_key", "route_id", "route_short", "direction_id", "seg", "day_type", "half_hour"],
            as_index=False,
        )["pax"].sum()
        parts.append(grp)
    trip_seg = pd.concat(parts, ignore_index=True)

    agg = (
        trip_seg.groupby(["route_id", "route_short", "direction_id", "seg", "day_type", "half_hour"])["pax"]
        .agg(load_mean="mean", load_std="std", n_trips="size")
        .reset_index()
    )
    agg["load_std"] = agg["load_std"].fillna(0.0)

    uplift = cfg["load"]["uplift"]
    z = cfg["load"]["ci_z"]
    se = agg["load_std"] / np.sqrt(agg["n_trips"])
    agg["load_est"] = agg["load_mean"] * uplift
    agg["load_low"] = (agg["load_mean"] - z * se).clip(lower=0) * uplift
    agg["load_high"] = (agg["load_mean"] + z * se) * uplift
    return agg
