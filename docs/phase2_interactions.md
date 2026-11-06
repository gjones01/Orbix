# Phase 2: connected analytics and globe exploration

## Interactions

**2026-11-06 visibility correction:** the original regression proved selection state and
panel updates but did not prove the satellite was visible after switching tabs. With the
current five-group snapshot, FENGYUN 4A (41882) projected outside the right edge of the
1600-pixel browser's globe viewport (normalized x ≈ 1.02). The new explicit Analysis
viewing request centres that satellite once, zooms out enough for its real altitude,
restores its group if locally hidden, and adds a labelled ring at its projected position.
Subsequent widget reruns do not reapply focus. Playback time and running state remain
unchanged. Group-level actions reveal their groups and fit their orbital heights.

`scripts/verify_analysis_selection.py` uses native button clicks and real tab switches,
including a fresh session with no prior globe click, repeat selections, hidden groups,
unusual satellites, individual satellites, ordinary reruns after camera adjustment and
selection during playback. It asserts the target lies inside the camera's visible area
and its label is visible, alongside the NORAD ID, information panel and orbit trail.
The existing smoke script also passes on all 11,982 currently saved satellites.

A related payload fix makes group indices refer to the groups actually loaded: a
GPS-only catalogue previously used a global index outside its one-element group list.
This has a dedicated regression test. Current test suite: **169 tests**.

Discovery cards now have one relevant action: select the farthest satellite, highlight
the group with the widest altitude range, or open the unusual-satellite explorer.
The explorer supports literal name/NORAD search, group filtering and 50-row pages.
It shows estimated altitude, the median for that satellite's constellation/regime,
signed difference from that median and the rule(s) that flagged it. Selecting by NORAD
ID avoids ambiguity from duplicate satellite names and reordered result pages.

Individual **View on Globe** actions update the existing `orbix_selected` state and
`orbix_highlight_ids`; the normal globe payload then supplies the marker, information
panel and 121-point orbit trail. The user opens the existing Globe tab. Streamlit 1.50's
tabs have no supported programmatic selection API, so there is no DOM-click workaround.
Explicit Analysis actions now centre their target once (see the visibility correction
above). Normal globe clicks preserve the camera; local group filters otherwise remain local.

The bridge consumes each globe event once. Previously a retained component event could
undo an Analysis selection on the next rerun. New globe clicks carry an event counter,
allowing the user to reselect the same satellite even at the same paused simulation time.
Incoming Python selections reuse the same front-end information-panel path as a click.

## Altitude comparison

