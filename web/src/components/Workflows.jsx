import { useView } from '../store.js'
import Graph from './Graph.jsx'
import { Card, Decision, EffectBadge } from './bits.jsx'
import { pct } from '../format.js'

const signed = (x) => (x == null ? '—' : x >= 0 ? `${pct(x)} lower` : `${pct(-x)} higher`)

export default function Workflows({ workflows }) {
  const chosen = useView((v) => v.resultId)
  const name = chosen || workflows[0]?.name
  const setName = (n) => { window.location.hash = `/workflows/${n}` }
  const w = workflows.find((x) => x.name === name)
  if (!w) return <p className="text-ink-3">No workflows in the feed.</p>
  const long = w.nodes > 12
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold">Workflows</h1>
        <p className="text-ink-2 text-sm">Each workflow is compiled by the graph engine as you open this page: its steps, their dependencies and their effects, then the optimizer's default passes. Nothing here calls a model.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {workflows.map((x) => (
          <button key={x.name} onClick={() => setName(x.name)} className={`px-3 py-1.5 rounded-md border text-sm ${x.name === name ? 'border-ink-3 bg-surface-2' : 'border-line text-ink-3 hover:text-ink-2'}`}>
            {x.name}
            {x.protected.length > 0 && <span className="ml-2 text-[11px] text-critical">{x.protected.length} protected</span>}
          </button>
        ))}
      </div>
      <Card title={w.about} right={<span className="text-xs text-ink-3 font-mono">{w.graph_id} · {w.nodes} steps</span>}>
        <div className="flex flex-wrap gap-4 text-xs text-ink-2 mb-4">
          {Object.entries(w.effects).map(([e, n]) => <span key={e} className="flex items-center gap-1.5"><EffectBadge effect={e} /> {n}</span>)}
          <span className="text-ink-3">{long ? 'Drag to pan, scroll to zoom.' : ''}</span>
        </div>
        <p className="text-xs text-ink-3 mb-2">As written</p>
        <Graph graph={w.graph.before} height={long ? 300 : 240} fit={!long} />
        <p className="text-xs text-ink-3 mt-4 mb-2">After the optimizer's passes</p>
        <Graph graph={w.graph.after} height={long ? 300 : 240} fit={!long} />
      </Card>
      {w.protected.length > 0 && (
        <Card title="Steps the optimizer refused to touch">
          <p className="text-sm text-ink-2 mb-3">These steps change the outside world. Effects are law: no pass may cache, reorder, re-run, speculate or batch them, and an irreversible step never runs during optimization or testing.</p>
          <ul className="flex flex-col gap-3 text-sm">
            {w.protected.map((p) => (
              <li key={p.node} className="border-l-2 border-critical pl-3">
                <span className="font-mono text-xs">{p.node}</span>
                {p.reasons.map((r) => <p key={r} className="text-ink-2">{r}</p>)}
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Passes">
        <ul className="flex flex-col gap-2 text-sm">
          {w.transforms.map((t) => (
            <li key={t.pass} className="flex gap-3">
              <span className={`w-28 shrink-0 text-xs font-mono mt-0.5 ${t.applied ? 'text-after' : 'text-ink-3'}`}>{t.pass}</span>
              <div>{t.applied ? t.changes.map((c) => <p key={c}>{c}</p>) : <p className="text-ink-3">not applied: {t.findings[0] || t.note}</p>}</div>
            </li>
          ))}
        </ul>
        <p className="text-xs text-ink-3 mt-3">A pass applied here is a proposal. It ships only if the firewall approves it on real tasks: see the measured runs below.</p>
      </Card>
      <Card title="Measured runs on 0G">
        {w.runs.length === 0 ? <p className="text-sm text-ink-3">No saved run for this workflow.</p> : (
          <table className="w-full text-sm">
            <thead className="text-xs text-ink-3 text-left"><tr>{['Tasks', 'Verdict', 'Cost', 'Time', 'Passes', 'Pipeline', 'Report'].map((h) => <th key={h} className="font-normal pb-2">{h}</th>)}</tr></thead>
            <tbody>
              {w.runs.map((r) => (
                <tr key={r.file} className="border-t border-line">
                  <td className="py-2 num">{r.tasks} <span className="text-xs text-ink-3">{r.split === 'test' ? 'held-out' : 'tuning'}</span></td>
                  <td className="py-2"><Decision decision={r.outcome} /></td>
                  <td className="py-2 num text-xs">{signed(r.cost_change)}</td>
                  <td className="py-2 num text-xs">{signed(r.time_change)}</td>
                  <td className="py-2 num text-xs">{r.passes ? r.passes.join(' → ') : '—'}</td>
                  <td className="py-2 font-mono text-[11px] text-ink-2">{r.policy.join('; ')}</td>
                  <td className="py-2 font-mono text-[11px] text-ink-3">{r.file.replace('reports/', '')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
