"""The Analysis tab: descriptive statistics over the same snapshot the globe is showing.

Layout is deliberately a short report - overview, regimes, distributions, freshness,
outliers, correlations - in the order the assignment ranks them, and every section states
what it does *not* claim. Charts come from :mod:`src.analysis.figures`; nothing here
computes statistics itself, and nothing here fetches data.

Provenance is repeated in the UI, not just the code: axis titles carry units, the overview
names how many columns are elements / derived / propagated, and the propagated columns are
labelled as describing one instant.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd
import streamlit as st

from src.analysis import figures, stats
from src.analysis.frame import GEO_BAND_KM, LEO_APOGEE_KM, catalog_frame, column_docs
from src.api.catalog import GROUP_SPECS
from src.ui.presentation import ConstellationScene

__all__ = ["render_analysis"]

_HIGHLIGHT_KEY = "orbix_highlight_ids"

#: Variables offered in the analysis selectors. All are documented and numeric.
_CHOICES = (
    "altitude_km", "semi_major_axis_km", "period_min", "eccentricity",
    "inclination_deg", "raan_deg", "bstar", "element_age_hours",
    "mean_motion_rev_day", "perigee_radius_km", "apogee_radius_km",
)

#: Variables offered for correlation, limited to physically distinct quantities.
_CORRELATION_CHOICES = (
    "altitude_km", "semi_major_axis_km", "period_min", "eccentricity",
    "inclination_deg", "raan_deg", "bstar", "element_age_hours",
)


def _colours() -> dict[str, str]:
    return {spec.id: spec.colour for spec in GROUP_SPECS}


def _caption(text: str) -> None:
    st.markdown(f'<div class="ox-caption">{text}</div>', unsafe_allow_html=True)


def _note(text: str) -> None:
    st.markdown(f'<div class="ox-card-foot">{text}</div>', unsafe_allow_html=True)


def _stat_bar(frame: pd.DataFrame, scene: ConstellationScene) -> None:
    """The overview strip: what is being analysed, and how it is measured."""
    anchor = frame.attrs.get("anchor")
    counts = column_docs()["provenance"].value_counts()
    ages = frame["element_age_hours"].dropna()
    items = [
        ("Objects", f"{len(frame):,}"),
        ("Constellations", f"{frame['constellation'].nunique()}"),
        ("Anchor (UTC)", anchor.strftime("%Y-%m-%d %H:%M") if anchor else "-"),
        ("Median element age", f"{ages.median():.1f} h" if not ages.empty else "-"),
        ("Oldest element", f"{ages.max():.1f} h" if not ages.empty else "-"),
        ("Columns", f"{counts.sum()} "
                    f"({counts.get('element', 0)} element / {counts.get('derived', 0)} "
                    f"derived / {counts.get('propagated', 0)} propagated)"),
    ]
    cells = "".join(
        f'<div class="ox-row"><span class="ox-k">{key}</span>'
        f'<span class="ox-v">{value}</span></div>'
        for key, value in items
    )
    st.markdown(f'<div class="ox-card"><div class="ox-rows">{cells}</div></div>',
                unsafe_allow_html=True)


def _table(data: pd.DataFrame, *, height: int = 260, columns: Iterable[str] | None = None,
           precision: int = 3) -> None:
    """Render a DataFrame with sane column selection and rounding."""
    if data is None or data.empty:
        st.caption("Nothing to show.")
        return
    shown = data[list(columns)] if columns else data
    st.dataframe(
        shown.round(precision), width="stretch", height=height, hide_index=True,
    )


def _sync_highlight(ids: list[int]) -> None:
    """Store the chart selection and re-run once so the globe can pick it up.

    The globe is rendered before this tab, so it is showing the *previous* highlight. One
    guarded rerun (the same pattern as globe selection, D20) makes the two agree without
    a loop: the stored value is compared before triggering.
    """
    stored = sorted(st.session_state.get(_HIGHLIGHT_KEY) or [])
    if sorted(ids) != stored:
        st.session_state[_HIGHLIGHT_KEY] = sorted(ids)
        st.rerun()


def _section_regimes(
    frame: pd.DataFrame, colours: dict[str, str], highlight: list[int]
) -> None:
    """Priority 1: the altitude-inclination map, with regime encoded by marker shape."""
    st.subheader("Orbital regimes")
    st.markdown(
        "Propagated altitude at the anchor instant against the inclination carried by the "
        "element set. Marker **shape** is the element-derived regime; **colour** is the "
        "constellation. Select points on the chart (box or lasso in the modebar) to "
        "highlight the same satellites on the globe."
    )
    names = list(frame["constellation"].astype(str).value_counts().index)
    controls = st.columns([2, 1, 1])
    with controls[0]:
        chosen = st.multiselect(
            "Constellations", names, default=names, key="analysis_regime_groups"
        )
    with controls[1]:
        log_y = st.toggle("Log altitude axis", value=True, key="analysis_regime_log")
    with controls[2]:
        st.metric("Highlighted", len(highlight))

    subset = frame.loc[frame["constellation"].astype(str).isin(chosen)] if chosen \
        else frame.iloc[0:0]
    figure = figures.regime_scatter(
        subset, colours=colours, highlight_ids=highlight, log_y=log_y
    )
    state = st.plotly_chart(
        figure, key="analysis_regime_chart", on_select="rerun",
        selection_mode=("points", "box", "lasso"),
    )
    selected = figures.selection_norad_ids(figure, state)
    if selected:
        _sync_highlight(selected)
    elif highlight and not selected:
        _caption(
            "Highlight cleared. Chart selection drives the globe highlight; the globe's own "
            "click selection is independent."
        )

    _table(
        stats.regime_counts(subset if not subset.empty else frame),
        height=180, precision=0,
    )
    _note(
        "Regime boundaries are a convention, not physics: LEO means the mean orbit stays "
        f"below {LEO_APOGEE_KM - 6378.137:.0f} km altitude, GEO means the geosynchronous "
        f"band {GEO_BAND_KM[0]:.0f}-{GEO_BAND_KM[1]:.0f} km, and e >= 0.25 is HEO. "
        "Regime comes from the mean elements; the y value is where the object was at the "
        "anchor, so an eccentric orbit can sit outside its own regime - which is exactly "
        "why both are shown."
    )


def _section_distributions(frame: pd.DataFrame, colours: dict[str, str]) -> None:
    """Priority 2: distributions and descriptive statistics, stratified on demand."""
    st.subheader("Distributions")
    controls = st.columns([2, 1, 1, 1])
    with controls[0]:
        variable = st.selectbox("Variable", _CHOICES, index=0, key="analysis_dist_var")
    with controls[1]:
        by = st.radio(
            "Group by", ("constellation", "stratum"), horizontal=True,
            key="analysis_dist_by",
        )
    with controls[2]:
        kind = st.radio("Chart", ("box", "violin"), horizontal=True,
                        key="analysis_dist_kind")
    with controls[3]:
        st.caption("Violin shows density - the fastest way to spot a mixed group.")

    data = stats.add_stratum(frame) if by == "stratum" else frame
    st.plotly_chart(
        figures.distribution_box(data, variable, by=by, kind=kind, colours=colours),
        key="analysis_distribution_chart",
    )
    described = stats.describe(data, [variable], by=by)
    _table(described, height=240)
    _note(
        "Box: quartiles with Tukey whiskers (the same fence the outlier section uses). "
        "Violin: adds the density estimate. A group whose violin has two lobes is a "
        "mixture of populations - switch Group by to 'stratum' and the lobes separate "
        "into physically meaningful regimes."
    )


def _section_freshness(frame: pd.DataFrame) -> None:
    """Priority 3: element age and missing-data audit, side by side."""
    st.subheader("Freshness and missing data")
    left, right = st.columns([3, 2])
    fresh = stats.freshness(frame)
    with left:
        st.plotly_chart(
            figures.freshness_bar(fresh, stale_hours=stats.STALE_ELEMENT_HOURS),
            key="analysis_freshness_chart",
        )
        _table(
            fresh,
            height=200,
            columns=("group", "n", "median_age_h", "min_age_h", "max_age_h",
                     "stale_count", "oldest_name"),
        )
    with right:
        missing = stats.missing_report(frame)
        st.plotly_chart(figures.missing_bar(missing), key="analysis_missing_chart")
        _table(
            missing.loc[missing["missing"] > 0]
            if (missing["missing"] > 0).any() else missing.head(6),
            height=200,
            columns=("column", "provenance", "missing", "missing_pct"),
        )
    _note(
        f"Element age is measured against the scene anchor, not against the clock, so this "
        f"page stays reproducible. Above {stats.STALE_ELEMENT_HOURS:.0f} h SGP4 drift is "
        "kilometres or worse. Missing values are reported, never imputed: a zero-filled B* "
        "would claim the object has no drag."
    )


def _section_outliers(frame: pd.DataFrame) -> None:
    """Priority 4: robust diagnostics, optionally segmented by orbital regime."""
    st.subheader("Outlier diagnostics")
    controls = st.columns([2, 1, 1, 1, 1])
    with controls[0]:
        variable = st.selectbox("Variable", _CHOICES, index=0, key="analysis_out_var")
    with controls[1]:
        segmented = st.radio(
            "Segmentation", ("constellation", "stratum"), horizontal=True,
            key="analysis_out_by",
        )
    with controls[2]:
        iqr_k = st.slider("IQR fence k", 0.5, 3.0, 1.5, 0.25, key="analysis_out_iqr")
    with controls[3]:
        mad_z = st.slider("Modified z", 1.5, 6.0, 3.5, 0.5, key="analysis_out_mad")
    with controls[4]:
        hetero = st.slider("Heterogeneous above (%)", 1, 50, 10,
                           key="analysis_out_hetero")

    data = stats.add_stratum(frame) if segmented == "stratum" else frame
    summary, flagged = stats.outliers(
        data, variable, by=segmented, iqr_k=iqr_k, mad_z=mad_z, heterogeneous_pct=hetero
    )
    st.plotly_chart(
        figures.outlier_strip(data, variable, by=segmented, summary=summary),
        key="analysis_outlier_chart",
    )
    _table(
        summary,
        height=200,
        columns=("group", "n", "median", "iqr", "lower_fence", "upper_fence", "mad",
                 "n_iqr", "n_mad", "n_flagged", "outlier_pct", "heterogeneous",
                 "degenerate", "note"),
    )
    if flagged.empty:
        _caption("No observation was flagged with these thresholds.")
    else:
        st.markdown(f"**{len(flagged):,} flagged observations** "
                    "(displayed, not removed - the frame keeps every row)")
        _table(
            flagged.sort_values("robust_z", key=lambda s: s.abs(), ascending=False),
            height=220,
            columns=("norad_id", "name", "group", "regime", "value", "group_median",
                     "robust_z", "reason"),
        )
    _note(
        "Two independent rules: the Tukey IQR fence and the modified z-score "
        "(0.6745 (x - median) / MAD). A flag is a prompt to inspect, not a verdict, and no "
        "row is deleted. When a large share of a group is flagged - the threshold above is a "
        "configurable heuristic, not a law - the likelier explanation is that the group "
        "mixes populations: switch Segmentation to 'stratum' and the flags usually vanish. "
        "If MAD is zero (more than half the group shares one value, e.g. GPS B*), the robust "
        "z-score is undefined and only the fence is used."
    )


def _section_correlations(frame: pd.DataFrame) -> None:
    """Priority 5: pooled versus within-group correlations, with the per-group evidence."""
    st.subheader("Correlations: pooled vs within constellation")
    controls = st.columns([3, 1, 1])
    with controls[0]:
        variables = st.multiselect(
            "Variables", _CORRELATION_CHOICES,
            default=["altitude_km", "inclination_deg", "eccentricity", "period_min"],
            key="analysis_corr_vars",
        )
    with controls[1]:
        by = st.radio("Compare within", ("constellation", "stratum"), horizontal=True,
                      key="analysis_corr_by")
    with controls[2]:
        min_n = st.slider("Minimum group size", 5, 50, 10, 5,
                          key="analysis_corr_min_n")
    if len(variables) < 2:
        st.caption("Select at least two variables.")
        return

    data = stats.add_stratum(frame) if by == "stratum" else frame
    left, right = st.columns([2, 3])
    with left:
        st.plotly_chart(
            figures.correlation_heatmap(
                stats.correlation_matrix(data, variables),
                title="Pooled Spearman (ranks, all groups together)",
            ),
            key="analysis_corr_heatmap",
        )
    comparison = stats.pooled_vs_within(data, variables, by=by, min_n=min_n)
    with right:
        st.plotly_chart(
            figures.pooled_within_chart(comparison), key="analysis_corr_dumbbell"
        )

    st.markdown("**Pooled vs stratified, with the evidence**")
    _table(
        comparison,
        height=200,
        columns=("pair", "pooled_rho", "within_median", "within_min", "within_max",
                 "n_groups_defined", "undefined_groups", "gap", "same_sign_share",
                 "reversed_groups", "simpson_pattern", "material_difference"),
    )

    pair = st.selectbox("Per-group detail for", comparison["pair"].tolist(),
                        key="analysis_corr_pair")
    row = comparison.loc[comparison["pair"] == pair].iloc[0]
    per_group = stats.per_group_correlations(data, row["x"], row["y"], by=by, min_n=min_n)
    _table(per_group, height=200)
    _note(
        "Read the columns, not the story. <code>gap</code> is pooled minus the within "
        "median: a gap means the pooled number is not a within-group relationship. A gap is "
        "<b>not</b> a sign reversal - <code>same_sign_share</code> and "
        "<code>reversed_groups</code> report direction per group, and "
        "<code>simpson_pattern</code> is set only when every defined group has the opposite "
        "sign to the pooled coefficient. Groups where a coefficient is undefined (too few "
        "objects, or a constant such as GPS B*) are counted in "
        "<code>undefined_groups</code> and listed with the reason above, never silently "
        "dropped. No p-values are shown: these are near-censuses of the tracked catalogue, "
        "so significance would be an artefact of n."
    )


def render_analysis(scene: ConstellationScene) -> list[int]:
    """Render the Analysis tab and return the NORAD ids highlighted from its charts."""
    colours = _colours()
    highlight = sorted(st.session_state.get(_HIGHLIGHT_KEY) or [])

    with st.spinner("Building the analysis frame from the loaded snapshot..."):
        frame = catalog_frame(scene)

    st.markdown("### Analysis")
    st.markdown(
        "Descriptive statistics over exactly the snapshot the globe is showing: same "
        "objects, same elements, same anchor instant. Nothing here refetches data, and "
        "nothing here is a temporal series - it is one snapshot describing one moment."
    )
    _stat_bar(frame, scene)
    _caption(
        f"anchor {frame.attrs['anchor']:%Y-%m-%d %H:%M} UTC / "
        f"{frame.attrs['window_minutes']:.0f} min playback window / "
        f"propagated columns describe that instant only"
    )

    _section_regimes(frame, colours, highlight)
    _section_distributions(frame, colours)
    _section_freshness(frame)
    _section_outliers(frame)
    _section_correlations(frame)

    with st.expander("Column provenance (what is observed, derived, or modelled)"):
        st.dataframe(column_docs(), width="stretch", hide_index=True, height=420)

    return sorted(st.session_state.get(_HIGHLIGHT_KEY) or [])
