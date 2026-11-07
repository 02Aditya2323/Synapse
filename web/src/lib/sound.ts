// Landing-page sound design, synthesized with Web Audio: no files to download. Cinematic: sub
// booms, metal slams in a big hall, risers and a trailer "braaam". Off until the visitor turns it
// on (browsers block audio before a click anyway). Experimental.
import { useSyncExternalStore } from 'react';

const KEY = 'synapse.sound';
let on = false;
try {
  on = localStorage.getItem(KEY) === '1';
} catch {
  /* storage blocked: stays off */
}
const subs = new Set<() => void>();

let ctx: AudioContext | null = null;
let master: GainNode | null = null; // dry, into the glue compressor
let hall: GainNode | null = null; // send into a long convolution reverb
let noise: AudioBuffer | null = null;
let air: { gain: GainNode; filter: BiquadFilterNode } | null = null;

/** A big, dark hall: decaying stereo noise, low-passed over time by squaring the envelope. */
function impulse(c: AudioContext, seconds: number) {
  const len = Math.floor(c.sampleRate * seconds);
  const buf = c.createBuffer(2, len, c.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const d = buf.getChannelData(ch);
    let lp = 0;
    for (let i = 0; i < len; i++) {
      const k = i / len;
      lp += (Math.random() * 2 - 1 - lp) * (0.35 - 0.3 * k); // darker as it decays
      d[i] = lp * Math.pow(1 - k, 2.6);
    }
  }
  return buf;
}

/** Soft saturation, for weight. */
function drive(c: AudioContext, amount: number) {
  const ws = c.createWaveShaper();
  const n = 1024;
  const curve = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    curve[i] = Math.tanh(x * amount) / Math.tanh(amount);
  }
  ws.curve = curve;
  ws.oversample = '2x'; // 4x costs CPU on laptops for no audible gain here
  return ws;
}

function audio() {
  if (ctx) return ctx;
  const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return null;
  ctx = new AC();
  const glue = ctx.createDynamicsCompressor();
  glue.threshold.value = -14;
  glue.knee.value = 8;
  glue.ratio.value = 6;
  glue.attack.value = 0.004;
  glue.release.value = 0.35;
  const out = ctx.createGain();
  out.gain.value = 0.9;
  glue.connect(out).connect(ctx.destination);
  master = ctx.createGain();
  master.gain.value = 0.7;
  master.connect(glue);
  const verb = ctx.createConvolver();
  verb.buffer = impulse(ctx, 3.2);
  const wet = ctx.createGain();
  wet.gain.value = 0.55;
  hall = ctx.createGain();
  hall.connect(verb).connect(wet).connect(glue);
  noise = ctx.createBuffer(1, ctx.sampleRate * 3, ctx.sampleRate);
  const d = noise.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  return ctx;
}

/** Audio may only start from a click, tap or key press: scrolling does not count. */
function wake() {
  const c = audio();
  if (!c) return;
  if (!watched) {
    watched = true;
    c.addEventListener('statechange', () => subs.forEach((f) => f()));
  }
  if (c.state !== 'running') void c.resume().then(() => subs.forEach((f) => f())).catch(() => {});
}
let watched = false;

/** Whether sound is really playing: on, and the browser has let the audio start. */
export function audible() {
  return on && !!ctx && ctx.state === 'running';
}

if (typeof window !== 'undefined') {
  // After a reload "on" is remembered, but the browser keeps audio paused until a gesture on this page.
  // Keep listening until it is running, and wake it again whenever the browser pauses it (tab switch,
  // back/forward cache), so sound comes back every time, not only on the first visit.
  const gesture = () => {
    if (on && !audible()) wake();
  };
  for (const ev of ['pointerdown', 'mousedown', 'touchstart', 'keydown', 'click']) {
    addEventListener(ev, gesture, { capture: true, passive: true });
  }
  const back = () => {
    if (on && ctx && ctx.state !== 'running' && document.visibilityState === 'visible') void ctx.resume().catch(() => {});
    subs.forEach((f) => f());
  };
  document.addEventListener('visibilitychange', back);
  addEventListener('pageshow', back);
}

export function setSound(next: boolean) {
  on = next;
  try {
    localStorage.setItem(KEY, next ? '1' : '0');
  } catch {
    /* fine: just not remembered */
  }
  if (next) {
    wake();
    setTimeout(() => sound.chime(0), 60);
  } else if (air && ctx) {
    air.gain.gain.setTargetAtTime(0, ctx.currentTime, 0.05);
  }
  subs.forEach((f) => f());
}

/** True when sound is switched on but the browser is still waiting for a click to start it. */
export function useSoundBlocked() {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => on && !audible(),
    () => false,
  );
}

export function useSound() {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => on,
    () => false,
  );
}

function ready() {
  return on && ctx && master && hall && ctx.state === 'running' ? ctx : null;
}

