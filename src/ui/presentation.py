"""Assemble the satellite scene the UI displays.

This module deliberately imports **no Streamlit**: it turns live data into plain
dataclasses so that the numbers shown in the info panel can be unit-tested without
starting a web server. ``src/ui/loader.py`` adds caching, and ``app.py`` renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from src.api.celestrak import CelestrakClient, Freshness, GpElement
from src.orbital.propagation import OrbitalState, propagate, propagate_series, utc_now

__all__ = [
    "ISS_CATALOG_NUMBER",
    "DEFAULT_TRACK_SAMPLES",
    "Fact",
    "Scene",
    "format_age",
    "format_utc",
    "format_utc_compact",
    "load_scene",
]

ISS_CATALOG_NUMBER = 25544

#: Track resolution for one orbital period. 120 samples is ~46 s apart for the ISS,
#: which renders as a smooth curve without a visible cost.
DEFAULT_TRACK_SAMPLES = 120


def format_utc(moment: datetime) -> str:
    """Format a timestamp as ``YYYY-MM-DD HH:MM:SS UTC`` for prose."""
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_utc_compact(moment: datetime) -> str:
    """Compact ISO-style UTC stamp for the narrow information panel."""
    return moment.strftime("%Y-%m-%d %H:%M:%SZ")


def format_age(age: timedelta) -> str:
    """Format an element age like ``+11.3 h`` or ``-2.0 d``."""
    hours = age.total_seconds() / 3600.0
    sign = "+" if hours >= 0 else "-"
    magnitude = abs(hours)
    if magnitude < 1.0:
        return f"{sign}{magnitude * 60.0:.0f} min"
    if magnitude < 48.0:
        return f"{sign}{magnitude:.1f} h"
    return f"{sign}{magnitude / 24.0:.1f} d"


@dataclass(frozen=True)
class Fact:
    """One label/value pair in the information panel."""

    label: str
    value: str
    hint: str = ""


@dataclass(frozen=True)
class Scene:
    """Everything the UI needs to render one instant of one satellite."""

    element: GpElement
    state: OrbitalState
    track: tuple[OrbitalState, ...]
    freshness: Freshness
    source: str
    notes: tuple[str, ...] = ()

    @property
    def object_name(self) -> str:
        """Common name from the element set."""
        return self.element.object_name

    @property
    def norad_id(self) -> int:
        """NORAD catalog number."""
        return self.element.norad_cat_id

    @property
    def is_live(self) -> bool:
        """True only if the element set was downloaded in this session."""
        return self.source == "network"

    @property
    def source_label(self) -> str:
        """Short provenance badge for the header."""
        return {
            "network": "LIVE",
            "cache": "CACHED",
            "snapshot": "SNAPSHOT",
        }.get(self.source, self.source.upper())

    @property
    def source_detail(self) -> str:
        """Sentence explaining where the element set came from."""
        return {
            "network": "Element set downloaded from CelesTrak in this session.",
            "cache": "Element set served from Orbix's local cache "
                     "(CelesTrak refreshes GP data every 2 hours).",
            "snapshot": "Element set loaded from a saved snapshot; CelesTrak was "
                        "not re-queried.",
        }.get(self.source, self.source)

    def facts(self) -> tuple[Fact, ...]:
        """The information-panel rows, in a fixed order."""
        state = self.state
        return (
            Fact("Satellite", self.object_name),
            Fact("NORAD catalog ID", f"{self.norad_id}"),
            Fact("Altitude", f"{state.altitude_km:,.1f} km", "above ellipsoid"),
            Fact("Velocity", f"{state.speed_km_s:.3f} km/s", "inertial"),
            Fact("Inclination", f"{state.inclination_deg:.2f}\u00b0"),
            Fact("Orbital period", f"{state.period_minutes:.2f} min"),
            Fact("Simulation time", format_utc_compact(state.timestamp)),
            Fact("Element age", format_age(state.element_age), "since epoch"),
        )

    def subpoint_fact(self) -> Fact:
        """Sub-satellite point, useful next to the globe."""
        return Fact(
            "Sub-satellite point",
            f"{self.state.latitude_deg:+.2f}\u00b0, {self.state.longitude_deg:+.2f}\u00b0",
            "WGS-84 geodetic",
        )


def load_scene(
    catalog_number: int = ISS_CATALOG_NUMBER,
    *,
    offline: bool = False,
    force_refresh: bool = False,
    when: datetime | None = None,
    track_samples: int = DEFAULT_TRACK_SAMPLES,
) -> Scene:
    """Fetch elements, propagate the current state, and sample one orbit.

    Parameters
    ----------
    catalog_number:
        NORAD catalog number. Step D is ISS-only by design.
    offline:
        Never contact CelesTrak; use the newest saved snapshot.
    force_refresh:
        Ask the API client to re-download even if its 2-hour cache is fresh.
        CelesTrak may answer HTTP 403 "not updated", which the client treats as a
        cache hit rather than an error.
    when:
        Simulation instant. Defaults to now (UTC); Step E drives this from a slider.
    track_samples:
        Number of Earth-fixed samples across one orbital period.
    """
    client = CelestrakClient(offline=offline)
    fetched = client.fetch_by_catalog_number(catalog_number, refresh=force_refresh)
    element = fetched.elements[0]

    moment = when or utc_now()
    state = propagate(element, moment)

    period_seconds = element.period_minutes * 60.0
    if period_seconds <= 0:
        raise ValueError(
            f"{element.object_name} has no usable period; cannot build a track"
        )
    step = period_seconds / max(1, track_samples)
    times: Sequence[datetime] = [
        moment + timedelta(seconds=step * index)
        for index in range(int(track_samples) + 1)
    ]
    track = propagate_series(element, times)

    return Scene(
        element=element,
        state=state,
        track=track,
        freshness=fetched.freshness(),
        source=fetched.source,
        notes=fetched.notes,
    )
