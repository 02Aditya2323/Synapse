import { useMemo, useState } from 'react'
import { useView } from '../store.js'
import Graph from './Graph.jsx'
import Metrics from './Metrics.jsx'
import Tasks from './Tasks.jsx'
import { Card, Decision, EffectBadge } from './bits.jsx'
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
          <tr><th className="font-normal pb-2">Technique</th><th className="font-normal pb-2">Why it was tried</th><th className="font-normal pb-2 text-right">Cost</th><th className="font-normal pb-2 text-right">Time</th><th className="font-normal pb-2 text-right">Passes (base, base, try)</th><th className="font-normal pb-2 pl-4">Tuning verdict</th></tr>
        </thead>
        <tbody>
          {s.trials.map((t) => (
            <tr key={t.technique} className="border-t border-line align-top">
              <td className="py-2 font-mono text-xs text-after">{t.technique}</td>
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

export default function Result({ r: given, result }) {
  const r = given || result
  const go = useView((s) => s.go)
  const [stack, setStack] = useState(false)
  const many = useMemo(() => r && (r.graph.after.nodes.length > 8 || r.layer === 'graph'), [r])
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
      <Card title="Before and after" right={<div className="flex items-center gap-4"><Legend />{!many && <button onClick={() => setStack(!stack)} className="text-xs text-ink-3 hover:text-ink-2">{side ? 'stack' : 'side by side'}</button>}</div>}>
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
      </Card>
      <Card title="Measured on held-out tasks"><Metrics metrics={r.metrics} /></Card>
      <Card title={r.layer === 'graph' ? 'Transformations' : 'What the search tried'}><Transforms r={r} /></Card>
      <Card title="Every task"><Tasks r={r} /></Card>
      <Card title="Billing"><Billing b={r.billing} /></Card>
    </div>
  )
}
