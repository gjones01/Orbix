"""Orbix - interactive 3D satellite visualisation.

Streamlit entry point (Phase 1, Step D). Responsibilities are deliberately thin:
configure the page, gather the user's data options, ask ``src/ui`` for a scene, and
hand a JSON payload to the Globe.gl component. All orbital mathematics lives in
``src/orbital/propagation.py``; all scene assembly lives in
``src/ui/presentation.py``.

Run with::

    streamlit run app.py
"""

from __future__ import annotations

import html

import streamlit as st

from components.globe import build_scene_payload, globe_component
from src.api.celestrak import CelestrakError, CelestrakNoDataAvailable
from src.orbital.propagation import PropagationError
from src.ui import theme
from src.ui.loading import SCENE_TTL_SECONDS, clear_scene_cache, load_scene_cached
from src.ui.presentation import ISS_CATALOG_NUMBER, Scene, format_utc

#: Height of the globe iframe in pixels. The globe is the centrepiece, so it gets
#: the vertical space; the info panel is a compact column beside it.
GLOBE_HEIGHT = 700

COMPONENT_KEY = "orbix_globe"


# --------------------------------------------------------------------------- #
# Header and sidebar
# --------------------------------------------------------------------------- #


def render_header(scene: Scene | None) -> None:
    """Wordmark, tagline and the data-provenance chip."""
    chip = ""
    if scene is not None:
        chip = (
            f'<span class="ox-chip {html.escape(scene.source)}">'
            f"{html.escape(scene.source_label)}</span>"
        )
    st.markdown(
        f"""
        <div class="ox-header">
          <div class="ox-brand">
            <span class="ox-wordmark">ORBIX</span>
            <span class="sep">&middot;</span>
            <span class="ox-tagline">Orbital View &nbsp;/&nbsp; Phase 1</span>
          </div>
          {chip}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> tuple[bool, bool]:
    """Data-source controls. Returns ``(offline, force_refresh)``."""
    with st.sidebar:
        st.markdown(
            """
            <div style="display:flex;align-items:baseline;gap:8px;">
              <span style="font-size:1.02rem;font-weight:700;letter-spacing:0.26em;
                           color:#f2f7ff;">ORBIX</span>
              <span style="font-size:0.62rem;letter-spacing:0.16em;
                           color:#5f7186;text-transform:uppercase;">mission control</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("### Target")
        st.markdown(
            "**ISS (ZARYA)**  \n"
            f"NORAD {ISS_CATALOG_NUMBER} &middot; International Space Station"
        )

        st.markdown("### Orbital data")
        st.caption(
            "CelesTrak General Perturbations (OMM/JSON). Orbix caches responses for "
            "2 hours to respect CelesTrak's refresh cadence and usage policy."
        )
        offline = st.toggle(
            "Offline mode (snapshots only)",
            value=False,
            help="Never contact CelesTrak. Uses the newest snapshot committed under "
            "data/snapshots/, so the app works with no network.",
        )
        force_refresh = st.checkbox(
            "Force CelesTrak re-download",
            value=False,
            help="Ignores the 2-hour cache. CelesTrak may answer HTTP 403 "
            "'not updated', which Orbix reports as a cache hit.",
        )
        if st.button("Refresh scene", type="secondary"):
            clear_scene_cache()
            st.rerun()

        st.markdown("### Propagation")
        st.caption(
            "SGP4 via Skyfield. Positions are computed in Python; the globe only "
            "draws what it is given. The orbit track covers the next period in the "
            "Earth-fixed frame, so it drifts west each pass as the Earth turns."
        )

        st.markdown("### About")
        st.caption(
            "Phase 1, Step D. Step E adds selection and playback; Phase 2 adds EDA, "
            "Phase 3 the LLM assistant."
        )
    return offline, force_refresh


# --------------------------------------------------------------------------- #
# Info panel
# --------------------------------------------------------------------------- #


def render_info_panel(scene: Scene) -> None:
    """Compact mission-control readout of the propagated state."""
    rows = "\n".join(
        f'<div class="ox-row"><span class="ox-k">{html.escape(fact.label)}</span>'
        f'<span class="ox-v">{html.escape(fact.value)}'
        + (f'<span class="ox-hint">{html.escape(fact.hint)}</span>' if fact.hint else "")
        + "</span></div>"
        for fact in scene.facts() + (scene.subpoint_fact(),)
    )
    st.markdown(
        f"""
        <div class="ox-card">
          <div class="ox-card-head">
            <span class="kicker">Selected asset</span>
            <span class="name">{html.escape(scene.object_name)}</span>
          </div>
          <div class="ox-rows">{rows}</div>
          <div class="ox-card-foot">
            {html.escape(scene.source_detail)}<br />
            Element epoch {html.escape(format_utc(scene.state.epoch))}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Error state
# --------------------------------------------------------------------------- #


def render_error(exc: Exception, offline: bool) -> None:
    """Explain a failed load without pretending there is data."""
    kind = type(exc).__name__
    if isinstance(exc, CelestrakNoDataAvailable) and offline:
        advice = (
            "Offline mode is on and no saved snapshot covers this satellite. Turn "
            "offline mode off, or fetch the data once with a network connection so "
            "a snapshot is stored under data/snapshots/."
        )
    elif isinstance(exc, PropagationError):
        advice = (
            "The orbital elements were valid but SGP4 could not produce a position "
            "for the requested instant. Try refreshing the element set."
        )
    elif isinstance(exc, CelestrakError):
        advice = (
            "CelesTrak could not be reached or returned an unusable response. Orbix "
            "will not invent orbital data, so nothing is drawn. Retry, or switch on "
            "offline mode to use the committed snapshot."
        )
    else:
        advice = "An unexpected error occurred."
    st.markdown(
        f"""
        <div class="ox-notice">
          <strong>Could not load orbital data ({html.escape(kind)}).</strong><br />
          {html.escape(str(exc))}<br /><br />
          {html.escape(advice)}
        </div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Scene layout and entry point
# --------------------------------------------------------------------------- #


def render_scene(scene: Scene, offline: bool) -> None:
    """Lay out the globe beside the information panel."""
    payload = build_scene_payload(
        satellite=scene.state,
        track=scene.track,
        height=GLOBE_HEIGHT,
    )

    globe_col, panel_col = st.columns([3.0, 1.25], gap="medium")

    with globe_col:
        event = globe_component(payload, height=GLOBE_HEIGHT, key=COMPONENT_KEY)
        if event:
            st.session_state["orbix_last_globe_event"] = event

        caption = (
            f"Simulation {format_utc(scene.state.timestamp)}"
            " &nbsp;&middot;&nbsp; drag to rotate, scroll to zoom"
            f" &nbsp;&middot;&nbsp; track {scene.state.period_minutes:.2f} min,"
            f" {len(scene.track)} Earth-fixed samples"
        )
        st.markdown(f'<div class="ox-caption">{caption}</div>', unsafe_allow_html=True)

        last_event = st.session_state.get("orbix_last_globe_event")
        if last_event:
            st.markdown(
                '<div class="ox-caption">globe &rarr; python: '
                f"{html.escape(str(last_event))}</div>",
                unsafe_allow_html=True,
            )

    with panel_col:
        render_info_panel(scene)
        if scene.notes:
            st.caption("Notes: " + "; ".join(scene.notes))
        st.caption(
            f"Scene cached for {SCENE_TTL_SECONDS}s so the view stays stable while "
            "you interact; the satellite state is not real-time (see element age)."
        )


def main() -> None:
    """Configure the page, load a scene, and render it."""
    theme.configure_page()
    theme.inject_css()

    offline, force_refresh = render_sidebar()

    try:
        with st.spinner("Acquiring orbital elements and propagating the orbit..."):
            scene = load_scene_cached(
                ISS_CATALOG_NUMBER,
                offline=offline,
                force_refresh=force_refresh,
                when=None,
            )
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        render_header(None)
        render_error(exc, offline)
        return

    render_header(scene)
    render_scene(scene, offline)


if __name__ == "__main__":
    main()


