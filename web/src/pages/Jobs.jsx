import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { PageTitle } from '../components/bits.jsx'
import { useView } from '../store.js'
import { Account, History, call, n18, og, when } from './paykit.jsx'

const TONE = { done: 'var(--ok)', running: 'var(--accent)', paid: 'var(--accent)', 'waiting for funds': 'var(--warn)', failed: 'var(--bad)' }

/** Every job and every payment, and the optimizer's own account. */
export default function Jobs() {
  const { go } = useView()
  const agent = useQuery({ queryKey: ['agent'], queryFn: () => call('/api/agent'), refetchInterval: 10000 })
  const history = useQuery({ queryKey: ['pay-history'], queryFn: () => call('/api/pay/history'), refetchInterval: 8000 })
  const quotes = (history.data?.quotes || []).filter((q) => q.status !== 'expired' || q.tx)
  return (
    <div className="flex flex-col gap-5">
      <PageTitle eyebrow="The optimizer agent" title="Jobs and payments" />
      <Account account={agent.data?.account} />
      <div className="panel overflow-hidden">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-left text-[var(--faint)] border-b border-[var(--line)]">
              <th className="font-normal px-5 py-2.5">job</th><th className="font-normal">agent</th><th className="font-normal">quoted</th>
              <th className="font-normal text-right">paid</th><th className="font-normal pl-6">status</th><th />
            </tr>
          </thead>
          <tbody>
            {quotes.map((q) => (
              <tr key={q.id} onClick={() => go(q.job_id ? 'job' : 'pay', q.id)} className="border-b border-[var(--line)] last:border-0 hover:bg-white/[.025] cursor-pointer">
                <td className="px-5 py-3 font-mono text-[var(--text-strong)]">{q.job_id || q.id}</td>
                <td className="text-[var(--text)]">{q.spec?.agent === 'custom' ? (q.spec.name || 'your agent') : `supplier check · ${(q.spec?.task || 'first request').replace('supplier-', '')}`}</td>
                <td className="font-mono text-[var(--muted)]">{when(q.created)}</td>
                <td className="text-right font-mono text-[var(--text-strong)]">{og(n18(q.pay), 4)}</td>
                <td className="pl-6"><span className="inline-flex items-center gap-1.5" style={{ color: TONE[q.status] || 'var(--muted)' }}><span className="w-1.5 h-1.5 rounded-full" style={{ background: TONE[q.status] || 'var(--muted)' }} />{q.status}</span></td>
                <td className="pr-5 text-right"><ArrowRight className="inline w-4 h-4 text-[var(--ghost)]" /></td>
              </tr>
            ))}
            {!quotes.length && <tr><td colSpan={6} className="px-5 py-4 text-[var(--ghost)]">No jobs yet. <button className="text-[var(--accent)]" onClick={() => go('pay')}>Optimize your agent</button></td></tr>}
          </tbody>
        </table>
      </div>
      <History data={history.data} go={(p, id) => go('job', id)} />
    </div>
  )
}
