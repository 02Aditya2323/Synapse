import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { ArrowRight, Ban, Check, CircleDot, Download, ExternalLink, Loader2, PauseCircle, Play, Wallet, X } from 'lucide-react'
import { Card, Decision, PageTitle } from '../components/bits.jsx'
import { Compare } from '../sir/Compare.tsx'
import { SirGraph } from '../sir/graph/SirGraph.tsx'
import { EffectLegend } from '../sir/graph/EffectLegend.tsx'
import { useView } from '../store.js'

export const CHAIN = { id: 16661, hex: '0x4115', rpcs: ['https://0g-mainnet.drpc.org', 'https://evmrpc.0g.ai'], explorer: 'https://chainscan.0g.ai' }
export const og = (v, d = 6) => (v == null ? '—' : `${Number(v).toFixed(d)} 0G`)
export const n18 = (v) => (v == null ? null : Number(v) / 1e18)
export const short = (h, n = 6) => (h ? `${h.slice(0, n + 2)}…${h.slice(-4)}` : '—')
export const when = (t) => (t ? new Date(t * 1000).toLocaleTimeString() : '—')

export async function call(url, body) {
  const r = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)
  const d = await r.json().catch(() => null)
  if (!r.ok) throw new Error(d?.error || `the API answered ${r.status}`)
  return d
}

export function Tx({ hash, kind = 'tx', explorer = CHAIN.explorer }) {
  if (!hash) return <span className="text-[var(--ghost)]">—</span>
  return (
    <a href={`${explorer}/${kind}/${hash}`} target="_blank" rel="noreferrer" title={hash}
      className="inline-flex items-center gap-1 font-mono text-[var(--accent)] hover:underline">
      {short(hash, kind === 'tx' ? 8 : 6)} <ExternalLink className="w-3 h-3" />
    </a>
  )
}

/** The wallet in the browser (MetaMask or any EIP-1193 wallet), switched to 0G mainnet. */
export async function wallet() {
  const eth = window.ethereum
  if (!eth) throw new Error('no wallet in this browser: install MetaMask, or pay from any wallet and paste the transaction hash below')
  const [from] = await eth.request({ method: 'eth_requestAccounts' })
  try {
    await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: CHAIN.hex }] })
  } catch (e) {
    if (e.code !== 4902) throw e
    await eth.request({ method: 'wallet_addEthereumChain', params: [{ chainId: CHAIN.hex, chainName: '0G Mainnet', rpcUrls: CHAIN.rpcs,
      nativeCurrency: { name: '0G', symbol: '0G', decimals: 18 }, blockExplorerUrls: [CHAIN.explorer] }] })
  }
  return { eth, from }
}

export function Flow() {
  const box = (title, sub) => (
    <div className="panel px-4 py-3 min-w-[170px]">
      <div className="text-[13px] font-semibold text-[var(--text-strong)]">{title}</div>
      <div className="text-[11.5px] text-[var(--muted)]">{sub}</div>
    </div>
  )
  const arrow = (label) => (
    <div className="flex flex-col items-center px-1">
      <span className="text-[10.5px] text-[var(--faint)] font-mono">{label}</span>
      <ArrowRight className="w-4 h-4 text-[var(--accent)]" />
    </div>
  )
  return (
    <div className="flex flex-wrap items-center gap-1">
      {box('Customer wallet', 'pays for one job')}
      {arrow('depositFor(agent)')}
      {box("0G Payment Layer", "0G's contract, not ours")}
      {arrow('credit')}
      {box("Agent's compute account", 'no one can withdraw it')}
      {arrow('Router key, capped')}
      {box('Paid model calls', 'request id + 0G charge each')}
    </div>
  )
}

