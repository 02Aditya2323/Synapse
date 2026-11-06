import { useView } from '../store.js'
import { Decision, Card } from './bits.jsx'
import { pct, value } from '../format.js'

const CLAIM = {
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
  const show = r.claim === 'blocked' ? ['Cost per task', 'Tasks passed', 'Irreversible tool calls (all tasks)'] : r.claim === 'faster' ? ['Time per task (median)', 'Cost per task', 'Tasks passed'] : ['Cost per task', 'Time per task, excl. rate-limit waits', 'Tasks passed']
  const metrics = show.map((n) => r.metrics.find((m) => m.name === n)).filter(Boolean)
  return (
    <button onClick={() => open(r.id)} className="text-left rounded-xl border border-line bg-surface-1 hover:border-ink-3 transition-colors p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className={`text-xs uppercase tracking-widest ${c.tone}`}>{c.word}</span>
        <Decision decision={r.verdict.decision} />
      </div>
      <div>
        <h2 className="text-lg font-semibold">{r.title}</h2>
        <p className="text-sm text-ink-3 mt-0.5">{r.subtitle}</p>
      </div>
      <div>
        <div className={`text-5xl font-semibold num ${r.claim === 'blocked' ? 'text-critical' : ''}`}><Headline r={r} /></div>
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
      <div className="text-xs text-ink-3">{r.provider} · {r.model} · {r.tasks} held-out tasks · {r.layer === 'graph' ? 'workflow graph' : 'per-call'} optimization</div>
    </button>
  )
}

export default function Overview({ featured }) {
  return (
    <div className="flex flex-col gap-8">
      <div className="max-w-3xl">
        <h1 className="text-2xl font-semibold tracking-tight">Make an agent faster and cheaper, and prove nothing broke</h1>
        <p className="text-ink-2 mt-2">
          synapse0g measures every model call, tries one optimization at a time on tuning tasks, and checks it against an unchanged run
          on the same tasks. The firewall approves a change only if no stable task breaks. These results come from tasks the search never saw.
        </p>
      </div>
      <div className="grid md:grid-cols-3 gap-5">{featured.map((r) => <Tile key={r.id} r={r} />)}</div>
      <Card title="How a result is decided">
        <ol className="grid md:grid-cols-4 gap-4 text-sm text-ink-2 list-decimal list-inside">
          <li>Run the agent unchanged twice on tuning tasks: which tasks are stable, how much numbers move by chance.</li>
          <li>Profile each step, predict each technique's saving, and try the best one beside an unchanged control run.</li>
          <li>Keep it only if no stable task breaks and cost or time improves with 95% confidence.</li>
          <li>Measure the final version against the unchanged agent on held-out tasks. That is what is shown here.</li>
        </ol>
      </Card>
    </div>
  )
}
