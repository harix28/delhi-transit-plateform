"""Audit saved holdout artifacts and scaling sensitivity; not occupancy validation."""
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from src.config import load_config, resolve


def labels(values, cfg):
    return np.select([values <= cfg["seated"], values <= cfg["total"]], ["seats", "standing"], default="crowded")


def main():
    cfg = load_config()
    out = resolve(cfg, "outputs")
    source = out / "holdout_comparison.csv"
    saved = json.loads((out / "evaluation.json").read_text())
    frame = pd.read_csv(source)
    error = frame.load_est_train - frame.load_est_test
    majority = frame.level_train.mode().iloc[0]
    audit = {
        "scope": "Recomputed from saved holdout artifacts, not rerun from raw tickets; no independent occupancy ground truth",
        "evaluated_bins": len(frame),
        "test_bins": saved["test_bins"],
        "evaluated_test_bin_fraction": len(frame) / saved["test_bins"],
        "usable_ticket_records": saved["records"],
        "matched_ticket_records": saved["matched_records"],
        "match_rate_among_usable": saved["matched_records"] / saved["records"],
        "mae_ticket_derived_scaled_load": float(error.abs().mean()),
        "rmse_ticket_derived_scaled_load": float(np.sqrt(error.pow(2).mean())),
        "label_agreement": float((frame.level_train == frame.level_test).mean()),
        "majority_label_baseline": majority,
        "majority_label_agreement": float((frame.level_test == majority).mean()),
        "configured_uplift": cfg["load"]["uplift"],
        "capacity_assumptions": cfg["crowding"],
        "scaling_matches_config": bool(np.allclose(frame.load_mean_train * cfg["load"]["uplift"], frame.load_est_train)),
        "minimum_sample_rule_satisfied": bool(((frame.n_trips_train >= 5) & (frame.n_trips_test >= 5)).all()),
        "warnings": ["Capacity and uplift are assumptions, not measured calibration.",
                     "Metrics cover overlapping bins with at least five inferred trips in both splits.",
                     "Excluded tickets and bins can create selection bias.",
                     "Do not interpret label agreement as real occupancy accuracy."]
    }
    with source.open("rb") as stream:
        audit["holdout_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    sensitivity = []
    for uplift in [1, 5, 10, 20, 25]:
        train = labels(frame.load_mean_train * uplift, cfg["crowding"])
        test = labels(frame.load_mean_test * uplift, cfg["crowding"])
        sensitivity.append({"uplift_assumption": uplift,
            "mae_scaled_ticket_load": float((frame.load_mean_train - frame.load_mean_test).abs().mean() * uplift),
            "label_agreement": float((train == test).mean()),
            "test_seats_fraction": float((test == "seats").mean()),
            "test_standing_fraction": float((test == "standing").mean()),
            "test_crowded_fraction": float((test == "crowded").mean())})
    pd.DataFrame(sensitivity).to_csv(out / "uplift_sensitivity.csv", index=False)
    diagnostic = frame.assign(abs_error=error.abs(), agreement=frame.level_train == frame.level_test)
    diagnostic.groupby(["route_short_train", "direction_id", "day_type"]).agg(
        evaluated_bins=("seg", "size"), mae_scaled_ticket_load=("abs_error", "mean"),
        label_agreement=("agreement", "mean")).reset_index().to_csv(out / "route_holdout_diagnostics.csv", index=False)
    patterns = pd.read_csv(out / "train_time_patterns.csv")
    rows = []
    for day in ["weekday", "weekend"]:
        for name, lo, hi in [("morning_08_10",16,19),("midday_12_15",24,29),("evening_17_20",34,39)]:
            g = patterns[(patterns.day_type == day) & patterns.half_hour.between(lo, hi)]
            if not g.empty:
                rows.append({"day_type":day,"window":name,"observed_segment_bins":int(g.segment_bins.sum()),
                    "conditional_mean_scaled_ticket_load":float((g.mean_ticket_load*g.segment_bins).sum()/g.segment_bins.sum())})
    pd.DataFrame(rows).to_csv(out / "pattern_diagnostics.csv", index=False)
    (out / "validation_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
