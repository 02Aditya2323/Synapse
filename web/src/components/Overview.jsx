import { useView } from '../store.js'
import { Decision, Card, PageTitle } from './bits.jsx'
import { money, pct, value } from '../format.js'

const CLAIM = {
  app: { word: 'Compiled app', tone: 'text-accent' },
  faster: { word: 'Faster', tone: 'text-after' },
  cheaper: { word: 'Cheaper', tone: 'text-after' },
  blocked: { word: 'Refused', tone: 'text-critical' },
}

function Headline({ r }) {
  const h = r.headline
  if (h.unit === 'x') return <>{h.value.toFixed(2)}×</>
  if (h.unit === '%') return <>{pct(h.value, 1)}</>
  return <>{h.value}</>
}

function Tile({ r }) {
  const open = useView((s) => s.open)
  const c = CLAIM[r.claim]
  const show = r.claim === 'app' ? ['Time per task (median)', 'Cost per task', 'Tasks passed'] : r.claim === 'blocked' ? ['Cost per task', 'Tasks passed', 'Irreversible tool calls (all tasks)'] : r.claim === 'faster' ? ['Time per task (median)', 'Cost per task', 'Tasks passed'] : ['Cost per task', 'Time per task, excl. rate-limit waits', 'Tasks passed']
  const metrics = show.map((n) => r.metrics.find((m) => m.name === n)).filter(Boolean)
  return (
    <button onClick={() => open(r.id)} className="panel text-left hover:!border-[var(--line-strong)] transition-colors p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className={`text-xs uppercase tracking-widest ${c.tone}`}>{c.word}</span>
        <Decision decision={r.verdict.decision} />
      </div>
      <div>
        <h2 className="text-lg font-semibold">{r.title}</h2>
        <p className="text-sm text-ink-3 mt-0.5">{r.subtitle}</p>
      </div>
      <div>
        <div className={`console-title text-[48px] ${r.claim === 'blocked' ? 'text-critical' : 'text-[var(--text-strong)]'}`}><Headline r={r} /></div>
        <div className="text-sm text-ink-2 mt-1">{r.headline.label}</div>
      </div>
      <dl className="grid grid-cols-1 gap-1.5 text-sm border-t border-line pt-4">
        {metrics.map((m) => (
          <div key={m.name} className="flex justify-between gap-4">
            <dt className="text-ink-3">{m.name.replace(' (all tasks)', '')}</dt>
            <dd className="num">{value(m.before, m.unit)} → <span className="text-ink">{value(m.after, m.unit)}</span></dd>
          </div>
        ))}
      </dl>
      <div className="text-xs text-ink-3">{r.provider} · {r.model} · {r.tasks} held-out tasks · {r.layer === 'app' ? 'compiled LangGraph app: graph and per-call' : r.layer === 'graph' ? 'workflow graph' : 'per-call'} optimization</div>
    </button>
  )
}

function Hero({ r }) {
  const open = useView((s) => s.open)
  const [asWritten, hand, compiled] = r.arms
  const bar = (a, color) => (
    <div className="flex items-center gap-3 text-[13px]">
      <span className="w-64 shrink-0 text-[var(--muted)]">{a.label}</span>
      <div className="flex-1 h-[10px] rounded-[3px] bg-white/[.04] overflow-hidden">
        <div className="h-full rounded-[3px]" style={{ width: `${(a.median_wall_s / asWritten.median_wall_s) * 100}%`, background: color }} />
      </div>
      <span className="w-20 text-right font-mono text-[var(--text-strong)]">{a.median_wall_s.toFixed(1)} s</span>
      <span className="w-20 text-right font-mono text-[var(--muted)]">{money(a.cost_per_task_usd)}</span>
    </div>
  )
  return (
    <button onClick={() => open(r.id)} className="panel text-left p-6 hover:!border-[var(--line-strong)] transition-colors">
      <div className="flex items-center justify-between">
        <span className="eyebrow">The demo app · {r.provider} · {r.tasks} held-out tasks</span>
        <Decision decision={r.verdict.decision} />
      </div>
      <div className="grid lg:grid-cols-[1fr_1.4fr] gap-8 mt-4 items-center">
        <div>
          <h2 className="text-[22px] font-semibold text-[var(--text-strong)]">{r.title}</h2>
          <p className="text-[13.5px] text-[var(--muted)] mt-1">{r.subtitle}. Three lines connect it; the compiler found the rest.</p>
          <div className="flex items-end gap-6 mt-4">
            <div><div className="console-title text-[56px] text-[var(--accent)]">{r.headline.value.toFixed(2)}×</div><div className="text-[13px] text-[var(--muted)] mt-1">faster, median per task</div></div>
            <div><div className="console-title text-[56px] text-[var(--text-strong)]">{pct(compiled && 1 - compiled.cost_per_task_usd / asWritten.cost_per_task_usd)}</div><div className="text-[13px] text-[var(--muted)] mt-1">cheaper (0G billed)</div></div>
            <div><div className="console-title text-[56px] text-[var(--text-strong)]">{r.verdict.passes.before}→{r.verdict.passes.after}</div><div className="text-[13px] text-[var(--muted)] mt-1">tasks passed</div></div>
          </div>
        </div>
        <div className="flex flex-col gap-2.5">
          <div className="flex text-[11.5px] text-[var(--faint)]"><span className="flex-1">median time per task</span><span className="w-20 text-right">time</span><span className="w-20 text-right">cost/task</span></div>
          {bar(asWritten, 'var(--line-strong)')}
          {bar(hand, 'var(--fx-read)')}
          {bar(compiled, 'var(--accent)')}
          <p className="text-[12px] text-[var(--faint)] mt-1">Hand-wired LangGraph reduces median time to 30.6 s; the compiled app reaches 15.4 s. The hand-wired version still recomputes the policy every time.</p>
        </div>
      </div>
    </button>
  )
}

export default function Overview({ featured }) {
  const hero = featured.find((r) => r.claim === 'app')
  const rest = featured.filter((r) => r.claim !== 'app')
  return (
    <div className="flex flex-col gap-8">
      <PageTitle eyebrow="Results" title="Faster and cheaper agents, and proof nothing broke">
        synapse0g compiles an agent into an effect-typed graph, rewrites what is safe to rewrite, and measures the result against the
        unchanged agent on tasks the tuning never saw. The firewall approves a change only if no stable task breaks.
      </PageTitle>
      {hero && <Hero r={hero} />}
      <div className="grid md:grid-cols-3 gap-5">{rest.map((r) => <Tile key={r.id} r={r} />)}</div>
      <Card title="How a result is decided">
        <ol className="grid md:grid-cols-4 gap-4 text-sm text-ink-2 list-decimal list-inside">
          <li>Compile the agent: steps, their effects, and the dependencies measured from what each step really reads.</li>
          <li>Run the passes. Each states the precondition that makes it safe; nothing that touches the world is moved, cached or repeated.</li>
          <li>Try each change on tuning tasks beside an unchanged control; keep it only if no stable task breaks and cost or time improves with 95% confidence.</li>
          <li>Measure the final version against the unchanged agent on held-out tasks. That is what is shown here.</li>
        </ol>
      </Card>
    </div>
  )
}
