// Silicon primitives: chip packages, Manhattan-routed traces, and a wafer floor.
// Shared by the landing hero and the console's 3D graph view.
import { useMemo, useRef, type MutableRefObject } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export const CHIP_W = 1.25;
export const CHIP_D = 0.82;
export const CHIP_H = 0.2;

/** A chip package: matte body, inset die plate, an LED bar, and pins down both long sides. */
export function Chip({ color, lit = 0, scale = 1, pins = 7, litRef }: {
  color: string;
  lit?: number;
  scale?: number;
  pins?: number;
  /** if given, the LED intensity is read from this ref every frame (no re-render) */
  litRef?: MutableRefObject<number>;
}) {
  const led = useRef<THREE.MeshBasicMaterial>(null);
  const die = useRef<THREE.MeshStandardMaterial>(null);
  const base = useMemo(() => new THREE.Color(color), [color]);
  const tmp = useMemo(() => new THREE.Color(), []);
  useFrame(() => {
    const l = litRef ? litRef.current : lit;
    if (led.current) led.current.color.copy(tmp.copy(base).multiplyScalar(0.6 + l * 3.2));
    if (die.current) die.current.emissiveIntensity = 0.24 + l * 0.9;
  });
  const pinGeo = useMemo(() => new THREE.BoxGeometry(0.07, 0.04, 0.16), []);
  const pinMat = useMemo(() => new THREE.MeshStandardMaterial({ color: '#b8c0cc', metalness: 1, roughness: 0.28 }), []);
  const xs = useMemo(() => Array.from({ length: pins }, (_, i) => (-CHIP_W / 2 + 0.16 + (i * (CHIP_W - 0.32)) / (pins - 1))), [pins]);
  return (
    <group scale={scale}>
      <mesh castShadow position={[0, CHIP_H / 2, 0]}>
        <boxGeometry args={[CHIP_W, CHIP_H, CHIP_D]} />
        <meshStandardMaterial color="#121722" metalness={0.4} roughness={0.5} />
      </mesh>
      <mesh position={[0, CHIP_H + 0.006, 0.04]}>
        <boxGeometry args={[CHIP_W * 0.78, 0.012, CHIP_D * 0.58]} />
        <meshStandardMaterial ref={die} color="#1b2331" metalness={0.7} roughness={0.28} emissive={color} emissiveIntensity={0.24} />
      </mesh>
      <mesh position={[0, CHIP_H + 0.01, -CHIP_D / 2 + 0.09]}>
        <boxGeometry args={[CHIP_W * 0.62, 0.014, 0.05]} />
        <meshBasicMaterial ref={led} color={color} toneMapped={false} />
      </mesh>
      {/* pin-1 dot */}
      <mesh position={[-CHIP_W / 2 + 0.13, CHIP_H + 0.008, CHIP_D / 2 - 0.12]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.035, 16]} />
        <meshBasicMaterial color="#2a3240" />
      </mesh>
      {xs.map((x) => (
        <group key={x}>
          <mesh geometry={pinGeo} material={pinMat} position={[x, 0.05, CHIP_D / 2 + 0.07]} />
          <mesh geometry={pinGeo} material={pinMat} position={[x, 0.05, -CHIP_D / 2 - 0.07]} />
        </group>
      ))}
    </group>
  );
}

/** Orthogonal route A → B on the floor: out along x, across in z, in along x. */
export function manhattan(a: THREE.Vector3, b: THREE.Vector3, bias = 0.5, y = 0.02): THREE.Vector3[] {
  const ax = a.x + CHIP_W / 2;
  const bx = b.x - CHIP_W / 2;
  if (Math.abs(a.z - b.z) < 0.01) return [new THREE.Vector3(ax, y, a.z), new THREE.Vector3(bx, y, b.z)];
  const mx = ax + (bx - ax) * bias;
  return [new THREE.Vector3(ax, y, a.z), new THREE.Vector3(mx, y, a.z), new THREE.Vector3(mx, y, b.z), new THREE.Vector3(bx, y, b.z)];
}