export function Account({ account }) {
  const a = account || {}
  return (
    <Card eyebrow="The agent's 0G compute account" title="What it holds now, read from 0G">
      {!a.connected ? <p className="text-[13px] text-[var(--muted)]">{a.why || 'reading…'}</p> : (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-[12.5px]">
          {[['0G admits calls against', og(a.admission_0g)], ['held by calls in flight', og(a.held_0g)], ['free to reserve', og(a.free_0g)],
            ['spent by the agent (settled)', og(a.spent_0g)], ['paid calls', a.calls]].map(([k, v]) => (
            <div key={k}><div className="text-[var(--faint)]">{k}</div><div className="font-mono text-[15px] text-[var(--text-strong)]">{v}</div></div>
          ))}
        </div>
      )}
      {a.key_cap && (
        <p className="text-[12px] text-[var(--muted)] mt-3">
          Spend cap on the agent's key: <span className="font-mono text-[var(--text-strong)]">{og(a.key_cap.cap_0g, 3)}</span>, {og(a.key_cap.used_0g, 4)} used,
          enforced by <span className={a.key_cap.by === '0G' ? 'text-[var(--ok)]' : 'text-[var(--warn)]'}>{a.key_cap.by}</span> ({a.key_cap.why}).
        </p>
      )}
      <p className="text-[11.5px] text-[var(--faint)] mt-1">0G admits a request only if the account covers at least {og(a.min_cost_0g ?? 0.01, 2)}; it counts 80% of the Payment Layer balance.</p>
    </Card>
  )
}


/** Where this payment is, from the server's record: survives Back and refresh. */
export function stagesOf(q, events, tx) {
  const st = q?.status
  const has = (t) => events.some((e) => e.type === t)
  const paid = ['paid', 'running', 'done', 'waiting for funds', 'failed'].includes(st)
  return [
    ['Quoted', !!q, null],
    ['Payment sent', !!(tx || q?.tx), null],
    ['Confirmed on chain', paid, null],
    ['Compiled to SIR', has('compiled'), null],
    ['Optimized run', has('result') || st === 'done', st === 'waiting for funds' ? 'paused' : st === 'failed' ? 'failed' : null],
    ['Optimized agent returned', st === 'done' && !!q?.job_id, null],
  ]
}

export function Stages({ q, events, tx }) {
  const list = stagesOf(q, events, tx)
  const now = list.findIndex(([, done]) => !done)
  return (
    <div className="flex flex-wrap items-center gap-1 text-[12.5px]">
      {list.map(([label, done, bad], i) => (
        <span key={label} className="flex items-center gap-1">
          {i > 0 && <ArrowRight className="w-3.5 h-3.5 text-[var(--ghost)]" />}
          <span className={`flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] ${done ? 'text-[var(--ok)]' : bad ? 'bg-[var(--warn)]/15 text-[var(--warn)]' : i === now ? 'bg-[var(--accent)]/15 text-[var(--accent)]' : 'text-[var(--ghost)]'}`}>
            {done ? <Check className="w-3.5 h-3.5" /> : bad ? <PauseCircle className="w-3.5 h-3.5" /> : i === now ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CircleDot className="w-3.5 h-3.5" />} {label}{bad ? ` · ${bad}` : ''}
          </span>
        </span>
      ))}
    </div>
  )
}

/** The compiled graph's node states, from the runtime's own events. */
export function runState(events) {
  const run = {}
  for (const e of events) {
    if (e.type !== 'node') continue
    const id = (e.node || '').split('#')[0]
    if (!id) continue
    if (e.event === 'node_start') run[id] = { state: 'running', active: 1, instances: [] }
    else if (e.event === 'node_end') run[id] = { state: 'done', active: 0, instances: [] }
    else if (e.event === 'cache_hit') run[id] = { state: 'cached', active: 0, instances: [], note: 'served from the cache' }
    else if (e.event === 'node_stub') run[id] = { state: 'stubbed', active: 0, instances: [], note: e.reason }
    else if (e.event === 'error') run[id] = { state: 'error', active: 0, instances: [], note: e.message }
  }
  return run
}

