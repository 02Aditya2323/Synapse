// Shared console components: panels, KPIs, chips, empty and error states.
import type { ReactNode } from 'react';
import { motion } from 'motion/react';
import { clsx } from 'clsx';
import { AlertTriangle, Database, RotateCw, Terminal } from 'lucide-react';
import type { Effect } from './types';
import { effectColor } from './graph/effects';

export const EASE = [0.22, 1, 0.36, 1] as const;

/** Section and field label. `index` is accepted for older call sites and ignored. */
export function MonoLabel({ children, className }: { children: ReactNode; index?: number; className?: string }) {
  return (
    <div className={clsx('text-[13px] font-medium text-[var(--muted)] inline-flex items-center gap-1.5', className)}>
      <span>{children}</span>
    </div>
  );
}

/** A panel's title: a small accent tick, then the label in the strong ink. */
function PanelTitle({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2.5 text-[13.5px] font-semibold text-[var(--text-strong)] min-w-0">
      <span className="truncate">{children}</span>
    </div>
  );
}

export function Panel({ title, index, right, children, className, bodyClassName }: {
  title?: ReactNode; index?: number; right?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string;
}) {
  return (
    <section className={clsx('panel spot flex flex-col min-h-0', className)}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 px-6 h-14 border-b border-white/[.06] shrink-0">
          {title ? <PanelTitle>{title}</PanelTitle> : <span />}
          {right}
        </header>
      )}
      <div className={clsx('min-h-0', bodyClassName ?? 'p-6')}>{children}</div>
    </section>
  );
}

type Tone = 'default' | 'ok' | 'bad' | 'warn' | 'accent' | 'info';
const toneText: Record<Tone, string> = {
  default: 'text-[var(--text-strong)]',
  ok: 'text-[var(--ok)]',
  bad: 'text-[var(--bad)]',
  warn: 'text-[var(--warn)]',
  accent: 'text-[var(--accent)]',
  info: 'text-[var(--info)]',
};

/**
 * label + big tabular number + one line of context. `value` null/undefined renders "—".
 * Pass `num` + `fmt` instead of `value` and the number counts up to what the API sent.
 */
export function KPI({ label, value, num, fmt, context, tone = 'default', className }: {
  label: string; value?: ReactNode; num?: number | null; fmt?: (n: number) => string; context?: ReactNode; tone?: Tone; className?: string;
}) {
  return (
    <div className={clsx('panel spot relative overflow-hidden px-5 py-5 flex flex-col gap-2 [container-type:inline-size]', className)}>
      <MonoLabel>{label}</MonoLabel>
      <div className={clsx('console-title text-[clamp(17px,16cqw,30px)] whitespace-nowrap tabular-nums', toneText[tone])}>
        {fmt && num != null ? fmt(num) : (value ?? '—')}
      </div>
      {context && <div className="text-[13px] text-[var(--muted)] truncate">{context}</div>}
    </div>
  );
}

export function Button({ children, onClick, disabled, primary, className, title, type = 'button' }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; primary?: boolean; className?: string; title?: string;
  type?: 'button' | 'submit';
}) {
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        'inline-flex items-center gap-2 h-8 px-3.5 rounded-full text-[13px] border transition-[background,border-color,box-shadow,filter] duration-150 cursor-pointer active:scale-[.98] disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none',
        primary
          ? 'bg-[var(--accent)] border-[var(--accent)] text-black font-semibold hover:bg-[#fb8a3c] hover:border-[#fb8a3c]'
          : 'bg-white/[.04] border-white/[.1] text-[var(--text-strong)] hover:border-white/25 hover:bg-white/[.07]',
        className,
      )}
    >
      {children}
    </button>
  );
}

export function EffectChip({ effect }: { effect: Effect | string }) {
  const c = effectColor(effect);
  return (
    <span
      className="inline-flex items-center gap-1.5 font-mono text-[11px] px-1.5 h-5 rounded-[4px] border"
      style={{ color: c, borderColor: `color-mix(in srgb, ${c} 45%, transparent)` }}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: c }} />
      {effect}
    </span>
  );
}

/** Honest empty state: what is missing, and either an action or the command that fills it. */
export function EmptyState({ title, description, command, action, className }: {
  title: string; description: ReactNode; command?: string; endpoint?: string; action?: ReactNode; className?: string;
}) {
  return (
    <div className={clsx('panel p-8 flex flex-col items-center text-center max-w-lg mx-auto', className)}>
      <div className="w-10 h-10 rounded-full bg-[var(--raised)] border border-[var(--line-strong)] flex items-center justify-center text-[var(--faint)] mb-4">
        <Database className="w-5 h-5" strokeWidth={1.75} />
      </div>
      <h3 className="text-[15px] font-semibold text-[var(--text-strong)] mb-1.5">{title}</h3>
      <p className="text-[13.5px] text-[var(--muted)] leading-relaxed">{description}</p>
      {action && <div className="mt-5">{action}</div>}
      {command && (
        <div className="mt-5 w-full bg-[var(--bg)] border border-[var(--line)] rounded-[var(--radius-sm)] px-3 py-2.5 text-left flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5 shrink-0 text-[var(--faint)]" />
          <code className="text-[12px] text-[var(--text)] select-all break-all">{command}</code>
        </div>
      )}
    </div>
  );
}

