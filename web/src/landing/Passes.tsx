// The twelve passes as cards with a before → after diagram each. On wide screens the row
// scrolls sideways while the section is pinned; on narrow screens it is a normal swipe row.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { motion, useTransform } from 'motion/react';
import { ArrowRight } from 'lucide-react';
import { PASS_DIAGRAMS, PASS_TITLES } from './PassDiagrams';
import { SectionLabel } from './sections';
import { useSectionProgress } from '../lib/motion';
import { usePasses } from '../lib/queries';
import type { PassInfo } from '../types/api';

const WHEN: Record<string, string> = {
  cse: 'Both steps always give the same answer for the same input.',
  memoize: 'The stored answer cannot have gone out of date.',
  parallelize: 'Neither step needs the other’s result, and neither changes the outside world.',
  context_prune: 'Nothing the model reads depends on the part that is removed.',
  context_trim: 'Every test task still passes with the shorter messages. Off unless you turn it on.',
  downgrade: 'Every test task still passes with the cheaper model.',
  loop_cutoff: 'Nothing inside the loop changes the outside world.',
  dce: 'Nothing reads the result, and the step changes nothing outside the workflow.',
  batch: 'The service accepts batches and the calls do not affect each other.',
  speculate: 'The early work only reads, and throwing it away costs nothing.',
  constfold: 'Every input is fixed and the step only computes.',
  early_exit: 'The confidence bar was tuned on separate data, and accuracy stays within a stated limit.',
};