/** Never more than one of a kind within `gap` ms, so a fast scroll can't pile sounds up. */
const last: Record<string, number> = {};
function allow(kind: string, gap: number) {
  const now = performance.now();
  if (now - (last[kind] ?? -1e9) < gap) return false;
  last[kind] = now;
  return true;
}

/** Connects a voice to the dry bus and, at `send`, to the hall. */
function route(c: AudioContext, node: AudioNode, send: number) {
  node.connect(master!);
  if (send > 0) {
    const s = c.createGain();
    s.gain.value = send;
    node.connect(s).connect(hall!);
  }
}

function env(c: AudioContext, peak: number, attack: number, decay: number, at: number) {
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(peak, at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + attack + decay);
  return g;
}

function osc(c: AudioContext, type: OscillatorType, from: number, to: number, glide: number, at: number, stop: number, detune = 0) {
  const o = c.createOscillator();
  o.type = type;
  o.detune.value = detune;
  o.frequency.setValueAtTime(from, at);
  if (to !== from) o.frequency.exponentialRampToValueAtTime(to, at + glide);
  o.start(at);
  o.stop(at + stop);
  return o;
}

function noiseSrc(c: AudioContext, at: number, stop: number) {
  const src = c.createBufferSource();
  src.buffer = noise;
  src.start(at, Math.random() * 1.5);
  src.stop(at + stop);
  return src;
}

/** Sub drop: the floor of every hit. */
function sub(c: AudioContext, at: number, peak: number, from = 70, to = 26, len = 1.4) {
  const o = osc(c, 'sine', from, to, len * 0.8, at, len + 0.1);
  const g = env(c, peak, 0.004, len, at);
  o.connect(g);
  route(c, g, 0.15);
}

/** Inharmonic metal: a struck steel plate. */
function metal(c: AudioContext, at: number, base: number, peak: number, len: number) {
  const ratios = [1, 2.76, 5.4, 8.93, 13.34];
  const bus = c.createGain();
  const hp = c.createBiquadFilter();
  hp.type = 'highpass';
  hp.frequency.value = 120;
  bus.connect(hp);
  route(c, hp, 0.9);
  ratios.forEach((r, k) => {
    const o = osc(c, k % 2 ? 'square' : 'triangle', base * r, base * r * 0.985, len, at, len + 0.1, (Math.random() - 0.5) * 18);
    const g = env(c, peak / (1 + k * 0.9), 0.002, len / (1 + k * 0.5), at);
    o.connect(g).connect(bus);
  });
}

