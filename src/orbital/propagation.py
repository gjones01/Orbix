"""SGP4 orbital propagation for Orbix.

This module is the only place where orbital *elements* become *positions*. It
takes a validated :class:`~src.api.celestrak.GpElement` (a CelesTrak OMM record)
and returns a satellite state at a requested UTC instant, in the three
coordinate representations the application needs.

Coordinate systems - read this before using the numbers
-------------------------------------------------------
SGP4 is not a Kepler solver, and the frames it works in are not the frames a
globe renders in. Mixing them up is the classic way to produce a satellite that
looks plausible but sits in the wrong place, so every output is labelled:

===================  =====================================================  =========
Name                 Definition                                             Rotates
===================  =====================================================  =========
**TEME**             True Equator, Mean Equinox. The frame SGP4 natively   No
                     produces ("of date" true equator, mean equinox).
                     Native output of the ``sgp4`` library.
**GCRS / ICRS**      Geocentric Celestial Reference System, axes fixed to   No
                     the distant quasars (≈ J2000). Skyfield converts
                     SGP4's TEME output into GCRS with a full IAU-2000
                     rotation chain (precession, nutation, equation of
                     the equinoxes). This is Orbix's *inertial* output.
**ITRS / ECEF**      International Terrestrial Reference System:           **Yes**
                     Earth-Centred, Earth-Fixed. ``x`` points at 0° longitude
                     on the equator, ``y`` at 90°E, ``z`` at the north pole;
                     the frame rotates with the Earth.
**WGS-84 geodetic**  Latitude/longitude/height on the WGS-84 ellipsoid      (surface)
                     (``a`` = 6378.137 km, ``f`` = 1/298.257223563), the
                     datum used by GPS and by most WebGL globes.
===================  =====================================================  =========

Consequences for the application:

* **Three.js renders an Earth-fixed globe.** Place satellites with
  :attr:`OrbitalState.ecef_km`, never with the inertial vector, or every
  satellite will appear to slide westwards as the Earth rotates underneath it.
* Latitude/longitude/altitude come from WGS-84 geodetic conversion of the
  geocentric position, i.e. altitude is measured above the *ellipsoid*, not
  above mean sea level and not above a sphere. At 52° latitude the ellipsoid
  differs from a sphere by about 10 km, which matters.
* The inertial (:attr:`~OrbitalState.gcrs_km`) and Earth-fixed
  (:attr:`~OrbitalState.ecef_km`) vectors have the **same length** - a rotation
  preserves magnitude - and the tests assert exactly that, because a frame
  mix-up is otherwise easy to miss.

Time scales
-----------
Element epochs and requested instants are timezone-aware UTC ``datetime``
objects. Internally Skyfield converts UTC to UT1/TT using its own tables; by
default Orbix uses Skyfield's *built-in* Earth-orientation tables, which needs no
network access and, for the ISS, differs from the full IERS ``finals2000A.all``
data by about 70 m and 0.0006° of longitude (measured, see
``docs/technical_decisions.md``). Set ``use_iers_earth_orientation=True`` on
:func:`get_timescale` for the higher-precision path; it downloads the IERS file
into ``data/cache/skyfield`` once.

Element age and staleness
-------------------------
SGP4 accuracy degrades away from the element epoch. CelesTrak itself flags
element sets older than 3.5 days, so Orbix emits a warning past that point and
refuses to propagate beyond :data:`DEFAULT_MAX_ELEMENT_AGE` (30 days) unless the
caller raises that limit explicitly.

Typical use::

    from src.api.celestrak import CelestrakClient
    from src.orbital.propagation import propagate

    result = CelestrakClient().fetch_by_catalog_number(25544)
    state = propagate(result.elements[0])
    print(state.latitude_deg, state.longitude_deg, state.altitude_km)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from skyfield.api import EarthSatellite, Loader, Timescale, load, wgs84
from skyfield.framelib import itrs
from sgp4.api import Satrec, jday

from src.api.celestrak import GpElement, STALE_ELEMENT_AGE

try:  # sgp4 >= 2.23 exposes the official OMM -> Satrec initialiser
    from sgp4 import omm as sgp4_omm
except ImportError:  # pragma: no cover - sgp4 is a hard dependency of Skyfield
    sgp4_omm = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_ELEMENT_AGE",
    "InvalidTimeError",
    "OrbitalState",
    "PropagationError",
    "PropagationFailure",
    "Sgp4NativeState",
    "SkyfieldDataError",
    "StaleElementsError",
    "build_satellite",
    "get_timescale",
    "native_sgp4_state",
    "propagate",
    "propagate_series",
    "utc_now",
]

_UTC = timezone.utc
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Where Skyfield may cache downloaded IERS data (only used when the built-in
#: Earth-orientation tables are switched off). Git-ignored.
SKYFIELD_DATA_DIR = _PROJECT_ROOT / "data" / "cache" / "skyfield"

#: Refuse to propagate this far from the element epoch. SGP4 remains numerically
#: stable for years, but the *accuracy* is long gone, so returning a position
#: without complaint would be misleading.
DEFAULT_MAX_ELEMENT_AGE = timedelta(days=30)

#: SGP4 is defined for this era; anything outside it is a caller error.
_MIN_YEAR = 1900
_MAX_YEAR = 2100


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class PropagationError(RuntimeError):
    """Base class for every recoverable propagation failure."""


class InvalidTimeError(PropagationError):
    """The requested timestamp is not a usable aware UTC datetime."""


class StaleElementsError(PropagationError):
    """The element set is too far in time from the requested instant."""


class PropagationFailure(PropagationError):
    """SGP4 refused to produce a position, or produced a non-finite one."""


class SkyfieldDataError(PropagationError):
    """Skyfield could not build a timescale (e.g. IERS data unavailable)."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def utc_now() -> datetime:
    """Current instant as a timezone-aware UTC datetime."""
    return datetime.now(_UTC)


