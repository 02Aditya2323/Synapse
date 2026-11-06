export const EFFECT = {
  pure: { color: '#8f8e86', text: 'pure' },
  read: { color: '#3987e5', text: 'read' },
  write: { color: '#fab219', text: 'write' },
  external: { color: '#ec835a', text: 'external' },
  irreversible: { color: '#d03b3b', text: 'irreversible' },
}

export function EffectBadge({ effect }) {
  const e = EFFECT[effect] || EFFECT.pure
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-ink-2">
      <span className="inline-block w-2 h-2 rounded-full" style={{ background: e.color }} />
      {e.text}
    </span>
  )
}

const DECISION = {
  APPROVE: { cls: 'border-good/60 bg-good/10', icon: '✓', text: 'APPROVE' },
  BLOCK: { cls: 'border-critical/60 bg-critical/10', icon: '✕', text: 'BLOCK' },
  'NO CHANGE': { cls: 'border-line bg-surface-2', icon: '–', text: 'NO CHANGE' },
}

export function Decision({ decision, size = 'sm' }) {
  const d = DECISION[decision] || DECISION['NO CHANGE']
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border font-semibold tracking-wide ${d.cls} ${size === 'lg' ? 'px-3 py-1 text-sm' : 'px-2 py-0.5 text-[11px]'}`}>
      <span aria-hidden>{d.icon}</span>
      {d.text}
    </span>
  )
}

const STATUS = {
  unchanged: { color: '#8f8e86', text: 'unchanged' },
  fixed: { color: '#0ca30c', text: 'fixed' },
  broken: { color: '#d03b3b', text: 'broken' },
  crashed: { color: '#fab219', text: 'crashed' },
  unstable: { color: '#ec835a', text: 'unstable' },
  unknown: { color: '#8f8e86', text: 'unknown' },
}

export function Status({ status }) {
  const s = STATUS[status] || STATUS.unknown
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span className="inline-block w-2 h-2 rounded-sm" style={{ background: s.color }} />
      {s.text}
    </span>
  )
}

export function Pass({ v }) {
  if (v == null) return <span className="text-ink-3">—</span>
  return v ? <span className="text-good">pass</span> : <span className="text-critical">fail</span>
}

export function Card({ title, children, right, className = '' }) {
  return (
    <section className={`rounded-xl border border-line bg-surface-1 ${className}`}>
      {title && (
        <div className="flex items-center justify-between px-5 pt-4">
          <h3 className="text-sm font-medium text-ink-2">{title}</h3>
          {right}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}
