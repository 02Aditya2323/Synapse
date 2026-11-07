import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, PauseCircle, Play } from 'lucide-react'
import { Card } from '../components/bits.jsx'
import { useView } from '../store.js'
import { Balances, Calls, Ledger, Optimization, Result, Stages, Tx, call, n18, og } from './paykit.jsx'

function Metric({ label, value, tone = 'var(--text-strong)', sub }) {
  return (
    <div className="px-5 py-4 border-r border-[var(--line)] last:border-r-0">
      <div className="text-[11.5px] text-[var(--faint)]">{label}</div>
      <div className="font-mono text-[22px] leading-tight" style={{ color: tone }}>{value}</div>
      {sub && <div className="text-[11px] text-[var(--faint)] mt-0.5">{sub}</div>}
    </div>
  )
}

/** One paid job, start to finish: everything the live run shows, for the customer's own agent. */
export default function Job() {
  const { resultId: quoteId, go } = useView()
  const [err, setErr] = useState(null)
  const view = useQuery({ queryKey: ['pay', quoteId], queryFn: () => call(`/api/pay/${quoteId}`), enabled: !!quoteId,
    refetchInterval: (q) => (['done', 'expired'].includes(q.state.data?.quote?.status) ? false : 1500) })
  if (!quoteId) return <div className="panel p-6 text-[13px] text-[var(--muted)]">Open a job from <button className="text-[var(--accent)]" onClick={() => go('jobs')}>Jobs and payments</button>.</div>
  if (view.error) return <div className="panel p-4 text-[13px] text-[var(--bad)]">{view.error.message}</div>
  const q = view.data?.quote
  if (!q) return <div className="panel p-6 text-[13px] text-[var(--faint)]">loading the job…</div>
  const events = view.data?.progress?.events || []
  const r = view.data?.job?.result
  const calls = events.filter((e) => e.type === 'call')
  const spent = r?.billed_0g ?? (calls.length ? calls.reduce((a, c) => a + (c.billed_0g || 0), 0) : (view.data?.ledger || []).reduce((a, h) => a + (h.charged_0g || 0), 0))
  const paused = [...events].reverse().find((e) => e.type === 'paused')
  const failed = [...events].reverse().find((e) => e.type === 'failed')
  const name = q.spec.agent === 'custom' ? (q.spec.name || 'your agent') : `supplier check · ${(q.spec.task || '').replace('supplier-', '') || 'first request'}`
  const resume = async () => { setErr(null); try { await call('/api/pay/resume', { quote: quoteId }); view.refetch() } catch (e) { setErr(e) } }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <button onClick={() => go('jobs')} className="inline-flex items-center gap-1.5 text-[12px] text-[var(--muted)] hover:text-[var(--text)] mb-2"><ArrowLeft className="w-3.5 h-3.5" /> Jobs and payments</button>
          <h1 className="console-title text-[30px] text-[var(--text-strong)]">{name}</h1>
          <div className="text-[12.5px] text-[var(--muted)] mt-1 font-mono">{q.job_id || 'not started'} · paid by <Tx hash={q.tx} /></div>
        </div>
        <Stages q={q} events={events} tx={q.tx} />
      </div>

      <div className="panel grid grid-cols-2 md:grid-cols-6 overflow-hidden">
        <Metric label="paid" value={og(n18(q.pay), 4)} />
        <Metric label="compute spent" value={og(spent, 5)} tone="var(--accent)" sub="billed by 0G, call by call" />
        <Metric label="model calls" value={r ? r.calls.length : calls.length || (view.data?.ledger || []).length} />
        <Metric label="time" value={r ? `${r.wall_s.toFixed(1)} s` : '—'} />
        <Metric label="calls at once" value={r ? r.peak_overlap : '—'} sub="the parallel steps, measured" />
        <Metric label="result" value={r ? (r.passed ? 'right' : r.custom ? 'ran' : 'wrong') : q.status} tone={r?.passed ? 'var(--ok)' : 'var(--text-strong)'} />
      </div>

      {(paused || q.status === 'waiting for funds') && (
        <div className="panel p-4 border-l-2 !border-l-[var(--warn)] flex flex-wrap items-center gap-4 text-[13px]">
          <PauseCircle className="w-5 h-5 text-[var(--warn)]" />
          <div className="flex-1 min-w-[300px]">
            <div className="text-[var(--text-strong)] font-medium">Paused before the next call: the payment is safe in the optimizer's account</div>
            <div className="text-[var(--muted)]">{paused?.reason || q.note}</div>
          </div>
          <button onClick={resume} className="inline-flex items-center gap-2 h-9 px-4 rounded-[8px] bg-[var(--accent)] text-black text-[13px] font-semibold"><Play className="w-4 h-4" /> Resume</button>
        </div>
      )}
      {failed && q.status === 'failed' && <div className="panel p-4 text-[13px] text-[var(--bad)]">The job failed: {failed.error}</div>}
      {err && <div className="panel p-4 text-[13px] text-[var(--bad)]">{err.message}</div>}

      <Optimization events={events} />

      {q.job_id && (
        <Card eyebrow="Every model call, paid from the optimizer's account" title={`${calls.length || (view.data?.ledger || []).length} calls`}>
          {calls.length ? <Calls events={events} /> : <Ledger rows={view.data?.ledger || []} />}
        </Card>
      )}

      {r && (
        <div className="panel p-6 border-l-2 !border-l-[var(--accent)]">
          <div className="eyebrow mb-4">Result, and your optimized agent</div>
          <Result job={view.data.job} q={q} quoteId={q.id} />
        </div>
      )}

      {Object.keys(q.balances || {}).length > 0 && (
        <Card eyebrow="Evidence" title="The optimizer's account at each step, read from the contract and from 0G">
          <Balances q={q} />
        </Card>
      )}
    </div>
  )
}
