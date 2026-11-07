// The hero: an ILLUSTRATIVE workflow that starts life as a living neural network and is
// compiled into silicon as you scroll.
//
//   1. Neurons. Steps are neurons floating in a connectome, the code's order is a chain of
//      axons, and one action potential crawls it.
//   2. Descent. The camera swings overhead, the network settles towards the floor and the
//      wafer powers up beneath it.
//   3. Drop. One by one, chip packages fall onto the neurons and absorb them, with a
//      flash and a shockwave on the floor at each impact.
//   4. Compile. The serial bus is re-routed into the real dependency graph: six reads
//      fire at once, the duplicate get_price merges into its twin, place_order is caged.
//
// Nothing here is a measurement; the page says so under the scene.
import { useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Html, Line, PerformanceMonitor } from '@react-three/drei';
import { EffectComposer, Bloom, Vignette } from '@react-three/postprocessing';
import * as THREE from 'three';
import type { Line2 } from 'three/examples/jsm/lines/Line2.js';
import { DATA_EDGES, EFFECT_EDGES, EFFECT_HEX, HERO_NODES, NODE_INDEX, SEQUENCE_EDGES } from './heroGraph';
import { CHIP_D, CHIP_H, Chip, Wafer, along, manhattan, resample } from '../three/silicon';
import { NEURON_FS, NEURON_VS, neuronGeometry, rng } from '../three/neuron';
import { sound } from '../lib/sound';

export interface HeroInput { morph: number; mx: number; my: number }

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const smooth = (a: number, b: number, x: number) => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/* ------------------------------------------------------------------ timeline */
// All stages read the spring-smoothed scroll value s (0..1).
const DESCENT: [number, number] = [0.05, 0.36]; // camera swing + network settles
const DROP_START = 0.22; // first chip leaves the sky
const DROP_STEP = 0.0185; // gap between chips
const DROP_LEN = 0.085; // one chip's fall
const COMPILE: [number, number] = [0.6, 1.0]; // serial bus -> dependency graph
const FALL_H = 7.5;
const NEURON_Y = 4.2; // the network floats this high above the floor

/** Where each step sits as a neuron: v1's winding chain, standing up in front of the camera. */
const NEURO = HERO_NODES.map((h) => new THREE.Vector3(h.serial[0] * 1.08, h.serial[1] * 1.15 + NEURON_Y, h.serial[2]));
/** As a chip on the serial bus, and once compiled. The die lies in XZ. */
const SERIAL = HERO_NODES.map((_, i) => new THREE.Vector3(-13 + i * (26 / (HERO_NODES.length - 1)), 0, Math.sin(i * 0.9) * 0.9 + 1.2));
const COMPILED = HERO_NODES.map((h) => new THREE.Vector3(h.compiled[0] * 1.15, 0, -h.compiled[1] * 1.05 + 1.0));

const SEG = 26;
const PACKETS_PER_EDGE = 3;
const BURST = 26;
const TRAIL = 10;
const SPARKS = 12; // per impact
const STREAK = 6; // per falling chip

/**
 * Rewrites a fat line's vertices in place. drei's setPositions allocates new GPU buffers on
 * every call; doing that for ~50 lines a frame is what made earlier versions stutter.
 */
function setLine(line: Line2 | null, pts: Float32Array, opacity: number) {
  if (!line) return;
  const mat = line.material as THREE.Material & { opacity: number };
  mat.opacity = opacity;
  line.visible = opacity > 0.01;
  if (!line.visible) return;
  const attr = line.geometry.getAttribute('instanceStart') as THREE.InterleavedBufferAttribute | undefined;
  if (!attr) return;
  const arr = attr.data.array as Float32Array;
  const n = Math.min(pts.length / 3 - 1, arr.length / 6);
  for (let s = 0; s < n; s++) {
    arr[s * 6] = pts[s * 3];
    arr[s * 6 + 1] = pts[s * 3 + 1];
    arr[s * 6 + 2] = pts[s * 3 + 2];
    arr[s * 6 + 3] = pts[s * 3 + 3];
    arr[s * 6 + 4] = pts[s * 3 + 4];
    arr[s * 6 + 5] = pts[s * 3 + 5];
  }
  attr.data.needsUpdate = true;
}

function pointsGeometry(n: number) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
  g.setAttribute('aAlpha', new THREE.BufferAttribute(new Float32Array(n), 1));
  g.setAttribute('aColor', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
  g.setAttribute('aSize', new THREE.BufferAttribute(new Float32Array(n), 1));
  return g;
}
const POINTS_VS = `attribute float aAlpha; attribute vec3 aColor; attribute float aSize; varying float vA; varying vec3 vC;
  void main(){ vA=aAlpha; vC=aColor; vec4 mv=modelViewMatrix*vec4(position,1.); gl_PointSize=aSize*(300./-mv.z); gl_Position=projectionMatrix*mv; }`;
/** a writer over a points geometry; reset() each frame, put() each particle */
function pointWriter(g: THREE.BufferGeometry) {
  const pa = g.getAttribute('position') as THREE.BufferAttribute;
  const al = g.getAttribute('aAlpha') as THREE.BufferAttribute;
  const co = g.getAttribute('aColor') as THREE.BufferAttribute;
  const sz = g.getAttribute('aSize') as THREE.BufferAttribute;
  let i = 0;
  return {
    reset() { i = 0; },
    put(x: number, y: number, z: number, a: number, c: THREE.Color, s: number) {
      pa.setXYZ(i, x, y, z);
      al.setX(i, a);
      co.setXYZ(i, c.r, c.g, c.b);
      sz.setX(i, s);
      i++;
    },
    flush() {
      for (let j = i; j < pa.count; j++) al.setX(j, 0);
      pa.needsUpdate = al.needsUpdate = co.needsUpdate = sz.needsUpdate = true;
    },
  };
}

