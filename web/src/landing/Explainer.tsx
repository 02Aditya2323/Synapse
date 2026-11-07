// "What it does", for someone who has never heard the word compiler. A before/after timeline
// planned from one workflow's measured step times, then the three ideas in plain words.
import { useLayoutEffect, useRef, useState } from 'react';
import { motion, useAnimationFrame, useInView } from 'motion/react';
import { Layers, Repeat2, ShieldCheck } from 'lucide-react';
import { SectionLabel } from './sections';
import { EASE, Reveal, rootScale } from '../lib/motion';
import { EFFECT_HEX } from './heroGraph';

interface PlannedBar { label: string; lane: number; start: number; end: number; effect: keyof typeof EFFECT_HEX }
const ms = (v: number) => `${(v / 1000).toFixed(1)} s`;

/* Measured, not drawn: the mean time of each step over the supplier-check agent's 20 held-out requests on 0G
   (reports/supplier_zg-main_2026-11-06T0719.json, "test as written 1" and "test optimized"). As written, the steps
   run one after another; optimized, the four lookups start together, the policy summary comes from the cache, and
   decide starts when the slowest lookup ends. */
const SECOND = 1000;
const PLAN: { name: string; before: PlannedBar[]; after: PlannedBar[]; lanes: number } = (() => {
  const steps: [string, number, keyof typeof EFFECT_HEX][] = [
    ['load_policy', 19.5, 'pure'], ['get_quotes', 11.9, 'read'], ['screen_reviews', 5.7, 'read'],
    ['check_certificates', 14.1, 'read'], ['decide', 17.6, 'pure'],
  ];
  let clock = 0;
  const before = steps.map(([label, s, effect]) => {
    const b = { label, lane: 0, start: clock, end: clock + s * SECOND, effect };
    clock = b.end;
    return b;
  });
  const after: PlannedBar[] = [
    { label: 'get_quotes', lane: 0, start: 0, end: 6.7 * SECOND, effect: 'read' },
    { label: 'screen_reviews', lane: 1, start: 0, end: 5.7 * SECOND, effect: 'read' },
    { label: 'check_certificates', lane: 2, start: 0, end: 2.8 * SECOND, effect: 'read' },
    { label: 'load_policy · cached', lane: 3, start: 0, end: 0.3 * SECOND, effect: 'pure' },
    { label: 'decide', lane: 0, start: 6.7 * SECOND, end: 23.4 * SECOND, effect: 'pure' },
  ];
  return { name: 'the supplier-check agent, mean of 20 requests on 0G', before, after, lanes: 4 };
})();

interface Bar { label: string; lane: number; start: number; end: number; color: string }

/** Seconds on screen for the slower run, so a demo graph and a real model play at the same pace. */
const PLAY_S = 8.8;
const SPAN = 10; // time units across the track
const LOOP = 11; // seconds per loop, including a pause at the end

/** A label is drawn only where it fits whole; a clipped word ("sav") reads as a different step. */
const LABEL_PX = (s: string) => s.length * 7.1 + 18;

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function Track({ title, bars, lanes, t, total, accent }: { title: string; bars: Bar[]; lanes: number; t: number; total: number; accent?: boolean }) {
  const done = t >= total;
  const [track, width] = useWidth<HTMLDivElement>();
  return (
    <div>
      <div className="flex items-baseline justify-between mb-2.5">
        <span className={accent ? 'text-[0.9375rem] font-semibold text-[var(--ink)]' : 'text-[0.9375rem] text-[var(--ink-dim)]'}>{title}</span>
        <span className={accent ? 'text-[0.875rem] text-[var(--accent)]' : 'text-[0.875rem] text-[var(--ink-dim)]'}>
          {done ? (accent ? 'Done' : 'Done') : 'Running…'}
        </span>
      </div>
      <div ref={track} className="relative rounded-[0.75rem] border border-white/[.08] bg-black/30" style={{ height: lanes * 38 + 16 }}>
        {bars.map((b, i) => {
          const fill = Math.max(0, Math.min(1, (t - b.start) / (b.end - b.start)));
          const fits = width > 0 && ((b.end - b.start) / SPAN) * (width - 16) - 4 >= LABEL_PX(b.label) * rootScale();
          return (
            <div
              key={i}
              title={b.label}
              className="absolute rounded-[0.4375rem] overflow-hidden border"
              style={{
                left: `calc(${(b.start / SPAN) * 100}% + 8px)`,
                width: `calc(${((b.end - b.start) / SPAN) * 100}% - 4px)`,
                top: 8 + b.lane * 38,
                height: 30,
                borderColor: `${b.color}55`,
                background: `${b.color}10`,
              }}
            >
              <div className="absolute inset-y-0 left-0" style={{ width: `${fill * 100}%`, background: `${b.color}38` }} />
              {fits && <span className="relative px-2 leading-[1.875rem] text-[0.7812rem] text-[var(--ink)] whitespace-nowrap">{b.label}</span>}
            </div>
          );
        })}
        <div className="absolute inset-y-0 w-px bg-[var(--accent)]" style={{ left: `calc(${(Math.min(t, total) / SPAN) * 100}% + 8px)`, boxShadow: '0 0 12px var(--accent)' }} />
      </div>
    </div>
  );
}

