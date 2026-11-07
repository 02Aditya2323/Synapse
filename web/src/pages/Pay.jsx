import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, ChevronDown, Code2, FileUp, Sparkles, Wallet, Zap } from 'lucide-react'
import { useView } from '../store.js'
import { Breakdown, Stages, Tx, call, n18, og, short, wallet } from './paykit.jsx'

const SAMPLE_INPUT = (rfq) => JSON.stringify({ rfq }, null, 1)

function AccountChip() {
  const agent = useQuery({ queryKey: ['agent'], queryFn: () => call('/api/agent'), refetchInterval: 10000 })
  const a = agent.data?.account
  if (!a) return null
  return (
    <div className="panel px-4 py-2.5 flex items-center gap-4 text-[12px]">
      <span className="flex items-center gap-1.5 text-[var(--muted)]"><span className={`w-1.5 h-1.5 rounded-full ${a.connected ? 'bg-[var(--ok)]' : 'bg-[var(--bad)]'}`} />optimizer's 0G account</span>
      {a.connected ? (
        <>
          {a.stale && <span className="text-[var(--warn)]" title={a.why}>0G unreachable, last read {a.stale_s}s ago</span>}
          <span><span className="text-[var(--faint)]">free </span><span className="font-mono text-[var(--text-strong)]">{og(a.free_0g, 4)}</span></span>
          <span><span className="text-[var(--faint)]">spent </span><span className="font-mono text-[var(--text-strong)]">{og(a.spent_0g, 4)}</span></span>
          {a.key_cap && <span><span className="text-[var(--faint)]">cap </span><span className="font-mono">{og(a.key_cap.cap_0g, 2)}</span></span>}
        </>
      ) : <span className="text-[var(--bad)]">{a.why}</span>}
    </div>
  )
}

function Segmented({ value, onChange, options }) {
  return (
    <div className="inline-flex p-1 rounded-[10px] bg-white/[.04] border border-[var(--line)]">
      {options.map(([k, label, Icon]) => (
        <button key={k} onClick={() => onChange(k)}
          className={`inline-flex items-center gap-2 h-8 px-4 rounded-[7px] text-[13px] transition-colors ${value === k ? 'bg-[var(--accent)] text-black font-semibold' : 'text-[var(--muted)] hover:text-[var(--text)]'}`}>
          <Icon className="w-4 h-4" /> {label}
        </button>
      ))}
    </div>
  )
}

