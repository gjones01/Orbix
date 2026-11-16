/** Optional camera rig. All vectors use the renderer's Earth-fixed frame.
 * No new propagation, renderer, network traffic, or animation loop.
 */
import * as THREE from './vendor/three.module.min.js';

export function createTracking(globe, state, getLayer) {
  const camera = globe.camera(), controls = globe.controls();
  const panel = document.getElementById('panel-tracking');
  const note = document.getElementById('tracking-note');
  const follow = document.getElementById('follow-satellite');
  const perspective = document.getElementById('satellite-perspective');
  const earth = document.getElementById('return-earth');
  const distance = document.getElementById('tracking-distance');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const p = new THREE.Vector3(), radial = new THREE.Vector3();
  const before = new THREE.Vector3(), after = new THREE.Vector3();
  const along = new THREE.Vector3(), previousAlong = new THREE.Vector3(0, 1, 0);
  const desired = new THREE.Vector3(), target = new THREE.Vector3();
  const fromDirection = new THREE.Vector3(), toDirection = new THREE.Vector3();
  const forward = new THREE.Vector3(), up = new THREE.Vector3();
  const rotation = new THREE.Quaternion(), identity = new THREE.Quaternion();
  const orientation = new THREE.Quaternion(), lookOffset = new THREE.Quaternion();
  const matrix = new THREE.Matrix4(), euler = new THREE.Euler(0, 0, 0, 'YXZ');
  let mode = 'earth', saved = null, elapsed = 0, selected = null;
  let yaw = 0, pitch = 0, pointer = null, message = '';
  let uiSignature = '';

  function valid() {
    const layer = getLayer(), i = state.selection;
    if (!layer || i == null || !layer.visible[i]) return false;
    p.fromArray(layer.positions, i * 3);
    return Number.isFinite(p.lengthSq()) && p.length() > state.radius + .05;
  }

  // Sample only the selected object's existing keyframes, with the same shortest
  // longitude interpolation and coordinate mapping as PointLayer.placeVertex.
  function sample(t, out) {
    const k = state.keyframes, n = k.count, base = state.selection * n;
    const scaled = Math.max(0, Math.min(1, t)) * (n - 1);
    const a = Math.floor(scaled), b = Math.min(n - 1, a + 1), w = scaled - a;
    const lat = k.lat[base+a] + (k.lat[base+b]-k.lat[base+a])*w;
    let dl = k.lng[base+b]-k.lng[base+a];
    if (dl > 180) dl -= 360; else if (dl < -180) dl += 360;
    const lng = (k.lng[base+a]+dl*w)*Math.PI/180;
    const phi = (90-lat)*Math.PI/180;
    const r = state.radius*(1+k.alt[base+a]+(k.alt[base+b]-k.alt[base+a])*w);
    return out.set(r*Math.sin(phi)*Math.sin(lng), r*Math.cos(phi), r*Math.sin(phi)*Math.cos(lng));
  }

  function localFrame() {
    radial.copy(p).normalize();
    sample(state.tNorm-.001, before); sample(state.tNorm+.001, after);
    along.subVectors(after, before).addScaledVector(radial, -along.dot(radial));
    // Near-stationary Earth-fixed objects have no reliable along-track direction.
    // Parallel-transport the last tangent, then use a deterministic perpendicular.
    if (!Number.isFinite(along.lengthSq()) || along.lengthSq() < 1e-10) {
      along.copy(previousAlong).addScaledVector(radial, -previousAlong.dot(radial));
      if (along.lengthSq() < 1e-8) {
        along.set(Math.abs(radial.x) < .8 ? 1 : 0, Math.abs(radial.x) < .8 ? 0 : 1, 0);
        along.addScaledVector(radial, -along.dot(radial));
      }
    }
    along.normalize(); previousAlong.copy(along);
  }

  function restore() {
    if (!saved) return;
    camera.position.copy(saved.position); camera.quaternion.copy(saved.quaternion);
    camera.up.copy(saved.up); controls.target.copy(saved.target);
    controls.minDistance = saved.min; controls.maxDistance = saved.max;
    controls.enableDamping = saved.damping; controls.enabled = saved.enabled;
    camera.near = saved.near; camera.updateProjectionMatrix();
    controls.update(); saved = null; mode = 'earth';
  }

  function returnEarth(immediate = false, reason = '') {
    message = reason;
    if (saved) { mode = 'return'; elapsed = 0; if (immediate || reduced.matches) restore(); }
    syncUI();
  }

  function start(next) {
    if (!valid()) { message = 'Select a visible satellite with a valid position.'; syncUI(); return false; }
    if (!saved) {
      saved = {position:camera.position.clone(), quaternion:camera.quaternion.clone(),
        up:camera.up.clone(), target:controls.target.clone(), min:controls.minDistance,
        max:controls.maxDistance, damping:controls.enableDamping, enabled:controls.enabled, near:camera.near};
      // Flush OrbitControls' residual drag before owning the camera.
      controls.enableDamping = false; controls.update();
    }
    controls.enabled = false; controls.minDistance = .001; controls.maxDistance = 1e7;
    camera.near = .02; camera.updateProjectionMatrix();
    mode = next; selected = state.selectedId; elapsed = 0; yaw = pitch = 0; message = '';
    syncUI(); return true;
  }

  function syncUI() {
    const available = valid(), active = mode !== 'earth';
    const phase = reduced.matches ? 3 : elapsed < .35 ? 0 : elapsed < 3 ? 1 : 3;
    const signature = `${available}|${mode}|${phase}|${message}`;
    if (signature === uiSignature) return;
    uiSignature = signature;
    panel.hidden = !available && !active && !message;
    follow.disabled = perspective.disabled = !available;
    follow.setAttribute('aria-pressed', String(mode === 'follow'));
    perspective.setAttribute('aria-pressed', String(mode === 'perspective'));
    earth.hidden = !active;
    distance.parentElement.hidden = mode !== 'follow';
    note.textContent = message || (mode === 'earth' ? 'Explore the selected satellite' :
      mode === 'return' ? 'Returning to Earth View' :
      elapsed < .35 && !reduced.matches ? 'Acquiring satellite…' :
      elapsed < 3 && !reduced.matches ? 'Approaching orbit…' :
      mode === 'follow' ? 'Following predicted position · selection reticle' :
      'Simulated perspective · drag to look · not an onboard camera');
  }

  // Spherical position easing cannot take a straight-line shortcut through Earth.
  function move(alpha) {
    const radius = camera.position.length();
    fromDirection.copy(camera.position).normalize(); toDirection.copy(desired).normalize();
    rotation.setFromUnitVectors(fromDirection, toDirection);
    // Do not alias the destination quaternion with a slerpQuaternions input:
    // Three's initial copy would replace the desired rotation with identity.
    rotation.slerp(identity, 1-alpha);
    camera.position.copy(fromDirection).applyQuaternion(rotation)
      .multiplyScalar(Math.max(state.radius+.15, radius+(desired.length()-radius)*alpha));
    camera.quaternion.slerp(orientation, alpha);
    camera.up.set(0,1,0).applyQuaternion(camera.quaternion);
    forward.set(0,0,-1).applyQuaternion(camera.quaternion);
    controls.target.copy(camera.position).add(forward);
    camera.updateMatrixWorld();
  }

  function update(dt) {
    if (mode === 'earth') { syncUI(); return; }
    dt = Math.min(.05, Math.max(0, dt)); // no jump after a hidden tab or stalled frame
    elapsed += dt;
    if (mode === 'return') {
      desired.copy(saved.position); orientation.copy(saved.quaternion);
      move(1-Math.exp(-4*dt));
      if (elapsed > 2.5) restore();
      syncUI(); return;
    }
    if (!valid()) { returnEarth(false, 'Tracking ended: satellite hidden or position unavailable.'); return; }
    if (selected !== state.selectedId) { selected = state.selectedId; elapsed = 0; yaw = pitch = 0; }
    localFrame();
    // At navigation/geostationary heights Earth subtends a much smaller angle.
    // Tilt the framing toward nadir so it remains a useful visual reference.
    const highOrbit = Math.max(0, Math.min(1, (p.length()/state.radius-1.2)/2));
    if (mode === 'follow') {
      const d = Number(distance.value);
      const outward = .6+.37*highOrbit;
      desired.copy(p).addScaledVector(radial,d*outward)
        .addScaledVector(along,-d*Math.sqrt(1-outward*outward));
      target.copy(p);
    } else {
      desired.copy(p).addScaledVector(radial,.35);
      target.copy(p).addScaledVector(along,10).addScaledVector(radial,-4-36*highOrbit);
    }
    up.copy(radial);
    matrix.lookAt(desired, target, up); orientation.setFromRotationMatrix(matrix);
    if (mode === 'perspective') {
      euler.set(pitch,yaw,0); lookOffset.setFromEuler(euler); orientation.multiply(lookOffset);
    }
    // A brief acquisition beat, then a ~3 second approach. Continued damping
    // also absorbs timeline scrubs, playback loops and changes of selection.
    if (elapsed >= .35 || reduced.matches) {
      const alpha = reduced.matches ? 1 : 1-Math.exp(-(elapsed < 3 ? 2.3 : 8)*dt);
      move(alpha);
    }
    syncUI();
  }

  follow.addEventListener('click', () => start('follow'));
  perspective.addEventListener('click', () => start('perspective'));
  earth.addEventListener('click', () => returnEarth());
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && mode !== 'earth') returnEarth();
    if (mode !== 'perspective' || /INPUT|SELECT|TEXTAREA/.test(event.target.tagName)) return;
    const step = .08;
    if (event.key === 'ArrowLeft') yaw += step;
    else if (event.key === 'ArrowRight') yaw -= step;
    else if (event.key === 'ArrowUp') pitch += step;
    else if (event.key === 'ArrowDown') pitch -= step;
    else return;
    pitch = Math.max(-1.2,Math.min(1.2,pitch)); event.preventDefault();
  });
  const canvas = globe.renderer().domElement;
  canvas.tabIndex = 0;
  canvas.addEventListener('pointerdown', event => {
    if (mode === 'perspective') { pointer = {x:event.clientX,y:event.clientY,id:event.pointerId}; canvas.setPointerCapture(event.pointerId); }
  });
  canvas.addEventListener('pointermove', event => {
    if (!pointer || mode !== 'perspective') return;
    yaw -= (event.clientX-pointer.x)*.004;
    pitch = Math.max(-1.2,Math.min(1.2,pitch-(event.clientY-pointer.y)*.004));
    pointer.x=event.clientX; pointer.y=event.clientY;
  });
  const release = () => { pointer = null; };
  canvas.addEventListener('pointerup',release); canvas.addEventListener('pointercancel',release);
  return {start, returnEarth, update, get mode(){return mode;}, get elapsed(){return elapsed;}};
}
