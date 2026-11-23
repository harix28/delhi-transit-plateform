import numpy as np
import pandas as pd


def add_crowding(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add ratio, level (seats/standing/crowded) and confidence columns.

    Thresholds are PLACEHOLDERS: tune against known peak patterns once real
    bus capacities are confirmed.
    """
    c = cfg["crowding"]
    out = df.copy()
    out["ratio"] = out["load_est"] / c["total"]
    out["level"] = np.select(
        [out["load_est"] <= c["seated"], out["load_est"] <= c["total"]],
        ["seats", "standing"],
        default="crowded",
    )
    out["confidence"] = np.select(
        [out["n_trips"] >= c["high_n"], out["n_trips"] >= c["med_n"]],
        ["high", "medium"],
        default="low",
    )
    return out
