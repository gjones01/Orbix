"""CelesTrak GP (General Perturbations) API client for Orbix.

Data source
-----------
CelesTrak's *Current Data (GP)* service, which republishes the 18th Space
Defense Squadron general-perturbation element sets as CCSDS Orbit Mean-elements
Messages (OMM) instead of legacy TLEs:

    Query syntax:   https://celestrak.org/NORAD/documentation/gp-data-formats.php
    Data browser:   https://celestrak.org/NORAD/elements/
    Usage policy:   https://celestrak.org/NORAD/documentation/gp-data-formats.php

Endpoint form::

    https://celestrak.org/NORAD/elements/gp.php?{QUERY}=VALUE&FORMAT=JSON

``{QUERY}`` is one of ``CATNR`` | ``INTDES`` | ``GROUP`` | ``NAME`` | ``SPECIAL``
and must be uppercase.  Orbix always requests ``FORMAT=JSON`` explicitly because
CelesTrak's default format switched to CSV in 2026, and only the JSON/OMM
formats support 6- and 9-digit catalog numbers.

Usage-policy constraints that shape this module
-----------------------------------------------
* GP data is refreshed **once every 2 hours**, so a local cache TTL of 2 hours
  is the polite maximum and is the default here.
* Since 2026-03-26 CelesTrak enforces *one download per update*: a repeated
  request for the same GROUP returns **HTTP 403** with the body
  ``"GP data has not updated since your last successful download of ..."``.
  Orbix treats that response as a cache hit, never as a hard failure, and never
  retries it.
* HTTP 301/403/404/500 responses must **stop** the caller rather than be retried
  (more than ~1000 error responses a day gets an IP firewalled).  This client
  performs no automatic retries for HTTP errors.
* Only the satellite groups/targets the app actually needs should be fetched,
  and every successful payload is persisted so it can be reused offline.

What this module deliberately does *not* do
-------------------------------------------
It never invents, interpolates, or substitutes orbital elements.  If neither the
network nor a saved snapshot can provide data, it raises an exception.
Coordinate propagation and 3D positions are out of scope here (see
``src/orbital/propagation.py``).

Typical use::

    client = CelestrakClient()
    result = client.fetch_by_catalog_number(25544)   # ISS (ZARYA)
    print(result.freshness().summary())
    iss = result.elements[0]
    print(iss.object_name, iss.epoch, iss.inclination)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import requests

logger = logging.getLogger(__name__)

__all__ = [
    "CelestrakClient",
    "CelestrakError",
    "CelestrakNetworkError",
    "CelestrakHTTPError",
    "CelestrakRateLimited",
    "CelestrakFormatError",
    "CelestrakEmptyResponse",
    "CelestrakNoDataAvailable",
    "Freshness",
    "GpElement",
    "GpFetchResult",
    "SnapshotRecord",
    "DEFAULT_CACHE_TTL",
    "DEFAULT_SNAPSHOT_DIR",
    "GP_ENDPOINT",
    "build_query_url",
    "element_from_omm",
    "load_snapshot",
    "parse_gp_payload",
    "parse_omm_epoch",
    "query_cache_key",
    "read_manifest",
    "write_manifest",
]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

GP_ENDPOINT = "https://celestrak.org/NORAD/elements/gp.php"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "data" / "snapshots"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

#: CelesTrak refreshes GP data every 2 hours; do not poll faster than that.
DEFAULT_CACHE_TTL = timedelta(hours=2)

#: (connect, read) timeouts in seconds.  CelesTrak can be slow under load.
DEFAULT_TIMEOUT: tuple[float, float] = (10.0, 30.0)

#: CelesTrak asks automated clients to identify themselves.
DEFAULT_USER_AGENT = "Orbix/0.1 (Streamlit orbital visualizer; educational project)"

#: OMM fields Orbix cannot propagate without.  ``OBJECT_NAME`` is intentionally
#: excluded: analyst objects in the 8xxxx catalog range have no name.
REQUIRED_OMM_FIELDS: tuple[str, ...] = (
    "NORAD_CAT_ID",
    "EPOCH",
    "MEAN_MOTION",
    "ECCENTRICITY",
    "INCLINATION",
    "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER",
    "MEAN_ANOMALY",
)

#: Substring of the HTTP 403 body CelesTrak returns for repeated downloads.
RATE_LIMIT_MARKER = "has not updated since your last successful download"

#: CelesTrak's own "OLDEST" table flag treats elements older than 3.5 days as
#: worth flagging, so Orbix surfaces the same threshold in its warnings.
STALE_ELEMENT_AGE = timedelta(days=3.5)

_UTC = timezone.utc
_TS_FORMAT = "%Y%m%dT%H%M%SZ"
_QUERY_PARAM_ORDER: tuple[str, ...] = ("CATNR", "INTDES", "GROUP", "NAME", "SPECIAL")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class CelestrakError(RuntimeError):
    """Base class for every recoverable CelesTrak failure."""


class CelestrakNetworkError(CelestrakError):
    """The endpoint could not be reached (DNS, TCP, TLS, or timeout)."""


class CelestrakHTTPError(CelestrakError):
    """CelesTrak answered with an unexpected HTTP status."""

    def __init__(self, status_code: int, message: str, *, url: str = "") -> None:
        super().__init__(f"HTTP {status_code} from CelesTrak: {message}".strip())
        self.status_code = status_code
        self.url = url


class CelestrakRateLimited(CelestrakHTTPError):
    """GP data has not updated since the last successful download (HTTP 403).

    This is CelesTrak's normal *one download per update* response.  It is not a
    failure of the data: the caller should keep using the copy it already has.
    """


class CelestrakFormatError(CelestrakError):
    """The response was not JSON, or not OMM-structured JSON."""


class CelestrakEmptyResponse(CelestrakError):
    """CelesTrak returned an empty element set for a valid query."""


class CelestrakNoDataAvailable(CelestrakError):
    """Neither a network fetch nor a saved snapshot could supply data."""


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


def _as_utc(moment: datetime) -> datetime:
    """Return ``moment`` as a timezone-aware UTC datetime (naive means UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=_UTC)
    return moment.astimezone(_UTC)


