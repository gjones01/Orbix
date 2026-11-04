"""Tests for the CelesTrak GP (OMM/JSON) API client.

Every test is hermetic: no network access, no writes outside ``tmp_path``.  The
element sets come from the genuine payload in ``tests/fixtures/iss_gp_25544.json``
(ISS, captured 2026-11-04T19:54Z).  Records that are deliberately malformed are
built inside individual tests and labelled as such - they are never presented as
real orbital data.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from src.api.celestrak import (
    DEFAULT_CACHE_TTL,
    CelestrakClient,
    CelestrakEmptyResponse,
    CelestrakError,
    CelestrakFormatError,
    CelestrakHTTPError,
    CelestrakNetworkError,
    CelestrakNoDataAvailable,
    CelestrakRateLimited,
    build_query_url,
    element_from_omm,
    load_snapshot,
    parse_gp_payload,
    parse_omm_epoch,
    query_cache_key,
)

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: object | None = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        default_text = "" if payload is None else json.dumps(payload)
        self.text = text if text is not None else default_text
        self.headers = headers if headers is not None else {
            "Content-Type": "application/json; charset=UTF-8"
        }
        self.content = content if content is not None else self.text.encode("utf-8")
        self.url = ""

    def json(self):
        if self._payload is None:
            raise ValueError("Response is not JSON")
        return self._payload


class FakeSession:
    """Scripted ``requests.Session`` replacement that records every call.

    Each queued item is either a :class:`FakeResponse` or an exception instance to
    raise.  Calling ``get`` with an empty queue fails loudly, which is how the
    "no network traffic" assertions are enforced.
    """

    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"Unexpected HTTP request: {url}")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    """Controllable clock so cache TTL behaviour is testable."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        self.now += delta
        return self.now


START = datetime(2026, 11, 4, 20, 0, 0, tzinfo=UTC)


def make_client(tmp_path: Path, session: FakeSession, clock: FakeClock, **kwargs):
    return CelestrakClient(
        snapshot_dir=tmp_path, session=session, clock=clock, **kwargs
    )


# --------------------------------------------------------------------------- #
# URL / cache-key helpers
# --------------------------------------------------------------------------- #


def test_build_query_url_pins_json_format():
    url = build_query_url({"CATNR": "25544"})
    assert url == (
        "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=JSON"
    )


def test_build_query_url_overrides_caller_format():
    # FORMAT=CSV must never leak through: CelesTrak's default is CSV since 2026.
    url = build_query_url({"GROUP": "STATIONS", "FORMAT": "CSV"})
    assert url.endswith("GROUP=STATIONS&FORMAT=JSON")


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"CATNR": "25544"}, "catnr-25544"),
        ({"GROUP": "STATIONS"}, "group-stations"),
        ({"GROUP": "gps-ops"}, "group-gps-ops"),
        ({"NAME": "ISS (ZARYA)"}, "name-iss-zarya"),
    ],
)
def test_query_cache_key(params, expected):
    assert query_cache_key(params) == expected


def test_query_cache_key_requires_a_selector():
    with pytest.raises(ValueError):
        query_cache_key({"FORMAT": "JSON"})


# --------------------------------------------------------------------------- #
# Parsing and validation
# --------------------------------------------------------------------------- #


def test_parse_epoch_treats_celestrak_timestamps_as_utc():
    parsed = parse_omm_epoch("2026-11-04T08:52:07.404096")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert (parsed.year, parsed.month, parsed.day) == (2026, 11, 4)
    assert parsed.hour == 8 and parsed.minute == 52 and parsed.second == 7


@pytest.mark.parametrize("value", ["", "not-a-date", None])
def test_parse_epoch_rejects_garbage(value):
    with pytest.raises(CelestrakFormatError):
        parse_omm_epoch(value)


def test_element_from_omm_maps_real_iss_record(iss_payload):
    element = element_from_omm(iss_payload[0])

    assert element.object_name == "ISS (ZARYA)"
    assert element.norad_cat_id == 25544
    assert element.object_id == "1998-067A"
    assert element.epoch == datetime(2026, 11, 4, 8, 52, 7, 404096, tzinfo=UTC)
    assert element.mean_motion == pytest.approx(15.49173979)
    assert element.eccentricity == pytest.approx(0.0007186)
    assert element.inclination == pytest.approx(51.6329)
    assert element.ra_of_asc_node == pytest.approx(326.3987)
    assert element.arg_of_pericenter == pytest.approx(346.7494)
    assert element.mean_anomaly == pytest.approx(13.3304)
    assert element.bstar == pytest.approx(0.00010236)
    assert element.rev_at_epoch == 59348
    # ISS orbital period is famously ~92.9 minutes.
    assert element.period_minutes == pytest.approx(92.953, abs=0.01)
    assert element.raw["OBJECT_NAME"] == "ISS (ZARYA)"


