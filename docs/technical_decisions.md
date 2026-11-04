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

## D13 — A real custom component, not injected HTML

**Decision.** The globe is a Streamlit custom component declared with
`declare_component(path=components/globe/frontend)`, and the front-end implements
Streamlit's component protocol (v1) by hand rather than through the React template.

**Rationale.** The brief asks for an architecture that can later support two-way
communication. `components.html()` cannot return a value; a declared component can.
Hand-writing the protocol (~40 lines: `componentReady`, `setFrameHeight`,
`onRender`, `setComponentValue`) avoids a Node/npm build step, which keeps the
repository reproducible with `pip install` alone.

**Consequence.** Marker clicks already travel back to Python and are displayed under
the globe; Step E's time slider can use the same channel. Camera movement stays
inside the iframe and never triggers a rerun. Verified in a headless browser: the
renderer receives 3 marker points, 1 label, 1 ring, 3 paths and 121 track vertices,
with zero severe console errors.

## D14 — No orbital maths in JavaScript, and explicit render units

**Decision.** `components/globe/payload.py` converts everything the renderer needs:
geodetic degrees and `altitude_km / 6371.0088` as the altitude fraction. The browser
only draws. A test fails the build if trigonometry or the Earth radius appears in the
front-end source.

**Evidence (each of these was a real bug, found by the browser smoke test):**

| Globe.gl parameter | Unit | Missed assumption |
|---|---|---|
| `pointAltitude`, `pathPointAlt`, `ringMaxRadius`(surface) | fraction of globe radius | Using km would put the ISS ~425 radii up |
| `pointRadius` | **angular degrees** | A value in globe radii made the marker ~0.2 px |
| `pointOfView({altitude})` | globe radii above the surface | - |

**Consequence.** The satellite's *position* is always its propagated position; only
the marker's drawn size is exaggerated (0.9°). Visibility comes from a halo, a
surface radar ring, a label and a leader line down to the sub-satellite point rather
than by floating the marker. The track is Earth-fixed, and a test pins the effect:
336.5° of longitude per 92.95-minute orbit instead of 360°, because the Earth turns
underneath the orbit.

## D15 — Front-end libraries and textures are vendored

**Decision.** Globe.gl 2.34.4, the NASA Blue Marble texture (public domain) and a
NASA-derived topology bump map are committed under
`components/globe/frontend/` (~3.2 MB total) and served by Streamlit.

**Evidence.** NASA's `eoimages.gsfc.nasa.gov` was unreachable from this network, and
a pinned CDN is a single point of failure at demo time; a failed `globe.gl.min.js`
would leave an empty box. `*.jpg`/`*.png` are marked `binary` in `.gitattributes` so
line-ending normalisation cannot corrupt them.

## D16 — Vectorised batch propagation for constellations

**Decision.** Constellation scenes use `src/orbital/batch.py` (direct `sgp4` with
NumPy), not the per-object Skyfield path. The Skyfield path remains the reference for
single satellites and for the cross-check tests.

**Evidence (measured, 11,958 objects):**

| Path | Cost | 11,958 × 24 samples |
|---|---|---|
| Skyfield, one object at a time | ~244 µs/state | ~70 s |
| `sgp4_array`, one call per object | ~1 µs/state | ~0.35 s |

Building the 11,958 `Satrec` models costs 0.12 s; a full 20-minute window (41
keyframes for 777 objects) loads in **0.05 s**, and all 11,958 in **0.38 s**.

**Correctness.** The batch path rotates TEME→ECEF with the same IAU-82 GMST
expression used by the independent Step C check, and takes UT1−UTC from Skyfield's
timescale rather than assuming UT1 = UTC. Measured disagreement with the verified
Skyfield pipeline: **0.3 m** in ECEF and 0.0 m in altitude across a sample of the
catalogue; tests assert < 1 km.

## D17 — A batched Points layer, chosen from measurements

**Decision.** Satellites are drawn as one `THREE.Points` geometry with a custom
shader; globe.gl's own point layer is not used for the constellation.

**Evidence (headless Chrome, SwiftShader software rendering, so a real GPU is faster):**

| Approach | Points | Static | Animating every frame | Draw calls |
|---|---|---|---|---|
| globe.gl points, unmerged | 3,000 | 239 fps | 48 fps | 3,000 |
| globe.gl points, unmerged | 11,958 | 189 fps | **19.7 fps** | 11,958 |
| Batched `THREE.Points` | 11,958 | — | **78 fps** (benchmark page) | 3 |
| Batched in the app | 11,958 | — | **52.6 fps**, 1.39 ms/frame | 3 |

