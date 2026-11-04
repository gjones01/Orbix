"""Streamlit caching for the scene loader.

Kept separate from ``presentation.py`` so that the assembly logic stays importable
and testable without Streamlit. The TTL matters for behaviour, not just speed: it
freezes the simulation timestamp between reruns so that rotating the globe or
toggling a control does not make the satellite jump.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from src.ui.presentation import ISS_CATALOG_NUMBER, Scene, load_scene

__all__ = ["SCENE_TTL_SECONDS", "clear_scene_cache", "load_scene_cached"]

#: How long a computed scene stays valid. Element sets refresh every 2 hours on
#: CelesTrak; a minute keeps the displayed position honest without recomputing on
#: every widget interaction.
SCENE_TTL_SECONDS = 60


@st.cache_data(ttl=SCENE_TTL_SECONDS, show_spinner=False, max_entries=8)
def load_scene_cached(
    catalog_number: int = ISS_CATALOG_NUMBER,
    offline: bool = False,
    force_refresh: bool = False,
    when: datetime | None = None,
) -> Scene:
    """Cached wrapper around :func:`src.ui.presentation.load_scene`."""
    return load_scene(
        catalog_number,
        offline=offline,
        force_refresh=force_refresh,
        when=when,
    )


def clear_scene_cache() -> None:
    """Drop cached scenes so the next rerun recomputes from source."""
    load_scene_cached.clear()
