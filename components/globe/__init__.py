"""Globe.gl constellation globe as a Streamlit custom component.

Layout::

    globe/
    ├── __init__.py      declared Streamlit component + public API
    ├── payload.py       Python -> JavaScript scene contract (pure, testable)
    └── frontend/        static assets served by Streamlit to the iframe
        ├── index.html
        ├── orbix-globe.js   Globe.gl Earth + batched satellite layer + HUD
        ├── style.css        HUD panels, tooltip, star field
        ├── assets/          Earth textures (licensing in assets/README.md)
        └── vendor/          pinned Globe.gl 2.34.4 and three.js r171

Why a custom component
----------------------
The front-end speaks Streamlit's component protocol (version 1) directly, so the
iframe can send values back to Python as well as receive them. Marker clicks arrive in
Python as ``{"event": "select", "norad_id": ...}``, while camera movement, filtering
and simulation playback stay inside the iframe and never trigger a rerun.

Why three.js is vendored separately
-----------------------------------
Globe.gl bundles three.js r171 internally but does not expose it. The satellite layer
needs ``THREE.Points`` (one draw call for ~12k objects), so the identical revision is
vendored alongside and imported as an ES module. Same revision means the objects are
interchangeable with Globe.gl's own scene graph.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

# Window sampling policy lives in src.orbital.window; the payload module only
# consumes it, so it is not re-exported here.
from .payload import (
    DEFAULT_MARKER_RADIUS,
    EARTH_RADIUS_KM,
    PAYLOAD_VERSION,
    altitude_ratio,
    build_constellation_payload,
    short_name,
)

__all__ = [
    "DEFAULT_MARKER_RADIUS",
    "EARTH_RADIUS_KM",
    "FRONTEND_DIR",
    "PAYLOAD_VERSION",
    "altitude_ratio",
    "build_constellation_payload",
    "globe_component",
    "short_name",
]

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

_COMPONENT_NAME = "orbix_globe"


@lru_cache(maxsize=1)
def _register() -> Any:
    """Declare the component once, lazily, so imports stay side-effect free."""
    if not (FRONTEND_DIR / "index.html").exists():  # pragma: no cover
        raise FileNotFoundError(
            f"Globe front-end assets are missing from {FRONTEND_DIR}"
        )
    return components.declare_component(_COMPONENT_NAME, path=str(FRONTEND_DIR))


def globe_component(
    payload: dict[str, Any],
    *,
    height: int = 720,
    key: str = "orbix_globe",
) -> Any:
    """Render the constellation globe and return the front-end's last event.

    Returns ``None`` until the user clicks a satellite, then a dict such as
    ``{"event": "select", "norad_id": 25544, "name": ..., "sim_time": ...}``.
    """
    return _register()(payload=payload, height=int(height), key=key, default=None)
