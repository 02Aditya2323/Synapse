import { useState } from 'react'
import { FileCode2, Play, Plug, Workflow } from 'lucide-react'
import { useMap, useRecorded, useSamples } from '../api.js'
import { useView } from '../store.js'
import { Card, Code, EffectBadge, PageTitle } from '../components/bits.jsx'
import { Compare } from '../sir/Compare.tsx'

const OPTIONS = [
  { id: 'paste', icon: FileCode2, title: 'Paste the code', line: 'Zero code changes', text: 'Paste an agent, or give a path. It is read, never run: each step\'s effect comes from what its code calls.' },
  { id: 'wrap', icon: Plug, title: 'Wrap the client', line: 'One line', text: 'Wrap any OpenAI-compatible client. Every call is measured and the calls are lifted into a graph.' },
  { id: 'compile', icon: Workflow, title: 'Compile a LangGraph app', line: 'Three lines', text: 'The compiler traces real reads and writes, rewrites the graph, runs it, and the firewall checks it.' },
]

function Inspector() {
  const samples = useSamples()
  const map = useMap()
  const [text, setText] = useState('')
  const [sample, setSample] = useState('')
  const r = map.data
  const choose = (file) => {
    setSample(file)
    const s = samples.data?.samples.find((x) => x.file === file)
    if (s) { setText(s.code); map.reset() }
  }
  const submit = () => map.mutate(text.includes('\n') ? { code: text } : { path: text.trim() })
  return (
    <div className="flex flex-col gap-5">
      <Card eyebrow="Option A" title="Give it your code">
        <div className="flex flex-wrap items-center gap-3 mb-3 text-[13px] text-[var(--muted)]">
          <span>Try a sample agent</span>
          <select aria-label="Choose a sample agent" value={sample} onChange={(e) => choose(e.target.value)}
            className="h-9 min-w-[240px] px-3 rounded-[8px] bg-[var(--bg)] border border-[var(--line-strong)] text-[13px] text-[var(--text-strong)]">
            <option value="">Choose one, or paste your own</option>
            {samples.data?.samples.map((s) => <option key={s.file} value={s.file}>{s.label}</option>)}
          </select>
          <span className="text-[var(--faint)]">Plain Python sample agents without synapse0g integration.</span>
        </div>
        <textarea aria-label="Agent code, or a path to it" value={text} onChange={(e) => { setText(e.target.value); setSample('') }}
          rows={text.includes('\n') ? 14 : 2} spellCheck={false} placeholder="Paste Python agent code, or a path such as synapse0g/samples/market_desk.py"
          className="field w-full h-auto py-2.5 font-mono text-[12.5px] leading-relaxed resize-y" />
        <div className="flex items-center gap-3 mt-3">
          <button onClick={submit} disabled={!text.trim() || map.isPending}
            className="inline-flex items-center gap-2 h-9 px-4 rounded-[8px] bg-[var(--accent)] text-black text-[13.5px] font-semibold disabled:opacity-40">
            <Play className="w-4 h-4" strokeWidth={2.2} /> {map.isPending ? 'Reading…' : 'Inspect'}
          </button>
          <span className="text-[12px] text-[var(--faint)]">Nothing is executed and nothing leaves this machine.</span>
        </div>
        {map.error && <p className="mt-3 text-[13px] text-[var(--bad)]">{map.error.message}</p>}
      </Card>
      {r && <InspectResult r={r} />}
    </div>
  )
}

