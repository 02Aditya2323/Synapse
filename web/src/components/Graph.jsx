import { useMemo } from 'react'
import { ReactFlow, Background, Handle, Position } from '@xyflow/react'
import dagre from '@dagrejs/dagre'
import { EFFECT } from './bits.jsx'
import { money } from '../format.js'

const W = 190
const H = 96

function NodeCard({ data }) {
  const n = data.node
  const e = EFFECT[n.effect] || EFFECT.pure
  const s = n.stats
  return (
    <div
      className={`rounded-lg border bg-surface-1 px-3 py-2 text-left shadow-sm ${n.changed ? 'border-after ring-1 ring-after/50' : 'border-line'}`}
      style={{ width: W, minHeight: H - 12, borderLeft: `3px solid ${e.color}` }}
    >
      <Handle type="target" position={Position.Left} className="!bg-ink-3 !border-0 !w-1.5 !h-1.5" />
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-ink-3">{n.kind}</span>
        <span className="text-[10px] text-ink-3">{e.text}</span>
      </div>
      <div className="text-[13px] font-medium truncate" title={n.label}>{n.label}</div>
      {n.model && n.kind === 'model' && n.model !== n.label && <div className="text-[11px] text-ink-2 truncate" title={n.model}>{n.model}</div>}
      {n.parallel && <div className="text-[11px] text-after">runs per sub-question, in parallel (limit {n.parallel})</div>}
      {n.inRegion && !n.parallel && <div className="text-[11px] text-ink-3">runs per sub-question, one at a time</div>}
      {n.memoized && <div className="text-[10px] text-ink-3">memoized</div>}
      {n.techniques?.length > 0 && <div className="text-[11px] text-after truncate">{n.techniques.join(' · ')}</div>}
      {s && (
        <div className="text-[11px] text-ink-3 num mt-0.5">
          {s.calls_per_task != null && `${s.calls_per_task.toFixed(1)} calls/task`}
          {s.latency_s != null && ` · ${s.latency_s.toFixed(2)} s/call`}
          {s.cost_per_task_usd != null && ` · ${money(s.cost_per_task_usd)}/task`}
        </div>
      )}
      {n.note && <div className="text-[10px] text-ink-3 mt-0.5">{n.note}</div>}
      <Handle type="source" position={Position.Right} className="!bg-ink-3 !border-0 !w-1.5 !h-1.5" />
    </div>
  )
}

const nodeTypes = { card: NodeCard }

const EFFECT_ORDER = ['pure', 'read', 'write', 'external', 'irreversible']

/** An agent loop: input and reply beside the model step, tools in columns grouped by effect. */
function agentLayout(graph) {
  const tools = graph.nodes.filter((n) => n.kind === 'tool')
    .sort((a, b) => EFFECT_ORDER.indexOf(a.effect) - EFFECT_ORDER.indexOf(b.effect) || a.label.localeCompare(b.label))
  const perCol = 4
  const pos = { in: { x: 0, y: 60 }, agent: { x: W + 60, y: 60 }, out: { x: W + 60, y: 60 + H + 50 } }
  tools.forEach((t, i) => { pos[t.id] = { x: 2 * (W + 60) + 40 + Math.floor(i / perCol) * (W + 24), y: (i % perCol) * (H + 16) } })
  const nodes = graph.nodes.map((n) => ({ id: n.id, type: 'card', position: pos[n.id] || { x: 0, y: 0 }, data: { node: n }, draggable: false }))
  const edges = graph.edges.filter((e) => e.label !== 'result').map((e, i) => ({
    id: `e${i}`, source: e.source, target: e.target,
    style: { stroke: '#5b5a55', strokeWidth: 1.2 },
  }))
  return { nodes, edges }
}

function layout(graph) {
  if (graph.nodes.filter((n) => n.kind === 'tool').length > 4) return agentLayout(graph)
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 18, ranksep: 56 })
  g.setDefaultEdgeLabel(() => ({}))
  const region = {}
  for (const r of graph.regions) for (const id of r.nodes) region[id] = r
  // the agent loop's tool -> agent "result" edges are implied; drawing them only adds clutter
  const edges = graph.edges.filter((e) => e.label !== 'result')
  graph.nodes.forEach((n) => g.setNode(n.id, { width: W, height: H }))
  edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  const nodes = graph.nodes.map((n) => {
    const p = g.node(n.id)
    const r = region[n.id]
    return {
      id: n.id,
      type: 'card',
      position: { x: p.x - W / 2, y: p.y - H / 2 },
      data: { node: { ...n, inRegion: !!r, parallel: r?.parallel ? r.limit : null } },
      draggable: false,
    }
  })
  const flowEdges = edges.map((e, i) => ({
    id: `e${i}`,
    source: e.source,
    target: e.target,
    label: e.label && !['message', ''].includes(e.label) ? e.label : undefined,
    style: { stroke: '#5b5a55', strokeWidth: 1.5 },
    labelStyle: { fill: '#8f8e86', fontSize: 10 },
    labelBgStyle: { fill: '#1a1a19' },
  }))
  return { nodes, edges: flowEdges }
}

export default function Graph({ graph, height = 360 }) {
  const { nodes, edges } = useMemo(() => layout(graph), [graph])
  return (
    <div style={{ height }} className="rounded-lg bg-surface-0 border border-line">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.08 }}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
        minZoom={0.2}
      >
        <Background color="#2f2f2c" gap={20} />
      </ReactFlow>
    </div>
  )
}
