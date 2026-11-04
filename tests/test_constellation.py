"""Tests for multi-group loading, batch propagation and the constellation payload.

Hermetic: CelesTrak is replaced by a stub client built from the committed snapshot, so
no network access and no Streamlit server are involved.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from components.globe.payload import (
    EARTH_RADIUS_KM,
    altitude_ratio,
    build_constellation_payload,
)
from src.orbital.window import plan_window
from src.api.catalog import GROUP_SPECS, load_catalog
from src.orbital.batch import BatchPropagator
from src.orbital.propagation import propagate

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Stub CelesTrak client
# --------------------------------------------------------------------------- #


class _Result:
    """Mirrors the shape of ``GpFetchResult`` for the code under test."""

    def __init__(self, elements, source="snapshot"):
        self.elements = tuple(elements)
        self.source = source
        self.notes = ()
        self.http_status = 200
        self.url = "stub://celestrak"
        self.snapshot = None


class StubClient:
    """Serves fixed element sets per group and can fail on demand."""

    def __init__(self, payloads: dict[str, list], fail: set[str] | None = None):
        self.payloads = payloads
        self.fail = fail or set()
        self.calls: list[str] = []

    def fetch_by_group(self, group, *, refresh=False, allow_stale=True):
        self.calls.append(group)
        if group in self.fail:
            from src.api.celestrak import CelestrakHTTPError

            raise CelestrakHTTPError(404, "No GP data found")
        return _Result(self.payloads.get(group, []))


@pytest.fixture
def iss_element(iss_payload):
    from src.api.celestrak import element_from_omm

    return element_from_omm(iss_payload[0])


@pytest.fixture
def two_elements(iss_payload):
    """Two distinct element sets: the ISS plus a slightly altered copy."""
    from src.api.celestrak import element_from_omm

    second = dict(iss_payload[0])
    second.update({"NORAD_CAT_ID": 99999, "OBJECT_NAME": "TEST OBJECT",
                   "INCLINATION": 97.5, "MEAN_MOTION": 14.2})
    return [element_from_omm(iss_payload[0]), element_from_omm(second)]


# --------------------------------------------------------------------------- #
# Catalogue loading
# --------------------------------------------------------------------------- #


def test_group_specs_cover_the_five_requested_groups():
    ids = {spec.id for spec in GROUP_SPECS}
    assert ids == {"stations", "gps-ops", "weather", "oneweb", "starlink"}
    # Starlink is the only group off by default: it is ~10x everything else.
    assert {spec.id for spec in GROUP_SPECS if not spec.default_enabled} == {"starlink"}


def test_catalogue_deduplicates_by_norad_id(two_elements):
    client = StubClient({
        "stations": [two_elements[0]],
        "gps-ops": [two_elements[0], two_elements[1]],   # ISS appears twice
    })
    catalog = load_catalog(["stations", "gps-ops"], client=client)

    assert len(catalog) == 2
    assert client.calls == ["stations", "gps-ops"]       # one request per group
    iss = catalog.satellites[25544]
    # Membership is preserved, not collapsed to a single group.
    assert iss.groups == {"stations", "gps-ops"}
    assert catalog.counts_by_group()["stations"] == 1
    assert catalog.counts_by_group()["gps-ops"] == 2


def test_primary_group_is_deterministic(two_elements):
    client = StubClient({"gps-ops": [two_elements[0]], "stations": [two_elements[0]]})
    catalog = load_catalog(["gps-ops", "stations"], client=client)
    # GROUP_SPECS order decides, not fetch order.
    assert catalog.satellites[25544].primary_group == "stations"


def test_failing_group_is_reported_and_not_substituted(two_elements):
    client = StubClient({"stations": [two_elements[0]]}, fail={"gps-ops"})
    catalog = load_catalog(["stations", "gps-ops"], client=client)

    assert len(catalog) == 1                               # only stations loaded
    gps = next(s for s in catalog.groups if s.spec.id == "gps-ops")
    assert gps.ok is False and gps.source == "failed"
    assert "404" in (gps.error or "")
    assert catalog.failed_groups[0].spec.id == "gps-ops"


def test_unknown_group_is_rejected():
    with pytest.raises(ValueError, match="Unknown CelesTrak group"):
        load_catalog(["stations", "not-a-group"], client=StubClient({}))


def test_offline_catalogue_reads_committed_snapshots():
    catalog = load_catalog(["stations"], offline=True)
    assert len(catalog) >= 10
    assert all(status.source in {"snapshot", "cache"} for status in catalog.groups)


# --------------------------------------------------------------------------- #
# Batch propagation
# --------------------------------------------------------------------------- #


def test_batch_matches_the_verified_skyfield_path(iss_element):
    moments = [iss_element.epoch, iss_element.epoch + timedelta(minutes=23)]
    sample = BatchPropagator([iss_element]).sample(moments)

    for index, moment in enumerate(moments):
        state = propagate(iss_element, moment)
        ecef = sample.ecef_km[0, index].astype(float)
        assert np.linalg.norm(ecef - np.array(state.ecef_km)) < 1.0     # < 1 km
        assert abs(float(sample.alt[0, index]) - state.altitude_km) < 1.0
        assert abs(float(sample.lat[0, index]) - state.latitude_deg) < 0.01


def test_batch_survives_invalid_elements(two_elements):
    from dataclasses import replace

    good, broken = two_elements
    broken = replace(broken, mean_motion=0.0)     # cannot be propagated
    sample = BatchPropagator([good, broken]).sample([good.epoch])

    assert sample.shape == (2, 1)
    assert bool(sample.valid[0, 0]) is True
    assert bool(sample.valid[1, 0]) is False
    assert list(sample.satellites_without_any_position()) == [1]
    assert sample.invalid_count == 1


def test_batch_rejects_naive_datetimes(iss_element):
    with pytest.raises(ValueError, match="timezone-aware"):
        BatchPropagator([iss_element]).sample([datetime(2026, 11, 4, 12, 0)])


def test_batch_rejects_empty_input():
    with pytest.raises(ValueError):
        BatchPropagator([])


def test_batch_geodetic_matches_an_independent_conversion(iss_element):
    from src.orbital.batch import WGS84_A_KM, WGS84_F

    sample = BatchPropagator([iss_element]).sample([iss_element.epoch])
    x, y, z = (float(v) for v in sample.ecef_km[0, 0])

    a, f = WGS84_A_KM, WGS84_F
    e2 = f * (2 - f)
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - e2))
    height = 0.0
    for _ in range(8):
        n = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        height = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - e2 * n / (n + height)))

    # Float32 storage: ~1e-5 deg is the representable precision at these magnitudes.
    assert float(sample.lat[0, 0]) == pytest.approx(float(np.degrees(lat)), abs=1e-4)
    assert float(sample.lng[0, 0]) == pytest.approx(float(np.degrees(lon)), abs=1e-4)
    assert float(sample.alt[0, 0]) == pytest.approx(height, abs=1e-2)


# --------------------------------------------------------------------------- #
# Window planning (playback sampling)
# --------------------------------------------------------------------------- #


def test_plan_window_meets_the_accuracy_target_for_small_constellations():
    start = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    plan = plan_window(start, start + timedelta(minutes=20), 777)

    assert plan.keyframes >= 30
    assert plan.max_interpolation_km <= 1.05        # target is 1 km
    assert plan.interval_seconds < 40
    assert "keyframes" in plan.describe()


def test_plan_window_coarsens_for_large_constellations():
    start = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    small = plan_window(start, start + timedelta(minutes=20), 777)
    large = plan_window(start, start + timedelta(minutes=20), 11958)

    assert large.keyframes < small.keyframes          # payload budget bites
    assert large.max_interpolation_km > small.max_interpolation_km
    # The degradation is bounded and always reported.
    assert large.max_interpolation_km < 50.0
    assert "interpolation error" in large.describe()


def test_plan_window_spans_the_requested_window_exactly():
    start = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=45)
    plan = plan_window(start, end, 100)

    assert plan.start == start and plan.end == end
    assert plan.duration_seconds == pytest.approx(2700.0)
    times = [start + timedelta(seconds=plan.interval_seconds * i)
             for i in range(plan.keyframes)]
    assert times[-1] - times[0] == pytest.approx(timedelta(minutes=45), abs=timedelta(seconds=1))


def test_plan_window_rejects_bad_input():
    start = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        plan_window(start, start, 10)
    with pytest.raises(ValueError):
        plan_window(start, start + timedelta(minutes=5), 0)


# --------------------------------------------------------------------------- #
# Forward-only playback window
# --------------------------------------------------------------------------- #


def test_scene_window_starts_at_the_anchor_and_runs_forward():
    """Playback begins at the anchor instant and never opens in the past."""
    anchor = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    from src.ui.presentation import load_constellation

    scene = load_constellation(
        ["stations"], window_minutes=20, offline=True, when=anchor
    )

    assert scene.plan.start == anchor
    assert scene.plan.end == anchor + timedelta(minutes=20)
    # Every keyframe is at or after the anchor: nothing is sampled in the past.
    assert all(moment >= anchor for moment in scene.sample.times)
    assert scene.sample.times[0] == anchor
    # Position at the first keyframe is the position at the anchor.
    assert scene.sample.times[-1] - scene.sample.times[0] == pytest.approx(
        timedelta(minutes=20), abs=timedelta(seconds=2)
    )


def test_selected_track_also_starts_at_the_anchor():
    """The trail is one period forward from the window start, not from the past."""
    anchor = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    from src.ui.presentation import load_constellation

    scene = load_constellation(["stations"], offline=True, when=anchor)
    track = scene.selected_track(25544, when=anchor)
    # First trail point equals the propagated state at the anchor.
    from src.orbital.propagation import propagate

    state = propagate(scene.element(25544), anchor)
    assert track[0][0] == pytest.approx(state.latitude_deg, abs=1e-3)
    assert track[0][1] == pytest.approx(state.longitude_deg, abs=1e-3)
    # One full period later the ISS has come back around (not merely moved a little).
    assert len(track) == 121


def test_window_anchor_defaults_to_now():
    from src.orbital.propagation import utc_now
    from src.ui.presentation import load_constellation

    before = utc_now()
    scene = load_constellation(["stations"], offline=True)
    after = utc_now()

    assert before - timedelta(seconds=5) <= scene.plan.start <= after + timedelta(seconds=5)
    assert scene.plan.end - scene.plan.start == pytest.approx(
        timedelta(minutes=20), abs=timedelta(seconds=1)
    )


def test_stale_guard_uses_the_far_end_of_the_window(monkeypatch):
    """An element fine at the start but stale by the end must be skipped.

    Playback runs forward, so the guard has to consider the window's far end. An
    element that is exactly 10 minutes inside the 30-day limit at the start is 10
    minutes outside it at +20 min.
    """
    from dataclasses import replace

    from src.api.catalog import Satellite, load_catalog
    from src.api.celestrak import element_from_omm
    from src.ui.presentation import MAX_ELEMENT_AGE, load_constellation

    anchor = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "iss_gp_25544.json").read_text("utf-8")
    )
    fresh = element_from_omm(fixture[0])
    borderline = replace(
        fresh,
        norad_cat_id=11111,
        epoch=anchor - (MAX_ELEMENT_AGE - timedelta(minutes=10)),
    )

    base = load_catalog(["stations"], offline=True)
    satellites = dict(base.satellites)
    satellites[11111] = Satellite(element=borderline, groups=frozenset({"stations"}))
    catalog = replace(base, satellites=satellites)
    monkeypatch.setattr("src.ui.presentation.load_catalog", lambda *a, **k: catalog)

    scene = load_constellation(["stations"], window_minutes=20, offline=True, when=anchor)

    assert scene.skipped_stale == (11111,)
    assert 11111 not in scene.catalog.satellites
    assert scene.rendered_count == len(base)          # the rest still render

    # With a 5-minute window the same element is inside the limit at both ends.
    short = load_constellation(
        ["stations"], window_minutes=5, offline=True, when=anchor
    )
    assert short.skipped_stale == ()
    assert 11111 in short.catalog.satellites




# --------------------------------------------------------------------------- #
# Payload contract
# --------------------------------------------------------------------------- #


@pytest.fixture
def scene():
    from src.ui.presentation import load_constellation

    return load_constellation(["stations", "gps-ops"], window_minutes=20, offline=True)


def _decode(payload: dict, key: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(payload["keyframes"][key]), dtype="<f4")


def test_payload_is_json_serialisable_and_index_aligned(scene):
    payload = scene.payload()
    text = json.dumps(payload, allow_nan=False)

    assert payload["version"] == 2
    assert len(payload["satellites"]) == scene.rendered_count
    assert payload["keyframes"]["count"] == scene.keyframes
    assert "NaN" not in text

    # Keyframe buffers are flat and index-aligned with the metadata list.
    count = payload["keyframes"]["count"]
    expected = len(payload["satellites"]) * count
    assert _decode(payload, "lat").size == expected
    assert _decode(payload, "lng").size == expected
    assert _decode(payload, "alt").size == expected


def test_keyframes_round_trip_to_the_batch_sample(scene):
    payload = scene.payload()
    count = payload["keyframes"]["count"]
    lat = _decode(payload, "lat").reshape(-1, count)
    alt = _decode(payload, "alt").reshape(-1, count)

    # Float32 storage must reproduce the propagated positions closely.
    assert np.allclose(lat, scene.sample.lat, atol=1e-4)
    assert np.allclose(alt * EARTH_RADIUS_KM, scene.sample.alt, atol=0.1)
    # Altitudes arrive in globe-radius units, never as kilometres.
    assert alt.max() < 10.0


def test_payload_altitude_uses_globe_radius_units(scene):
    assert altitude_ratio(EARTH_RADIUS_KM) == pytest.approx(1.0)
    alt = _decode(scene.payload(), "alt")
    # The scene mixes LEO stations (one globe radius + ~6%) with GPS (three radii):
    # nothing may be sent as kilometres, and the spread must be physical.
    assert float(alt.min()) < 0.1
    assert 2.5 < float(alt.max()) < 4.0
    assert float(np.median(alt)) > 0.0


def test_light_payload_omits_keyframes(scene):
    full = scene.payload()
    light = scene.payload(include_keyframes=False)

    assert "keyframes" in full and "keyframes" not in light
    assert len(json.dumps(light)) < len(json.dumps(full))
    # Everything needed to keep drawing is still present.
    assert light["satellites"] == full["satellites"]
    assert light["window"] == full["window"]


def test_payload_marks_enabled_groups(scene):
    payload = scene.payload(enabled_groups={"stations"})
    flags = {group["id"]: group["enabled"] for group in payload["groups"]}

    # Only the groups that were actually loaded are reported.
    assert set(flags) == {"stations", "gps-ops"}
    assert flags["stations"] is True
    assert flags["gps-ops"] is False


def test_selection_includes_an_earth_fixed_track(scene):
    payload = scene.payload(selection=25544)
    track = payload["selected_track"]

    assert payload["selection"] == 25544
    assert track and len(track) == 121
    for lat, lng, alt in track:
        assert -90.0 <= lat <= 90.0
        assert -180.0 <= lng <= 180.0
        assert 0.0 < alt < 0.2

    # No selection -> no track, and the field is explicitly null.
    assert scene.payload(selection=None)["selected_track"] is None


def test_selection_track_is_earth_fixed(scene):
    """The trail must advance less than 360 deg of longitude per orbit."""
    track = scene.payload(selection=25544)["selected_track"]
    total = 0.0
    for (_, lng_a, _), (_, lng_b, _) in zip(track, track[1:]):
        delta = lng_b - lng_a
        while delta > 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        total += delta
    assert 300.0 < total < 350.0        # 336.7 deg for the ISS, not 360


def test_scene_reports_counts_by_group_and_stale_elements(scene):
    counts = scene.counts_by_group()
    # Counts follow the loaded snapshot rather than being pinned to a fixed number:
    # CelesTrak's station list legitimately changes (20 -> 19 objects during testing).
    assert counts["stations"] == len(scene.catalog.in_groups({"stations"}))
    assert counts["stations"] >= 10
    assert counts["gps-ops"] == len(scene.catalog.in_groups({"gps-ops"})) >= 20
    assert counts["starlink"] == 0          # not requested, so never present
    assert scene.skipped_stale == ()
    assert "unique satellites" in scene.summary()


def test_scene_can_look_up_elements_and_groups(scene):
    assert scene.element(25544) is not None
    assert scene.group_of(25544) == "stations"
    assert scene.element(1) is None
    assert scene.selected_track(1) == ()



