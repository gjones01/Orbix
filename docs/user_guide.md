## Explore the globe

Choose satellite groups in the sidebar. Drag the globe to rotate it, scroll to zoom,
hover to identify a satellite, and click to select it and see its orbit trail.
Use the playback controls to move through the selected time window. **Reload scene**
starts a new snapshot. Saved-data mode lets you explore without downloading orbital data.

## Compare and investigate

In **Analysis**, choose groups and height units, compare population and altitude,
or explore interesting and unusual satellites. **View on Globe** selects the satellite;
open **Globe** to see its marker and trail. Group highlighting works the same way.
The height chart's near-Earth band is below 2,000 km; its bands change the chart view,
not the overall summary. Dots show medians, thick lines the middle half, and thin lines
the full range. Missing positions are excluded from height charts, not from the catalogue.

## Ask Orbix

Ask questions in everyday language or choose a suggested question. Your question,
relevant satellite data and recent conversation are sent to the configured AI provider
only when you submit. A remote provider requires a connection even in saved-data mode.
AI explanations can make mistakes. Supporting evidence and request details are available
below. Clearing the conversation removes the session's answers. Loading a new snapshot
resets the conversation; changing comparison groups labels older answers appropriately.

## About the data and calculations

CelesTrak supplies orbital elements, not live position measurements. Orbix uses SGP4
to estimate satellite positions. Globe playback advances from the snapshot time; Analysis
uses the starting snapshot. Element age describes the orbit fit, not the download time.

Orbix makes one CelesTrak request per loaded group and caches it for two hours. Globe
filters do not re-download data. Force re-download bypasses that cache; CelesTrak can
still return its unchanged data. Saved-data mode uses local snapshots. The browser
interpolates between precomputed positions; sampling and accuracy details are below.

Unusual heights are identified within each group and orbital regime using IQR fences
(1.5×IQR) or a modified z-score above 3.5. At least four valid values are needed.
A zero MAD disables only the z-score rule. Each satellite is counted once and flags
never remove records. Unusual does not establish malfunction or danger. Different
orbital populations can account for many flags.

The reference tools below retain the full statistical tables, distributions, adjustable
outlier diagnostics, correlations, linked orbit map, column provenance and data-quality
audit. They use the groups selected in Analysis. Correlations describe associations,
not causes; undefined values stay missing. Technical source documentation is maintained
in `docs/technical_decisions.md`, `docs/phase2_interactions.md` and `docs/ai_analyst.md`.
