"""Reproduce altitude flags and independently verify both rules on saved data.

Run from the repository root: python scripts/audit_altitude_outliers.py
The fixed UTC anchor makes the report reproducible with the same saved snapshots.
No data is downloaded and no thresholds are tuned.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.analysis import stats
from src.analysis.frame import catalog_frame
from src.ui.presentation import load_constellation


def audit(frame) -> dict:
    """Compare exact NORAD-ID sets with an independent NumPy implementation."""
    data = stats.add_stratum(frame)
    summary, flags = stats.outliers(data, "altitude_km", by="stratum")
    independent_ids = set()
    rows = []
    for group, part in data.groupby("stratum", observed=True):
        valid = part.dropna(subset=["altitude_km"])
        x = valid["altitude_km"].to_numpy()
        if len(x) < 4:
            rows.append({"group": group, "n": len(x), "assessed": False})
            continue
        q1, median, q3 = np.quantile(x, [.25, .5, .75], method="linear")
        lower, upper = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        mad = np.median(np.abs(x - median))
        iqr = (x < lower) | (x > upper)
        modified = np.abs(.6745 * (x - median) / mad) > 3.5 if mad > 0 else np.zeros(len(x), dtype=bool)
        union = iqr | modified
        independent_ids.update(int(n) for n in valid.loc[union, "norad_id"])
        rows.append({"group": group, "n": len(x), "assessed": True,
                     "median_km": float(median), "q1_km": float(q1), "q3_km": float(q3),
                     "mad_km": float(mad), "lower_fence_km": float(lower), "upper_fence_km": float(upper),
                     "iqr_only": int((iqr & ~modified).sum()),
                     "modified_z_only": int((modified & ~iqr).sum()),
                     "both": int((iqr & modified).sum()), "either": int(union.sum()),
                     "below_median": int((union & (x < median)).sum()),
                     "above_median": int((union & (x > median)).sum())})
    actual = set(flags["norad_id"]) if not flags.empty else set()
    assert independent_ids == actual, "Independent NumPy rules disagree with application"
    assert len(actual) == len(flags), "A satellite was counted twice"
    stars = data.loc[data["constellation"].astype(str) == "starlink"].copy()
    stars["flagged"] = stars["norad_id"].isin(actual)
    stars["altitude_band_25km"] = (stars["altitude_km"] // 25 * 25)
    shells = stars.groupby("altitude_band_25km").agg(n=("norad_id", "size"), flagged=("flagged", "sum"))
    samples = flags.copy()
    if not samples.empty:
        samples["delta_km"] = samples["value"] - samples["group_median"]
        samples = samples.sort_values("delta_km", key=abs, ascending=False).head(12)
    return {
        "anchor_utc": frame.attrs["anchor"].isoformat(), "catalogue_count": len(frame),
        "missing_altitude": int(frame["altitude_km"].isna().sum()),
        "assessed": int(summary.loc[summary["n"] >= 4, "n"].sum()),
        "unique_flagged": len(actual), "independent_check": "exact NORAD-ID sets match",
        "rules": "IQR k=1.5 OR abs(modified z)>3.5; per constellation/regime; n>=4",
        "strata": rows,
        "starlink_altitude_bins_25km": shells.reset_index().to_dict("records"),
        "examples": samples.fillna("undefined").to_dict("records"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--when", default="2026-11-05T23:00:00+00:00")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    scene = load_constellation(offline=True, when=datetime.fromisoformat(args.when))
    result = audit(catalog_frame(scene))
    elements = {str(n): satellite.element.as_omm() for n, satellite in sorted(scene.catalog.satellites.items())}
    result["source_elements_sha256"] = hashlib.sha256(
        json.dumps(elements, sort_keys=True).encode("utf-8")
    ).hexdigest()
    rendered = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
