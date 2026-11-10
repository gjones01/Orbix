"""Multi-group CelesTrak catalogue: one request per group, de-duplicated.

Responsibilities
----------------
* Fetch each requested group **once** through :class:`~src.api.celestrak.CelestrakClient`
  (which owns the 2-hour cache, the snapshot fallback and the rate-limit handling).
* Merge the groups into a single catalogue keyed by **NORAD catalog ID**, preserving
  *every* group an object belongs to (some objects appear in more than one group).
* Record the outcome for each group independently - ``ok`` / ``cache`` / ``snapshot`` /
  ``failed`` - so the UI can show exactly what loaded. A failing group never silently
  falls back to a different group.

The catalogue is intentionally passive: no propagation happens here, and no API
request is made per satellite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from src.api.celestrak import CelestrakClient, CelestrakError, GpElement

logger = logging.getLogger(__name__)

__all__ = [
    "GROUP_SPECS",
    "Catalog",
    "GroupSpec",
    "GroupStatus",
    "Satellite",
    "load_catalog",
]


@dataclass(frozen=True)
class GroupSpec:
    """A CelesTrak group Orbix knows how to display."""

    id: str            #: CelesTrak GROUP identifier (lowercase, as published)
    label: str         #: human-readable name used in the UI
    colour: str        #: marker colour for this category
    note: str = ""     #: one-line description
    default_enabled: bool = True


#: The groups Orbix loads. Identifiers verified against
#: https://celestrak.org/NORAD/elements/ (47 advertised groups, 2026-11-04).
#:
#: Starlink is not enabled by default: at the time of writing it holds ~11,200
#: objects, which is more than the rest of the catalogue combined.
GROUP_SPECS: tuple[GroupSpec, ...] = (
    GroupSpec("stations", "Space stations", "#ffcc66",
              "Crewed stations and permanently docked modules"),
    GroupSpec("gps-ops", "GPS", "#6ee7b7", "Operational GPS constellation"),
    GroupSpec("weather", "Weather", "#ff9e64", "Meteorological and Earth-observation"),
    GroupSpec("oneweb", "OneWeb", "#c792ea", "OneWeb broadband constellation"),
    GroupSpec("starlink", "Starlink", "#58b6ff",
              "Large LEO broadband constellation (~11k objects)",
              default_enabled=True),
)

_BY_ID = {spec.id: spec for spec in GROUP_SPECS}


def spec_for(group_id: str) -> GroupSpec | None:
    """Look up a group specification by identifier (case-insensitive)."""
    return _BY_ID.get(group_id.strip().lower())


@dataclass(frozen=True)
class GroupStatus:
    """What happened when one group was loaded."""

    spec: GroupSpec
    source: str            #: "network" | "cache" | "snapshot" | "failed"
    #: Records returned by CelesTrak for this group, BEFORE cross-group de-duplication.
    #: ``Catalog.counts_by_group()`` reports the unique count instead; the two differ
    #: whenever an object belongs to more than one loaded group.
    records_fetched: int
    error: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the group produced records."""
        return self.source != "failed" and self.records_fetched > 0

    @property
    def label(self) -> str:
        """Human-readable status word for the UI."""
        return {
            "network": "downloaded",
            "cache": "cached",
            "snapshot": "snapshot",
            "failed": "failed",
        }.get(self.source, self.source)


@dataclass(frozen=True)
class Satellite:
    """One catalogued object, with every group it belongs to."""

    element: GpElement
    groups: frozenset[str]

    @property
    def norad_id(self) -> int:
        """NORAD catalog number - Orbix's primary object identifier."""
        return self.element.norad_cat_id

    @property
    def primary_group(self) -> str:
        """First group in :data:`GROUP_SPECS` order that contains this object."""
        for spec in GROUP_SPECS:
            if spec.id in self.groups:
                return spec.id
        return next(iter(sorted(self.groups)))


