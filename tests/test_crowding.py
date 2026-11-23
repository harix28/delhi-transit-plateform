import pandas as pd

from src.config import load_config
from src.models.crowding import add_crowding


def test_levels_and_confidence():
    cfg = load_config()
    df = pd.DataFrame({"load_est": [10, 50, 120], "n_trips": [30, 10, 1]})
    out = add_crowding(df, cfg)
    assert list(out["level"]) == ["seats", "standing", "crowded"]
    assert list(out["confidence"]) == ["high", "medium", "low"]
