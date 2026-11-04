"""Python -> JavaScript constellation scene payload.

This module is the contract between Python (which owns every orbital calculation) and
the browser (which only draws and interpolates). Two principles:

1. **No orbital maths in JavaScript.** Positions arrive as Earth-fixed geodetic
   degrees and altitudes already scaled to globe radii; the renderer only interpolates
   between supplied keyframes.
2. **The payload must stay small.** Keyframe arrays are base64-encoded ``Float32``
   buffers (4 bytes per number, no JSON punctuation), and the keyframe count adapts to
   the number of satellites so a request can never explode in size.

Payload shape (version 2)::

    {
      "version": 2,
      "scene_id": "...",              # changes only when data/window changes
      "window": {start, end, keyframes, interval_seconds, max_interpolation_km, note},
      "groups": [{id, label, colour, count, enabled, source, ok, error}],
      "satellites": [{id, name, g, alt, incl, period}],   # index-aligned metadata
      "keyframes": {count, times[], lat, lng, alt},       # omitted on light reruns
      "selection": 25544 | null,
      "selected_track": [[lat, lng, alt_ratio], ...] | null,
      "limits": {marker_radius, truncated}
    }

Keyframe buffers are flat and index-aligned with ``satellites``: satellite ``i``
occupies ``[i*count : (i+1)*count]`` in each decoded array.
"""

from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np

from src.api.catalog import Catalog, GroupStatus
from src.orbital.batch import BatchSample

__all__ = [
    "DEFAULT_MARKER_RADIUS",
    "EARTH_RADIUS_KM",
    "KEYFRAME_BUDGET_CHARS",
    "MAX_KEYFRAMES",
    "PAYLOAD_VERSION",
    "WindowPlan",
    "altitude_ratio",
    "build_constellation_payload",
    "encode_float32",
    "plan_window",
    "short_name",
]

#: IUGG mean Earth radius (km). Globe.gl's sphere is one Earth radius, so this is the
#: factor between physical altitude and the renderer's radius-relative units.
EARTH_RADIUS_KM = 6371.0088

PAYLOAD_VERSION = 2

#: Upper bound on base64 characters spent on keyframes per payload (~1.5 MB binary).
KEYFRAME_BUDGET_CHARS = 2_000_000

#: Never sample more finely than this, even for tiny constellations.
MAX_KEYFRAMES = 91

#: Reference LEO period/radius for the interpolation-error estimate: the fastest
#: objects Orbix displays, which keeps the quoted error conservative.
_REFERENCE_PERIOD_S = 92.95 * 60.0
_REFERENCE_RADIUS_KM = 6793.0

#: Sampling target: aim for at most this chord error, then coarsen only if the payload
#: budget demands it. 1 km is far below the element-set accuracy, so the animation is
#: essentially exact while the payload stays reasonable.
TARGET_INTERPOLATION_KM = 1.0

#: Marker radius in angular degrees (Globe.gl's unit for point size). Positions are
#: exact; only the drawn size is exaggerated.
DEFAULT_MARKER_RADIUS = 0.32


def altitude_ratio(altitude_km: float) -> float:
    """Convert a physical altitude in km to Globe.gl's globe-radius units."""
    return float(altitude_km) / EARTH_RADIUS_KM


def short_name(object_name: str, limit: int = 22) -> str:
    """Compact label, e.g. ``ISS (ZARYA)`` -> ``ISS``."""
    head = object_name.split("(")[0].strip()
    name = head or object_name.strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "\u2026"


def encode_float32(values: np.ndarray) -> str:
    """Encode an array as a base64 little-endian Float32 blob."""
    return base64.b64encode(
        np.ascontiguousarray(values, dtype="<f4").tobytes()
    ).decode("ascii")


def _chord_error_km(interval_seconds: float) -> float:
    """Worst-case error from linearly interpolating a circular orbit.

    A satellite moving at angular rate ``w`` along a circle of radius ``r`` deviates
    from the straight line between two samples by ``r * (1 - cos(w*dt/2))`` at the
    midpoint. The UI quotes this number, so it is computed rather than guessed.
    """
    omega = 2.0 * math.pi / _REFERENCE_PERIOD_S
    half = omega * interval_seconds / 2.0
    return _REFERENCE_RADIUS_KM * (1.0 - math.cos(half))


@dataclass(frozen=True)
class WindowPlan:
    """How a playback window is sampled, and how accurate that sampling is."""

    start: datetime
    end: datetime
    keyframes: int
    interval_seconds: float
    max_interpolation_km: float

    @property
    def duration_seconds(self) -> float:
        """Window length in seconds."""
        return (self.end - self.start).total_seconds()

    def describe(self) -> str:
        """One-line accuracy note for the UI."""
        return (
            f"{self.keyframes} keyframes over "
            f"{self.duration_seconds / 60.0:.0f} min "
            f"(every {self.interval_seconds / 60.0:.1f} min); worst-case "
            f"interpolation error \u2248 {self.max_interpolation_km:.1f} km"
        )


def _interval_for_error(target_km: float) -> float:
    """Largest sampling interval whose chord error stays below ``target_km``."""
    if target_km <= 0:
        return 0.0
    omega = 2.0 * math.pi / _REFERENCE_PERIOD_S
    ratio = min(1.0, target_km / _REFERENCE_RADIUS_KM)
    return 2.0 * math.acos(1.0 - ratio) / omega