export function Optimization({ events }) {
  const compiled = [...events].reverse().find((e) => e.type === 'compiled')
  const policy = [...events].reverse().find((e) => e.type === 'policy')
  const run = useMemo(() => runState(events), [events])
  if (!compiled) {
    const tracing = events.filter((e) => e.type === 'call' && e.arm === 'compile')
    if (!events.some((e) => e.type === 'phase')) return null
    return (
      <Card eyebrow="3 · Your agent, compiled to SIR" title="Tracing each step with real calls…">
        <div className="flex flex-col gap-1 text-[12.5px] font-mono">
          {tracing.map((c, i) => <div key={i}><span className="text-[var(--ok)]">traced</span> <span className="text-[var(--text)]">{c.step_label}</span> · {c.latency_s?.toFixed(1)} s · {og(c.billed_0g, 6)}{c.status === 'replayed' ? ' · replayed, free' : ''}</div>)}
          <div className="text-[var(--faint)] animate-pulse">tracing the next step…</div>
        </div>
      </Card>
    )
  }
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span className="eyebrow">3 · Your agent, compiled to SIR and optimized</span>
        <span className="text-[12.5px] text-[var(--muted)]">{compiled.reused ? 'compiled earlier on this server: same app, same SIR' : 'compiled now'} · passes applied: <span className="text-[var(--accent)]">{compiled.applied.join(', ')}</span></span>
        <span className="ml-auto"><EffectLegend /></span>
      </div>
      {compiled.before && <Compare before={compiled.before} after={compiled.after} passes={compiled.passes || []} asWritten={compiled.order} height={280} />}
      <div className="grid xl:grid-cols-[1.4fr_1fr] gap-4">
        <div className="panel overflow-hidden">
          <div className="px-4 h-11 flex items-center border-b border-[var(--line)] text-[13.5px] font-semibold text-[var(--text-strong)]">The optimized graph, running on your request</div>
          <div className="relative h-[300px]"><SirGraph graph={compiled.after} run={run} runMode showCriticalPath={false} interactive={false} /></div>
        </div>
        <Card eyebrow="Per-step call changes" title="Approved by the firewall on tuning requests">
          {policy && Object.keys(policy.steps).length ? (
            <div className="flex flex-col gap-2 text-[12.5px]">
              {Object.entries(policy.steps).map(([k, v]) => (
                <div key={k} className="flex flex-wrap gap-1.5 items-center">
                  <span className="text-[var(--text)]">{v.label || k}</span>
                  {v.techniques.map((t) => <span key={t} className="px-1.5 py-[1px] rounded-[4px] bg-[var(--accent)]/15 text-[var(--accent)] text-[11px]">{t.startsWith('route:') ? `→ ${t.slice(6)}` : t.replace('_', ' ')}</span>)}
                </div>
              ))}
              {policy.held_out && (
                <div className="mt-2 pt-2 border-t border-[var(--line)] flex items-center gap-2 text-[12px] text-[var(--muted)]">
                  held-out verdict <Decision decision={policy.held_out.approve ? 'APPROVE' : 'BLOCK'} /> from {policy.report}
                </div>
              )}
            </div>
          ) : <p className="text-[12.5px] text-[var(--ghost)]">none</p>}
        </Card>
      </div>
    </section>
  )
}

