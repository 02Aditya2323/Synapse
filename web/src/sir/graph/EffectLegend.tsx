import { EFFECTS } from '../types';
import { EFFECT_META } from './effects';

/**
 * The legend is the product's core idea in one picture: data edges say what the optimizer
 * may move, effect edges say what it must not. It is deliberately large and always visible.
 */
export function EffectLegend({ critical = false }: { compact?: boolean; critical?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px]">
      <div className="flex items-center gap-2" title="A value flows from one node to the next. Parallelism may reorder around it.">
        <svg width="34" height="10" aria-hidden>
          <line x1="0" y1="5" x2="28" y2="5" stroke="var(--muted)" strokeWidth="2" />
          <path d="M28 1 L34 5 L28 9 Z" fill="var(--muted)" />
        </svg>
        <span className="text-[var(--text)]">Data</span>
      </div>
      <div className="flex items-center gap-2" title="An ordering the effect lattice requires. Never reordered.">
        <svg width="34" height="10" aria-hidden>
          <line x1="0" y1="5" x2="28" y2="5" stroke="var(--ghost)" strokeWidth="1.75" strokeDasharray="5 4" />
          <path d="M28 1 L34 5 L28 9" fill="none" stroke="var(--ghost)" strokeWidth="1.5" />
        </svg>
        <span className="text-[var(--text)]">Effect order</span>
      </div>
      {critical && (
        <div className="flex items-center gap-2">
          <svg width="34" height="10" aria-hidden>
            <line x1="0" y1="5" x2="34" y2="5" stroke="var(--crit)" strokeWidth="2.5" />
          </svg>
          <span className="text-[var(--text)]">Critical path</span>
        </div>
      )}
      <span className="w-px h-4 bg-[var(--line-strong)]" />
      {EFFECTS.map((e) => (
        <div key={e} className="flex items-center gap-1.5" title={`${e}: ${EFFECT_META[e].rule}`}>
          <span
            className={`w-3 h-3 rounded-[3px] ${e === 'irreversible' ? 'fx-irreversible-hatch' : ''}`}
            style={{
              background: e === 'irreversible' ? undefined : EFFECT_META[e].color,
              border: e === 'irreversible' ? '2px solid var(--fx-irreversible)' : undefined,
            }}
          />
          <span className="text-[12px]" style={{ color: EFFECT_META[e].color }}>
            {e}
          </span>
        </div>
      ))}
    </div>
  );
}