def _validate_moment(moment: Any) -> datetime:
    """Return ``moment`` as an aware UTC datetime, or raise.

    A naive datetime is rejected rather than silently treated as UTC: assuming
    the wrong zone moves a LEO satellite by ~27,000 km per hour of error, so the
    caller must be explicit.
    """
    if moment is None:
        return utc_now()
    if not isinstance(moment, datetime):
        raise InvalidTimeError(
            f"Timestamp must be a datetime, got {type(moment).__name__}"
        )
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise InvalidTimeError(
            "Timestamp is timezone-naive; attach a timezone (UTC recommended), "
            "e.g. datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)"
        )
    aware = moment.astimezone(_UTC)
    if not _MIN_YEAR <= aware.year <= _MAX_YEAR:
        raise InvalidTimeError(
            f"Timestamp year {aware.year} is outside the supported SGP4 range "
            f"{_MIN_YEAR}-{_MAX_YEAR}"
        )
    return aware


def _vec(values: Any, label: str) -> tuple[float, float, float]:
    """Convert an array-like to a finite 3-tuple of floats."""
    array = np.asarray(values, dtype=float).reshape(-1)
    if array.size != 3 or not np.all(np.isfinite(array)):
        raise PropagationFailure(
            f"{label} is not a finite 3-vector: {array.tolist()}"
        )
    return (float(array[0]), float(array[1]), float(array[2]))


def _norm(vector: Sequence[float]) -> float:
    """Euclidean length of a 3-vector."""
    return math.sqrt(sum(component * component for component in vector))


@lru_cache(maxsize=4)
def get_timescale(*, use_iers_earth_orientation: bool = False) -> Timescale:
    """Return a cached Skyfield timescale.

    Parameters
    ----------
    use_iers_earth_orientation:
        ``False`` (default) uses Skyfield's built-in ΔT and leap-second tables
        and performs **no network access**. ``True`` loads the IERS
        ``finals2000A.all`` file (downloaded once into
        :data:`SKYFIELD_DATA_DIR`) for UT1-UTC and polar motion; for the ISS the
        difference is about 70 m and 0.0006° of longitude.
    """
    try:
        if use_iers_earth_orientation:
            SKYFIELD_DATA_DIR.mkdir(parents=True, exist_ok=True)
            loader = Loader(str(SKYFIELD_DATA_DIR), verbose=False)
            return loader.timescale(builtin=False)
        return load.timescale(builtin=True)
    except Exception as exc:  # noqa: BLE001 - network/filesystem failures vary
        raise SkyfieldDataError(
            f"Could not build a Skyfield timescale: {type(exc).__name__}: {exc}"
        ) from exc


