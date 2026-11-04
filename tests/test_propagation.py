"""Tests for SGP4 orbital propagation.

Hermetic: the element set comes from the genuine CelesTrak ISS payload captured
on 2026-11-04 (``tests/fixtures/iss_gp_25544.json``), and Skyfield runs with its
built-in Earth-orientation tables, so nothing is downloaded.

The numerically interesting checks are done twice: once through Orbix's pipeline
(Skyfield) and once through the ``sgp4`` library plus a from-scratch TEME->ECEF
rotation, so a genuine error cannot hide behind a shared implementation.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.api.celestrak import GpElement, element_from_omm
from src.orbital.propagation import (
    DEFAULT_MAX_ELEMENT_AGE,
    InvalidTimeError,
    OrbitalState,
    PropagationError,
    PropagationFailure,
    StaleElementsError,
    build_satellite,
    get_timescale,
    native_sgp4_state,
    propagate,
    propagate_series,
    utc_now,
)

UTC = timezone.utc
ISS_EPOCH = datetime(2026, 11, 4, 8, 52, 7, 404096, tzinfo=UTC)
ISS_INCLINATION = 51.6329


@pytest.fixture
def iss_element(iss_payload) -> GpElement:
    """The real ISS element set from the committed CelesTrak snapshot."""
    return element_from_omm(iss_payload[0])


def _gmst_degrees(jd_ut1: float) -> float:
    """IAU-82 Greenwich Mean Sidereal Time (Vallado, eq. 3-47), in degrees.

    Implemented here rather than imported so that the ECEF cross-check does not
    depend on any code under test.
    """
    days = jd_ut1 - 2451545.0
    centuries = days / 36525.0
    theta = (
        280.46061837
        + 360.98564736629 * days
        + 0.000387933 * centuries**2
        - centuries**3 / 38710000.0
    )
    return theta % 360.0


def _teme_to_ecef(teme_km, jd_ut1: float):
    """Rotate a TEME vector into ECEF about the z axis by -GMST (independent)."""
    theta = math.radians(_gmst_degrees(jd_ut1))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    x, y, z = teme_km
    return (
        cos_t * x + sin_t * y,
        -sin_t * x + cos_t * y,
        z,
    )


def _wgs84_ecef_to_geodetic(ecef_km, a: float = 6378.137, f: float = 1 / 298.257223563):
    """Iterative WGS-84 ECEF -> (lat_deg, lon_deg, height_km).

    Written from scratch so the geodetic output of the module is checked against
    an independent implementation rather than against itself.
    """
    x, y, z = ecef_km
    e2 = f * (2.0 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - e2))
    height = 0.0
    for _ in range(10):
        sin_lat = math.sin(lat)
        n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        height = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - e2 * n / (n + height)))
    return math.degrees(lat), math.degrees(lon), height


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def test_build_satellite_from_real_omm(iss_element):
    satellite = build_satellite(iss_element)

    assert satellite.name == "ISS (ZARYA)"
    assert satellite.model.satnum == 25544
    # Skyfield echoes the OMM epoch back as a Time object.
    assert satellite.epoch.utc_datetime() == ISS_EPOCH


# --------------------------------------------------------------------------- #
# Physical plausibility at the element epoch
# --------------------------------------------------------------------------- #


def test_propagate_at_epoch_gives_plausible_iss_state(iss_element):
    state = propagate(iss_element, ISS_EPOCH)

    # ISS flies at roughly 400-430 km above the WGS-84 ellipsoid.
    assert 400.0 < state.altitude_km < 430.0
    # Orbital speed of the ISS is about 7.66 km/s.
    assert 7.5 < state.speed_km_s < 7.8
    # Geocentric radius = Earth radius + altitude.
    assert 6700.0 < state.radius_km < 6900.0
    # Inclination comes straight from the elements.
    assert state.inclination_deg == pytest.approx(ISS_INCLINATION)
    # Keplerian period from the mean motion: 1440 / 15.49173979 rev/day.
    assert state.period_minutes == pytest.approx(92.95, abs=0.05)
    # Latitude can never exceed the inclination (geodetic may exceed it by a
    # few tenths of a degree, hence the slack).
    assert abs(state.latitude_deg) <= ISS_INCLINATION + 0.3
    assert -180.0 <= state.longitude_deg <= 180.0


def test_state_exposes_epoch_and_element_age(iss_element):
    state = propagate(iss_element, ISS_EPOCH + timedelta(hours=2))

    assert state.epoch == ISS_EPOCH
    assert state.element_age == timedelta(hours=2)
    assert state.timestamp == ISS_EPOCH + timedelta(hours=2)


def test_mean_radius_matches_the_semi_major_axis_from_mean_motion(iss_element):
    """Tie the element set to the propagated geometry via Kepler's third law.

    ``a = (mu / n^2)^(1/3)`` with the mean motion in rad/s. Sampling one orbit and
    averaging the geocentric radius must recover that ``a``; the small residual
    comes from SGP4 using Kozai mean elements plus short-period terms, not from an
    error in the propagation.
    """
    mu = 398600.4418  # km^3/s^2
    n_rad_s = iss_element.mean_motion * 2.0 * math.pi / 86400.0
    semi_major_km = (mu / n_rad_s**2) ** (1.0 / 3.0)

    states = propagate_series(
        iss_element,
        [ISS_EPOCH + timedelta(seconds=30 * index) for index in range(190)],
    )
    mean_radius = sum(state.radius_km for state in states) / len(states)

    assert semi_major_km == pytest.approx(6797.3, abs=1.0)
    assert mean_radius == pytest.approx(semi_major_km, abs=5.0)


def test_ground_track_respects_inclination_over_a_full_orbit(iss_element):
    times = [ISS_EPOCH + timedelta(minutes=minute) for minute in range(0, 93, 1)]
    states = propagate_series(iss_element, times)

    assert len(states) == len(times)
    max_latitude = max(abs(state.latitude_deg) for state in states)
    # Geodetic latitude slightly exceeds the inclination near the turning points.
    assert ISS_INCLINATION - 0.5 < max_latitude < ISS_INCLINATION + 0.3


# --------------------------------------------------------------------------- #
# Coordinate frames: Earth-fixed vs inertial
# --------------------------------------------------------------------------- #


def test_ecef_and_inertial_vectors_have_identical_length(iss_element):
    """A rotation preserves magnitude - so the two frames must agree on radius."""
    state = propagate(iss_element, ISS_EPOCH)

    ecef_radius = math.dist((0.0, 0.0, 0.0), state.ecef_km)
    gcrs_radius = math.dist((0.0, 0.0, 0.0), state.gcrs_km)

    assert ecef_radius == pytest.approx(gcrs_radius, abs=1e-6)
    assert state.radius_km == pytest.approx(gcrs_radius, abs=1e-6)


def test_ecef_and_inertial_vectors_are_not_the_same_vector(iss_element):
    """If these were interchangeable, the Earth-fixed frame would be useless."""
    state = propagate(iss_element, ISS_EPOCH)

    separation = math.dist(state.ecef_km, state.gcrs_km)
    # At 6793 km radius, TEME/ICRS and ITRS differ by hundreds to thousands of km.
    assert separation > 100.0


def test_geodetic_coordinates_match_an_independent_ecef_conversion(iss_element):
    """Latitude/longitude/altitude must come from the Earth-fixed vector.

    An independent iterative WGS-84 ECEF -> geodetic conversion is applied to
    :attr:`OrbitalState.ecef_km`; if the module had confused ECEF with inertial
    coordinates, the two answers could not agree.
    """
    for offset in (timedelta(0), timedelta(minutes=31), timedelta(minutes=77)):
        state = propagate(iss_element, ISS_EPOCH + offset)
        lat, lon, height = _wgs84_ecef_to_geodetic(state.ecef_km)

        assert lat == pytest.approx(state.latitude_deg, abs=1e-6)
        assert lon == pytest.approx(state.longitude_deg, abs=1e-6)
        assert height == pytest.approx(state.altitude_km, abs=1e-3)


def test_earth_fixed_frame_rotates_with_the_earth(iss_element):
    """The angle between the inertial and Earth-fixed vector advances with GMST.

    Earth-fixed longitude is inertial longitude minus GMST, so the signed angle
    from the inertial vector to the Earth-fixed one decreases by the Earth's
    rotation. Over three hours the Earth turns ~45.1 deg; treating the Earth as
    stationary (an inertial/Earth-fixed mix-up) would give a change of zero.
    """

    def rotation_angle(state: OrbitalState) -> float:
        def xy(vertex):
            return math.atan2(vertex[1], vertex[0])

        return math.degrees(xy(state.ecef_km) - xy(state.gcrs_km))

    start = propagate(iss_element, ISS_EPOCH)
    later = propagate(iss_element, ISS_EPOCH + timedelta(hours=3))

    # Wrap into (-180, 180] so the sign survives.
    delta = (rotation_angle(later) - rotation_angle(start) + 180.0) % 360.0 - 180.0
    sidereal_degrees_per_hour = 15.0410686
    assert delta == pytest.approx(-3 * sidereal_degrees_per_hour, abs=1.0)


def test_orbital_state_rejects_mixed_up_frames():
    """The dataclass refuses an Earth-fixed vector of the wrong length."""
    with pytest.raises(PropagationFailure, match="frame"):
        OrbitalState(
            object_name="ISS (ZARYA)",
            norad_cat_id=25544,
            timestamp=ISS_EPOCH,
            epoch=ISS_EPOCH,
            element_age=timedelta(0),
            latitude_deg=0.0,
            longitude_deg=0.0,
            altitude_km=415.0,
            ecef_km=(1000.0, 1000.0, 1000.0),   # wrong radius on purpose
            gcrs_km=(5635.0, -3793.0, -14.0),
            speed_km_s=7.66,
            radius_km=6793.0,
            period_minutes=92.95,
            inclination_deg=51.63,
        )


# --------------------------------------------------------------------------- #
# Independent cross-check against the sgp4 library
# --------------------------------------------------------------------------- #


def test_independent_sgp4_gives_the_same_radius_and_speed(iss_element):
    """The direct sgp4 path (TEME) must agree with Skyfield's GCRS output.

    Both ingest the same OMM record, so a mismatch here means the element
    ingestion or the propagation is wrong, not merely rotated.
    """
    when = ISS_EPOCH + timedelta(minutes=17)
    state = propagate(iss_element, when)
    native = native_sgp4_state(iss_element, when)

    assert native.norad_cat_id == 25544
    assert native.error_code == 0
    assert native.timestamp == when

    teme_radius = math.dist((0.0, 0.0, 0.0), native.teme_km)
    teme_speed = math.dist((0.0, 0.0, 0.0), native.teme_velocity_km_s)

    # A frame rotation preserves both magnitudes exactly.
    assert teme_radius == pytest.approx(state.radius_km, abs=1e-6)
    assert teme_speed == pytest.approx(state.speed_km_s, abs=1e-6)


def test_from_scratch_teme_to_ecef_rotation_reproduces_the_pipeline(iss_element):
    """Rotate SGP4's TEME vector by -GMST ourselves and compare with ITRS.

    This is the strongest frame check available without another library: the
    position comes from ``sgp4``, the rotation is written out by hand from the
    IAU-82 GMST expression, and only the UT1 instant is shared with Skyfield.
    """
    timescale = get_timescale()
    for offset in (timedelta(0), timedelta(minutes=23), timedelta(minutes=61)):
        when = ISS_EPOCH + offset
        state = propagate(iss_element, when, timescale=timescale)
        native = native_sgp4_state(iss_element, when)

        jd_ut1 = timescale.from_datetime(when).ut1
        independent_ecef = _teme_to_ecef(native.teme_km, jd_ut1)

        # Measured agreement is < 0.001 km; allow a generous kilometre.
        assert math.dist(independent_ecef, state.ecef_km) < 1.0


# --------------------------------------------------------------------------- #
# Time handling
# --------------------------------------------------------------------------- #


def test_timezone_aware_non_utc_input_is_converted(iss_element):
    """A +02:00 timestamp must describe the same instant as its UTC equivalent."""
    instant = ISS_EPOCH + timedelta(hours=3)
    shifted = instant.astimezone(timezone(timedelta(hours=2)))

    assert shifted.hour == 13  # 11:52 UTC expressed at +02:00
    from_utc = propagate(iss_element, instant)
    from_shifted = propagate(iss_element, shifted)

    assert from_shifted.timestamp == from_utc.timestamp
    assert from_shifted.latitude_deg == pytest.approx(from_utc.latitude_deg, abs=1e-9)
    assert from_shifted.longitude_deg == pytest.approx(from_utc.longitude_deg, abs=1e-9)


def test_naive_datetime_is_rejected(iss_element):
    with pytest.raises(InvalidTimeError, match="naive"):
        propagate(iss_element, datetime(2026, 11, 4, 8, 52, 7))


def test_non_datetime_timestamp_is_rejected(iss_element):
    with pytest.raises(InvalidTimeError, match="datetime"):
        propagate(iss_element, "2026-11-04T08:52:07Z")


@pytest.mark.parametrize("year", [1899, 2101])
def test_out_of_range_year_is_rejected(iss_element, year):
    with pytest.raises(InvalidTimeError, match="range"):
        propagate(iss_element, datetime(year, 6, 1, tzinfo=UTC))


def test_none_means_now(iss_element):
    before = utc_now()
    state = propagate(iss_element, None)
    after = utc_now()

    assert before - timedelta(seconds=1) <= state.timestamp <= after + timedelta(seconds=1)
    assert state.timestamp.tzinfo is not None


# --------------------------------------------------------------------------- #
# Stale elements and invalid data
# --------------------------------------------------------------------------- #


def test_stale_elements_beyond_limit_are_refused(iss_element):
    beyond = ISS_EPOCH + DEFAULT_MAX_ELEMENT_AGE + timedelta(days=1)
    with pytest.raises(StaleElementsError, match="beyond"):
        propagate(iss_element, beyond)


def test_elements_between_warning_and_limit_produce_a_warning(iss_element):
    state = propagate(iss_element, ISS_EPOCH + timedelta(days=5))

    assert state.warnings, "expected a staleness warning"
    assert any("3.5 days" in warning for warning in state.warnings)


def test_recent_elements_produce_no_warning(iss_element):
    state = propagate(iss_element, ISS_EPOCH + timedelta(hours=6))
    assert state.warnings == ()


def test_limit_can_be_raised_deliberately(iss_element):
    when = ISS_EPOCH + timedelta(days=60)
    with pytest.raises(StaleElementsError):
        propagate(iss_element, when)

    state = propagate(iss_element, when, max_element_age=None)
    assert state.timestamp == when
    assert state.warnings  # still warns


def test_sgp4_error_message_becomes_propagation_failure(monkeypatch, iss_element):
    """A non-None Skyfield message must raise, never yield a position."""

    class _BoomGeocentric:
        message = "mean eccentricity is outside the range 0.0 to 1.0"
        xyz = None
        velocity = None

        def frame_xyz(self, frame):  # pragma: no cover - must not be reached
            raise AssertionError("frame conversion must not run after an SGP4 error")

    class _BoomSatellite:
        def at(self, t):
            return _BoomGeocentric()

    monkeypatch.setattr(
        "src.orbital.propagation.build_satellite", lambda *a, **k: _BoomSatellite()
    )

    with pytest.raises(PropagationFailure, match="could not propagate"):
        propagate(iss_element, ISS_EPOCH)


def test_non_finite_position_becomes_propagation_failure(monkeypatch, iss_element):
    class _NanGeocentric:
        message = None
        xyz = type("Xyz", (), {"km": np.array([np.nan, np.nan, np.nan])})()

        def frame_xyz(self, frame):  # pragma: no cover - must not be reached
            raise AssertionError("frame conversion must not run with NaN input")

    class _NanSatellite:
        def at(self, t):
            return _NanGeocentric()

    monkeypatch.setattr(
        "src.orbital.propagation.build_satellite", lambda *a, **k: _NanSatellite()
    )

    with pytest.raises(PropagationFailure, match="finite"):
        propagate(iss_element, ISS_EPOCH)


def test_corrupt_element_set_is_rejected_at_ingestion(iss_payload):
    """Elements the API layer would never emit must not silently propagate."""
    record = dict(iss_payload[0])
    record["EPOCH"] = "not-a-timestamp"
    with pytest.raises(Exception):
        # element_from_omm is the gate that rejects it before propagation.
        element_from_omm(record)


# --------------------------------------------------------------------------- #
# Series, determinism and serialisation
# --------------------------------------------------------------------------- #


def test_positions_change_meaningfully_as_time_advances(iss_element):
    start = propagate(iss_element, ISS_EPOCH)
    one_minute = propagate(iss_element, ISS_EPOCH + timedelta(minutes=1))

    moved = math.dist(start.gcrs_km, one_minute.gcrs_km)
    # At ~7.66 km/s the satellite covers ~460 km in a minute.
    assert 400.0 < moved < 520.0
    # ... and its ground position must change too.
    assert abs(one_minute.latitude_deg - start.latitude_deg) > 0.01


def test_propagate_series_matches_individual_calls(iss_element):
    times = [ISS_EPOCH + timedelta(minutes=m) for m in (0, 10, 20)]
    series = propagate_series(iss_element, times)

    for when, state in zip(times, series):
        single = propagate(iss_element, when)
        assert state.gcrs_km == pytest.approx(single.gcrs_km, abs=1e-9)
        assert state.ecef_km == pytest.approx(single.ecef_km, abs=1e-9)
        assert state.speed_km_s == pytest.approx(single.speed_km_s, abs=1e-12)


def test_propagation_is_deterministic(iss_element):
    first = propagate(iss_element, ISS_EPOCH)
    second = propagate(iss_element, ISS_EPOCH)
    assert first == second


def test_state_is_json_serialisable(iss_element):
    state = propagate(iss_element, ISS_EPOCH)
    payload = json.dumps(state.as_dict())

    assert '"norad_cat_id": 25544' in payload
    assert "ecef_km" in payload and "gcrs_km" in payload


def test_propagation_error_types_share_a_base(iss_element):
    for error in (InvalidTimeError, StaleElementsError, PropagationFailure):
        assert issubclass(error, PropagationError)




