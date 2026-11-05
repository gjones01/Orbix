"""Satellite exploration with progressively disclosed academic diagnostics.

All views share one group filter and the globe snapshot; no data is fetched here.
"""

from __future__ import annotations

from html import escape
from typing import Iterable

import pandas as pd
import streamlit as st

from src.analysis import figures, stats
from src.analysis.overview import BANDS, KM_PER_MILE, altitude_subset, discoveries, group_altitudes, overview
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


def _table(data: pd.DataFrame, *, height: int = 260, columns: Iterable[str] | None = None,
           precision: int = 3) -> None:
    """Render a DataFrame with sane column selection and rounding."""
    if data is None or data.empty:
        st.caption("Nothing to show.")
        return
    shown = (data[list(columns)] if columns else data).round(precision).copy()
    # Frame attrs include the datetime anchor, already displayed explicitly above.
    # Arrow table metadata must be JSON-serializable.
    shown.attrs = {}
    st.dataframe(
        shown, width="stretch", height=height, hide_index=True,
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
    log_y = st.toggle("Compress altitude scale (logarithmic)", value=False,
                      key="analysis_regime_log",
                      help="Equal distances then represent equal ratios, not equal kilometres.")
    subset = frame
    figure = figures.regime_scatter(
        # Keep trace indices stable across selection reruns. Plotly displays its own
        # selection; moving highlighted rows into another trace would reinterpret
        # the retained event against different satellites on the next run.
        subset, colours=colours, log_y=log_y
    )
    state = st.plotly_chart(
        figure, key=f"analysis_regime_chart_{st.session_state.get('analysis_selection_revision', 0)}", on_select="rerun",
        selection_mode=("points", "box", "lasso"),
    )
    selected = figures.selection_norad_ids(figure, state)
    # Process only a new selection event. A retained Plotly selection must not
    # resurrect highlights after a group/satellite button or an explicit clear.
    previous = st.session_state.get("analysis_last_chart_selection", [])
    if selected != previous:
        st.session_state["analysis_last_chart_selection"] = selected
        _sync_highlight(selected)

    _table(
        stats.regime_counts(subset),
        height=180, precision=0,
    )
    _note(
        "Regime boundaries are a convention, not physics: LEO means the mean orbit stays "
        f"below {LEO_APOGEE_KM - 6378.137:.0f} km altitude, GEO means the geosynchronous "
        f"band {GEO_BAND_KM[0]:.0f}-{GEO_BAND_KM[1]:.0f} km from Earth's centre, and e >= 0.25 is HEO. "
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
        f"page stays reproducible. {stats.STALE_ELEMENT_HOURS:.0f} h is the app's age-warning "
        "threshold; actual position accuracy depends on the orbit and element quality. Missing values are reported, never imputed: a zero-filled B* "
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


def _set_highlight(ids: list[int]) -> None:
    """Reset the chart's retained event when a dashboard button takes ownership."""
    st.session_state["analysis_selection_revision"] = (
        st.session_state.get("analysis_selection_revision", 0) + 1
    )
    st.session_state["analysis_last_chart_selection"] = []
    _sync_highlight(ids)


def _cards(summary: dict, labels: dict[str, str], unit: str) -> None:
    factor = 1 if unit == "km" else 1 / KM_PER_MILE
    typical = summary["median_altitude_km"] * factor
    cards = [
        ("Satellites analyzed", f"{summary['total']:,}",
         f"Across {len(summary['counts'])} selected groups"),
        ("Most common group", labels.get(summary["largest"], summary["largest"] or "—"),
         f"{summary['largest_share']:.1f}% of selected satellites"),
        ("Typical altitude", f"{typical:,.0f} {unit}" if pd.notna(typical) else "Unavailable",
         f"Median of {summary['valid_altitudes']:,} altitude estimates"),
        ("Unusual altitudes", f"{len(summary['flagged']):,}",
         f"Among {summary['assessed']:,} assessed · worth exploring"),
    ]
    cells = "".join(
        f'<div class="ox-analysis-metric"><div class="ox-analysis-label">{escape(label)}</div>'
        f'<div class="ox-analysis-value">{escape(value)}</div>'
        f'<div class="ox-analysis-detail">{escape(detail)}</div></div>'
        for label, value, detail in cards
    )
    st.markdown(f'<div class="ox-analysis-metrics">{cells}</div>', unsafe_allow_html=True)


def _freshness_summary(frame: pd.DataFrame) -> None:
    ages = frame["element_age_hours"].dropna()
    if ages.empty:
        st.caption("Orbit-data age is unavailable. See the data quality audit below.")
        return
    age = float(ages.median())
    if age < 0:
        text = f"Typical orbit-data epoch is {abs(age):.0f} hours after the snapshot time."
    else:
        duration = f"{age:.0f} hours" if age < 48 else f"{age / 24:.1f} days"
        text = f"Orbit data is typically {duration} old at the snapshot time."
    stale = int((ages > stats.STALE_ELEMENT_HOURS).sum())
    if stale:
        text += f" {stale:,} element sets exceed the 3.5-day age warning."
    missing = int(frame["altitude_km"].isna().sum())
    if missing:
        text += f" {missing:,} satellites have no altitude estimate."
    st.caption(text + " Data quality and limitations are explained below.")


def _explore(frame: pd.DataFrame, summary: dict, labels: dict[str, str],
             colours: dict[str, str], unit: str) -> None:
    st.subheader("Explore satellite groups")
    largest = labels.get(summary["largest"], summary["largest"])
    st.markdown(f"**{largest} accounts for {summary['largest_share']:.1f}%** of this selection. "
                "Larger groups have more influence on the overall typical altitude.")
    st.plotly_chart(figures.group_count_bar(summary["counts"], labels=labels, colours=colours),
                    key="analysis_group_counts", config={"displayModeBar": False})
    left, right = st.columns([2, 1], vertical_alignment="bottom")
    with left:
        group = st.selectbox("Group to highlight on the globe", summary["counts"]["group"].tolist(),
                             format_func=lambda g: labels.get(g, g), key="analysis_highlight_group")
    with right:
        if st.button("Highlight group", key="analysis_highlight_group_button", width="stretch"):
            ids = frame.loc[(frame["constellation"].astype(str) == group)
                            & frame["altitude_km"].notna(), "norad_id"].tolist()
            _set_highlight(ids)
    st.caption("Open the Globe tab to see highlighted satellites. Objects without a position "
               "estimate cannot be highlighted. Each satellite is counted in its primary group.")

    st.subheader("How high do satellites orbit?")
    st.markdown("Compare heights above Earth. Narrow the range to see nearby orbits more clearly.")
    band = st.radio("Altitude range", BANDS, horizontal=True, key="analysis_altitude_band")
    subset = altitude_subset(frame, band)
    st.caption(f"Showing {len(subset):,} of {summary['valid_altitudes']:,} available altitude "
               "estimates. This range control affects only the chart below; boundaries are in kilometres.")
    st.plotly_chart(figures.altitude_ranges(group_altitudes(subset), labels=labels,
                                           colours=colours, unit=unit),
                    key="analysis_altitude_ranges", config={"displayModeBar": False})
    st.caption("Dot = typical height (median). Thick line = the middle half of satellites. "
               "Thin line = lowest to highest. All heights are estimates at the snapshot time; "
               "a line shows a range, not a continuous population. Hover over a dot for counts.")


def _discoveries(frame: pd.DataFrame, summary: dict, labels: dict[str, str], unit: str) -> None:
    st.subheader("Interesting discoveries")
    items = discoveries(frame, summary, labels, unit)
    for title, body in items:
        st.markdown(f'<div class="ox-analysis-discovery"><strong>{escape(title)}</strong>'
                    f'<p>{escape(body)}</p></div>', unsafe_allow_html=True)
    st.caption("Computed from your selected groups. Unusual-altitude counts use the default "
               "IQR and modified z-score rules within each group and orbital regime. "
               "Strata with fewer than four estimates are unassessed; no rows are removed.")
    with st.expander("Explore individual satellites"):
        only_flagged = st.toggle("Only unusual altitudes", key="analysis_only_flagged")
        candidates = frame.loc[frame["altitude_km"].notna()]
        if only_flagged:
            ids = summary["flagged"]["norad_id"] if not summary["flagged"].empty else []
            candidates = candidates.loc[candidates["norad_id"].isin(ids)]
        if candidates.empty:
            st.info("No satellites with an altitude estimate match this view.")
        else:
            names = candidates.set_index("norad_id")["name"].to_dict()
            selected = st.selectbox("Satellite", candidates["norad_id"].tolist(),
                                    format_func=lambda n: f"{names[n]} · NORAD {n}",
                                    key="analysis_satellite")
            row = candidates.loc[candidates["norad_id"] == selected].iloc[0]
            factor = 1 if unit == "km" else 1 / KM_PER_MILE
            st.write(f"Estimated height: **{row['altitude_km'] * factor:,.0f} {unit}** · "
                     f"Orbit tilt: **{row['inclination_deg']:.1f}°**")
            st.caption("Orbit tilt is the angle of the orbital plane relative to Earth’s equator.")
            if st.button("Highlight satellite on globe"):
                _set_highlight([int(selected)])


def render_analysis(scene: ConstellationScene) -> list[int]:
    """Render an approachable overview and retain every academic diagnostic."""
    labels = {spec.id: spec.label for spec in GROUP_SPECS}
    colours = _colours()
    frame = catalog_frame(scene)
    st.markdown('<div class="ox-analysis-eyebrow">SATELLITE EXPLORER</div>', unsafe_allow_html=True)
    st.title("A closer look at orbit")
    st.markdown("Explore the satellites in your loaded snapshot: which groups dominate, "
                "how high they orbit, and what stands out.")
    st.caption(f"Position estimates for {frame.attrs['anchor']:%d %b %Y · %H:%M} UTC. "
               "Computed from CelesTrak orbital elements using SGP4; globe playback moves "
               "forward from this time, while this analysis stays at the starting snapshot.")
    available = [spec.id for spec in GROUP_SPECS
                 if spec.id in set(frame["constellation"].astype(str))]
    # Reconcile persisted filters when the sidebar changes the loaded catalogue.
    old_available = st.session_state.get("analysis_available_groups")
    if "analysis_groups" not in st.session_state:
        saved = st.session_state.get("analysis_saved_groups", available)
        st.session_state["analysis_groups"] = [g for g in saved if g in available]
    if old_available != available:
        previous = st.session_state.get("analysis_groups", available)
        st.session_state["analysis_groups"] = (available if previous == old_available
                                               else [g for g in available if g in previous])
        st.session_state["analysis_available_groups"] = available
    left, right = st.columns([3, 1])
    def save_groups() -> None:
        # A globe selection can rerun before this tab renders. Keep the filter in
        # non-widget state so Streamlit's widget cleanup cannot discard it.
        st.session_state["analysis_saved_groups"] = st.session_state["analysis_groups"]
    with left:
        groups = st.multiselect("Groups to compare", available, format_func=lambda g: labels[g],
                                key="analysis_groups", on_change=save_groups,
                                help="Applies to the summary, discoveries "
                                "and all advanced diagnostics. Load more groups in the sidebar.")
    st.session_state["analysis_saved_groups"] = list(groups)
    with right:
        unit = st.radio("Height units", ("km", "mi"), horizontal=True, key="analysis_units")
    subset = frame.loc[frame["constellation"].astype(str).isin(groups)].copy()
    scope = tuple(groups)
    if st.session_state.get("analysis_scope") != scope:
        st.session_state["analysis_scope"] = scope
        st.session_state["analysis_selection_revision"] = (
            st.session_state.get("analysis_selection_revision", 0) + 1
        )
        st.session_state["analysis_last_chart_selection"] = []
    highlight = sorted(st.session_state.get(_HIGHLIGHT_KEY) or [])
    valid_ids = set(subset.loc[subset["altitude_km"].notna(), "norad_id"])
    retained = [n for n in highlight if n in valid_ids]
    if highlight != retained:
        _set_highlight(retained)
    if subset.empty:
        st.info("Choose at least one group above to explore its satellites. "
                "If no groups are available, check the data-loading report on the Globe tab.")
        return []
    summary = overview(subset)
    _cards(summary, labels, unit)
    _freshness_summary(subset)
    if highlight:
        st.caption(f"{len(highlight):,} satellites highlighted on the Globe tab.")
        if st.button("Clear globe highlights"):
            _set_highlight([])
    _explore(subset, summary, labels, colours, unit)
    _discoveries(subset, summary, labels, unit)

    st.subheader("Data quality & methodology")
    st.caption("Inspect the evidence behind these views. All diagnostics use your selected "
               "groups. Technical tables and charts retain their documented scientific units.")
    with st.expander("Data freshness, missing values & source details"):
        st.write(f"Catalogue retrieved: {scene.catalog.retrieved_at:%Y-%m-%d %H:%M} UTC. "
                 "Retrieval time records the download; element epochs record when the orbit "
                 "was fitted. Neither is a live position measurement.")
        for status in scene.catalog.groups:
            st.write(f"{status.spec.label}: {status.label}" +
                     (f" — {status.error}" if status.error else ""))
        _section_freshness(subset)
    with st.expander("Descriptive statistics & distributions"):
        _section_distributions(subset, colours)
        st.markdown("**All descriptive statistics**")
        _table(stats.describe(subset), height=400)
    with st.expander("Outlier diagnostics & thresholds"):
        st.caption("The overview uses altitude, group + regime, k = 1.5 and modified z = 3.5. "
                   "The controls below are an independent investigation and do not change its count.")
        _section_outliers(subset)
        st.markdown("**Evidence for the overview’s unusual-altitude count**")
        _table(summary["outlier_summary"])
        _table(summary["flagged"])
    with st.expander("Correlation analysis"):
        _section_correlations(subset)
    with st.expander("Advanced orbit map & linked selection"):
        _section_regimes(subset, colours, highlight)
    with st.expander("Satellite data & column methodology"):
        st.caption("Altitudes are propagated WGS-84 heights above Earth. Semi-major axis, "
                   "perigee and apogee radii describe mean elements and are measured from "
                   "Earth’s centre. Altitude display bands are not orbital-regime classifications. "
                   "Missing observations remain missing, and flags never remove satellites.")
        _table(subset, height=420)
        st.dataframe(column_docs(), width="stretch", hide_index=True, height=420)
    return sorted(st.session_state.get(_HIGHLIGHT_KEY) or [])
