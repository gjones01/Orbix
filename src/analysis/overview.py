"""Plain-language dashboard summaries derived from the existing analysis frame.

Display bands describe propagated altitude at the anchor only; they are deliberately
separate from the element-derived orbital regimes used by the statistical diagnostics.
All calculations retain kilometres. Unit conversion happens only at presentation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.analysis import stats

KM_PER_MILE = 1.609344
BANDS = ("All altitudes", "Below 2,000 km", "2,000–30,000 km", "30,000 km and above")


def altitude_subset(frame: pd.DataFrame, band: str) -> pd.DataFrame:
    """Select non-overlapping display bands without reclassifying mean orbits."""
    altitude = frame["altitude_km"]
    masks = {
        BANDS[0]: altitude.notna(),
        BANDS[1]: altitude < 2000,
        BANDS[2]: (altitude >= 2000) & (altitude < 30000),
        BANDS[3]: altitude >= 30000,
    }
    return frame.loc[masks[band]].copy()


def group_altitudes(frame: pd.DataFrame) -> pd.DataFrame:
    """Reuse the documented descriptive statistics for the altitude range chart."""
    result = stats.describe(frame, ["altitude_km"])
    if result.empty:
        return result
    return result.loc[result["n"] > 0].sort_values("median", ignore_index=True)


def overview(frame: pd.DataFrame) -> dict:
    """Summary and fixed altitude flags, compared within group AND orbital regime.

The summary deliberately uses documented defaults, independent of exploratory sliders
in the advanced diagnostics. Small strata (<4 valid values) are counted as unassessed.
"""
    counts = (stats.composition(frame) if not frame.empty
              else pd.DataFrame(columns=["group", "n", "share_pct"]))
    counts = counts.loc[counts["n"] > 0]
    altitudes = frame["altitude_km"].dropna()
    summary, flagged = stats.outliers(stats.add_stratum(frame), "altitude_km", by="stratum")
    assessed = int(summary.loc[summary["n"] >= 4, "n"].sum()) if not summary.empty else 0
    return {
        "total": len(frame),
        "counts": counts,
        "largest": str(counts.iloc[0]["group"]) if not counts.empty else None,
        "largest_share": float(counts.iloc[0]["share_pct"]) if not counts.empty else 0,
        "median_altitude_km": float(altitudes.median()) if len(altitudes) else np.nan,
        "valid_altitudes": len(altitudes),
        "assessed": assessed,
        "flagged": flagged,
        "outlier_summary": summary,
    }


def discoveries(frame: pd.DataFrame, summary: dict, labels: dict[str, str],
                unit: str = "km") -> list[tuple[str, str]]:
    """Deterministic observations, each supported by this exact filtered frame."""
    factor = 1 if unit == "km" else 1 / KM_PER_MILE
    items = []
    valid = frame.dropna(subset=["altitude_km"])
    if not valid.empty:
        farthest = valid.loc[valid["altitude_km"].idxmax()]
        items.append(("Farthest from Earth in this selection",
                      f"{farthest['name']} is estimated at {farthest['altitude_km'] * factor:,.0f} "
                      f"{unit} above Earth at the snapshot time."))
    ranges = group_altitudes(frame)
    if not ranges.empty:
        eligible = ranges.loc[ranges["n"] >= 4].copy()
        if not eligible.empty:
            eligible["span"] = eligible["max"] - eligible["min"]
            widest = eligible.loc[eligible["span"].idxmax()]
            group = labels.get(str(widest["group"]), str(widest["group"]))
            items.append(("A closer look at altitude spread",
                          f"{group} spans {widest['min'] * factor:,.0f}–"
                          f"{widest['max'] * factor:,.0f} {unit} across {int(widest['n']):,} "
                          "available estimates. This is the widest full range among selected "
                          "groups with at least four estimates."))
    n = len(summary["flagged"])
    items.append(("Unusual does not mean unsafe",
                  f"{n:,} altitude estimates stand out among {summary['assessed']:,} assessed "
                  "satellites when compared within the same group and orbital regime. "
                  "Different orbit shells can explain a flag; it is not evidence of danger."))
    return items