def parse_omm_epoch(value: str) -> datetime:
    """Parse an OMM ``EPOCH`` string into an aware UTC datetime.

    CelesTrak emits ISO-8601 without a zone designator, e.g.
    ``2026-11-04T08:52:07.404096``, which per the OMM standard is UTC.
    """
    if not isinstance(value, str) or not value.strip():
        raise CelestrakFormatError(f"OMM EPOCH is missing or not a string: {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CelestrakFormatError(f"Unparseable OMM EPOCH: {value!r}") from exc
    return _as_utc(parsed)


def _to_float(value: Any, field_name: str) -> float:
    """Coerce an OMM numeric field, rejecting NaN/Inf and non-numeric values."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CelestrakFormatError(
            f"OMM field {field_name} is not numeric: {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise CelestrakFormatError(f"OMM field {field_name} is not finite: {value!r}")
    return number


def _to_int(value: Any, field_name: str) -> int | None:
    """Coerce an OMM integer field, returning ``None`` for blank/null values."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise CelestrakFormatError(
            f"OMM field {field_name} is not an integer: {value!r}"
        ) from exc


@dataclass(frozen=True)
class GpElement:
    """One validated OMM element set, plus the raw CelesTrak record.

    Units follow the OMM standard: angles in degrees, ``mean_motion`` in
    revolutions per day, ``eccentricity`` dimensionless, ``bstar`` in 1/Earth
    radii, and ``epoch`` an aware UTC datetime.
    """

    object_name: str
    norad_cat_id: int
    epoch: datetime
    mean_motion: float
    eccentricity: float
    inclination: float
    ra_of_asc_node: float
    arg_of_pericenter: float
    mean_anomaly: float
    object_id: str | None = None
    ephemeris_type: int | None = None
    classification_type: str | None = None
    element_set_no: int | None = None
    rev_at_epoch: int | None = None
    bstar: float | None = None
    mean_motion_dot: float | None = None
    mean_motion_ddot: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    # -- derived orbital quantities (cheap, no propagation) ----------------- #

    @property
    def period_minutes(self) -> float | None:
        """Keplerian period in minutes derived from the mean motion."""
        if self.mean_motion <= 0:
            return None
        return 1440.0 / self.mean_motion

    @property
    def epoch_age(self) -> timedelta:
        """How old this element set is, relative to now (UTC)."""
        return datetime.now(_UTC) - self.epoch

    def age_at(self, moment: datetime) -> timedelta:
        """How old this element set is at an arbitrary UTC ``moment``."""
        return _as_utc(moment) - self.epoch

    def as_omm(self) -> dict[str, Any]:
        """Return a complete OMM dictionary suitable for ``EarthSatellite.from_omm``.

        Optional fields absent from the CelesTrak payload are filled with the
        CCSDS defaults rather than being dropped, because SGP4 ingestion expects
        all seventeen OMM keywords.
        """
        omm: dict[str, Any] = {
            "OBJECT_NAME": self.object_name,
            "OBJECT_ID": self.object_id or "",
            "EPOCH": self.epoch.strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "MEAN_MOTION": self.mean_motion,
            "ECCENTRICITY": self.eccentricity,
            "INCLINATION": self.inclination,
            "RA_OF_ASC_NODE": self.ra_of_asc_node,
            "ARG_OF_PERICENTER": self.arg_of_pericenter,
            "MEAN_ANOMALY": self.mean_anomaly,
            "EPHEMERIS_TYPE": self.ephemeris_type if self.ephemeris_type is not None else 0,
            "CLASSIFICATION_TYPE": self.classification_type or "U",
            "NORAD_CAT_ID": self.norad_cat_id,
            "ELEMENT_SET_NO": self.element_set_no if self.element_set_no is not None else 999,
            "REV_AT_EPOCH": self.rev_at_epoch if self.rev_at_epoch is not None else 0,
            "BSTAR": self.bstar if self.bstar is not None else 0.0,
            "MEAN_MOTION_DOT": self.mean_motion_dot if self.mean_motion_dot is not None else 0.0,
            "MEAN_MOTION_DDOT": self.mean_motion_ddot if self.mean_motion_ddot is not None else 0.0,
        }
        return omm


