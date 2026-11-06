"""Bounded Streamlit caches for pure statistics, keyed by actual data and controls.

Scene propagation is cached separately. Frame construction is held once per session
scene, so repeated widget interactions do not hash all the scene's keyframe arrays.
"""
import streamlit as st

from src.analysis import overview as dashboard, stats
from src.analysis.frame import catalog_frame


def analysis_frame(scene):
    cached = st.session_state.get("orbix_analysis_frame")
    if cached is None or cached["scene"] is not scene:
        cached = {"scene": scene, "frame": catalog_frame(scene)}
        st.session_state["orbix_analysis_frame"] = cached
    return cached["frame"]


def _cache(function):
    return st.cache_data(show_spinner=False, max_entries=12)(function)


overview = _cache(dashboard.overview)
group_altitudes = _cache(dashboard.group_altitudes)
discoveries = _cache(dashboard.discoveries)
unusual_satellites = _cache(dashboard.unusual_satellites)
describe = _cache(stats.describe)
outliers = _cache(stats.outliers)
freshness = _cache(stats.freshness)
missing_report = _cache(stats.missing_report)
correlation_matrix = _cache(stats.correlation_matrix)
pooled_vs_within = _cache(stats.pooled_vs_within)
per_group_correlations = _cache(stats.per_group_correlations)
