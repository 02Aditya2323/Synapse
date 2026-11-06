import { Decision } from './bits.jsx'
import { money, pct } from '../format.js'

const signed = (x) => (x == null ? '—' : x >= 0 ? `${pct(x)} lower` : `${pct(-x)} higher`)

export default function History({ rows }) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Run history</h1>
        <p className="text-ink-2 text-sm">Every saved run, including the ones that found nothing worth shipping. Invalid runs are kept and marked.</p>
      </div>
      <div className="rounded-xl border border-line bg-surface-1 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-ink-3 text-left">
            <tr className="border-b border-line">
              {['Agent', 'Layer', 'Provider', 'Tasks', 'Outcome', 'Cost', 'Time', 'Passes', 'Crashed', 'Spent', 'Policy', 'Report'].map((h) => <th key={h} className="font-normal px-3 py-2.5">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr key={h.file} className={`border-t border-line align-top ${h.invalid ? 'opacity-50' : ''}`}>
                <td className="px-3 py-2">{h.agent}{h.invalid && <div className="text-[11px] text-warning">invalid: provider rejected tool calls</div>}</td>
                <td className="px-3 py-2 text-ink-2">{h.layer}</td>
                <td className="px-3 py-2 text-ink-2 whitespace-nowrap">{h.provider}</td>
                <td className="px-3 py-2 num whitespace-nowrap">{h.tasks} <span className="text-ink-3 text-xs">{h.split === 'test' ? 'held-out' : 'tuning'}</span></td>
                <td className="px-3 py-2">{h.outcome === 'no beneficial change' ? <span className="text-ink-3 text-xs">no beneficial change</span> : <Decision decision={h.outcome} />}</td>
                <td className="px-3 py-2 num text-xs">{signed(h.cost_change)}</td>
                <td className="px-3 py-2 num text-xs">{signed(h.time_change)}</td>
                <td className="px-3 py-2 num text-xs">{h.passes ? h.passes.join(' / ') : '—'}{h.agent === 'shop_ops' && <div className="text-[11px] text-ink-3">fails unchanged too</div>}</td>
                <td className="px-3 py-2 num text-xs">{h.crashed ?? '—'}</td>
                <td className="px-3 py-2 num text-xs">{money(h.spent_usd)}</td>
                <td className="px-3 py-2 font-mono text-[11px] text-ink-2">{h.policy?.join('; ') || 'unchanged'}</td>
                <td className="px-3 py-2 font-mono text-[11px] text-ink-3">{h.file.replace('reports/', '')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
