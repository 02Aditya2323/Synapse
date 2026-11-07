// Procedural neurons for the landing hero: a soma with branching, tapering dendrites.
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A soma with branching, tapering dendrites, merged into one geometry. */
export function neuronGeometry(seed: number, scale: number) {
  const r = rng(seed);
  const parts: THREE.BufferGeometry[] = [];
  const soma = new THREE.IcosahedronGeometry(0.2 * scale, 3);
  const p = soma.getAttribute('position') as THREE.BufferAttribute;
  const v = new THREE.Vector3();
  const k1 = r() * 10, k2 = r() * 10;
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    const n = 1 + 0.16 * Math.sin(v.x * 9 + k1) * Math.cos(v.y * 8 + k2) + 0.08 * Math.sin(v.z * 13);
    v.multiplyScalar(n);
    p.setXYZ(i, v.x, v.y, v.z);
  }
  soma.computeVertexNormals();
  parts.push(soma);

  const branch = (origin: THREE.Vector3, dir: THREE.Vector3, steps: number, radius: number, depth: number) => {
    const pts = [origin.clone()];
    const d = dir.clone();
    const cur = origin.clone();
    for (let s = 0; s < steps; s++) {
      d.add(new THREE.Vector3(r() - 0.5, r() - 0.5, r() - 0.5).multiplyScalar(0.9)).normalize();
      cur.add(d.clone().multiplyScalar((0.16 + r() * 0.16) * scale));
      pts.push(cur.clone());
      if (depth < 2 && s >= 1 && r() < 0.45) {
        const nd = d.clone().add(new THREE.Vector3(r() - 0.5, r() - 0.5, r() - 0.5).multiplyScalar(1.6)).normalize();
        branch(cur.clone(), nd, Math.max(2, steps - s - 1), radius * 0.6, depth + 1);
      }
    }
    const curve = new THREE.CatmullRomCurve3(pts);
    const segs = steps * 4;
    const tube = new THREE.TubeGeometry(curve, segs, radius * scale, 4, false);
    // taper: shrink rings towards the tip
    const tp = tube.getAttribute('position') as THREE.BufferAttribute;
    const ring = 5;
    for (let i = 0; i <= segs; i++) {
      const c = curve.getPointAt(i / segs);
      const f = 1 - (i / segs) * 0.8;
      for (let j = 0; j < ring; j++) {
        const idx = i * ring + j;
        if (idx >= tp.count) continue;
        v.fromBufferAttribute(tp, idx).sub(c).multiplyScalar(f).add(c);
        tp.setXYZ(idx, v.x, v.y, v.z);
      }
    }
    parts.push(tube);
  };
  const count = 5 + Math.floor(r() * 4);
  for (let i = 0; i < count; i++) {
    const dir = new THREE.Vector3(r() - 0.5, r() - 0.5, (r() - 0.5) * 0.8).normalize();
    branch(dir.clone().multiplyScalar(0.17 * scale), dir, 3 + Math.floor(r() * 3), 0.028 - i * 0.0012, 0);
  }
  const g = mergeGeometries(parts.map((x) => (x.index ? x.toNonIndexed() : x)), false)!;
  g.computeVertexNormals();
  return g;
}

export const NEURON_VS = /* glsl */ `
varying vec3 vN; varying vec3 vV; varying vec3 vP;
void main(){
  vec4 wp = modelMatrix * vec4(position, 1.);
  vP = position;
  vN = normalize(normalMatrix * normal);
  vec4 mv = viewMatrix * wp;
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;
export const NEURON_FS = /* glsl */ `
uniform vec3 uColor; uniform float uLit; uniform float uTime; uniform float uAlpha;
varying vec3 vN; varying vec3 vV; varying vec3 vP;
void main(){
  float rim = pow(1. - max(dot(normalize(vN), normalize(vV)), 0.), 2.2);
  float r = length(vP);
  // a membrane shimmer, and a flash that radiates out from the soma when the neuron fires
  float shimmer = .5 + .5 * sin(r * 22. - uTime * 3.);
  float flash = uLit * smoothstep(.0, .25, uLit) * (0.6 + 0.4 * sin(r * 30. - uTime * 18.));
  vec3 base = uColor * (.18 + rim * 1.25 + shimmer * .08);
  vec3 col = base + uColor * flash * 2.4 + vec3(1.) * uLit * rim * .6;
  gl_FragColor = vec4(col, uAlpha * (.55 + rim * .45));
}`;
