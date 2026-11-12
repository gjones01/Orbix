# Reactive motion

Space is black, with the blue atmospheric shell and CSS horizon glow removed.
A white directional light illuminates Earth; a low neutral ambient fill (0.3
versus sunlight intensity 3) keeps night-side geography readable. The Sun disc,
warm halo and short diffraction rays use the same direction
as that light and sit in the distant sky, following camera rotation without zoom
parallax. Earth occludes the Sun naturally. The fraction of the sunlit hemisphere
visible on screen depends on viewing angle; it is not forced to half the image.

`components/globe/frontend/sunlight.js` computes approximate subsolar latitude and
longitude from the playback UTC time using the [USNO solar coordinate algorithm](https://aa.usno.navy.mil/faq/sun_approx)
and [sidereal time](https://aa.usno.navy.mil/faq/GAST). UTC approximates UT1/TT and
nutation is omitted. This is visual day/night lighting, not eclipse or spacecraft
shadow analysis. Satellite markers stay visible as exploration overlays. The Sun
disc and five-degree-radius flare are stylized for visibility, not an angular-size
measurement. Six faint optical ghost rings align with the projected Sun and view
center as the camera rotates. They use one batched draw call, fade near the screen
edge, and disappear behind the camera or when Earth blocks the Sun. Ring opacity
is capped near 0.2, keeping geography and satellite overlays readable. There are
no screen-wide opaque streaks or autonomous animations.
No ephemeris downloads,
shadow maps, new services or animation loops are needed.

The starfield is a single batch of 900 decorative directions covering the sky.
It follows camera rotation, ignores camera translation/zoom, and stays behind
Earth and satellites. It replaces the earlier CSS pointer-parallax layers, adds
one draw call and has no animation loop or position uploads of its own. These
stars are decorative, not a scientific sky catalogue. Direct camera movement
also works with reduced motion; there is no autonomous drift or twinkling.
Selection emphasis and information-panel
updates settle in 240 ms; controls and discovery cards use 180 ms feedback.
On first load, Earth and satellites share a 450 ms canvas fade after both the
Earth texture and satellite scene are ready. Globe.gl's separate startup spin
is disabled. Reduced motion reveals the complete scene immediately; ordinary
reruns do not replay the entrance. `scripts/verify_startup_ui.py` checks cold
loads with cache disabled and 400 ms network latency in both motion modes.
Orbital positions, trails, camera control and chart values are not animated for
decoration. No new dependencies or services are required.

`components/globe/frontend/starfield.js` owns the distant sky.
`components/globe/frontend/motion.js` owns panel motion, cancels it when hidden, and respects
`prefers-reduced-motion`. User-controlled orbital playback remains available.
Selection uses shader uniforms within the existing rendering loop. Analysis
surface transitions live in `src/ui/theme.py`.

## Original motion measurement (before spherical sky)

`python scripts/benchmark_motion.py --output docs/starfield-performance.json` loads a fixed
offline snapshot at 2026-11-10 17:00 UTC, with 11,977 satellites, playback enabled,
and continuous synthetic pointer movement. Three 3-second samples follow warm-up.
Chrome headless uses SwiftShader at 1600 × 1000. Raw results are in
[before](motion-before.json) and [after](motion-after.json).

| Median across three samples | Before | After |
| --- | ---: | ---: |
| FPS | 41.86 | 42.00 |
| 95th-percentile frame interval | 25.1 ms | 29.2 ms |
| Reported position update time | 1.47 ms | 1.31 ms |
| Draw calls | 3 | 3 |

Throughput is similar; tail frame timing is worse in this short run. These noisy
software-rendered samples do not demonstrate a speed improvement or predict
hardware GPU performance. Position-update time is the renderer's sampled reading,
not a full timing distribution. A hardware-browser check remains useful.

The current benchmark also asserts that sky projection changes with rotation
but not zoom, satellite coordinates remain unchanged, and hidden/reduced-motion
and selection behavior still work. Current results are in
[starfield-performance.json](starfield-performance.json); the table above records
the previous CSS-starfield implementation.

## Verification

- 215 pytest tests passed.
- Offline browser smoke test passed: all groups, playback/reset, selection,
  121-point trails, group highlighting, statistical reference and linked scatter.
- Dedicated Analysis-to-Globe tests passed for farthest, unusual and individual
  satellites, repeated selections, hidden groups and selection during playback.
- AI chat browser flow passed three local HTTP mock requests, with no console
  errors; a live LLM request was not performed.
- Desktop (1600 px) and laptop (1366 px) layouts were checked in Chrome;
  screenshots are stored under `docs/screenshots/motion-*`.
