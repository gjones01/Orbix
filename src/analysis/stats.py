"""Descriptive statistics, missing-data audit, robust outliers and correlation analysis.

Everything here is pure pandas/numpy and free of Streamlit, so it can be reviewed and
tested without a running app. Figures are built separately
(:mod:`src.analysis.figures`); these functions return plain DataFrames.

Statistical choices, and why
----------------------------
**Medians and quartiles, not just means.** Orbital element distributions are heavy tailed
and strongly multi-modal (a constellation is a set of discrete shells), so a mean
describes almost nothing on its own and a standard deviation assumes a symmetry the data
does not have.

**Spearman, not Pearson.** The relationships of interest are monotone at best, not
linear, and several variables (eccentricity, B*) are skewed and bounded. Spearman
correlates ranks, so one extreme object cannot dominate the coefficient.

**No p-values.** These are not random samples from a population: they are near-complete
censuses of the tracked catalogues. At n ~ 10^4 every correlation is "significant", so a
p-value would be noise dressed as evidence. Effect size (rho) and n are reported instead.

**Robust z-scores, with a stated failure mode.** The modified z-score
``0.6745 (x - median) / MAD`` divides by the median absolute deviation, which is zero
whenever more than half of a group shares one value - exactly what happens to inclination
inside a single Starlink shell. That case is detected and reported as *degenerate*
instead of producing infinity, and the IQR fence is used on its own there.
"""

from __future__ import annotations

from itertools import combinations
from typing import Sequence

import numpy as np
import pandas as pd

from src.analysis.provenance import column as column_spec

__all__ = [
    "DESCRIPTIVE_COLUMNS",
    "STALE_ELEMENT_HOURS",
    "composition",
    "correlation_matrix",
    "describe",
    "freshness",
    "missing_report",
    "outliers",
    "pooled_vs_within",
]

#: Element age beyond which SGP4 accuracy degrades to a few kilometres or worse. 3.5 days
#: is the usual rule of thumb for LEO.
STALE_ELEMENT_HOURS = 84.0

#: Columns summarised by :func:`describe` by default. The mix of propagated / derived /
#: element quantities is deliberate: seeing that ``altitude_km`` and
#: ``semi_major_axis_km`` disagree is itself informative.
DESCRIPTIVE_COLUMNS: tuple[str, ...] = (
    "altitude_km",
    "semi_major_axis_km",
    "period_min",
    "eccentricity",
    "inclination_deg",
    "raan_deg",
    "bstar",
    "element_age_hours",
)

#: Constant of the modified z-score: makes MAD comparable to a standard deviation.
_MAD_SCALE = 0.6745

#: If more than this share of a group is flagged, the group is probably a mixture of
#: populations rather than a single population with contamination.
_HETEROGENEOUS_PCT = 10.0


def _numeric(frame: pd.DataFrame, columns: Sequence[str]) -> list[str]:
    """Keep only the requested columns that exist and are numeric."""
    return [
        name for name in columns
        if name in frame.columns and pd.api.types.is_numeric_dtype(frame[name])
    ]


def missing_report(frame: pd.DataFrame) -> pd.DataFrame:
    """Audit of absent values per column, with provenance so context is not lost.

    Orbix never imputes. A missing B* is reported as missing rather than as 0.0, because
    a zero-filled drag term is a *claim* that the object has no drag.
    """
    rows = []
    total = len(frame)
    for name in frame.columns:
        spec = column_spec(name)
        missing = int(frame[name].isna().sum())
        rows.append({
            "column": name,
            "label": spec.label,
            "provenance": spec.kind.value,
            "dtype": str(frame[name].dtype),
            "n": total,
            "missing": missing,
            "missing_pct": 100.0 * missing / total if total else float("nan"),
        })
    return pd.DataFrame(rows)


def composition(frame: pd.DataFrame, by: str = "constellation") -> pd.DataFrame:
    """Group sizes and their share of the frame - the selection-bias number.

    Starlink alone is usually more than half of the catalogue, so a statistic computed
    over all objects is mostly a Starlink statistic. Reporting the share keeps that
    visible instead of implicit.
    """
    counts = frame[by].value_counts()
    total = int(counts.sum())
    return pd.DataFrame({
        "group": counts.index.astype(str),
        "n": counts.to_numpy(dtype="int64"),
        "share_pct": (100.0 * counts.to_numpy() / total) if total else np.array([]),
    })