def test_element_as_omm_provides_all_seventeen_keywords(iss_payload):
    element = element_from_omm(iss_payload[0])
    omm = element.as_omm()
    expected = {
        "OBJECT_NAME",
        "OBJECT_ID",
        "EPOCH",
        "MEAN_MOTION",
        "ECCENTRICITY",
        "INCLINATION",
        "RA_OF_ASC_NODE",
        "ARG_OF_PERICENTER",
        "MEAN_ANOMALY",
        "EPHEMERIS_TYPE",
        "CLASSIFICATION_TYPE",
        "NORAD_CAT_ID",
        "ELEMENT_SET_NO",
        "REV_AT_EPOCH",
        "BSTAR",
        "MEAN_MOTION_DOT",
        "MEAN_MOTION_DDOT",
    }
    assert expected <= set(omm)
    assert parse_omm_epoch(omm["EPOCH"]) == element.epoch


def test_element_as_omm_fills_missing_optional_fields(iss_payload):
    record = dict(iss_payload[0])
    for key in ("BSTAR", "REV_AT_EPOCH", "CLASSIFICATION_TYPE", "OBJECT_ID"):
        record.pop(key)
    omm = element_from_omm(record).as_omm()
    assert omm["BSTAR"] == 0.0
    assert omm["REV_AT_EPOCH"] == 0
    assert omm["CLASSIFICATION_TYPE"] == "U"
    assert omm["OBJECT_ID"] == ""


def test_missing_required_field_is_rejected(iss_payload):
    record = dict(iss_payload[0])
    del record["INCLINATION"]
    with pytest.raises(CelestrakFormatError, match="INCLINATION"):
        element_from_omm(record)


@pytest.mark.parametrize("bad_motion", [0, -15.5, "abc", None, float("nan")])
def test_unpropagatable_mean_motion_is_rejected(iss_payload, bad_motion):
    record = dict(iss_payload[0])
    record["MEAN_MOTION"] = bad_motion
    with pytest.raises(CelestrakFormatError):
        element_from_omm(record)


@pytest.mark.parametrize("bad_ecc", [1.0, -0.1, 1.5])
def test_impossible_eccentricity_is_rejected(iss_payload, bad_ecc):
    record = dict(iss_payload[0])
    record["ECCENTRICITY"] = bad_ecc
    with pytest.raises(CelestrakFormatError):
        element_from_omm(record)


def test_unnamed_analyst_object_gets_catalog_fallback_name(iss_payload):
    record = dict(iss_payload[0])
    record["OBJECT_NAME"] = None
    assert element_from_omm(record).object_name == "CAT-25544"


def test_parse_payload_rejects_non_array_json():
    with pytest.raises(CelestrakFormatError, match="array"):
        parse_gp_payload({"error": "no such satellite"})


def test_parse_payload_rejects_empty_array():
    with pytest.raises(CelestrakEmptyResponse):
        parse_gp_payload([])


def test_strict_parsing_raises_on_first_bad_record(iss_payload):
    # Synthetic malformed copy: deliberately broken for validation testing only.
    broken = dict(iss_payload[0])
    broken["NORAD_CAT_ID"] = None
    with pytest.raises(CelestrakFormatError, match="index 0"):
        parse_gp_payload([broken], strict=True)


def test_lenient_parsing_skips_bad_records_and_reports_them(iss_payload):
    # Second record is a deliberately broken copy used to prove group queries
    # survive a single unusable satellite instead of failing outright.
    broken = dict(iss_payload[0])
    broken["ECCENTRICITY"] = "not-a-number"
    elements, warnings = parse_gp_payload(
        [iss_payload[0], broken], strict=False
    )
    assert len(elements) == 1
    assert len(warnings) == 1
    assert "Skipped record 1" in warnings[0]


# --------------------------------------------------------------------------- #
# Network fetch + snapshot persistence
# --------------------------------------------------------------------------- #