def element_from_omm(record: Mapping[str, Any]) -> GpElement:
    """Validate one raw CelesTrak OMM record and convert it to :class:`GpElement`.

    Raises :class:`CelestrakFormatError` if a required field is missing or
    physically impossible (e.g. a non-positive mean motion, which cannot be
    propagated at all).
    """
    if not isinstance(record, Mapping):
        raise CelestrakFormatError(
            f"Expected an OMM object, got {type(record).__name__}"
        )

    missing = [name for name in REQUIRED_OMM_FIELDS if name not in record]
    if missing:
        raise CelestrakFormatError(
            "OMM record is missing required field(s): " + ", ".join(missing)
        )

    norad_cat_id = _to_int(record.get("NORAD_CAT_ID"), "NORAD_CAT_ID")
    if norad_cat_id is None:
        raise CelestrakFormatError("OMM record has no usable NORAD_CAT_ID")

    raw_name = record.get("OBJECT_NAME")
    object_name = str(raw_name).strip() if raw_name is not None else ""
    if not object_name:
        object_name = f"CAT-{norad_cat_id}"

    mean_motion = _to_float(record.get("MEAN_MOTION"), "MEAN_MOTION")
    if mean_motion <= 0:
        raise CelestrakFormatError(
            f"OBJECT {norad_cat_id} has non-positive mean motion ({mean_motion}); "
            "SGP4 cannot propagate it"
        )

    eccentricity = _to_float(record.get("ECCENTRICITY"), "ECCENTRICITY")
    if not 0.0 <= eccentricity < 1.0:
        raise CelestrakFormatError(
            f"OBJECT {norad_cat_id} has eccentricity outside [0, 1): {eccentricity}"
        )

    def optional_float(name: str) -> float | None:
        if record.get(name) is None:
            return None
        return _to_float(record.get(name), name)

    return GpElement(
        object_name=object_name,
        norad_cat_id=norad_cat_id,
        epoch=parse_omm_epoch(record.get("EPOCH")),
        mean_motion=mean_motion,
        eccentricity=eccentricity,
        inclination=_to_float(record.get("INCLINATION"), "INCLINATION"),
        ra_of_asc_node=_to_float(record.get("RA_OF_ASC_NODE"), "RA_OF_ASC_NODE"),
        arg_of_pericenter=_to_float(
            record.get("ARG_OF_PERICENTER"), "ARG_OF_PERICENTER"
        ),
        mean_anomaly=_to_float(record.get("MEAN_ANOMALY"), "MEAN_ANOMALY"),
        object_id=(str(record["OBJECT_ID"]).strip() if record.get("OBJECT_ID") else None),
        ephemeris_type=_to_int(record.get("EPHEMERIS_TYPE"), "EPHEMERIS_TYPE"),
        classification_type=(
            str(record["CLASSIFICATION_TYPE"]).strip()
            if record.get("CLASSIFICATION_TYPE")
            else None
        ),
        element_set_no=_to_int(record.get("ELEMENT_SET_NO"), "ELEMENT_SET_NO"),
        rev_at_epoch=_to_int(record.get("REV_AT_EPOCH"), "REV_AT_EPOCH"),
        bstar=optional_float("BSTAR"),
        mean_motion_dot=optional_float("MEAN_MOTION_DOT"),
        mean_motion_ddot=optional_float("MEAN_MOTION_DDOT"),
        raw=dict(record),
    )


