"""Tests for the Python side of the 3D globe (Step D).

Covers the three things that can silently go wrong between Python and the browser:

1. the payload contract and its unit conversions (altitude -> globe radii),
2. the information-panel facts shown next to the globe,
3. the presence and integrity of the component's static front-end.

The tests are hermetic: the scene is built from the committed CelesTrak snapshot in
offline mode, so no network access and no Streamlit server are involved.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from components.globe import FRONTEND_DIR
from components.globe.payload import (
    EARTH_RADIUS_KM,
    PAYLOAD_VERSION,
    altitude_ratio,
    build_scene_payload,
    short_name,
)
from src.ui.presentation import (
    Scene,
    format_age,
    format_utc_compact,
    load_scene,
)

UTC = timezone.utc


@pytest.fixture
def scene() -> Scene:
    """A real ISS scene from the committed snapshot (no network)."""
    return load_scene(offline=True, track_samples=24)


@pytest.fixture
def payload(scene: Scene) -> dict:
    return build_scene_payload(satellite=scene.state, track=scene.track, height=700)


# --------------------------------------------------------------------------- #
# Unit conversion: physical altitude -> Globe.gl globe-radius units
# --------------------------------------------------------------------------- #


def test_altitude_ratio_is_km_over_mean_earth_radius():
    assert altitude_ratio(6371.0088) == pytest.approx(1.0)
    assert altitude_ratio(0.0) == 0.0
    # The ISS flies at about 400-430 km, i.e. 6.3-6.7% of a globe radius.
    assert 0.062 < altitude_ratio(400.0) < 0.068


def test_payload_altitude_ratio_matches_the_physical_altitude(payload):
    satellite = payload["satellite"]

    assert satellite["altitude_ratio"] == pytest.approx(
        satellite["altitude_km"] / EARTH_RADIUS_KM
    )
    # Converting back must recover the physical altitude used for the readout.
    assert satellite["altitude_ratio"] * EARTH_RADIUS_KM == pytest.approx(
        satellite["altitude_km"], abs=1e-9
    )
    assert 0.055 < satellite["altitude_ratio"] < 0.075


def test_ground_altitude_ratio_is_the_surface():
    # The leader line must start exactly on the surface, not at some guessed height.
    assert build_scene_payload(
        satellite=load_scene(offline=True, track_samples=2).state
    )["satellite"]["ground_altitude_ratio"] == 0.0


# --------------------------------------------------------------------------- #
# Payload contract
# --------------------------------------------------------------------------- #


def test_payload_shape_is_stable(payload, scene):
    assert payload["version"] == PAYLOAD_VERSION
    assert set(payload) == {"version", "satellite", "track", "meta", "height"}
    assert payload["height"] == 700

    satellite = payload["satellite"]
    assert satellite["name"] == scene.object_name
    assert satellite["norad_id"] == 25544
    assert satellite["short_name"] == "ISS"
    assert satellite["lat"] == pytest.approx(scene.state.latitude_deg)
    assert satellite["lon"] == pytest.approx(scene.state.longitude_deg)
    # Marker radius is an angular size in degrees (Globe.gl pointRadius units).
    assert 0.2 <= satellite["marker_radius"] <= 3.0

    # The renderer indexes track points positionally as [lat, lon, alt].
    for point in payload["track"]["points"]:
        assert len(point) == 3
        assert -90.0 <= point[0] <= 90.0
        assert -180.0 <= point[1] <= 180.0
        assert 0.0 < point[2] < 0.2


def test_payload_is_json_serialisable_without_nan(payload):
    text = json.dumps(payload, allow_nan=False)  # raises if any NaN/Inf slipped in
    assert "NaN" not in text
    assert json.loads(text)["satellite"]["norad_id"] == 25544


def test_track_covers_one_orbital_period(payload, scene):
    points = payload["track"]["points"]

    assert payload["track"]["sample_count"] == len(points) == 25  # 24 samples + 1
    assert payload["track"]["period_minutes"] == pytest.approx(
        scene.state.period_minutes
    )
    # The sampled span is one period: the last point is ~one period after the first.
    assert payload["track"]["period_minutes"] == pytest.approx(92.95, abs=0.1)


def test_track_starts_at_the_satellite(payload):
    first = payload["track"]["points"][0]

    assert first[0] == pytest.approx(payload["satellite"]["lat"])
    assert first[1] == pytest.approx(payload["satellite"]["lon"])
    assert first[2] == pytest.approx(payload["satellite"]["altitude_ratio"])


def test_track_is_earth_fixed_not_inertial(payload):
    """An Earth-fixed track falls short of 360 deg by the Earth's rotation.

    Over one 92.95-minute orbit the Earth turns ~23.3 deg, so the unwrapped
    longitude advance must be ~336.7 deg. An inertial track would total ~360 deg -
    that difference is the test.
    """
    longitudes = [point[1] for point in payload["track"]["points"]]
    total = 0.0
    for previous, current in zip(longitudes, longitudes[1:]):
        delta = current - previous
        while delta > 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        total += delta

    period_minutes = payload["track"]["period_minutes"]
    expected = 360.0 - 360.0 * (period_minutes / 1436.068)
    assert total == pytest.approx(expected, abs=2.0)
    assert total < 350.0, "track looks inertial (no Earth rotation applied)"


def test_build_payload_rejects_non_finite_positions(scene):
    """A NaN altitude must raise rather than render a phantom satellite."""
    broken = dataclasses.replace(scene.state, altitude_km=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        build_scene_payload(satellite=broken, track=())


# --------------------------------------------------------------------------- #
# Information panel
# --------------------------------------------------------------------------- #


def test_panel_shows_every_required_field(scene):
    labels = [fact.label for fact in scene.facts()]

    for required in (
        "Satellite",
        "NORAD catalog ID",
        "Altitude",
        "Velocity",
        "Inclination",
        "Orbital period",
        "Simulation time",
        "Element age",
    ):
        assert required in labels, f"missing required field {required!r}"


def test_panel_values_are_formatted_for_humans(scene):
    facts = {fact.label: fact.value for fact in scene.facts()}

    assert facts["Satellite"] == "ISS (ZARYA)"
    assert facts["NORAD catalog ID"] == "25544"
    assert facts["Altitude"].endswith(" km")
    assert 380.0 < float(facts["Altitude"].split()[0].replace(",", "")) < 450.0
    assert facts["Velocity"].endswith(" km/s")
    assert 7.4 < float(facts["Velocity"].split()[0]) < 7.9
    assert facts["Inclination"].endswith("\u00b0")
    # The panel rounds to 2 decimals; the element value is 51.6329.
    assert float(facts["Inclination"].rstrip("\u00b0")) == pytest.approx(51.63, abs=0.005)
    assert facts["Orbital period"].endswith(" min")
    assert float(facts["Orbital period"].split()[0]) == pytest.approx(92.95, abs=0.05)
    # Compact UTC stamp: sortable, unambiguous, no wrapping in a narrow column.
    assert facts["Simulation time"].endswith("Z")
    assert len(facts["Simulation time"]) == 20
    assert facts["Element age"][0] in "+-"


def test_subpoint_fact_uses_geodetic_degrees(scene):
    fact = scene.subpoint_fact()

    assert fact.label == "Sub-satellite point"
    assert fact.hint == "WGS-84 geodetic"
    latitude, longitude = fact.value.split(", ")
    assert -90.0 <= float(latitude.rstrip("\u00b0")) <= 90.0
    assert -180.0 <= float(longitude.rstrip("\u00b0")) <= 180.0


def test_source_labels_are_explicit():
    scene = load_scene(offline=True, track_samples=2)

    # Reading a committed snapshot is not a live download, and must not claim to be.
    assert scene.source == "snapshot"
    assert scene.is_live is False
    assert scene.source_label == "SNAPSHOT"
    assert "snapshot" in scene.source_detail.lower()


def test_format_age_scales_units():
    assert format_age(timedelta(minutes=30)) == "+30 min"
    assert format_age(timedelta(hours=11, minutes=18)) == "+11.3 h"
    assert format_age(-timedelta(hours=2)) == "-2.0 h"
    assert format_age(timedelta(days=4)) == "+4.0 d"


def test_format_utc_compact_is_iso_like():
    stamp = datetime(2026, 11, 4, 20, 27, 11, tzinfo=timezone.utc)
    assert format_utc_compact(stamp) == "2026-11-04 20:27:11Z"


def test_short_name_truncates_long_labels():
    assert short_name("ISS (ZARYA)") == "ISS"
    assert short_name("CSS (TIANHE)") == "CSS"
    assert len(short_name("A VERY LONG SATELLITE NAME INDEED")) <= 18


# --------------------------------------------------------------------------- #
# Front-end assets (the component would be a blank box without these)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "relative_path",
    [
        "index.html",
        "orbix-globe.js",
        "style.css",
        "assets/earth-blue-marble.jpg",
        "assets/earth-topology.png",
        "vendor/globe.gl.min.js",
    ],
)
def test_frontend_asset_exists_and_is_not_empty(relative_path):
    path = FRONTEND_DIR / relative_path
    assert path.is_file(), f"missing front-end asset: {relative_path}"
    assert path.stat().st_size > 1024, f"front-end asset looks truncated: {relative_path}"


def test_frontend_html_references_only_local_assets():
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    # No CDN dependency: the globe must work on a network that blocks unpkg.
    assert "http://" not in html and "https://" not in html
    assert "./orbix-globe.js" in html
    assert "./vendor/globe.gl.min.js" in html


def test_frontend_js_contains_the_streamlit_bridge():
    js = (FRONTEND_DIR / "orbix-globe.js").read_text(encoding="utf-8")

    for message in (
        "streamlit:componentReady",
        "streamlit:setFrameHeight",
        "streamlit:setComponentValue",
        "streamlit:render",
    ):
        assert message in js, f"component protocol message missing: {message}"


def test_frontend_js_has_no_orbital_mechanics():
    """All maths belongs in Python; the renderer must only consume payload values."""
    js = (FRONTEND_DIR / "orbix-globe.js").read_text(encoding="utf-8")

    for forbidden in ("Math.sin", "Math.cos", "6371", "Math.atan2", "radians"):
        assert forbidden not in js, f"orbital/geodetic maths leaked into JS: {forbidden}"

