# Cinematic satellite tracking

Select a satellite, then use **Follow Satellite** or **Satellite Perspective** above
the playback timeline. Earth View remains the default. **Return to Earth** or Escape
restores the camera view, zoom limits, and controls saved before tracking. The distance
slider changes follow framing only. In Satellite Perspective, drag the canvas or use
arrow keys while the canvas is focused to look around. No camera action sends an event
to Python or requests data.

## Camera and position model

`tracking.js` runs from the existing globe update loop, after playback updates the
batched satellite positions. It reads the selected vertex, rather than calculating a
second trajectory. The renderer uses geodetic latitude/longitude on its spherical
Earth, with its existing radius-relative altitude. The camera uses exactly that same
Earth-fixed display coordinate system, not TEME or inertial coordinates.

Velocity is not included in the frontend payload. The camera estimates a display-frame
tangent from two nearby interpolated positions in the existing keyframes, using the
same longitude wrapping as the point layer. It removes the radial component. For
near-stationary objects it projects the previous tangent onto the new radial plane,
with a deterministic perpendicular as a final fallback. This is a viewing frame, not
an estimate of spacecraft attitude or a physical inertial orbital-plane measurement.

Follow places the camera above and behind the selected point with radial up. Perspective
places it just above the point and looks forward and down toward the horizon. Orientation
uses quaternion interpolation. Position easing interpolates angular direction and radius
separately to avoid travelling through Earth. A short acquisition pause precedes the
approximately three-second approach; exponential easing is frame-rate independent.
Scrubs, playback looping, and new selections are damped instead of teleporting the camera.
The existing selection reticle identifies the target; no physical satellite model is invented.

## Recovery and limits

Hidden or invalid targets trigger an automatic return with a brief explanation. A new
scene and explicit Analysis-to-Globe actions restore Earth View before applying their
existing navigation. Resize uses the existing renderer handler. Large frame gaps are
clamped so a background tab cannot produce a single large camera movement. Reduced
motion skips cinematic travel; actual orbital playback still follows its selected speed.
The distance slider is a framing control in scene units, not a scientific separation
measurement. Perspective is simulated, not a live onboard camera or known sensor view.

High playback speeds and large timeline jumps briefly separate the damped camera from
the exact target. Existing keyframe interpolation error remains unchanged and can be
noticeable at close range. Earth texture resolution is unchanged. Real spacecraft attitude,
city lights, and detailed spacecraft geometry are outside this feature.

## Verification

Run `python scripts/verify_tracking_ui.py` for offline Chrome verification using the
existing saved catalogue. It checks modes, projected marker visibility, pause settling,
playback and scrubbing, look-around, selection changes, hiding/invalid position recovery,
return/cancel, reduced motion, Analysis navigation, and resize. No live AI requests.
`docs/tracking-performance.json` records same-session Earth and Follow samples on
headless Chrome with SwiftShader; these are software-renderer observations, not a GPU
performance guarantee. The rig adds no render loop, assets, geometry, or dependencies.


### Recorded verification (16 November 2026)

- Python regression suite: **226 passed**.
- Tracking browser test: passed with **11,959 satellites**, zero severe console errors.
  Includes high-orbit Earth framing and unchanged geometry counts after repeated mode
  switches, in addition to the checks above.
- Cold-load browser check: passed on repeat in normal and reduced-motion modes,
  including synchronized reveal, autoplay, and pause persistence. The first run
  missed intermediate fade frames; the repeated trace captured them.
- Existing Analysis selection browser test: passed (farthest, unusual, individual,
  hidden groups, repeat selection, and playback).
- Existing globe interaction test: passed (Earth occlusion, hidden points, drag versus
  click). Its screenshot crop now measures the actual Constellations HUD boundary,
  excluding the changing FPS counter in the taller glass panel.
- Software-renderer samples: Earth View **15.9?23.9 FPS**, median **23.9**;
  Follow **19.4?21.5 FPS**, median **20.7**. These short, variable samples compare
  different camera views; close-up Earth also changes GPU fill workload. They are not
  a controlled pre-change GPU benchmark. Real hardware performance is unmeasured.

Implementation files: `components/globe/frontend/tracking.js`, `orbix-globe.js`,
`index.html`, and `style.css`. Verification lives in `scripts/verify_tracking_ui.py`
and the corrected HUD crop in `scripts/verify_globe_interactions.py`. Usage is added
to `docs/user_guide.md`; this document, screenshots, and the performance JSON record
implementation and verification. No new dependencies, propagation changes, provider
calls, or data acquisition changes were introduced.