def test_fetch_persists_payload_and_manifest(tmp_path, iss_payload, iss_payload_bytes):
    session = FakeSession(
        [FakeResponse(payload=iss_payload, content=iss_payload_bytes)]
    )
    client = make_client(tmp_path, session, FakeClock(START))

    result = client.fetch_by_catalog_number(25544)

    assert result.source == "network"
    assert result.http_status == 200
    assert len(result.elements) == 1
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=JSON"
    assert kwargs["timeout"] == client.timeout

    payload_files = list(tmp_path.glob("catnr-25544_*.json"))
    assert len(payload_files) == 1
    assert payload_files[0].read_bytes() == iss_payload_bytes

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    entry = manifest["snapshots"][0]
    assert entry["cache_key"] == "catnr-25544"
    assert entry["record_count"] == 1
    assert entry["retrieved_at"] == "2026-11-04T20:00:00Z"
    assert entry["newest_epoch"].startswith("2026-11-04T08:52:07")
    assert entry["sha256"] == hashlib.sha256(iss_payload_bytes).hexdigest()


def test_second_fetch_within_ttl_uses_cache_and_no_network(tmp_path, iss_payload):
    session = FakeSession([FakeResponse(payload=iss_payload)])
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    first = client.fetch_by_catalog_number(25544)
    clock.advance(DEFAULT_CACHE_TTL - timedelta(minutes=1))
    second = client.fetch_by_catalog_number(25544)

    assert first.source == "network"
    assert second.source == "cache"
    assert len(session.calls) == 1  # the cached read performed no HTTP request
    assert second.elements[0].norad_cat_id == 25544


def test_fetch_after_ttl_refreshes_from_network(tmp_path, iss_payload):
    session = FakeSession(
        [FakeResponse(payload=iss_payload), FakeResponse(payload=iss_payload)]
    )
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(DEFAULT_CACHE_TTL + timedelta(minutes=1))
    refreshed = client.fetch_by_catalog_number(25544)

    assert refreshed.source == "network"
    assert len(session.calls) == 2
    assert len(list(tmp_path.glob("catnr-25544_*.json"))) == 2


def test_refresh_true_bypasses_fresh_cache(tmp_path, iss_payload):
    session = FakeSession(
        [FakeResponse(payload=iss_payload), FakeResponse(payload=iss_payload)]
    )
    client = make_client(tmp_path, session, FakeClock(START))

    client.fetch_by_catalog_number(25544)
    forced = client.fetch_by_catalog_number(25544, refresh=True)

    assert forced.source == "network"
    assert len(session.calls) == 2


def test_group_query_is_lenient_and_labelled(tmp_path, iss_payload):
    gps_like = dict(iss_payload[0])
    gps_like.update({"OBJECT_NAME": "GPS BIIR-2  (PRN 13)", "NORAD_CAT_ID": 24876})
    # Deliberately broken copy: proves a group query survives one bad record.
    broken = dict(iss_payload[0])
    broken.update({"NORAD_CAT_ID": 99999, "ECCENTRICITY": "bad"})

    session = FakeSession([FakeResponse(payload=[iss_payload[0], gps_like, broken])])
    client = make_client(tmp_path, session, FakeClock(START))

    result = client.fetch_by_group("stations")

    assert len(result.elements) == 2
    assert len(result.notes) == 1
    assert set(result.by_catalog_number) == {25544, 24876}
    assert session.calls[0][0].endswith("GROUP=STATIONS&FORMAT=JSON")


# --------------------------------------------------------------------------- #
# Failure handling: no retries, explicit fallbacks, never fabricated data
# --------------------------------------------------------------------------- #


def test_rate_limited_403_reuses_snapshot_instead_of_failing(tmp_path, iss_payload):
    rate_limited_body = (
        "GP data has not updated since your last successful download of "
        "GROUP=active at 2026-11-04T08:10:22 UTC. Data is updated once every 2 hours."
    )
    session = FakeSession(
        [
            FakeResponse(payload=iss_payload),
            FakeResponse(
                status_code=403,
                text=rate_limited_body,
                headers={"Content-Type": "text/html"},
            ),
        ]
    )
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(DEFAULT_CACHE_TTL + timedelta(minutes=5))
    result = client.fetch_by_catalog_number(25544)

    assert result.source == "snapshot"
    assert len(session.calls) == 2  # a third call would violate the usage policy
    assert any("no new GP data" in note for note in result.notes)
    assert result.elements[0].object_name == "ISS (ZARYA)"