function InspectResult({ r }) {
  return (
    <>
      <div className="text-[13px] text-[var(--muted)]">
        {r.functions} functions read from {r.source}{r.entry && <>; entry function <span className="font-mono text-[var(--text-strong)]">{r.entry}</span></>}.
        Every effect below is inferred, with its evidence.
      </div>
      {r.refusal && (
        <div className="panel p-4 border-l-2 !border-l-[var(--warn)] text-[13px]">
          <div className="font-semibold text-[var(--warn)]">Not drawn as a graph</div>
          <p className="text-[var(--text)] mt-1">{r.refusal}</p>
          <p className="text-[var(--faint)] mt-1">Control flow the reader cannot see through is refused with its line, never guessed.</p>
        </div>
      )}
      {r.before && <Compare before={r.before} after={r.after} passes={r.passes} beforeLabel="As written" afterLabel="After the passes" />}
      {r.steps.length > 0 && (
        <Card title="Why each step got its effect">
          <table className="dtable">
            <thead><tr><th>Step</th><th>Function</th><th>Effect</th><th>Evidence</th></tr></thead>
            <tbody>
              {r.steps.map((s) => (
                <tr key={s.node}>
                  <td className="font-mono text-[12px] text-[var(--faint)]">{s.node}</td>
                  <td className="font-mono text-[12.5px] text-[var(--text-strong)]">{s.target}</td>
                  <td><EffectBadge effect={s.effect} /></td>
                  <td className="text-[13px] text-[var(--muted)]">{s.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {r.tools.length > 0 && (
        <Card title={`Agent tools found in a dispatch table (${r.tools.length})`}>
          <ul className="text-[13px] space-y-1">
            {r.tools.map((t) => <li key={t.tool} className="flex gap-3"><span className="font-mono w-48 shrink-0">{t.tool}</span><EffectBadge effect={t.effect} /><span className="text-[var(--muted)]">{t.why}</span></li>)}
          </ul>
        </Card>
      )}
    </>
  )
}

function WrapOption() {
  const runs = useRecorded()
  const go = useView((s) => s.go)
  return (
    <Card eyebrow="Option B" title="Wrap the client: one line">
      <div className="grid lg:grid-cols-2 gap-6">
        <div className="space-y-3 text-[13.5px] text-[var(--muted)]">
          <Code>{`from openai import OpenAI
import synapse0g

client = synapse0g.wrap(OpenAI(), workflow="support_desk")
# the rest of the app is unchanged`}</Code>
          <p>Calls still go to the app's own provider with its own key. Each one is timed and its token counts kept. At exit the calls are lifted into a graph: a call depends on an earlier one when that call's answer appears in its prompt. Then the passes run on it.</p>
          <p>Saved: the graph and the measurements. Never prompt or answer text. A wrapper sees calls, not the code between them, so to apply what it finds, compile the app.</p>
          <Code>{'python examples/wrap_agent.py      # a small support agent on 0G, wrapped'}</Code>
        </div>
        <div>
          <div className="label mb-2">Recorded runs on this machine</div>
          {runs.data?.runs.length ? (
            <ul className="space-y-1.5">
              {runs.data.runs.slice(0, 6).map((r) => (
                <li key={r.run_id}>
                  <button onClick={() => go('recorded', r.run_id)} className="w-full text-left rounded-[8px] border border-[var(--line)] hover:border-[var(--line-strong)] bg-[var(--bg)] px-3 py-2">
                    <div className="flex justify-between text-[13px]"><span className="text-[var(--text-strong)]">{r.workflow}</span><span className="font-mono text-[11.5px] text-[var(--faint)]">{r.saved_at}</span></div>
                    <div className="text-[12px] text-[var(--muted)]">{r.calls} calls · {r.repeated_calls} exact repeats · passes: {r.applied.join(', ') || 'none applied'}</div>
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="text-[13px] text-[var(--faint)]">None yet. Run a wrapped app and it appears here.</p>}
        </div>
      </div>
    </Card>
  )
}

function CompileOption({ hero }) {
  const open = useView((s) => s.open)
  return (
    <Card eyebrow="Option C" title="Compile a LangGraph app: three lines">
      <div className="grid lg:grid-cols-2 gap-6 text-[13.5px] text-[var(--muted)]">
        <div className="space-y-3">
          <Code>{`from synapse0g.openai import OpenAI          # was: from openai import OpenAI
app = synapse0g.compile(builder, sample=request, effects=EFFECTS)   # was: builder.compile()
state = app.invoke(request)`}</Code>
          <p><span className="font-mono text-[var(--text)]">EFFECTS</span> names the steps that touch the world. Any step not named is treated as touching it: never traced, moved, cached or repeated.</p>
          <Code>{`python examples/supplier_agent.py RFQ-003            # compile, show the plan, run
synapse0g bench --agent supplier --model zg-main     # measure as written vs compiled`}</Code>
        </div>
        <div className="space-y-3">
          <p>The compiler runs the declared read-only steps once on a sample and records which state keys each really reads and writes. Edges come from that data flow, not from the order the code was written in. The passes then rewrite the graph, and every model call goes through the call-level optimizer.</p>
          <p>Unlike a wrapper, the compiled app runs the rewritten graph, so its savings are real and are measured on held-out tasks.</p>
          {hero && <button onClick={() => open(hero.id)} className="text-[var(--accent)] hover:underline">See the supplier-check agent, compiled and measured on 0G →</button>}
        </div>
      </div>
    </Card>
  )
}

export default function Connect({ hero }) {
  const [tab, setTab] = useState('paste')
  return (
    <div className="flex flex-col gap-7">
      <PageTitle eyebrow="Connect" title="Three ways in, one graph">
        Pick how much of your agent synapse0g gets to see. Each way ends in the same effect-typed graph, where the passes find what can run in parallel, what repeats and what must never be touched.
      </PageTitle>
      <div className="grid md:grid-cols-3 gap-4">
        {OPTIONS.map((o) => {
          const Icon = o.icon
          const on = tab === o.id
          return (
            <button key={o.id} onClick={() => setTab(o.id)} className={`panel text-left p-5 transition-colors ${on ? '!border-[var(--accent)]' : 'hover:!border-[var(--line-strong)]'}`}>
              <div className="flex items-center gap-2">
                <Icon className={`w-4 h-4 ${on ? 'text-[var(--accent)]' : 'text-[var(--faint)]'}`} strokeWidth={1.75} />
                <span className="text-[11px] uppercase tracking-[.14em] text-[var(--faint)]">{o.line}</span>
              </div>
              <div className="mt-2 text-[16px] font-semibold text-[var(--text-strong)]">{o.title}</div>
              <p className="mt-1 text-[13px] text-[var(--muted)]">{o.text}</p>
            </button>
          )
        })}
      </div>
      {tab === 'paste' && <Inspector />}
      {tab === 'wrap' && <WrapOption />}
      {tab === 'compile' && <CompileOption hero={hero} />}
    </div>
  )
}