function Timeline() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { margin: '-15% 0px' });
  const [t, setT] = useState(0);
  const acc = useRef(0);
  const plan = PLAN;
  const hero = { name: PLAN.name };
  const offline = false;
  useAnimationFrame((_, delta) => {
    if (!inView) return;
    acc.current = (acc.current + delta / 1000) % LOOP;
    setT(acc.current);
  });
  const serialMs = plan ? Math.max(...plan.before.map((b) => b.end)) : 0;
  const plannedMs = plan ? Math.max(...plan.after.map((b) => b.end)) : 0;
  const scale = serialMs ? PLAY_S / serialMs : 1;
  const toBars = (bars: PlannedBar[]): Bar[] =>
    bars.map((b) => ({ label: b.label, lane: b.lane, start: b.start * scale, end: b.end * scale, color: EFFECT_HEX[b.effect] }));
  return (
    <div ref={ref} className="rounded-[1.25rem] border border-white/10 bg-[#080a0f] p-6 sm:p-8">
      <div className="flex items-center justify-between mb-6">
        <span className="text-[0.875rem] text-[var(--ink-dim)]">{hero ? `One run of ${hero.name}` : 'One run of a workflow'}</span>
        <span className="text-[0.7812rem] px-2.5 h-6 inline-flex items-center rounded-full border border-white/15 text-[var(--ink-dim)]">From measured step times</span>
      </div>
      {plan ? (
        <>
          <div className="space-y-7">
            <Track title={`As written · ${ms(serialMs)}`} bars={toBars(plan.before)} lanes={1} t={t} total={serialMs * scale} />
            <Track title={`With Synapse · ${ms(plannedMs)}`} bars={toBars(plan.after)} lanes={plan.lanes} t={t} total={plannedMs * scale} accent />
          </div>
          <p className="mt-6 text-[0.9062rem] text-[var(--ink-dim)] leading-relaxed">
            Each bar is a step's measured time. Optimized, a step starts as soon as what it really depends on has finished, and a pure
            step already answered is served from the cache. Watch it happen on a real run in the live demo.
          </p>
        </>
      ) : (
        <p className="text-[0.9375rem] text-[var(--ink-dim)] leading-relaxed">
          {offline ? 'The compiler is not running, so there are no measured steps to show.' : 'No measured steps yet: synapse demo --workflow all'}
        </p>
      )}
    </div>
  );
}

const IDEAS = [
  {
    icon: Layers,
    title: 'Run steps side by side',
    body: 'Steps that do not depend on each other run at the same time instead of waiting in line.',
  },
  {
    icon: Repeat2,
    title: 'Never do the same work twice',
    body: 'Duplicate calls are merged, and repeated questions are answered from a cache.',
  },
  {
    icon: ShieldCheck,
    title: 'Leave risky steps alone',
    body: 'Sending an email or placing an order is never repeated, reordered or skipped.',
  },
];

export function WhatItDoes() {
  return (
    <section id="what" className="relative py-28 lg:py-36">
      <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.1fr] gap-12 lg:gap-16 items-center">
          <div>
            <SectionLabel>What it does</SectionLabel>
            <Reveal>
              <h2 className="mt-5 font-display text-[clamp(2.125rem,4.4vw,3.75rem)] leading-[1.02] text-[var(--ink)]">
                Your agent waits more than it works.
              </h2>
            </Reveal>
            <Reveal delay={0.08}>
              <p className="mt-6 text-[1.125rem] leading-[1.65] text-[var(--ink-dim)] max-w-[52ch]">
                An AI agent runs a list of steps: ask a model, search the web, look up an order, send an email. Written the obvious way,
                every step waits for the one before it, even when it does not need to.
              </p>
              <p className="mt-4 text-[1.125rem] leading-[1.65] text-[var(--ink-dim)] max-w-[52ch]">
                <span className="text-[var(--ink)]">Synapse reads the agent, works out what really depends on what, and rebuilds it to run
                faster and cheaper.</span> Then it tests the new version against the old one, and hands it back to you as code.
              </p>
            </Reveal>
          </div>
          <Reveal delay={0.1}>
            <Timeline />
          </Reveal>
        </div>

        <div className="mt-20 grid md:grid-cols-3 gap-5">
          {IDEAS.map((it, i) => (
            <motion.div
              key={it.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-10% 0px' }}
              transition={{ duration: 0.7, ease: EASE, delay: i * 0.08 }}
              className="spot group rounded-[1.125rem] border border-white/10 bg-white/[.02] p-7 hover:border-white/20 transition-colors"
            >
              <span className="w-12 h-12 rounded-[0.75rem] bg-[var(--accent)]/10 border border-[var(--accent)]/25 flex items-center justify-center text-[var(--accent)] group-hover:scale-105 transition-transform">
                <it.icon className="w-5 h-5" />
              </span>
              <h3 className="mt-6 text-[1.25rem] font-semibold text-[var(--ink)]">{it.title}</h3>
              <p className="mt-2 text-[1rem] leading-relaxed text-[var(--ink-dim)]">{it.body}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