/** The API's own sentence, in a red-bordered panel, with retry. Never a silent fallback. */
export function ErrorPanel({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const message = error instanceof Error ? error.message : String(error);
  const code = (error as { code?: string } | null)?.code;
  return (
    <div
      className={clsx(
        'rounded-[var(--radius)] border border-[var(--bad)]/40 bg-[var(--bad-bg)] p-4 flex items-start gap-3 max-w-2xl',
        className,
      )}
    >
      <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-[var(--bad)]" strokeWidth={1.75} />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] text-[var(--text-strong)] break-words">{message}</div>
        {code && <div className="font-mono text-[11px] text-[var(--faint)] mt-1">{code}</div>}
      </div>
      {onRetry && (
        <Button onClick={onRetry} className="shrink-0">
          <RotateCw className="w-3.5 h-3.5" /> Retry
        </Button>
      )}
    </div>
  );
}

export function Loading({ label = 'Loading', className }: { label?: string; className?: string }) {
  return (
    <div className={clsx('flex flex-col gap-4', className)}>
      <div className="flex items-center gap-2 text-[13px] text-[var(--faint)] font-mono">
        <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-rec-pulse" />
        {label}…
      </div>
      <div className="grid grid-cols-3 gap-3 max-w-[900px]" aria-hidden>
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-20 rounded-[var(--radius)] shimmer" style={{ animationDelay: `${i * 120}ms` }} />
        ))}
        <div className="col-span-3 h-40 rounded-[var(--radius)] shimmer" style={{ animationDelay: '360ms' }} />
      </div>
    </div>
  );
}

/** Wraps a query: loading / error / empty / data. */
export function QueryView<T>({ q, empty, children, loadingLabel }: {
  q: { data?: T; error: unknown; isLoading: boolean; refetch: () => unknown };
  empty?: (d: T) => ReactNode | null;
  children: (d: T) => ReactNode;
  loadingLabel?: string;
}) {
  if (q.isLoading) return <Loading label={loadingLabel} className="p-6" />;
  if (q.error) return <ErrorPanel error={q.error} onRetry={() => q.refetch()} className="m-6" />;
  if (q.data === undefined) return null;
  const e = empty?.(q.data);
  if (e) return <>{e}</>;
  return <>{children(q.data)}</>;
}

/** APPROVE (green) / BLOCK (warning). A BLOCK is a verdict, not an error. */
export function VerdictBlock({ decision, reason, children }: { decision: string; reason?: string; children?: ReactNode }) {
  const approve = decision.toUpperCase() === 'APPROVE';
  const c = approve ? 'var(--ok)' : 'var(--bad)';
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={approve ? { opacity: 1 } : { opacity: 1, x: [0, -6, 6, -4, 3, 0] }}
      transition={{ duration: approve ? 0.25 : 0.45, delay: approve ? 0 : 0.28 }}
      className={clsx(
        'relative overflow-hidden rounded-[var(--radius)] border p-6',
        approve ? 'border-[var(--ok)]/40 bg-[var(--ok-bg)]' : 'border-[var(--bad)]/50 bg-[var(--bad-bg)]',
      )}
    >
      {/* the flash when the verdict lands */}
      <motion.div
        className="absolute inset-0 pointer-events-none"
        style={{ background: c }}
        initial={{ opacity: 0 }}
        animate={{ opacity: [0, 0.22, 0] }}
        transition={{ duration: 0.55, delay: 0.25 }}
      />
      <div
        aria-hidden
        className="absolute -right-6 -top-10 font-display text-[150px] leading-none select-none pointer-events-none"
        style={{ color: 'transparent', WebkitTextStroke: `1px ${c}`, opacity: 0.18 }}
      >
        {decision.toUpperCase()}
      </div>
      <div className="relative flex items-center gap-4">
        <motion.span
          className="inline-flex items-center px-4 py-1.5 border-[3px] rounded-[8px] font-display text-[44px] leading-none"
          style={{ color: c, borderColor: c, boxShadow: `0 0 40px -6px ${c}`, textShadow: `0 0 24px ${c}` }}
          initial={{ scale: 2.4, rotate: approve ? 0 : -10, opacity: 0 }}
          animate={{ scale: 1, rotate: approve ? 0 : -3, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 460, damping: 20, mass: 1 }}
        >
          {decision.toUpperCase()}
        </motion.span>
        <span className="text-[13px] text-[var(--muted)]">Firewall verdict</span>
      </div>
      {reason && (
        <motion.p
          className="relative mt-4 text-[15px] text-[var(--text-strong)]"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.45, duration: 0.4, ease: EASE }}
        >
          {reason}
        </motion.p>
      )}
      <motion.div className="relative" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.6, duration: 0.4 }}>
        {children}
      </motion.div>
    </motion.div>
  );
}

export function FixtureBadge() {
  return null;
  return (
    <span
      className="text-[12px] font-medium px-2 h-6 inline-flex items-center rounded-full border border-[var(--warn)]/50 text-[var(--warn)] bg-[var(--warn-bg)]"
      title="VITE_USE_FIXTURES=1: every number on this page comes from fixtures/, not the live API"
    >
      Fixture data
    </span>
  );
}

export function Samples({ n, what = 'samples' }: { n?: number | null; what?: string }) {
  return (
    <span className="font-mono text-[11px] text-[var(--faint)]">
      n={n ?? '—'} {what}
    </span>
  );
}
