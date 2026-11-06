"""Streamlit caching for the constellation scene.

Kept separate from ``presentation.py`` so the assembly logic stays importable and
testable without Streamlit. The scene is the expensive object (catalogue load +
thousands of SGP4 propagations), so it is cached; the *payload* is rebuilt per run
because it carries the current selection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

import streamlit as st

from src.ui.presentation import ConstellationScene, load_constellation

__all__ = ["SCENE_TTL_SECONDS", "clear_scene_cache", "load_constellation_cached"]

#: Scene lifetime. Element sets refresh every 2 hours on CelesTrak; five minutes keeps
#: the window near the present without recomputing on every interaction.
SCENE_TTL_SECONDS = 300


@st.cache_data(ttl=SCENE_TTL_SECONDS, show_spinner=False, max_entries=4)
def load_constellation_cached(
    group_ids: Sequence[str],
    window_minutes: float,
    offline: bool,
    force_refresh: bool,
    when: datetime | None = None,
) -> ConstellationScene:
    """Cached wrapper around :func:`src.ui.presentation.load_constellation`."""
    return load_constellation(
        tuple(group_ids),
        window_minutes=window_minutes,
        offline=offline,
        force_refresh=force_refresh,
        when=when,
    )


def clear_scene_cache() -> None:
    """Drop cached scenes so the next rerun recomputes from source."""
    load_constellation_cached.clear()
    st.session_state.pop("orbix_loaded_scene", None)
    st.session_state.pop("orbix_analysis_frame", None)
