import { changeText, changeTone, pct, value } from '../format.js'

const TONE = { good: 'text-good', bad: 'text-critical', neutral: 'text-ink-3' }

function Bar({ v, max, color, label }) {
  const w = max > 0 && v != null ? Math.max((v / max) * 100, 0.5) : 0
  return (
    <div className="flex items-center gap-2 group" title={`${label}: ${v ?? 'not measured'}`}>
      <div className="flex-1 h-2.5 bg-surface-2 rounded-r">
        <div className="h-full rounded-r" style={{ width: `${w}%`, background: color }} />
      </div>
    </div>
  )
}

export default function Metrics({ metrics }) {
  return (
    <div>
      <div className="flex gap-4 text-xs text-ink-3 mb-3">
        <span className="flex items-center gap-1.5"><span className="w-3 h-2.5 rounded-sm bg-before" />unchanged agent</span>
        <span className="flex items-center gap-1.5"><span className="w-3 h-2.5 rounded-sm bg-after" />optimized</span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs text-ink-3">
          <tr className="text-left">
            <th className="font-normal pb-2">Measure</th>
            <th className="font-normal pb-2 text-right">Unchanged</th>
            <th className="font-normal pb-2 text-right">Optimized</th>
            <th className="font-normal pb-2 text-right">Change</th>
            <th className="font-normal pb-2 pl-6 w-[28%]">Compared</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => {
            const max = Math.max(m.before ?? 0, m.after ?? 0)
            return (
              <tr key={m.name} className="border-t border-line align-top">
                <td className="py-2.5 pr-4">
                  <div>{m.name}</div>
                  {m.note && <div className="text-xs text-ink-3 mt-0.5">{m.note}</div>}
                </td>
                <td className="py-2.5 text-right num text-ink-2">{value(m.before, m.unit)}</td>
                <td className="py-2.5 text-right num">{value(m.after, m.unit)}</td>
                <td className={`py-2.5 text-right num ${TONE[changeTone(m)]}`}>
                  {changeText(m)}
                  {m.ci && <div className="text-[11px] text-ink-3">95% CI {pct(m.ci[0])} to {pct(m.ci[1])} lower</div>}
                </td>
                <td className="py-3 pl-6">
                  <div className="flex flex-col gap-1">
                    <Bar v={m.before} max={max} color="var(--color-before)" label="unchanged" />
                    <Bar v={m.after} max={max} color="var(--color-after)" label="optimized" />
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
