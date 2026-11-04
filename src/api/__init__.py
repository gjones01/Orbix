"""External data acquisition layer."""

from .celestrak import (
    CelestrakClient,
    CelestrakEmptyResponse,
    CelestrakError,
    CelestrakFormatError,
    CelestrakHTTPError,
    CelestrakNetworkError,
    CelestrakNoDataAvailable,
    CelestrakRateLimited,
    Freshness,
    GpElement,
    GpFetchResult,
    SnapshotRecord,
    load_snapshot,
)

__all__ = [
    "CelestrakClient",
    "CelestrakEmptyResponse",
    "CelestrakError",
    "CelestrakFormatError",
    "CelestrakHTTPError",
    "CelestrakNetworkError",
    "CelestrakNoDataAvailable",
    "CelestrakRateLimited",
    "Freshness",
    "GpElement",
    "GpFetchResult",
    "SnapshotRecord",
    "load_snapshot",
]
