# Technical decisions and known limitations

A running record of *why* Orbix is built the way it is. Each entry states the
decision, the evidence behind it, and the consequence. Updated as the project
progresses.

**Environment on which everything below was verified**

| Item | Value |
|---|---|
| OS / shell | Windows (win32), PowerShell |
| Python | 3.13.5 (`C:\Python313\python.exe`) |
| Already installed | requests 2.32.4, streamlit 1.50.0, pandas 2.3.2, numpy 2.3.2, python-dotenv 1.1.1, pypdf 6.19.0 |
| Added for testing | pytest 9.2.0 (user-level, dev only — see `requirements-dev.txt`) |
| Not yet installed | skyfield / sgp4 / jplephem (needed at Step C) |

---

## D1 — OMM JSON instead of legacy TLE

**Decision.** All CelesTrak requests use `FORMAT=JSON` (CCSDS OMM keywords).

**Evidence.** CelesTrak's GP documentation (updated 2026-06-23) states that
5-digit catalog numbers were exhausted on 2026-07-11, that new objects receive
6-digit numbers for which "GP data will not be available … using the TLE format",
and that `FORMAT` now **defaults to CSV** if omitted.

**Consequence.** `build_query_url()` always writes `FORMAT=JSON`, overriding any
caller-supplied format, and a unit test asserts it.

## D2 — One storage mechanism: immutable snapshots + a manifest

**Decision.** Successful responses are written verbatim to
`data/snapshots/{cache_key}_{YYYYmmddTHHMMSSZ}.json`, and `manifest.json` records
`url`, `query`, `retrieved_at`, `http_status`, `record_count`, `newest_epoch` and
`sha256` for every file. The cache *is* the newest snapshot for a query key; there
is no second, separate cache file.

**Rationale.** The brief requires both caching (to limit requests) and durable
snapshots (for reproducibility and offline fallback). Keeping raw bytes untouched
means a snapshot can always be re-parsed, re-hashed and cited in the report.

**Consequence.** `source` on every result is exactly one of `network`, `cache`,
`snapshot`, so the UI can state provenance honestly. A missing or corrupt
manifest is survivable: `latest_snapshot()` can rebuild records by scanning
filename timestamps.

## D3 — No automatic HTTP retries

**Decision.** A single `GET` per attempt. Timeouts, connection errors, and any
non-200 status become typed exceptions; no retry loop, no backoff storm.

**Evidence.** CelesTrak's usage policy: "if you receive an HTTP 403 or 404 error,
the response is not going to change by repeating the request and can result in
your IP address being put in the firewall", and more than ~1000 error responses
per day from one IP triggers a firewall block.

**Consequence.** Tests assert `len(session.calls) == 1` on the 403/404 paths.

## D4 — HTTP 403 "not updated" is a cache hit, not a failure

**Decision.** A 403 whose body contains
`has not updated since your last successful download` raises
`CelestrakRateLimited`, which `fetch()` converts into the newest saved snapshot
(with an explicit note). Any other 403, and all other error statuses, still fail.

**Evidence.** Since 2026-03-26 CelesTrak enforces one download per 2-hour update
and returns exactly that message for repeats.

**Consequence.** A 2-hour `cache_ttl` (equal to CelesTrak's refresh cadence) makes
this path rare, but it is exercised by
`test_rate_limited_403_reuses_snapshot_instead_of_failing`.

## D5 — Validation is strict for single objects, lenient for groups

**Decision.** `CATNR` queries raise on the first malformed record (loud failure,
because the caller asked for one specific object). `GROUP` queries skip bad
records and report them through `result.notes`.

**Rationale.** One degraded satellite in a 30-object GPS group must not blank the
whole globe, but a silently-skipped ISS query would be a correctness bug.

**Consequence.** Physics sanity checks live in `element_from_omm()`: mean motion
must be positive and finite, eccentricity within `[0, 1)`, all required OMM
keywords present and parseable. Rejected records are never "repaired" with
invented values.

## D6 — Timestamps are always timezone-aware UTC

**Decision.** `parse_omm_epoch()` treats CelesTrak's zone-less
`2026-11-04T08:52:07.404096` as UTC and returns an aware `datetime`; a trailing
`Z` is also accepted.

**Rationale.** OMM defines the epoch in UTC, and mixing naive and aware datetimes
is the classic source of multi-hour propagation errors. Skyfield requires aware
UTC times, so this conversion is the input contract for Step C.

## D7 — No fabricated data, ever

**Decision.** If neither the network nor a saved snapshot yields valid elements,
the client raises (`CelestrakNoDataAvailable` or the underlying typed error).
There is no synthetic-orbit fallback and no placeholder ISS.

**Consequence.** The Step B script exits non-zero and prints
"No satellite data was returned. Orbix will not substitute fabricated elements."

## D8 — Snapshot hygiene in Git

**Decision.** Small payloads (ISS, STATIONS and other chosen groups) are committed
so the app and tests work offline and reproducibly; bulk groups (active, Starlink,
last-30-days, visual) are git-ignored.

**Rationale.** The assignment asks for data files in the repository, but a
multi-megabyte file that changes every 2 hours does not belong in Git history.

## D9 — Tests are hermetic and run from the repo root

**Decision.** `pytest.ini` sets `testpaths = tests` and
`addopts = --basetemp=.pytest_tmp`. Tests inject a `FakeSession` and a `FakeClock`
into `CelestrakClient`, so no network call and no dependence on the real clock
occurs.

**Rationale.** `C:\Users\Gerry\AppData\Local\Temp\pytest-of-Gerry` is owned by
another account on this machine and cannot be scanned (`PermissionError
[WinError 5]`), so pytest's scratch directory is kept inside the repo
(and git-ignored).

---

## Known limitations (as of end of Step B)

1. **Element age, not real time.** GP element sets are fits valid near their
   epoch; at the time of testing the ISS epoch was ~11 hours old. Propagated
   positions will differ from reality by roughly a kilometre. The UI must show
   epoch age and must not imply live tracking.
2. **SGP4 is not implemented yet.** This step propagates nothing; it validates
   elements, units and epochs only. Step C adds Skyfield.
3. **Skyfield not yet installed.** Compatibility verified from package metadata:
   `skyfield 1.55` ships a pure-Python wheel, `sgp4 2.27` ships
   `cp311-abi3-win_amd64` (stable ABI, usable on CPython 3.13) plus a pure-Python
   wheel, and `numpy 2.3.2` is already present. Actual installation and
   `EarthSatellite.from_omm()` verification happen at Step C.
4. **Manifest growth is unbounded.** Each real refresh appends one entry. At the
   2-hour cadence that stays small, but no pruning policy is implemented yet.
5. **Streamlit Cloud filesystem is ephemeral.** Runtime snapshots do not survive
   a redeploy; committed snapshots are the baseline.
6. **`GROUP=active` and Starlink are intentionally unused so far** - they are the
   groups CelesTrak's rate limiting targets first, and they are far larger than
   the current visualisation needs.
7. **No LLM, no EDA, no chart code exists.** Deliberate phase gating; see
   `docs/assignment_requirements.md` §3.
8. **Outstanding question — how to size the Starlink subset** (Step F): the whole
   constellation is ~9,000 objects and one of the most rate-limited endpoints, so
   the plan is a bounded, cached subset rather than the full group.

