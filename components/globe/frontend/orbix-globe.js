/**
 * Orbix constellation globe.
 *
 * Drawn with Globe.gl (Earth, atmosphere, camera) plus one custom Three.js Points
 * layer for the satellites. The layer exists because globe.gl's per-object points
 * cannot animate thousands of satellites smoothly: measured 19.7 fps at 11,958 points
 * versus 78 fps for a single batched Points geometry (see docs/technical_decisions.md).
 *
 * Division of labour, strictly:
 *   Python  -> orbital mechanics (SGP4), geodetic coordinates, altitude scaling,
 *              keyframe sampling and the interpolation-error estimate.
 *   Browser -> drawing, hovering, picking, filtering and linear interpolation between
 *              the supplied keyframes. No orbital maths lives here.
 *
 * Markers and tracks are Earth-fixed, matching the globe's rotating mesh.
 */
import * as THREE from './vendor/three.module.min.js';
import { createMotion } from './motion.js';
import { createStarfield } from './starfield.js';
import { createSunlight } from './sunlight.js';

(function () {
  'use strict';

  const DEG = Math.PI / 180;
  const motion = createMotion();

  // ------------------------------------------------------------------ //
  // 1. Streamlit component bridge (protocol version 1)
  // ------------------------------------------------------------------ //
  const Bridge = (function () {
    const embedded = window.parent !== window;
    function post(type, extra) {
      if (!embedded) return;
      window.parent.postMessage(
        Object.assign({ isStreamlitMessage: true, type: type }, extra || {}), '*');
    }
    return {
      ready: () => post('streamlit:componentReady', { apiVersion: 1 }),
      setHeight: (px) => post('streamlit:setFrameHeight', { height: px }),
      setValue: (value) => post('streamlit:setComponentValue', {
        value: value, dataType: 'json',
      }),
      onRender: (handler) => window.addEventListener('message', (event) => {
        const data = event.data;
        if (!data || typeof data !== 'object' || data.type !== 'streamlit:render') return;
        handler(data.args || {});
      }),
    };
  })();

  // ------------------------------------------------------------------ //
  // 2. DOM helpers
  // ------------------------------------------------------------------ //
  const statusEl = document.getElementById('status');
  const statusTextEl = document.getElementById('status-text');
  const statusCardEl = statusEl.querySelector('.status-card');
  const tooltipEl = document.getElementById('tooltip');

  function showStatus(message, detail) {
    statusCardEl.classList.toggle('error', Boolean(detail));
    statusTextEl.innerHTML = detail
      ? message + '<br /><code>' + String(detail) + '</code>'
      : message;
    statusEl.classList.remove('hidden');
  }
  function hideStatus() { statusEl.classList.add('hidden'); }

  function formatUtc(date) {
    const iso = date.toISOString();
    return iso.slice(0, 10) + ' ' + iso.slice(11, 19) + 'Z';
  }

  // ------------------------------------------------------------------ //
  // 3. State
  // ------------------------------------------------------------------ //
  const state = {
    globe: null,
    layer: null,
    sceneId: null,
    metadata: [],
    keyframes: null,        // {count, lat, lng, alt, times:[Date]}
    radius: 100,
    markerRadius: 0.32,
    groupSpecs: [],
    enabledGroups: new Set(),
    selection: null,        // selected *index* into metadata
    selectedId: null,       // selected NORAD id
    playing: false,
    speed: 60,              // simulation seconds per real second
    tNorm: 0,               // 0..1 across the window
    clockMs: 0,
    frameStats: { frames: 0, fps: 0, updateMs: 0, startedAt: 0 },
    hoverIndex: -1,
    lastSentSelection: null,
    highlightCount: 0,
    lastAnalysisAction: null,
  };

  let layer = null;

  // ------------------------------------------------------------------ //
  // 4. Satellite layer: one batched Points geometry for the whole catalogue
  // ------------------------------------------------------------------ //
  class PointLayer {
    constructor(count, radius) {
      this.count = count;
      this.radius = radius;
      this.positions = new Float32Array(count * 3);
      this.colors = new Float32Array(count * 3);
      this.sizes = new Float32Array(count);
      this.visible = new Uint8Array(count);
      // 1 where the Analysis tab highlighted the object: drawn larger, colour unchanged.
      this.highlighted = new Uint8Array(count);
      this.keyframes = null;
      this.baseSize = 4.0;
      this.selectedIndex = -1;
      this.savedColour = null;

      this.geometry = new THREE.BufferGeometry();
      this.geometry.setAttribute('position', new THREE.BufferAttribute(this.positions, 3));
      this.geometry.setAttribute('color', new THREE.BufferAttribute(this.colors, 3));
      this.geometry.setAttribute('size', new THREE.BufferAttribute(this.sizes, 1));
      this.geometry.setAttribute('satIndex', new THREE.BufferAttribute(Float32Array.from({length: count}, (_, i) => i), 1));

      this.material = new THREE.ShaderMaterial({
        uniforms: { selected: {value: -1}, reveal: {value: 1}, focus: {value: 0} },
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        vertexColors: true,
        vertexShader: [
          'attribute float size;',
          'attribute float satIndex;',
          'uniform float selected;',
          'uniform float reveal;',
          'varying float vSelected;',
          'varying float vVisible;',
          'varying vec3 vColor;',
          'void main() {',
          '  vColor = color;',
          '  vVisible = step(0.1, size);',
          '  vSelected = 1.0 - step(0.5, abs(satIndex - selected));',
          '  vec4 mv = modelViewMatrix * vec4(position, 1.0);',
          '  gl_Position = projectionMatrix * mv;',
          // Perspective-scaled but clamped, so markers stay sensible at any zoom.
          '  float scale = clamp(260.0 / max(1.0, -mv.z), 0.45, 2.4);',
          '  gl_PointSize = size * scale * mix(1.0, mix(0.455, 1.0, reveal), vSelected);',
          '}',
        ].join('\n'),
        fragmentShader: [
          'varying vec3 vColor;',
          'varying float vSelected;',
          'varying float vVisible;',
          'uniform float focus;',
          'void main() {',
          '  if (vVisible < 0.5) discard;',
          '  vec2 d = gl_PointCoord - vec2(0.5);',
          '  float r = length(d) * 2.0;',
          '  if (r > 1.0) discard;',
          // Solid core plus soft halo: the glowing dot, with no extra geometry.
          // Intensity is kept below 1 so additive blending does not clip every
          // marker to white and destroy the category colours.
          '  float core = smoothstep(0.46, 0.16, r);',
          '  float halo = pow(max(0.0, 1.0 - r), 2.4) * 0.45;',
          '  float alpha = clamp(core * 0.95 + halo, 0.0, 1.0);',
          '  gl_FragColor = vec4(vColor * (0.45 + 0.55 * core), alpha * (1.0 - focus * 0.12 * (1.0 - vSelected)));',
          '}',
        ].join('\n'),
      });

      this.points = new THREE.Points(this.geometry, this.material);
      this.points.frustumCulled = false;
      this._raycaster = new THREE.Raycaster();
      this._ndc = new THREE.Vector2();
    }

    /** Place one vertex from geodetic degrees + globe-radius altitude fraction. */
    placeVertex(index, lat, lng, altRatio) {
      const phi = (90 - lat) * DEG;
      const theta = (90 - lng) * DEG;
      const r = this.radius * (1 + altRatio);
      const sinPhi = Math.sin(phi);
      const offset = index * 3;
      this.positions[offset] = r * sinPhi * Math.cos(theta);
      this.positions[offset + 1] = r * Math.cos(phi);
      this.positions[offset + 2] = r * sinPhi * Math.sin(theta);
    }

    setKeyframes(keyframes) {
      this.keyframes = keyframes;
      // Denser constellations get slightly smaller dots so the shell stays readable.
      this.baseSize = this.count > 4000 ? 3.8 : (this.count > 1000 ? 4.6 : 5.8);
    }

    setColours(colours) {
      this.colors.set(colours);
      this.geometry.attributes.color.needsUpdate = true;
    }

    setVisibility(flags) {
      this.visible.set(flags);
      this.geometry.attributes.size.needsUpdate = true;
    }

    /** Flag satellites highlighted from the Analysis tab (1 = emphasised). */
    setHighlights(flags) {
      this.highlighted.set(flags);
      this.geometry.attributes.size.needsUpdate = true;
    }

    /** Interpolate every visible satellite to `tNorm` (0..1 of the window). */
    update(tNorm) {
      const kf = this.keyframes;
      if (!kf) return 0;
      const frames = kf.count;
      const span = Math.max(1, frames - 1);
      const scaled = Math.min(span, Math.max(0, tNorm * span));
      const a = Math.floor(scaled);
      const b = Math.min(frames - 1, a + 1);
      const w = scaled - a;
      const latArr = kf.lat, lngArr = kf.lng, altArr = kf.alt;
      let drawn = 0;
      for (let i = 0; i < this.count; i++) {
        if (!this.visible[i]) { this.sizes[i] = 0; continue; }
        const base = i * frames;
        const lat = latArr[base + a] + (latArr[base + b] - latArr[base + a]) * w;
        let dlng = lngArr[base + b] - lngArr[base + a];
        if (dlng > 180) dlng -= 360; else if (dlng < -180) dlng += 360;
        const lng = lngArr[base + a] + dlng * w;
        const alt = altArr[base + a] + (altArr[base + b] - altArr[base + a]) * w;
        this.placeVertex(i, lat, lng, alt);
        this.sizes[i] = (i === this.selectedIndex) ? this.baseSize * 2.2
          : (this.highlighted[i] ? this.baseSize * 1.9 : this.baseSize);
        drawn++;
      }
      this.geometry.attributes.position.needsUpdate = true;
      this.geometry.attributes.size.needsUpdate = true;
      return drawn;
    }

    /** Highlight one satellite by index, or -1 to clear. */
    setSelection(index, highlight) {
      if (this.selectedIndex === index) return;
      this.selectionStarted = performance.now();
      this.material.uniforms.selected.value = index;
      this.material.uniforms.reveal.value = motion.active ? 0 : 1;
      if (this.selectedIndex >= 0 && this.savedColour) {
        this.colors.set(this.savedColour, this.selectedIndex * 3);
      }
      this.selectedIndex = index;
      this.savedColour = null;
      if (index >= 0) {
        const offset = index * 3;
        this.savedColour = [this.colors[offset], this.colors[offset + 1],
                            this.colors[offset + 2]];
        this.colors[offset] = highlight[0];
        this.colors[offset + 1] = highlight[1];
        this.colors[offset + 2] = highlight[2];
      }
      this.geometry.attributes.color.needsUpdate = true;
      this.geometry.attributes.size.needsUpdate = true;
    }

    /** Uniform-only emphasis; no position updates or full-buffer uploads. */
    tickMotion(now) {
      if (this.selectionStarted == null) return;
      const t = motion.active ? Math.min(1, (now - this.selectionStarted) / 240) : 1;
      const eased = 1 - Math.pow(1 - t, 3);
      this.material.uniforms.reveal.value = eased;
      this.material.uniforms.focus.value = this.selectedIndex >= 0 ? eased : 0;
      if (t === 1) this.selectionStarted = null;
    }

    /** Screen-space picking; returns a visible vertex index or -1. */
    pick(ndcX, ndcY, camera, viewportHeight) {
      const distance = camera.position.length();
      const worldPerPixel =
        (2 * distance * Math.tan(camera.fov * DEG / 2)) / Math.max(1, viewportHeight);
      this._raycaster.params.Points.threshold = 10 * worldPerPixel;
      this._ndc.set(ndcX, ndcY);
      this._raycaster.setFromCamera(this._ndc, camera);
      const hits = this._raycaster.intersectObject(this.points, false);
      for (let h = 0; h < hits.length; h++) {
        const index = hits[h].index;
        if (index < 0 || index >= this.count || !this.visible[index]) continue;
        // Points raycasting does not know about the opaque Earth mesh. Match the
        // rendered visibility before offering a hover/click target.
        const point = new THREE.Vector3().fromArray(this.positions, index * 3);
        if (!earthOccludes(point, camera.position, this.radius)) return index;
      }
      return -1;
    }
  }

  // ------------------------------------------------------------------ //
  // 5. Globe, HUD and interaction
  // ------------------------------------------------------------------ //
  const HIGHLIGHT = [1.0, 1.0, 1.0];

  function earthOccludes(point, cameraPosition, radius) {
    const ray = point.clone().sub(cameraPosition);
    const distance = ray.length();
    if (!distance) return false;
    ray.multiplyScalar(1 / distance);
    const t = Math.min(distance, Math.max(0, -cameraPosition.dot(ray)));
    return cameraPosition.clone().addScaledVector(ray, t).length() < radius;
  }

  function hexToRgb(hex) {
    const value = hex.replace('#', '');
    return [
      parseInt(value.slice(0, 2), 16) / 255,
      parseInt(value.slice(2, 4), 16) / 255,
      parseInt(value.slice(4, 6), 16) / 255,
    ];
  }

  let earthReady = false, sceneReady = false, revealScheduled = false;
  let sunlight = null;
  function revealScene() {
    if (!earthReady || !sceneReady || revealScheduled) return;
    revealScheduled = true;
    // Let the shared canvas paint Earth, points and any selected trail before
    // revealing it. This runs once, never on ordinary Streamlit reruns.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.getElementById('stage').classList.add('ready');
      state.lastFrameMs = performance.now();
      setPlaying(true);
      hideStatus();
    }));
  }

  function initGlobe() {
    if (typeof Globe !== 'function') {
      showStatus('The 3D engine could not be loaded.',
        'vendor/globe.gl.min.js is missing or blocked');
      return null;
    }
    // Configure assets and readiness before mounting: mounting first can emit
    // readiness for an untextured globe before our callback is registered.
    const globe = Globe({ animateIn: false, rendererConfig: { antialias: false, alpha: true } });
    globe.backgroundColor('rgba(0,0,0,0)')
         .globeImageUrl('./assets/earth-blue-marble.jpg')
         .bumpImageUrl('./assets/earth-topology.png')
         .showAtmosphere(false)
         .pathPoints('points').pathPointLat('lat').pathPointLng('lng')
         .pathPointAlt('alt').pathColor('color').pathStroke('stroke')
         .pathTransitionDuration(0)
         .onGlobeReady(() => { earthReady = true; revealScene(); })
         (document.getElementById('globe'));

    const material = globe.globeMaterial();
    material.shininess = 9;
    material.specular.setRGB(0.045, 0.045, 0.045);
    material.bumpScale = 0.28;

    const controls = globe.controls();
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.rotateSpeed = 0.45;
    controls.zoomSpeed = 0.7;
    controls.minDistance = 118;
    controls.maxDistance = 900;
    controls.enablePan = false;
    controls.autoRotate = false;

    state.globe = globe;
    const stars = createStarfield();
    stars.material.uniforms.pixelRatio.value = globe.renderer().getPixelRatio();
    globe.scene().add(stars);
    sunlight = createSunlight(globe);
    state.radius = globe.getGlobeRadius();
    return globe;
  }

  // -- playback ------------------------------------------------------- //

  function windowSeconds() {
    if (!state.keyframes) return 1;
    const times = state.keyframes.times;
    return Math.max(1, (times[times.length - 1] - times[0]) / 1000);
  }

  function currentTime() {
    if (!state.keyframes) return null;
    const times = state.keyframes.times;
    return new Date(times[0].getTime() + state.tNorm * windowSeconds() * 1000);
  }

  function setTimeUi() {
    const time = currentTime();
    const clock = document.getElementById('sim-clock');
    if (clock && time) clock.textContent = formatUtc(time);
    const slider = document.getElementById('time-slider');
    if (slider && Math.abs(parseFloat(slider.value) - state.tNorm) > 0.0005) {
      slider.value = String(state.tNorm);
    }
  }

  function redraw() {
    if (!layer) return;
    if (sunlight) sunlight.update(currentTime());
    const started = performance.now();
    const drawn = layer.update(state.tNorm);
    const elapsed = performance.now() - started;
    state.frameStats.updateMs = state.frameStats.updateMs * 0.9 + elapsed * 0.1;
    state.drawnCount = drawn;
    setTimeUi();
    updateCounts();
  }

  function tick(now) {
    if (layer) layer.tickMotion(now);
    const dt = state.lastFrameMs ? (now - state.lastFrameMs) / 1000 : 0;
    state.lastFrameMs = now;
    if (state.playing && state.keyframes) {
      const advance = (dt * state.speed) / windowSeconds();
      state.tNorm += advance;
      if (state.tNorm >= 1) state.tNorm = 0;    // loop the bounded window
      redraw();
    }
    updateSelectionMarker(); // Also follows camera drags while playback is paused.
    // fps counter
    state.frameStats.frames += 1;
    if (!state.frameStats.startedAt) state.frameStats.startedAt = now;
    if (now - state.frameStats.startedAt >= 1000) {
      state.frameStats.fps =
        state.frameStats.frames * 1000 / (now - state.frameStats.startedAt);
      state.frameStats.frames = 0;
      state.frameStats.startedAt = now;
      updateCounts();
    }
    requestAnimationFrame(tick);
  }

  function updateCounts() {
    const visible = document.getElementById('visible-count');
    const total = document.getElementById('total-count');
    const fps = document.getElementById('fps-count');
    if (visible) visible.textContent = String(state.drawnCount || 0);
    if (total) total.textContent = String(state.metadata.length);
    if (fps) {
      fps.textContent =
        state.frameStats.fps.toFixed(0) + ' fps \u00b7 ' +
        state.frameStats.updateMs.toFixed(1) + ' ms';
    }
  }

  // ------------------------------------------------------------------ //
  // 6. Scene assembly
  // ------------------------------------------------------------------ //
  function decodeFloat32(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Float32Array(bytes.buffer);
  }

  function buildColours(metadata, groupSpecs) {
    const colours = new Float32Array(metadata.length * 3);
    for (let i = 0; i < metadata.length; i++) {
      const spec = groupSpecs[metadata[i].g] || { colour: '#8fb4d8' };
      const rgb = hexToRgb(spec.colour);
      colours[i * 3] = rgb[0];
      colours[i * 3 + 1] = rgb[1];
      colours[i * 3 + 2] = rgb[2];
    }
    return colours;
  }

  function visibilityFor(metadata, groupSpecs, enabled) {
    const flags = new Uint8Array(metadata.length);
    for (let i = 0; i < metadata.length; i++) {
      const spec = groupSpecs[metadata[i].g];
      flags[i] = spec && enabled.has(spec.id) ? 1 : 0;
    }
    return flags;
  }

  function rebuildLayer(payload) {
    const globe = state.globe;
    if (layer) {
      globe.scene().remove(layer.points);
      layer.geometry.dispose();
      layer.material.dispose();
      layer = null;
    }
    const metadata = payload.satellites || [];
    state.metadata = metadata;
    state.groupSpecs = payload.groups || [];
    layer = new PointLayer(metadata.length, state.radius);
    layer.setColours(buildColours(metadata, state.groupSpecs));
    globe.scene().add(layer.points);
  }

  function applyGroupFilters(initial) {
    if (initial) {
      state.enabledGroups = new Set(
        state.groupSpecs.filter((g) => g.enabled).map((g) => g.id));
    }
    layer.setVisibility(
      visibilityFor(state.metadata, state.groupSpecs, state.enabledGroups));
    renderGroupControls();
    redraw();
  }

  function applySelection(noradId) {
    state.selectedId = noradId || null;
    let index = -1;
    if (noradId) {
      const needle = Number(noradId);
      for (let i = 0; i < state.metadata.length; i++) {
        if (state.metadata[i].id === needle) { index = i; break; }
      }
    }
    state.selection = index;
    if (index >= 0) {
      // Reuse the same marker and information panel path for Python selections.
      selectIndex(index, false);
    } else {
      if (layer) layer.setSelection(index, HIGHLIGHT);
      document.getElementById('selected-name').textContent = 'click a satellite';
      document.getElementById('selected-meta').textContent = '';
      redraw();
    }
  }

  /**
   * Emphasise the satellites selected in the Analysis tab.
   * Independent of `selection` (the globe's own click): this only enlarges markers, so a
   * highlighted set and a selected object can coexist.
   */
  function applyHighlights(noradIds) {
    const wanted = new Set((noradIds || []).map(Number));
    const flags = new Uint8Array(state.metadata.length);
    let count = 0;
    for (let i = 0; i < state.metadata.length; i++) {
      if (wanted.has(state.metadata[i].id)) { flags[i] = 1; count++; }
    }
    state.highlightCount = count;
    if (layer) layer.setHighlights(flags);
    redraw();
  }

  function updateSelectionMarker() {
    const marker = document.getElementById('selection-marker');
    const i = state.selection;
    marker.hidden = true;
    if (!layer || i == null || i < 0 || !layer.visible[i]) return;
    const point = new THREE.Vector3().fromArray(layer.positions, i * 3);
    if (![point.x, point.y, point.z].every(Number.isFinite)) return;
    const camera = state.globe.camera();
    const projected = point.clone().project(camera);
    if (Math.abs(projected.x) > .98 || Math.abs(projected.y) > .95 ||
        projected.z < -1 || projected.z > 1) return;
    // Do not draw a label through Earth when the user rotates to the far side.
    if (earthOccludes(point, camera.position, state.radius)) return;
    const canvas = state.globe.renderer().domElement;
    marker.style.left = ((projected.x + 1) * canvas.clientWidth / 2) + 'px';
    marker.style.top = ((1 - projected.y) * canvas.clientHeight / 2) + 'px';
    marker.querySelector('span').textContent = state.metadata[i].name + ' · ' + state.selectedId;
    marker.hidden = false;
  }

  function applyAnalysisAction(action) {
    if (!action || action.revision === state.lastAnalysisAction) return;
    state.lastAnalysisAction = action.revision;
    const wanted = new Set((action.ids || []).map(Number));
    const indices = [];
    state.metadata.forEach((meta, i) => {
      if (!wanted.has(meta.id)) return;
      const group = state.groupSpecs[meta.g];
      if (group) state.enabledGroups.add(group.id);
      indices.push(i);
    });
    // An explicit action reveals its group even if it was hidden locally.
    // It does not alter loaded data, simulation time or playback state.
    applyGroupFilters(false);
    const points = indices.map(i => new THREE.Vector3().fromArray(layer.positions, i * 3))
      .filter(p => [p.x, p.y, p.z].every(Number.isFinite) && p.length() > 0);
    if (!points.length) return;
    const globe = state.globe, radius = state.radius;
    let view, distance;
    if (action.kind === 'select') {
      const p = points[0], r = p.length();
      distance = Math.max(3.5 * radius, r + 2 * radius);
      view = {lat: Math.asin(p.y / r) / DEG, lng: Math.atan2(p.x, p.z) / DEG};
    } else {
      // Fit the highlighted orbital heights without inventing a common location.
      const r = Math.max(...points.map(p => p.length()));
      const halfFov = Math.atan(Math.tan(globe.camera().fov * DEG / 2) * Math.min(1, globe.camera().aspect));
      distance = Math.max(globe.camera().position.length(), r / Math.sin(halfFov) * 1.12);
      view = globe.pointOfView();
    }
    globe.controls().maxDistance = Math.max(globe.controls().maxDistance, distance * 1.1);
    globe.pointOfView({...view, altitude: distance / radius - 1}, 0);
    updateSelectionMarker();
  }

  function drawSelectedTrack(track) {
    if (!state.globe) return;
    if (!track || track.length < 2) {
      state.globe.pathsData([]);
      return;
    }
    const points = track.map((p) => ({ lat: p[0], lng: p[1], alt: p[2] }));
    state.globe.pathsData([
      { points: points, color: 'rgba(255, 255, 255, 0.10)', stroke: 3.2 },
      { points: points, color: 'rgba(255, 236, 180, 0.85)', stroke: 1.0 },
    ]);
  }

  function applyScene(payload) {
    const sceneChanged = payload.scene_id !== state.sceneId || !layer;
    if (sceneChanged) {
      rebuildLayer(payload);
      state.sceneId = payload.scene_id;
    }
    // Keyframes are only shipped when the scene changes; reuse otherwise.
    if (payload.keyframes) {
      const kf = payload.keyframes;
      layer.setKeyframes({
        count: kf.count,
        lat: decodeFloat32(kf.lat),
        lng: decodeFloat32(kf.lng),
        alt: decodeFloat32(kf.alt),
        times: kf.times.map((t) => new Date(t)),
      });
      state.keyframes = layer.keyframes;
      const windowInfo = payload.window || {};
      const note = document.getElementById('window-note');
      if (note) note.textContent = windowInfo.note || '';

      // A new scene is anchored at "now": the simulation always opens on the start of
      // its window, never part-way through a previous one.
      state.tNorm = 0;
      const range = document.getElementById('window-range');
      if (range && windowInfo.start && windowInfo.end) {
        const start = new Date(windowInfo.start);
        const end = new Date(windowInfo.end);
        const minutes = Math.round((end - start) / 60000);
        range.textContent =
          'start ' + formatUtc(start) +
          '  \u00b7  range 0 \u2192 +' + minutes + ' min' +
          '  \u00b7  orbital prediction, not live tracking';
      }
    }
    if (!layer.keyframes) {
      showStatus('No position samples were supplied.', 'payload.keyframes missing');
      return;
    }
    applyGroupFilters(sceneChanged);
    // A light rerun can arrive before Python has stored the click (payload.selection is
    // still null). Treat null on an unchanged scene as "no news", not "deselect", so the
    // highlight never flickers; a scene change always applies the payload verbatim.
    if (payload.selection != null || sceneChanged) {
      applySelection(payload.selection);
    }
    // Highlight comes from the Analysis tab. An explicit list (even empty) is news; a
    // missing field means an older payload and changes nothing.
    if (Array.isArray(payload.highlight_ids)) {
      applyHighlights(payload.highlight_ids);
    }
    drawSelectedTrack(payload.selected_track);
    applyAnalysisAction(payload.analysis_action);
    redraw();
    sceneReady = true;
    revealScene();
  }

  // ------------------------------------------------------------------ //
  // 7. HUD wiring
  // ------------------------------------------------------------------ //
  function renderGroupControls() {
    const host = document.getElementById('filters');
    if (!host) return;
    host.innerHTML = '';
    state.groupSpecs.forEach((spec) => {
      const row = document.createElement('label');
      row.className = 'filter-row';
      row.title = spec.ok
        ? spec.id + ' \u00b7 ' + spec.source + ' \u00b7 ' + spec.count + ' objects'
        : (spec.error || 'not loaded');

      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = state.enabledGroups.has(spec.id);
      box.addEventListener('change', () => {
        if (box.checked) state.enabledGroups.add(spec.id);
        else state.enabledGroups.delete(spec.id);
        // Filtering is local: no data is re-requested and Python is not re-run.
        applyGroupFilters(false);
      });

      const dot = document.createElement('i');
      dot.className = 'dot';
      dot.style.background = spec.ok ? spec.colour : '#4a5a70';

      const name = document.createElement('span');
      name.className = 'filter-name';
      name.textContent = spec.label;

      const count = document.createElement('b');
      count.className = 'filter-count';
      count.textContent = spec.ok ? String(spec.count) : 'n/a';
      if (!spec.ok) count.classList.add('bad');

      row.appendChild(box);
      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(count);
      host.appendChild(row);
    });
  }

  function setPlaying(playing) {
    state.playing = playing;
    const button = document.getElementById('play-button');
    if (button) {
      button.textContent = playing ? '\u2016 Pause' : '\u25b6 Play';
      button.classList.toggle('active', playing);
    }
    state.lastFrameMs = 0;
  }

  function ndcFromEvent(event) {
    const canvas = state.globe.renderer().domElement;
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * 2 - 1,
      y: -((event.clientY - rect.top) / rect.height) * 2 + 1,
      px: event.clientX - rect.left,
      py: event.clientY - rect.top,
      height: rect.height,
    };
  }

  function hideTooltip() {
    state.hoverIndex = -1;
    if (tooltipEl) tooltipEl.classList.remove('visible');
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function handleHover(event) {
    if (!layer) return;
    const ndc = ndcFromEvent(event);
    const index = layer.pick(ndc.x, ndc.y, state.globe.camera(), ndc.height);
    state.hoverIndex = index;
    if (index < 0) { hideTooltip(); return; }
    const meta = state.metadata[index];
    const spec = state.groupSpecs[meta.g] || {};
    tooltipEl.innerHTML =
      '<b>' + escapeHtml(meta.name) + '</b>' +
      '<span>NORAD ' + meta.id + ' \u00b7 ' + escapeHtml(spec.label || '') + '</span>' +
      '<span>' + meta.alt.toFixed(1) + ' km \u00b7 ' + meta.incl.toFixed(2) + '\u00b0</span>';
    tooltipEl.style.left = Math.min(ndc.px + 14, 400) + 'px';
    tooltipEl.style.top = Math.max(10, ndc.py - 12) + 'px';
    tooltipEl.classList.add('visible');
  }

  function selectIndex(index, notifyPython) {
    if (index < 0 || index >= state.metadata.length) return;
    const meta = state.metadata[index];
    const changed = layer && layer.selectedIndex !== index;
    state.selection = index;
    state.selectedId = meta.id;
    if (layer) layer.setSelection(index, HIGHLIGHT);
    redraw();
    document.getElementById('selected-name').textContent = meta.name;
    document.getElementById('selected-meta').textContent =
      'NORAD ' + meta.id + ' \u00b7 ' + meta.alt.toFixed(1) + ' km \u00b7 ' +
      meta.incl.toFixed(2) + '\u00b0 \u00b7 ' + meta.period.toFixed(2) + ' min';
    if (changed) motion.emphasize(document.getElementById('panel-selection'));
    if (notifyPython) {
      state.lastSentSelection = meta.id;
      state.selectionEvent = (state.selectionEvent || 0) + 1;
      const simTime = currentTime() || new Date();
      Bridge.setValue({
        event: 'select',
        norad_id: meta.id,
        event_id: state.selectionEvent,
        name: meta.name,
        sim_time: simTime.toISOString(),
      });
    }
  }

  function handleClick(event) {
    if (!layer) return;
    const ndc = ndcFromEvent(event);
    const index = layer.pick(ndc.x, ndc.y, state.globe.camera(), ndc.height);
    if (index >= 0) selectIndex(index, true);
  }

  function wireControls() {
    const play = document.getElementById('play-button');
    if (play) play.addEventListener('click', () => setPlaying(!state.playing));

    const reset = document.getElementById('reset-button');
    if (reset) {
      reset.addEventListener('click', () => {
        setPlaying(false);
        state.tNorm = 0;
        redraw();
      });
    }

    const speed = document.getElementById('speed-select');
    if (speed) {
      speed.addEventListener('change', () => { state.speed = Number(speed.value) || 60; });
    }

    const slider = document.getElementById('time-slider');
    if (slider) {
      slider.addEventListener('input', () => {
        state.tNorm = Number(slider.value);
        redraw();
      });
    }

    const canvas = state.globe.renderer().domElement;
    let lastHover = 0;
    let pointerStart = null, dragged = false;
    canvas.addEventListener('pointerdown', event => {
      pointerStart = {x:event.clientX, y:event.clientY};
      dragged = false;
    });
    canvas.addEventListener('pointermove', event => {
      if (pointerStart && Math.hypot(event.clientX-pointerStart.x, event.clientY-pointerStart.y) > 5) {
        dragged = true;
        hideTooltip();
      }
    });
    canvas.addEventListener('pointerup', () => { pointerStart = null; });
    canvas.addEventListener('pointercancel', () => { pointerStart = null; dragged = true; });
    canvas.addEventListener('mousemove', (event) => {
      if (pointerStart) return;
      const now = performance.now();
      if (now - lastHover < 45) return;      // throttle picking to ~22 Hz
      lastHover = now;
      handleHover(event);
    });
    canvas.addEventListener('mouseleave', hideTooltip);
    canvas.addEventListener('click', event => {
      if (!dragged) handleClick(event);
    });
  }

  // ------------------------------------------------------------------ //
  // 8. Render handler and boot
  // ------------------------------------------------------------------ //
  function resizeGlobe(height) {
    if (!state.globe) return;
    const host = document.getElementById('globe');
    state.globe.width(host.clientWidth || window.innerWidth)
                .height(height || host.clientHeight || 700);
  }

  function handleRender(args) {
    const height = typeof args.height === 'number' ? args.height : 700;
    Bridge.setHeight(height);
    if (!state.globe) {
      const globe = initGlobe();
      if (!globe) return;
      wireControls();
      requestAnimationFrame(tick);
    }
    resizeGlobe(height);
    const payload = args.payload;
    if (!payload || !payload.satellites) {
      showStatus('No scene data was supplied to the globe.',
        'Python sent an empty payload');
      return;
    }
    try {
      applyScene(payload);
    } catch (error) {
      showStatus('The scene could not be rendered.', error && error.message);
      throw error;
    }
  }

  // Debug handle for the browser smoke test and the developer console.
  window.__orbix = {
    motion: motion,
    state: state,
    get layer() { return layer; },
    get metadata() { return state.metadata; },
    get counts() {
      return {
        total: state.metadata.length,
        drawn: state.drawnCount || 0,
        keyframes: state.keyframes ? state.keyframes.count : 0,
      };
    },
    get perf() {
      return {
        fps: +state.frameStats.fps.toFixed(1),
        updateMs: +state.frameStats.updateMs.toFixed(2),
      };
    },
    get simTime() { return currentTime(); },
    get highlightCount() { return state.highlightCount || 0; },
    // Behaves like a real marker click: notifies Python so the payload comes back with
    // the selected satellite's trail.
    select: (index) => selectIndex(index, true),
    setPlaying: setPlaying,
    setTime: (t) => { state.tNorm = t; redraw(); },
    setHighlights: (ids) => applyHighlights(ids),
    toggleGroup: (id, on) => {
      if (on) state.enabledGroups.add(id); else state.enabledGroups.delete(id);
      applyGroupFilters(false);
    },
  };

  Bridge.onRender(handleRender);
  Bridge.ready();
  Bridge.setHeight(document.body.clientHeight || 700);
  window.addEventListener('resize', () => {
    resizeGlobe(document.getElementById('globe').clientHeight);
  });
})();


