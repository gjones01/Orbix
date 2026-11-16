# Cinematic Earth lighting

The globe's **Lighting** selector offers Cinematic (default) and Basic. Basic restores
the previous neutral fill, surface settings, and no atmospheric shell or night lights.
It is a client-side fallback; switching quality does not reload data, change playback,
or alter satellite selection. Failed optional texture loads fall back to the original
geographic surface and directional sunlight. There are no new runtime dependencies.

## Effects and coordinate system

The existing directional Sun remains fixed in the scene's Earth-fixed frame, derived
from the current playback timestamp. Its approximation uses the US Naval Observatory
solar ecliptic longitude, obliquity, right ascension, declination, and Greenwich mean
sidereal time formulae. UTC approximates UT1/TT. This is visualization lighting, not
an ephemeris for eclipse prediction. Camera movement never changes the Sun direction.
References: https://aa.usno.navy.mil/faq/sun_approx and https://aa.usno.navy.mil/faq/GAST .

Cinematic reduces neutral ambient fill from 0.30 to 0.12. Directional light still provides
the day/night terminator; geography and its UV orientation are unchanged. Lower bump
strength avoids exaggerated terrain at satellite altitude. A single transparent sphere,
0.8% above Earth's radius, approximates view-dependent scattering. Grazing views brighten
the thin blue rim, modulated by solar elevation; the nightside is faint and the twilight
rim has a restrained warm component. This is an artistic scattering approximation,
not volumetric radiative transfer or atmospheric refraction.

NASA Black Marble 2016 supplies real historical night-light geography. The same UVs as
the daytime map prevent independent rotation or flipping. Smooth solar-elevation masking
reveals lights at dusk; a brightness threshold suppresses the composite's terrain
background. The Natural Earth land mask restricts the stronger Phong reflection to water.
Specular response depends on both the camera and directional Sun; land retains only a
very faint reflection. Coastal detail is generalized, and sea ice is not modelled.

The two additional maps are 2048x1024, about 354 KB combined on disk. They load locally,
with mipmaps and ordinary filtering to limit close-view cost. Cities and water share the
existing surface draw call; atmosphere adds one draw call. No duplicate render loop,
per-frame geometry, global bloom, or new postprocessing framework is introduced.
Existing Sun glare is retained. Global tone mapping/exposure changes were deliberately
avoided so satellite and star brightness stay stable. Clouds are deferred to protect
tracking performance and avoid implying live weather.

## Assets and reproducibility

Sources, rights, processing, and original SHA256 hashes are in
`components/globe/frontend/assets/README.md`. NASA night imagery is a 2016 composite;
Natural Earth polygons are public domain. `scripts/prepare_earth_textures.py` reproduces
the local image reductions and mask from the documented source inputs. Neither asset
is used as scientific input to the satellite analysis.

## Verification

`python scripts/verify_lighting_ui.py --stage final` runs the offline browser checks
and records `docs/lighting-final-performance.json`. It compares Cinematic and Basic
at the same paused snapshot and camera views with the same selected ISS trail, tests
three Earth orientations plus Follow and Perspective, and checks that satellite vertex
positions remain unchanged. It additionally checks shared Sun direction, time changes,
night/day contrast, laptop layout, and startup with optional textures blocked.

The baseline, sunlight-only, atmosphere-only, and surface stages were checked separately
before proceeding. Their JSON reports are retained. Earlier stage runs used different
snapshot timestamps, so the final same-session quality comparison is more directly
comparable. All FPS measurements use headless Chrome **SwiftShader software rendering**;
no real GPU or physical laptop performance is claimed. Close-up texture resolution,
short benchmark variability, and simplified atmosphere/water physics remain limitations.

Implementation: `earth-effects.js`, `sunlight.js`, the globe renderer's initialization,
iframe HTML/CSS, vendored surface assets, verification/preparation scripts, and guidance.
Orbital propagation, data acquisition, analysis calculations, and AI behavior are unchanged.


## Recorded results ? 16 November 2026

- Python regression suite: **226 passed**.
- Tracking browser regression: passed all modes, pause/resume, scrubbing, look-around,
  selection changes, hidden/invalid recovery, cancellation, reduced motion, tab switching,
  Analysis navigation, resize, and stable geometry counts after warming the trail view.
- Final lighting browser checks: **11,959 satellites**, no unexpected severe console
  errors, unchanged sampled satellite positions across lighting/camera modes.
- Shared sunlight uniforms follow timeline changes; the sampled day patch was brighter
  than the night patch. Startup also passed with both optional textures blocked.
- Same-session software-rendered Follow: **27.3?28.2 FPS Cinematic**, **30.1?32.8 FPS
  Basic**. Earth/day: **31.3?32.3 versus 35.8?36.6 FPS**. The selected trail is present
  in both comparisons: **8 versus 7 draw calls**.
- A 1366x900 browser viewport measured **31.3 FPS** in Cinematic Follow. This is viewport
  emulation on the same desktop software renderer, not measurement of a laptop GPU.
- Earlier pre-change baseline Follow measured **31.8?32.2 FPS**, with the timestamp
  caveat noted above. Basic is the direct fallback for devices where the added cost matters.

Files added: `components/globe/frontend/earth-effects.js`,
`assets/earth-night-2016.jpg`, `assets/earth-water-mask.png`,
`scripts/prepare_earth_textures.py`, `scripts/verify_lighting_ui.py`, and this report.
Integration changes: `orbix-globe.js`, `sunlight.js`, `index.html`, `style.css`,
`assets/README.md`, and `docs/user_guide.md`. The tracking verification now waits for
the selected high-orbit trail to upload before checking geometry counts, avoiding a
resource-measurement race. Stage performance JSON files and final screenshots are
retained as evidence.