/** Point at fraction k (0..1) of a polyline's length. */
export function along(pts: THREE.Vector3[], k: number, out: THREE.Vector3) {
  let total = 0;
  for (let i = 1; i < pts.length; i++) total += pts[i].distanceTo(pts[i - 1]);
  let d = Math.min(1, Math.max(0, k)) * total;
  for (let i = 1; i < pts.length; i++) {
    const seg = pts[i].distanceTo(pts[i - 1]);
    if (d <= seg || i === pts.length - 1) return out.copy(pts[i - 1]).lerp(pts[i], seg ? Math.min(1, d / seg) : 0);
    d -= seg;
  }
  return out.copy(pts[pts.length - 1]);
}

/** Resample a polyline to exactly n points (fat lines want a fixed vertex count). */
export function resample(pts: THREE.Vector3[], n: number, out: Float32Array) {
  const v = new THREE.Vector3();
  for (let i = 0; i < n; i++) {
    along(pts, i / (n - 1), v);
    out[i * 3] = v.x;
    out[i * 3 + 1] = v.y;
    out[i * 3 + 2] = v.z;
  }
  return out;
}

/**
 * The wafer: a fine routing grid, coarser die-cut lines, scattered vias, a soft light that
 * follows the cursor across the floor, and a compiler "laser" sweeping along x.
 */
export function Wafer({ cursor, sweep, fade, size = 90 }: {
  cursor: MutableRefObject<THREE.Vector3>;
  /** x position of the sweep line, or null for none */
  sweep?: MutableRefObject<number | null>;
  /** 0..1 overall visibility, read every frame (default 1) */
  fade?: MutableRefObject<number>;
  size?: number;
}) {
  const uniforms = useMemo(
    () => ({ uCursor: { value: new THREE.Vector3() }, uSweep: { value: -999 }, uTime: { value: 0 }, uFade: { value: 1 } }),
    [],
  );
  const mesh = useRef<THREE.Mesh>(null);
  useFrame((_, dt) => {
    uniforms.uCursor.value.lerp(cursor.current, 0.12);
    uniforms.uSweep.value = sweep?.current ?? -999;
    uniforms.uTime.value += dt;
    uniforms.uFade.value = fade ? fade.current : 1;
    if (mesh.current) mesh.current.visible = uniforms.uFade.value > 0.005;
  });
  return (
    <mesh ref={mesh} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]} receiveShadow>
      <planeGeometry args={[size, size]} />
      <shaderMaterial
        transparent
        depthWrite={false}
        uniforms={uniforms}
        vertexShader={/* glsl */ `
          varying vec3 vW;
          void main(){ vec4 w = modelMatrix * vec4(position,1.); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`}
        fragmentShader={/* glsl */ `
          uniform vec3 uCursor; uniform float uSweep; uniform float uTime; uniform float uFade; varying vec3 vW;
          float grid(vec2 p, float s, float w){ vec2 g = abs(fract(p / s - .5) - .5) * s / fwidth(p); return 1. - min(min(g.x, g.y) / w, 1.); }
          float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
          void main(){
            vec2 p = vW.xz;
            float fine = grid(p, .5, 1.) * .22;
            float coarse = grid(p, 5., 1.4) * .55;
            // vias: a few cells carry a dot
            vec2 cell = floor(p / .5);
            float via = step(.93, hash(cell)) * (1. - smoothstep(.04, .07, length(fract(p / .5) - .5) * .5));
            float d = length(p - uCursor.xz);
            float glow = exp(-d * d * .06);
            float sweep = exp(-pow((p.x - uSweep) * 3.5, 2.));
            vec3 base = vec3(.16, .19, .24);
            vec3 accent = vec3(.976, .451, .086);
            vec3 col = base * (fine + coarse + via * .8) + accent * glow * (.35 * (fine + coarse) + .05) + accent * sweep * (.6 + coarse);
            // the floor powers up from the centre outwards as uFade rises
            float fade = smoothstep(42., 8., length(p)) * smoothstep(uFade * 46., uFade * 46. - 10., length(p)) * uFade;
            float a = (fine + coarse + via * .6) * .9 + glow * .25 + sweep * .9;
            gl_FragColor = vec4(col * 1.4, a * fade);
          }`}
      />
    </mesh>
  );
}
