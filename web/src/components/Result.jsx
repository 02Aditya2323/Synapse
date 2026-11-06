import { useMemo, useState } from 'react'
import { useView } from '../store.js'
import Graph from './Graph.jsx'
import Metrics from './Metrics.jsx'
import Tasks from './Tasks.jsx'
import { Card, Code, Decision, EffectBadge } from './bits.jsx'
import { Compare } from '../sir/Compare.tsx'
import { money, pct } from '../format.js'

function VerdictBanner({ r }) {
  const v = r.verdict
  const block = v.decision === 'BLOCK'
  const c = v.counts
  return (
    <div className={`rounded-xl border p-5 flex flex-wrap items-center gap-6 ${block ? 'border-critical/60 bg-critical/10' : 'border-good/50 bg-good/5'}`}>
      <div className="flex flex-col gap-2">
        <span className="text-xs text-ink-3 uppercase tracking-widest">Firewall verdict, {r.tasks} held-out tasks</span>
        <Decision decision={v.decision} size="lg" />
      </div>
      <div className="flex gap-6 text-sm num">
        {[['unchanged', c.unchanged, 'text-ink'], ['fixed', c.fixed, 'text-good'], ['broken', c.broken, c.broken ? 'text-critical' : 'text-ink'], ['crashed', c.crashed, c.crashed ? 'text-warning' : 'text-ink']].map(([k, n, cls]) => (
          <div key={k}>
            <div className={`text-2xl font-semibold ${cls}`}>{n}</div>
            <div className="text-xs text-ink-3">{k}</div>
          </div>
        ))}
        <div>
          <div className="text-2xl font-semibold">{v.passes.before} → {v.passes.after}</div>
          <div className="text-xs text-ink-3">tasks passed</div>
        </div>
      </div>
      <div className="flex-1 min-w-64 text-sm text-ink-2">
        {v.reasons.map((x) => <p key={x}>{x}</p>)}
        {v.outcome && <p className="mt-1 font-medium text-ink">{v.outcome}</p>}
      </div>
    </div>
  )
}