export function Breakdown({ q }) {
  const b = q.breakdown
  const custom = q.spec?.agent === 'custom'
  const fee = Math.round((b.fee_rate ?? 0.5) * 100)
  const subtotal = b.subtotal ?? ((b.expected || 0) + (b.fee || 0))
  const atMinimum = (q.pay || 0) > subtotal + 1e14  // more than rounding: the minimum charge applies
  const line = (k, v, why, strong) => ({ k, v, why, strong })
  const rows = [
    line('Your agent', custom ? `${b.steps} steps` : `${b.steps} steps · ${b.model_steps} call the model`,
      custom ? 'found by reading your code: every step of its graph' : 'supplier check: load policy, quotes, reviews, certificates, decide, place order'),
    line('Price of one model call', og(n18(b.per_call), 6), b.per_call_source),
    line('1 · Compile your agent to SIR', b.compiled ? og(0, 6) : og(n18(b.compile), 6),
      b.compiled ? 'already compiled: its trace is recorded and replayed at no cost'
        : custom ? `traced once, step by step, to learn what each step reads and writes: ${b.steps} calls × ${og(n18(b.per_call), 6)}`
          : 'traced once, step by step: the costliest measured run of this agent as written'),
    line('2 · Run your optimized agent', og(n18(b.run_max), 6),
      custom ? `once, on your input: ${b.steps} calls × ${og(n18(b.per_call), 6)}`
        : `once, on your request: the costliest of ${b.runs_measured} measured optimized runs (${b.calls_per_run} calls, mean ${og(n18(b.run_mean), 6)})`),
    line('Compute for your agent', og(n18(b.expected), 6), '1 + 2: what 0G bills for your job, at most', true),
    line(`Optimizer's fee (${fee}%)`, og(n18(b.fee), 6), `${fee}% of the compute: what the optimizer earns for the work`),
    line('Subtotal', og(n18(subtotal), 6), 'compute + fee', true),
  ]
  return (
    <table className="w-full text-[12.5px]">
      <tbody>
        {rows.map((r) => (
          <tr key={r.k} className={`border-b border-[var(--line)] ${r.strong ? 'bg-white/[.02]' : ''}`}>
            <td className={`py-2 pr-4 whitespace-nowrap ${r.strong ? 'text-[var(--text-strong)] font-medium' : 'text-[var(--text)]'}`}>{r.k}</td>
            <td className="py-2 font-mono text-right text-[var(--text-strong)] whitespace-nowrap">{r.v}</td>
            <td className="py-2 pl-5 text-[var(--faint)]">{r.why}</td>
          </tr>
        ))}
        <tr>
          <td className="pt-3 font-semibold text-[var(--text-strong)]">You pay</td>
          <td className="pt-3 font-mono text-right text-[18px] text-[var(--accent)] whitespace-nowrap">{og(n18(q.pay), 4)}</td>
          <td className="pt-3 pl-5 text-[var(--faint)]">{atMinimum ? 'the minimum charge: 0G accepts no model call against less, so the subtotal is raised to it; what the job does not use stays as compute credit' : 'the subtotal, rounded up to 0.0001 0G'} · every call is billed exactly and shown on the job page · valid until {when(q.expires)}</td>
        </tr>
      </tbody>
    </table>
  )
}

export function Ledger({ rows }) {
  const charged = rows.reduce((a, r) => a + (r.charged_0g || 0), 0)
  return (
    <table className="w-full text-[11.5px]">
      <thead>
        <tr className="text-left text-[var(--faint)]">
          <th className="font-normal pb-1.5">time</th><th className="font-normal">model</th><th className="font-normal">0G request id</th>
          <th className="font-normal">state</th><th className="font-normal text-right">reserved first</th><th className="font-normal text-right">0G charged</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.hold} className="border-t border-[var(--line)]">
            <td className="py-1.5 font-mono text-[var(--muted)]">{when(r.at)}</td>
            <td className="font-mono text-[var(--muted)]">{r.model}</td>
            <td className="font-mono text-[var(--muted)]" title={r.request_id || ''}>{short(r.request_id, 10)}</td>
            <td className={r.state === 'settled' ? 'text-[var(--ok)]' : 'text-[var(--warn)]'}>{r.state === 'settled' ? 'billed' : r.state}</td>
            <td className="text-right font-mono text-[var(--faint)]">{og(r.reserved_0g, 6)}</td>
            <td className="text-right font-mono text-[var(--text-strong)]">{og(r.charged_0g, 7)}</td>
          </tr>
        ))}
        {!rows.length && <tr><td colSpan={6} className="py-2 text-[var(--ghost)]">no calls yet</td></tr>}
      </tbody>
      {rows.length > 0 && (
        <tfoot><tr className="border-t border-[var(--line-strong)]"><td colSpan={5} className="pt-2 text-[var(--muted)]">{rows.length} calls, from the spend ledger: each reserved at its worst case, then settled at 0G's charge</td>
          <td className="pt-2 text-right font-mono text-[var(--text-strong)]">{og(charged, 7)}</td></tr></tfoot>
      )}
    </table>
  )
}