export const sound = {
  /** A chip drops out of the sky: a heavy low whoosh. */
  fall() {
    const c = ready();
    if (!c || !allow('fall', 90)) return;
    const t = c.currentTime;
    const src = noiseSrc(c, t, 0.7);
    const bp = c.createBiquadFilter();
    bp.type = 'bandpass';
    bp.Q.value = 0.9;
    bp.frequency.setValueAtTime(1800, t);
    bp.frequency.exponentialRampToValueAtTime(160, t + 0.6);
    const g = env(c, 0.16, 0.12, 0.5, t);
    src.connect(bp).connect(g);
    route(c, g, 0.35);
    const rumble = osc(c, 'sawtooth', 90, 45, 0.6, t, 0.7);
    const lp = c.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 180;
    const rg = env(c, 0.06, 0.15, 0.45, t);
    rumble.connect(lp).connect(rg);
    route(c, rg, 0.2);
  },

  /** A chip slams onto its neuron: sub boom, distorted punch, steel, crack, and a long hall tail.
   * Each hit lands a little heavier than the last; risky steps hit with a dark brass stab. */
  impact(i: number, effect?: string) {
    const c = ready();
    if (!c || !allow('impact', 55)) return;
    const t = c.currentTime;
    const weight = Math.min(1, 0.55 + i * 0.035);
    sub(c, t, 0.95 * weight, 75, 24, 1.6);
    // punch: a saturated body thump
    const body = osc(c, 'sine', 180, 48, 0.12, t, 0.5);
    const d = drive(c, 4);
    const bg = env(c, 0.55 * weight, 0.002, 0.35, t);
    body.connect(d).connect(bg);
    route(c, bg, 0.4);
    // crack: the first milliseconds of the hit
    const crack = noiseSrc(c, t, 0.25);
    const cf = c.createBiquadFilter();
    cf.type = 'lowpass';
    cf.frequency.setValueAtTime(6000, t);
    cf.frequency.exponentialRampToValueAtTime(400, t + 0.15);
    const cg = env(c, 0.45 * weight, 0.001, 0.18, t);
    crack.connect(cf).connect(cg);
    route(c, cg, 0.7);
    metal(c, t, 92 - i * 1.6, 0.11 * weight, 1.8);
    if (effect === 'irreversible' || effect === 'external') {
      // the step nobody may touch: a low, dissonant brass stab
      [41.2, 43.65, 82.4].forEach((f, k) => {
        const o = osc(c, 'sawtooth', f, f, 0, t, 2.4, k * 7);
        const lp = c.createBiquadFilter();
        lp.type = 'lowpass';
        lp.frequency.setValueAtTime(1400, t);
        lp.frequency.exponentialRampToValueAtTime(160, t + 1.8);
        const g = env(c, 0.16, 0.01, 2.0, t);
        o.connect(lp).connect(drive(c, 3)).connect(g);
        route(c, g, 0.6);
      });
    }
  },

  /** The chips start to rewire: a rising tension riser. */
  compile() {
    const c = ready();
    if (!c || !allow('compile', 2000)) return;
    const t = c.currentTime;
    const len = 1.6;
    const src = noiseSrc(c, t, len + 0.2);
    const hp = c.createBiquadFilter();
    hp.type = 'bandpass';
    hp.Q.value = 2;
    hp.frequency.setValueAtTime(300, t);
    hp.frequency.exponentialRampToValueAtTime(7000, t + len);
    const ng = c.createGain();
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.exponentialRampToValueAtTime(0.22, t + len);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + len + 0.15);
    src.connect(hp).connect(ng);
    route(c, ng, 0.5);
    [0, 7, -5].forEach((det) => {
      const o = osc(c, 'sawtooth', 55, 220, len, t, len + 0.2, det);
      const lp = c.createBiquadFilter();
      lp.type = 'lowpass';
      lp.frequency.setValueAtTime(200, t);
      lp.frequency.exponentialRampToValueAtTime(3000, t + len);
      const g = c.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.05, t + len);
      g.gain.exponentialRampToValueAtTime(0.0001, t + len + 0.15);
      o.connect(lp).connect(g);
      route(c, g, 0.3);
    });
  },

  /** Compiled: the trailer BRAAAM. */
  compiled() {
    const c = ready();
    if (!c || !allow('compiled', 2000)) return;
    const t = c.currentTime;
    sub(c, t, 1, 60, 30, 2.6);
    const stack = [55, 55, 82.41, 110, 110.6, 164.81];
    const bus = c.createGain();
    const lp = c.createBiquadFilter();
    lp.type = 'lowpass';
    lp.Q.value = 3;
    lp.frequency.setValueAtTime(220, t);
    lp.frequency.exponentialRampToValueAtTime(2400, t + 0.25);
    lp.frequency.exponentialRampToValueAtTime(300, t + 3);
    const g = env(c, 0.5, 0.06, 3.2, t);
    bus.connect(lp).connect(drive(c, 5)).connect(g);
    route(c, g, 0.6);
    stack.forEach((f, k) => {
      const o = osc(c, 'sawtooth', f * 0.97, f, 0.2, t, 3.6, (k - 2.5) * 9);
      const og = c.createGain();
      og.gain.value = 0.22;
      o.connect(og).connect(bus);
    });
    metal(c, t, 73, 0.09, 3);
  },

  /** Entering a section: a distant low boom. */
  chime(k: number) {
    const c = ready();
    if (!c || !allow('chime', 400)) return;
    const t = c.currentTime;
    sub(c, t, 0.45, 55 - (k % 4) * 3, 28, 1.6);
    const src = noiseSrc(c, t, 0.6);
    const lp = c.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.setValueAtTime(900, t);
    lp.frequency.exponentialRampToValueAtTime(120, t + 0.5);
    const g = env(c, 0.12, 0.08, 0.5, t);
    src.connect(lp).connect(g);
    route(c, g, 0.9);
  },

  /** A step changes in How it works: a heavy mechanical lock. */
  tick(k: number) {
    const c = ready();
    if (!c || !allow('tick', 90)) return;
    const t = c.currentTime;
    const src = noiseSrc(c, t, 0.08);
    const bp = c.createBiquadFilter();
    bp.type = 'bandpass';
    bp.frequency.value = 2200;
    bp.Q.value = 3;
    const g = env(c, 0.3, 0.001, 0.05, t);
    src.connect(bp).connect(g);
    route(c, g, 0.5);
    sub(c, t, 0.35, 110 - k * 8, 45, 0.35);
    metal(c, t + 0.03, 140 + k * 12, 0.04, 0.5);
  },

  /** Low wind that follows scroll speed (0..1). */
  air(speed: number) {
    const c = ready();
    if (!c || !noise) return;
    if (!air) {
      const src = c.createBufferSource();
      src.buffer = noise;
      src.loop = true;
      const filter = c.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = 200;
      filter.Q.value = 1.5;
      const gain = c.createGain();
      gain.gain.value = 0;
      src.connect(filter).connect(gain);
      route(c, gain, 0.4);
      src.start();
      air = { gain, filter };
    }
    const v = Math.min(1, Math.max(0, speed));
    air.gain.gain.setTargetAtTime(v * 0.09, c.currentTime, 0.1);
    air.filter.frequency.setTargetAtTime(160 + v * 700, c.currentTime, 0.12);
  },
};