def test_forbidden_without_rate_limit_marker_is_an_http_error(tmp_path):
    session = FakeSession(
        [
            FakeResponse(
                status_code=403,
                text="<html>Access denied by firewall</html>",
                headers={"Content-Type": "text/html"},
            )
        ]
    )
    client = make_client(tmp_path, session, FakeClock(START))

    with pytest.raises(CelestrakHTTPError) as excinfo:
        client.fetch_by_catalog_number(25544)
    assert excinfo.value.status_code == 403
    assert len(session.calls) == 1


def test_http_404_is_not_retried(tmp_path):
    session = FakeSession(
        [
            FakeResponse(
                status_code=404,
                text="No GP data found",
                headers={"Content-Type": "text/plain"},
            )
        ]
    )
    client = make_client(tmp_path, session, FakeClock(START))

    with pytest.raises(CelestrakHTTPError):
        client.fetch_by_catalog_number(123456789)
    assert len(session.calls) == 1


def test_network_error_falls_back_to_newest_snapshot(tmp_path, iss_payload):
    session = FakeSession(
        [
            FakeResponse(payload=iss_payload),
            requests.ConnectionError("network unreachable"),
        ]
    )
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(DEFAULT_CACHE_TTL + timedelta(hours=1))
    result = client.fetch_by_catalog_number(25544)

    assert result.source == "snapshot"
    assert any("CelestrakNetworkError" in note for note in result.notes)
    assert result.freshness().is_cached is True


def test_timeout_falls_back_to_snapshot(tmp_path, iss_payload):
    session = FakeSession(
        [FakeResponse(payload=iss_payload), requests.Timeout("read timed out")]
    )
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(DEFAULT_CACHE_TTL + timedelta(minutes=1))
    assert client.fetch_by_catalog_number(25544).source == "snapshot"


def test_network_error_without_snapshot_raises(tmp_path):
    session = FakeSession([requests.ConnectionError("network unreachable")])
    client = make_client(tmp_path, session, FakeClock(START))

    with pytest.raises(CelestrakError):
        client.fetch_by_catalog_number(25544)


def test_allow_stale_false_propagates_network_error(tmp_path, iss_payload):
    session = FakeSession(
        [FakeResponse(payload=iss_payload), requests.ConnectionError("down")]
    )
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(DEFAULT_CACHE_TTL + timedelta(minutes=1))

    with pytest.raises(CelestrakNetworkError):
        client.fetch_by_catalog_number(25544, allow_stale=False)


def test_non_json_content_type_is_rejected(tmp_path, iss_payload):
    session = FakeSession(
        [
            FakeResponse(
                payload=iss_payload,
                headers={"Content-Type": "text/csv; charset=UTF-8"},
            )
        ]
    )
    client = make_client(tmp_path, session, FakeClock(START))

    with pytest.raises(CelestrakFormatError, match="Content-Type"):
        client.fetch_by_catalog_number(25544)


def test_empty_response_falls_back_to_snapshot(tmp_path, iss_payload):
    session = FakeSession(
        [FakeResponse(payload=iss_payload), FakeResponse(payload=[])]
    )
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(DEFAULT_CACHE_TTL + timedelta(minutes=1))
    result = client.fetch_by_catalog_number(25544)

    assert result.source == "snapshot"
    assert any("CelestrakEmptyResponse" in note for note in result.notes)


def test_invalid_norad_id_is_rejected_before_any_request(tmp_path):
    session = FakeSession([])
    client = make_client(tmp_path, session, FakeClock(START))
    with pytest.raises(ValueError):
        client.fetch_by_catalog_number(0)
    assert session.calls == []


# --------------------------------------------------------------------------- #
# Offline mode, saved-snapshot loading, manifest recovery
# --------------------------------------------------------------------------- #


def test_offline_mode_serves_snapshot_without_network(tmp_path, iss_payload):
    session = FakeSession([FakeResponse(payload=iss_payload)])
    client = make_client(tmp_path, session, FakeClock(START))
    client.fetch_by_catalog_number(25544)

    offline_client = make_client(tmp_path, FakeSession([]), FakeClock(START), offline=True)
    result = offline_client.fetch_by_catalog_number(25544)

    assert result.source == "snapshot"
    assert any("Offline mode" in note for note in result.notes)
    assert result.elements[0].object_name == "ISS (ZARYA)"


def test_offline_mode_without_snapshot_raises(tmp_path):
    client = make_client(tmp_path, FakeSession([]), FakeClock(START), offline=True)
    with pytest.raises(CelestrakNoDataAvailable):
        client.fetch_by_catalog_number(25544)


