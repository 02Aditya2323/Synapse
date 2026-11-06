export const EFFECT = {
  pure: { color: 'var(--fx-pure)', text: 'pure' },
  read: { color: 'var(--fx-read)', text: 'read' },
  write: { color: 'var(--fx-write)', text: 'write' },
  external: { color: 'var(--fx-external)', text: 'external' },
  irreversible: { color: 'var(--fx-irreversible)', text: 'irreversible' },
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
  unchanged: { color: 'var(--faint)', text: 'unchanged' },
  fixed: { color: 'var(--ok)', text: 'fixed' },
  broken: { color: 'var(--bad)', text: 'broken' },
  crashed: { color: 'var(--warn)', text: 'crashed' },
  unstable: { color: '#fb923c', text: 'unstable' },
  unknown: { color: 'var(--faint)', text: 'unknown' },
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

export function Card({ title, children, right, className = '', eyebrow }) {
  return (
    <section className={`panel ${className}`}>
      {(title || eyebrow) && (
        <div className="flex items-center justify-between gap-4 px-5 pt-4">
          <div>
            {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
            {title && <h3 className="text-[14px] font-semibold text-[var(--text-strong)]">{title}</h3>}
          </div>
          {right}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

export function PageTitle({ eyebrow, title, children }) {
  return (
    <div className="max-w-4xl">
      {eyebrow && <div className="eyebrow mb-3">{eyebrow}</div>}
      <h1 className="console-title text-[34px] text-[var(--text-strong)]">{title}</h1>
      {children && <div className="text-[14.5px] text-[var(--muted)] mt-3 leading-relaxed">{children}</div>}
    </div>
  )
}

export function Code({ children }) {
  return <pre className="font-mono text-[12.5px] leading-relaxed text-[var(--text)] bg-[var(--bg)] border border-[var(--line)] rounded-[8px] p-3 overflow-x-auto whitespace-pre">{children}</pre>
}