/* Tuned for Apple silicon as well as desktop GPUs: Macs and 8-core machines draw fewer particles; the pixel
   ratio starts at 1.25 and drops to 1 if the frame rate falls (see PerformanceMonitor below). */
const LITE = typeof navigator !== 'undefined' &&
  (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) || (navigator.hardwareConcurrency || 8) <= 8);
const PARTICLES = LITE ? 2400 : 4200;

/** Shared per-frame state the components read without re-rendering. */
interface Stage { s: number; D: number; mC: number }

/* ----------------------------------------------------------------- connectome */

function Connectome({ input, stage }: { input: MutableRefObject<HeroInput>; stage: MutableRefObject<Stage> }) {
  const group = useRef<THREE.Group>(null);
  const N = PARTICLES;
  const { dust, links } = useMemo(() => {
    const r = rng(7);
    const pos = new Float32Array(N * 3);
    const col = new Float32Array(N * 3);
    const seed = new Float32Array(N);
    const palette = [new THREE.Color('#2dd4bf'), new THREE.Color('#60a5fa'), new THREE.Color('#9aa3b2'), new THREE.Color('#f97316')];
    for (let i = 0; i < N; i++) {
      // a flattened shell, denser towards the rim: reads as a brain-like volume
      const u = r() * Math.PI * 2, w = Math.acos(2 * r() - 1);
      const rad = Math.pow(r(), 0.35);
      pos[i * 3] = Math.sin(w) * Math.cos(u) * 30 * rad;
      pos[i * 3 + 1] = Math.sin(w) * Math.sin(u) * 13 * rad;
      pos[i * 3 + 2] = Math.cos(w) * 12 * rad - 10;
      const c = palette[r() < 0.04 ? 3 : Math.floor(r() * 3)];
      col.set([c.r, c.g, c.b], i * 3);
      seed[i] = r();
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
    g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1));
    const seg: number[] = [];
    const a = new THREE.Vector3(), b = new THREE.Vector3();
    for (let i = 0; i < 1100; i++) {
      const p = Math.floor(r() * N);
      a.fromArray(pos, p * 3);
      for (let t = 0; t < 30; t++) {
        const q = Math.floor(r() * N);
        b.fromArray(pos, q * 3);
        if (a.distanceTo(b) < 2.6) {
          seg.push(a.x, a.y, a.z, b.x, b.y, b.z);
          break;
        }
      }
    }
    const lg = new THREE.BufferGeometry();
    lg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(seg), 3));
    return { dust: g, links: lg };
  }, []);
  const uniforms = useMemo(() => ({ uTime: { value: 0 }, uMouse: { value: new THREE.Vector2() }, uFlat: { value: 0 }, uLift: { value: NEURON_Y } }), []);
  const mouse = useMemo(() => new THREE.Vector2(), []);
  const linkMat = useMemo(
    () => new THREE.LineBasicMaterial({ color: '#3b4a5e', transparent: true, opacity: 0.22, blending: THREE.AdditiveBlending, depthWrite: false }),
    [],
  );
  const linkObj = useRef<THREE.LineSegments>(null);
  useFrame((_, dt) => {
    const D = stage.current.D;
    uniforms.uTime.value += dt;
    uniforms.uFlat.value = D;
    uniforms.uMouse.value.lerp(mouse.set(input.current.mx, input.current.my), 0.08);
    linkMat.opacity = 0.22 * (1 - D) * (1 - D);
    if (linkObj.current) linkObj.current.visible = linkMat.opacity > 0.005;
    if (group.current) {
      group.current.rotation.y += dt * 0.012 * (1 - D);
      group.current.rotation.x = Math.sin(uniforms.uTime.value * 0.05) * 0.05 * (1 - D);
      group.current.position.y = NEURON_Y * (1 - D);
    }
  });
  return (
    <group ref={group}>
      <lineSegments ref={linkObj} geometry={links} material={linkMat} />
      <points geometry={dust} frustumCulled={false}>
        <shaderMaterial
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          uniforms={uniforms}
          vertexShader={/* glsl */ `
            attribute vec3 aColor; attribute float aSeed; uniform float uTime; uniform vec2 uMouse; uniform float uFlat; uniform float uLift;
            varying vec3 vC; varying float vA;
            void main(){
              // as the network descends, the dust settles onto the wafer like silicon being laid down
              vec4 w = modelMatrix * vec4(position, 1.);
              float land = clamp(uFlat * 1.4 - aSeed * .4, 0., 1.);
              w.y = mix(w.y, .03, land * land * (3. - 2. * land));
              vec4 mv = viewMatrix * w;
              vec4 clip = projectionMatrix * mv;
              vec2 ndc = clip.xy / clip.w;
              float near = exp(-pow(length((ndc - uMouse) * vec2(1.6, 1.)), 2.) * 10.);
              float tw = .45 + .55 * sin(uTime * (0.6 + aSeed * 2.2) + aSeed * 40.);
              float wave = smoothstep(.85, 1., sin(position.x * .18 - uTime * .9 + position.y * .1));
              vA = (.16 + .45 * tw + wave * .7 + near * 1.1) * (1. - uFlat * .72);
              vC = mix(aColor, vec3(.98, .45, .09), near * .8);
              gl_PointSize = (0.9 + aSeed * 1.6 + near * 2.2) * (70. / -mv.z) * (1. - uFlat * .35);
              gl_Position = clip;
            }`}
          fragmentShader={/* glsl */ `
            varying vec3 vC; varying float vA;
            void main(){
              float d = length(gl_PointCoord - .5);
              float a = smoothstep(.5, 0., d);
              gl_FragColor = vec4(vC * (.7 + a * .6), a * vA * .5);
            }`}
        />
      </points>
    </group>
  );
}

