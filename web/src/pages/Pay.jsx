import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { ArrowRight, Ban, Check, CircleDot, Download, ExternalLink, Loader2, PauseCircle, Play, Wallet, X } from 'lucide-react'
import { Card, Decision, PageTitle } from '../components/bits.jsx'
import { Compare } from '../sir/Compare.tsx'
import { SirGraph } from '../sir/graph/SirGraph.tsx'
import { EffectLegend } from '../sir/graph/EffectLegend.tsx'
import { useView } from '../store.js'

const CHAIN = { id: 16661, hex: '0x4115', rpc: 'https://evmrpc.0g.ai', explorer: 'https://chainscan.0g.ai' }
const og = (v, d = 6) => (v == null ? '—' : `${Number(v).toFixed(d)} 0G`)
const n18 = (v) => (v == null ? null : Number(v) / 1e18)
const short = (h, n = 6) => (h ? `${h.slice(0, n + 2)}…${h.slice(-4)}` : '—')
const when = (t) => (t ? new Date(t * 1000).toLocaleTimeString() : '—')

async function call(url, body) {
  const r = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)
  const d = await r.json().catch(() => null)
  if (!r.ok) throw new Error(d?.error || `the API answered ${r.status}`)
  return d
}

function Tx({ hash, kind = 'tx', explorer = CHAIN.explorer }) {
  if (!hash) return <span className="text-[var(--ghost)]">—</span>
  return (
    <a href={`${explorer}/${kind}/${hash}`} target="_blank" rel="noreferrer" title={hash}
      className="inline-flex items-center gap-1 font-mono text-[var(--accent)] hover:underline">
      {short(hash, kind === 'tx' ? 8 : 6)} <ExternalLink className="w-3 h-3" />
    </a>
  )
}

/** The wallet in the browser (MetaMask or any EIP-1193 wallet), switched to 0G mainnet. */
async function wallet() {
  const eth = window.ethereum
  if (!eth) throw new Error('no wallet in this browser: install MetaMask, or pay from any wallet and paste the transaction hash below')
  const [from] = await eth.request({ method: 'eth_requestAccounts' })
  try {
    await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: CHAIN.hex }] })
  } catch (e) {
    if (e.code !== 4902) throw e
    await eth.request({ method: 'wallet_addEthereumChain', params: [{ chainId: CHAIN.hex, chainName: '0G Mainnet', rpcUrls: [CHAIN.rpc],
      nativeCurrency: { name: '0G', symbol: '0G', decimals: 18 }, blockExplorerUrls: [CHAIN.explorer] }] })
  }
  return { eth, from }
}

