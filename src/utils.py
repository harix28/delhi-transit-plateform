import numpy as np
import pandas as pd


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres. Accepts scalars, arrays or Series."""
    r = 6371000.0
    lat1 = np.radians(np.asarray(lat1, dtype=float))
    lat2 = np.radians(np.asarray(lat2, dtype=float))
    dphi = lat2 - lat1
    dlmb = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dphi / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def canonical_stop_text(s: pd.Series) -> pd.Series:
    """Canonicalise stop names with common Delhi transit abbreviations, aliases and spelling variants."""
    s = s.fillna("").astype(str).str.lower()
    s = s.str.replace(r"[^a-z0-9 ]+", " ", regex=True)
    replacements = [
        (r"\b(xing|x ing|cross)\b", "crossing"),
        (r"\b(sec|sect)\b", "sector"),
        (r"\b(staion|stn)\b", "station"),
        (r"\b(hosp)\b", "hospital"),
        (r"\b(mor|morh|mod)\b", "more"),
        (r"\b(bdr)\b", "border"),
        (r"\b(pur|pura)\b", "puri"),
        (r"\b(vill)\b", "village"),
        (r"\b(t|ter)\b", "terminal"),
        (r"\b(s j hospital|sj hospital)\b", "safdurjung hospital"),
        (r"\b(devali|devli)\b", "devoli"),
        (r"\b(peeragarhi)\b", "peera garhi"),
        (r"\b(shadipur)\b", "shadi puri"),
        (r"\b(mahipalpur)\b", "mahipal puri"),
        (r"\b(radisson|radission|reddison)\b", "radisson"),
        (r"\b(air india office)\b", ""),
        (r"\b(isbt)\b", ""),
    ]
    for pattern, repl in replacements:
        s = s.str.replace(pattern, repl, regex=True)
    s = s.str.replace(r"\s+", " ", regex=True).str.strip()
    return s


def norm_name(s: pd.Series) -> pd.Series:
    """Normalise stop names so ticket names and GTFS names can be matched."""
    return canonical_stop_text(s)
