import { useRecorded, useRecordedRun } from '../api.js'
import { useView } from '../store.js'
import { Card, Code, PageTitle } from '../components/bits.jsx'
import { Compare } from '../sir/Compare.tsx'

const ms = (v) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`)
const n = (v) => (v == null ? '—' : v.toLocaleString())

function Run({ id }) {
  const q = useRecordedRun(id)
  if (q.isLoading) return <p className="text-[var(--faint)]">Loading the recorded run…</p>
  if (q.error) return <p className="text-[var(--bad)]">{q.error.message}</p>
  const r = q.data
  const w = r.waste || {}
  const tokens = r.calls.reduce((a, c) => a + (c.tokens_in || 0) + (c.tokens_out || 0), 0)
  const wall = r.calls.reduce((a, c) => a + c.latency_ms, 0)
  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[['Model calls', r.calls.length], ['Exact repeats', r.repeated_calls], ['Tokens (provider-reported)', n(tokens)], ['Time in calls, one after another', ms(wall)]].map(([k, v]) => (
          <div key={k} className="panel px-4 py-3">
            <div className="label">{k}</div>
            <div className="font-mono text-[22px] text-[var(--text-strong)] mt-1">{v}</div>
          </div>
        ))}
      </div>
      <Compare before={r.before} after={r.after} passes={r.passes} beforeLabel="As recorded" afterLabel="After the passes" />
      <Card title="What the passes would save on this run">
        <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 text-[13px]">
          <div><dt className="label">Steps that repeat an earlier one</dt><dd className="font-mono text-[18px] text-[var(--text-strong)]">{w.removable?.length ?? 0}</dd></div>
          <div><dt className="label">Tokens in those repeats</dt><dd className="font-mono text-[18px] text-[var(--text-strong)]">{n((w.tokens_in_removable ?? 0) + (w.tokens_out_removable ?? 0))}</dd></div>
          <div><dt className="label">Time in series</dt><dd className="font-mono text-[18px] text-[var(--text-strong)]">{ms(w.serial_ms)}</dd></div>
          <div><dt className="label">With only real dependencies in order</dt><dd className="font-mono text-[18px] text-[var(--text-strong)]">{ms(w.dependency_bound_ms)}</dd></div>
        </dl>
        <p className="text-[12.5px] text-[var(--faint)] mt-3">{r.note} The second time figure is a bound worked out from the graph; only a run of the compiled app shows how much of it is reachable.</p>
      </Card>
      <Card title="Every call">
        <table className="dtable">
          <thead><tr><th>#</th><th>Model</th><th className="num">Time</th><th className="num">Tokens in</th><th className="num">Tokens out</th><th>Request hash</th></tr></thead>
          <tbody>
            {r.calls.map((c, i) => (
              <tr key={i}>
                <td className="font-mono text-[12px] text-[var(--faint)]">n{i}</td>
                <td className="font-mono text-[12.5px]">{c.model}</td>
                <td className="num">{ms(c.latency_ms)}</td>
                <td className="num">{n(c.tokens_in)}</td>
                <td className="num">{n(c.tokens_out)}</td>
                <td className="font-mono text-[11.5px] text-[var(--faint)]">{c.request_hash}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[12px] text-[var(--faint)] mt-2">Prompts and answers were never saved; the hash shows which requests were identical.</p>
      </Card>
    </div>
  )
}

export default function Recorded() {
  const runs = useRecorded()
  const { resultId, go } = useView()
  const list = runs.data?.runs || []
  const id = resultId || list[0]?.run_id
  return (
    <div className="flex flex-col gap-6">
      <PageTitle eyebrow="Option B · wrap" title="Recorded runs">
        Apps wrapped with <span className="font-mono text-[var(--text)]">synapse0g.wrap(client)</span> show up here: their calls, measured, lifted into a graph.
      </PageTitle>
      {runs.isLoading && <p className="text-[var(--faint)]">Loading…</p>}
      {runs.error && <p className="text-[var(--bad)]">{runs.error.message}</p>}
      {!runs.isLoading && !list.length && (
        <Card title="Nothing recorded yet">
          <p className="text-[13.5px] text-[var(--muted)] mb-3">Wrap a client in any app and run it. The run lands here when the app exits.</p>
          <Code>{'python examples/wrap_agent.py'}</Code>
        </Card>
      )}
      {list.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {list.map((r) => (
            <button key={r.run_id} onClick={() => go('recorded', r.run_id)}
              className={`px-3 py-1.5 rounded-[8px] border text-[13px] ${r.run_id === id ? 'border-[var(--accent)] text-[var(--text-strong)]' : 'border-[var(--line)] text-[var(--muted)] hover:border-[var(--line-strong)]'}`}>
              {r.workflow} <span className="font-mono text-[11px] text-[var(--faint)] ml-1">{r.saved_at.slice(5, 16).replace('T', ' ')}</span>
            </button>
          ))}
        </div>
      )}
      {id && <Run id={id} />}
    </div>
  )
}