/** Step 1: the agent to optimize: your own code, or one of the example agents. */
function AgentForm({ onQuote }) {
  const [mode, setMode] = useState('code')
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [sample, setSample] = useState('{}')
  const [task, setTask] = useState('')
  const [payer, setPayer] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const file = useRef(null)
  const tasks = useQuery({ queryKey: ['live-tasks', 'supplier'], queryFn: () => call('/api/live/tasks?agent=supplier'), staleTime: Infinity })
  const list = useMemo(() => tasks.data?.tasks || [], [tasks.data])

  const loadExample = async () => {
    setErr(null)
    try {
      const d = await call('/api/example/supplier')
      setCode(d.code); setName('supplier check')
      const t = list.find((x) => x.id === 'supplier-RFQ-011') || list[0]
      if (t) setSample(SAMPLE_INPUT(t.rfq))
    } catch (e) { setErr(e) }
  }
  const upload = async (f) => { if (f) { setCode(await f.text()); setName(f.name.replace(/\.py$/, '')) } }
  const connect = async () => { setErr(null); try { setPayer((await wallet()).from) } catch (e) { setErr(e) } }
  const quote = async () => {
    setErr(null); setBusy(true)
    try {
      let body = { payer, task: task || undefined }
      if (mode === 'code') {
        let parsed
        try { parsed = JSON.parse(sample || '{}') } catch { throw new Error('the sample input is not valid JSON') }
        body = { payer, code, sample: parsed, name: name || undefined }
      }
      onQuote(await call('/api/pay/quote', body))
    } catch (e) { setErr(e) }
    setBusy(false)
  }

  return (
    <div className="panel p-6 flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-[15px] font-semibold text-[var(--text-strong)]">1 · Your agent</div>
        <Segmented value={mode} onChange={setMode} options={[['code', 'Your code', Code2], ['example', 'Example agent', Sparkles]]} />
      </div>

      {mode === 'code' ? (
        <div className="grid lg:grid-cols-[1fr_320px] gap-4">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-[12px]">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="agent name"
                className="h-8 w-[220px] px-3 rounded-[7px] bg-[var(--bg)] border border-[var(--line-strong)] text-[12.5px]" />
              <button onClick={() => file.current?.click()} className="inline-flex items-center gap-1.5 h-8 px-3 rounded-[7px] border border-[var(--line-strong)] text-[var(--muted)] hover:text-[var(--text)]"><FileUp className="w-3.5 h-3.5" /> Upload .py</button>
              <input ref={file} type="file" accept=".py" hidden onChange={(e) => upload(e.target.files?.[0])} />
              <button onClick={loadExample} className="h-8 px-3 rounded-[7px] text-[var(--accent)] hover:underline">load a sample agent</button>
            </div>
            <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false}
              placeholder={'# paste your LangGraph agent\n# a StateGraph at module level, and optionally EFFECTS = {...}'}
              className="h-[280px] p-3 rounded-[8px] bg-black/40 border border-[var(--line-strong)] font-mono text-[12px] leading-[1.5] text-[var(--text)] resize-y" />
          </div>
          <div className="flex flex-col gap-2">
            <div className="text-[12px] text-[var(--muted)] h-8 flex items-center">Sample input (JSON): the state your agent starts from</div>
            <textarea value={sample} onChange={(e) => setSample(e.target.value)} spellCheck={false}
              className="h-[280px] p-3 rounded-[8px] bg-black/40 border border-[var(--line-strong)] font-mono text-[11.5px] leading-[1.5] text-[var(--text)] resize-y" />
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="text-[12.5px] text-[var(--muted)]">Supplier check: a LangGraph purchasing agent. Each request hides a trap the optimized agent must still get right.</div>
          <select value={task} onChange={(e) => setTask(e.target.value)}
            className="h-10 px-3 rounded-[8px] bg-[var(--bg)] border border-[var(--line-strong)] text-[13px] text-[var(--text-strong)]">
            <option value="">{tasks.isLoading ? 'loading…' : 'first held-out request'}</option>
            {list.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 pt-4 border-t border-[var(--line)]">
        <button onClick={connect} className="inline-flex items-center gap-2 h-10 px-4 rounded-[8px] border border-[var(--line-strong)] text-[13px] text-[var(--text-strong)]">
          <Wallet className="w-4 h-4" /> {payer ? short(payer) : 'Connect wallet'}
        </button>
        <input value={payer} onChange={(e) => setPayer(e.target.value.trim())} placeholder="or paste the paying wallet, 0x…"
          className="h-10 w-[360px] px-3 rounded-[8px] bg-[var(--bg)] border border-[var(--line-strong)] font-mono text-[12px]" />
        <button onClick={quote} disabled={!payer || busy || (mode === 'code' && !code.trim())}
          className="ml-auto inline-flex items-center gap-2 h-10 px-6 rounded-[8px] bg-[var(--accent)] text-black text-[14px] font-semibold disabled:opacity-40">
          {busy ? 'Reading your agent…' : 'Get the price'} <ArrowRight className="w-4 h-4" />
        </button>
      </div>
      {err && <div className="text-[13px] text-[var(--bad)]">{err.message}</div>}
    </div>
  )
}

/** Steps 2 and 3: the price, the payment, and the way into the job. */
function QuotePay({ quoteId, go }) {
  const [showWhy, setShowWhy] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [pasted, setPasted] = useState('')
  const txKey = `synapse0g-tx-${quoteId}`
  const [tx, setTxState] = useState(() => localStorage.getItem(txKey) || '')
  const setTx = (h) => { setTxState(h); localStorage.setItem(txKey, h) }
  const view = useQuery({ queryKey: ['pay', quoteId], queryFn: () => call(`/api/pay/${quoteId}`),
    refetchInterval: (q) => (['done', 'expired'].includes(q.state.data?.quote?.status) ? false : 1500) })
  const q = view.data?.quote
  const events = view.data?.progress?.events || []

  useEffect(() => {
    if (!tx || q?.tx || confirm?.state === 'paid') return
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

  if (view.error) return <div className="panel p-4 text-[13px] text-[var(--bad)]">{view.error.message}</div>
  if (!q) return <div className="panel p-6 text-[13px] text-[var(--faint)]">loading the quote…</div>
  const b = q.breakdown
  const pay = async () => {
    setErr(null); setBusy(true)
    try {
      const { eth, from } = await wallet()
      if (from.toLowerCase() !== q.payer) throw new Error(`the wallet is ${from}; this quote is for ${q.payer}`)
      const t = view.data.transaction
      setTx(await eth.request({ method: 'eth_sendTransaction', params: [{ from, to: t.to, data: t.data, value: '0x' + BigInt(t.value_wei).toString(16) }] }))
    } catch (e) { setErr(e) }
    setBusy(false)
  }
  const agentName = q.spec.agent === 'custom' ? (q.spec.name || 'your agent') : `supplier check · ${(q.spec.task || 'first request').replace('supplier-', '')}`

  return (
    <div className="panel p-6 flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-[15px] font-semibold text-[var(--text-strong)]">2 · Pay the optimizer</div>
          <div className="text-[12.5px] text-[var(--muted)] mt-1">{agentName}{q.spec.steps ? ` · ${q.spec.steps} steps found in your code` : ''} · quote <span className="font-mono">{q.id}</span></div>
        </div>
        <button onClick={() => go('pay')} className="h-8 px-3 rounded-[7px] border border-[var(--line-strong)] text-[12px] text-[var(--muted)]">New agent</button>
      </div>

      <div className="flex flex-wrap items-end gap-8">
        <div>
          <div className="text-[12px] text-[var(--faint)]">you pay</div>
          <div className="console-title text-[44px] leading-none text-[var(--accent)]">{og(n18(q.pay), 4)}</div>
        </div>
        <div className="flex flex-wrap gap-2 text-[12px] pb-1">
          {[['compute for your agent', b.expected], [`optimizer's fee ${Math.round((b.fee_rate ?? 0.5) * 100)}%`, b.fee]].map(([k, v]) => (
            <span key={k} className="px-2.5 py-1 rounded-[6px] bg-white/[.04] border border-[var(--line)]"><span className="text-[var(--faint)]">{k} </span><span className="font-mono text-[var(--text)]">{og(n18(v), 4)}</span></span>
          ))}
          <button onClick={() => setShowWhy(!showWhy)} className="inline-flex items-center gap-1 px-2 text-[var(--muted)] hover:text-[var(--text)]">how it's priced <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showWhy ? 'rotate-180' : ''}`} /></button>
        </div>
      </div>
      {showWhy && <Breakdown q={q} />}

      <div className="flex flex-wrap items-center gap-4 pt-4 border-t border-[var(--line)]">
        {q.status === 'awaiting payment' && !tx ? (
          <>
            <button onClick={pay} disabled={busy}
              className="inline-flex items-center gap-2 h-11 px-6 rounded-[9px] bg-[var(--accent)] text-black text-[14.5px] font-semibold disabled:opacity-40">
              <Wallet className="w-4 h-4" /> Pay {og(n18(q.pay), 4)}
            </button>
            <span className="text-[12px] text-[var(--faint)]">one transaction: <span className="font-mono">depositFor</span> the optimizer's 0G compute account</span>
            <div className="ml-auto flex gap-2">
              <input value={pasted} onChange={(e) => setPasted(e.target.value.trim())} placeholder="paid elsewhere? tx hash"
                className="h-9 w-[240px] px-2 rounded-[7px] bg-[var(--bg)] border border-[var(--line-strong)] font-mono text-[11.5px]" />
              <button onClick={() => pasted && setTx(pasted)} className="h-9 px-3 rounded-[7px] border border-[var(--line-strong)] text-[12px]">Check</button>
            </div>
          </>
        ) : (
          <>
            <Stages q={q} events={events} tx={tx} />
            {(q.tx || tx) && <span className="text-[12px] text-[var(--faint)]">tx <Tx hash={q.tx || tx} /></span>}
          </>
        )}
      </div>
      {q.status === 'expired' && <div className="text-[13px] text-[var(--warn)]">This quote expired before a payment arrived. Get a new price.</div>}
      {confirm && !['paid', 'pending'].includes(confirm.state) && <div className="text-[13px] text-[var(--bad)]">{confirm.state}{confirm.why ? `: ${confirm.why}` : ''}</div>}
      {err && <div className="text-[13px] text-[var(--bad)]">{err.message}</div>}

      {q.job_id && (
        <button onClick={() => go('job', q.id)}
          className="group flex items-center justify-between gap-4 p-5 rounded-[12px] bg-[var(--accent)]/10 border border-[var(--accent)]/40 hover:bg-[var(--accent)]/15 text-left">
          <div>
            <div className="flex items-center gap-2 text-[15px] font-semibold text-[var(--text-strong)]"><Zap className="w-4 h-4 text-[var(--accent)]" /> 3 · Watch your agent being optimized</div>
            <div className="text-[12.5px] text-[var(--muted)] mt-1">SIR forming, the optimized graph running, every paid call, the result and your optimized code · {q.status}</div>
          </div>
          <ArrowRight className="w-6 h-6 text-[var(--accent)] group-hover:translate-x-1 transition-transform" />
        </button>
      )}
    </div>
  )
}

export default function Pay() {
  const { resultId: quoteId, go } = useView()
  return (
    <div className="flex flex-col gap-5 max-w-[1150px]">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow mb-2">The optimizer agent · 0G mainnet</div>
          <h1 className="console-title text-[34px] text-[var(--text-strong)]">Optimize your agent</h1>
          <p className="text-[14px] text-[var(--muted)] mt-1.5">Pay the optimizer in 0G. Get your agent back faster and cheaper, as code.</p>
        </div>
        <AccountChip />
      </div>
      {quoteId ? <QuotePay quoteId={quoteId} go={go} /> : <AgentForm onQuote={(q) => go('pay', q.id)} />}
    </div>
  )
}
