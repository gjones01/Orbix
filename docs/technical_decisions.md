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

## D10 — Four coordinate frames, each labelled and each used once

**Decision.** `src/orbital/propagation.py` returns three representations of the
same physical position and documents the fourth (SGP4's native frame):

| Output | Frame | Rotates with Earth | Used for |
|---|---|---|---|
| `latitude_deg` / `longitude_deg` / `altitude_km` | WGS-84 geodetic | surface | UI readouts, sub-satellite point |
| `ecef_km` | ITRS (ECEF) | **yes** | placing markers on the Three.js globe |
| `gcrs_km` | GCRS/ICRS | no | orbit-plane geometry, inertial reasoning |
| `native_sgp4_state().teme_km` | TEME | no | cross-check only, never rendered |

**Evidence.** Skyfield's `framelib` has no `teme` frame; `sat.at()` returns GCRS
and `frame_xyz(itrs)` gives Earth-fixed coordinates. Using the inertial vector for
a rotating globe is the most common way to make a correct propagator look wrong,
so the frames are separated by name.

**Consequence.** `OrbitalState.__post_init__` asserts that the inertial and
Earth-fixed vectors have the same length (a rotation cannot change magnitude) and
raises if they do not, so a frame mix-up fails loudly at construction. Tests also
re-derive lat/lon from `ecef_km` with an independent WGS-84 conversion, and assert
that the Earth-fixed frame's rotation advances by GMST (15.041°/h).

## D11 — Built-in Earth-orientation tables by default; IERS on request

**Decision.** `get_timescale()` uses `load.timescale(builtin=True)`, i.e. no
network access. Passing `use_iers_earth_orientation=True` loads
`finals2000A.all` from the IERS into `data/cache/skyfield` instead.

**Evidence (measured, ISS at its epoch, 2026-11-04T08:52:07Z):**

| Timescale | UT1-UTC used | Longitude | ECEF offset |
|---|---|---|---|
| built-in tables | +0.0873 s | 149.748618° | — |
| IERS `finals2000A.all` | −0.0582 s | 149.749226° | 72 m |

**Rationale.** A 72 m difference is far below the ~1 km accuracy limit of the
element set itself, and avoiding the download keeps propagation working offline,
on Streamlit Cloud cold starts, and when the IERS is unreachable. The opt-in path
writes to a git-ignored directory rather than polluting the repository root.

## D12 — Naive timestamps are rejected, not assumed to be UTC

**Decision.** `propagate()` raises `InvalidTimeError` for a timezone-naive
`datetime`; callers must attach a timezone.

**Evidence.** Skyfield silently normalises impossible dates: `ts.utc(2026, 13, 45)`
returns 2027-02-14, and `ts.utc(nan)` raises an internal `IndexError`. Because
Skyfield's constructors are not a validation layer, Orbix validates the instant
itself (type, tz-awareness, year range 1900-2100) before handing it over.

**Consequence.** A one-hour timezone mistake would move a LEO satellite ~27,000 km,
which is not a bug a user would spot on a globe. Tests cover naive input, string
input, an out-of-range year, and a +02:00 input that must equal its UTC twin.

---

## Known limitations (as of end of Step C)

1. **Accuracy is bounded by the element set, not by the code.** CelesTrak/Vallado
   puts TLE-class elements at ~1 km accuracy *at epoch*, degrading away from it.
   Orbix's propagation adds no error of that size: the independent `sgp4`
   cross-check and a hand-written TEME->ECEF rotation agree with Skyfield's
   pipeline to 0.003 m, and the built-in/IERS timescale choice moves the answer by
   72 m.
2. **Geodetic vs geocentric latitude.** `latitude_deg` is WGS-84 *geodetic* (like a
   GPS fix), which can exceed the orbital inclination by up to ~0.2° at 52°. That
   is correct, not a propagation error, and the tests allow for it.
3. **Positions are not real time.** Element epochs run hours behind (11.3 h in the
   test snapshot). The UI must show element age and never imply live tracking.
4. **Element age is guarded, not fixed.** Propagation beyond 30 days from the epoch
   is refused; 3.5-30 days warns. A caller can override with `max_element_age=None`.
5. **Docked objects share element sets.** CelesTrak publishes identical elements for
   ISS(ZARYA), POISK, NAUKA and other docked modules, so they propagate to identical
   positions. Correct, but it will look like a bug in a scatter plot of 20 stations.
6. **No JPL ephemeris.** Satellite geocentric positions do not need `de421.bsp`, so
   Orbix never downloads it. Anything requiring Earth orientation relative to the
   solar system (e.g. sunlit/eclipse shading) will need an additional ephemeris.
7. **Performance.** ~244 us per state on this machine; a 120-point orbit trajectory
   takes ~29 ms. Fine for Step E, but 9,000 Starlink positions would be ~2.2 s if
   recomputed naively per frame, so Step F will need caching or vectorisation.
8. **No SGP4 "deep space" concerns exercised yet.** GPS-OPS satellites (12 h orbits)
   use the same code path but have not been validated against a third-party
   reference in this repository.
9. **Streamlit Cloud filesystem is ephemeral.** Runtime snapshots and any downloaded
   IERS file do not survive a redeploy; committed snapshots are the baseline.
10. **No LLM, no EDA, no 3D code exists.** Deliberate phase gating; see
    `docs/assignment_requirements.md` §3.


---

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