The measurement changed the design: an object-per-satellite approach *looks* fine
when static and collapses when animated, which is exactly what playback needs.

**Consequence.** Globe.gl bundles three.js r171 but does not expose it, so the same
revision is vendored and imported as an ES module; objects created with it are
interchangeable with globe.gl's scene graph (verified to the pixel: our
lat/lng→globe-space conversion projects to the same screen point as globe.gl's own
`getScreenCoords`). Picking is a `Raycaster` with a threshold derived from the camera
distance, giving a constant ~10 px pick radius at any zoom.

## D18 — Adaptive keyframes: accuracy target first, payload budget second

**Decision.** `plan_window()` samples finely enough to keep linear-interpolation error
≤ 1 km, then coarsens only if `satellites × keyframes` would exceed ~2 MB of base64.
Keyframes ship as base64 `Float32` (4 bytes/number) and are omitted from reruns whose
scene fingerprint is unchanged.

**Evidence.** For 777 satellites the planner returns 41 keyframes over 20 min (every
30 s, 1.0 km error) and a 596 KB payload; a selection rerun without keyframes is
97 KB. For 11,958 satellites it returns 10 keyframes (every 2.2 min, 19.2 km error)
and 3.3 MB. Shipping 30-second sampling for 12k objects would be ~13 MB and would
stall the browser.

**Consequence.** The interpolation error is computed (`r·(1−cos(ωΔt/2))` for the
fastest period in the catalogue) and displayed in both the globe HUD and the Streamlit
panel. Accuracy degrades visibly rather than silently, and filtering to fewer
constellations restores fine sampling.

## D19 — Playback is anchored at load time and runs forward

**Decision.** The playback window starts at the instant the scene is built (now, UTC)
and extends forward by the selected width (10/20/45/90 min). The simulation opens on
the window start, `Reset` returns to it, and `Play` advances into the future. Orbix is
labelled as an *orbital prediction* view, never live tracking.

**Rationale.** The first implementation centred the window on now, so pressing Play
began 10 minutes in the past with nothing on screen saying so. A forward-only window
matches what "Play" implies (watch what happens next) and makes the start instant
meaningful.

**Consequence.** The stale-element guard now uses the *far* end of the window
(`max(|start−epoch|, |end−epoch|)`), because playback can reach 90 minutes beyond the
anchor. A window change re-anchors to the new now and resets `tNorm` to 0; a
selection-only rerun deliberately does not (it would jump the simulation).

## D20 — Selection needs one extra rerun, and a "null means no news" rule

**Decision.** When the component returns a selection, `app.py` stores it and calls
`st.rerun()`; on the front-end, a *null* `payload.selection` on an unchanged scene is
ignored rather than treated as a deselect.

**Evidence.** The payload is built before the component is called, so the run that
receives a click still carries the *previous* selection - the selected satellite's
trail therefore arrived one interaction late. Treating the resulting null as a
deselect made the highlight flicker off and back on.

**Consequence.** Click → trail in one extra cheap rerun (~97 KB, keyframes omitted);
the highlight never flickers; repeated events cannot loop because the JS only sends a
given NORAD ID once and the Python side only reruns when the value actually changes.
Verified in the browser: selection yields 2 path objects with 121 points.

---

## D21 — Column provenance is a registry, not a convention

**Decision.** Every column of the analysis frame is declared once, in
`src/analysis/provenance.py`, with a provenance class (`identity`, `grouping`,
`element`, `derived`, `propagated`), a unit and a source. `catalog_frame()` emits
columns in exactly that order and `validate_frame()` refuses a frame that disagrees
with the registry.

**Why.** The dangerous mistakes in orbital data science are all provenance mistakes:
treating a fitted mean element as a measurement of the satellite, or computing
`semi_major_axis - R_earth` and calling it altitude. The second one is measurable on
real data: for the 17 near-circular objects in the default snapshot the two agree to
7.5 km, but for `FREGAT DEB` (e = 0.093) the naive formula is **681 km wrong**, because
it ignores where in the orbit the object is. Both columns now exist, both are labelled,
and a test asserts the relationship `|(a - R) - h| <= a*e + 21 km` on every row.

**Consequence.** Instantaneous speed is *absent* rather than estimated: `BatchSample`
carries positions only, and a finite difference across 30 s keyframes is a chord
average, not a speed. There is a comment in the registry where the column would be.