export function Calls({ events }) {
  const calls = events.filter((e) => e.type === 'call')
  const phases = events.filter((e) => e.type === 'phase')
  const billed = calls.reduce((a, c) => a + (c.billed_0g || 0), 0)
  return (
    <div>
      <div className="flex flex-col gap-1 mb-3 text-[12.5px]">
        {phases.map((p, i) => <div key={i} className="text-[var(--muted)]"><span className="font-mono text-[var(--faint)] mr-2">{p.t.toFixed(1)}s</span>{p.text}</div>)}
      </div>
      <table className="w-full text-[11.5px]">
        <thead>
          <tr className="text-left text-[var(--faint)]">
            <th className="font-normal pb-1.5">stage</th><th className="font-normal">step</th><th className="font-normal">model</th>
            <th className="font-normal">0G request id</th><th className="font-normal text-right">time</th><th className="font-normal text-right">0G charged</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c, i) => (
            <motion.tr key={c.request_id || i} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="border-t border-[var(--line)]">
              <td className="py-1.5 text-[var(--muted)]">{c.arm}</td>
              <td className="font-mono text-[var(--text)] max-w-[200px] truncate" title={c.step_label}>{c.step_label}</td>
              <td className="font-mono text-[var(--muted)]">{c.tag}</td>
              <td className="font-mono text-[var(--muted)]" title={c.request_id}>{short(c.request_id, 10)}</td>
              <td className="text-right font-mono">{c.latency_s?.toFixed(1)} s</td>
              <td className="text-right font-mono text-[var(--text-strong)]">{c.status === 'ok' ? og(c.billed_0g, 7) : c.status === 'replayed' ? <span className="text-[var(--faint)]" title="paid in the attempt before the pause">already paid</span> : <span className="text-[var(--bad)]">{c.status}</span>}</td>
            </motion.tr>
          ))}
          {!calls.length && <tr><td colSpan={6} className="py-2 text-[var(--ghost)]">waiting for the first call…</td></tr>}
        </tbody>
        {calls.length > 0 && (
          <tfoot><tr className="border-t border-[var(--line-strong)]"><td colSpan={5} className="pt-2 text-[var(--muted)]">{calls.length} calls, billed by 0G to the agent's account</td>
            <td className="pt-2 text-right font-mono text-[var(--text-strong)]">{og(billed, 7)}</td></tr></tfoot>
        )}
      </table>
    </div>
  )
}

