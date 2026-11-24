from pathlib import Path
import os

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path=None):
    path = Path(path or os.getenv("TRANSIT_CONFIG", ROOT / "config.yaml"))
    if not path.is_absolute():
        path = ROOT / path
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve(cfg, key):
    """Absolute path for a key under cfg['paths']."""
    return ROOT / cfg["paths"][key]