def parse_gp_payload(
    payload: Any,
    *,
    strict: bool = True,
) -> tuple[tuple[GpElement, ...], tuple[str, ...]]:
    """Convert a decoded CelesTrak JSON payload into validated elements.

    Parameters
    ----------
    payload:
        The decoded JSON value.  CelesTrak's JSON format is a list of OMM
        objects; a single-element list is used even for ``CATNR`` queries.
    strict:
        When ``True`` (single-object queries) any malformed record raises.
        When ``False`` (group queries) malformed records are skipped and
        reported through the returned warnings tuple, so one bad satellite
        cannot take down the whole group.

    Returns
    -------
    (elements, warnings)
    """
    if not isinstance(payload, list):
        raise CelestrakFormatError(
            "Expected a JSON array of OMM records, got "
            f"{type(payload).__name__}"
        )

    elements: list[GpElement] = []
    warnings: list[str] = []
    for index, record in enumerate(payload):
        try:
            elements.append(element_from_omm(record))
        except CelestrakFormatError as exc:
            if strict:
                raise CelestrakFormatError(
                    f"Invalid OMM record at index {index}: {exc}"
                ) from exc
            warnings.append(f"Skipped record {index}: {exc}")

    if not elements:
        raise CelestrakEmptyResponse(
            "CelesTrak returned no usable OMM records"
            + (f" ({len(warnings)} invalid record(s) skipped)" if warnings else "")
        )
    return tuple(elements), tuple(warnings)


# --------------------------------------------------------------------------- #
# Snapshot bookkeeping
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SnapshotRecord:
    """Metadata describing one saved CelesTrak response (an immutable snapshot)."""

    cache_key: str
    query: Mapping[str, str]
    url: str
    filename: str
    retrieved_at: datetime
    http_status: int
    record_count: int
    newest_epoch: datetime | None
    sha256: str

    def path(self, root: Path) -> Path:
        """Absolute location of the payload file inside snapshot directory ``root``."""
        return Path(root) / self.filename

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the JSON-friendly form stored in ``manifest.json``."""
        return {
            "cache_key": self.cache_key,
            "query": dict(self.query),
            "url": self.url,
            "filename": self.filename,
            "retrieved_at": self.retrieved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "http_status": self.http_status,
            "record_count": self.record_count,
            "newest_epoch": (
                self.newest_epoch.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                if self.newest_epoch
                else None
            ),
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, entry: Mapping[str, Any]) -> "SnapshotRecord":
        """Rebuild a record from a ``manifest.json`` entry."""
        newest = entry.get("newest_epoch")
        return cls(
            cache_key=str(entry["cache_key"]),
            query=dict(entry.get("query") or {}),
            url=str(entry.get("url", "")),
            filename=str(entry["filename"]),
            retrieved_at=parse_omm_epoch(str(entry["retrieved_at"])),
            http_status=int(entry.get("http_status", 200)),
            record_count=int(entry.get("record_count", 0)),
            newest_epoch=parse_omm_epoch(str(newest)) if newest else None,
            sha256=str(entry.get("sha256", "")),
        )


@dataclass(frozen=True)
class Freshness:
    """Human-readable data-freshness report for the UI.

    Two ages matter and they are different things:

    * ``retrieval_age`` - when Orbix last downloaded the element set.
    * ``epoch_age``     - when the elements themselves were fitted (OMM EPOCH).
      This is the number that determines propagation accuracy.
    """

    source: Literal["network", "cache", "snapshot"]
    retrieved_at: datetime
    retrieval_age: timedelta
    newest_epoch: datetime | None
    epoch_age: timedelta | None
    cache_ttl: timedelta
    note: str | None = None

    @property
    def is_cached(self) -> bool:
        """True when the data did not come from a fresh network download."""
        return self.source != "network"

    @property
    def elements_are_old(self) -> bool:
        """True when the element set is older than CelesTrak's 3.5-day flag."""
        return self.epoch_age is not None and self.epoch_age > STALE_ELEMENT_AGE

    def summary(self) -> str:
        """One-line description safe to show in the Streamlit UI."""
        origin = {
            "network": "live CelesTrak download",
            "cache": "local cache (within 2h refresh window)",
            "snapshot": "saved snapshot (CelesTrak not re-queried)",
        }[self.source]
        parts = [
            f"Source: {origin}",
            f"Retrieved {self.retrieved_at:%Y-%m-%d %H:%M:%S} UTC "
            f"({_humanise_delta(self.retrieval_age)} ago)",
        ]
        if self.epoch_age is not None and self.newest_epoch is not None:
            parts.append(
                f"Element epoch {self.newest_epoch:%Y-%m-%d %H:%M:%S} UTC "
                f"({_humanise_delta(self.epoch_age)} old)"
            )
            if self.elements_are_old:
                parts.append("WARNING: elements older than 3.5 days")
        if self.note:
            parts.append(self.note)
        return " | ".join(parts)


def _humanise_delta(delta: timedelta) -> str:
    """Format a timedelta compactly, e.g. ``45s``, ``7m``, ``3.4h``, ``2.1d``."""
    seconds = abs(delta.total_seconds())
    if seconds < 90:
        return f"{seconds:.0f}s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{minutes:.0f}m"
    hours = minutes / 60.0
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24.0:.1f}d"


