// The hero's ILLUSTRATIVE workflow. It is a picture of the idea, not a measurement:
// no latency, token or cost value appears anywhere on the landing page unless it came
// from the API. The page says so under the scene.
import type { Effect } from '../types/api';

export interface HeroNode {
  id: string;
  label: string;
  effect: Effect;
  /** position as written: one long sequence */
  serial: [number, number, number];
  /** position once compiled: a dependency graph, laid out by layer */
  compiled: [number, number, number];
  /** compiled layer, for the wave of activity in parallel mode */
  layer: number;
  /** collapsed by CSE into this node once compiled */
  mergesInto?: string;
}

const SEQ = [
  'in', 'plan', 'parse', 's1', 's2', 's3', 's4', 's5', 's6', 'gp1', 'gp2', 'synth', 'answer', 'log', 'order',
];

function serialPos(i: number): [number, number, number] {
  // a long, gently winding line: what code written top to bottom executes like
  const t = i / (SEQ.length - 1);
  const x = -10.5 + t * 21;
  const y = Math.sin(t * Math.PI * 2.2) * 1.1 - 0.4;
  const z = Math.cos(t * Math.PI * 1.6) * 1.2;
  return [x, y, z];
}

const C: Record<string, { label: string; effect: Effect; compiled: [number, number, number]; layer: number; mergesInto?: string }> = {
  in: { label: 'workflow_input', effect: 'pure', compiled: [-9, 0.2, 0], layer: 0 },
  plan: { label: 'plan · model', effect: 'pure', compiled: [-6.2, 1.4, 0.4], layer: 1 },
  parse: { label: 'parse_plan', effect: 'pure', compiled: [-3.4, 1.4, 0], layer: 2 },
  s1: { label: 'search', effect: 'read', compiled: [0, 4.6, -0.6], layer: 3 },
  s2: { label: 'search', effect: 'read', compiled: [0, 3.2, 0.5], layer: 3 },
  s3: { label: 'search', effect: 'read', compiled: [0, 1.8, -0.3], layer: 3 },
  s4: { label: 'search', effect: 'read', compiled: [0, 0.4, 0.6], layer: 3 },
  s5: { label: 'search', effect: 'read', compiled: [0, -1.0, -0.4], layer: 3 },
  s6: { label: 'search', effect: 'read', compiled: [0, -2.4, 0.3], layer: 3 },
  gp1: { label: 'get_price("INFY")', effect: 'read', compiled: [-3.4, -3.4, 0.2], layer: 1 },
  gp2: { label: 'get_price("INFY")', effect: 'read', compiled: [-3.4, -3.4, 0.2], layer: 1, mergesInto: 'gp1' },
  synth: { label: 'synthesize · model', effect: 'pure', compiled: [3.6, 0.6, 0], layer: 4 },
  answer: { label: 'parse_answer', effect: 'pure', compiled: [6.2, 0.6, 0.3], layer: 5 },
  log: { label: 'post_audit_log', effect: 'external', compiled: [8.4, -1.6, -0.2], layer: 6 },
  order: { label: 'place_order', effect: 'irreversible', compiled: [10.4, 0.6, 0], layer: 7 },
};

export const HERO_NODES: HeroNode[] = SEQ.map((id, i) => ({
  id,
  ...C[id],
  serial: serialPos(i),
}));

export const NODE_INDEX = Object.fromEntries(HERO_NODES.map((n, i) => [n.id, i]));

/** How the code reads: each step after the one before. */
export const SEQUENCE_EDGES: [string, string][] = SEQ.slice(1).map((id, i) => [SEQ[i], id]);

/** What actually depends on what. */
export const DATA_EDGES: [string, string][] = [
  ['in', 'plan'],
  ['plan', 'parse'],
  ...(['s1', 's2', 's3', 's4', 's5', 's6'] as const).flatMap((s) => [['parse', s], [s, 'synth']] as [string, string][]),
  ['in', 'gp1'],
  ['in', 'gp2'],
  ['gp1', 'synth'],
  ['gp2', 'synth'],
  ['synth', 'answer'],
  ['answer', 'log'],
  ['answer', 'order'],
];

/** Observable side effects keep program order (IR_SPEC §3.2). Never reordered. */
export const EFFECT_EDGES: [string, string][] = [['log', 'order']];

export const EFFECT_HEX: Record<Effect, string> = {
  pure: '#2dd4bf',
  read: '#60a5fa',
  write: '#c4b5fd',
  external: '#f59e0b',
  irreversible: '#ef4444',
};
