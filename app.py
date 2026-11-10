"""Orbix - interactive 3D satellite constellation explorer.

Streamlit entry point (Phase 1, Steps E-F). Thin by design: configure the page,
collect the user's data scope, ask ``src/ui`` for a scene, hand the payload to the
Globe.gl component and render the panels that report what is loaded.

Orbital mechanics live in ``src/orbital``; scene assembly in ``src/ui/presentation.py``.
Filtering and simulation playback run inside the globe component so that neither
triggers a Streamlit rerun.

Run with::

    python -m streamlit run app.py
"""

from __future__ import annotations

import html

import streamlit as st

from components.globe import globe_component
from src.api.catalog import GROUP_SPECS
from src.ui import theme
from src.ui.loading import (
    clear_scene_cache,
    load_constellation_cached,
)
from src.ui.analysis_tab import render_analysis
from src.ui.user_guide import render_user_guide
from src.ui.panels import render_error, render_selected
from src.ui.presentation import ConstellationScene, default_groups

GLOBE_HEIGHT = 720
COMPONENT_KEY = "orbix_globe"

WINDOW_CHOICES = {
    "10 minutes": 10.0,
    "20 minutes": 20.0,
    "45 minutes": 45.0,
    "90 minutes (one ISS orbit)": 90.0,
}


def render_header(scene: ConstellationScene | None) -> None:
    """Wordmark, headline counts and the data-provenance chip."""
    if scene is None:
        chip = ""
        meta = ""
    else:
        sources = {status.source for status in scene.catalog.groups if status.ok}
        label = "LIVE" if sources == {"network"} else (
            "MIXED" if "network" in sources else "CACHED"
        )
        chip_class = "live" if label == "LIVE" else "cache"
        chip = f'<span class="ox-chip {chip_class}">{label}</span>'
        meta = (
            f'<span class="ox-tagline">{scene.unique_count:,} satellites &middot; '
            f"{scene.rendered_count:,} shown</span>"
        )
    st.markdown(
        f"""
        <div class="ox-header">
          <div class="ox-brand">
            <span class="ox-wordmark">ORBIX</span>
            <span class="sep">&middot;</span>
            <span class="ox-tagline">Satellite explorer</span>
            {meta}
          </div>
          {chip}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> tuple[tuple[str, ...], float, bool, bool]:
    """Data-scope controls. Returns ``(groups, window_minutes, offline, refresh)``."""
    with st.sidebar:
        st.markdown(
            """
            <div style="display:flex;align-items:baseline;gap:8px;">
              <span style="font-size:1.02rem;font-weight:700;letter-spacing:0.26em;
                           color:#f2f7ff;">ORBIX</span>
              <span style="font-size:0.62rem;letter-spacing:0.16em;color:#5f7186;
                           text-transform:uppercase;">mission control</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("### Satellite groups")
        defaults = set(default_groups())
        chosen: list[str] = []
        for spec in GROUP_SPECS:
            enabled = st.checkbox(
                f"{spec.label}",
                value=spec.id in defaults,
                key=f"group_{spec.id}",
            )
            if enabled:
                chosen.append(spec.id)

        st.markdown("### Playback window")
        window_label = st.selectbox(
            "Window width",
            list(WINDOW_CHOICES),
            index=1,
            label_visibility="collapsed",
            help="How far ahead you can play the simulation.",
        )
        window_minutes = WINDOW_CHOICES[window_label]

        st.markdown("### Data")
        offline = st.toggle(
            "Use saved data",
            value=False,
            help="Explore saved satellite data without downloading updates.",
        )
        force_refresh = st.session_state.get("guide_force_refresh", False)
        if st.button("Reload scene", type="secondary"):
            clear_scene_cache()
            st.rerun()

    return tuple(chosen), window_minutes, offline, force_refresh


def main() -> None:
    """Configure the page, load a scene, render the globe and the panels."""
    theme.configure_page()
    theme.inject_css()

    groups, window_minutes, offline, force_refresh = render_sidebar()

    if not groups:
        render_header(None)
        st.markdown(
            '<div class="ox-notice">Select at least one constellation in the sidebar.'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    try:
        with st.spinner("Loading catalogue and propagating the constellation..."):
            scope = (groups, window_minutes, offline, force_refresh)
            saved = st.session_state.get("orbix_loaded_scene")
            # Pin the user's snapshot across interactions, including after the shared
            # five-minute cache expires. Reload scene or a data-scope change replaces it.
            if saved is None or saved["scope"] != scope:
                saved = {"scope": scope, "scene": load_constellation_cached(
                    groups, window_minutes, offline, force_refresh, None
                )}
                st.session_state["orbix_loaded_scene"] = saved
            scene = saved["scene"]
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        render_header(None)
        render_error(exc, offline)
        return

    # Selection survives ordinary interactions: it lives in session state and is
    # re-applied to the payload on every run.
    if "orbix_selected" not in st.session_state:
        st.session_state["orbix_selected"] = None
    selected = st.session_state["orbix_selected"]

    render_header(scene)

    # Keyframes are megabytes; only re-ship them when the scene actually changed.
    signature = (
        tuple(sorted(groups)),
        round(window_minutes, 2),
        scene.catalog.retrieved_at.isoformat(),
    )
    include_keyframes = st.session_state.get("orbix_scene_signature") != signature

    # The Analysis tab drives a highlight through session state; read it before building
    # the payload so the globe and the chart agree within the same run.
    highlight_ids = sorted(st.session_state.get("orbix_highlight_ids") or [])

    globe_tab, analysis_tab, guide_tab = st.tabs(["Globe", "Analysis", "User guide"])

    with globe_tab:
        payload = scene.payload(
            enabled_groups=groups,
            selection=selected,
            highlight_ids=highlight_ids,
            include_keyframes=include_keyframes,
        )
        # Explicit Analysis actions may reveal/focus their targets once. Keeping
        # this token stable prevents ordinary widget reruns from moving the camera.
        payload["analysis_action"] = st.session_state.get("analysis_globe_action")
        payload_bytes = len(str(payload))
        st.session_state["orbix_scene_signature"] = signature

        event = globe_component(payload, height=GLOBE_HEIGHT, key=COMPONENT_KEY)
        if (event and event.get("event") == "select"
                and event != st.session_state.get("orbix_last_event")):
            norad_id = int(event["norad_id"])
            # Components retain their last event on unrelated reruns. Consume it once
            # so an old globe click cannot override a newer Analysis selection.
            st.session_state["orbix_last_event"] = dict(event)
            # The payload above carried the *previous* selection, so re-run once with the
            # new one: that is what brings back the selected satellite's trail. Guarded so
            # a repeated event cannot loop.
            if scene.element(norad_id) is not None and st.session_state.get("orbix_selected") != norad_id:
                st.session_state["orbix_selected"] = norad_id
                st.rerun()
            selected = st.session_state["orbix_selected"]

        render_selected(scene, selected)
        for status in scene.catalog.groups:
            if not status.ok:
                st.warning(f"{status.spec.label} could not be loaded. Try Reload scene or saved data.")
        st.caption(f"Playback: {scene.plan.start:%H:%M}–{scene.plan.end:%H:%M} UTC · Estimated positions")

    with analysis_tab:
        render_analysis(scene)

    with guide_tab:
        render_user_guide(scene, payload_bytes)

if __name__ == "__main__":
    main()