@dataclass(frozen=True)
class GpFetchResult:
    """Outcome of a CelesTrak query, including provenance of the data."""

    elements: tuple[GpElement, ...]
    source: Literal["network", "cache", "snapshot"]
    retrieved_at: datetime
    url: str
    snapshot: SnapshotRecord | None = None
    http_status: int | None = None
    notes: tuple[str, ...] = ()
    cache_ttl: timedelta = DEFAULT_CACHE_TTL

    def __len__(self) -> int:
        return len(self.elements)

    def __iter__(self):
        return iter(self.elements)

    @property
    def by_catalog_number(self) -> dict[int, GpElement]:
        """Elements keyed by NORAD catalog number."""
        return {element.norad_cat_id: element for element in self.elements}

    @property
    def newest_epoch(self) -> datetime | None:
        """Most recent OMM epoch across all returned elements."""
        if not self.elements:
            return None
        return max(element.epoch for element in self.elements)

    def freshness(self, *, now: datetime | None = None) -> Freshness:
        """Build a :class:`Freshness` report describing this result."""
        moment = _as_utc(now) if now is not None else datetime.now(_UTC)
        newest = self.newest_epoch
        return Freshness(
            source=self.source,
            retrieved_at=self.retrieved_at,
            retrieval_age=moment - self.retrieved_at,
            newest_epoch=newest,
            epoch_age=(moment - newest) if newest else None,
            cache_ttl=self.cache_ttl,
            note="; ".join(self.notes) if self.notes else None,
        )


# --------------------------------------------------------------------------- #
# Manifest helpers (shared by the client and by offline tooling/tests)
# --------------------------------------------------------------------------- #

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    """Lowercase, filesystem-safe token used in snapshot filenames."""
    return _SLUG_RE.sub("-", str(value).strip().lower()).strip("-") or "value"


def query_cache_key(params: Mapping[str, str]) -> str:
    """Stable identifier for a query, e.g. ``catnr-25544`` or ``group-stations``."""
    parts = [
        f"{name.lower()}-{_slug(params[name])}"
        for name in _QUERY_PARAM_ORDER
        if name in params
    ]
    if not parts:
        raise ValueError(
            "CelesTrak query needs one of " + ", ".join(_QUERY_PARAM_ORDER)
        )
    return "_".join(parts)


def build_query_url(params: Mapping[str, str], endpoint: str = GP_ENDPOINT) -> str:
    """Build the full request URL, always pinning ``FORMAT=JSON``."""
    ordered = dict(params)
    ordered["FORMAT"] = "JSON"
    query = "&".join(f"{key}={value}" for key, value in ordered.items())
    return f"{endpoint}?{query}"


def _manifest_path(root: Path) -> Path:
    return Path(root) / MANIFEST_FILENAME