The default **Near Earth** view shows estimates below 2,000 km. **Higher Orbits** shows
estimates at or above 2,000 km; **All Satellites** restores all available heights.
This boundary follows [ESA's low-Earth-orbit overview](https://www.esa.int/articles/about-us/directorates/space-transportation/types-of-orbits).
The chart explicitly distinguishes a height cut at one instant from the element-based
classification of the whole orbit. No classification code or scientific calculation changed.

Each view reports the shown, outside-view and missing counts. Only this chart is filtered;
summary cards, discoveries and outlier baselines still use all selected groups. All axes
remain linear and km/miles conversions are display-only.

## Outlier audit: result and interpretation

The high count is consistent with the existing statistical rules. An independent NumPy
implementation reproduces the **exact set of flagged NORAD IDs**, not just its size.
No threshold or scientific calculation was changed.

Reproducible anchor: **2026-11-05 23:00:00 UTC**, using the saved five-group catalogue.
The report includes a SHA-256 fingerprint of the source orbital elements.

| Check | Result |
|---|---|
| Unique catalogue satellites | 11,984 |
| Missing altitude estimates | 1 |
| Valid estimates in strata smaller than four | 2, each in a one-object stratum |
| Assessed satellites | 11,981 |
| Unique flagged satellites | 1,497 (about 12.5% of assessed) |
| Flagged by IQR only | 52 |
| Flagged by modified z-score only | 0 |
| Flagged by both | 1,445 |

Rules are applied within **primary constellation + element-derived orbital regime**:

- IQR: below Q1 − 1.5×IQR or above Q3 + 1.5×IQR, using linear-interpolated quartiles.
- Modified z-score: absolute value of 0.6745×(altitude − median)/MAD greater than 3.5.
- Combination is **OR**, with one output row per satellite, even if both rules fire.
- Strata with fewer than four valid heights are unassessed. A zero MAD disables only
  the modified z-score, leaving the IQR rule. Missing heights are not filled or tested.

| Comparison stratum | Assessed | Flagged |
|---|---:|---:|
| Starlink / LEO | 11,207 | 1,491 |
| OneWeb / LEO | 651 | 3 |
| GPS / MEO | 32 | 2 |
| Weather / GEO | 22 | 1 |
| Weather / LEO | 51 | 0 |
| Space stations / LEO | 18 | 0 |

Starlink dominates the count. Its central half lies at roughly **465–485 km**, yielding
IQR fences of **435–515 km** at this anchor. Its other altitude populations remain in the
same LEO stratum: the 25-km bins include 713 objects at 350–375 km, all flagged, versus
9,589 objects at 450–500 km, none flagged. Thus splitting by LEO/MEO/GEO removes gross
regime mixtures but does not make every constellation unimodal. Of the 1,491 Starlink
flags, 1,247 lie below its median and 244 above. These observations support population
heterogeneity; they do not diagnose mission state, malfunction, danger or bad positioning.

Examined examples include ONEWEB-0618 (NORAD 55159), estimated at 521.4 km against a
1,212.0-km comparison median, and GPS BIIRM-4 (NORAD 32260), at 19,748.5 km against
20,207.9 km. The machine-readable report preserves exact values and reasons for twelve
examples, plus every stratum and the Starlink altitude-bin counts.

The user's approximate **1,515** count cannot be reproduced exactly without its original
anchor and element snapshot. The audit reproduces its scale and explains the mechanism.
Counts vary with the propagated instant; the browser verification's later snapshot showed
approximately 1,500 flags with the same rules. Flags remain educational diagnostics.

An inaccurate explanatory comment in an existing test was corrected: for values 1–40
plus 60, the IQR upper fence is 61, not 59.5. The test now explicitly checks that IQR
does not flag 60, while the test's lowered modified-z threshold does. Production rules
were already correct.

Reproduce the audit:

```powershell
python scripts/audit_altitude_outliers.py --output docs/altitude_outlier_audit.json
```

## Caching and preservation

The active scene is pinned in session state. Analysis interactions do not reload CelesTrak
or restart the snapshot when the shared five-minute cache expires. **Reload scene**, a
loaded-group change, or a playback-window/source setting change explicitly replaces it.
Frame construction is reused per scene. Bounded Streamlit caches reuse statistical
results by actual DataFrame contents and method parameters, while a bounded orbit-trail
cache keys on the frozen element set, anchor and sample count.

All advanced descriptive statistics, missing-data audits, outliers, correlations, orbit
maps and data provenance remain accessible. No dependencies, credentials or paid services
were added. The repository still has no LLM integration; it remains documented Phase 3
work and is not claimed as fulfilled by these deterministic discoveries.

## Verification

The existing suite and new regression tests cover shared filters, stable target IDs,
independent outlier verification, unit conversion, empty/missing observations, result
paging, cache eviction without snapshot reset and shared selection state.
Final full-suite result: **168 passed** (12.25 seconds). `git diff --check` passed.

Headless Chrome loaded all five groups (11,984 satellites). It verified selection from
Analysis to INSAT-3DR (NORAD 41752), the information panel and 121-point trail, then a
return click to GPS BIIR-13 (NORAD 28474). Scene identity, paused playback time and camera
position stayed unchanged, and playback continued afterward. Group highlights, clear,
advanced scatter-event transport and all six advanced diagnostic sections also passed.
The unusual explorer selected ONEWEB-0618 (NORAD 55159) through the same globe path.
The large catalogue rendered at about 44–45 fps with software WebGL in these runs.
The explorer and overview were captured and visually reviewed at 1600 px and 1366 px.
Wide data tables use their own horizontal scrolling at laptop width; the page itself
does not overflow horizontally.

Browser checks use saved snapshots and do not re-test live CelesTrak requests. API fake-HTTP
tests remain in the passing suite. Physical lasso dragging, mobile layout, hardware-GPU
performance and LLM integration were not tested. Desktop/laptop screenshots are provided.

## Files changed

- `app.py`: pin active scenes and consume globe events once.
- `components/globe/frontend/orbix-globe.js`: shared panel updates and unique click events.
- `src/analysis/overview.py`: altitude views, structured discovery targets, unusual rows.
- `src/ui/analysis_tab.py`: interactive discoveries, unusual explorer, shared selection.
- `src/ui/analysis_cache.py` (new): bounded caches for analysis results.
- `src/ui/loading.py`, `src/ui/presentation.py`: reload invalidation and orbit-trail caching.
- `scripts/audit_altitude_outliers.py` (new), `scripts/smoke_test_ui.py`: reproducible audit
  and browser regression coverage.
- `tests/test_analysis.py`, `tests/test_analysis_ui.py`, `tests/test_overview.py`: regression
  tests and corrected mathematical test explanation.
- README, this report, `docs/altitude_outlier_audit.json`, and updated screenshots.