def build_satellite(
    element: GpElement,
    timescale: Timescale | None = None,
) -> EarthSatellite:
    """Build a Skyfield :class:`EarthSatellite` from a validated OMM element.

    ``element.as_omm()`` supplies all seventeen OMM keywords that SGP4 ingestion
    requires (CelesTrak omits some occasionally), so this works for every element
    the API layer accepts.
    """
    ts = timescale or get_timescale()
    try:
        satellite = EarthSatellite.from_omm(ts, dict(element.as_omm()))
    except Exception as exc:  # noqa: BLE001 - Skyfield raises assorted types
        raise PropagationError(
            f"SGP4 could not ingest elements for {element.object_name} "
            f"(NORAD {element.norad_cat_id}): {type(exc).__name__}: {exc}"
        ) from exc
    return satellite


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OrbitalState:
    """A satellite's state at one instant, in every frame Orbix needs.

    All positions are geocentric (origin at the Earth's centre of mass) and in
    kilometres; all angles are in degrees. Fields are grouped by frame so that a
    reader cannot accidentally treat an inertial vector as an Earth-fixed one.
    """

    # -- identity ---------------------------------------------------------- #
    object_name: str
    norad_cat_id: int

    # -- time -------------------------------------------------------------- #
    timestamp: datetime          #: the instant requested (aware UTC)
    epoch: datetime              #: the OMM element epoch (aware UTC)
    element_age: timedelta       #: timestamp - epoch (negative = before epoch)

    # -- WGS-84 geodetic (Earth surface coordinates) ----------------------- #
    latitude_deg: float          #: geodetic latitude,   -90 .. +90
    longitude_deg: float         #: geodetic longitude, -180 .. +180, east +
    altitude_km: float           #: height above the WGS-84 ellipsoid

    # -- Earth-fixed Cartesian, for rendering on a rotating globe ---------- #
    #: ITRS/ECEF axes: x -> 0 deg longitude on the equator, y -> 90 deg E,
    #: z -> north pole. Use this for Three.js; it rotates with the Earth.
    ecef_km: tuple[float, float, float]

    # -- Inertial Cartesian, for orbital geometry -------------------------- #
    #: GCRS (ICRS axes) geocentric position: not rotating with the Earth.
    #: Correct for drawing orbit planes and inertial trajectories.
    gcrs_km: tuple[float, float, float]

    # -- scalar summaries -------------------------------------------------- #
    speed_km_s: float            #: inertial speed magnitude (GCRS velocity)
    radius_km: float             #: |position| from the Earth's centre
    period_minutes: float        #: Keplerian period from the mean motion
    inclination_deg: float       #: orbital inclination from the elements

    # -- provenance -------------------------------------------------------- #
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # A rotation cannot change a vector's length, so the inertial and
        # Earth-fixed radii must agree. Asserting it here catches a frame
        # mix-up at the source instead of in the renderer.
        ecef_radius = _norm(self.ecef_km)
        if abs(ecef_radius - self.radius_km) > 1e-6 * max(1.0, self.radius_km):
            raise PropagationFailure(
                "ECEF and inertial radii disagree "
                f"({ecef_radius:.3f} km vs {self.radius_km:.3f} km); "
                "a coordinate frame was mixed up"
            )

    @property
    def ecef_radius_km(self) -> float:
        """Length of the Earth-fixed position vector (= geocentric radius)."""
        return _norm(self.ecef_km)

    @property
    def subpoint_latitude_deg(self) -> float:
        """Geodetic latitude of the point directly below the satellite."""
        return self.latitude_deg

    def as_dict(self) -> dict[str, Any]:
        """Flat, JSON-friendly view for the UI and for report tables."""
        return {
            "object_name": self.object_name,
            "norad_cat_id": self.norad_cat_id,
            "timestamp": self.timestamp.isoformat(),
            "epoch": self.epoch.isoformat(),
            "element_age_hours": self.element_age.total_seconds() / 3600.0,
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "altitude_km": self.altitude_km,
            "ecef_km": list(self.ecef_km),
            "gcrs_km": list(self.gcrs_km),
            "speed_km_s": self.speed_km_s,
            "radius_km": self.radius_km,
            "period_minutes": self.period_minutes,
            "inclination_deg": self.inclination_deg,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class Sgp4NativeState:
    """Raw SGP4 output in its native **TEME** frame, straight from ``sgp4``.

    Provided deliberately *outside* Skyfield so the application's own pipeline can
    be cross-checked against the reference implementation. Not used for
    rendering: TEME neither rotates with the Earth nor uses ICRS axes.
    """

    norad_cat_id: int
    timestamp: datetime
    error_code: int
    teme_km: tuple[float, float, float]
    teme_velocity_km_s: tuple[float, float, float]


# --------------------------------------------------------------------------- #
# Propagation
# --------------------------------------------------------------------------- #


def propagate(
    element: GpElement,
    when: datetime | None = None,
    *,
    timescale: Timescale | None = None,
    max_element_age: timedelta | None = DEFAULT_MAX_ELEMENT_AGE,
) -> OrbitalState:
    """Propagate ``element`` to ``when`` and return its state in every frame.

    Parameters
    ----------
    element:
        A validated OMM element set (from :class:`src.api.celestrak.GpElement`).
    when:
        Requested instant, timezone-aware (UTC recommended). ``None`` means now.
        Naive datetimes are rejected rather than assumed to be UTC.
    timescale:
        Optional pre-built Skyfield timescale (pass one when propagating many
        satellites so it is not rebuilt each time).
    max_element_age:
        Refuse to propagate when ``|when - element.epoch|`` exceeds this. Pass
        ``None`` to disable the guard (not recommended).

    Raises
    ------
    InvalidTimeError
        ``when`` is naive, not a datetime, or outside 1900-2100.
    StaleElementsError
        The element set is further than ``max_element_age`` from ``when``.
    PropagationFailure
        SGP4 returned an error message or a non-finite vector.
    """
    moment, age = _check_time(element, when, max_element_age)
    ts = timescale or get_timescale()
    satellite = build_satellite(element, ts)
    return _state_at(element, satellite, ts, moment, age)


def _check_time(
    element: GpElement,
    when: datetime | None,
    max_element_age: timedelta | None,
) -> tuple[datetime, timedelta]:
    """Validate the requested instant and enforce the element-age limit."""
    moment = _validate_moment(when)
    age = moment - element.epoch
    if max_element_age is not None and abs(age) > max_element_age:
        raise StaleElementsError(
            f"Element set for {element.object_name} (NORAD {element.norad_cat_id}) "
            f"is {abs(age).total_seconds() / 86400.0:.2f} days from the requested "
            f"instant {moment.isoformat()}, beyond the {max_element_age.days}-day "
            "limit; fetch fresh elements or raise max_element_age deliberately"
        )
    return moment, age


def propagate_series(
    element: GpElement,
    times: Sequence[datetime],
    *,
    timescale: Timescale | None = None,
    max_element_age: timedelta | None = DEFAULT_MAX_ELEMENT_AGE,
) -> tuple[OrbitalState, ...]:
    """Propagate one element set to several instants, e.g. a full orbit trajectory.

    The timescale and the SGP4 model are built once, so sampling an orbit costs
    one propagation per point instead of a full re-ingestion each time.
    """
    ts = timescale or get_timescale()
    satellite = build_satellite(element, ts)
    states: list[OrbitalState] = []
    for when in times:
        moment, age = _check_time(element, when, max_element_age)
        states.append(_state_at(element, satellite, ts, moment, age))
    return tuple(states)


def native_sgp4_state(
    element: GpElement,
    when: datetime | None = None,
) -> Sgp4NativeState:
    """Propagate with the ``sgp4`` library directly and return native **TEME**.

    This bypasses Skyfield on purpose: it is the independent reference used to
    verify Orbix's main pipeline (:func:`propagate`). TEME is SGP4's own output
    frame, so these numbers are *not* directly comparable with
    :attr:`OrbitalState.gcrs_km` or :attr:`OrbitalState.ecef_km` - they differ by
    a rotation of order a few kilometres at LEO altitudes.
    """
    moment = _validate_moment(when)
    if sgp4_omm is None:  # pragma: no cover - sgp4 always ships with Skyfield
        raise PropagationError("sgp4 OMM support is unavailable in this environment")

    satrec = Satrec()
    sgp4_omm.initialize(satrec, dict(element.as_omm()))
    jd, fraction = jday(
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second + moment.microsecond / 1e6,
    )
    error_code, position, velocity = satrec.sgp4(jd, fraction)
    if error_code != 0:
        raise PropagationFailure(
            f"sgp4 returned error code {error_code} for {element.object_name} "
            f"(NORAD {element.norad_cat_id}) at {moment.isoformat()}"
        )
    return Sgp4NativeState(
        norad_cat_id=element.norad_cat_id,
        timestamp=moment,
        error_code=error_code,
        teme_km=_vec(position, "TEME position"),
        teme_velocity_km_s=_vec(velocity, "TEME velocity"),
    )


def _state_at(
    element: GpElement,
    satellite: EarthSatellite,
    timescale: Timescale,
    moment: datetime,
    age: timedelta,
) -> OrbitalState:
    """Shared implementation: Skyfield position -> :class:`OrbitalState`.

    This is the only place the frame conversions happen, so ``propagate`` and
    ``propagate_series`` cannot drift apart.
    """
    geocentric = satellite.at(timescale.from_datetime(moment))

    # Skyfield stores the SGP4 status here: None on success, otherwise the
    # propagator's complaint (decayed orbit, eccentricity out of range, ...).
    if geocentric.message is not None:
        raise PropagationFailure(
            f"SGP4 could not propagate {element.object_name} "
            f"(NORAD {element.norad_cat_id}) to {moment.isoformat()}: "
            f"{geocentric.message}"
        )

    # Inertial: GCRS/ICRS axes, does not rotate with the Earth.
    gcrs = _vec(geocentric.xyz.km, "GCRS position")
    # Earth-fixed: ITRS axes, rotates with the Earth. This is what a globe wants.
    ecef = _vec(geocentric.frame_xyz(itrs).km, "ECEF position")
    velocity = _vec(geocentric.velocity.km_per_s, "GCRS velocity")

    # WGS-84 geodetic conversion of the same geocentric position.
    latitude, longitude = wgs84.latlon_of(geocentric)
    height = wgs84.height_of(geocentric)

    warnings: list[str] = []
    if abs(age) > STALE_ELEMENT_AGE:
        warnings.append(
            f"Element set is {abs(age).total_seconds() / 86400.0:.1f} days from the "
            "requested time; CelesTrak flags elements older than 3.5 days and SGP4 "
            "accuracy degrades away from the epoch"
        )

    period = element.period_minutes
    if period is None:
        raise PropagationFailure(
            f"Element set for {element.object_name} has no usable mean motion, "
            "so its orbital period cannot be derived"
        )

    return OrbitalState(
        object_name=element.object_name,
        norad_cat_id=element.norad_cat_id,
        timestamp=moment,
        epoch=element.epoch,
        element_age=age,
        latitude_deg=float(latitude.degrees),
        longitude_deg=float(longitude.degrees),
        altitude_km=float(height.km),
        ecef_km=ecef,
        gcrs_km=gcrs,
        speed_km_s=_norm(velocity),
        radius_km=_norm(gcrs),
        period_minutes=period,
        inclination_deg=element.inclination,
        warnings=tuple(warnings),
    )