@dataclass(frozen=True)
class Catalog:
    """De-duplicated set of satellites plus per-group load status."""

    satellites: Mapping[int, Satellite]
    groups: tuple[GroupStatus, ...]
    retrieved_at: datetime

    def __len__(self) -> int:
        return len(self.satellites)

    @property
    def unique_count(self) -> int:
        """Number of unique NORAD IDs loaded."""
        return len(self.satellites)

    @property
    def failed_groups(self) -> tuple[GroupStatus, ...]:
        """Groups that produced no records."""
        return tuple(status for status in self.groups if not status.ok)

    def counts_by_group(self) -> dict[str, int]:
        """How many *unique* objects each group contributes."""
        counts: dict[str, int] = {spec.id: 0 for spec in GROUP_SPECS}
        for satellite in self.satellites.values():
            for group_id in satellite.groups:
                counts[group_id] = counts.get(group_id, 0) + 1
        return counts

    def in_groups(self, group_ids: Iterable[str]) -> tuple[Satellite, ...]:
        """Satellites belonging to any of ``group_ids`` (no duplicates)."""
        allowed = {group.lower() for group in group_ids}
        return tuple(
            sat for sat in self.satellites.values() if sat.groups & allowed
        )


def load_catalog(
    group_ids: Iterable[str] | None = None,
    *,
    offline: bool = False,
    force_refresh: bool = False,
    client: CelestrakClient | None = None,
) -> Catalog:
    """Load several CelesTrak groups and merge them into one de-duplicated set.

    One request per group (never one per satellite). Failures are isolated: a group
    that cannot be loaded is reported as ``failed`` with its error, and the remaining
    groups still load. No group is ever substituted for another.

    Parameters
    ----------
    group_ids:
        CelesTrak group identifiers. Defaults to every group in :data:`GROUP_SPECS`.
        Unknown identifiers are rejected rather than silently ignored.
    offline:
        Serve entirely from saved snapshots.
    force_refresh:
        Ask CelesTrak to re-download even inside the 2-hour window (it may answer
        HTTP 403 "not updated", which the client reports as a cache hit).
    """
    requested = (
        [spec.id for spec in GROUP_SPECS] if group_ids is None
        else [str(group).strip().lower() for group in group_ids]
    )
    unknown = [group for group in requested if group not in _BY_ID]
    if unknown:
        raise ValueError(
            "Unknown CelesTrak group(s): "
            + ", ".join(unknown)
            + ". Known groups: " + ", ".join(spec.id for spec in GROUP_SPECS)
        )

    api = client or CelestrakClient(offline=offline)
    statuses: list[GroupStatus] = []
    merged: dict[int, Satellite] = {}
    memberships: dict[int, set[str]] = {}
    elements: dict[int, GpElement] = {}

    for group_id in requested:
        spec = _BY_ID[group_id]
        try:
            result = api.fetch_by_group(group_id, refresh=force_refresh)
        except CelestrakError as exc:
            # Isolated failure: record it and keep going. Do not substitute a group.
            logger.warning("Group %s failed: %s: %s", group_id, type(exc).__name__, exc)
            statuses.append(
                GroupStatus(
                    spec=spec,
                    source="failed",
                    records_fetched=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        added = 0
        for element in result.elements:
            norad_id = element.norad_cat_id
            if norad_id not in elements:
                elements[norad_id] = element
                memberships[norad_id] = set()
                added += 1
            memberships[norad_id].add(group_id)

        statuses.append(
            GroupStatus(
                spec=spec,
                source=result.source,
                records_fetched=len(result.elements),
                notes=result.notes,
            )
        )
        logger.info(
            "Group %s: %d records (%s), %d new unique", group_id,
            len(result.elements), result.source, added,
        )

    for norad_id in sorted(elements):
        merged[norad_id] = Satellite(
            element=elements[norad_id], groups=frozenset(memberships[norad_id])
        )

    return Catalog(
        satellites=merged,
        groups=tuple(statuses),
        retrieved_at=datetime.now(timezone.utc),
    )