**Cost.** A new column means touching the registry, the frame and usually a note. That
is the point: it cannot be added silently.


## D22 — Statistical choices stated in code, and the failure modes named

**Decision.** `src/analysis/stats.py` reports medians and quartiles alongside means,
uses **Spearman** rather than Pearson, reports **no p-values**, never imputes, and
detects outliers with two independent rules (Tukey IQR fence at k = 1.5 and the
modified z-score `0.6745 (x - median) / MAD` at 3.5), recording which rule fired.

**Why each one.**

* *Spearman*: the relationships are monotone at best, and eccentricity and B* are
  bounded and heavily skewed, so a rank correlation is the defensible summary.
* *No p-values*: these are near-censuses of the tracked catalogue, not random samples.
  At n = 11 957 everything is "significant"; a p-value would be noise dressed as
  evidence. Effect size and n are reported instead.
* *No imputation*: a zero-filled B* is a claim that the object has no drag. GPS proves
  the point - all 32 objects publish B* = 0, so a B* analysis of GPS is meaningless,
  and the frame says so instead of filling a gap.
* *Two outlier rules*: they fail differently. MAD is zero whenever more than half a
  group shares one value (GPS B*, inclination inside one Starlink shell), which is
  detected and reported as `degenerate` with the IQR fence used alone.

**The finding that changed the design.** On the real `weather` group, 23 of 74 objects
(31%) are flagged on altitude - because the group mixes ~800 km polar platforms with
GEO meteorological satellites (median 830 km, max 35 800 km). Calling those 23
"outliers" would be wrong: they are a second population. `outliers()` therefore reports
`outlier_pct` and a `heterogeneous` flag above 10%, and the note says to inspect the
distribution. The same happens on Starlink (1 463 of 11 181 = 13.1%), which occupies
discrete shells at 350-570 km. Conversely, an exact 50/50 two-mode mixture is *not*
flagged, because the quartiles land on the modes - a test documents that, so nobody
later "fixes" it as a bug.

**Consequence for correlations.** `pooled_vs_within()` puts the pooled Spearman next to
the within-group values. On the default snapshot, `altitude_km ~ bstar` is -0.12 pooled
but -0.68 inside groups (aggregation bias); `inclination_deg ~ period_min` is -0.73
pooled, -0.21 within, and flips sign in at least one group. By contrast
`altitude_km ~ period_min` survives stratification (0.90 pooled, 0.85 within), which is
the method distinguishing a real relationship from a compositional artefact. Adding
Starlink (93.5% of the full frame) moves the pooled coefficients drastically, which is
the same warning from the other direction: pooled numbers describe the sample
composition, not satellites.


## D23 – Stratify before interpreting, and never claim a reversal that is not there

**Decision.** A contaminated-looking group is treated as a *stratification* problem first.
`stats.add_stratum()` splits each constellation by the element-derived orbital regime, and
every relevant view (distributions, outliers, correlations) can be re-run on those strata.
The heterogeneity threshold is a parameter (`heterogeneous_pct`), not a constant of nature.

**Why.** On the real snapshot the `weather` group is 74 objects: 51 LEO at ~500 km and 22
GEO at 35 786 km. Pooled, 23 of 74 (31%) sit outside the LEO fence and would be reported as
outliers - but METEOSAT and GOES are not anomalies, they are a second population.
Re-running the same rule per stratum (`weather / LEO`, `weather / GEO`) leaves **0 and 1**
flags. The objects never changed; the grouping did. Starlink behaves the same way (13.1%
flagged) because it occupies discrete shells at 350-570 km. Hence: flags are reported, rows
are never deleted, and a high flag rate is displayed as evidence about the *group*.

**Consequence for correlation reporting.** The earlier `sign_flip`/`confounded` pair was
ambiguous and one label was simply wrong: `altitude_km ~ bstar` is -0.09 pooled and -0.68
within groups, which is a large difference in **magnitude** with every group agreeing in
sign (`same_sign_share = 1.0`, `reversed_groups = 0`). It was never a sign reversal. The
report now carries `pooled_rho`, the per-group range with its n range, `gap`,
`same_sign_share`, `reversed_groups`, `undefined_groups` and a `simpson_pattern` flag that
is only set when *every* defined group has the opposite sign to the pooled coefficient (and
at least two groups exist). On this snapshot `simpson_pattern` is false for every pair;
`inclination_deg ~ period_min` does reverse in one group of four (stations +0.78 vs OneWeb
-0.86), which the per-group table shows directly instead of being called a paradox.

