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
from typing import Any, Iterable, Sequence

import numpy as np

from src.api.catalog import Catalog, GroupStatus
from src.orbital.batch import BatchSample
from src.orbital.window import WindowPlan

__all__ = [
    "DEFAULT_MARKER_RADIUS",
    "EARTH_RADIUS_KM",
    "PAYLOAD_VERSION",
    "altitude_ratio",
    "build_constellation_payload",
    "encode_float32",
    "short_name",
]

#: IUGG mean Earth radius (km). Globe.gl's sphere is one Earth radius, so this is the
#: factor between physical altitude and the renderer's radius-relative units.
EARTH_RADIUS_KM = 6371.0088

PAYLOAD_VERSION = 2

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
                "count": status.records_fetched,
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

