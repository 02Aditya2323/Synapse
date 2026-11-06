import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { ArrowRight, Ban, Check, CircleDot, Gauge, Play, ShieldCheck, X, Zap } from 'lucide-react'
import { Card, Decision, PageTitle } from '../components/bits.jsx'
import { money } from '../format.js'
import { Compare } from '../sir/Compare.tsx'
import { SirGraph } from '../sir/graph/SirGraph.tsx'
import { layoutGraph } from '../sir/graph/layout.ts'
import { EffectLegend } from '../sir/graph/EffectLegend.tsx'
import { useView } from '../store.js'

const STEPS = ['load_policy', 'get_quotes', 'screen_reviews', 'check_certificates', 'decide', 'place_order']
const TECH = { reasoning_off: 'reasoning off', reuse: 'reused' }
const tech = (t) => TECH[t] || (t.startsWith('route:') ? `→ ${t.slice(6)}` : t)
const s1 = (v) => (v == null ? '—' : `${v.toFixed(1)} s`)
const n0 = (v) => (v == null ? '—' : Math.round(v).toLocaleString())

async function call(url, init) {
  const r = await fetch(url, init)
  const body = await r.json().catch(() => null)
  if (!r.ok) throw new Error(body?.error || `the API answered ${r.status}`)
  return body
}

/** Polls a live run and folds its events into one state object. */
function useLiveRun(id) {
  const [events, setEvents] = useState([])
  const [done, setDone] = useState(false)
  const [error, setError] = useState(null)
  useEffect(() => {
    if (!id) return
    setEvents([]); setDone(false); setError(null)
    let next = 0, stop = false
    const tick = async () => {
      try {
        const d = await call(`/api/live/${id}?since=${next}`)
        next = d.next
        if (d.events.length) setEvents((e) => [...e, ...d.events])
        if (d.done) { setDone(true); return }
      } catch (e) { setError(e); return }
      if (!stop) setTimeout(tick, 350)
    }
    tick()
    return () => { stop = true }
  }, [id])
  return { events, done, error }
}

function fold(events) {
  const s = { calls: { compile: [], as_written: [], compiled: [] }, asRun: {}, compRun: {}, stepOf: {}, phase: null, arms: {}, stub: null, armStart: null }
  for (const e of events) {
    if (e.type === 'task') s.task = e
    else if (e.type === 'phase') { s.phase = e; if (e.phase === 'race') s.armStart = e.t }
    else if (e.type === 'compiled') s.compiled = e
    else if (e.type === 'policy') s.policy = e
    else if (e.type === 'call') s.calls[e.arm]?.push(e)
    else if (e.type === 'step') {
      const ms = (e.t - (s.armStart ?? e.t)) * 1000
      const prev = s.asRun[e.node]
      s.asRun[e.node] = e.state === 'running'
        ? { state: 'running', active: 1, instances: [], start_ms: ms }
        : { ...prev, state: 'done', active: 0, instances: [], end_ms: ms, start_ms: prev?.start_ms ?? ms }
      for (const k of e.calls || []) s.stepOf[k] = e.node
    } else if (e.type === 'node') {
      const id = (e.node || '').split('#')[0]
      if (!id) continue
      const prev = s.compRun[id]
      if (e.event === 'node_start') s.compRun[id] = { state: 'running', active: 1, instances: [], start_ms: e.t_ms }
      else if (e.event === 'node_end') s.compRun[id] = { ...prev, state: 'done', active: 0, instances: [], end_ms: e.t_ms, start_ms: prev?.start_ms ?? e.t_ms }
      else if (e.event === 'cache_hit') s.compRun[id] = { state: 'cached', active: 0, instances: [], start_ms: e.t_ms, end_ms: e.t_ms, note: 'served from the cache' }
      else if (e.event === 'node_stub') { s.compRun[id] = { state: 'stubbed', active: 0, instances: [], note: e.reason }; s.stub = e }
      else if (e.event === 'error') s.compRun[id] = { state: 'error', active: 0, instances: [], note: e.message }
    } else if (e.type === 'arm_done') s.arms[e.arm] = e.summary
    else if (e.type === 'result') s.result = e
    else if (e.type === 'failed') s.failed = e
    s.t = e.t
  }
  return s
}