function Transforms({ r }) {
  if (r.layer === 'graph')
    return (
      <ul className="flex flex-col gap-3 text-sm">
        {r.transforms.map((t) => (
          <li key={t.pass} className="flex gap-3">
            <span className={`mt-0.5 w-28 shrink-0 text-xs font-mono ${t.applied ? 'text-after' : 'text-ink-3'}`}>{t.pass}</span>
            <div>
              {t.applied ? t.changes.map((c) => <p key={c}>{c}</p>) : <p className="text-ink-3">not applied: {t.findings[0] || t.note}</p>}
            </div>
          </li>
        ))}
      </ul>
    )
  const s = r.search
  return (
    <div className="flex flex-col gap-4 text-sm">
      <p className="text-ink-3">Tried on {s.tuning_tasks} tuning tasks, one at a time, each beside an unchanged control run.</p>
      <table className="w-full">
        <thead className="text-xs text-ink-3 text-left">
          <tr><th className="font-normal pb-2">Technique</th>{s.trials.some((t) => t.step) && <th className="font-normal pb-2">Step</th>}<th className="font-normal pb-2">Why it was tried</th><th className="font-normal pb-2 text-right">Cost</th><th className="font-normal pb-2 text-right">Time</th><th className="font-normal pb-2 text-right">Passes (base, base, try)</th><th className="font-normal pb-2 pl-4">Tuning verdict</th></tr>
        </thead>
        <tbody>
          {s.trials.map((t) => (
            <tr key={t.technique + (t.step || '')} className="border-t border-line align-top">
              <td className="py-2 font-mono text-xs text-after">{t.technique}</td>
              {t.step && <td className="py-2 pr-4 text-xs text-ink-2">{t.step}</td>}
              <td className="py-2 text-ink-2 pr-4">{t.why}</td>
              <td className="py-2 text-right num">{pct(t.cost_gain)} lower</td>
              <td className="py-2 text-right num">{t.latency_gain >= 0 ? `${pct(t.latency_gain)} lower` : `${pct(-t.latency_gain)} higher`}</td>
              <td className="py-2 text-right num">{t.passes.join(', ')}</td>
              <td className="py-2 pl-4"><Decision decision={t.approve ? 'APPROVE' : 'BLOCK'} />{t.reasons.map((x) => <div key={x} className="text-xs text-ink-3 mt-1">{x}</div>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {s.skipped.length > 0 && (
        <div>
          <p className="text-xs text-ink-3 mb-1">Not tried: predicted saving under 3%</p>
          <ul className="text-xs text-ink-2 flex flex-col gap-0.5">{s.skipped.map((c) => <li key={c.technique}><span className="font-mono">{c.technique}</span>: {c.why}</li>)}</ul>
        </div>
      )}
      <p>Policy taken to held-out tasks: <span className="font-mono text-after">{s.policy.join('; ') || 'unchanged'}</span></p>
    </div>
  )
}

function Billing({ b }) {
  return (
    <div className="text-sm flex flex-col gap-2">
      <p className="text-ink-2">{b.explain}</p>
      {b.source === 'billed' && (
        <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 num mt-1">
          <div><dt className="text-xs text-ink-3">0G billed (held-out arms)</dt><dd className="text-lg">{money(b.billed_usd)}</dd></div>
          <div><dt className="text-xs text-ink-3">at list price</dt><dd className="text-lg">{money(b.listed_usd)}</dd></div>
          <div><dt className="text-xs text-ink-3">requests with a 0G bill</dt><dd className="text-lg">{b.requests_with_bill} / {b.requests}</dd></div>
          <div><dt className="text-xs text-ink-3">whole run, all arms</dt><dd className="text-lg">{money(b.spent_usd)}</dd></div>
        </dl>
      )}
      {b.source === 'billed' && b.listed_usd > 0 && (
        <p className="text-xs text-ink-3">0G billed {pct(1 - b.billed_usd / b.listed_usd)} less than list price: it charges cached prompt tokens at about 11% of the input price.</p>
      )}
      {b.source !== 'billed' && <p className="num">Whole run, all arms: {money(b.spent_usd)}</p>}
    </div>
  )
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-3">
      {['pure', 'read', 'write', 'external', 'irreversible'].map((e) => <EffectBadge key={e} effect={e} />)}
      <span className="text-[11px] text-ink-2 flex items-center gap-1"><span className="w-3 h-2 rounded-sm border border-after" />changed by the optimizer</span>
    </div>
  )
}

function Arms({ r }) {
  return (
    <div className="flex flex-col gap-3">
      <table className="dtable">
        <thead><tr><th>How it ran</th><th className="num">Median time</th><th className="num">vs as written</th><th className="num">Cost per task</th><th className="num">Calls per task</th><th className="num">Passed</th><th className="num">Orders placed in the test</th></tr></thead>
        <tbody>
          {r.arms.map((a, i) => (
            <tr key={a.label}>
              <td><div className={`text-[13.5px] ${i === 2 ? 'text-[var(--accent)] font-semibold' : 'text-[var(--text-strong)]'}`}>{a.label}</div><div className="text-[12px] text-[var(--faint)]">{a.note}</div></td>
              <td className="num">{a.median_wall_s.toFixed(1)} s</td>
              <td className="num">{a.speedup ? `${a.speedup.toFixed(2)}×` : '—'}</td>
              <td className="num">{money(a.cost_per_task_usd)}</td>
              <td className="num">{a.calls_per_task.toFixed(1)}</td>
              <td className="num">{a.passed} / {r.tasks}</td>
              <td className="num">{a.orders_executed}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[12.5px] text-[var(--faint)]">All three arms ran the same {r.tasks} held-out tasks in one pool, task by task, on the same model. Speed is the median over tasks of (mean of the two as-written runs) / this run. The compiled app never places an order during a test: its irreversible step is skipped, and the order it would have placed is what gets checked.</p>
    </div>
  )
}

function Stages({ r }) {
  const g = r.graph_stage
  return (
    <ol className="flex flex-col gap-3 text-[13.5px]">
      <li className="flex gap-3"><span className="font-mono text-[var(--accent)] w-5">1</span><div><span className="text-[var(--text-strong)]">Compile.</span> <span className="text-[var(--muted)]">The declared read-only steps ran once on a tuning request; their measured reads and writes became the graph's edges, and the passes rewrote it.</span></div></li>
      <li className="flex gap-3"><span className="font-mono text-[var(--accent)] w-5">2</span><div><span className="text-[var(--text-strong)]">Graph changes, {g.tasks} tuning tasks:</span> <Decision decision={g.approve ? 'APPROVE' : 'BLOCK'} /> <span className="text-[var(--muted)] num">passes {g.passes.join(', ')} (as written, as written, compiled); time {pct(g.latency_gain)} lower, cost {pct(g.cost_gain)} lower</span>{g.reasons.map((x) => <div key={x} className="text-[12px] text-[var(--faint)]">{x}</div>)}</div></li>
      <li className="flex gap-3"><span className="font-mono text-[var(--accent)] w-5">3</span><div><span className="text-[var(--text-strong)]">Per-call changes, on top:</span> <span className="text-[var(--muted)]">{r.search.trials.length} tried, {r.search.trials.filter((t) => t.approve).length} approved. Policy taken forward: </span><span className="font-mono text-[var(--accent)]">{r.search.policy.join('; ') || 'unchanged'}</span></div></li>
      <li className="flex gap-3"><span className="font-mono text-[var(--accent)] w-5">4</span><div><span className="text-[var(--text-strong)]">Held-out, {r.tasks} tasks never seen above:</span> <Decision decision={r.verdict.decision} /></div></li>
    </ol>
  )
}

export default function Result({ r: given, result }) {
  const r = given || result
  const go = useView((s) => s.go)
  const [stack, setStack] = useState(false)
  const many = useMemo(() => r && (r.graph ? r.graph.after.nodes.length > 8 || r.layer === 'graph' : true), [r])
  if (!r) return <p className="text-ink-3">That result is not in the feed.</p>
  const side = !(stack || many)
  return (
    <div className="flex flex-col gap-6">
      <div>
        <button onClick={() => go('overview')} className="text-sm text-ink-3 hover:text-ink-2">← All results</button>
        <h1 className="text-2xl font-semibold mt-2">{r.title}</h1>
        <p className="text-ink-2">{r.subtitle}</p>
        <p className="text-xs text-ink-3 mt-1">{r.provider} · {r.model} · {r.tasks} held-out tasks · source: <span className="font-mono">{r.report}</span></p>
      </div>
      <VerdictBanner r={r} />
      {r.integration && (
        <Card eyebrow="The integration" title="Three lines in the developer's app">
          <Code>{r.integration.join('\n')}</Code>
        </Card>
      )}
      {r.sir ? (
        <Compare before={r.sir.before} after={r.sir.after} passes={r.sir.passes} asWritten={r.sir.as_written}
          beforeLabel={r.layer === 'app' ? 'Compiled from the app (as written)' : 'As written'} afterLabel="After the passes" />
      ) : <Card title="Before and after" right={<div className="flex items-center gap-4"><Legend />{!many && <button onClick={() => setStack(!stack)} className="text-xs text-ink-3 hover:text-ink-2">{side ? 'stack' : 'side by side'}</button>}</div>}>
        <div className={`grid gap-4 ${side ? 'lg:grid-cols-2' : ''}`}>
          <div>
            <p className="text-xs text-ink-3 mb-2">Unchanged {r.layer === 'graph' ? 'workflow' : 'agent, as it ran'}</p>
            <Graph graph={r.graph.before} height={r.layer === 'graph' ? 250 : many ? 470 : 300} />
          </div>
          <div>
            <p className="text-xs text-ink-3 mb-2">Optimized {r.layer === 'graph' ? 'workflow' : 'agent, as it ran'}{r.policy.length ? ': ' : ''}<span className="text-after">{r.layer === 'call' && r.policy.join('; ')}</span></p>
            <Graph graph={r.graph.after} height={r.layer === 'graph' ? 250 : many ? 470 : 300} />
          </div>
        </div>
      </Card>}
      {r.arms && <Card eyebrow="Against the framework" title="As written, wired by hand, compiled"><Arms r={r} /></Card>}
      {r.graph_stage && <Card title="How the result was decided"><Stages r={r} /></Card>}
      <Card title="Measured on held-out tasks"><Metrics metrics={r.metrics} /></Card>
      {r.layer !== 'graph' && <Card title={r.layer === 'app' ? 'Per-call changes the search tried (tuning tasks)' : 'What the search tried'}><Transforms r={r} /></Card>}
      <Card title="Every task"><Tasks r={r} /></Card>
      <Card title="Billing"><Billing b={r.billing} /></Card>
    </div>
  )
}