/* ------------------------------------------------------------------------ die */

function Die({ input, sweep, cursor, stage, waferFade }: {
  input: MutableRefObject<HeroInput>;
  sweep: MutableRefObject<number | null>;
  cursor: MutableRefObject<THREE.Vector3>;
  stage: MutableRefObject<Stage>;
  waferFade: MutableRefObject<number>;
}) {
  const n = HERO_NODES.length;
  const { camera, scene } = useThree();
  const groups = useRef<(THREE.Group | null)[]>([]);
  const neuronObjs = useRef<(THREE.Mesh | null)[]>([]);
  const chipObjs = useRef<(THREE.Group | null)[]>([]);
  const rings = useRef<(THREE.Mesh | null)[]>([]);
  const labelAnchors = useRef<(THREE.Group | null)[]>([]);
  const labels = useRef<(HTMLDivElement | null)[]>([]);
  const callouts = useRef<(HTMLDivElement | null)[]>([]);
  const lit = useMemo(() => HERO_NODES.map(() => ({ current: 0 })), []);
  const axonLines = useRef<(Line2 | null)[]>([]);
  const seqLines = useRef<(Line2 | null)[]>([]);
  const dataLines = useRef<(Line2 | null)[]>([]);
  const effLines = useRef<(Line2 | null)[]>([]);
  const cage = useRef<THREE.Group>(null);
  const beam = useRef<THREE.Mesh>(null);
  const light = useRef<THREE.PointLight>(null);
  const pos = useMemo(() => HERO_NODES.map(() => new THREE.Vector3()), []);
  const npos = useMemo(() => HERO_NODES.map(() => new THREE.Vector3()), []); // neuron world positions
  const flat = useMemo(() => new Float32Array(SEG * 3), []);
  const route = (f: string, to: string, bias = 0.5) => manhattan(pos[NODE_INDEX[f]], pos[NODE_INDEX[to]], bias);
  const biases = useMemo(() => DATA_EDGES.map((_, i) => 0.35 + ((i * 37) % 30) / 100), []);
  const bends = useMemo(() => {
    const r = rng(11);
    return SEQUENCE_EDGES.map(() => new THREE.Vector3((r() - 0.5) * 0.6, (r() - 0.5) * 1.3, (r() - 0.5) * 1.8));
  }, []);
  const tumble = useMemo(() => {
    const r = rng(29);
    return HERO_NODES.map(() => new THREE.Vector3((r() - 0.5) * 2.4, (r() - 0.5) * 3.2, (r() - 0.5) * 2.4));
  }, []);

  const neurons = useMemo(
    () =>
      HERO_NODES.map((h, i) => ({
        geo: neuronGeometry(101 + i * 17, h.effect === 'irreversible' ? 1.45 : h.effect === 'external' ? 1.15 : 1),
        mat: new THREE.ShaderMaterial({
          vertexShader: NEURON_VS,
          fragmentShader: NEURON_FS,
          transparent: true,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
          uniforms: {
            uColor: { value: new THREE.Color(EFFECT_HEX[h.effect]) },
            uLit: { value: 0 },
            uTime: { value: 0 },
            uAlpha: { value: 1 },
          },
        }),
        spin: new THREE.Vector3(0.05 + (i % 3) * 0.03, 0.08 + (i % 4) * 0.02, 0.02),
      })),
    [],
  );

  // square packets on the die, round sparks for the neural side
  const nSq = TRAIL + DATA_EDGES.length * PACKETS_PER_EDGE + BURST;
  const nRound = TRAIL + 7 + n * (SPARKS + STREAK);
  const sq = useMemo(() => pointsGeometry(nSq), [nSq]);
  const round = useMemo(() => pointsGeometry(nRound), [nRound]);
  const sqW = useMemo(() => pointWriter(sq), [sq]);
  const roundW = useMemo(() => pointWriter(round), [round]);
  const sqMat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        vertexShader: POINTS_VS,
        fragmentShader: `varying float vA; varying vec3 vC;
          void main(){ vec2 q=gl_PointCoord-.5; float d=max(abs(q.x),abs(q.y)); float a=smoothstep(.5,.1,d); gl_FragColor=vec4(vC*(1.8+a),a*vA); }`,
      }),
    [],
  );
  const roundMat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        vertexShader: POINTS_VS,
        fragmentShader: `varying float vA; varying vec3 vC;
          void main(){ float d=length(gl_PointCoord-.5); float a=smoothstep(.5,0.,d); float core=smoothstep(.18,0.,d); gl_FragColor=vec4(vC*(1.6+a)+core,a*vA); }`,
      }),
    [],
  );
  const colors = useMemo(() => HERO_NODES.map((h) => new THREE.Color(EFFECT_HEX[h.effect])), []);
  const white = useMemo(() => new THREE.Color('#fff2e0'), []);
  const burstDirs = useMemo(
    () => Array.from({ length: BURST }, (_, i) => new THREE.Vector3(Math.cos(i * 2.4), 0.6 + (i % 5) * 0.35, Math.sin(i * 2.4)).normalize()),
    [],
  );
  const sparkDirs = useMemo(() => {
    const r = rng(77);
    return Array.from({ length: SPARKS }, (_, j) => {
      const a = (j / SPARKS) * Math.PI * 2 + r() * 0.4;
      return new THREE.Vector3(Math.cos(a), 0.35 + r() * 0.9, Math.sin(a)).multiplyScalar(0.8 + r() * 0.7);
    });
  }, []);

  const t = useRef(0);
  // the scroll target is followed by a spring, so a wheel notch glides instead of jumping
  const mS = useRef(0);
  const mV = useRef(0);
  const prevDrop = useMemo(() => new Float32Array(n), [n]);
  const impactT = useMemo(() => new Float32Array(n).fill(-99), [n]);
  const prevMC = useRef(0);
  const shake = useRef(0);
  const v = useMemo(() => new THREE.Vector3(), []);
  const A = useMemo(() => new THREE.Vector3(), []);
  const B = useMemo(() => new THREE.Vector3(), []);
  const C = useMemo(() => new THREE.Vector3(), []);
  const camP = useMemo(() => new THREE.Vector3(), []);
  const camL = useMemo(() => new THREE.Vector3(), []);
  const look = useMemo(() => new THREE.Vector3(0.4, NEURON_Y, 0), []);
  const ndc = useMemo(() => new THREE.Vector2(), []);
  const ray = useMemo(() => new THREE.Raycaster(), []);
  const floor = useMemo(() => new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), []);

  /** a point on the curved axon between two neurons */
  const axonAt = (ia: number, ib: number, bend: THREE.Vector3, k: number, o: THREE.Vector3) => {
    A.copy(npos[ia]);
    B.copy(npos[ib]);
    C.copy(A).add(B).multiplyScalar(0.5).add(bend);
    const u = 1 - k;
    return o.set(
      u * u * A.x + 2 * u * k * C.x + k * k * B.x,
      u * u * A.y + 2 * u * k * C.y + k * k * B.y,
      u * u * A.z + 2 * u * k * C.z + k * k * B.z,
    );
  };

  useFrame((_, dtRaw) => {
    const dt = Math.min(dtRaw, 0.05);
    t.current += dt;
    const time = t.current;
    const target = clamp01(input.current.morph);
    // a quick, critically damped spring: weight without lag (Lenis already eases the scroll)
    const acc = (target - mS.current) * 120 - mV.current * 21;
    mV.current += acc * dt;
    mS.current = clamp01(mS.current + mV.current * dt);
    const s = mS.current;
    const D = ease(smooth(DESCENT[0], DESCENT[1], s));
    const mC = ease(clamp01((s - COMPILE[0]) / (COMPILE[1] - COMPILE[0])));
    if (t.current > 0.8) {
      if (prevMC.current < 0.04 && mC >= 0.04) sound.compile();
      if (prevMC.current < 0.9 && mC >= 0.9) sound.compiled();
    }
    prevMC.current = mC;
    const onDie = smooth(COMPILE[0] - 0.06, COMPILE[0], s); // every chip has landed
    stage.current.s = s;
    stage.current.D = D;
    stage.current.mC = mC;
    const speed = Math.min(1, Math.abs(mV.current) * 0.9) * onDie;

    waferFade.current = smooth(0.12, 0.42, s);
    const fog = scene.fog as THREE.Fog | null;
    if (fog) {
      fog.near = 18 + D * 12;
      fog.far = 48 + D * 22;
    }

    // camera: a front-on look at the network that arcs up and over into a die shot
    const front = 1 - D;
    const arc = Math.sin(D * Math.PI);
    // pulled back while the chips rain down so the whole bus is in frame, then in for the rewire
    const wide = smooth(0.18, 0.32, s) * (1 - smooth(COMPILE[0] - 0.08, COMPILE[0] + 0.1, s));
    // portrait screens: step back so the neuron chain is not cropped to three cells
    const narrow = clamp01((1.3 - (camera as THREE.PerspectiveCamera).aspect) / 0.85);
    camP.set(
      input.current.mx * 1.6 * front + (input.current.mx * 1.8 + mV.current * 1.4 * onDie) * D - arc * 3.2,
      (NEURON_Y + 0.4 + input.current.my * 0.9) * front + (15.5 + mC * 1.5 - input.current.my * 1.2 - speed * 1.6) * D + arc * 1.5 + wide * 2.6,
      (20.5 + narrow * 16) * front + (15.5 - mC * 1.5 - speed * 2.2) * D - arc * 3.5 + wide * 2.4,
    );
    camL.set(0.4 * front + 0.6 * D, NEURON_Y * front * front, 0.4 * D);
    const k = 1 - Math.exp(-dt * 5);
    camera.position.lerp(camP, k);
    look.lerp(camL, k);
    shake.current *= Math.exp(-dt * 7);
    const sh = shake.current;
    camera.position.x += Math.sin(time * 61) * sh * 0.5;
    camera.position.y += Math.sin(time * 47 + 1) * sh;
    camera.lookAt(look);
    camera.rotation.z += arc * 0.05 - Math.max(-0.04, Math.min(0.04, mV.current * 0.025 * onDie));

    // the cursor's spot on the floor
    ray.setFromCamera(ndc.set(input.current.mx, input.current.my), camera);
    ray.ray.intersectPlane(floor, cursor.current);
    if (light.current) {
      light.current.position.set(cursor.current.x, 2.6, cursor.current.z);
      light.current.intensity = 18 * waferFade.current;
    }

    // compiler sweep: a beam crosses the die on a loop, once there is a die to sweep
    const sx = -17 + (((time / 7) % 1) * 34);
    sweep.current = onDie > 0.01 ? sx : null;
    if (beam.current) {
      beam.current.visible = onDie > 0.01;
      (beam.current.material as THREE.ShaderMaterial).uniforms.uA.value = (0.55 + mC * 0.45 + speed * 0.9) * onDie;
    }

    // positions: neuron chain -> settles over the bus -> rewired into the graph
    for (let i = 0; i < n; i++) {
      const Di = ease(smooth(DESCENT[0] + i * 0.004, DESCENT[1] + i * 0.004, s));
      const ki = ease(clamp01(mC * 1.3 - (i / n) * 0.3));
      pos[i].copy(SERIAL[i]).lerp(COMPILED[i], ki);
      const hover = 0.62 * Di;
      npos[i].copy(NEURO[i]).lerp(pos[i], Di);
      npos[i].y += hover + Math.sin(time * 0.8 + i * 1.7) * 0.07 * (1 - Di * 0.6);
      npos[i].z += Math.cos(time * 0.6 + i) * 0.05 * (1 - Di);
    }

    // neural activity: one signal walks the chain
    const ts = (time * 2.2) % (n + 2.5);
    // compiled activity: the graph fires layer by layer
    const wave = (time * 1.55) % (8 + 1.8);
    roundW.reset();
    sqW.reset();

    for (let i = 0; i < n; i++) {
      const h = HERO_NODES[i];
      const g = groups.current[i];
      if (!g) continue;
      const d = clamp01((s - (DROP_START + i * DROP_STEP)) / DROP_LEN);
      const landed = smooth(0.92, 1, d);
      // a chip that crosses the floor this frame: flash, shockwave, a kick to the camera
      // sound only for a real scroll forward, not the first frames after a reload deep in the page
      const audible = time > 0.8;
      if (audible && prevDrop[i] <= 0 && d > 0) sound.fall();
      if (prevDrop[i] < 1 && d >= 1) {
        if (audible) sound.impact(i, h.effect);
        impactT[i] = time;
        shake.current = Math.min(0.32, shake.current + 0.16);
      }
      prevDrop[i] = d;
      const e = time - impactT[i]; // seconds since impact
      const impact = e >= 0 && e < 2 ? Math.exp(-e * 3.2) : 0;

      g.position.copy(pos[i]);
      const merged = h.mergesInto ? smooth(0.55, 0.88, mC) : 0;
      g.scale.setScalar(Math.max(0.0001, 1 - merged));

      // neuron: drifts, glows as the chip closes in, and is absorbed on impact
      const neuron = neuronObjs.current[i];
      const nu = neurons[i];
      const absorb = smooth(0.82, 1, d);
      const sigS = Math.max(0, 1 - Math.abs(ts - i - 0.15) * 1.6) * (1 - D);
      nu.mat.uniforms.uLit.value = sigS + smooth(0.35, 0.95, d) * 1.6 * (1 - absorb);
      nu.mat.uniforms.uTime.value = time;
      nu.mat.uniforms.uAlpha.value = 1 - absorb;
      if (neuron) {
        neuron.visible = absorb < 0.999;
        neuron.position.set(npos[i].x - pos[i].x, npos[i].y, npos[i].z - pos[i].z);
        neuron.rotation.x += dt * nu.spin.x * (1 + d * 6);
        neuron.rotation.y += dt * nu.spin.y * (1 + d * 6);
        neuron.scale.setScalar(Math.max(0.0001, (1 - absorb) * (1 + smooth(0.4, 0.85, d) * 0.35)));
      }

      // chip: falls from the sky with a tumble, squashes on impact, then bounces once
      const chip = chipObjs.current[i];
      if (chip) {
        chip.visible = d > 0;
        const fall = 1 - d * d;
        const tb = (1 - d) * (1 - d);
        const bounce = impact * Math.abs(Math.sin(e * 13)) * 0.22;
        chip.position.set(0, fall * FALL_H + bounce, 0);
        chip.rotation.set(tumble[i].x * tb, tumble[i].y * tb, tumble[i].z * tb);
        const sqz = impact * Math.max(0, Math.cos(e * 18)) * 0.28;
        const pop = 0.55 + 0.45 * smooth(0, 0.35, d);
        chip.scale.set(pop * (1 + sqz * 0.4), pop * (1 - sqz), pop * (1 + sqz * 0.4));
      }

      // shockwave ring on the floor
      const ring = rings.current[i];
      if (ring) {
        const ok = e >= 0 && e < 0.8;
        ring.visible = ok;
        if (ok) {
          const q = e / 0.8;
          ring.scale.setScalar(0.6 + Math.sqrt(q) * 3.6);
          (ring.material as THREE.MeshBasicMaterial).opacity = (1 - q) * (1 - q) * 0.9;
        }
      }

      // LED: dark until landed, a flash on impact, then the die's own activity
      const sigC = Math.max(0, 1 - Math.abs(ts - i - 0.2) * 1.4) * (1 - mC);
      const cw = Math.max(0, 1 - Math.abs(wave - h.layer - 0.1) * 1.7) * mC;
      const glint = Math.exp(-Math.pow((pos[i].x - sx) * 1.6, 2)) * 0.6 * onDie;
      lit[i].current = (sigC * onDie + cw + glint) * landed + impact * 2.6;

      // label rides the neuron, then sits under the chip
      const la = labelAnchors.current[i];
      if (la) {
        la.position.set(
          (npos[i].x - pos[i].x) * (1 - landed),
          (npos[i].y - 0.62) * (1 - landed) + (CHIP_H + 0.05) * landed,
          (npos[i].z - pos[i].z) * (1 - landed) + (CHIP_D / 2 + 0.38) * landed,
        );
      }
      const lab = labels.current[i];
      if (lab) {
        const neuralO = 0.42 * (1 - smooth(0.05, 0.25, D));
        const chipO = (0.35 + 0.65 * smooth(0.1, 0.6, mC)) * landed;
        lab.style.opacity = String(Math.max(neuralO * (1 - landed), chipO) * (1 - merged));
      }

      // falling streak and impact sparks
      const c = colors[i];
      for (let j = 0; j < STREAK; j++) {
        const on = d > 0.05 && d < 1 ? 1 : 0;
        const y = (1 - d * d) * FALL_H + 0.3 + j * 0.35 * (0.4 + d);
        roundW.put(pos[i].x, y, pos[i].z, on * (1 - j / STREAK) * 0.55, j === 0 ? white : c, 0.5 - j * 0.05);
      }
      for (let j = 0; j < SPARKS; j++) {
        const on = e >= 0 && e < 0.9 ? 1 : 0;
        const q = Math.max(0, e) / 0.9;
        const dd = sparkDirs[j];
        roundW.put(
          pos[i].x + dd.x * q * 2.2,
          0.15 + dd.y * q * 1.8 - q * q * 1.6,
          pos[i].z + dd.z * q * 2.2,
          on * (1 - q) * 0.9,
          j % 3 === 0 ? white : c,
          0.32 * (1 - q * 0.5),
        );
      }
    }
    if (cage.current) {
      cage.current.visible = onDie > 0.01;
      cage.current.position.copy(pos[NODE_INDEX.order]);
      cage.current.rotation.y = time * 0.5;
      cage.current.scale.setScalar((0.92 + Math.sin(time * 3) * 0.03) * Math.max(0.0001, onDie));
    }

    // axons between neurons, fading as the chips arrive
    const axO = 0.5 * (1 - smooth(0.2, 0.5, s));
    SEQUENCE_EDGES.forEach(([f, to], i) => {
      if (axO <= 0.01) return setLine(axonLines.current[i], flat, 0);
      const ia = NODE_INDEX[f], ib = NODE_INDEX[to];
      for (let q = 0; q < SEG; q++) {
        axonAt(ia, ib, bends[i], q / (SEG - 1), v);
        flat[q * 3] = v.x;
        flat[q * 3 + 1] = v.y;
        flat[q * 3 + 2] = v.z;
      }
      setLine(axonLines.current[i], flat, axO);
    });
    // the action potential and its synapse
    {
      const e = Math.floor(ts);
      const f = ts - e;
      const ei = Math.min(SEQUENCE_EDGES.length - 1, e);
      const ia = NODE_INDEX[SEQUENCE_EDGES[ei][0]], ib = NODE_INDEX[SEQUENCE_EDGES[ei][1]];
      for (let j = 0; j < TRAIL; j++) {
        const kk = f - j * 0.07;
        const on = e < SEQUENCE_EDGES.length && kk >= 0 ? 1 : 0;
        axonAt(ia, ib, bends[ei], Math.max(0, kk), v);
        roundW.put(v.x, v.y, v.z, on * (1 - j / TRAIL) * (1 - D), j === 0 ? white : colors[ib], 0.75 - j * 0.06);
      }
      const pi = NODE_INDEX[SEQUENCE_EDGES[Math.max(0, Math.min(SEQUENCE_EDGES.length - 1, e - 1))][1]];
      for (let j = 0; j < 7; j++) {
        const on = e >= 1 && e - 1 < SEQUENCE_EDGES.length && f < 0.5 ? 1 : 0;
        const a = j * 0.9;
        const r = 0.25 + f * 1.6;
        roundW.put(npos[pi].x + Math.cos(a) * r, npos[pi].y + Math.sin(a * 1.7) * r * 0.6, npos[pi].z + Math.sin(a) * r, on * (1 - f / 0.5) * (1 - D), white, 0.35);
      }
    }
    roundW.flush();

    // the die's traces: the serial bus lights up behind the landed chips, then rewires
    const land = (i: number) => smooth(0.92, 1, clamp01((s - (DROP_START + i * DROP_STEP)) / DROP_LEN));
    SEQUENCE_EDGES.forEach(([f, to], i) => {
      const o = 0.55 * (1 - smooth(0.05, 0.55, mC)) * land(NODE_INDEX[f]) * land(NODE_INDEX[to]);
      setLine(seqLines.current[i], o > 0.01 ? resample(route(f, to), SEG, flat) : flat, o);
    });
    DATA_EDGES.forEach(([f, to], i) => {
      const o = 0.6 * smooth(0.3, 0.85, mC);
      setLine(dataLines.current[i], o > 0.01 ? resample(route(f, to, biases[i]), SEG, flat) : flat, o);
    });
    EFFECT_EDGES.forEach(([f, to], i) => {
      const l = effLines.current[i];
      const o = 0.95 * smooth(0.4, 0.9, mC);
      setLine(l, o > 0.01 ? resample(route(f, to), SEG, flat) : flat, o);
      if (l && o > 0.01) l.computeLineDistances();
    });

    // packets on the die
    const e = Math.min(SEQUENCE_EDGES.length - 1, Math.floor(ts));
    const fseq = ts - Math.floor(ts);
    const seqRoute = route(SEQUENCE_EDGES[e][0], SEQUENCE_EDGES[e][1]);
    for (let j = 0; j < TRAIL; j++) {
      const kk = fseq - j * 0.045;
      along(seqRoute, Math.max(0, kk), v);
      sqW.put(v.x, v.y + 0.04, v.z, (Math.floor(ts) < SEQUENCE_EDGES.length && kk >= 0 ? 1 : 0) * (1 - j / TRAIL) * (1 - mC) * onDie, j === 0 ? white : colors[NODE_INDEX[SEQUENCE_EDGES[e][1]]], 0.42 - j * 0.03);
    }
    if (mC > 0.001) {
      DATA_EDGES.forEach(([f, to], ei) => {
        const local = wave - HERO_NODES[NODE_INDEX[f]].layer;
        const r = route(f, to, biases[ei]);
        for (let j = 0; j < PACKETS_PER_EDGE; j++) {
          const kk = local - j * 0.07;
          along(r, clamp01(kk), v);
          sqW.put(v.x, v.y + 0.04, v.z, (kk > 0 && kk < 1 ? 1 : 0) * mC * (1 - j / PACKETS_PER_EDGE), j === 0 ? white : colors[NODE_INDEX[to]], 0.36 - j * 0.07);
        }
      });
      // the CSE merge: a puff of fragments where get_price #2 folds into #1
      const mergeK = smooth(0.62, 0.95, mC) * (1 - smooth(0.95, 1.0, mC) * 0.6);
      const gp = pos[NODE_INDEX.gp1];
      for (let j = 0; j < BURST; j++) {
        const kk = (time * 0.6 + j / BURST) % 1;
        v.copy(gp).addScaledVector(burstDirs[j], 0.4 + kk * 1.6);
        sqW.put(v.x, v.y + 0.04, v.z, mergeK * (1 - kk) * 0.9, colors[NODE_INDEX.gp1], 0.18);
      }
    }
    sqW.flush();

    callouts.current.forEach((el, i) => {
      if (el) el.style.opacity = String(narrow > 0.5 ? 0 : smooth(0.62 + i * 0.06, 0.92 + i * 0.02, mC));
    });
  });

  const init = useMemo(() => Array.from({ length: SEG }, () => new THREE.Vector3()), []);
  const beamMat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
        uniforms: { uA: { value: 0 } },
        vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
        fragmentShader: `uniform float uA; varying vec2 vUv;
          void main(){ float core = exp(-pow((vUv.x-.5)*40.,2.)); float halo = exp(-pow((vUv.x-.5)*7.,2.))*.35;
            float h = smoothstep(1.,0.,vUv.y) * smoothstep(0.,.05,vUv.y);
            gl_FragColor = vec4(vec3(1.,.55,.18)*(core*2.+halo), (core+halo)*h*uA); }`,
      }),
    [],
  );

  const callout = (i: number, at: THREE.Vector3, title: string, body: string, side: 'left' | 'right' = 'right') => (
    <group position={[at.x, CHIP_H + 0.3, at.z]}>
      <Html zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
        <div
          ref={(el) => {
            callouts.current[i] = el;
          }}
          className="flex items-start gap-2 opacity-0"
          style={{ transform: side === 'left' ? 'translate(-100%, -50%) translateX(-30px)' : 'translate(26px, -50%)' }}
        >
          {side === 'right' && <span className="xhair text-[var(--accent)] mt-1.5 shrink-0 scale-75" />}
          <div className={`rounded-[0.625rem] border border-white/10 bg-black/60 backdrop-blur-md px-3 py-2 ${side === 'left' ? 'text-right' : ''}`}>
            <div className="text-[0.8125rem] font-semibold text-[var(--ink)] whitespace-nowrap">{title}</div>
            <div className="text-[0.7812rem] text-[var(--ink-dim)] whitespace-nowrap">{body}</div>
          </div>
          {side === 'left' && <span className="xhair text-[var(--accent)] mt-1.5 shrink-0 scale-75" />}
        </div>
      </Html>
    </group>
  );

  const lineProps = { points: init, transparent: true, depthWrite: false, frustumCulled: false } as const;
  return (
    <group>
      <pointLight ref={light} color="#f97316" intensity={0} distance={9} decay={2} />
      <mesh ref={beam} position={[0, 1.5, 0.5]} rotation={[0, Math.PI / 2, 0]} material={beamMat} visible={false}>
        <planeGeometry args={[26, 3]} />
      </mesh>

      {SEQUENCE_EDGES.map((_, i) => (
        <Line key={`a${i}`} ref={(l) => { axonLines.current[i] = l as unknown as Line2 | null; }} {...lineProps} color="#c7ccd6" lineWidth={1.4} opacity={0.5} />
      ))}
      {SEQUENCE_EDGES.map((_, i) => (
        <Line key={`s${i}`} ref={(l) => { seqLines.current[i] = l as unknown as Line2 | null; }} {...lineProps} color="#eef2f8" lineWidth={2.2} opacity={0} />
      ))}
      {DATA_EDGES.map((_, i) => (
        <Line key={`d${i}`} ref={(l) => { dataLines.current[i] = l as unknown as Line2 | null; }} {...lineProps} color="#ff9a4d" lineWidth={1.9} opacity={0} />
      ))}
      {EFFECT_EDGES.map((_, i) => (
        <Line key={`x${i}`} ref={(l) => { effLines.current[i] = l as unknown as Line2 | null; }} {...lineProps} color="#9ca3af" lineWidth={1.8} dashed dashSize={0.2} gapSize={0.15} opacity={0} />
      ))}
      <points geometry={round} material={roundMat} frustumCulled={false} />
      <points geometry={sq} material={sqMat} frustumCulled={false} />

      {HERO_NODES.map((h, i) => (
        <group key={h.id} ref={(g) => { groups.current[i] = g; }}>
          <mesh ref={(m) => { neuronObjs.current[i] = m; }} geometry={neurons[i].geo} material={neurons[i].mat} />
          <group ref={(g) => { chipObjs.current[i] = g; }} visible={false}>
            <Chip color={EFFECT_HEX[h.effect]} litRef={lit[i]} scale={h.effect === 'irreversible' ? 1.15 : 1} />
          </group>
          <mesh ref={(m) => { rings.current[i] = m; }} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]} visible={false}>
            <ringGeometry args={[0.46, 0.5, 48]} />
            <meshBasicMaterial color={EFFECT_HEX[h.effect]} transparent opacity={0} toneMapped={false} depthWrite={false} blending={THREE.AdditiveBlending} />
          </mesh>
          <group ref={(g) => { labelAnchors.current[i] = g; }}>
            <Html center zIndexRange={[5, 0]} style={{ pointerEvents: 'none' }}>
              <div
                ref={(el) => { labels.current[i] = el; }}
                className="font-mono text-[0.625rem] whitespace-nowrap tracking-wide px-1"
                style={{ color: EFFECT_HEX[h.effect], textShadow: '0 0 6px #000, 0 0 2px #000', opacity: 0 }}
              >
                {h.label}
              </div>
            </Html>
          </group>
        </group>
      ))}

      <group ref={cage} visible={false}>
        <lineSegments position={[0, 0.55, 0]}>
          <edgesGeometry args={[new THREE.BoxGeometry(2.0, 1.1, 1.6)]} />
          <lineBasicMaterial color="#ef4444" transparent opacity={0.9} toneMapped={false} />
        </lineSegments>
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]}>
          <ringGeometry args={[1.25, 1.32, 6]} />
          <meshBasicMaterial color="#ef4444" toneMapped={false} transparent opacity={0.8} />
        </mesh>
      </group>

      {callout(1, COMPILED[NODE_INDEX.s1], 'Run side by side', 'Six searches no longer wait in line')}
      {callout(2, COMPILED[NODE_INDEX.gp1], 'Duplicate merged', 'Two identical price checks become one', 'left')}
      {callout(3, COMPILED[NODE_INDEX.order], 'Left untouched', 'Placing an order is never repeated or reordered', 'left')}
    </group>
  );
}

