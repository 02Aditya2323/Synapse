// Every formatter returns "—" for null/undefined. A value the backend did not measure is
// not zero, and the UI must never make it look like one (docs/CORRECTNESS.md).

export const DASH = '—';

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export function ms(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  if (Math.abs(v) >= 10_000) return `${(v / 1000).toFixed(1)} s`;
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${Math.round(v)} ms`;
}

export function int(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return Math.round(v).toLocaleString('en-US');
}

export function usd(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  if (v === 0) return '$0';
  const a = Math.abs(v);
  const digits = a >= 1 ? 2 : a >= 0.01 ? 4 : 5;
  return `${v < 0 ? '−' : ''}$${a.toFixed(digits)}`;
}

export function pct(v: number | null | undefined, digits = 1, signed = false): string {
  if (!isNum(v)) return DASH;
  const s = Math.abs(v).toFixed(digits);
  if (!signed) return `${v < 0 ? '−' : ''}${s}%`;
  return `${v > 0 ? '+' : v < 0 ? '−' : '±'}${s}%`;
}

/** A 0..1 rate as a percentage. */
export function rate(v: number | null | undefined, digits = 0): string {
  if (!isNum(v)) return DASH;
  return `${(v * 100).toFixed(digits)}%`;
}

export function signedMs(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return `${v > 0 ? '+' : v < 0 ? '−' : '±'}${ms(Math.abs(v))}`;
}

export function signedInt(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return `${v > 0 ? '+' : v < 0 ? '−' : '±'}${int(Math.abs(v))}`;
}

export function signedUsd(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return `${v > 0 ? '+' : v < 0 ? '−' : '±'}${usd(Math.abs(v))}`;
}

export function x(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return `${v.toFixed(v >= 10 ? 0 : 1)}×`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toISOString().replace('T', ' ').slice(0, 16) + 'Z';
}

/** The same moment as `when`, for reading: "31 Oct, 22:37" in the viewer's time. */
export function whenShort(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  const day = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  return `${day}, ${time}`;
}

export function preview(v: unknown, max = 80): string {
  if (v === null || v === undefined) return DASH;
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}
