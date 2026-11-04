"""Globe.gl 3D Earth rendered as a Streamlit custom component.

Layout of this package::

    globe/
    ├── __init__.py      declared Streamlit component + public API
    ├── payload.py       Python -> JavaScript scene contract (pure, testable)
    └── frontend/        static assets served by Streamlit to the iframe
        ├── index.html
        ├── orbix-globe.js   scene code + Streamlit protocol bridge
        ├── style.css
        ├── assets/          Earth textures (licensing in assets/README.md)
        └── vendor/          pinned Globe.gl bundle

Two-way communication
---------------------
The component uses Streamlit's custom-component protocol directly (version 1), so
the iframe can send values back to Python as well as receive them. Today it sends
an event when a satellite marker is clicked; Step E uses the same channel for
selection and playback controls. Camera movement happens entirely inside the
iframe and never triggers a Python rerun.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

from .payload import (
    EARTH_RADIUS_KM,
    PAYLOAD_VERSION,
    altitude_ratio,
    build_scene_payload,
    short_name,
)

__all__ = [
    "EARTH_RADIUS_KM",
    "PAYLOAD_VERSION",
    "FRONTEND_DIR",
    "altitude_ratio",
    "build_scene_payload",
    "globe_component",
    "short_name",
]

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

_COMPONENT_NAME = "orbix_globe"


@lru_cache(maxsize=1)
def _register() -> Any:
    """Declare the component once, lazily.

    Declared lazily so importing this package (in tests, or in tooling) never
    touches Streamlit's component registry.
    """
    if not (FRONTEND_DIR / "index.html").exists():  # pragma: no cover
        raise FileNotFoundError(
            f"Globe front-end assets are missing from {FRONTEND_DIR}"
        )
    return components.declare_component(_COMPONENT_NAME, path=str(FRONTEND_DIR))


def globe_component(
    payload: dict[str, Any],
    *,
    height: int = 680,
    key: str = "orbix_globe",
) -> Any:
    """Render the 3D globe and return whatever the front-end last sent.

    Returns ``None`` until the user interacts with the globe, then a dict such as
    ``{"event": "select", "norad_id": 25544, ...}``.
    """
    return _register()(payload=payload, height=int(height), key=key, default=None)
