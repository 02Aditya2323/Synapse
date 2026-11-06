import { motion } from 'motion/react'
import { BarChart3, Boxes, CreditCard, History, Plug, Radio, Wallet, Zap } from 'lucide-react'
import { useView } from '../store.js'
import { useHealth } from '../api.js'

/** The glyph: a signal fans out from one node to two. */
export function Glyph({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <circle cx="16" cy="16" r="14.5" stroke="var(--accent)" strokeWidth="1.5" opacity=".9" />
      <path d="M11.4 16h3.6l4.6-5.4M15 16l4.6 5.4" stroke="var(--line-strong)" strokeWidth="1.4" />
      <path d="M11.4 16h3.6l4.6-5.4M15 16l4.6 5.4" stroke="var(--accent)" strokeWidth="1.6" className="trace-run" />
      <circle cx="9" cy="16" r="2.4" fill="var(--accent)" />
      <circle cx="22" cy="9.5" r="2.4" fill="var(--text)" />
      <circle cx="22" cy="22.5" r="2.4" fill="var(--text)" />
    </svg>
  )
}

const NAV = [
  { page: 'live', label: 'Live run', icon: Zap },
  { page: 'overview', label: 'Results', icon: BarChart3, also: ['result'] },
  { page: 'connect', label: 'Connect an agent', icon: Plug },
  { page: 'recorded', label: 'Recorded runs', icon: Radio },
  { page: 'workflows', label: 'Workflows', icon: Boxes },
  { page: 'history', label: 'Run history', icon: History },
]

function NavItem({ item }) {
  const { page, go } = useView()
  const active = page === item.page || item.also?.includes(page)
  const Icon = item.icon
  return (
    <button onClick={() => go(item.page)}
      className={`group relative w-full flex items-center gap-2.5 px-3 h-9 rounded-[8px] text-[14px] transition-colors ${active ? 'text-[var(--text-strong)]' : 'text-[var(--muted)] hover:text-[var(--text)] hover:bg-white/[.025]'}`}>
      {active && (
        <motion.span layoutId="nav-active" className="absolute inset-0 rounded-[8px] bg-white/[.055]" transition={{ type: 'spring', stiffness: 420, damping: 36 }}>
          <span className="absolute left-0 top-2 bottom-2 w-[2px] rounded-full bg-[var(--accent)]" />
        </motion.span>
      )}
      <Icon className={`relative w-4 h-4 ${active ? 'text-[var(--accent)]' : 'text-[var(--faint)] group-hover:text-[var(--muted)]'}`} strokeWidth={1.75} />
      <span className="relative truncate">{item.label}</span>
    </button>
  )
}

function HealthLine() {
  const h = useHealth()
  const up = !!h.data && !h.error
  const color = up ? 'var(--ok)' : h.isLoading ? 'var(--ghost)' : 'var(--bad)'
  return (
    <div className="m-3 rounded-[10px] border border-white/[.07] bg-white/[.02] px-3.5 py-3 text-[12px] text-[var(--faint)] space-y-1">
      <div className="flex items-center gap-2">
        <span className="relative flex w-2 h-2">
          {up && <span className="ping-soft absolute inset-0 rounded-full" style={{ background: color }} />}
          <span className="relative w-2 h-2 rounded-full" style={{ background: color }} />
        </span>
        <span className="text-[var(--text)]">{up ? 'API connected' : h.isLoading ? 'Connecting…' : 'API unreachable'}</span>
      </div>
      <div className="font-mono text-[11px]">{up ? 'measured results only' : 'synapse0g ui'}</div>
    </div>
  )
}

export default function Shell({ children }) {
  const go = useView((s) => s.go)
  return (
    <div className="min-h-full flex">
      <aside className="sticky top-0 h-screen w-[248px] shrink-0 border-r border-white/[.06] flex flex-col bg-[linear-gradient(180deg,rgba(17,24,39,.72),rgba(11,15,23,.92))] backdrop-blur-xl">
        <button onClick={() => go('overview')} className="group flex items-center gap-2.5 px-5 h-16 border-b border-white/[.06] text-left">
          <Glyph />
          <span className="font-display font-display-tight text-[16px] text-[var(--text-strong)] group-hover:text-[var(--accent)] transition-colors" style={{ fontWeight: 800, letterSpacing: '-0.01em' }}>
            SYNAPSE<span className="text-[var(--accent)]">0G</span>
          </span>
          <span className="ml-auto text-[10.5px] font-medium uppercase tracking-[.14em] text-[var(--faint)]">Console</span>
        </button>
        <nav className="flex-1 px-3 py-3 space-y-0.5">
          <div className="px-3 pt-1 pb-1.5 text-[10.5px] font-medium uppercase tracking-[.14em] text-[var(--ghost)]">Compiler</div>
          {NAV.map((item) => <NavItem key={item.page} item={item} />)}
          <div className="px-3 pt-4 pb-1.5 text-[10.5px] font-medium uppercase tracking-[.14em] text-[var(--ghost)]">Paid agent</div>
          <NavItem item={{ page: 'pay', label: 'Pay and run', icon: CreditCard }} />
          <NavItem item={{ page: 'agent', label: 'Funding and jobs', icon: Wallet }} />
        </nav>
        <HealthLine />
      </aside>
      <div className="flex-1 min-w-0 relative">
        <div className="console-sky" aria-hidden />
        <main className="relative z-[1] max-w-[1400px] mx-auto px-8 py-8">{children}</main>
      </div>
    </div>
  )
}
