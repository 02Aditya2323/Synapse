import { useState } from 'react'
import { ArrowRight, ArrowUpRight, Check, CircleDashed, CircleHelp, Coins } from 'lucide-react'
import { useAgent, useFunding, useJobs, useSubmitJob } from '../api.js'
import { Card, Decision, PageTitle } from '../components/bits.jsx'
import { money } from '../format.js'

const og = (v, d = 6) => (v == null ? '—' : `${v.toFixed(d)} 0G`)
const short = (h) => (h ? `${h.slice(0, 6)}…${h.slice(-4)}` : '—')
const ago = (t) => {
  const s = Math.max(0, Date.now() / 1000 - t)
  return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`
}

const STATE = {
  done: { icon: Check, color: 'var(--ok)', word: 'happened' },
  waiting: { icon: CircleDashed, color: 'var(--ghost)', word: 'not yet' },
  unknown: { icon: CircleHelp, color: 'var(--warn)', word: 'cannot be read' },
}

function Ext({ href, children }) {
  if (!href) return <span className="font-mono">{children}</span>
  return (
    <a href={href} target="_blank" rel="noreferrer" className="font-mono text-[var(--info)] hover:underline inline-flex items-center gap-0.5">
      {children}<ArrowUpRight className="w-3 h-3" />
    </a>
  )
}

/** Agent ID, token, owner, network: each the real value or "not yet". */
function Identity({ funding }) {
  const [identity, token] = funding.stages
  const t = token.evidence || {}
  const fields = [
    ['Agent ID', identity.evidence?.agent_id],
    ['Token', t.token && <Ext href={t.explorer}>{t.symbol ? `${t.symbol} · ${short(t.token)}` : short(t.token)}</Ext>],
    ['Owner wallet', identity.evidence?.owner && <span className="font-mono">{short(identity.evidence.owner)}</span>],
    ['Compute share', t.split && `${(t.split.compute_bps / 100).toFixed(1)}% of creator fees`],
    ['Network', `0G mainnet · ${funding.network.chain_id}`],
  ]
  return (
    <div className="panel px-5 py-4 flex flex-wrap items-center gap-x-8 gap-y-3">
      {fields.map(([k, v]) => (
        <div key={k} className="min-w-[120px]">
          <div className="text-[11px] uppercase tracking-[.12em] text-[var(--ghost)]">{k}</div>
          <div className={`text-[13.5px] mt-0.5 ${v ? 'text-[var(--text-strong)]' : 'text-[var(--ghost)] italic'}`}>{v || 'not yet'}</div>
        </div>
      ))}
      <div className="ml-auto">
        {t.url ? (
          <a href={t.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 h-9 px-4 rounded-[8px] bg-[var(--accent)] text-black text-[13px] font-semibold hover:brightness-110">
            <Coins className="w-4 h-4" /> Buy the token on comfy
          </a>
        ) : (
          <span title="The token has not launched yet" className="inline-flex items-center gap-2 h-9 px-4 rounded-[8px] border border-[var(--line)] text-[var(--ghost)] text-[13px] cursor-not-allowed">
            <Coins className="w-4 h-4" /> Token not launched
          </span>
        )}
      </div>
    </div>
  )
}

/** The loop, stage by stage, each read from its own source. */
function Loop({ stages }) {
  return (
    <Card eyebrow="The loop" title="From a trade to a paid model call" right={<span className="text-[12px] text-[var(--faint)]">each stage read from its own source; nothing is assumed from the one before</span>}>
      <ol className="grid md:grid-cols-3 xl:grid-cols-6 gap-3">
        {stages.map((s, i) => {
          const st = STATE[s.state] || STATE.unknown
          const Icon = st.icon
          return (
            <li key={s.key} className="relative rounded-[10px] border p-3.5 flex flex-col gap-2 min-h-[150px]"
              style={{ borderColor: s.state === 'done' ? 'color-mix(in srgb, var(--ok) 40%, transparent)' : 'var(--line)', background: s.state === 'done' ? 'var(--ok-bg)' : 'rgba(255,255,255,.015)' }}>
              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[.1em]" style={{ color: st.color }}>
                <Icon className="w-3.5 h-3.5" strokeWidth={2} /> {i + 1} · {st.word}
              </div>
              <div className="text-[13.5px] font-semibold text-[var(--text-strong)] leading-snug">{s.title}</div>
              <div className="text-[12px] text-[var(--muted)] leading-relaxed">{s.detail}</div>
              {i < stages.length - 1 && <ArrowRight className="hidden xl:block absolute -right-[11px] top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--line-strong)] z-[1]" />}
            </li>
          )
        })}
      </ol>
    </Card>
  )
}

const MODES = [
  { key: 'paused', text: 'paused', note: 'nothing that costs money runs; the queue is kept' },
  { key: 'static', text: 'static', note: 'inspect jobs only; studies wait for funds' },
  { key: 'reduced', text: 'reduced', note: 'a smaller study, marked as smaller' },
  { key: 'full', text: 'full', note: 'the whole study at its quoted size' },
]

/** Balance against the thresholds that choose how the agent works. */
function Budget({ agent }) {
  const { balance_usd: bal, reserve_usd: reserve, thresholds: th, mode } = agent
  const top = Math.max(th.full_at_usd * 1.25, bal * 1.05, 0.01)
  const at = (v) => `${Math.min(100, (v / top) * 100)}%`
  const bands = [
    ['paused', 0, reserve], ['static', reserve, th.reduced_at_usd], ['reduced', th.reduced_at_usd, th.full_at_usd], ['full', th.full_at_usd, top],
  ]
  const shade = { paused: 'rgba(239,68,68,.16)', static: 'rgba(245,158,11,.13)', reduced: 'rgba(56,189,248,.12)', full: 'rgba(34,197,94,.14)' }
  const current = MODES.find((m) => m.key === mode)
  return (
    <Card eyebrow="Budget" title="It slows down before it runs out" right={<span className="text-[11.5px] text-[var(--faint)]">{agent.balance_source}</span>}>
      <div className="flex items-end gap-6">
        <div>
          <div className="console-title text-[40px] text-[var(--text-strong)]">{money(bal)}</div>
          <div className="text-[12.5px] text-[var(--muted)]">spendable now</div>
        </div>
        <div className="pb-1.5">
          <div className="text-[11px] uppercase tracking-[.12em] text-[var(--ghost)]">mode</div>
          <div className="text-[18px] font-semibold text-[var(--accent)]">{current?.text}</div>
        </div>
        <div className="pb-1.5 text-[12.5px] text-[var(--muted)] max-w-[260px]">{current?.note}</div>
      </div>
      <div className="relative mt-6 h-7 rounded-[6px] overflow-hidden flex border border-[var(--line)]">
        {bands.map(([k, a, b]) => (
          <div key={k} className="h-full flex items-center justify-center text-[10.5px] uppercase tracking-[.1em] text-[var(--faint)] border-r border-[var(--bg)] last:border-r-0"
            style={{ width: `calc(${at(b)} - ${at(a)})`, background: shade[k] }}>{(b - a) / top > 0.08 ? k : ''}</div>
        ))}
        <div className="absolute top-0 bottom-0 w-[2px] bg-[var(--text-strong)]" style={{ left: at(bal) }} />
      </div>
      <div className="relative h-5 mt-1 text-[10.5px] font-mono text-[var(--faint)]">
        {[reserve, th.reduced_at_usd, th.full_at_usd].map((v) => <span key={v} className="absolute -translate-x-1/2" style={{ left: at(v) }}>{money(v)}</span>)}
      </div>
      <p className="text-[12px] text-[var(--faint)] mt-2">
        A study runs in full above {money(th.full_at_usd)} and reduced above {money(th.reduced_at_usd)}. The last {money(reserve)} is never spent, so status and inspect jobs keep answering.
        Every study also has a hard cap: at most twice its quote, and never more than the balance above the reserve.
      </p>
    </Card>
  )
}

function Quotes({ quotes }) {
  const rows = [['Runs', (q) => q.runs], ['Model calls', (q) => q.calls.toLocaleString()], ['Cost per call', (q) => money(q.usd_per_call)], ['Provider cost', (q) => money(q.provider_usd)], ['Price (×1.5)', (q) => money(q.price_usd)]]
  const q0 = quotes.full
  return (
    <Card eyebrow="Pricing" title="What a study costs before it runs">
      <table className="w-full text-[13px]">
        <thead><tr className="text-[11.5px] text-[var(--faint)] text-left"><th className="font-normal pb-2" />{Object.keys(quotes).map((m) => <th key={m} className="font-normal pb-2 text-right">{m} · {quotes[m].tuning_tasks}+{quotes[m].held_out_tasks} tasks</th>)}</tr></thead>
        <tbody>
          {rows.map(([k, f]) => (
            <tr key={k} className="border-t border-[var(--line)]"><td className="py-1.5 text-[var(--muted)]">{k}</td>{Object.values(quotes).map((q) => <td key={q.size} className="py-1.5 text-right font-mono text-[var(--text-strong)]">{f(q)}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <p className="text-[12px] text-[var(--faint)] mt-3">Cost per call: {q0.per_call_source}. Runs include the graph check, every trial with its recheck, and the held-out measurement.</p>
    </Card>
  )
}

function Trades({ fees }) {
  const { totals, recent } = fees.evidence
  return (
    <Card eyebrow="Earnings" title="Trades of the agent's token" right={<span className="text-[12px] text-[var(--faint)]">read from ChargeTaken events on chain</span>}>
      {recent.length === 0 ? (
        <p className="text-[13px] text-[var(--ghost)]">No trades yet. Each trade on comfy will appear here with its transaction, the fee it paid and the share that goes to compute.</p>
      ) : (
        <>
          <div className="flex gap-8 mb-4 text-[13px]">
            <div><div className="text-[var(--faint)] text-[11.5px]">trades by others</div><div className="font-mono text-[var(--text-strong)]">{totals.outside_trades}</div></div>
            <div><div className="text-[var(--faint)] text-[11.5px]">volume</div><div className="font-mono text-[var(--text-strong)]">{og(totals.volume_0g, 4)}</div></div>
            <div><div className="text-[var(--faint)] text-[11.5px]">to compute, per the split</div><div className="font-mono text-[var(--ok)]">{og(totals.compute_0g)}</div></div>
            <div><div className="text-[var(--faint)] text-[11.5px]">creator earnings (claimable)</div><div className="font-mono text-[var(--text-strong)]">{og(totals.claimable_0g)}</div></div>
            {totals.team_trades > 0 && <div><div className="text-[var(--faint)] text-[11.5px]">team trades, not counted</div><div className="font-mono text-[var(--warn)]">{totals.team_trades}</div></div>}
          </div>
          <table className="w-full text-[12.5px]">
            <thead><tr className="text-[11px] text-[var(--faint)] text-left"><th className="font-normal pb-1.5">tx</th><th className="font-normal">trader</th><th className="font-normal">side</th><th className="font-normal text-right">amount</th><th className="font-normal text-right">fees</th><th className="font-normal text-right">to compute</th></tr></thead>
            <tbody>
              {recent.map((r) => (
                <tr key={r.tx} className={`border-t border-[var(--line)] ${r.team ? 'opacity-50' : ''}`}>
                  <td className="py-1.5"><Ext href={r.explorer}>{short(r.tx)}</Ext></td>
                  <td className="font-mono text-[var(--muted)]">{short(r.trader)}{r.team && <span className="ml-1.5 text-[var(--warn)] font-sans">team</span>}</td>
                  <td className={r.is_buy ? 'text-[var(--ok)]' : 'text-[var(--bad)]'}>{r.is_buy ? 'buy' : 'sell'}</td>
                  <td className="text-right font-mono">{og(r.quote_0g, 4)}</td>
                  <td className="text-right font-mono">{og(r.base_fee_0g + r.opening_fee_0g + r.creator_tax_0g)}</td>
                  <td className="text-right font-mono text-[var(--ok)]">{r.team ? '—' : og(r.compute_0g)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  )
}

function Receipts({ stage, credit }) {
  const e = stage.evidence || {}
  return (
    <Card eyebrow="Spending" title="Inference receipts" right={<span className="text-[12px] text-[var(--faint)]">paid from: {e.paid_from}</span>}>
      <div className="flex gap-8 text-[13px] mb-3">
        <div><div className="text-[var(--faint)] text-[11.5px]">0G-billed calls</div><div className="font-mono text-[var(--text-strong)]">{(e.calls || 0).toLocaleString()}</div></div>
        <div><div className="text-[var(--faint)] text-[11.5px]">billed by 0G</div><div className="font-mono text-[var(--text-strong)]">{money(e.billed_usd)}</div></div>
        <div><div className="text-[var(--faint)] text-[11.5px]">credit arrivals seen</div><div className="font-mono text-[var(--text-strong)]">{credit.evidence?.rises?.length ?? 0}</div></div>
      </div>
      <table className="w-full text-[12.5px]">
        <thead><tr className="text-[11px] text-[var(--faint)] text-left"><th className="font-normal pb-1.5">request id</th><th className="font-normal text-right">billed</th><th className="font-normal pl-4">from</th></tr></thead>
        <tbody>
          {(e.latest || []).map((r) => (
            <tr key={r.request_id} className="border-t border-[var(--line)]">
              <td className="py-1 font-mono text-[var(--muted)]">{r.request_id}</td>
              <td className="text-right font-mono text-[var(--text-strong)]">{money(r.billed_usd)}</td>
              <td className="pl-4 text-[var(--faint)] truncate max-w-[220px]">{r.report}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[12px] text-[var(--faint)] mt-3">Request ids and billed cost as 0G returned them with each call. Counted once per request id; search trials are in each study's spend but not listed here.</p>
    </Card>
  )
}

function Jobs() {
  const jobs = useJobs()
  const submit = useSubmitJob()
  const [path, setPath] = useState('synapse0g/samples/market_desk.py')
  const send = (kind) => submit.mutate(kind === 'study' ? { kind, spec: { agent: 'procurement' } } : { kind, spec: { path } }, { onSuccess: () => jobs.refetch() })
  return (
    <Card eyebrow="Work" title="Jobs" right={<span className="text-[12px] text-[var(--faint)]">one at a time, within the balance</span>}>
      <div className="flex flex-wrap gap-2 items-center mb-4">
        <input value={path} onChange={(e) => setPath(e.target.value)} className="h-9 w-[340px] rounded-[8px] bg-[var(--bg)] border border-[var(--line)] px-3 font-mono text-[12.5px]" aria-label="path of an agent to inspect" />
        <button onClick={() => send('inspect')} className="h-9 px-3.5 rounded-[8px] border border-[var(--line-strong)] text-[13px] hover:bg-white/[.04]">Inspect (free)</button>
        <button onClick={() => send('study')} className="h-9 px-3.5 rounded-[8px] border border-[var(--accent)] text-[var(--accent)] text-[13px] hover:bg-[var(--accent)]/10">Quote a procurement study</button>
        {submit.error && <span className="text-[12.5px] text-[var(--bad)]">{submit.error.message}</span>}
      </div>
      {jobs.error ? <p className="text-[13px] text-[var(--bad)]">{jobs.error.message}</p> : !jobs.data?.jobs?.length ? (
        <p className="text-[13px] text-[var(--ghost)]">No jobs yet.</p>
      ) : (
        <table className="w-full text-[12.5px]">
          <thead><tr className="text-[11px] text-[var(--faint)] text-left"><th className="font-normal pb-1.5">job</th><th className="font-normal">kind</th><th className="font-normal">status</th><th className="font-normal">mode</th><th className="font-normal text-right">quoted</th><th className="font-normal text-right">spent</th><th className="font-normal pl-4">result</th></tr></thead>
          <tbody>
            {jobs.data.jobs.map((j) => (
              <tr key={j.id} className="border-t border-[var(--line)] align-top">
                <td className="py-1.5 font-mono text-[var(--muted)]">{j.id}<div className="font-sans text-[var(--ghost)]">{ago(j.created)}</div></td>
                <td>{j.kind}</td>
                <td className={{ done: 'text-[var(--ok)]', failed: 'text-[var(--bad)]', waiting: 'text-[var(--warn)]', running: 'text-[var(--info)]' }[j.status] || ''}>{j.status}</td>
                <td>{j.mode || '—'}</td>
                <td className="text-right font-mono">{money(j.quote?.price_usd)}</td>
                <td className="text-right font-mono">{j.status === 'done' ? money(j.spent_usd) : '—'}</td>
                <td className="pl-4 text-[var(--muted)] max-w-[360px]">
                  {j.result?.verdict && <Decision decision={j.result.verdict} />}
                  {j.kind === 'inspect' && j.result && <span>{j.result.after?.nodes?.length} steps · entry {j.result.entry}</span>}
                  {j.note && <div className="text-[var(--faint)] mt-0.5">{j.note}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-[12px] text-[var(--faint)] mt-3">Jobs are run by the agent process (<code className="font-mono">synapse0g agent run</code>). A study that the balance cannot cover waits, and says so.</p>
    </Card>
  )
}

function Failed({ what, error }) {
  return (
    <div className="panel p-5 border-l-2 !border-l-[var(--bad)]">
      <p className="font-medium text-[var(--text-strong)]">{what} could not be read.</p>
      <p className="text-sm text-ink-2 mt-1">{error.message}. Nothing is shown in its place.</p>
    </div>
  )
}

export default function PaidAgent() {
  const funding = useFunding()
  const agent = useAgent()
  const stage = (k) => funding.data?.stages.find((s) => s.key === k)
  return (
    <div className="flex flex-col gap-6">
      <PageTitle eyebrow="Paid agent · 0G mainnet" title="An optimizer that pays for its own model calls">
        synapse0g runs as an agent: it takes optimization jobs, prices them, and pays for every model call from its own 0G compute account.
        That account is funded by trades of its token on comfy. Each stage below is read from where it happens: the chain, the 0G Router, the agent's own billed calls.
      </PageTitle>
      {funding.error ? <Failed what="The funding loop" error={funding.error} /> : funding.isLoading ? <p className="text-ink-3">Reading the chain and the Router…</p> : (
        <>
          <Identity funding={funding.data} />
          <Loop stages={funding.data.stages} />
          {funding.data.errors.length > 0 && <div className="panel p-4 text-[12.5px] text-[var(--warn)]">{funding.data.errors.join(' · ')}</div>}
        </>
      )}
      <div className="grid xl:grid-cols-2 gap-6">
        {agent.error ? <Failed what="The agent's budget" error={agent.error} /> : agent.data && <Budget agent={agent.data} />}
        {agent.data && <Quotes quotes={agent.data.quotes} />}
      </div>
      {funding.data && (
        <div className="grid xl:grid-cols-2 gap-6 items-start">
          <Trades fees={stage('fees')} />
          <Receipts stage={stage('inference')} credit={stage('credit')} />
        </div>
      )}
      <Jobs />
    </div>
  )
}