def describe(
    frame: pd.DataFrame,
    columns: Sequence[str] = DESCRIPTIVE_COLUMNS,
    by: str = "constellation",
) -> pd.DataFrame:
    """Tidy per-group descriptive statistics.

    One row per (group, variable) with n, missing count, mean, sample standard deviation,
    min, quartiles, median, max and IQR. ``missing`` is kept separate from ``n`` so that a
    partially-null column never looks well sampled.
    """
    rows: list[dict[str, object]] = []
    for group, sub in frame.groupby(by, observed=True):
        for name in _numeric(frame, columns):
            spec = column_spec(name)
            values = sub[name].dropna()
            n = int(values.size)
            row: dict[str, object] = {
                "group": group,
                "variable": name,
                "unit": spec.unit,
                "n": n,
                "missing": int(sub[name].isna().sum()),
            }
            if n:
                q1, median, q3 = (float(v) for v in values.quantile([0.25, 0.5, 0.75]))
                row.update({
                    "mean": float(values.mean()),
                    "sd": float(values.std(ddof=1)) if n > 1 else float("nan"),
                    "min": float(values.min()),
                    "q1": q1,
                    "median": median,
                    "q3": q3,
                    "max": float(values.max()),
                    "iqr": q3 - q1,
                })
            else:
                row.update({key: float("nan") for key in
                            ("mean", "sd", "min", "q1", "median", "q3", "max", "iqr")})
            rows.append(row)
    return pd.DataFrame(rows)


