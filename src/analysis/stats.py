"""Descriptive statistics, missing-data audit, robust outliers and correlation analysis.

Everything here is pure pandas/numpy and free of Streamlit, so it can be reviewed and
tested without a running app. These functions return plain DataFrames; the Plotly figures
built on them live in :mod:`src.analysis.figures`.

Statistical choices, and why
----------------------------
**Medians and quartiles, not just means.** Orbital element distributions are heavy tailed
and strongly multi-modal (a constellation is a set of discrete shells), so a mean
describes almost nothing on its own and a standard deviation assumes a symmetry the data
does not have.

**Spearman, not Pearson.** The relationships of interest are monotone at best, not linear,
and several variables (eccentricity, B*) are skewed and bounded. Spearman correlates
ranks, so one extreme object cannot dominate the coefficient.

**No p-values.** These are not random samples from a population: they are near-complete
censuses of the tracked catalogues. At n ~ 10^4 every correlation is "significant", so a
p-value would be noise dressed as evidence. Effect size (rho) and n are reported instead.

**Nothing is ever dropped.** Outlier detection returns flags alongside the untouched frame;
the caller keeps every row and decides what to display. A flag is a diagnostic, not a
deletion, and not a claim that the object is wrong.

**Stratification before interpretation.** A "constellation" is an editorial group, not a
homogeneous population: the weather group alone mixes ~800 km polar platforms with
geosynchronous meteorological satellites. :func:`add_stratum` therefore splits groups by
orbital regime, and :func:`pooled_vs_within` reports per-group coefficients next to the
pooled one instead of asserting an aggregation effect.
"""

from __future__ import annotations

from itertools import combinations
from typing import Sequence

import numpy as np
import pandas as pd

from src.analysis.provenance import column as column_spec

