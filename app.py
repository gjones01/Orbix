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
            f"{scene.rendered_count:,} propagated &middot; "
            f"{scene.keyframes} keyframes</span>"
        )
    st.markdown(
        f"""
        <div class="ox-header">
          <div class="ox-brand">
            <span class="ox-wordmark">ORBIX</span>
            <span class="sep">&middot;</span>
            <span class="ox-tagline">Constellation View &nbsp;/&nbsp; Phase 1</span>
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

        st.markdown("### Constellations to load")
        st.caption(
            "One CelesTrak request per group, cached for 2 hours. Changing a "
            "filter inside the globe never re-downloads anything."
        )
        defaults = set(default_groups())
        chosen: list[str] = []
        for spec in GROUP_SPECS:
            enabled = st.checkbox(
                f"{spec.label}",
                value=spec.id in defaults,
                key=f"group_{spec.id}",
                help=spec.note,
            )
            if enabled:
                chosen.append(spec.id)

        st.markdown("### Playback window")
        window_label = st.selectbox(
            "Window width",
            list(WINDOW_CHOICES),
            index=1,
            label_visibility="collapsed",
            help="The simulation starts at load time and runs forward. This sets how "
                 "far ahead it can play. Keyframe density adapts to the number of "
                 "satellites; the resulting interpolation error is shown under the "
                 "globe.",
        )
        window_minutes = WINDOW_CHOICES[window_label]

        st.markdown("### Orbital data")
        offline = st.toggle(
            "Offline mode (snapshots only)",
            value=False,
            help="Never contact CelesTrak. Uses the snapshots committed under "
                 "data/snapshots/, so the app works with no network.",
        )
        force_refresh = st.checkbox(
            "Force re-download",
            value=False,
            help="Ignores the 2-hour cache. CelesTrak may answer HTTP 403 "
                 "'not updated', which Orbix reports as a cache hit.",
        )
        if st.button("Reload scene", type="secondary"):
            clear_scene_cache()
            st.rerun()

        st.markdown("### Notes")
        st.caption(
            "SGP4 propagation in Python; the browser only interpolates between the "
            "supplied keyframes. Hover identifies, click selects, and the selected "
            "object keeps its trail while you rotate."
        )
    return tuple(chosen), window_minutes, offline, force_refresh


def render_selected(scene: ConstellationScene, selected: int | None) -> None:
    """Details of the selected satellite, or a hint when nothing is selected."""
    if selected is None:
        st.markdown(
            '<div class="ox-card"><div class="ox-rows">'
            '<div class="ox-row"><span class="ox-k">Selection</span>'
            '<span class="ox-v">none</span></div>'
            '<div class="ox-row"><span class="ox-k">How</span>'
            '<span class="ox-v">hover to identify \u00b7 click to select</span></div>'
            "</div></div>",
            unsafe_allow_html=True,
        )
        return

    element = scene.element(selected)
    if element is None:
        st.markdown(
            f'<div class="ox-notice">NORAD {selected} is not in the loaded catalogue.'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    group_id = scene.group_of(selected) or ""
    spec = next((item for item in GROUP_SPECS if item.id == group_id), None)
    state = None
    for index, norad_id in enumerate(scene.sample.norad_ids):
        if int(norad_id) == int(selected):
            rows = scene.sample.lat[index], scene.sample.lng[index], scene.sample.alt[index]
            state = (float(rows[0][0]), float(rows[1][0]), float(rows[2][0]))
            break

    age = scene.plan.start - element.epoch
    hours = age.total_seconds() / 3600.0
    latitude = f"{state[0]:+.4f}\u00b0" if state else "\u2014"
    longitude = f"{state[1]:+.4f}\u00b0" if state else "\u2014"
    altitude = f"{state[2]:,.1f} km" if state else "\u2014"

    since = next(
        (sat for sat in scene.catalog.satellites.values() if sat.norad_id == selected),
        None,
    )
    groups = ", ".join(
        item.label for item in GROUP_SPECS
        if since and item.id in since.groups
    ) or (spec.label if spec else "\u2014")

    rows_html = "".join(
        f'<div class="ox-row"><span class="ox-k">{label}</span>'
        f'<span class="ox-v">{value}</span></div>'
        for label, value in (
            ("Satellite", html.escape(element.object_name)),
            ("NORAD catalog ID", f"{element.norad_cat_id}"),
            ("Constellation", html.escape(groups)),
            ("Sub-satellite point", f"{latitude}, {longitude}"),
            ("Altitude", altitude),
            ("Inclination", f"{element.inclination:.2f}\u00b0"),
            ("Orbital period", f"{element.period_minutes:.2f} min"),
            ("Element epoch", element.epoch.strftime("%Y-%m-%d %H:%M UTC")),
            ("Element age", f"{hours:+.1f} h"),
        )
    )
    st.markdown(
        f"""
        <div class="ox-card">
          <div class="ox-card-head">
            <span class="kicker">Selected satellite</span>
            <span class="name">{html.escape(element.object_name)}</span>
          </div>
          <div class="ox-rows">{rows_html}</div>
          <div class="ox-card-foot">
            Position at the start of the playback window, Earth-fixed (WGS-84
            geodetic). The globe trail spans one full orbital period.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_error(exc: Exception, offline: bool) -> None:
    """Explain a failed load without pretending there is data."""
    kind = type(exc).__name__
    advice = {
        True: "Offline mode is on: no snapshots cover the requested groups. Turn it "
              "off to fetch them once, or load fewer constellations.",
        False: "CelesTrak could not be reached or returned unusable data. Orbix will "
               "not invent orbital elements, so nothing is drawn. Retry, or switch on "
               "offline mode to use the committed snapshots.",
    }[offline]
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
            scene = load_constellation_cached(
                groups, window_minutes, offline, force_refresh, None
            )
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
    payload = scene.payload(
        enabled_groups=groups,
        selection=selected,
        include_keyframes=include_keyframes,
    )
    payload_bytes = len(str(payload))
    st.session_state["orbix_scene_signature"] = signature

    event = globe_component(payload, height=GLOBE_HEIGHT, key=COMPONENT_KEY)
    if event and event.get("event") == "select":
        norad_id = int(event["norad_id"])
        # The payload above carried the *previous* selection, so re-run once with the
        # new one: that is what brings back the selected satellite's trail. Guarded so
        # a repeated event cannot loop.
        if st.session_state.get("orbix_selected") != norad_id:
            st.session_state["orbix_selected"] = norad_id
            st.session_state["orbix_last_event"] = event
            st.rerun()
        selected = norad_id

    left, right = st.columns([1.15, 1], gap="medium")
    with left:
        render_selected(scene, selected)
    with right:
        render_scene_report(scene, payload_bytes)

    last_event = st.session_state.get("orbix_last_event")
    caption = (
        f"Orbital prediction from CelesTrak elements &middot; playback window "
        f"{scene.plan.start:%H:%M}\u2013{scene.plan.end:%H:%M} UTC, forward from load"
        f" &middot; filtering and playback run in the browser without rerunning "
        f"Streamlit"
    )
    if last_event:
        caption += f" &middot; last event: select {last_event.get('norad_id')}"
    st.markdown(f'<div class="ox-caption">{caption}</div>', unsafe_allow_html=True)

def render_scene_report(scene: ConstellationScene, payload_bytes: int) -> None:
    """What was loaded, how it was sampled, and how accurate that sampling is."""
    status_rows = []
    for status in scene.catalog.groups:
        spec = status.spec
        css = "ok" if status.ok else "bad"
        detail = (
            f"{status.count:,} objects \u00b7 {status.label}"
            if status.ok
            else (status.error or "not loaded")
        )
        status_rows.append(
            f'<div class="ox-row"><span class="ox-k">{html.escape(spec.label)}</span>'
            f'<span class="ox-v {css}">{html.escape(detail)}</span></div>'
        )
    stale_note = (
        f" \u00b7 {len(scene.skipped_stale):,} stale element set(s) skipped"
        if scene.skipped_stale
        else ""
    )
    st.markdown(
        f"""
        <div class="ox-card">
          <div class="ox-card-head">
            <span class="kicker">Loaded data</span>
            <span class="name">{scene.unique_count:,} unique satellites</span>
          </div>
          <div class="ox-rows">{''.join(status_rows)}</div>
          <div class="ox-card-foot">
            De-duplicated by NORAD catalog ID across groups; duplicates are counted
            once and keep every group they belong to.<br />
            {html.escape(scene.plan.describe())}<br />
            Payload to the browser: {payload_bytes / 1024.0:,.0f} KB{stale_note}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