def plan_window(
    nominal_start: datetime,
    nominal_end: datetime,
    n_satellites: int,
    *,
    budget_chars: int = KEYFRAME_BUDGET_CHARS,
    max_keyframes: int = MAX_KEYFRAMES,
    target_error_km: float = TARGET_INTERPOLATION_KM,
) -> WindowPlan:
    """Pick a keyframe count from the accuracy target and the payload budget.

    Sampling is as fine as needed to stay under ``target_error_km``, then coarsened if
    ``n_satellites`` would push the payload past ``budget_chars``. Bigger
    constellations therefore get a larger - but always displayed - interpolation error.
    """
    if nominal_end <= nominal_start:
        raise ValueError("window end must be after start")
    if n_satellites <= 0:
        raise ValueError("need at least one satellite")

    duration = (nominal_end - nominal_start).total_seconds()

    # 1. How fine must we sample to hit the accuracy target?
    wanted_interval = max(1.0, _interval_for_error(target_error_km))
    needed = int(math.ceil(duration / wanted_interval)) + 1

    # 2. How fine may we sample before the payload budget bites?
    #    3 coordinates per satellite per keyframe, 4 bytes each, 4/3 base64 inflation.
    chars_per_keyframe = n_satellites * 3 * 4 * 4 / 3
    affordable = max(2, int(budget_chars / chars_per_keyframe))

    keyframes = int(max(2, min(max_keyframes, needed, affordable)))
    interval = duration / (keyframes - 1)
    return WindowPlan(
        start=nominal_start,
        end=nominal_end,
        keyframes=keyframes,
        interval_seconds=interval,
        max_interpolation_km=_chord_error_km(interval),
    )


def _scene_id(catalog: Catalog, plan: WindowPlan, sample: BatchSample) -> str:
    """Fingerprint of everything that would force the browser to rebuild buffers."""
    digest = hashlib.sha256()
    digest.update(str(len(catalog.satellites)).encode())
    for norad_id in sorted(catalog.satellites):
        digest.update(str(norad_id).encode())
    digest.update(plan.start.isoformat().encode())
    digest.update(plan.end.isoformat().encode())
    digest.update(str(sample.shape).encode())
    return digest.hexdigest()[:16]


def build_constellation_payload(
    *,
    catalog: Catalog,
    sample: BatchSample,
    plan: WindowPlan,
    group_specs: Sequence[Any],
    enabled_groups: Iterable[str],
    selection: int | None = None,
    selected_track: Sequence[Any] | None = None,
    include_keyframes: bool = True,
    truncated: bool = False,
    marker_radius: float = DEFAULT_MARKER_RADIUS,
) -> dict[str, Any]:
    """Assemble the JSON payload consumed by ``orbix-globe.js``.

    Parameters
    ----------
    catalog:
        The de-duplicated catalogue; determines satellite order and metadata.
    sample:
        Batch positions, shaped ``(satellites, keyframes)``, in catalogue order.
    plan:
        The window sampling decision (also carries the accuracy note).
    enabled_groups:
        Group ids the user currently has switched on. The browser filters locally, so
        changing a filter never needs new data.
    selection:
        NORAD ID to highlight, or ``None``.
    selected_track:
        Pre-computed ``[lat, lng, alt_ratio]`` points for the selected satellite.
    include_keyframes:
        Set ``False`` on reruns where the scene is unchanged, so a selection click does
        not re-ship megabytes of positions.
    """
    satellites = sorted(catalog.satellites.values(), key=lambda sat: sat.norad_id)
    order = {sat.norad_id: index for index, sat in enumerate(satellites)}
    if len(satellites) != sample.lat.shape[0]:
        raise ValueError(
            "catalogue and sample disagree: "
            f"{len(satellites)} satellites vs {sample.lat.shape[0]} samples"
        )

    group_index = {spec.id: index for index, spec in enumerate(group_specs)}
    metadata = []
    for sat in satellites:
        element = sat.element
        # Deliberately compact: with ~12k satellites every field here is multiplied by
        # 12k and re-sent on each rerun.
        metadata.append(
            {
                "id": sat.norad_id,
                "name": short_name(element.object_name),
                "g": group_index.get(sat.primary_group, 0),
                "alt": round(float(sample.alt[order[sat.norad_id], 0]), 0),
                "incl": round(element.inclination, 1),
                "period": round(element.period_minutes or 0.0, 1),
            }
        )

    payload: dict[str, Any] = {
        "version": PAYLOAD_VERSION,
        "scene_id": _scene_id(catalog, plan, sample),
        "window": {
            "start": plan.start.isoformat(),
            "end": plan.end.isoformat(),
            "keyframes": plan.keyframes,
            "interval_seconds": round(plan.interval_seconds, 3),
            "max_interpolation_km": round(plan.max_interpolation_km, 2),
            "note": plan.describe(),
        },
        "groups": [
            {
                "id": status.spec.id,
                "label": status.spec.label,
                "colour": status.spec.colour,
                "count": status.count,
                "enabled": status.spec.id in {g.lower() for g in enabled_groups},
                "source": status.source,
                "ok": status.ok,
                "error": status.error,
            }
            for status in catalog.groups
        ],
        "satellites": metadata,
        "selection": selection,
        "selected_track": list(selected_track) if selected_track else None,
        "limits": {
            "marker_radius": marker_radius,
            "truncated": bool(truncated),
            "satellites": len(metadata),
        },
    }

    if include_keyframes:
        # (N, K) -> flat, index-aligned with `satellites`.
        payload["keyframes"] = {
            "count": int(plan.keyframes),
            "times": [moment.isoformat() for moment in sample.times],
            "lat": encode_float32(sample.lat.reshape(-1)),
            "lng": encode_float32(sample.lng.reshape(-1)),
            "alt": encode_float32(
                np.vectorize(altitude_ratio, otypes=[np.float32])(
                    sample.alt.reshape(-1)
                )
            ),
        }

    return payload