export default function HeroSilicon({ input, active }: { input: MutableRefObject<HeroInput>; active: boolean }) {
  const sweep = useRef<number | null>(null);
  const cursor = useRef(new THREE.Vector3());
  const stage = useRef<Stage>({ s: 0, D: 0, mC: 0 });
  const waferFade = useRef(0);
  const [dpr, setDpr] = useState(LITE ? 1.25 : 1.5);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    // a hidden tab draws nothing
    const on = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  return (
    <Canvas
      frameloop={active && visible ? 'always' : 'never'}
      dpr={dpr}
      camera={{ position: [0, NEURON_Y + 0.4, 20.5], fov: 40, near: 0.1, far: 140 }}
      gl={{ antialias: false, alpha: true, powerPreference: 'high-performance', stencil: false }}
      style={{ position: 'absolute', inset: 0 }}
    >
      <fog attach="fog" args={['#05070b', 18, 48]} />
      <ambientLight intensity={0.65} />
      <hemisphereLight args={['#b8c8ec', '#1a1208', 0.75]} />
      <directionalLight position={[6, 12, 8]} intensity={1.9} color="#eef3ff" />
      <directionalLight position={[-8, 6, -6]} intensity={1.1} color="#f97316" />
      <directionalLight position={[10, 4, -10]} intensity={0.9} color="#38bdf8" />
      <Wafer cursor={cursor} sweep={sweep} fade={waferFade} />
      <Connectome input={input} stage={stage} />
      <Die input={input} sweep={sweep} cursor={cursor} stage={stage} waferFade={waferFade} />
      <PerformanceMonitor onDecline={() => setDpr(1)} onIncline={() => setDpr(LITE ? 1.25 : 1.5)} flipflops={3} onFallback={() => setDpr(1)} />
      <EffectComposer multisampling={0}>
        <Bloom mipmapBlur intensity={1.45} luminanceThreshold={0.32} luminanceSmoothing={0.25} radius={0.72} levels={LITE ? 4 : 6} resolutionScale={LITE ? 0.5 : 1} />
        <Vignette eskil={false} offset={0.22} darkness={0.55} />
      </EffectComposer>
    </Canvas>
  );
}