export function Balances({ q }) {
  const order = [['at_quote', 'at the quote'], ['at_payment', 'after the payment'], ['after_job', 'after the job']]
  const have = order.filter(([k]) => q.balances?.[k])
  if (!have.length) return null
  return (
    <table className="w-full text-[12.5px]">
      <thead><tr className="text-left text-[var(--faint)]"><th className="font-normal pb-1.5">the agent account</th><th className="font-normal text-right">Payment Layer (contract)</th>
        <th className="font-normal text-right">0G admits against</th><th className="font-normal text-right">spent, settled</th><th className="font-normal text-right">read at</th></tr></thead>
      <tbody>
        {have.map(([k, label]) => {
          const b = q.balances[k]
          return (
            <tr key={k} className="border-t border-[var(--line)]">
              <td className="py-1.5 text-[var(--text)]">{label}</td>
              <td className="text-right font-mono">{b.vault_error ? <span className="text-[var(--bad)]" title={b.vault_error}>unread</span> : og(b.vault_0g)}</td>
              <td className="text-right font-mono">{b.router_error ? <span className="text-[var(--bad)]" title={b.router_error}>unread</span> : og(b.admission_0g)}</td>
              <td className="text-right font-mono">{og(b.spent_0g)}</td>
              <td className="text-right font-mono text-[var(--faint)]">{when(b.at)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** The generated file, with each optimization's lines marked: the optimized graph, written back as code. */
export function CodeView({ code }) {
  const MARKS = [
    [/parallelize|waits for all of these/, 'parallelize', 'var(--accent)'],
    [/memoiz|served from the cache/, 'memoize', 'var(--info)'],
    [/^\s+'s-[0-9a-f]{8}'|STEP_CHANGES = \{|refused by the firewall/, 'call policy', 'var(--ok)'],
    [/protected\(|irreversible/, 'protected', 'var(--bad)'],
  ]
  const lines = code.split('\n')
  return (
    <div className="pt-4 border-t border-[var(--line)]">
      <div className="flex flex-wrap items-center gap-3 mb-2 text-[12px]">
        <span className="text-[13px] font-semibold text-[var(--text-strong)]">optimized_agent.py: the optimized SIR, written back as your agent's code</span>
        {MARKS.map(([, k, c]) => <span key={k} className="inline-flex items-center gap-1.5 text-[var(--muted)]"><span className="w-2 h-2 rounded-full" style={{ background: c }} />{k}</span>)}
      </div>
      <pre className="max-h-[520px] overflow-auto rounded-[8px] bg-black/40 border border-[var(--line)] py-3 text-[11.5px] leading-[1.55]">
        {lines.map((ln, i) => {
          const m = MARKS.find(([re]) => re.test(ln))
          return (
            <div key={i} className="flex" style={m ? { background: 'rgba(255,255,255,.035)', boxShadow: `inset 2px 0 0 ${m[2]}` } : undefined}>
              <span className="w-10 shrink-0 pr-3 text-right text-[var(--ghost)] select-none">{i + 1}</span>
              <span className="whitespace-pre text-[var(--text)]">{ln}</span>
            </div>
          )
        })}
      </pre>
    </div>
  )
}

export function Result({ job, q, quoteId }) {
  const r = job?.result
  if (!r) return null
  const paid = n18(q.pay)
  return (
    <div className="flex flex-col gap-5">
      <div className="grid md:grid-cols-4 gap-5 text-[13px]">
        {r.custom ? (
          <div><div className="text-[var(--faint)]">your agent's output</div>
            <div className="flex items-center gap-1.5 font-mono text-[var(--text-strong)] truncate" title={JSON.stringify(r.answer)}>{r.passed ? <Check className="w-4 h-4 text-[var(--ok)]" /> : <X className="w-4 h-4 text-[var(--bad)]" />}{Object.keys(r.answer || {}).slice(0, 4).join(', ') || 'no output'}</div>
            <div className="text-[var(--faint)]">{r.crashed ? `crashed: ${r.crashed}` : 'ran to the end on your input'}</div></div>
        ) : (
        <div><div className="text-[var(--faint)]">answer on your request</div>
          <div className="flex items-center gap-1.5 font-mono text-[var(--text-strong)]">{r.passed ? <Check className="w-4 h-4 text-[var(--ok)]" /> : <X className="w-4 h-4 text-[var(--bad)]" />}{r.answer?.supplier_id || 'no order'}</div>
          <div className="text-[var(--faint)]">{r.rfq} · {r.passed ? (r.answer?.supplier_id ? 'right supplier' : 'right: no supplier qualifies') : 'wrong'}</div></div>
        )}
        <div><div className="text-[var(--faint)]">actual compute cost</div><div className="font-mono text-[18px] text-[var(--accent)]">{og(r.billed_0g, 6)}</div>
          <div className="text-[var(--faint)]">of {og(paid, 4)} paid; the rest stays as the agent's credit</div></div>
        <div><div className="text-[var(--faint)]">time · calls at once</div><div className="font-mono text-[18px] text-[var(--text-strong)]">{r.wall_s?.toFixed(1)} s · {r.peak_overlap}</div>
          <div className="text-[var(--faint)]">{r.calls.length} calls{r.compiled_now ? ', compile included' : ''}</div></div>
        <div><div className="text-[var(--faint)]">{r.custom ? "irreversible and external steps" : "the order"}</div><div className="flex items-center gap-1.5 text-[var(--text-strong)]"><Ban className="w-4 h-4 text-[var(--bad)]" />{r.custom ? `flagged: ${(r.flagged || []).join(', ') || 'none'}` : 'not placed'}</div>
          <div className="text-[var(--faint)]">{r.order_note}</div></div>
      </div>
      {r.package && (
        <div className="flex flex-wrap items-center gap-4 pt-4 border-t border-[var(--line)]">
          <a href={`/api/pay/${quoteId}/package`} className="inline-flex items-center gap-2 h-10 px-5 rounded-[8px] bg-[var(--accent)] text-black text-[14px] font-semibold">
            <Download className="w-4 h-4" /> Download your optimized agent (.zip)
          </a>
          <span className="text-[12.5px] text-[var(--muted)]"><span className="font-mono">optimized_agent.py</span>, generated from the optimized SIR and the approved call policy, beside your original code, both graphs, the policy and this run's evidence. Run it with <span className="font-mono">python optimized_agent.py</span>.</span>
        </div>
      )}
      {r.optimized_code && <CodeView code={r.optimized_code} />}
    </div>
  )
}

export function History({ data, go }) {
  const rows = data?.deposits || []
  return (
    <Card eyebrow="Every payment to the agent's account" title="Transaction history"
      right={data && <span className="text-[11.5px] text-[var(--faint)]">account <Tx hash={data.account} kind="address" /> · Payment Layer <Tx hash={data.payment_layer} kind="address" /></span>}>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-left text-[var(--faint)]">
            <th className="font-normal pb-1.5">block</th><th className="font-normal">transaction</th><th className="font-normal">from</th><th className="font-normal">who</th>
            <th className="font-normal text-right">paid in</th><th className="font-normal pl-4">quote</th><th className="font-normal">job</th>
            <th className="font-normal text-right">compute spent</th><th className="font-normal text-right">calls</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.tx + d.log_index} className="border-t border-[var(--line)]">
              <td className="py-1.5 font-mono text-[var(--muted)]">{d.block}</td>
              <td><Tx hash={d.tx} /></td>
              <td><Tx hash={d.payer} kind="address" /></td>
              <td><span className={`px-1.5 py-[1px] rounded-[4px] text-[11px] ${d.note === 'customer' ? 'bg-[var(--ok)]/15 text-[var(--ok)]' : 'bg-white/[.06] text-[var(--muted)]'}`}>{d.note === 'team wallet' ? 'team (test, not revenue)' : d.note}</span></td>
              <td className="text-right font-mono text-[var(--text-strong)]">{og(d.amount_0g, 4)}</td>
              <td className="pl-4 font-mono text-[var(--muted)]">{d.quote ? <button onClick={() => go('pay', d.quote.id)} className="text-[var(--accent)] hover:underline">{d.quote.id} · {d.quote.status}</button> : 'no quote: kept as credit'}</td>
              <td className="font-mono text-[var(--muted)]">{d.job ? `${d.job.id} · ${d.job.status}` : '—'}</td>
              <td className="text-right font-mono">{og(d.job?.billed_0g)}</td>
              <td className="text-right font-mono">{d.job?.calls ?? '—'}</td>
            </tr>
          ))}
          {!rows.length && <tr><td colSpan={9} className="py-3 text-[var(--ghost)]">No payment to the agent's account yet.</td></tr>}
        </tbody>
      </table>
    </Card>
  )
}

export function Recent({ quotes, current, go }) {
  if (!quotes?.length) return null
  return (
    <Card eyebrow="Your payments" title="Open any of them: everything is kept on the server">
      <div className="flex flex-wrap gap-2">
        {quotes.map((q) => (
          <button key={q.id} onClick={() => go('pay', q.id)}
            className={`px-3 py-1.5 rounded-[8px] border text-[12px] font-mono ${q.id === current ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-[var(--line-strong)] text-[var(--muted)] hover:text-[var(--text)]'}`}>
            {q.id} · {og(n18(q.pay), 3)} · {q.status}{q.spec?.task ? ` · ${q.spec.task.replace('supplier-', '')}` : ''}
          </button>
        ))}
      </div>
    </Card>
  )
}