function Flow() {
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

function Account({ account }) {
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
function stagesOf(q, events, tx) {
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

function Stages({ q, events, tx }) {
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
function runState(events) {
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

function Optimization({ events }) {
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

function Breakdown({ q }) {
  const b = q.breakdown
  const rows = [
    ['One optimized run, at most', og(n18(b.run_max)), `the costliest of ${b.runs_measured} measured runs (mean ${og(n18(b.run_mean))}), from ${b.from_report}`],
    ['Compiling the app, once', og(n18(b.compile)), b.compile ? 'tracing it with real calls, paid by the agent too' : 'already compiled: nothing to pay'],
    ['Room for two calls in flight', og(n18(b.room)), `each reserved at its worst case (${og(n18(b.worst_call))}) before it is sent`],
    ['Already free in the account', `− ${og(n18(b.free_now))}`, 'credit left from earlier payments'],
    ['0G counts 80% of the Payment Layer', `÷ ${b.vault_ratio}`, 'so the deposit is a little more than the need'],
  ]
  return (
    <table className="w-full text-[12.5px]">
      <tbody>
        {rows.map(([k, v, why]) => (
          <tr key={k} className="border-b border-[var(--line)]">
            <td className="py-1.5 text-[var(--text)]">{k}</td>
            <td className="py-1.5 font-mono text-right text-[var(--text-strong)] whitespace-nowrap">{v}</td>
            <td className="py-1.5 pl-4 text-[var(--faint)]">{why}</td>
          </tr>
        ))}
        <tr>
          <td className="pt-2.5 font-semibold text-[var(--text-strong)]">You pay</td>
          <td className="pt-2.5 font-mono text-right text-[18px] text-[var(--accent)] whitespace-nowrap">{og(n18(q.pay), 4)}</td>
          <td className="pt-2.5 pl-4 text-[var(--faint)]">What the job does not spend stays as the agent's credit. Quote expires {when(q.expires)}.</td>
        </tr>
      </tbody>
    </table>
  )
}

/** The spend ledger's own record of a job's calls: reserved, charged, request id. Kept across restarts. */
function Ledger({ rows }) {
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

function Calls({ events }) {
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
              <td className="text-right font-mono text-[var(--text-strong)]">{c.status === 'ok' ? og(c.billed_0g, 7) : <span className="text-[var(--bad)]">{c.status}</span>}</td>
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

function Balances({ q }) {
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
function CodeView({ code }) {
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

function Result({ job, q, quoteId }) {
  const r = job?.result
  if (!r) return null
  const paid = n18(q.pay)
  return (
    <div className="flex flex-col gap-5">
      <div className="grid md:grid-cols-4 gap-5 text-[13px]">
        <div><div className="text-[var(--faint)]">answer on your request</div>
          <div className="flex items-center gap-1.5 font-mono text-[var(--text-strong)]">{r.passed ? <Check className="w-4 h-4 text-[var(--ok)]" /> : <X className="w-4 h-4 text-[var(--bad)]" />}{r.answer?.supplier_id || 'no order'}</div>
          <div className="text-[var(--faint)]">{r.rfq} · {r.passed ? 'right supplier' : 'wrong'}</div></div>
        <div><div className="text-[var(--faint)]">actual compute cost</div><div className="font-mono text-[18px] text-[var(--accent)]">{og(r.billed_0g, 6)}</div>
          <div className="text-[var(--faint)]">of {og(paid, 4)} paid; the rest stays as the agent's credit</div></div>
        <div><div className="text-[var(--faint)]">time · calls at once</div><div className="font-mono text-[18px] text-[var(--text-strong)]">{r.wall_s?.toFixed(1)} s · {r.peak_overlap}</div>
          <div className="text-[var(--faint)]">{r.calls.length} calls{r.compiled_now ? ', compile included' : ''}</div></div>
        <div><div className="text-[var(--faint)]">the order</div><div className="flex items-center gap-1.5 text-[var(--text-strong)]"><Ban className="w-4 h-4 text-[var(--bad)]" />not placed</div>
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

function History({ data, go }) {
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

function Recent({ quotes, current, go }) {
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

export default function Pay() {
  const { resultId: quoteId, go } = useView()
  const tasks = useQuery({ queryKey: ['live-tasks', 'supplier'], queryFn: () => call('/api/live/tasks?agent=supplier'), staleTime: Infinity })
  const agent = useQuery({ queryKey: ['agent'], queryFn: () => call('/api/agent'), refetchInterval: 10000 })
  const history = useQuery({ queryKey: ['pay-history'], queryFn: () => call('/api/pay/history'), refetchInterval: 8000 })
  const [payer, setPayer] = useState('')
  const [task, setTask] = useState('')
  const [pasted, setPasted] = useState('')
  const [confirm, setConfirm] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const txKey = quoteId ? `synapse0g-tx-${quoteId}` : null
  const [tx, setTxState] = useState('')
  useEffect(() => { setTxState(txKey ? localStorage.getItem(txKey) || '' : ''); setConfirm(null); setErr(null) }, [txKey])
  const setTx = (h) => { setTxState(h); if (txKey) localStorage.setItem(txKey, h) }

  const view = useQuery({ queryKey: ['pay', quoteId], queryFn: () => call(`/api/pay/${quoteId}`), enabled: !!quoteId,
    refetchInterval: (q) => (['done', 'expired'].includes(q.state.data?.quote?.status) ? false : 1500) })
  const q = view.data?.quote
  const events = view.data?.progress?.events || []
  const paused = [...events].reverse().find((e) => e.type === 'paused')
  const failed = [...events].reverse().find((e) => e.type === 'failed')

  // after the payment is sent: the server checks it on chain until it is mined and matched (the watcher does too)
  useEffect(() => {
    if (!quoteId || !tx || q?.tx || confirm?.state === 'paid') return
    let stop = false
    const tick = async () => {
      try {
        const c = await call('/api/pay/confirm', { quote: quoteId, tx })
        setConfirm(c)
        if (c.state === 'pending' && !stop) setTimeout(tick, 2500)
      } catch (e) { setErr(e) }
    }
    tick()
    return () => { stop = true }
  }, [quoteId, tx, q?.tx])

  const connect = async () => { setErr(null); try { setPayer((await wallet()).from) } catch (e) { setErr(e) } }
  const getQuote = async () => {
    setErr(null); setBusy(true)
    try { const r = await call('/api/pay/quote', { payer, task: task || undefined }); go('pay', r.id) } catch (e) { setErr(e) }
    setBusy(false)
  }
  const pay = async () => {
    setErr(null); setBusy(true)
    try {
      const { eth, from } = await wallet()
      if (from.toLowerCase() !== q.payer) throw new Error(`the wallet is ${from}; this quote is for ${q.payer}`)
      const t = view.data.transaction
      const hash = await eth.request({ method: 'eth_sendTransaction', params: [{ from, to: t.to, data: t.data, value: '0x' + BigInt(t.value_wei).toString(16) }] })
      setTx(hash)
    } catch (e) { setErr(e) }
    setBusy(false)
  }
  const resume = async () => {
    setErr(null)
    try { await call('/api/pay/resume', { quote: quoteId }); view.refetch() } catch (e) { setErr(e) }
  }
  const tasksList = useMemo(() => tasks.data?.tasks || [], [tasks.data])

  return (
    <div className="flex flex-col gap-6">
      <PageTitle eyebrow="Paid agent · 0G mainnet" title="Pay synapse0g, and watch your payment optimize your agent">
        A customer pays straight into the agent's 0G compute account with <span className="font-mono">depositFor</span> on 0G's Payment Layer.
        synapse0g checks the payment on chain and starts at once: your agent is compiled to SIR, optimized, run on your request, and returned to you.
        Every model call is paid from that balance, with 0G's request id and charge. This page keeps its place: Back and refresh bring it back.
      </PageTitle>
      <Flow />
      <Account account={agent.data?.account} />
      <Recent quotes={history.data?.quotes} current={quoteId} go={go} />

      {!quoteId && (
        <Card eyebrow="1 · Quote" title="What the job needs, before you pay">
          <div className="flex flex-wrap items-center gap-3">
            <button onClick={connect} className="inline-flex items-center gap-2 h-9 px-4 rounded-[8px] border border-[var(--line-strong)] text-[13px] text-[var(--text-strong)]">
              <Wallet className="w-4 h-4" /> {payer ? short(payer) : 'Connect wallet'}
            </button>
            <input value={payer} onChange={(e) => setPayer(e.target.value.trim())} placeholder="or paste the paying wallet, 0x…"
              className="h-9 w-[380px] px-3 rounded-[8px] bg-[var(--bg)] border border-[var(--line-strong)] font-mono text-[12.5px] text-[var(--text-strong)]" />
            <select value={task} onChange={(e) => setTask(e.target.value)}
              className="h-9 min-w-[300px] px-3 rounded-[8px] bg-[var(--bg)] border border-[var(--line-strong)] text-[13px] text-[var(--text-strong)]">
              <option value="">supplier check · first held-out request</option>
              {tasksList.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select>
            <button onClick={getQuote} disabled={!payer || busy}
              className="h-9 px-5 rounded-[8px] bg-[var(--accent)] text-black text-[13.5px] font-semibold disabled:opacity-40">{busy ? 'Pricing…' : 'Get quote'}</button>
          </div>
        </Card>
      )}

      {err && <div className="panel p-4 text-[13px] text-[var(--bad)]">{err.message}</div>}
      {quoteId && view.error && <div className="panel p-4 text-[13px] text-[var(--bad)]">{view.error.message}</div>}

      {q && (
        <>
          <div className="panel px-5 py-4 flex flex-wrap items-center gap-4">
            <span className="font-mono text-[13px] text-[var(--text-strong)]">{q.id}</span>
            <Stages q={q} events={events} tx={tx} />
            <button onClick={() => go('pay')} className="ml-auto h-8 px-3 rounded-[6px] border border-[var(--line-strong)] text-[12px] text-[var(--muted)]">New quote</button>
          </div>

          {(paused || q.status === 'waiting for funds') && (
            <div className="panel p-4 border-l-2 !border-l-[var(--warn)] flex flex-wrap items-center gap-4 text-[13px]">
              <PauseCircle className="w-5 h-5 text-[var(--warn)]" />
              <div className="flex-1 min-w-[300px]">
                <div className="text-[var(--text-strong)] font-medium">Paused before the next call: your payment is safe in the agent's account</div>
                <div className="text-[var(--muted)]">{paused?.reason || q.note}</div>
              </div>
              <button onClick={resume} className="inline-flex items-center gap-2 h-9 px-4 rounded-[8px] bg-[var(--accent)] text-black text-[13px] font-semibold">
                <Play className="w-4 h-4" /> Resume on the credit already paid
              </button>
            </div>
          )}
          {failed && q.status === 'failed' && <div className="panel p-4 text-[13px] text-[var(--bad)]">The job failed: {failed.error}</div>}

          <Card eyebrow="1 · Quote" title={`${og(n18(q.pay), 4)} for one optimized run of your agent`}>
            <Breakdown q={q} />
          </Card>

          <Card eyebrow="2 · Payment" title="One transaction from your wallet, straight to the agent's compute account">
            <div className="grid md:grid-cols-[1fr_auto] gap-6 items-start text-[12.5px]">
              <table className="w-full">
                <tbody>
                  {[['to (0G Payment Layer)', <Tx key="to" hash={view.data.transaction?.to} kind="address" />],
                    ['call', <span key="c" className="font-mono">depositFor({short('0x' + (view.data.transaction?.data || '').slice(-40), 6)}) <span className="text-[var(--faint)]">the agent's account</span></span>],
                    ['value', <span key="v" className="font-mono text-[var(--text-strong)]">{og(n18(q.pay), 6)}</span>],
                    ['from', <Tx key="f" hash={q.payer} kind="address" />],
                    ['transaction', q.tx || tx ? <Tx key="t" hash={q.tx || tx} /> : <span key="t" className="text-[var(--ghost)]">not sent yet</span>],
                    ['status', <span key="s" className={q.tx ? 'text-[var(--ok)]' : 'text-[var(--muted)]'}>{q.tx ? 'confirmed on chain and matched to this quote' : confirm ? `${confirm.state}${confirm.why ? `: ${confirm.why}` : ''}` : q.status}</span>]].map(([k, v]) => (
                    <tr key={k} className="border-b border-[var(--line)]"><td className="py-1.5 w-[190px] text-[var(--faint)]">{k}</td><td>{v}</td></tr>
                  ))}
                </tbody>
              </table>
              {q.status === 'awaiting payment' && (
                <div className="flex flex-col gap-2 w-[300px]">
                  <button onClick={pay} disabled={busy || !!tx}
                    className="inline-flex items-center justify-center gap-2 h-10 px-5 rounded-[8px] bg-[var(--accent)] text-black text-[14px] font-semibold disabled:opacity-40">
                    <Wallet className="w-4 h-4" /> Pay {og(n18(q.pay), 4)} with wallet
                  </button>
                  <div className="flex gap-2">
                    <input value={pasted} onChange={(e) => setPasted(e.target.value.trim())} placeholder="paid elsewhere? tx hash"
                      className="flex-1 h-8 px-2 rounded-[6px] bg-[var(--bg)] border border-[var(--line-strong)] font-mono text-[11.5px]" />
                    <button onClick={() => setTx(pasted)} disabled={!pasted || !!tx} className="h-8 px-3 rounded-[6px] border border-[var(--line-strong)] text-[12px] disabled:opacity-40">Check</button>
                  </div>
                </div>
              )}
            </div>
          </Card>

          <Optimization events={events} />

          {q.job_id && (
            <Card eyebrow="4 · Every model call, paid from your deposit" title={`${q.job_id} · ${q.status}`}>
              {events.some((e) => e.type === 'call') ? <Calls events={events} /> : <Ledger rows={view.data?.ledger || []} />}
            </Card>
          )}

          {view.data?.job?.result && (
            <div className="panel p-6 border-l-2 !border-l-[var(--accent)]">
              <div className="eyebrow mb-4">5 · Result, and your optimized agent</div>
              <Result job={view.data.job} q={q} quoteId={q.id} />
            </div>
          )}

          {Object.keys(q.balances || {}).length > 0 && (
            <Card eyebrow="6 · Evidence" title="The agent's account at each step, read from the contract and from 0G">
              <Balances q={q} />
            </Card>
          )}
        </>
      )}

      <History data={history.data} go={go} />
    </div>
  )
}