function useWide() {
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.matchMedia('(min-width: 1024px)').matches);
  useEffect(() => {
    const m = window.matchMedia('(min-width: 1024px)');
    const on = () => setWide(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return wide;
}

function Card({ tech, i, total }: { tech: PassInfo; i: number; total: number }) {
  const name = tech.name;
  const t = PASS_TITLES[name] ?? { title: name, what: tech.description };
  return (
    <article className="spot group relative w-[min(25rem,84vw)] shrink-0 snap-start rounded-[1.25rem] border border-white/10 bg-[#080a0f] p-6 flex flex-col hover:border-white/20 transition-colors">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[0.8125rem] text-[var(--ink-dim)]">{String(i + 1).padStart(2, '0')} / {total}</span>
        <span className="font-mono text-[0.75rem] px-2 h-6 inline-flex items-center rounded-full border border-white/10 text-[var(--ink-dim)]" title={tech.precondition}>
          {name}
        </span>
      </div>
      <h3 className="mt-5 text-[1.375rem] font-semibold leading-tight text-[var(--ink)] group-hover:text-[var(--accent)] transition-colors">{t.title}</h3>
      <p className="mt-2 text-[0.9688rem] leading-relaxed text-[var(--ink-dim)] min-h-[3rem]">{t.what}</p>
      {PASS_DIAGRAMS[name] && <div className="mt-5 rounded-[0.875rem] border border-white/[.07] bg-black/40 p-3">{PASS_DIAGRAMS[name]}</div>}
      <div className="mt-5 pt-4 border-t border-white/[.07]">
        <div className="text-[0.8125rem] font-medium text-[var(--accent)]">Only when</div>
        <p className="mt-1 text-[0.9062rem] leading-relaxed text-[var(--ink)]">{WHEN[name] ?? tech.precondition}</p>
      </div>
    </article>
  );
}

function EndCard() {
  return (
    <div className="w-[min(25rem,84vw)] shrink-0 snap-start rounded-[1.25rem] border border-dashed border-white/15 p-7 flex flex-col justify-end">
      <div className="text-[1.625rem] font-semibold leading-tight text-[var(--ink)]">Picked for your agent, not from a menu.</div>
      <p className="mt-3 text-[0.9688rem] text-[var(--ink-dim)] leading-relaxed">
        Synapse0g measures your agent first, then chooses the rewrites that save the most time and money for it.
      </p>
      <a href="#/live" className="mt-6 inline-flex items-center gap-1.5 text-[0.9375rem] text-[var(--accent)] hover:underline">
        Watch them work on a live run <ArrowRight className="w-4 h-4" />
      </a>
    </div>
  );
}

/** The order to show them in. The list itself comes from the compiler. */
const ORDER = ['parallelize', 'memoize', 'cse', 'downgrade', 'context_prune', 'context_trim', 'batch', 'dce', 'loop_cutoff', 'speculate', 'constfold', 'early_exit'];

const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen'];

function useList(): { list: PassInfo[]; error: unknown; loading: boolean } {
  const passes = usePasses();
  const rank = (name: string) => {
    const at = ORDER.indexOf(name);
    return at < 0 ? ORDER.length : at;
  };
  const list = [...(passes.data?.passes ?? [])].sort((a, b) => rank(a.name) - rank(b.name));
  return { list, error: passes.error, loading: passes.isLoading };
}

/** No list, no cards: the rewrites are read from the compiler, so with it down there is nothing to draw. */
function Missing({ error, loading }: { error: unknown; loading: boolean }) {
  return (
    <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8">
      <div className="rounded-[1.25rem] border border-dashed border-white/15 p-8 text-[1rem] text-[var(--ink-dim)] leading-relaxed max-w-[60ch]">
        {loading
          ? 'Reading the list of rewrites from the compiler…'
          : error
            ? 'The compiler is not running, so the list of rewrites cannot be read. Start it with synapse ui --demo.'
            : 'The compiler reported no rewrites.'}
      </div>
    </div>
  );
}

function Header({ count }: { count: number }) {
  return (
    <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8 mb-10 grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-6 items-end">
      <div>
        <SectionLabel>The optimizations</SectionLabel>
        <h2 className="mt-5 font-display text-[clamp(2.125rem,4.4vw,3.75rem)] leading-[1.02] text-[var(--ink)] max-w-[20ch]">
          {count ? `${WORDS[count] ?? count} ways to speed it up. Each with a safety rule.` : 'Ways to speed it up. Each with a safety rule.'}
        </h2>
      </div>
      <p className="text-[1.0625rem] leading-relaxed text-[var(--ink-dim)] max-w-[40ch]">
        A rewrite is used only where its rule holds. If Synapse0g cannot show the rule holds, it leaves the step alone.
      </p>
    </div>
  );
}

export function Passes() {
  const wide = useWide();
  const { list, error, loading } = useList();
  if (list.length === 0) {
    return (
      <section id="passes" className="relative py-28 border-t border-white/[.06]">
        <Header count={0} />
        <Missing error={error} loading={loading} />
      </section>
    );
  }
  return wide ? <PinnedRail /> : <SwipeRail />;
}

function SwipeRail() {
  const { list } = useList();
  return (
    <section id="passes" className="relative py-28 border-t border-white/[.06]">
      <Header count={list.length} />
      <div className="flex gap-4 overflow-x-auto snap-x snap-mandatory scroll-px-5 sm:scroll-px-8 px-5 sm:px-8 pb-4 [scrollbar-width:none]">
        {list.map((p, i) => (
          <Card key={p.name} tech={p} i={i} total={list.length} />
        ))}
        <EndCard />
      </div>
    </section>
  );
}

function PinnedRail() {
  const { list } = useList();
  const ref = useRef<HTMLElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const [dist, setDist] = useState(0);
  const p = useSectionProgress(ref);
  const x = useTransform(p, [0.04, 0.96], [0, -dist]);
  const measure = useCallback(() => {
    if (track.current) setDist(Math.max(0, track.current.scrollWidth - window.innerWidth));
  }, []);
  useLayoutEffect(() => {
    measure();
    addEventListener('resize', measure);
    return () => removeEventListener('resize', measure);
  }, [measure, list.length]);
  return (
    <section id="passes" ref={ref} className="relative border-t border-white/[.06]" style={{ height: `calc(100vh + ${dist}px)` }}>
      <div className="sticky top-0 h-screen overflow-hidden flex flex-col justify-center pt-16">
        <Header count={list.length} />
        <motion.div ref={track} style={{ x }} className="flex gap-5 w-max px-[max(2rem,calc((100vw-77.5rem)/2+2rem))]">
          {list.map((p, i) => (
            <Card key={p.name} tech={p} i={i} total={list.length} />
          ))}
          <EndCard />
        </motion.div>
        <div className="max-w-[77.5rem] w-full mx-auto px-5 sm:px-8 mt-8">
          <div className="h-[0.125rem] rounded-full bg-white/[.06] overflow-hidden">
            <motion.div className="h-full bg-[var(--accent)] origin-left" style={{ scaleX: p }} />
          </div>
        </div>
      </div>
    </section>
  );
}
