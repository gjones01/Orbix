import * as THREE from './vendor/three.module.min.js';

/** Decorative sky directions, not an astronomical star catalogue. One draw call. */
export function createStarfield() {
  const count = 900;
  const positions = new Float32Array(count * 3);
  const brightness = new Float32Array(count);
  let seed = 7143;
  const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
  for (let i = 0; i < count; i++) {
    const y = random() * 2 - 1, angle = random() * Math.PI * 2;
    const r = Math.sqrt(1 - y * y);
    positions.set([r * Math.cos(angle), y, r * Math.sin(angle)], i * 3);
    brightness[i] = .25 + random() * .55;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('brightness', new THREE.BufferAttribute(brightness, 1));
  const material = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { pixelRatio: { value: 1 } },
    vertexShader: `
      attribute float brightness;
      uniform float pixelRatio;
      varying float intensity;
      void main() {
        intensity = brightness;
        // A direction (w=0) follows camera rotation but ignores camera distance.
        vec4 clip = projectionMatrix * modelViewMatrix * vec4(position, 0.0);
        clip.z = clip.w * 0.99999;
        gl_Position = clip;
        gl_PointSize = (1.3 + brightness * 2.0) * pixelRatio;
      }`,
    fragmentShader: `
      varying float intensity;
      void main() {
        float r = length(gl_PointCoord - vec2(0.5)) * 2.0;
        if (r > 1.0) discard;
        gl_FragColor = vec4(0.80, 0.88, 1.0, intensity * pow(1.0-r, 1.4));
      }`,
  });
  const stars = new THREE.Points(geometry, material);
  stars.name = 'orbix-starfield';
  stars.frustumCulled = false;
  stars.renderOrder = -1;
  stars.raycast = () => {}; // Never steal a globe drag or satellite click.
  return stars;
}
