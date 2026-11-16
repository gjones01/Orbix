## Explore the globe

Choose visible satellite groups in the globe's Constellations panel. Drag the globe to rotate it, scroll to zoom,
hover to identify a satellite, and click to select it and see its orbit trail.
Playback starts automatically when the globe first appears. Use **Pause** to stop
it, or the playback controls to move through the selected time window. **Reload scene**
starts a new snapshot. Saved-data mode lets you explore without downloading orbital data.

Earth's day and night sides follow the playback time. Rotate the view to find the
Sun in the starfield; Earth can block it from view. Satellite markers remain bright
on the night side so you can explore them. A faint fill light keeps Earth's surface
readable at night; the Sun's glow and starburst are stylized. Lighting is an approximate visualization,
not a prediction of whether an individual satellite is in Earth's shadow.

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


### Mission workspace

Use **Constellations** on the globe to show or hide loaded satellites instantly.
The sidebar's collapsed **Data sources** section controls which catalogues are loaded;
changing a source creates a new snapshot. Playback window, saved data, and Reload scene
remain in the sidebar, which can also be collapsed for a wider view.

**Ask Orbix** is beside the globe, or below it on smaller screens. Its conversation panel
scrolls independently. Open **Conversation tools & ideas** for suggestions and Clear
conversation. The analyst uses your comparison groups from Analysis and can answer
questions about the selected satellite. Changing visibility on the globe does not
change the comparison groups. The robot, history, and User guide evidence remain available.

Glass panels and the loading wireframe are decorative; loading ends when the scene is
ready. Reduced-motion preferences disable the loader's animation.


### Follow a satellite

Select a satellite on the globe and choose **Follow Satellite** above the timeline.
Use **Distance** to adjust framing, or **Satellite Perspective** to explore the view
from near its predicted position. Drag the view (or focus the canvas and use arrow keys)
to look around in Satellite Perspective. Playback, speed, pause, and the timeline still
work. **Return to Earth** or Escape restores your previous Earth View.

The perspective is simulated from orbital predictions; it is not an onboard camera
or a model of the satellite's actual orientation. A hidden or unavailable target returns
you to Earth View automatically. Reduced-motion settings skip the cinematic approach.


### Earth lighting

Choose **Cinematic** lighting for the thin atmospheric rim, night lights, and water
reflections. Use **Basic** for the previous simpler appearance or smoother interaction
on a slower device. Lighting follows the playback timestamp. Earth textures are static
composites: the night lights show NASA's 2016 map, not live imagery or current outages.
The atmospheric glow and reflections are visual approximations, not scientific forecasts.
