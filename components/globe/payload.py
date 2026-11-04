"""Python -> JavaScript scene payload for the Globe.gl component.

This module is the contract between Python (which owns every orbital
calculation) and the browser (which only draws). Keeping the conversion here means
the renderer receives values in exactly the units Globe.gl expects and never has
to do geometry of its own.

Units and frames
----------------
* ``lat`` / ``lon`` - **WGS-84 geodetic degrees**, Earth-fixed. These come
  straight from :class:`src.orbital.propagation.OrbitalState`, whose ``ecef_km``
  vector is in the same rotating frame as the globe mesh.
* ``altitude_km`` - physical height above the WGS-84 ellipsoid (for display).
* ``altitude_ratio`` - the same height as a **fraction of the globe's radius**,
  which is how Globe.gl expresses altitude (``0`` = surface, ``1`` = one Earth
  radius above it). Globe.gl treats its sphere as one Earth radius, so the
  conversion factor is the mean Earth radius.

Nothing here guesses: if the propagation module did not produce a value, the
payload is not built.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from src.orbital.propagation import OrbitalState

__all__ = [
    "EARTH_RADIUS_KM",
    "PAYLOAD_VERSION",
    "altitude_ratio",
    "build_scene_payload",
    "short_name",
]

#: IUGG mean Earth radius (km). Globe.gl's sphere is taken to be one Earth radius,
#: so this converts physical altitude into the renderer's radius-relative units.
EARTH_RADIUS_KM = 6371.0088

PAYLOAD_VERSION = 1

#: Marker radius in **angular degrees** of arc - the unit Globe.gl uses for
#: ``pointRadius`` (its default is 0.25). The marker's *position* is exact; only its
#: drawn size is exaggerated, because a to-scale ISS at 425 km would be invisible.
DEFAULT_MARKER_RADIUS = 0.9


def altitude_ratio(altitude_km: float) -> float:
    """Convert a physical altitude in km to Globe.gl's globe-radius units."""
    return float(altitude_km) / EARTH_RADIUS_KM


def short_name(object_name: str, limit: int = 18) -> str:
    """Compact label for the globe, e.g. ``ISS (ZARYA)`` -> ``ISS``."""
    head = object_name.split("(")[0].strip()
    name = head or object_name.strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "\u2026"


def _point(state: OrbitalState) -> list[float]:
    """[lat, lon, alt_ratio] for one Earth-fixed position."""
    values = (state.latitude_deg, state.longitude_deg)
    if not all(math.isfinite(value) for value in values + (state.altitude_km,)):
        raise ValueError(
            f"Non-finite position for {state.object_name}: {values} at {state.timestamp}"
        )
    return [float(state.latitude_deg), float(state.longitude_deg),
            altitude_ratio(state.altitude_km)]


def build_scene_payload(
    *,
    satellite: OrbitalState,
    track: Sequence[OrbitalState] = (),
    height: int = 680,
    marker_radius: float = DEFAULT_MARKER_RADIUS,
) -> dict[str, Any]:
    """Assemble the JSON payload consumed by ``orbix-globe.js``.

    Parameters
    ----------
    satellite:
        The propagated state to draw right now.
    track:
        Error-free propagated states covering roughly one orbital period. Each is
        an Earth-fixed position, so the drawn curve is the track the satellite
        will trace over the *rotating* Earth, not an inertial orbit plane.
    height:
        Pixel height requested for the component iframe.
    """
    if not math.isfinite(satellite.altitude_km):
        raise ValueError("Satellite altitude is not finite; refusing to render")

    points = [_point(state) for state in track]

    return {
        "version": PAYLOAD_VERSION,
        "satellite": {
            "name": satellite.object_name,
            "short_name": short_name(satellite.object_name),
            "norad_id": satellite.norad_cat_id,
            "lat": float(satellite.latitude_deg),
            "lon": float(satellite.longitude_deg),
            "altitude_km": float(satellite.altitude_km),
            "altitude_ratio": altitude_ratio(satellite.altitude_km),
            "ground_altitude_ratio": 0.0,
            "marker_radius": float(marker_radius),
            "speed_km_s": float(satellite.speed_km_s),
        },
        "track": {
            "points": points,
            "period_minutes": float(satellite.period_minutes),
            "sample_count": len(points),
        },
        "meta": {
            "timestamp": satellite.timestamp.isoformat(),
            "element_epoch": satellite.epoch.isoformat(),
            "element_age_hours": satellite.element_age.total_seconds() / 3600.0,
            "warnings": list(satellite.warnings),
        },
        "height": int(height),
    }