def outliers(
    frame: pd.DataFrame,
    column: str,
    by: str = "constellation",
    *,
    iqr_k: float = 1.5,
    mad_z: float = 3.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Robust outlier detection per group, by IQR fence and modified z-score.

    Two independent criteria are used because they fail in different places. The Tukey
    fence ``[q1 - k*IQR, q3 + k*IQR]`` is scale-free but assumes the body of the
    distribution is unimodal; the modified z-score uses the median and MAD, tolerates
    skew, but is undefined when MAD is zero. An object is flagged if *either* criterion
    fires, and ``reason`` records which - nothing is called an outlier without a stated
    rule.

    Returns
    -------
    (summary, flagged)
        ``summary``: one row per group with fences, counts and a ``degenerate`` flag.
        ``flagged``: the individual objects, with the reason they were flagged.
    """
    column_spec(column)  # fail loudly on an undocumented column
    summary_rows: list[dict[str, object]] = []
    flagged_rows: list[dict[str, object]] = []

    for group, sub in frame.groupby(by, observed=True):
        values = sub.loc[sub[column].notna(), column]
        n = int(values.size)
        row: dict[str, object] = {
            "group": group, "variable": column, "n": n,
            "missing": int(sub[column].isna().sum()),
        }
        if n < 4:
            row.update({
                "q1": np.nan, "median": np.nan, "q3": np.nan, "iqr": np.nan,
                "lower_fence": np.nan, "upper_fence": np.nan, "mad": np.nan,
                "n_iqr": 0, "n_mad": 0, "n_flagged": 0, "outlier_pct": np.nan,
                "heterogeneous": False, "degenerate": True,
                "note": "too few values to judge",
            })
            summary_rows.append(row)
            continue

        q1, median, q3 = (float(v) for v in values.quantile([0.25, 0.5, 0.75]))
        iqr = q3 - q1
        lower, upper = q1 - iqr_k * iqr, q3 + iqr_k * iqr
        mad = float((values - median).abs().median())
        degenerate = mad <= 0.0

        flag_iqr = (values < lower) | (values > upper)
        if degenerate:
            # MAD of zero: half the group shares one value, so a robust z is undefined.
            z = pd.Series(np.nan, index=values.index, dtype="float64")
            flag_mad = pd.Series(False, index=values.index)
            z_thresh = pd.Series(False, index=values.index)
        else:
            z = _MAD_SCALE * (values - median) / mad
            z_thresh = z.abs() > mad_z
            flag_mad = z_thresh

        flagged = flag_iqr | flag_mad
        outlier_pct = 100.0 * int(flagged.sum()) / n
        notes = []
        if degenerate:
            notes.append("MAD is zero (over half the group shares one value): "
                         "IQR fence only")
        if outlier_pct > _HETEROGENEOUS_PCT:
            # A large "outlier" share is evidence about the GROUP, not about the objects:
            # the weather group, for instance, mixes ~800 km polar platforms with GEO
            # meteorological satellites, so a third of it sits outside the LEO fence and
            # none of those objects is anomalous. Call it what it is.
            notes.append(
                f"{outlier_pct:.0f}% flagged: the group is more likely multi-modal "
                "than contaminated - inspect its distribution before calling these "
                "objects outliers"
            )
        row.update({
            "q1": q1, "median": median, "q3": q3, "iqr": iqr,
            "lower_fence": lower, "upper_fence": upper, "mad": mad,
            "n_iqr": int(flag_iqr.sum()), "n_mad": int(flag_mad.sum()),
            "n_flagged": int(flagged.sum()), "outlier_pct": outlier_pct,
            "heterogeneous": bool(outlier_pct > _HETEROGENEOUS_PCT),
            "degenerate": bool(degenerate),
            "note": "; ".join(notes),
        })
        summary_rows.append(row)

        for index in values.index[flagged]:
            reasons = []
            if bool(flag_iqr.at[index]):
                reasons.append(
                    f"outside {iqr_k:g}xIQR fence [{lower:.4g}, {upper:.4g}]"
                )
            if bool(z_thresh.at[index]):
                reasons.append(
                    f"modified z {float(z.at[index]):+.1f} exceeds {mad_z:g}"
                )
            flagged_rows.append({
                "norad_id": int(sub.at[index, "norad_id"]),
                "name": str(sub.at[index, "name"]),
                "group": group,
                "value": float(values.at[index]),
                "group_median": median,
                "robust_z": float(z.at[index]) if not degenerate else np.nan,
                "reason": "; ".join(reasons),
            })

    return pd.DataFrame(summary_rows), pd.DataFrame(flagged_rows)


def correlation_matrix(
    frame: pd.DataFrame, columns: Sequence[str], *, by: str | None = None
) -> pd.DataFrame:
    """Spearman rank-correlation matrix, optionally for a single group."""
    names = _numeric(frame, columns)
    subset = frame if by is None else frame.loc[frame["constellation"] == by]
    return subset[names].corr(method="spearman")


def freshness(frame: pd.DataFrame, by: str = "constellation") -> pd.DataFrame:
    """Element-age audit: how current the orbit fits are, and who is stale.

    Age is measured against the scene anchor, not against "now", so the numbers stay
    reproducible when the same snapshot is re-analysed later.
    """
    rows = []
    for group, sub in frame.groupby(by, observed=True):
        ages = sub["element_age_hours"].dropna()
        if ages.empty:
            continue
        oldest = sub.loc[ages.idxmax()]
        stale = ages > STALE_ELEMENT_HOURS
        rows.append({
            "group": group,
            "n": int(ages.size),
            "min_age_h": float(ages.min()),
            "median_age_h": float(ages.median()),
            "max_age_h": float(ages.max()),
            "stale_count": int(stale.sum()),
            "stale_pct": 100.0 * float(stale.mean()),
            "oldest_norad": int(oldest["norad_id"]),
            "oldest_name": str(oldest["name"]),
        })
    return pd.DataFrame(rows).sort_values(
        "median_age_h", ascending=False, ignore_index=True
    )


def pooled_vs_within(
    frame: pd.DataFrame,
    columns: Sequence[str],
    by: str = "constellation",
    *,
    min_n: int = 10,
    min_rho: float = 0.3,
) -> pd.DataFrame:
    """Compare pooled Spearman correlations with the same correlations inside each group.

    This is the aggregation-bias demonstration. Pooling different constellations mixes the
    *between-group* structure into the coefficient: two variables can correlate across
    groups simply because groups differ in both (a Simpson-type effect), while within any
    single constellation there is no relationship at all - or the opposite one.

    For every variable pair the table reports the pooled rho, the within-group rho values
    (number of groups where it is defined, min, median, max) and the gap between pooled and
    median-within. A pair is marked ``sign_flip`` when at least one group's rho has the
    opposite sign to the pooled value, and ``confounded`` when the pooled and within-median
    coefficients differ by more than ``min_rho`` - in both cases the pooled number must not
    be reported as a finding about satellites.

    Groups are only included when they have at least ``min_n`` complete pairs and both
    variables vary: a correlation is undefined for a constant, and inclination inside a
    single Starlink shell is exactly that. Those groups are counted in
    ``undefined_groups`` instead of being silently dropped.
    """
    names = _numeric(frame, columns)
    pairs = list(combinations(names, 2))
    pooled_matrix = frame[names].corr(method="spearman")

    # One eligibility rule for both the values and the "undefined" count, so a group can
    # never be counted as undefined and used as evidence at the same time.
    per_group: dict[str, dict[tuple[str, str], float | None]] = {}
    for group, sub in frame.groupby(by, observed=True):
        rho = sub[names].corr(method="spearman")
        per_group[str(group)] = {
            pair: (
                float(rho.loc[pair])
                if len(sub[list(pair)].dropna()) >= min_n and pd.notna(rho.loc[pair])
                else None
            )
            for pair in pairs
        }

    rows = []
    for pair in pairs:
        a, b = pair
        pooled = pooled_matrix.loc[a, b]
        defined = {
            group: value
            for group, values in per_group.items()
            if (value := values[pair]) is not None
        }
        undefined_count = len(per_group) - len(defined)
        if not defined:
            rows.append({
                "pair": f"{a} ~ {b}", "pooled_rho": pooled,
                "pooled_n": int(len(frame)), "n_groups": 0,
                "within_min": np.nan, "within_median": np.nan, "within_max": np.nan,
                "gap": np.nan, "sign_flip": False, "confounded": False,
                "undefined_groups": undefined_count,
            })
            continue

        within = np.array(list(defined.values()))
        median_within = float(np.median(within))
        gap = float(pooled - median_within) if pd.notna(pooled) else float("nan")
        sign_flip = bool(
            pd.notna(pooled) and pooled != 0
            and np.any(np.sign(within) == -np.sign(pooled))
        )
        rows.append({
            "pair": f"{a} ~ {b}",
            "pooled_rho": pooled,
            "pooled_n": int(len(frame)),
            "n_groups": len(defined),
            "within_min": float(within.min()),
            "within_median": median_within,
            "within_max": float(within.max()),
            "gap": gap,
            "sign_flip": sign_flip,
            "confounded": bool(pd.notna(gap) and abs(gap) >= min_rho) or sign_flip,
            "undefined_groups": undefined_count,
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values("gap", key=lambda s: s.abs(), ascending=False,
                             ignore_index=True)
