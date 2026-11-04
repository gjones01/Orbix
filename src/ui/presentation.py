"""Assemble the constellation scene the UI displays.

This module imports **no Streamlit** so the numbers behind the globe can be tested
without a browser or a server. It orchestrates:

``load_catalog``  (one CelesTrak request per group, de-duplicated by NORAD ID)
        -> ``BatchPropagator`` (vectorised SGP4 for every object)
        -> ``plan_window``     (how finely to sample the playback window)
        -> ``BatchSample``     (lat/lng/alt for every satellite at every keyframe)
        -> payload             (the JSON contract in ``components.globe.payload``)

The window is sampled once per scene rather than re-propagated per browser frame, and
the sampling density adapts to the constellation size so the payload stays bounded.
The resulting interpolation error is computed and reported, never hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from components.globe.payload import (
    WindowPlan,
    altitude_ratio,
    build_constellation_payload,
    plan_window,
)
from src.api.catalog import GROUP_SPECS, Catalog, Satellite, load_catalog
from src.orbital.batch import BatchPropagator, BatchSample
from src.orbital.propagation import propagate_series, utc_now

__all__ = [
    "DEFAULT_WINDOW_MINUTES",
    "MAX_ELEMENT_AGE",
    "TRACK_SAMPLES",
    "ConstellationScene",
    "default_groups",
    "load_constellation",
]

#: Total playback window width in minutes: long enough to see motion in every orbit
#: type, short enough that keyframe sampling can stay fine.
DEFAULT_WINDOW_MINUTES = 20.0

#: Refuse to propagate elements this far from the simulation window: SGP4 stays
#: numerically stable, but the accuracy is long gone. Reported, never silent.
MAX_ELEMENT_AGE = timedelta(days=30)

#: Samples used for a selected satellite's orbit trail.
TRACK_SAMPLES = 121

UTC = timezone.utc


def default_groups() -> tuple[str, ...]:
    """Groups enabled by default (everything except the very large Starlink set)."""
    return tuple(spec.id for spec in GROUP_SPECS if spec.default_enabled)


@dataclass(frozen=True)
class ConstellationScene:
    """Everything needed to draw the constellation and explain what is drawn.

    Deliberately holds no ``BatchPropagator``: that object wraps C-extension state and
    cannot be pickled, and the UI never needs to re-sample after construction. Keeping
    the scene plain data is what lets Streamlit cache it.
    """

    catalog: Catalog
    sample: BatchSample
    plan: WindowPlan
    skipped_stale: tuple[int, ...] = ()

    @property
    def unique_count(self) -> int:
        """Number of unique satellites in the catalogue."""
        return len(self.catalog)

    @property
    def rendered_count(self) -> int:
        """Satellites actually propagated (excludes stale elements)."""
        return int(self.sample.shape[0])

    @property
    def keyframes(self) -> int:
        """Sampled instants in the playback window."""
        return int(self.sample.shape[1])

    def counts_by_group(self) -> dict[str, int]:
        """How many unique objects each group contributes."""
        return self.catalog.counts_by_group()

    def element(self, norad_id: int):
        """Look up one element set by NORAD ID."""
        satellite = self.catalog.satellites.get(int(norad_id))
        return satellite.element if satellite else None

    def group_of(self, norad_id: int) -> str | None:
        """Primary group of one satellite."""
        satellite = self.catalog.satellites.get(int(norad_id))
        return satellite.primary_group if satellite else None

    def selected_track(
        self,
        norad_id: int,
        when: datetime | None = None,
        samples: int = TRACK_SAMPLES,
    ) -> tuple[tuple[float, float, float], ...]:
        """Earth-fixed ``[lat, lng, alt_ratio]`` trail for one satellite.

        Uses the single-satellite Skyfield path (the verified one) over one orbital
        period starting at ``when``. Altitude is returned in globe-radius units, so the
        renderer needs no conversion at all.
        """
        element = self.element(norad_id)
        if element is None:
            return ()
        start = when or self.plan.start
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        period_seconds = (element.period_minutes or 92.95) * 60.0
        step = period_seconds / max(1, samples - 1)
        times = [start + timedelta(seconds=step * index) for index in range(samples)]
        states = propagate_series(element, times)
        return tuple(
            (
                round(state.latitude_deg, 4),
                round(state.longitude_deg, 4),
                round(altitude_ratio(state.altitude_km), 5),
            )
            for state in states
        )

    def payload(
        self,
        *,
        enabled_groups: Iterable[str] | None = None,
        selection: int | None = None,
        include_keyframes: bool = True,
        selected_when: datetime | None = None,
    ) -> dict:
        """Build the JSON payload for the globe component."""
        groups = tuple(enabled_groups) if enabled_groups is not None else default_groups()
        track = (
            self.selected_track(selection, selected_when)
            if selection is not None
            else None
        )
        return build_constellation_payload(
            catalog=self.catalog,
            sample=self.sample,
            plan=self.plan,
            group_specs=GROUP_SPECS,
            enabled_groups=groups,
            selection=selection,
            selected_track=track,
            include_keyframes=include_keyframes,
        )

    def summary(self) -> str:
        """One-line description of what was loaded."""
        statuses = ", ".join(
            f"{status.spec.label} {status.count}"
            for status in self.catalog.groups
            if status.ok
        )
        return f"{self.unique_count} unique satellites ({statuses})"


def load_constellation(
    group_ids: Iterable[str] | None = None,
    *,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    offline: bool = False,
    force_refresh: bool = False,
    when: datetime | None = None,
) -> ConstellationScene:
    """Load, de-duplicate and sample a constellation over a playback window.

    Parameters
    ----------
    group_ids:
        CelesTrak groups to load. ``None`` means every group in ``GROUP_SPECS``.
    window_minutes:
        Total playback window, centred on ``when``.
    offline:
        Serve from saved snapshots only.
    force_refresh:
        Ignore the 2-hour cache (CelesTrak may answer 403 "not updated").
    when:
        Centre of the window; defaults to now (UTC).
    """
    catalog = load_catalog(group_ids, offline=offline, force_refresh=force_refresh)
    if not catalog.satellites:
        details = "; ".join(
            f"{status.spec.id}: {status.error or 'no records'}"
            for status in catalog.groups
        )
        raise ValueError("No satellites were loaded from any group (" + details + ")")

    centre = when or utc_now()
    if centre.tzinfo is None:
        raise ValueError("Simulation time must be timezone-aware")
    half = timedelta(minutes=max(1.0, window_minutes) / 2.0)
    start, end = centre - half, centre + half

    # Guard against unbounded extrapolation from stale elements.
    usable: list[Satellite] = []
    skipped: list[int] = []
    for satellite in sorted(catalog.satellites.values(), key=lambda s: s.norad_id):
        if abs(start - satellite.element.epoch) > MAX_ELEMENT_AGE:
            skipped.append(satellite.norad_id)
        else:
            usable.append(satellite)

    if not usable:
        raise ValueError(
            "Every element set is older than the "
            f"{MAX_ELEMENT_AGE.days}-day propagation limit"
        )
    if skipped:
        # Drop them from the scene rather than render stale ghosts; the caller reports
        # the count so nothing disappears silently.
        catalog = Catalog(
            satellites={sat.norad_id: sat for sat in usable},
            groups=catalog.groups,
            retrieved_at=catalog.retrieved_at,
        )

    propagator = BatchPropagator([sat.element for sat in usable])
    plan = plan_window(start, end, len(usable))
    times = [
        start + timedelta(seconds=plan.interval_seconds * index)
        for index in range(plan.keyframes)
    ]
    sample = propagator.sample(times)

    return ConstellationScene(
        catalog=catalog,
        sample=sample,
        plan=plan,
        skipped_stale=tuple(skipped),
    )