__all__ = [
    "DESCRIPTIVE_COLUMNS",
    "HETEROGENEOUS_PCT",
    "STALE_ELEMENT_HOURS",
    "add_stratum",
    "composition",
    "correlation_matrix",
    "describe",
    "freshness",
    "missing_report",
    "outliers",
    "per_group_correlations",
    "pooled_vs_within",
    "regime_counts",
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

#: Default share of a group that may be flagged before the group itself is reported as
#: heterogeneous. A DIAGNOSTIC HEURISTIC, not a statistical law: the real criterion is the
#: shape of the distribution, which the caller should plot. Override per call.
HETEROGENEOUS_PCT = 10.0


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


def regime_counts(frame: pd.DataFrame, by: str = "constellation") -> pd.DataFrame:
    """Cross-tabulation of constellation by orbital regime.

    This is how a mixed group announces itself: one row per constellation, one column per
    regime, cell = number of objects. ``weather`` shows up as LEO *and* GEO rather than as
    a single population with outliers.
    """
    table = pd.crosstab(frame[by].astype(str), frame["regime"].astype(str))
    return table.reset_index().rename(columns={"index": by})


def add_stratum(
    frame: pd.DataFrame, *, with_regime: bool = True, by: str = "constellation"
) -> pd.DataFrame:
    """Return a copy with a ``stratum`` column for stratified statistics.

    With ``with_regime`` the stratum looks like ``"weather / GEO"``; without it, the
    stratum is just the constellation. Nothing else changes: the original columns,
    provenance and rows are preserved, so any flagged observation can still be traced back
    to the object that produced it.
    """
    out = frame.copy()
    groups = out[by].astype(str)
    if with_regime:
        out["stratum"] = groups + " / " + out["regime"].astype(str)
    else:
        out["stratum"] = groups
    return out


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
    heterogeneous_pct: float = HETEROGENEOUS_PCT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Robust outlier diagnostics per group, by IQR fence and modified z-score.

    Two independent criteria are used because they fail in different places. The Tukey
    fence ``[q1 - k*IQR, q3 + k*IQR]`` is scale-free but assumes the body of the
    distribution is unimodal; the modified z-score uses the median and MAD, tolerates
    skew, but is undefined when MAD is zero. An object is flagged if *either* criterion
    fires, and ``reason`` records which.

    **Flags never remove rows.** The frame is untouched; ``flagged`` is a separate table of
    observations with the rule that fired, so the caller can display, filter or ignore it.
    A flag is a prompt to look, not a verdict.

    **A high flag rate indicts the group, not the objects.** When more than
    ``heterogeneous_pct`` per cent of a group is flagged, ``heterogeneous`` is set and the
    note says the group is probably multi-modal: the weather group mixes LEO and GEO
    platforms, Starlink occupies discrete shells, and in both cases the "outliers" are a
    legitimate orbital population. The threshold is passed in as a configurable heuristic
    precisely because it is not a statistical law - stratify the group instead
    (:func:`add_stratum`) and look at the distribution.

    Returns
    -------
    (summary, flagged)
        ``summary``: one row per group with fences, counts, ``outlier_pct`` and flags.
        ``flagged``: the individual observations, with their reason.
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
        else:
            z = _MAD_SCALE * (values - median) / mad
            flag_mad = z.abs() > mad_z

        flagged = flag_iqr | flag_mad
        outlier_pct = 100.0 * int(flagged.sum()) / n
        notes = []
        if degenerate:
            notes.append("MAD is zero (over half the group shares one value): "
                         "IQR fence only")
        if outlier_pct > heterogeneous_pct:
            notes.append(
                f"{outlier_pct:.0f}% flagged (> {heterogeneous_pct:g}% heuristic): the "
                "group is more likely multi-modal than contaminated - stratify it by "
                "regime and inspect the distribution before calling these objects outliers"
            )
        row.update({
            "q1": q1, "median": median, "q3": q3, "iqr": iqr,
            "lower_fence": lower, "upper_fence": upper, "mad": mad,
            "n_iqr": int(flag_iqr.sum()), "n_mad": int(flag_mad.sum()),
            "n_flagged": int(flagged.sum()), "outlier_pct": outlier_pct,
            "heterogeneous": bool(outlier_pct > heterogeneous_pct),
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
            if bool(flag_mad.at[index]):
                reasons.append(
                    f"modified z {float(z.at[index]):+.1f} exceeds {mad_z:g}"
                )
            flagged_rows.append({
                "norad_id": int(sub.at[index, "norad_id"]),
                "name": str(sub.at[index, "name"]),
                "group": group,
                "regime": str(sub.at[index, "regime"]),
                "value": float(values.at[index]),
                "group_median": median,
                "robust_z": float(z.at[index]) if not degenerate else np.nan,
                "reason": "; ".join(reasons),
            })

    return pd.DataFrame(summary_rows), pd.DataFrame(flagged_rows)


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


def per_group_correlations(
    frame: pd.DataFrame,
    x: str,
    y: str,
    by: str = "constellation",
    *,
    min_n: int = 10,
) -> pd.DataFrame:
    """Every per-group Spearman coefficient for one variable pair, defined or not.

    This is the evidence behind :func:`pooled_vs_within`: one row per group with its n, its
    coefficient and - crucially - *why* a coefficient is missing when it is missing. A
    correlation of a constant is undefined, not zero, and a group with five objects cannot
    support one; both cases are reported instead of being quietly dropped.
    """
    names = [x, y]
    rows = []
    for group, sub in frame.groupby(by, observed=True):
        pair = sub[names].dropna()
        n = int(len(pair))
        if n < min_n:
            rho, defined, reason = np.nan, False, f"only {n} complete pairs (min {min_n})"
        elif pair[x].nunique() < 2 or pair[y].nunique() < 2:
            constant = x if pair[x].nunique() < 2 else y
            rho, defined, reason = np.nan, False, f"{constant} is constant in this group"
        else:
            value = pair[x].corr(pair[y], method="spearman")
            if pd.isna(value):
                rho, defined, reason = np.nan, False, "coefficient undefined"
            else:
                rho, defined, reason = float(value), True, ""
        rows.append({
            "group": str(group), "n": n, "rho": rho,
            "defined": defined, "reason": reason,
        })
    return pd.DataFrame(rows).sort_values("n", ascending=False, ignore_index=True)


def correlation_matrix(
    frame: pd.DataFrame, columns: Sequence[str], *, by: str | None = None
) -> pd.DataFrame:
    """Spearman rank-correlation matrix, optionally for one constellation."""
    names = _numeric(frame, columns)
    subset = frame if by is None else frame.loc[frame["constellation"] == by]
    return subset[names].corr(method="spearman")


def pooled_vs_within(
    frame: pd.DataFrame,
    columns: Sequence[str],
    by: str = "constellation",
    *,
    min_n: int = 10,
    material_gap: float = 0.3,
) -> pd.DataFrame:
    """Compare pooled Spearman coefficients with the same coefficients inside each group.

    Pooling mixes *between-group* structure into the coefficient: two variables can
    correlate across groups simply because the groups differ in both, while inside any
    single group there is no relationship - or the opposite one. The table is therefore
    written to be read sceptically:

    * ``pooled_rho``, ``pooled_n`` - the number people quote, and its n.
    * ``within_min`` / ``within_median`` / ``within_max`` plus the per-group n range - what
      actually happens inside the groups.
    * ``gap`` = pooled - within median, and ``material_difference`` when |gap| >=
      ``material_gap``. A gap proves nothing about *why*.
    * ``same_sign_share`` - share of defined groups whose coefficient has the pooled sign.
      **A difference in magnitude is not a sign reversal**, and this column exists so the
      two cannot be confused.
    * ``reversed_groups`` - how many groups have the opposite sign to the pooled one.
    * ``simpson_pattern`` - true only when *every* defined group has a non-zero coefficient
      of the opposite sign to the pooled one, with at least two groups. That is the narrow
      condition under which pooled and stratified associations genuinely disagree in
      direction, i.e. the only case where language about aggregation reversal is earned. It
      is a flag on this evidence, not a claim about the world.
    * ``undefined_groups`` - groups where no coefficient exists. Reported, never omitted;
      :func:`per_group_correlations` gives the reason for each.
    """
    names = _numeric(frame, columns)
    pairs = list(combinations(names, 2))
    pooled_matrix = frame[names].corr(method="spearman")

    # One eligibility rule for the values and the "undefined" count, so a group can never be
    # counted as undefined and used as evidence at the same time.
    per_group: dict[str, dict[tuple[str, str], tuple[float | None, int]]] = {}
    for group, sub in frame.groupby(by, observed=True):
        rho = sub[names].corr(method="spearman")
        entries: dict[tuple[str, str], tuple[float | None, int]] = {}
        for pair in pairs:
            complete = int(len(sub[list(pair)].dropna()))
            if complete >= min_n and pd.notna(rho.loc[pair]):
                entries[pair] = (float(rho.loc[pair]), complete)
            else:
                entries[pair] = (None, 0)
        per_group[str(group)] = entries

    rows = []
    for a, b in pairs:
        pair = (a, b)
        pooled = pooled_matrix.loc[a, b]
        defined = {
            group: values[pair][0]
            for group, values in per_group.items()
            if values[pair][0] is not None
        }
        sizes = [
            values[pair][1] for values in per_group.values()
            if values[pair][0] is not None
        ]
        base = {
            "x": a, "y": b, "pair": f"{a} ~ {b}",
            "pooled_rho": pooled, "pooled_n": int(len(frame)),
            "n_groups_defined": len(defined),
            "undefined_groups": len(per_group) - len(defined),
        }
        if not defined:
            rows.append({
                **base,
                "within_min": np.nan, "within_median": np.nan, "within_max": np.nan,
                "within_n_min": 0, "within_n_max": 0, "gap": np.nan,
                "same_sign_share": np.nan, "reversed_groups": 0,
                "simpson_pattern": False, "material_difference": False,
            })
            continue

        within = np.array(list(defined.values()), dtype="float64")
        median_within = float(np.median(within))
        gap = float(pooled - median_within) if pd.notna(pooled) else float("nan")
        if pd.notna(pooled) and pooled != 0:
            same_sign = np.sign(within) == np.sign(pooled)
            same_sign_share = float(same_sign.mean())
            reversed_groups = int((~same_sign & (within != 0)).sum())
            simpson = bool(
                len(within) >= 2 and np.all(np.sign(within) == -np.sign(pooled))
            )
        else:
            same_sign_share, reversed_groups, simpson = np.nan, 0, False

        rows.append({
            **base,
            "within_min": float(within.min()),
            "within_median": median_within,
            "within_max": float(within.max()),
            "within_n_min": int(min(sizes)),
            "within_n_max": int(max(sizes)),
            "gap": gap,
            "same_sign_share": same_sign_share,
            "reversed_groups": reversed_groups,
            "simpson_pattern": simpson,
            "material_difference": bool(pd.notna(gap) and abs(gap) >= material_gap),
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values(
        "gap", key=lambda s: s.abs(), ascending=False, ignore_index=True
    )