def test_load_snapshot_restores_saved_file(tmp_path, iss_payload):
    path = tmp_path / "catnr-25544_20261104T120000Z.json"
    path.write_text(json.dumps(iss_payload), encoding="utf-8")

    result = load_snapshot(path)

    assert result.source == "snapshot"
    assert len(result.elements) == 1
    assert result.elements[0].object_name == "ISS (ZARYA)"
    assert any("saved snapshot" in note for note in result.notes)
    assert result.snapshot is not None
    assert result.snapshot.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_snapshot_missing_file_raises(tmp_path):
    with pytest.raises(CelestrakNoDataAvailable):
        load_snapshot(tmp_path / "nope.json")


def test_manifest_can_be_rebuilt_from_snapshot_directory(tmp_path, iss_payload):
    # A checkout that has payload files but no manifest must still work.
    (tmp_path / "catnr-25544_20261104T120000Z.json").write_text(
        json.dumps(iss_payload), encoding="utf-8"
    )
    assert not (tmp_path / "manifest.json").exists()

    client = make_client(tmp_path, FakeSession([]), FakeClock(START), offline=True)
    result = client.fetch_by_catalog_number(25544)

    assert result.source == "snapshot"
    assert result.retrieved_at == datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)


def test_manifest_entries_are_ordered_newest_last(tmp_path, iss_payload):
    session = FakeSession(
        [FakeResponse(payload=iss_payload), FakeResponse(payload=iss_payload)]
    )
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(timedelta(hours=3))
    client.fetch_by_catalog_number(25544)

    snapshots = client.list_snapshots()
    assert len(snapshots) == 2
    assert snapshots[0].retrieved_at < snapshots[1].retrieved_at
    assert client.latest_snapshot("catnr-25544") == snapshots[1]


def test_corrupt_snapshot_file_falls_back_to_network(tmp_path, iss_payload):
    corrupt = tmp_path / "catnr-25544_20261104T195000Z.json"
    corrupt.write_text("{not json", encoding="utf-8")

    session = FakeSession([FakeResponse(payload=iss_payload)])
    client = make_client(tmp_path, session, FakeClock(START))
    result = client.fetch_by_catalog_number(25544)

    # The unreadable file was skipped, so the client had to download.
    assert result.source == "network"
    assert len(session.calls) == 1


# --------------------------------------------------------------------------- #
# Freshness reporting
# --------------------------------------------------------------------------- #


def test_freshness_reports_retrieval_and_epoch_ages(tmp_path, iss_payload):
    session = FakeSession([FakeResponse(payload=iss_payload)])
    client = make_client(tmp_path, session, FakeClock(START))
    result = client.fetch_by_catalog_number(25544)

    freshness = result.freshness(now=START + timedelta(hours=11, minutes=7, seconds=53))

    assert freshness.source == "network"
    assert freshness.retrieval_age == timedelta(hours=11, minutes=7, seconds=53)
    # ISS epoch is 2026-11-04T08:52:07.404096Z, so ~22h old at START+11h.
    assert freshness.epoch_age is not None
    assert freshness.epoch_age > timedelta(hours=21)
    assert freshness.elements_are_old is False
    assert freshness.is_cached is False

    summary = freshness.summary()
    assert "live CelesTrak download" in summary
    assert "Element epoch" in summary


def test_freshness_flags_elements_older_than_three_and_a_half_days(
    tmp_path, iss_payload
):
    session = FakeSession([FakeResponse(payload=iss_payload)])
    client = make_client(tmp_path, session, FakeClock(START))
    result = client.fetch_by_catalog_number(25544)

    freshness = result.freshness(now=START + timedelta(days=5))

    assert freshness.elements_are_old is True
    assert "WARNING" in freshness.summary()


def test_freshness_marks_cached_data_as_cached(tmp_path, iss_payload):
    session = FakeSession([FakeResponse(payload=iss_payload)])
    clock = FakeClock(START)
    client = make_client(tmp_path, session, clock)

    client.fetch_by_catalog_number(25544)
    clock.advance(timedelta(minutes=30))
    cached = client.fetch_by_catalog_number(25544)

    freshness = cached.freshness(now=clock.now)
    assert freshness.is_cached is True
    assert "local cache" in freshness.summary()


def test_period_minutes_matches_known_iss_period(iss_payload):
    element = element_from_omm(iss_payload[0])
    assert element.period_minutes is not None
    # ISS: ~92-93 minutes per orbit.
    assert 92.0 < element.period_minutes < 93.5
    assert not math.isnan(element.period_minutes)




