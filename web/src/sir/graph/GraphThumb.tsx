import { useMemo } from 'react';
import { motion } from 'motion/react';
import type { Graph } from '../types';
import { layoutGraph } from './layout';
import { NODE_H, NODE_W } from './SirNodeCard';
import { effectColor } from './effects';

/** A small, non-interactive picture of a graph: nodes coloured by effect, data edges as curves. */
export function GraphThumb({ graph, className }: { graph: Graph; className?: string }) {
  const l = useMemo(() => layoutGraph(graph, 'LR'), [graph]);
  const ps = Object.values(l.positions);
  if (!ps.length) return null;
  const x0 = Math.min(...ps.map((p) => p.x));
  const y0 = Math.min(...ps.map((p) => p.y));
  const pad = 30;
  const vb = `${x0 - pad} ${y0 - pad} ${l.width + pad * 2} ${l.height + pad * 2}`;
  const mid = (id: string, side: 'l' | 'r') => {
    const p = l.positions[id];
    return { x: p.x + (side === 'r' ? NODE_W : 0), y: p.y + NODE_H / 2 };
  };
  return (
    <svg viewBox={vb} className={className} preserveAspectRatio="xMidYMid meet" aria-hidden>
      {graph.edges.data.map((e, i) => {
        if (!l.positions[e.from] || !l.positions[e.to]) return null;
        const a = mid(e.from, 'r');
        const b = mid(e.to, 'l');
        const mx = (a.x + b.x) / 2;
        return (
          <motion.path
            key={i}
            d={`M${a.x} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${b.x} ${b.y}`}
            fill="none"
            stroke="rgba(255,255,255,.22)"
            strokeWidth={5}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.8, delay: 0.1 + i * 0.03 }}
          />
        );
      })}
      {graph.nodes.map((n, i) => {
        const p = l.positions[n.id];
        const c = effectColor(n.effect);
        return (
          <motion.g key={n.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.05 * i }}>
            <rect x={p.x} y={p.y} width={NODE_W} height={NODE_H} rx={14} fill="#0b0e14" stroke={c} strokeOpacity={0.7} strokeWidth={4} />
            <rect x={p.x + 24} y={p.y} width={NODE_W - 48} height={8} fill={c} />
            {n.effect === 'irreversible' && <rect x={p.x} y={p.y} width={NODE_W} height={NODE_H} rx={14} fill={c} fillOpacity={0.18} />}
          </motion.g>
        );
      })}
    </svg>
  );
}
