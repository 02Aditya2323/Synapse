import dagre from 'dagre';
import type { Graph } from '../types';
import { NODE_H, NODE_W } from './SirNodeCard';

export type Direction = 'LR' | 'TB';
export type Positions = Record<string, { x: number; y: number }>;
export interface Layout { positions: Positions; direction: Direction; width: number; height: number }

function run(graph: Graph, direction: Direction, anchor?: Positions): Layout {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: direction, nodesep: 28, ranksep: direction === 'LR' ? 64 : 44, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  const ids = new Set(graph.nodes.map((n) => n.id));
  graph.nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  for (const e of [...graph.edges.data, ...graph.edges.effect]) {
    if (ids.has(e.from) && ids.has(e.to) && e.from !== e.to) g.setEdge(e.from, e.to);
  }
  dagre.layout(g);
  const positions: Positions = {};
  for (const n of graph.nodes) {
    const p = g.node(n.id);
    positions[n.id] = anchor?.[n.id] ?? { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 };
  }
  const xs = Object.values(positions).map((p) => p.x);
  const ys = Object.values(positions).map((p) => p.y);
  const width = xs.length ? Math.max(...xs) - Math.min(...xs) + NODE_W : 0;
  const height = ys.length ? Math.max(...ys) - Math.min(...ys) + NODE_H : 0;
  return { positions, direction, width, height };
}

/**
 * dagre layout over data + effect edges.
 *
 * `direction: 'auto'` lays the graph out both ways and keeps the one whose shape best
 * fits a pane of `aspect` (width / height). Real workflows are mostly long chains with a
 * fan-out in the middle; left-to-right turns those into a thin line, top-to-bottom into
 * a column, and which one is readable depends on the pane.
 *
 * `anchor` pins nodes that also exist in another graph to that graph's positions, so the
 * original and the optimized graph draw the same node in the same place; the
 * before/after and live-run screens depend on that.
 */
export function layoutGraph(
  graph: Graph,
  direction: Direction | 'auto' = 'auto',
  opts: { anchor?: Positions; aspect?: number } = {},
): Layout {
  if (direction !== 'auto') return run(graph, direction, opts.anchor);
  const aspect = opts.aspect ?? 1.6;
  const fit = (l: Layout) => {
    if (!l.width || !l.height) return 0;
    // scale at which the layout fits a 1 x (1/aspect) box: bigger = more legible
    return Math.min(aspect / l.width, 1 / l.height);
  };
  const lr = run(graph, 'LR', opts.anchor);
  const tb = run(graph, 'TB', opts.anchor);
  return fit(lr) >= fit(tb) ? lr : tb;
}

/** Bounding box of a set of nodes, padded, for drawing a region around them. */
export function bbox(positions: Positions, ids: string[], pad = 14) {
  const ps = ids.map((i) => positions[i]).filter(Boolean);
  if (!ps.length) return null;
  const x0 = Math.min(...ps.map((p) => p.x)) - pad;
  const y0 = Math.min(...ps.map((p) => p.y)) - pad - 16;
  const x1 = Math.max(...ps.map((p) => p.x)) + NODE_W + pad;
  const y1 = Math.max(...ps.map((p) => p.y)) + NODE_H + pad;
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}
