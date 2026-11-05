# Analysis dashboard redesign

The dashboard follows five questions: what is loaded, which groups dominate, how high
satellites orbit, what stands out, and how the evidence was produced. Overview cards,
horizontal count bars and a linear altitude-range chart replace the dense scatterplot
as the default reading path. All advanced analyses remain accessible through expanders.

## Scientific choices

- `catalog_frame(scene)` remains the sole source. No independent fetch or propagation
  is introduced, and no existing scientific calculation has changed.
- Counts use primary group membership and one row per NORAD ID, matching the original
  frame. A failed position estimate still contributes to the catalogue count, but not
  to the altitude median, chart or globe highlights. Missing values are not imputed.
- Altitude medians and quartiles reuse `stats.describe`. Conversion to miles uses
  1 mile = 1.609344 km only at presentation; source data and diagnostics remain in km.
- Display bands partition instantaneous altitude at 2,000 and 30,000 km. They are not
  orbit classifications. The all-altitudes view includes full ranges to expose mixed
  groups; narrowing the chart gives local detail without logarithmic compression.
- The overview's unusual-altitude count uses `stats.outliers` on `stats.add_stratum`:
  constellation plus element-derived regime, IQR k=1.5 and modified z=3.5. Strata with
  fewer than four estimates are unassessed. The assessed denominator is shown.
- Advanced sliders remain independent of the overview's fixed defaults. Both the
  original diagnostic evidence and the overview's exact flagged rows are available.
- Retrieval time, element epoch and position-estimate time are distinct. The overview
  states its anchor and explains that playback does not advance this analysis.
- The freshness caption treats 84 hours as the application's warning heuristic,
  rather than promising a specific positional error from age alone. The orbit-map
  methodology explicitly labels the GEO band as a distance from Earth's centre.

## Interaction and scope

All report sections share the group filter; the altitude-band control affects only its
chart and says so. Group and individual highlight buttons reuse `orbix_highlight_ids`.
An explicit clear action resets the retained chart event. The advanced scatterplot
keeps trace indices stable across reruns to preserve NORAD mapping. Filter values also
have non-widget state because a globe selection may rerun before Analysis renders.

The existing scene cache continues to handle expensive acquisition and SGP4 work.
The new charts aggregate to a few group traces rather than plotting thousands of points.
No runtime dependencies, credentials, paid APIs or unrelated application pages changed.
The existing uncommitted snapshot files are outside this redesign.

LLM integration was absent and documented as Phase 3. The new computed discoveries
provide data interpretation but do not fulfill or claim the LLM requirement.

## Verification

The automated suite covers original orbital/statistical contracts plus altitude band
boundaries, unit conversion, missing/empty/small selections, shared filters and widget
reruns. The browser smoke script checks real WebGL rendering and the dashboard highlight
round trip, opens the advanced sections and captures desktop/laptop layouts. Verification
uses the saved current catalogue offline, avoiding extra CelesTrak downloads.

Verified on 2026-11-05:

- Full pytest suite: **162 passed**. After the final filter-state adjustment, the six
  focused summary/UI tests passed again without warnings.
- Streamlit starts and serves a healthy local endpoint. Headless Chrome renders the
  default 776 satellites and the full 11,984-satellite selection including Starlink.
- Group highlighting travels through the actual dashboard button, Python session state,
  globe payload and renderer; clearing returns the highlighted count to zero.
- A Plotly selection event traverses Streamlit and highlights one satellite, then clears
  correctly. This checks event transport; physical mouse box/lasso dragging was not
  separately automated. Existing tests check event-to-NORAD mapping.
- All six advanced diagnostic expanders open successfully. Globe filtering, selection,
  full orbit trail, playback and reset checks pass; no severe browser errors were reported.
- Screenshots at 1600 px and 1366 px were captured and visually reviewed, with no
  horizontal page overflow. The large catalogue rendered at about 42 fps in this
  software-WebGL test; this is an observation, not a hardware performance guarantee.
- Browser verification used offline snapshots. Live CelesTrak acquisition was not
  re-requested; its existing fake-HTTP/API regression tests passed. No LLM integration
  was available to test. Mobile layouts and hardware-GPU performance were not verified.

Changed implementation files: `src/ui/analysis_tab.py`, `src/ui/theme.py`,
`src/analysis/figures.py`, and new `src/analysis/overview.py`. Verification changes are
in `scripts/smoke_test_ui.py`, `tests/test_analysis_ui.py`, and `tests/test_overview.py`.
Documentation changes are this file, the README, and four dashboard screenshots.