/** The as-written graph drawn the way the code runs it: one step after another. */
function chainOf(before, order) {
  const steps = (order || STEPS).filter((id) => before.nodes.some((n) => n.id === id))
  const ids = ['n_in', ...steps]
  return { ...before, critical_path: [], edges: { data: ids.slice(1).map((to, i) => ({ from: ids[i], to })), effect: [] },
    nodes: before.nodes.filter((n) => n.id !== 'n_state') }
}

function totals(calls) {
  const ok = calls.filter((c) => c.status === 'ok' || c.status === 'reused')
  const billed = ok.filter((c) => c.billed_usd != null)
  return { calls: ok.length, tokens: ok.reduce((a, c) => a + (c.tokens_in || 0) + (c.tokens_out || 0), 0),
    thinking: ok.reduce((a, c) => a + (c.reasoning_tokens || 0), 0), billed: billed.reduce((a, c) => a + c.billed_usd, 0) }
}

/** Seconds since both versions started, from the server's event times, ticking between events. */
function Clock({ base, until, running }) {
  const [now, setNow] = useState(performance.now())
  const seen = useRef({ base: null, at: performance.now() })
  if (seen.current.base !== base) seen.current = { base, at: performance.now() }
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(performance.now()), 100)
    return () => clearInterval(id)
  }, [running])
  if (until != null) return <>{until.toFixed(1)} s</>
  if (!running || base == null) return <>—</>
  return <>{(base + (now - seen.current.at) / 1000).toFixed(1)} s</>
}

