import { useEffect, useMemo, useState } from 'react';
import { ReactFlow, Background, Controls, MarkerType, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { Graph } from '../types';
import type { NodeRun } from '../stream';
import { NODE_H, NODE_W, RegionBox, SirNodeCard, type NodeTint, type RegionFlowNode, type SirFlowNode } from './SirNodeCard';
import { bbox, layoutGraph, type Layout } from './layout';

const nodeTypes = { sir: SirNodeCard, region: RegionBox };

export interface SirGraphProps {
  graph: Graph;
  /** a precomputed layout (before/after and live run share one across both sides) */
  layout?: Layout;
  showCriticalPath?: boolean;
  /** nodes to emphasise (hovered transformation / finding); the rest are dimmed */
  highlight?: string[];
  /** nodes carrying a finding ("why was this not optimized?") */
  flagged?: string[];
  selected?: string | null;
  onSelect?: (id: string | null) => void;
  run?: Record<string, NodeRun>;
  runMode?: boolean;
  interactive?: boolean;
  className?: string;
  /** node id -> reason a pass removed it (defaults to graph.meta.elided) */
  elided?: Record<string, string>;
  /** region id -> label override, e.g. the live fan-out width */
  regionLabels?: Record<string, string>;
  /** node id -> an outside verdict drawn on the card (what reran, what a fault touched) */
  tints?: Record<string, NodeTint>;
}

/** Width / height of the element, once it has been laid out. */
export function useAspect<T extends HTMLElement>() {
  // a callback ref, not useRef: the element may mount after the component does (behind a
  // loading state), and an effect keyed on [] would never see it
  const [el, ref] = useState<T | null>(null);
  const [aspect, setAspect] = useState<number | null>(null);
  useEffect(() => {
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      if (width > 0 && height > 0) setAspect((prev) => {
        const next = width / height;
        // only re-layout on a real change of shape, not on every pixel of a resize
        return prev && Math.abs(prev - next) / prev < 0.15 ? prev : next;
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return { ref, aspect };
}

/**
 * One layout for an original and its optimized form: the original picks the direction
 * that fits the pane, and every node the optimized graph shares is pinned to the same
 * spot, so only what changed moves.
 */
export function usePairedLayout(original: Graph | undefined, optimized: Graph | undefined, aspect: number | null) {
  const left = useMemo(
    () => (original && aspect ? layoutGraph(original, 'auto', { aspect }) : undefined),
    [original, aspect],
  );
  const right = useMemo(
    () => (optimized && left ? layoutGraph(optimized, left.direction, { anchor: left.positions }) : undefined),
    [optimized, left],
  );
  return { left, right };
}

export function SirGraph({
  graph,
  layout: given,
  showCriticalPath = true,
  highlight = [],
  flagged = [],
  selected = null,
  onSelect,
  run,
  runMode = false,
  interactive = true,
  className = '',
  elided,
  regionLabels,
  tints,
}: SirGraphProps) {
  const { ref, aspect } = useAspect<HTMLDivElement>();
  const layout = useMemo(
    () => given ?? (aspect ? layoutGraph(graph, 'auto', { aspect }) : null),
    [given, graph, aspect],
  );

  const crit = useMemo(() => new Set(showCriticalPath ? (graph.critical_path ?? []) : []), [graph, showCriticalPath]);
  const critEdges = useMemo(() => {
    const cp = showCriticalPath ? (graph.critical_path ?? []) : [];
    return new Set(cp.slice(1).map((to, i) => `${cp[i]}->${to}`));
  }, [graph, showCriticalPath]);

  const hl = useMemo(() => new Set(highlight), [highlight]);
  const fl = useMemo(() => new Set(flagged), [flagged]);

  const nodes: Node[] = useMemo(() => {
    if (!layout) return [];
    const ids = new Set(graph.nodes.map((n) => n.id));
    const regions: RegionFlowNode[] = (graph.regions ?? []).flatMap((r) => {
      const box = bbox(layout.positions, r.body.filter((b) => ids.has(b)));
      if (!box) return [];
      return [{
        id: `region:${r.id}`,
        type: 'region' as const,
        position: { x: box.x, y: box.y },
        data: { region: r, width: box.width, height: box.height, label: regionLabels?.[r.id] },
        width: box.width,
        height: box.height,
        draggable: false,
        selectable: false,
        focusable: false,
        zIndex: -1,
      }];
    });
    const sir: SirFlowNode[] = graph.nodes.map((n) => ({
      id: n.id,
      type: 'sir' as const,
      position: layout.positions[n.id] ?? { x: 0, y: 0 },
      // Explicit size: React Flow v12 hides a node until it has dimensions, and these
      // objects are rebuilt on every stream event without `measured`; the ResizeObserver
      // never fires again for an unchanged size, so without this the live run goes blank.
      width: NODE_W,
      height: NODE_H,
      draggable: false,
      data: {
        node: n,
        graphName: graph.name,
        direction: layout.direction,
        critical: crit.has(n.id),
        highlighted: hl.has(n.id),
        dimmed: hl.size > 0 && !hl.has(n.id),
        flagged: fl.has(n.id),
        selected: selected === n.id,
        elided: (elided ?? graph.meta?.elided)?.[n.id],
        run: run?.[n.id],
        runMode,
        tint: tints?.[n.id],
      },
    }));
    return [...regions, ...sir];
  }, [graph, layout, crit, hl, fl, selected, run, runMode, elided, regionLabels, tints]);

  const edges: Edge[] = useMemo(() => {
    const ids = new Set(graph.nodes.map((n) => n.id));
    const dim = (a: string, b: string) => hl.size > 0 && !(hl.has(a) || hl.has(b));
    const data: Edge[] = graph.edges.data
      .filter((e) => ids.has(e.from) && ids.has(e.to))
      .map((e, i) => {
        const onCrit = critEdges.has(`${e.from}->${e.to}`);
        const stroke = onCrit ? 'var(--crit)' : 'var(--line-strong)';
        return {
          id: `d${i}-${e.from}-${e.to}`,
          source: e.from,
          target: e.to,
          type: 'smoothstep',
          style: { stroke, strokeWidth: onCrit ? 2.25 : 1.5, opacity: dim(e.from, e.to) ? 0.2 : 1 },
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: stroke },
        };
      });
    const effect: Edge[] = graph.edges.effect
      .filter((e) => ids.has(e.from) && ids.has(e.to))
      .map((e, i) => {
        const touched = selected === e.from || selected === e.to || hl.has(e.from) || hl.has(e.to);
        return {
          id: `x${i}-${e.from}-${e.to}`,
          source: e.from,
          target: e.to,
          type: 'smoothstep',
          label: touched ? e.reason : undefined,
          labelStyle: { fill: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: 10 },
          labelBgStyle: { fill: 'var(--bg)' },
          style: {
            stroke: 'var(--ghost)',
            strokeWidth: 1.25,
            strokeDasharray: '5 5',
            opacity: dim(e.from, e.to) ? 0.15 : 0.9,
          },
          markerEnd: { type: MarkerType.Arrow, width: 14, height: 14, color: 'var(--ghost)' },
        };
      });
    return [...data, ...effect];
  }, [graph, critEdges, hl, selected]);

  // A large graph shrunk to fit is unreadable: open on its first steps at a legible size instead.
  const big = graph.nodes.length > 14;
  const fitOptions = useMemo(() => {
    if (!big || !layout) return { padding: 0.12 };
    const first = [...graph.nodes]
      .sort((a, b) => {
        const pa = layout.positions[a.id], pb = layout.positions[b.id];
        return layout.direction === 'TB' ? pa.y - pb.y || pa.x - pb.x : pa.x - pb.x || pa.y - pb.y;
      })
      .slice(0, 8)
      .map((n) => ({ id: n.id }));
    return { padding: 0.15, nodes: first, minZoom: 0.55 };
  }, [big, graph, layout]);

  return (
    <div ref={ref} className={`w-full h-full relative bg-[var(--bg)] ${className}`}>
      {big && interactive && <div className="absolute top-2 left-3 z-10 text-[11.5px] text-[var(--faint)] pointer-events-none">{graph.nodes.length} steps: drag to pan, scroll to zoom</div>}
      {layout && (
        <ReactFlow
          // a new direction means new positions: remount so fitView runs again
          key={`${graph.id}:${layout.direction}`}
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_, n) => n.type === 'sir' && onSelect?.(n.id === selected ? null : n.id)}
          onPaneClick={() => onSelect?.(null)}
          fitView
          fitViewOptions={fitOptions}
          minZoom={0.2}
          maxZoom={1.6}
          nodesConnectable={false}
          elementsSelectable={interactive}
          panOnDrag={interactive}
          zoomOnScroll={interactive}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="var(--line)" gap={20} size={1} />
          {interactive && <Controls showInteractive={false} position="bottom-right" />}
        </ReactFlow>
      )}
    </div>
  );
}
