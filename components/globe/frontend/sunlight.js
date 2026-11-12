import * as THREE from './vendor/three.module.min.js';

const DEG = Math.PI / 180;

// Approximate solar coordinates and sidereal time, US Naval Observatory:
// https://aa.usno.navy.mil/faq/sun_approx and /faq/GAST.
// UTC is used as an approximation to UT1/TT: visual lighting, not eclipse analysis.
export function solarPosition(date) {
  const jd = date.getTime() / 86400000 + 2440587.5;
  const d = jd - 2451545;
  const g = (357.529 + .98560028 * d) * DEG;
  const q = 280.459 + .98564736 * d;
  const longitude = (q + 1.915 * Math.sin(g) + .020 * Math.sin(2 * g)) * DEG;
  const obliquity = (23.439 - .00000036 * d) * DEG;
  const ra = Math.atan2(Math.cos(obliquity) * Math.sin(longitude), Math.cos(longitude)) / DEG;
  const lat = Math.asin(Math.sin(obliquity) * Math.sin(longitude)) / DEG;
  const midnight = Math.floor(jd - .5) + .5;
  const hours = (jd - midnight) * 24;
  const t = d / 36525;
  const gmst = (6.697375 + .065709824279 * (midnight - 2451545) + 1.0027379 * hours + .0000258 * t * t) * 15;
  const lng = ((ra - gmst + 180) % 360 + 360) % 360 - 180;
  return { lat, lng };
}

export function createSunlight(globe) {
  const light = new THREE.DirectionalLight(0xffffff, 3);
  light.name = 'orbix-sunlight';
  // Low neutral fill keeps geography readable without flattening day/night contrast.
  const fill = new THREE.AmbientLight(0xffffff, 0.3);
  fill.name = 'orbix-night-fill';
  globe.lights([light, fill]);
  const direction = new THREE.Vector3();
  const material = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { direction: { value: direction } },
    vertexShader: `
      uniform vec3 direction;
      varying vec2 disc;
      void main() {
        disc = uv * 2.0 - 1.0;
        vec4 clip = projectionMatrix * viewMatrix * vec4(direction, 0.0);
        // Local five-degree flare, deliberately stylized for legibility.
        clip.xy += disc * 0.08749 * vec2(projectionMatrix[0][0], projectionMatrix[1][1]) * clip.w;
        clip.z = clip.w * 0.99998;
        gl_Position = clip;
      }`,
    fragmentShader: `
      varying vec2 disc;
      void main() {
        float r = length(disc);
        if (r > 1.0) discard;
        float core = 1.0 - smoothstep(0.095, 0.15, r);
        float edge = 1.0 - smoothstep(0.65, 1.0, r);
        float glow = 1.25 * exp(-r * 5.5) * edge;
        float rays = (0.55 * exp(-abs(disc.y) * 110.0)
                    + 0.35 * exp(-abs(disc.x) * 110.0)
                    + 0.24 * exp(-abs(disc.x-disc.y) * 100.0)
                    + 0.24 * exp(-abs(disc.x+disc.y) * 100.0))
                    * pow(1.0-r, 2.0) * edge;
        float alpha = max(core, min(0.92, glow + rays * 1.35));
        vec3 color = mix(vec3(1.0,0.80,0.48), vec3(1.0,1.0,0.98),
                         max(core, exp(-r*9.0)));
        gl_FragColor = vec4(color, alpha);
      }`,
  });
  const sun = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
  sun.name = 'orbix-sun';
  sun.frustumCulled = false;
  sun.raycast = () => {};
  globe.scene().add(sun);
  globe.scene().add(createLensFlare(direction, globe.getGlobeRadius()));
  let lastTime = null;
  return {
    update(date) {
      if (!date || date.getTime() === lastTime) return;
      lastTime = date.getTime();
      const { lat, lng } = solarPosition(date);
      direction.copy(globe.getCoords(lat, lng)).normalize();
      light.position.copy(direction).multiplyScalar(10000);
      sun.userData.subsolar = { lat, lng };
    },
  };
}

/** Camera-space optical ghosts, batched into one draw call with no update loop. */
function createLensFlare(direction, earthRadius) {
  const positions = [], settings = [], colors = [];
  const ghosts = [
    [-0.18, .035, 1, .85, .50], [.18, .065, .55, .78, 1],
    [.38, .025, 1, .72, .32], [.62, .085, .55, 1, .68],
    [.85, .045, .55, .74, 1], [1.12, .11, 1, .78, .48],
  ];
  for (const [along, radius, r, g, b] of ghosts) {
    for (const [x,y] of [[-1,-1],[1,-1],[1,1],[-1,-1],[1,1],[-1,1]]) {
      positions.push(x,y,0); settings.push(along,radius); colors.push(r,g,b);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('ghost', new THREE.Float32BufferAttribute(settings,2));
  geometry.setAttribute('tint', new THREE.Float32BufferAttribute(colors,3));
  const material = new THREE.ShaderMaterial({
    transparent: true, depthTest: false, depthWrite: false,
    uniforms: { direction: {value:direction}, earthRadius: {value:earthRadius} },
    vertexShader: `
      uniform vec3 direction;
      uniform float earthRadius;
      attribute vec2 ghost;
      attribute vec3 tint;
      varying vec2 disc;
      varying vec3 color;
      varying float visibility;
      void main() {
        vec4 source = projectionMatrix * viewMatrix * vec4(direction,0.0);
        vec2 screen = source.xy / max(source.w,0.0001);
        float edge = max(abs(screen.x),abs(screen.y));
        float alongRay = dot(cameraPosition,direction);
        float closest = sqrt(max(0.0,dot(cameraPosition,cameraPosition)-alongRay*alongRay));
        float clearEarth = alongRay < 0.0 ? smoothstep(earthRadius-1.0,earthRadius+1.0,closest) : 1.0;
        visibility = step(0.0001,source.w) * (1.0-smoothstep(0.8,1.02,edge)) * clearEarth;
        vec2 center = screen * (1.0-2.0*ghost.x);
        vec2 size = vec2(projectionMatrix[0][0]/projectionMatrix[1][1],1.0) * ghost.y;
        gl_Position = vec4(center + position.xy*size,0.0,1.0);
        disc = position.xy;
        color = tint;
      }`,
    fragmentShader: `
      varying vec2 disc;
      varying vec3 color;
      varying float visibility;
      void main() {
        float r = length(disc);
        if(r>1.0 || visibility<0.001) discard;
        float ring = exp(-pow((r-0.80)/0.075,2.0));
        float interior = (1.0-smoothstep(0.1,0.9,r))*0.018;
        gl_FragColor = vec4(color,visibility*(ring*0.19+interior));
      }`,
  });
  const flare = new THREE.Mesh(geometry,material);
  flare.name = 'orbix-lens-flare';
  flare.frustumCulled = false;
  flare.renderOrder = 1000;
  flare.raycast = () => {};
  return flare;
}