**Consequence for the UI.** Chart selection highlights satellites on the globe via
`payload.highlight_ids` -> a per-point flag that enlarges markers. The globe's own click
selection (with its orbit trail) is a separate channel, so a highlighted set and a selected
object can coexist. The Python half and the renderer half are tested separately: a unit
test for the payload field, a browser check that three ids produce three enlarged markers.


## Known limitations (as of end of Step F)

1. **Playback sampling is a trade-off, and it is displayed.** At 12k satellites a
   20-minute window is sampled every ~2.2 min, giving ~19 km of interpolation error at
   the midpoint between keyframes. Flight-dynamics work would use the propagator at
   every instant; a browser animation cannot. Filtering to fewer constellations (or
   choosing a shorter window) tightens it to ~1 km.
2. **Interpolation is linear in geodetic space**, not a great-circle arc. The quoted
   error is the resulting chord error; it is not corrected.
3. **Starlink is off by default.** 11,181 of the 11,958 objects are Starlink; enabling
   it multiplies the payload by ~6 and coarsens sampling. It is a checkbox, not a
   hidden cap.
4. **Marker size is not to scale.** Positions and altitudes are exact; the dot radius
   is constant on screen so that a 400 km satellite is findable at planetary zoom.
5. **Hover picking is CPU ray-casting** over the whole point cloud, throttled to
   ~22 Hz. At 12k that is ~1–2 ms per pick, which is fine here but would need GPU
   picking (an ID buffer) at 100k objects.
6. **No terminator/day-night shading** (the texture is a fully sunlit composite), and
   no eclipse or sunlight modelling.
7. **Elements are hours old**, so this is a planning/education view, not live tracking.
   Element age is shown for the selection and the scene reports skipped stale sets.
8. **A few records are genuinely odd**: ~70 objects below 300 km (decaying) and 23
   near 35,800 km (GEO weather). They are plotted where SGP4 puts them; the dataset is
   not filtered to look tidy.
9. **WebGL was verified with software rendering** (SwiftShader). Frame rates on a real
   GPU were not measured; relative comparisons between approaches remain valid.
10. **Category colours wash out at small marker sizes** because of additive blending;
    they read best when zoomed in or filtered. Cosmetic, deferred by the brief.
11. **Streamlit DOM selectors** are used by the CSS for chrome hiding; if Streamlit
    renames its `data-testid`s the app still works but looks less bespoke.
12. **No EDA, no LLM.** Deliberate phase gating; see `docs/assignment_requirements.md` §3.

1. **One satellite, no playback.** Step D is ISS-only and shows a single instant;
   the sidebar says so. Time scrubbing, multi-object selection and group switching are
   Steps E and F by design.
2. **The scene is a snapshot, not a live feed.** Positions are computed at load time and
   cached for 60 s, and GP element sets are already hours old, so the globe is not a
   real-time tracker. The panel shows element age to make this explicit.
3. **The marker's size is not to scale.** At 425 km the ISS would be sub-pixel; its
   position and altitude are exact, its drawn radius (0.9°) is not. The leader line
   and surface ring exist so the exaggeration is not misread as altitude.
4. **The orbit track is Earth-fixed, not an inertial orbit plane.** It drifts west each
   pass (336.5°/orbit). Drawing the inertial plane instead would need the globe to stop
   rotating or the track to be rotated in the shader; that is deliberately not done.
5. **Depth sorting of the translucent track glow** is left to Three.js. With many
   satellites this will need per-object render ordering.
6. **No sunlight/terminator shading.** The texture is a fully sunlit composite, so day
   and night are not modelled (that needs a solar ephemeris, which Orbix does not ship).
7. **~3.2 MB of vendored assets** are committed. Acceptable for demo reliability, but
   it is repository weight that a CDN-based design would avoid.
8. **The WebGL path is verified with software rendering** (SwiftShader) in headless
   Chrome. Frame rate on a real GPU was not measured; 1 satellite + 121 track points is
   far below any plausible limit, but Step F's thousands of objects are unmeasured.
9. **Streamlit's DOM class names are used by the CSS.** If Streamlit renames its
   `data-testid`s the chrome-hiding rules may stop applying; the app still works, it
   just looks less bespoke.
10. **No LLM, no EDA.** Deliberate phase gating; see `docs/assignment_requirements.md` §3.

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

