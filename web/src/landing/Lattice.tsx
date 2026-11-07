// The effect lattice as an exploded stack of layers. On wide screens the section pins and
// each scroll fifth lifts the next layer; the tabs jump straight to one. On narrow screens
// it is a normal section and the tabs drive it.
import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useMotionValueEvent } from 'motion/react';
import { clsx } from 'clsx';
import { Check, Lock, Minus, X as XIcon } from 'lucide-react';
import { TIERS } from './content';
import { useLattice } from '../lib/queries';
import { EFFECT_HEX } from './heroGraph';
import { SectionLabel } from './sections';
import { EASE, scrollToY, useSectionProgress } from '../lib/motion';

const PLAIN: Record<string, { name: string; line: string }> = {
  pure: { name: 'Just computes', line: 'Same input, same output. Nothing outside the agent changes.' },
  read: { name: 'Looks things up', line: 'Reads the outside world, which can change between calls.' },
  write: { name: 'Keeps notes', line: 'Changes the agent’s own memory or scratchpad.' },
  external: { name: 'Changes the world', line: 'Sends an email, posts to an API, writes a file.' },
  irreversible: { name: 'Cannot be undone', line: 'Places an order, charges a card, deletes a record.' },
};

/** The rule's own name for a conditional cell, in this page's words. */
const COND_NOTE: Record<string, string> = {
  'read requires staleness_s': 'while fresh',
  'disjoint writes': 'separate notes only',
  'determinism!=stochastic': 'not random samples',
  'nothing reads its keys': 'if nothing reads it',
};

const DO: Record<string, string> = {
  CSE: 'Merge duplicates',
  MEMOIZE: 'Remember answers',
  REORDER: 'Change the order',
  PARALLELIZE: 'Run side by side',
  BATCH: 'Bundle requests',
  SPECULATE: 'Start early',
  ELIDE: 'Skip if unused',
};