def read_manifest(root: Path) -> list[SnapshotRecord]:
    """Load every snapshot record from ``manifest.json``.

    A missing or unreadable manifest is not fatal: Orbix can still fall back to
    scanning the snapshot directory, so a corrupt index degrades rather than
    breaks.
    """
    path = _manifest_path(root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        logger.warning("Could not read snapshot manifest %s: %s", path, exc)
        return []

    entries = payload.get("snapshots", []) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        logger.warning("Ignoring malformed snapshot manifest list in %s", path)
        return []
    records: list[SnapshotRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Ignoring non-object snapshot manifest entry")
            continue
        try:
            records.append(SnapshotRecord.from_dict(entry))
        except (KeyError, TypeError, ValueError, CelestrakFormatError) as exc:
            logger.warning("Ignoring malformed manifest entry %r: %s", entry, exc)
    return records


def write_manifest(root: Path, records: Sequence[SnapshotRecord]) -> Path:
    """Atomically rewrite ``manifest.json``, newest record last."""
    path = _manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda rec: rec.retrieved_at)
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshots": [record.to_dict() for record in ordered],
    }
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def _scan_snapshot_dir(root: Path, cache_key: str) -> list[SnapshotRecord]:
    """Rebuild minimal records from ``{cache_key}_YYYYmmddTHHMMSSZ.json`` files.

    Used only when the manifest has no entry for a key, so that snapshots copied
    in by hand (or a rebuilt checkout) are still usable.
    """
    root = Path(root)
    if not root.exists():
        return []
    records: list[SnapshotRecord] = []
    for file_path in sorted(root.glob(f"{cache_key}_*.json")):
        stamp = file_path.stem[len(cache_key) + 1 :]
        try:
            retrieved_at = datetime.strptime(stamp, _TS_FORMAT).replace(tzinfo=_UTC)
        except ValueError:
            logger.debug("Ignoring snapshot with unparsable timestamp: %s", file_path)
            continue
        try:
            raw = file_path.read_bytes()
            elements, _ = parse_gp_payload(json.loads(raw), strict=False)
        except (OSError, json.JSONDecodeError, UnicodeError, CelestrakError) as exc:
            logger.warning("Ignoring unreadable snapshot %s: %s", file_path, exc)
            continue
        records.append(
            SnapshotRecord(
                cache_key=cache_key,
                query={},
                url="",
                filename=file_path.name,
                retrieved_at=retrieved_at,
                http_status=200,
                record_count=len(elements),
                newest_epoch=max(element.epoch for element in elements),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    return records


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class CelestrakClient:
    """Fetches GP element sets from CelesTrak with caching and snapshot fallback.

    Behaviour on :meth:`fetch`:

    1. If a saved snapshot for the same query is younger than ``cache_ttl``
       (default 2 h, matching CelesTrak's refresh cadence) it is used directly -
       no network traffic at all.
    2. Otherwise the query is attempted.  A successful response is written to
       ``snapshot_dir`` and indexed in ``manifest.json``.
    3. If the network fails, or CelesTrak replies "not updated since your last
       successful download" (HTTP 403), the newest saved snapshot is returned
       with ``source="snapshot"`` and an explicit note.  Set
       ``allow_stale=False`` to raise instead.
    4. If there is no snapshot either, an exception is raised.  Orbix never
       fabricates element sets.

    Parameters
    ----------
    snapshot_dir:
        Directory holding ``manifest.json`` and the raw ``*.json`` payloads.
        Defaults to ``<repo>/data/snapshots``.
    cache_ttl:
        How long a saved snapshot stays authoritative.
    timeout:
        ``requests`` timeout as ``(connect, read)`` seconds.
    user_agent:
        Identifying UA string; overridable via the ``ORBIX_USER_AGENT`` env var.
    offline:
        Never touch the network; serve snapshots only.
    session:
        Optional pre-built ``requests.Session`` (used by tests).
    clock:
        Optional callable returning "now" (used by tests).
    """

    def __init__(
        self,
        *,
        snapshot_dir: str | Path | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
        offline: bool = False,
        session: requests.Session | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else DEFAULT_SNAPSHOT_DIR
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self.user_agent = (
            user_agent or os.environ.get("ORBIX_USER_AGENT") or DEFAULT_USER_AGENT
        )
        self.offline = offline
        self._clock = clock or (lambda: datetime.now(_UTC))
        if session is None:
            session = requests.Session()
            session.headers.update(
                {"User-Agent": self.user_agent, "Accept": "application/json"}
            )
        self._session = session

    # -- public API -------------------------------------------------------- #

    def fetch_by_catalog_number(
        self,
        norad_cat_id: int,
        *,
        refresh: bool = False,
        allow_stale: bool = True,
    ) -> GpFetchResult:
        """Fetch the element set for a single satellite, e.g. ISS ``25544``."""
        if int(norad_cat_id) <= 0:
            raise ValueError(f"NORAD catalog number must be positive: {norad_cat_id}")
        return self.fetch(
            {"CATNR": str(int(norad_cat_id))},
            strict=True,
            refresh=refresh,
            allow_stale=allow_stale,
        )

    def fetch_by_group(
        self,
        group: str,
        *,
        refresh: bool = False,
        allow_stale: bool = True,
    ) -> GpFetchResult:
        """Fetch a CelesTrak group such as ``STATIONS`` or ``GPS-OPS``."""
        group_name = str(group).strip().upper()
        if not group_name:
            raise ValueError("Group name must not be empty")
        return self.fetch(
            {"GROUP": group_name},
            strict=False,
            refresh=refresh,
            allow_stale=allow_stale,
        )

    def fetch(
        self,
        params: Mapping[str, str],
        *,
        strict: bool = True,
        refresh: bool = False,
        allow_stale: bool = True,
    ) -> GpFetchResult:
        """Run one GP query and return validated elements with full provenance.

        ``params`` must contain exactly one CelesTrak query selector
        (``CATNR``/``GROUP``/...); ``FORMAT`` is always forced to ``JSON``.
        """
        clean = {
            key.upper(): str(value).strip()
            for key, value in params.items()
            if key.upper() != "FORMAT"
        }
        cache_key = query_cache_key(clean)
        url = build_query_url(clean)
        cached = self.latest_snapshot(cache_key)

        # 1. Offline mode never touches the network and always reports snapshots.
        if self.offline:
            if cached is None:
                raise CelestrakNoDataAvailable(
                    f"Offline mode is enabled and no snapshot exists for query "
                    f"'{cache_key}' in {self.snapshot_dir}"
                )
            result = self._try_snapshot(
                cached,
                source="snapshot",
                strict=strict,
                notes=("Offline mode: network access disabled.",),
            )
            if result is None:
                result = self._recover_snapshot(cache_key, strict=strict)
            if result is None:
                raise CelestrakNoDataAvailable(
                    f"Offline mode is enabled and snapshot '{cached.filename}' is unreadable"
                )
            return result

        # 2. Serve from cache while it is still authoritative.
        if cached is not None and not refresh:
            age = self._now() - cached.retrieved_at
            if age < self.cache_ttl:
                result = self._try_snapshot(cached, source="cache", strict=strict)
                if result is not None:
                    logger.debug(
                        "Cache hit for %s (age %s)", cache_key, _humanise_delta(age)
                    )
                    return result

        # 3. Try the network, falling back to the newest snapshot.
        try:
            return self._download(clean, cache_key, url, strict=strict)
        except CelestrakRateLimited:
            # CelesTrak enforcing one-download-per-update is not a failure.
            result = self._fallback_to_snapshot(
                cached,
                strict=strict,
                allow_stale=allow_stale,
                note="CelesTrak reports no new GP data since the last download.",
            )
            if result is None:
                raise
            logger.info(
                "CelesTrak rate limit for %s; reusing snapshot %s",
                cache_key,
                cached.filename,
            )
            return result
        except CelestrakError as exc:
            result = self._fallback_to_snapshot(
                cached,
                strict=strict,
                allow_stale=allow_stale,
                note=f"{type(exc).__name__}: {exc}",
            )
            if result is None:
                raise
            logger.warning(
                "CelesTrak unavailable for %s (%s); using snapshot %s",
                cache_key,
                exc,
                cached.filename,
            )
            return result

    def _fallback_to_snapshot(
        self,
        cached: SnapshotRecord | None,
        *,
        strict: bool,
        allow_stale: bool,
        note: str,
    ) -> GpFetchResult | None:
        """Serve ``cached`` after a failed request, or ``None`` when not allowed."""
        if cached is None or not allow_stale:
            return None
        return self._try_snapshot(
            cached, source="snapshot", strict=strict, notes=(note,)
        ) or self._recover_snapshot(cached.cache_key, strict=strict)

    def _recover_snapshot(self, cache_key: str, *, strict: bool) -> GpFetchResult | None:
        """A damaged indexed file must not hide usable older saved data."""
        for record in sorted(_scan_snapshot_dir(self.snapshot_dir, cache_key),
                             key=lambda rec: rec.retrieved_at, reverse=True):
            result = self._try_snapshot(record, source="snapshot", strict=strict,
                                        notes=("Recovered saved data after an unreadable snapshot.",))
            if result is not None:
                return result
        return None

    # -- snapshot access --------------------------------------------------- #

    def list_snapshots(self) -> list[SnapshotRecord]:
        """Every snapshot known to the manifest, oldest first."""
        records = read_manifest(self.snapshot_dir)
        return sorted(records, key=lambda rec: rec.retrieved_at)

    def latest_snapshot(self, cache_key: str) -> SnapshotRecord | None:
        """Newest usable snapshot for ``cache_key``, or ``None``."""
        records = [
            rec
            for rec in read_manifest(self.snapshot_dir)
            if rec.cache_key == cache_key
        ]
        if not records:
            records = _scan_snapshot_dir(self.snapshot_dir, cache_key)
        existing = [rec for rec in records if rec.path(self.snapshot_dir).exists()]
        if not existing:
            return None
        return max(existing, key=lambda rec: rec.retrieved_at)

    # -- internals --------------------------------------------------------- #

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    def _download(
        self,
        params: Mapping[str, str],
        cache_key: str,
        url: str,
        *,
        strict: bool,
    ) -> GpFetchResult:
        """Perform the HTTP request, validate it, and snapshot the payload."""
        logger.info("GET %s", url)
        try:
            response = self._session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.Timeout as exc:
            raise CelestrakNetworkError(
                f"Request to {url} timed out after {self.timeout} s"
            ) from exc
        except requests.RequestException as exc:
            raise CelestrakNetworkError(f"Request to {url} failed: {exc}") from exc

        status = int(response.status_code)
        body = response.text or ""

        if status == 403 and RATE_LIMIT_MARKER in body.lower():
            raise CelestrakRateLimited(403, _collapse_text(body), url=url)
        if status != 200:
            # CelesTrak explicitly asks clients not to retry 4xx/5xx responses.
            raise CelestrakHTTPError(status, _collapse_text(body)[:300], url=url)

        content_type = str(response.headers.get("Content-Type", ""))
        if "json" not in content_type.lower():
            raise CelestrakFormatError(
                f"CelesTrak returned Content-Type {content_type!r} instead of JSON "
                f"(body starts: {body[:160]!r})"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CelestrakFormatError(
                f"CelesTrak response was not valid JSON: {exc}"
            ) from exc

        elements, warnings = parse_gp_payload(payload, strict=strict)

        raw_bytes = getattr(response, "content", None)
        if not raw_bytes:
            raw_bytes = body.encode("utf-8")
        record = self._save_snapshot(
            cache_key=cache_key,
            params=params,
            url=url,
            raw=raw_bytes,
            elements=elements,
            http_status=status,
        )
        if record is None:
            warnings = warnings + ("Snapshot could not be written to disk.",)

        return GpFetchResult(
            elements=elements,
            source="network",
            retrieved_at=record.retrieved_at if record else self._now(),
            url=url,
            snapshot=record,
            http_status=status,
            notes=warnings,
            cache_ttl=self.cache_ttl,
        )

    def _try_snapshot(
        self,
        record: SnapshotRecord,
        *,
        source: Literal["cache", "snapshot"],
        strict: bool,
        notes: Sequence[str] = (),
    ) -> GpFetchResult | None:
        """Load ``record`` from disk, returning ``None`` if it is unreadable."""
        path = record.path(self.snapshot_dir)
        try:
            elements, warnings = parse_gp_payload(
                json.loads(path.read_bytes()), strict=strict
            )
        except (OSError, json.JSONDecodeError, UnicodeError, CelestrakError) as exc:
            logger.warning("Snapshot %s is unusable: %s", path, exc)
            return None
        return GpFetchResult(
            elements=elements,
            source=source,
            retrieved_at=record.retrieved_at,
            url=record.url,
            snapshot=record,
            http_status=record.http_status,
            notes=tuple(notes) + warnings,
            cache_ttl=self.cache_ttl,
        )

    def _save_snapshot(
        self,
        *,
        cache_key: str,
        params: Mapping[str, str],
        url: str,
        raw: bytes,
        elements: Sequence[GpElement],
        http_status: int,
    ) -> SnapshotRecord | None:
        """Persist the raw payload verbatim and index it in the manifest."""
        retrieved_at = self._now()
        stamp = retrieved_at.strftime(_TS_FORMAT)
        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{cache_key}_{stamp}.json"
            path = self.snapshot_dir / filename
            suffix = 2
            while path.exists():
                filename = f"{cache_key}_{stamp}-{suffix}.json"
                path = self.snapshot_dir / filename
                suffix += 1
            path.write_bytes(raw)
        except OSError as exc:
            logger.warning("Could not write snapshot for %s: %s", cache_key, exc)
            return None

        record = SnapshotRecord(
            cache_key=cache_key,
            query=dict(params),
            url=url,
            filename=filename,
            retrieved_at=retrieved_at,
            http_status=http_status,
            record_count=len(elements),
            newest_epoch=max((element.epoch for element in elements), default=None),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        try:
            records = [
                rec for rec in read_manifest(self.snapshot_dir) if rec.filename != filename
            ]
            records.append(record)
            write_manifest(self.snapshot_dir, records)
        except OSError as exc:
            logger.warning("Could not update snapshot manifest: %s", exc)
        logger.info("Saved snapshot %s (%d record(s))", filename, record.record_count)
        return record


def _collapse_text(text: str, limit: int = 400) -> str:
    """Flatten an HTML/text error body into a single short line."""
    stripped = re.sub(r"(?s)<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", stripped).strip()[:limit]


def load_snapshot(
    path: str | Path,
    *,
    strict: bool = False,
    cache_ttl: timedelta = DEFAULT_CACHE_TTL,
) -> GpFetchResult:
    """Load a previously saved snapshot file without any network access.

    Useful for reproducible analysis, offline demos, and tests.  The file must be
    a raw CelesTrak GP JSON payload, i.e. exactly what
    :meth:`CelestrakClient.fetch` writes to ``data/snapshots``.
    """
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise CelestrakNoDataAvailable(
            f"Snapshot {file_path} is unreadable: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CelestrakFormatError(
            f"Snapshot {file_path} is not valid JSON: {exc}"
        ) from exc

    elements, warnings = parse_gp_payload(payload, strict=strict)
    recorded_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=_UTC)
    record = SnapshotRecord(
        cache_key=f"snapshot-{_slug(file_path.stem)}",
        query={},
        url=file_path.resolve().as_uri(),
        filename=file_path.name,
        retrieved_at=recorded_at,
        http_status=200,
        record_count=len(elements),
        newest_epoch=max(element.epoch for element in elements),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    return GpFetchResult(
        elements=elements,
        source="snapshot",
        retrieved_at=recorded_at,
        url=record.url,
        snapshot=record,
        http_status=None,
        notes=("Loaded directly from a saved snapshot file.",) + warnings,
        cache_ttl=cache_ttl,
    )











