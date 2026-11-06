export const pct = (x, digits = 0) => (x == null ? '—' : `${(x * 100).toFixed(digits)}%`)

export function money(v) {
  if (v == null) return '—'
  if (v === 0) return '$0'
  if (Math.abs(v) < 0.01) return `$${v.toFixed(6)}`
  return `$${v.toFixed(4)}`
}

export function value(v, unit) {
  if (v == null) return '—'
  if (unit === 'usd') return money(v)
  if (unit === 's') return `${v.toFixed(2)} s`
  if (unit === 'tokens') return Math.round(v).toLocaleString()
  if (unit === 'calls') return v.toFixed(v < 20 ? 2 : 0)
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

/** "77% lower" / "14% higher", from a fractional reduction. */
export function changeText(m) {
  if (m.change == null) return m.before === m.after ? 'same' : '—'
  if (Math.abs(m.change) < 0.0005) return 'same'
  const lower = m.change > 0
  return `${Math.abs(m.change * 100).toFixed(0)}% ${lower ? 'lower' : 'higher'}`
}

/** good | bad | neutral, for a metric's change. */
export function changeTone(m) {
  if (m.change == null || Math.abs(m.change) < 0.0005) return 'neutral'
  const better = m.lower_is_better ? m.change > 0 : m.change < 0
  return better ? 'good' : 'bad'
}