function useWide() {
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.matchMedia('(min-width: 1024px) and (min-height: 700px)').matches);
  useEffect(() => {
    const m = window.matchMedia('(min-width: 1024px) and (min-height: 700px)');
    const on = () => setWide(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return wide;
}

/* -------------------------------------------------------------- the pieces */

function Stack({ tier, spread }: { tier: number; spread: number }) {
  return (
    <div className="relative h-full min-h-[18.75rem] [perspective:100rem]">
      <div
        className="absolute left-1/2 top-1/2 w-[min(27.5rem,78%)] aspect-[1.55] [transform-style:preserve-3d]"
        style={{ transform: 'translate(-50%,-38%) rotateX(58deg) rotateZ(-36deg)' }}
      >
        {TIERS.map((x, i) => {
          const on = i === tier;
          const c = EFFECT_HEX[x.effect];
          const z = (4 - i) * spread + (on ? 30 : 0);
          return (
            <div
              key={x.effect}
              className={clsx('absolute inset-0 rounded-[0.75rem] border transition-[transform,opacity,box-shadow,border-color] duration-700', x.effect === 'irreversible' && 'fx-irreversible-hatch')}
              style={{
                transform: `translateZ(${z}px)`,
                borderColor: on ? c : `${c}45`,
                background: `linear-gradient(135deg, ${c}${on ? '2a' : '0d'}, rgba(7,9,13,.94) 70%)`,
                boxShadow: on ? `0 0 70px -10px ${c}, inset 0 0 40px -20px ${c}` : 'none',
                opacity: i < tier ? 0.3 : 1,
                transitionTimingFunction: 'cubic-bezier(.22,1,.36,1)',
              }}
            >
              <div
                className="absolute inset-3 rounded-[0.5rem] opacity-60"
                style={{ backgroundImage: `linear-gradient(${c}22 1px, transparent 1px), linear-gradient(90deg, ${c}22 1px, transparent 1px)`, backgroundSize: '18px 18px' }}
              />
              {on && <div className="absolute inset-0 rounded-[0.75rem] overflow-hidden"><div className="lattice-sweep absolute inset-y-0 w-1/3" style={{ background: `linear-gradient(90deg, transparent, ${c}33, transparent)` }} /></div>}
              <div className="absolute left-4 top-3 text-[0.8125rem] font-medium" style={{ color: c }}>
                {PLAIN[x.effect].name}
              </div>
              {x.effect === 'irreversible' && <Lock className="absolute right-4 top-3 w-4 h-4" style={{ color: c }} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Tabs({ tier, onPick }: { tier: number; onPick: (i: number) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5 p-1.5 rounded-[0.875rem] border border-white/10 bg-black/40 w-fit" role="tablist" aria-label="Kinds of step">
      {TIERS.map((x, i) => {
        const on = i === tier;
        const c = EFFECT_HEX[x.effect];
        return (
          <button
            key={x.effect}
            role="tab"
            aria-selected={on}
            onClick={() => onPick(i)}
            className={clsx('h-9 px-3.5 rounded-[0.625rem] text-[0.875rem] transition-colors cursor-pointer flex items-center gap-2', on ? 'text-black font-medium' : 'text-[var(--ink-dim)] hover:text-[var(--ink)]')}
            style={on ? { background: c } : undefined}
          >
            {!on && <span className="w-2 h-2 rounded-full" style={{ background: c }} />}
            {PLAIN[x.effect].name}
          </button>
        );
      })}
    </div>
  );
}

function Detail({ tier }: { tier: number }) {
  const t = TIERS[tier];
  const c = EFFECT_HEX[t.effect];
  const lattice = useLattice();
  const TRANSFORMS = lattice.data?.transforms ?? [];
  const legal = lattice.data?.tiers.find((x) => x.effect === t.effect)?.legal ?? {};
  const allowed = TRANSFORMS.filter((x) => legal[x]?.status !== 'no').length;
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={t.effect}
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -14 }}
        transition={{ duration: 0.4, ease: EASE }}
      >
        <div className="flex items-baseline gap-3 text-[0.875rem] text-[var(--ink-dim)]">
          <span>
            Layer {tier + 1} of 5
          </span>
          <span className="font-mono" style={{ color: c }}>
            {t.effect}
          </span>
        </div>
        <div className="mt-2 font-display leading-[.95] text-[clamp(2.25rem,4.6vw,4.5rem)]" style={{ color: c, textShadow: `0 0 50px ${c}40` }}>
          {PLAIN[t.effect].name}
        </div>
        <p className="mt-4 text-[clamp(1.0625rem,1.4vw,1.25rem)] leading-relaxed text-[var(--ink)] max-w-[44ch]">{PLAIN[t.effect].line}</p>
        {TRANSFORMS.length > 0 && <div className="mt-6 flex items-center gap-3 text-[0.875rem] text-[var(--ink-dim)]">
          <span>Allowed rewrites</span>
          <div className="flex-1 max-w-[13.75rem] h-1.5 rounded-full bg-white/[.06] overflow-hidden">
            <motion.div className="h-full rounded-full" style={{ background: c }} initial={{ width: 0 }} animate={{ width: `${(allowed / TRANSFORMS.length) * 100}%` }} transition={{ duration: 0.8, ease: EASE }} />
          </div>
          <span className="text-[var(--ink)]">
            {allowed} of {TRANSFORMS.length}
          </span>
        </div>}
        <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-[36.25rem]">
          {TRANSFORMS.map((x, k) => {
            const s = legal[x]?.status ?? 'no';
            return (
              <motion.div
                key={x}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.05 * k, duration: 0.3 }}
                className="flex items-center gap-3 h-11 px-4 rounded-[0.625rem] border"
                style={{ borderColor: s === 'no' ? 'rgba(255,255,255,.07)' : `${c}66`, background: s === 'yes' ? `${c}16` : 'transparent' }}
              >
                {s === 'yes' && <Check className="w-4 h-4 shrink-0" style={{ color: c }} strokeWidth={3} />}
                {s === 'cond' && <Minus className="w-4 h-4 shrink-0" style={{ color: c }} strokeWidth={3} />}
                {s === 'no' && <XIcon className="w-4 h-4 shrink-0 text-[var(--ghost)]" strokeWidth={2.5} />}
                <span className={clsx('text-[0.9375rem] whitespace-nowrap', s === 'no' ? 'text-[var(--ghost)]' : 'text-[var(--ink)]')}>{DO[x]}</span>
                {s === 'cond' && <span className="ml-auto pl-2 text-[0.7812rem] text-right leading-tight" style={{ color: c }}>{COND_NOTE[legal[x]?.why ?? ''] ?? legal[x]?.why}</span>}
              </motion.div>
            );
          })}
        </div>
        <p className="mt-6 text-[1rem] leading-relaxed border-l-2 pl-4 text-[var(--ink)] max-w-[48ch]" style={{ borderColor: c }}>
          {lattice.isError ? 'The compiler is not running, so its rules cannot be read.' : t.verdict}
        </p>
      </motion.div>
    </AnimatePresence>
  );
}

function Heading() {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-6 items-end">
      <div>
        <SectionLabel>Safety</SectionLabel>
        <h2 className="mt-5 font-display text-[clamp(2rem,4vw,3.5rem)] leading-[1.02] text-[var(--ink)]">Faster, never riskier.</h2>
      </div>
      <p className="text-[1.0625rem] leading-relaxed text-[var(--ink-dim)] max-w-[46ch]">
        Every step is labelled by what it can do to the world. The deeper the layer, the less Synapse0g may touch it: an irreversible step is flagged and never run while optimizing.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- the section */

export function Lattice() {
  return useWide() ? <Pinned /> : <Static />;
}

function Pinned() {
  const ref = useRef<HTMLElement>(null);
  const p = useSectionProgress(ref);
  const [tier, setTier] = useState(0);
  const [prog, setProg] = useState(0);
  useMotionValueEvent(p, 'change', (v) => {
    // spread only changes over the first third; quantize so scrolling does not re-render every frame
    const q = Math.round(Math.min(1, v * 3) * 30) / 30;
    setProg((c) => (c === q ? c : q));
    const t = Math.min(4, Math.max(0, Math.floor(v * 5)));
    setTier((c) => (c === t ? c : t));
  });
  const spread = 20 + prog * 44;
  const goTo = (i: number) => {
    const el = ref.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    const span = el.offsetHeight - window.innerHeight;
    scrollToY(top + span * ((i + 0.5) / 5), 1.1);
  };
  return (
    <section id="safety" ref={ref} className="relative h-[420vh] border-t border-white/[.06]">
      <div className="sticky top-0 h-screen overflow-hidden pt-24 pb-10">
        <div aria-hidden className="absolute inset-0 pointer-events-none transition-[background] duration-700" style={{ background: `radial-gradient(ellipse 45% 55% at 28% 58%, ${EFFECT_HEX[TIERS[tier].effect]}12, transparent 70%)` }} />
        <div className="relative h-full max-w-[77.5rem] mx-auto px-5 sm:px-8 flex flex-col">
          <Heading />
          <div className="relative z-10 mt-6">
            <Tabs tier={tier} onPick={goTo} />
          </div>
          <div className="relative z-0 flex-1 min-h-0 grid grid-cols-[1fr_1.1fr] gap-10 items-center">
            <Stack tier={tier} spread={spread} />
            <Detail tier={tier} />
          </div>
        </div>
      </div>
    </section>
  );
}

function Static() {
  const [tier, setTier] = useState(4);
  return (
    <section id="safety" className="relative py-28 border-t border-white/[.06]">
      <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8">
        <Heading />
        <div className="mt-8">
          <Tabs tier={tier} onPick={setTier} />
        </div>
        <div className="mt-8 h-[20rem] overflow-hidden">
          <Stack tier={tier} spread={42} />
        </div>
        <Detail tier={tier} />
      </div>
    </section>
  );
}
