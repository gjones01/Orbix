"""Information panels for the Orbix UI.

Extracted from ``app.py`` so the entry point stays orchestration plus app shell, and so
these renderers can be read (and later tested) on their own. Everything here is
presentation only: the numbers arrive already computed by ``src/ui/presentation.py``
and ``src/analysis``.
"""

from __future__ import annotations

import html

import streamlit as st

from src.api.catalog import GROUP_SPECS
from src.ui.presentation import ConstellationScene

__all__ = ["render_error", "render_scene_report", "render_selected"]


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
            ("Location", f"{latitude}, {longitude}"),
            ("Altitude", altitude),
            ("Orbit tilt", f"{element.inclination:.2f}\u00b0"),
            ("Time around Earth", f"{element.period_minutes:.2f} min"),
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
            Estimated position at the start of playback. The trail shows one trip around Earth.
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


def render_scene_report(scene: ConstellationScene, payload_bytes: int) -> None:
    """What was loaded, how it was sampled, and how accurate that sampling is."""
    status_rows = []
    for status in scene.catalog.groups:
        spec = status.spec
        css = "ok" if status.ok else "bad"
        detail = (
            f"{status.records_fetched:,} objects \u00b7 {status.label}"
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
