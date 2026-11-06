import { useState } from 'react'
import { Pass, Status } from './bits.jsx'
import { money } from '../format.js'

const longest = (lists) => lists.reduce((a, b) => (b && b.length > a.length ? b : a), [])

const ORDER = { broken: 0, crashed: 1, fixed: 2, unstable: 3, unchanged: 4, unknown: 5 }

/** The actions, with any the unchanged runs did not take (by count) marked in red. */
function Actions({ list, against }) {
  if (list == null) return <span className="text-ink-3">—</span>
  if (!list.length) return <span className="text-ink-3">no state-changing action</span>
  const left = {}
  for (const a of against || list) left[a] = (left[a] || 0) + 1
  return (
    <span className="font-mono text-[11px] text-ink-2">
      {list.map((a, i) => {
        const extra = against && !(left[a] > 0)
        if (left[a] > 0) left[a] -= 1
        return <span key={i}>{i > 0 && ' → '}<span className={extra ? 'text-critical font-semibold' : ''}>{a}</span></span>
      })}
    </span>
  )
}

export default function Tasks({ r }) {
  const rows = [...r.task_rows].sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.id.localeCompare(b.id))
  const counts = rows.reduce((m, t) => ({ ...m, [t.status]: (m[t.status] || 0) + 1 }), {})
  const [filter, setFilter] = useState(counts.broken ? 'broken' : 'all')
  const [open, setOpen] = useState(null)
  const shown = filter === 'all' ? rows : rows.filter((t) => t.status === filter)
  const retail = rows.some((t) => t.actions_before)
  return (
    <div>
      <div className="flex gap-2 mb-3 text-xs">
        {['all', ...Object.keys(counts).sort((a, b) => ORDER[a] - ORDER[b])].map((k) => (
          <button key={k} onClick={() => setFilter(k)} className={`px-2.5 py-1 rounded-md border ${filter === k ? 'border-ink-3 bg-surface-2' : 'border-line text-ink-3'}`}>
            {k} {k === 'all' ? rows.length : counts[k]}
          </button>
        ))}
      </div>
      {retail && <p className="text-xs text-ink-3 mb-2">Actions are the state-changing tools the agent called, in order (names are recorded, arguments are not). Red: an action the unchanged agent did not take. A task passes when the final database matches tau-bench's reference.</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-ink-3 text-left">
            <tr>
              <th className="font-normal pb-2">Task</th><th className="font-normal pb-2">Status</th>
              <th className="font-normal pb-2">Unchanged</th><th className="font-normal pb-2">Optimized</th>
              <th className="font-normal pb-2 text-right">Time</th><th className="font-normal pb-2 text-right">Cost</th>
              {retail && <th className="font-normal pb-2 pl-4">Actions: unchanged → optimized</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => (
              <tr key={t.id} className="border-t border-line align-top cursor-pointer hover:bg-surface-2/50" onClick={() => setOpen(open === t.id ? null : t.id)}>
                <td className="py-2 font-mono text-xs whitespace-nowrap">{t.id.replace('tau-retail-', '').replace('hotpot_', '')}</td>
                <td className="py-2"><Status status={t.status} /></td>
                <td className="py-2 text-xs">{t.before.map((b, i) => <span key={i} className="mr-2"><Pass v={b} /></span>)}</td>
                <td className="py-2 text-xs"><Pass v={t.after} /></td>
                <td className="py-2 text-right num text-xs whitespace-nowrap">{t.before_wall_s?.toFixed(1)} → {t.after_wall_s?.toFixed(1)} s</td>
                <td className="py-2 pl-3 text-right num text-xs whitespace-nowrap">{money(t.before_cost_usd)} → {money(t.after_cost_usd)}</td>
                {retail && (
                  <td className="py-2 pl-4 text-xs">
                    <div><span className="text-ink-3 mr-1.5">before</span><Actions list={t.actions_before[0]} /></div>
                    <div className="mt-0.5"><span className="text-ink-3 mr-1.5">after</span><Actions list={t.actions_after} against={longest(t.actions_before)} /></div>
                    {open === t.id && <div className="mt-2 text-ink-3 font-mono text-[11px]">unchanged: {t.answer_before}<br />optimized: {t.answer_after}</div>}
                  </td>
                )}
                {!retail && open === t.id && null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!retail && <p className="text-xs text-ink-3 mt-2">Click a row to see both answers.</p>}
      {!retail && open && (() => {
        const t = rows.find((x) => x.id === open)
        return t && <div className="mt-2 rounded-md bg-surface-2 p-3 text-xs font-mono"><div>unchanged: {t.answer_before}</div><div>optimized: {t.answer_after}</div>{t.error && <div className="text-warning">error: {t.error}</div>}</div>
      })()}
    </div>
  )
}