function Lane({ title, sub, graph, run, calls, summary, running, accent, stepOf, base, direction }) {
  const t = totals(calls)
  // one layout per graph: a new layout object on every render makes the graph redraw forever
  const layout = useMemo(() => (graph && direction ? layoutGraph(graph, direction) : undefined), [graph, direction])
  return (
    <div className="panel overflow-hidden flex flex-col">
      <div className="px-4 h-12 flex items-center gap-3 border-b border-[var(--line)]">
        <span className="w-2 h-2 rounded-full" style={{ background: accent }} />
        <span className="text-[14px] font-semibold text-[var(--text-strong)] whitespace-nowrap">{title}</span>
        <span className="text-[12px] text-[var(--faint)] truncate">{sub}</span>
        <span className="ml-auto font-mono text-[20px] text-[var(--text-strong)] whitespace-nowrap"><Clock base={base} until={summary?.wall_s} running={running && !summary} /></span>
      </div>
      {graph && (
        <div className="relative h-[300px]">
          <SirGraph graph={graph} layout={layout} run={run} runMode showCriticalPath={false} interactive={false} />
        </div>
      )}
      <div className="grid grid-cols-4 border-t border-[var(--line)] text-[12px]">
        {[['model calls', t.calls], ['tokens', n0(t.tokens)], ['hidden reasoning', n0(t.thinking)], ['0G billed', money(t.billed)]].map(([k, v]) => (
          <div key={k} className="px-4 py-2.5 border-r border-[var(--line)] last:border-r-0">
            <div className="text-[var(--faint)]">{k}</div>
            <div className="font-mono text-[15px] text-[var(--text-strong)]">{v}</div>
          </div>
        ))}
      </div>
      <div className="border-t border-[var(--line)] max-h-[210px] overflow-y-auto">
        <table className="w-full text-[11.5px]">
          <tbody>
            {calls.map((c, i) => (
              <motion.tr key={c.request_id || i} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="border-b border-[var(--line)]">
                <td className="pl-4 py-1.5 font-mono text-[var(--text)] max-w-[170px] truncate" title={c.step_label}>{stepOf[c.step] || c.step_label}</td>
                <td className="font-mono text-[var(--muted)]">{c.tag}</td>
                <td className="text-right font-mono">{c.latency_s?.toFixed(1)} s</td>
                <td className="text-right font-mono text-[var(--muted)]">{n0(c.reasoning_tokens || 0)} think</td>
                <td className="text-right font-mono text-[var(--text-strong)]">{money(c.billed_usd)}</td>
                <td className="pl-3 pr-4 text-right whitespace-nowrap">
                  {(c.techniques || []).map((x) => <span key={x} className="ml-1 px-1.5 py-[1px] rounded-[4px] bg-[var(--accent)]/15 text-[var(--accent)] text-[10.5px]">{tech(x)}</span>)}
                </td>
              </motion.tr>
            ))}
            {!calls.length && <tr><td className="px-4 py-3 text-[var(--ghost)]">{running ? 'waiting for the first call…' : 'not started'}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Both runs on one clock: one step after another, against the lookups overlapping. */
function Timeline({ s }) {
  const rows = (run) => STEPS.map((id) => ({ id, r: run[id] })).filter((x) => x.r && (x.r.start_ms != null || x.r.state === 'stubbed'))
  const a = rows(s.asRun), c = rows(s.compRun)
  const end = Math.max(1, ...[...a, ...c].map((x) => x.r.end_ms ?? x.r.start_ms ?? 0), (s.arms.as_written?.wall_s || 0) * 1000)
  const pct = (v) => `${(v / end) * 100}%`
  const lane = (title, list, color, wall) => (
    <div className="mb-4 last:mb-0">
      <div className="flex items-center justify-between text-[12px] mb-1.5">
        <span className="text-[var(--text-strong)] font-medium">{title}</span>
        <span className="font-mono text-[var(--muted)]">{wall != null ? `${wall.toFixed(1)} s` : 'running…'}</span>
      </div>
      {list.map(({ id, r }) => (
        <div key={id} className="flex items-center gap-3 h-[22px]">
          <span className="w-[140px] shrink-0 font-mono text-[11.5px] text-[var(--muted)]">{id}</span>
          <div className="relative flex-1 h-[12px] rounded-[3px] bg-white/[.03]">
            {r.state === 'cached'
              ? <span className="absolute -top-[1px] text-[10.5px] text-[var(--info)]" style={{ left: pct(r.start_ms) }}>◆ served from cache</span>
              : r.state === 'stubbed'
                ? <span className="absolute -top-[1px] text-[10.5px] text-[var(--bad)]">not executed (irreversible)</span>
                : <motion.div layout className="absolute top-0 bottom-0 rounded-[3px]"
                    style={{ left: pct(r.start_ms), width: `calc(${pct(Math.max((r.end_ms ?? end) - r.start_ms, end / 300))})`, background: color, opacity: r.state === 'running' ? 0.55 : 1 }} />}
          </div>
        </div>
      ))}
    </div>
  )
  return (
    <Card eyebrow="On one clock" title="One step after another, against the lookups at the same time">
      {lane('As written', a, 'var(--line-strong)', s.arms.as_written?.wall_s)}
      {lane('Compiled by synapse0g', c, 'var(--accent)', s.arms.compiled?.wall_s)}
      <p className="text-[12px] text-[var(--faint)] mt-2">Seconds from the moment each version started; both started together. Bars are the steps' measured run times.</p>
    </Card>
  )
}

function Big({ value, label, tone = 'var(--text-strong)' }) {
  return (
    <div>
      <div className="console-title text-[46px] leading-none" style={{ color: tone }}>{value}</div>
      <div className="text-[12.5px] text-[var(--muted)] mt-1.5">{label}</div>
    </div>
  )
}

/** Step by step: what each version did, from this run's calls. */
function Steps({ s }) {
  const by = (arm) => {
    const m = {}
    for (const c of s.calls[arm]) {
      const step = s.stepOf[c.step]
      if (!step) continue
      ;(m[step] ||= []).push(c)
    }
    return m
  }
  const a = by('as_written'), c = by('compiled')
  const parallel = new Set((s.compiled?.after?.meta?.parallel_groups || []).flat())
  const row = (step) => {
    const x = a[step]?.[0], y = c[step]?.[0]
    const cr = s.compRun[step]
    const how = []
    if (parallel.has(step)) how.push(['parallel', 'runs beside the other lookups'])
    if (cr?.state === 'cached') how.push(['cached', 'served from the cache'])
    for (const t of y?.techniques || []) how.push([tech(t), t === 'reasoning_off' ? 'hidden reasoning switched off' : `routed to ${t.slice(6)}`])
    if (step === 'place_order') {
      return (
        <tr key={step} className="border-t border-[var(--line)] bg-[var(--bad-bg)]/40">
          <td className="py-2 pl-1 font-mono text-[var(--text-strong)]">{step}<div className="font-sans text-[11px] text-[var(--bad)]">irreversible: money moves</div></td>
          <td colSpan={3} className="text-[var(--muted)]">{s.arms.as_written?.receipt?.po_number ? <>placed order <span className="font-mono text-[var(--text-strong)]">{s.arms.as_written.receipt.po_number}</span> on the test marketplace</> : '—'}</td>
          <td colSpan={3} className="text-[var(--muted)]">{s.stub ? <span className="inline-flex items-center gap-1.5"><Ban className="w-3.5 h-3.5 text-[var(--bad)]" />not executed: {s.stub.reason}</span> : '—'}</td>
          <td className="text-[var(--muted)]">never cached, re-ordered, re-run or sped up</td>
        </tr>
      )
    }
    return (
      <tr key={step} className="border-t border-[var(--line)]">
        <td className="py-2 pl-1 font-mono text-[var(--text-strong)]">{step}</td>
        <td className="font-mono text-[var(--muted)]">{x?.tag || '—'}</td>
        <td className="text-right font-mono">{s1(x?.latency_s)}</td>
        <td className="text-right font-mono">{x ? n0(x.reasoning_tokens || 0) : '—'}</td>
        <td className="pl-6 font-mono text-[var(--muted)]">{cr?.state === 'cached' ? 'cache' : y?.tag || '—'}</td>
        <td className="text-right font-mono text-[var(--text-strong)]">{cr?.state === 'cached' ? '0.0 s' : s1(y?.latency_s)}</td>
        <td className="text-right font-mono text-[var(--text-strong)]">{cr?.state === 'cached' ? '0' : y ? n0(y.reasoning_tokens || 0) : '—'}</td>
        <td className="pl-6">
          <div className="flex flex-wrap gap-1">
            {how.map(([k, why]) => <span key={k} title={why} className="px-1.5 py-[1px] rounded-[4px] bg-[var(--accent)]/15 text-[var(--accent)] text-[11px]">{k}</span>)}
            {!how.length && <span className="text-[var(--ghost)] text-[11.5px]">unchanged</span>}
          </div>
        </td>
      </tr>
    )
  }
  return (
    <Card eyebrow="Step by step" title="Where the time and money went, on this request">
      <table className="w-full text-[12.5px] table-fixed">
        <colgroup><col className="w-[150px]" /><col className="w-[120px]" /><col className="w-[70px]" /><col className="w-[70px]" /><col className="w-[140px]" /><col className="w-[70px]" /><col className="w-[70px]" /><col /></colgroup>
        <thead>
          <tr className="text-[11px] text-[var(--faint)] text-left">
            <th className="font-normal pb-2 pl-1">step</th>
            <th className="font-normal" colSpan={3}>as written: model · time · hidden reasoning</th>
            <th className="font-normal pl-6" colSpan={3}>synapse0g: model · time · hidden reasoning</th>
            <th className="font-normal pl-6">what synapse0g changed</th>
          </tr>
        </thead>
        <tbody>{STEPS.map(row)}</tbody>
      </table>
      <p className="text-[12px] text-[var(--faint)] mt-3">Times are each model call's own time. In the compiled graph the four lookups overlap, so the run takes about as long as the slowest of them plus the decision.</p>
    </Card>
  )
}

function Refused({ policy }) {
  const blocked = policy.trials.filter((t) => !t.approve)
  const kept = policy.trials.filter((t) => t.approve)
  return (
    <Card eyebrow="The firewall" title="Changes it tried, kept and refused" right={<span className="text-[11.5px] text-[var(--faint)]">from the study on tuning requests · {policy.report}</span>}>
      <div className="grid md:grid-cols-2 gap-5 text-[12.5px]">
        <div>
          <div className="text-[11px] uppercase tracking-[.12em] text-[var(--bad)] mb-2">refused</div>
          {blocked.length === 0 && <p className="text-[var(--ghost)]">none</p>}
          {blocked.map((t, i) => (
            <div key={i} className="border-l-2 border-[var(--bad)] pl-3 py-1 mb-2">
              <div className="text-[var(--text-strong)]"><span className="font-mono">{tech(t.technique)}</span> on “{t.label}”</div>
              <div className="text-[var(--muted)]">{t.reasons.join('; ')}</div>
            </div>
          ))}
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-[.12em] text-[var(--ok)] mb-2">kept, now applied in this run</div>
          {kept.map((t, i) => (
            <div key={i} className="border-l-2 border-[var(--ok)] pl-3 py-1 mb-2">
              <div className="text-[var(--text-strong)]"><span className="font-mono">{tech(t.technique)}</span> on “{t.label}”</div>
              <div className="text-[var(--muted)]">{t.why}; cost {(t.cost_gain * 100).toFixed(0)}% lower, no request broken ({t.passes?.join(' / ')} passed)</div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  )
}

function Result({ s }) {
  const { as_written: a, compiled: c, same_decision } = s.result
  const ho = s.policy?.held_out
  const speed = a.wall_s / c.wall_s
  const saved = a.billed_usd && c.billed_usd != null ? 1 - c.billed_usd / a.billed_usd : null
  return (
    <div className="panel p-6 border-l-2 !border-l-[var(--accent)]">
      <div className="flex items-center justify-between mb-5">
        <span className="eyebrow">This run · one held-out request · measured</span>
        <span className="text-[12px] text-[var(--faint)]">{s.task.label} · {s.task.tag} on 0G</span>
      </div>
      <div className="flex flex-wrap gap-12 items-end">
        <Big value={`${speed.toFixed(2)}×`} label={`faster: ${a.wall_s.toFixed(1)} s → ${c.wall_s.toFixed(1)} s`} tone="var(--accent)" />
        <Big value={saved == null ? '—' : `${Math.round(saved * 100)}%`} label={`cheaper, 0G billed: ${money(a.billed_usd)} → ${money(c.billed_usd)}`} />
        <Big value={`${n0(a.reasoning_tokens)} → ${n0(c.reasoning_tokens)}`} label="hidden reasoning tokens" />
        <div className="flex flex-col gap-1.5 text-[13px]">
          {[['as written', a], ['synapse0g', c]].map(([k, v]) => (
            <div key={k} className="flex items-center gap-2">
              {v.passed ? <Check className="w-4 h-4 text-[var(--ok)]" /> : <X className="w-4 h-4 text-[var(--bad)]" />}
              <span className="w-24 text-[var(--muted)]">{k}</span>
              <span className="font-mono text-[var(--text-strong)] max-w-[260px] truncate" title={v.short || ''}>{v.short || 'no answer'}</span>
              <span className="text-[var(--faint)]">{v.passed ? 'right answer' : 'wrong'}</span>
            </div>
          ))}
          <div className="text-[12px] text-[var(--faint)]">{same_decision ? 'same decision' : 'different decisions'}</div>
        </div>
      </div>
      {(a.error || c.error) && <p className="mt-4 text-[13px] text-[var(--bad)]">{a.error || c.error}</p>}
      {ho?.verdict && (
        <div className="mt-6 pt-4 border-t border-[var(--line)] flex flex-wrap items-center gap-4 text-[13px]">
          <ShieldCheck className="w-4 h-4 text-[var(--ok)]" />
          <span className="text-[var(--muted)]">One {s.task.task_noun} is not a verdict. On {ho.tasks} held-out {s.task.task_noun}s the firewall said</span>
          <Decision decision={ho.verdict.approve ? 'APPROVE' : 'BLOCK'} />
          <span className="text-[var(--muted)]">
            {ho.verdict.passes?.[0]}/{ho.tasks} → {ho.verdict.passes?.[2]}/{ho.tasks} passed · cost {Math.round(ho.verdict.cost_gain * 100)}% lower · time {Math.round(ho.verdict.latency_gain * 100)}% lower
          </span>
        </div>
      )}
    </div>
  )
}

/** An agent whose study approved no change: it ships as written. One live run, and why nothing changed. */
function Unchanged({ s }) {
  const a = s.result.as_written
  const tried = s.policy?.trials?.length || 0
  return (
    <div className="panel p-6 border-l-2 !border-l-[var(--line-strong)]">
      <div className="flex items-center justify-between mb-5">
        <span className="eyebrow">This run · one held-out {s.task.task_noun} · measured</span>
        <span className="text-[12px] text-[var(--faint)]">{s.task.tag} on 0G</span>
      </div>
      <div className="flex flex-wrap gap-12 items-end">
        <Big value="unchanged" label="no change passed the firewall, so the agent ships as written" />
        <Big value={`${a.wall_s.toFixed(1)} s`} label={`${a.calls} model calls · ${money(a.billed_usd)} billed by 0G`} />
        <div className="flex items-center gap-2 text-[13px]">
          {a.passed ? <Check className="w-4 h-4 text-[var(--ok)]" /> : <X className="w-4 h-4 text-[var(--bad)]" />}
          <span className="font-mono text-[var(--text-strong)] max-w-[300px] truncate" title={a.short || ''}>{a.short || 'no answer'}</span>
          <span className="text-[var(--faint)]">{a.passed ? 'right answer' : 'wrong'}</span>
        </div>
      </div>
      {a.error && <p className="mt-4 text-[13px] text-[var(--bad)]">{a.error}</p>}
      <p className="mt-5 pt-4 border-t border-[var(--line)] text-[13px] text-[var(--muted)]">
        Its study tried {tried} change{tried === 1 ? '' : 's'} on tuning {s.task.task_noun}s and the firewall refused {tried === 1 ? 'it' : 'every one'} (below).
        Shipping nothing is a result: a change that cannot show it keeps answers right is not made.
      </p>
    </div>
  )
}

const PHASES = [['compile', 'Compile to SIR'], ['race', 'Run on 0G'], ['result', 'Compare']]

export default function LiveRun() {
  const [agent, setAgent] = useState('supplier')
  const tasks = useQuery({ queryKey: ['live-tasks', agent], queryFn: () => call(`/api/live/tasks?agent=${agent}`), staleTime: Infinity })
  const [task, setTask] = useState('')
  const [cold, setCold] = useState(false)
  const { resultId: runId, go } = useView()
  const [startError, setStartError] = useState(null)
  const { events, done, error } = useLiveRun(runId)
  const s = useMemo(() => fold(events), [events])
  const chain = useMemo(() => s.compiled && chainOf(s.compiled.before, s.compiled.order), [s.compiled])
  const running = !!runId && !done
  const phaseNow = s.result ? 'result' : s.phase?.phase

  const start = async () => {
    setStartError(null)
    try {
      const r = await call('/api/live', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent, task: task || undefined, cold }) })
      go('live', r.id)
    } catch (e) { setStartError(e) }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageTitle eyebrow="Live · 0G mainnet · real calls" title="Watch an agent get compiled, optimized and checked">
        Pick an agent and one of its held-out tasks. It is compiled to SIR where it has a graph, then run as written and as compiled at the
        same moment, on the same task, on 0G. Run metrics come from this task; the labelled held-out verdict comes from the agent's saved study.
      </PageTitle>

      <div className="panel px-5 py-4 flex flex-wrap items-center gap-4">
        <label className="text-[13px] text-[var(--muted)]">Agent</label>
        <select value={agent} onChange={(e) => { setAgent(e.target.value); setTask('') }} disabled={running}
          className="h-9 px-3 rounded-[8px] bg-[var(--bg)] border border-[var(--line-strong)] text-[13px] text-[var(--text-strong)]">
          {(tasks.data?.agents || [{ id: 'supplier', label: 'Supplier check' }]).map((a) => <option key={a.id} value={a.id} title={a.framework}>{a.label}</option>)}
        </select>
        <label className="text-[13px] text-[var(--muted)]">Task</label>
        <select value={task} onChange={(e) => setTask(e.target.value)} disabled={running}
          className="h-9 min-w-[380px] max-w-[520px] px-3 rounded-[8px] bg-[var(--bg)] border border-[var(--line-strong)] text-[13px] text-[var(--text-strong)]">
          <option value="">{tasks.isLoading ? 'loading…' : tasks.error ? 'could not load tasks' : 'first held-out task'}</option>
          {tasks.data?.tasks.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <label className="flex items-center gap-2 text-[13px] text-[var(--muted)]" title="Compile again and empty the step cache">
          <input type="checkbox" checked={cold} onChange={(e) => setCold(e.target.checked)} disabled={running} /> start cold
        </label>
        <button onClick={start} disabled={running}
          className="inline-flex items-center gap-2 h-9 px-5 rounded-[8px] bg-[var(--accent)] text-black text-[13.5px] font-semibold disabled:opacity-40">
          <Play className="w-4 h-4" strokeWidth={2.2} /> {running ? 'Running…' : 'Run live'}
        </button>
        <div className="ml-auto flex items-center gap-1 text-[12.5px]">
          {PHASES.map(([k, label], i) => {
            const idx = PHASES.findIndex((p) => p[0] === phaseNow)
            const st = !runId ? 'todo' : i < idx || (k === 'result' && s.result) ? 'done' : i === idx ? 'now' : 'todo'
            return (
              <span key={k} className="flex items-center gap-1">
                {i > 0 && <ArrowRight className="w-3.5 h-3.5 text-[var(--ghost)]" />}
                <span className={`flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] ${st === 'now' ? 'bg-[var(--accent)]/15 text-[var(--accent)]' : st === 'done' ? 'text-[var(--ok)]' : 'text-[var(--ghost)]'}`}>
                  {st === 'done' ? <Check className="w-3.5 h-3.5" /> : st === 'now' ? <CircleDot className="w-3.5 h-3.5 animate-pulse" /> : <CircleDot className="w-3.5 h-3.5" />} {label}
                </span>
              </span>
            )
          })}
        </div>
      </div>

      {startError && <div className="panel p-4 text-[13px] text-[var(--bad)]">{startError.message}</div>}
      {error && <div className="panel p-4 text-[13px] text-[var(--bad)]">Lost the run: {error.message}</div>}
      {s.failed && <div className="panel p-4 text-[13px] text-[var(--bad)]">The run failed: {s.failed.error}</div>}

      {!runId && (
        <Card title="What happens when you press Run live">
          <ol className="grid md:grid-cols-4 gap-4 text-[13px] text-[var(--muted)] list-decimal list-inside">
            <li>The agent is lifted to SIR: each step, its effect, and what it really reads. A ReAct agent has no graph to rewrite, and the page says so.</li>
            <li>The passes rewrite the graph. Each says what it changed, or what it refused and why: irreversible and external steps are never touched.</li>
            <li>As written and compiled run at the same moment on 0G, with the per-step changes the firewall approved. If it approved none, the agent runs once, unchanged.</li>
            <li>Time, tokens, hidden reasoning, 0G's bill and the answer, side by side. A compiled run never executes an irreversible or external step.</li>
          </ol>
        </Card>
      )}

      {runId && s.phase?.phase === 'compile' && !s.compiled && (
        <Card eyebrow="1 · Compile" title={s.phase.text}>
          <div className="flex flex-col gap-1.5 text-[12.5px]">
            {s.calls.compile.map((c, i) => (
              <motion.div key={i} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-4 font-mono">
                <span className="text-[var(--ok)]">traced</span><span className="text-[var(--text)] w-[320px] truncate">{c.step_label}</span>
                <span>{c.latency_s?.toFixed(1)} s</span><span className="text-[var(--muted)]">{n0(c.tokens_in)} in · {n0(c.tokens_out)} out</span>
              </motion.div>
            ))}
            <div className="text-[var(--faint)] animate-pulse">tracing the next step…</div>
          </div>
        </Card>
      )}

      {s.compiled && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <span className="eyebrow">1 · Compiled to SIR</span>
            <span className="text-[12.5px] text-[var(--muted)]">
              {s.compiled.reused ? `graph compiled earlier on ${s.compiled.compiled_on} (${s.compiled.seconds}s), reused: same app, same SIR` : `traced and compiled in ${s.compiled.seconds}s`}
              {' · '}passes applied: <span className="text-[var(--accent)]">{s.compiled.applied.join(', ')}</span>
            </span>
            <span className="ml-auto"><EffectLegend /></span>
          </div>
          <Compare before={s.compiled.before} after={s.compiled.after} passes={s.compiled.passes} asWritten={s.compiled.order} height={300} />
        </section>
      )}

      {s.task?.kind === 'loop' && (
        <Card eyebrow="1 · The agent" title={s.task.framework}>
          <p className="text-[13px] text-[var(--muted)]">{s.phase?.phase === 'compile' ? s.phase.text : 'A ReAct agent is one model step repeated with tools: no graph to rewrite. Only call-level changes apply, and only those its firewall approved.'}</p>
        </Card>
      )}

      {(s.compiled || (s.task?.kind === 'loop' && s.armStart != null)) && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <span className="eyebrow">2 · {s.policy && !Object.keys(s.policy.steps).length && !s.compiled ? `Run live, unchanged` : `Both versions, same ${s.task?.task_noun || 'task'}, same moment`}</span>
            {s.task && <span className="text-[12.5px] text-[var(--muted)] truncate">{s.task.summary}</span>}
          </div>
          <div className={`grid gap-4 ${s.compiled || (s.policy && Object.keys(s.policy.steps).length) ? 'xl:grid-cols-2' : ''}`}>
            <Lane title="As written" sub={`${s.task?.tag} · ${s.task?.agent === 'supplier' ? 'one step after another' : s.task?.framework}`}
              graph={s.task?.agent === 'supplier' ? chain : s.compiled?.before} run={s.asRun} calls={s.calls.as_written}
              summary={s.arms.as_written} running={running} accent="var(--line-strong)" stepOf={s.stepOf}
              base={s.armStart != null ? s.t - s.armStart : null} direction={s.task?.agent === 'supplier' ? 'LR' : undefined} />
            {(s.compiled || (s.policy && Object.keys(s.policy.steps).length > 0)) && (
              <Lane title="Compiled by synapse0g"
                sub={s.task?.agent === 'supplier' ? 'parallel · approved changes · order stubbed' : s.compiled ? 'compiled graph · approved changes · external steps stubbed' : 'approved call changes'}
                graph={s.compiled?.after} run={s.compRun} calls={s.calls.compiled} summary={s.arms.compiled} running={running}
                accent="var(--accent)" stepOf={s.stepOf} base={s.armStart != null ? s.t - s.armStart : null} />
            )}
          </div>
        </section>
      )}

      {s.task?.agent === 'supplier' && s.compiled && (Object.keys(s.asRun).length > 0 || Object.keys(s.compRun).length > 0) && <Timeline s={s} />}
      {s.result && (s.result.unchanged ? <Unchanged s={s} /> : <Result s={s} />)}
      {s.result && s.task?.agent === 'supplier' && <Steps s={s} />}
      {s.policy && s.result && <Refused policy={s.policy} />}
      {s.result && (
        <p className="text-[12px] text-[var(--faint)] flex items-center gap-2"><Gauge className="w-3.5 h-3.5" /> Run again with another task: the graph is reused, and pure steps already computed are served from the cache. <Zap className="w-3.5 h-3.5" /> Tick “start cold” to compile from scratch.</p>
      )}
    </div>
  )
}
