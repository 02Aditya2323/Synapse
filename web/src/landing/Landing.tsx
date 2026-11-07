import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useMotionValueEvent, useTransform } from 'motion/react';
import { clsx } from 'clsx';
import { ArrowDown, ArrowRight, BarChart3, Code2, Coins, Cpu, FileCode2, Menu, Play, Volume2, VolumeX, X } from 'lucide-react';
import { setSound, sound, useSound, useSoundBlocked } from '../lib/sound';
import type { HeroInput } from './HeroSilicon';
import { Glyph, SectionLabel } from './sections';
import { WhatItDoes } from './Explainer';
import { Passes } from './Passes';
import { Lattice } from './Lattice';
import { EASE, Pill, Reveal, ScrollProgress, SplitReveal, scrollToId, scrollToY, useLenis, useSectionProgress } from '../lib/motion';

const HeroSilicon = lazy(() => import('./HeroSilicon'));

const SECTIONS = [
  { id: 'what', label: 'What it does' },
  { id: 'optimizer', label: 'The optimizer agent' },
  { id: 'demo', label: 'Live demo' },
  { id: 'safety', label: 'Safety' },
  { id: 'passes', label: 'Optimizations' },
  { id: 'results', label: 'Results' },
];
const IDS = SECTIONS.map((s) => s.id);

function useActiveSection(ids: string[]) {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    let frame = 0;
    const pick = () => {
      frame = 0;
      const line = window.innerHeight * 0.45;
      let on: string | null = null;
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= line) on = id;
      }
      const last = on && document.getElementById(on);
      if (last && last.getBoundingClientRect().bottom < line) on = null;
      setActive(on);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(pick);
    };
    pick();
    addEventListener('scroll', onScroll, { passive: true });
    addEventListener('resize', onScroll);
    return () => {
      removeEventListener('scroll', onScroll);
      removeEventListener('resize', onScroll);
      cancelAnimationFrame(frame);
    };
  }, [ids]);
  return active;
}

/* ------------------------------------------------------------------------- nav */

function Nav() {
  const active = useActiveSection(IDS);
  const soundOn = useSound();
  const blocked = useSoundBlocked();
  const seen = useRef(false);
  useEffect(() => {
    if (!active) return;
    if (seen.current) sound.chime(IDS.indexOf(active));
    seen.current = true;
  }, [active]);
  const [solid, setSolid] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = () => setSolid(window.scrollY > 24);
    on();
    addEventListener('scroll', on, { passive: true });
    return () => removeEventListener('scroll', on);
  }, []);
  const go = (id: string) => {
    setOpen(false);
    scrollToId(id);
  };
  return (
    <header
      className={clsx(
        'fixed top-0 inset-x-0 z-[80] transition-[background,border-color,backdrop-filter] duration-300 border-b',
        solid || open ? 'bg-[rgba(6,8,12,.82)] backdrop-blur-xl border-white/[.07]' : 'bg-transparent border-transparent',
      )}
    >
      <div className="max-w-[82.5rem] mx-auto h-16 px-5 sm:px-8 flex items-center gap-6">
        <a href="#/" className="flex items-center gap-2.5 shrink-0" aria-label="Synapse0g home" onClick={() => scrollToY(0)}>
          <Glyph />
          <span className="font-display font-display-tight text-[1rem] text-[var(--ink)]" style={{ fontWeight: 800 }}>
            SYNAPSE<span className="text-[var(--accent)]">0G</span>
          </span>
        </a>
        <nav className="hidden xl:flex items-center gap-1 mx-auto" aria-label="Sections">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => go(s.id)}
              aria-current={active === s.id ? 'true' : undefined}
              className={clsx(
                'relative h-9 px-3.5 text-[0.875rem] whitespace-nowrap transition-colors cursor-pointer rounded-full',
                active === s.id ? 'text-[var(--ink)]' : 'text-[var(--ink-dim)] hover:text-[var(--ink)]',
              )}
            >
              {s.label}
              <span
                className={clsx(
                  'absolute left-1/2 -translate-x-1/2 bottom-0.5 h-[0.1875rem] w-[0.1875rem] rounded-full bg-[var(--accent)] transition-opacity duration-300',
                  active === s.id ? 'opacity-100' : 'opacity-0',
                )}
              />
            </button>
          ))}
        </nav>
        <div className="ml-auto xl:ml-0 flex items-center gap-2 shrink-0">
          <button
            // on but not yet started by the browser: this click starts it, rather than turning it off
            onClick={() => (blocked ? setSound(true) : setSound(!soundOn))}
            className={clsx(
              'h-9 rounded-full border flex items-center justify-center gap-2 cursor-pointer transition-colors',
              blocked ? 'px-3 border-[var(--accent)]/60 text-[var(--accent)] animate-pulse' : 'w-9',
              !blocked && (soundOn ? 'border-[var(--accent)]/50 text-[var(--accent)]' : 'border-white/10 text-[var(--ink-dim)] hover:text-[var(--ink)]'),
            )}
            aria-label={blocked ? 'Start sound' : soundOn ? 'Turn sound off' : 'Turn sound on'}
            aria-pressed={soundOn}
            title={blocked ? 'Your browser waits for a click to start sound' : soundOn ? 'Sound on' : 'Sound off'}
          >
            {soundOn ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
            {blocked && <span className="text-[0.8125rem] whitespace-nowrap">click to start sound</span>}
          </button>
          <a href="#/live" className="hidden sm:inline-flex h-9 px-3 items-center whitespace-nowrap text-[0.875rem] text-[var(--ink)] hover:opacity-80">
            Live run
          </a>
          <a
            href="#/pay"
            className="hidden sm:inline-flex h-9 pl-4 pr-3 rounded-full whitespace-nowrap bg-[var(--ink)] text-black text-[0.875rem] font-medium items-center gap-1.5 hover:bg-white transition-colors"
          >
            Optimize your agent <ArrowRight className="w-4 h-4" />
          </a>
          <button
            onClick={() => setOpen((o) => !o)}
            className="xl:hidden w-10 h-10 rounded-full border border-white/10 flex items-center justify-center text-[var(--ink)] cursor-pointer"
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
          >
            {open ? <X className="w-4 h-4" /> : <Menu className="w-4 h-4" />}
          </button>
        </div>
      </div>
      <AnimatePresence>
        {open && (
          <motion.nav
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: EASE }}
            className="xl:hidden overflow-hidden border-t border-white/[.07]"
          >
            <div className="px-5 py-4 flex flex-col">
              {SECTIONS.map((s) => (
                <button key={s.id} onClick={() => go(s.id)} className="h-12 text-left text-[1.0625rem] text-[var(--ink)] border-b border-white/[.05] cursor-pointer">
                  {s.label}
                </button>
              ))}
              <a href="#/pay" className="mt-4 h-12 rounded-full bg-[var(--ink)] text-black text-[0.9375rem] font-medium flex items-center justify-center gap-2">
                Optimize your agent <ArrowRight className="w-4 h-4" />
              </a>
            </div>
          </motion.nav>
        )}
      </AnimatePresence>
    </header>
  );
}

/* ------------------------------------------------------------------------ hero */

function Hero() {
  const section = useRef<HTMLElement>(null);
  const input = useRef<HeroInput>({ morph: 0, mx: 0, my: 0 });
  const [active, setActive] = useState(true);
  const [phase, setPhase] = useState<'serial' | 'compiled'>('serial');
  const gauge = useRef<HTMLDivElement>(null);
  const p = useSectionProgress(section);
  useMotionValueEvent(p, 'change', (v) => {
    const m = Math.min(1, Math.max(0, (v - 0.03) / 0.8));
    input.current.morph = m;
    const next = m > 0.78 ? 'compiled' : 'serial';
    setPhase((cur) => (cur === next ? cur : next));
    if (gauge.current) gauge.current.style.transform = `scaleX(${m})`;
  });
  const headBlur = useTransform(p, [0.05, 0.2], ['blur(0px)', 'blur(8px)']);
  const headY = useTransform(p, [0, 0.26], ['0%', '-24%']);
  const headO = useTransform(p, [0.04, 0.2], [1, 0]);
  const midO = useTransform(p, [0.2, 0.27, 0.42, 0.48], [0, 1, 1, 0]);
  const midY = useTransform(p, [0.2, 0.27, 0.42, 0.48], [30, 0, 0, -30]);
  const midBlur = useTransform(p, [0.2, 0.27, 0.42, 0.48], ['blur(8px)', 'blur(0px)', 'blur(0px)', 'blur(8px)']);
  const compBlur = useTransform(p, [0.58, 0.7], ['blur(8px)', 'blur(0px)']);
  const compO = useTransform(p, [0.58, 0.7], [0, 1]);
  const compY = useTransform(p, [0.58, 0.7], [36, 0]);

  useEffect(() => {
    const move = (e: PointerEvent) => {
      input.current.mx = (e.clientX / innerWidth) * 2 - 1;
      input.current.my = -((e.clientY / innerHeight) * 2 - 1);
    };
    addEventListener('pointermove', move, { passive: true });
    const io = new IntersectionObserver(([e]) => setActive(e.isIntersecting), { rootMargin: '100px' });
    if (section.current) io.observe(section.current);
    return () => {
      removeEventListener('pointermove', move);
      io.disconnect();
    };
  }, []);

  return (
    <section id="top" ref={section} className="relative h-[330vh]">
      <div className="sticky top-0 h-[100svh] overflow-hidden">
        <Suspense fallback={null}>
          <HeroSilicon input={input} active={active} />
        </Suspense>
        <div aria-hidden className="hero-aurora absolute inset-0 pointer-events-none mix-blend-screen" />
        <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_62%_55%,transparent_45%,rgba(5,7,11,.6)_100%)]" />
        <div className="absolute inset-y-0 left-0 w-full lg:w-[52vw] pointer-events-none bg-[linear-gradient(90deg,rgba(5,7,11,.74)_0%,rgba(5,7,11,.32)_55%,transparent_100%)]" />
        <div className="hidden md:block absolute inset-5 top-[5.25rem] pointer-events-none">
          {['top-0 left-0 border-t border-l', 'top-0 right-0 border-t border-r', 'bottom-0 left-0 border-b border-l', 'bottom-0 right-0 border-b border-r'].map((c) => (
            <span key={c} className={clsx('absolute w-5 h-5 border-white/20', c)} />
          ))}
        </div>

        <motion.div style={{ y: headY, opacity: headO, filter: headBlur }} className="absolute inset-x-0 top-[17vh] pointer-events-none">
          <div className="relative max-w-[82.5rem] mx-auto px-5 sm:px-8 lg:px-12">
            <h1 aria-label="Compile the agent. Prove it.">
              <SplitReveal text={'COMPILE THE AGENT.'} stagger={0.08} className="block font-display text-[clamp(2.75rem,7.4vw,7.75rem)] text-[var(--ink)] leading-[.88]" />
              <SplitReveal text="PROVE IT." delay={0.25} className="lg:hidden block font-display text-[clamp(2.75rem,7.4vw,7.75rem)] text-[var(--accent)] leading-[.88]" />
            </h1>
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.9, duration: 0.8, ease: EASE }}
              className="hidden lg:flex absolute right-5 sm:right-8 lg:right-12 bottom-[0.6vw] items-center gap-2.5 px-4 h-10 rounded-full border border-white/12 bg-black/40 backdrop-blur text-[0.8125rem] text-[var(--ink-dim)]"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-pulse" /> an agent that earns its own compute on <span className="text-[var(--ink)]">0G</span>
            </motion.div>
          </div>
        </motion.div>

        <motion.div style={{ opacity: midO, y: midY, filter: midBlur }} className="absolute inset-x-0 top-[15vh] pointer-events-none">
          <div className="max-w-[82.5rem] mx-auto px-5 sm:px-8 lg:px-12">
            <div className="font-display text-[clamp(1.75rem,3.4vw,3.5rem)] text-[var(--ink)] leading-[.95]">
              SYNAPSES IN.
              <br />
              <span className="text-[var(--accent)]">CIRCUITS</span> OUT.
            </div>
            <p className="mt-5 text-[1.0625rem] text-[var(--ink-dim)] leading-relaxed max-w-[36ch]">
              An agent behaves like a loose web of calls. Synapse0g lifts every step into a graph it can measure, move and test.
            </p>
          </div>
        </motion.div>

        <motion.div style={{ opacity: compO, y: compY, filter: compBlur }} className="absolute inset-x-0 top-[15vh] pointer-events-none">
          <div className="max-w-[82.5rem] mx-auto px-5 sm:px-8 lg:px-12">
            <div className="font-display text-[clamp(1.75rem,3.4vw,3.5rem)] text-[var(--ink)] leading-[.95]">
              SAME AGENT.
              <br />
              <span className="text-[var(--accent)]">REAL</span> DEPENDENCIES.
            </div>
            <p className="mt-5 text-[1.0625rem] text-[var(--ink-dim)] leading-relaxed max-w-[36ch]">
              Steps that never needed to wait now run side by side, answers already known come from a cache, and the irreversible step is
              flagged and left exactly as it was.
            </p>
          </div>
        </motion.div>

        <motion.div style={{ opacity: headO }} className="absolute inset-x-0 bottom-[10vh]">
          <div className="max-w-[82.5rem] mx-auto px-5 sm:px-8 lg:px-12 flex items-end justify-between gap-8">
            <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.5, duration: 0.9, ease: EASE }} className="w-full max-w-[34rem]">
              <p className="text-[clamp(1rem,1.3vw,1.1875rem)] leading-[1.6] text-[var(--ink-dim)] mb-7">
                Synapse0g is an AI agent that optimizes other agents. Pay it in 0G: it compiles your agent, rebuilds it to run faster and
                cheaper, proves the answers did not change, and <span className="text-[var(--ink)]">hands the optimized agent back to you as code.</span>
              </p>
              <div className="flex flex-wrap gap-3">
                <Pill variant="accent" to="/pay">Optimize your agent</Pill>
                <Pill variant="dark" to="/live">Watch a live run</Pill>
              </div>
            </motion.div>
            <div className="hidden lg:block shrink-0 -mb-2 [filter:drop-shadow(0_0_28px_rgba(249,115,22,.55))_drop-shadow(0_0_70px_rgba(249,115,22,.25))]" aria-hidden>
              <SplitReveal text="PROVE IT." delay={0.45} stagger={0.1} className="block font-display text-[clamp(2.5rem,5.8vw,6.25rem)] leading-[.88] text-[var(--accent)] whitespace-nowrap" />
            </div>
          </div>
        </motion.div>

        <div className="absolute inset-x-0 bottom-0 h-14 border-t border-white/[.06] bg-black/30 backdrop-blur-sm">
          <div className="h-full max-w-[82.5rem] mx-auto px-5 sm:px-8 lg:px-12 flex items-center gap-5 text-[0.8125rem]">
            <span className="hidden sm:inline text-[var(--ink-dim)]">Illustration of one agent</span>
            <div className="flex-1 max-w-[28.75rem] mx-auto flex items-center gap-3">
              <span className={phase === 'serial' ? 'text-[var(--ink)]' : 'text-[var(--ink-dim)]'}>As written</span>
              <div className="flex-1 h-[0.1875rem] rounded-full bg-white/10 overflow-hidden">
                <div ref={gauge} className="h-full bg-[var(--accent)] origin-left" style={{ transform: 'scaleX(0)' }} />
              </div>
              <span className={phase === 'compiled' ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]'}>Compiled</span>
            </div>
            <span className="hidden sm:flex items-center gap-1.5 text-[var(--ink-dim)]">
              {phase === 'serial' ? 'Scroll to compile' : 'Keep scrolling'} <ArrowDown className="w-3.5 h-3.5 animate-bounce" />
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------- the optimizer agent */

const LOOP = [
  { icon: Code2, title: 'Bring your agent', body: 'Paste or upload a LangGraph agent with one sample input, or pick an example. Its steps are read from the code and priced.' },
  { icon: Coins, title: 'Pay in 0G', body: "One transaction from your wallet: depositFor the optimizer's 0G compute account on 0G's Payment Layer. No contract of ours in between." },
  { icon: Cpu, title: 'It pays for its own compute', body: 'The optimizer traces your agent to SIR, rewrites it and runs it, every model call paid from that balance, with 0G request ids.' },
  { icon: FileCode2, title: 'Get your agent back', body: 'optimized_agent.py, generated from the optimized graph, beside your original code, both graphs and every charge.' },
];

function Optimizer() {
  return (
    <section id="optimizer" className="relative py-28 lg:py-36 border-t border-white/[.06]">
      <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-10 items-end">
          <div>
            <SectionLabel>The optimizer agent</SectionLabel>
            <Reveal>
              <h2 className="mt-5 font-display text-[clamp(2.125rem,4.4vw,3.75rem)] leading-[1.02] text-[var(--ink)]">
                An agent that earns its compute by improving yours.
              </h2>
            </Reveal>
          </div>
          <Reveal delay={0.08}>
            <p className="text-[1.125rem] leading-[1.65] text-[var(--ink-dim)] max-w-[50ch]">
              Most agents stop the day their owner stops paying the API bill. This one is paid by the people it works for: each payment lands
              straight in its 0G compute account, and pays for the model calls that optimize the next agent.
            </p>
          </Reveal>
        </div>
        <div className="mt-14 grid md:grid-cols-2 xl:grid-cols-4 gap-5">
          {LOOP.map((it, i) => (
            <motion.div
              key={it.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-10% 0px' }}
              transition={{ duration: 0.7, ease: EASE, delay: i * 0.08 }}
              className="spot group relative rounded-[1.125rem] border border-white/10 bg-white/[.02] p-7 hover:border-white/20 transition-colors"
            >
              <span className="absolute top-6 right-6 font-mono text-[0.8125rem] text-[var(--ink-dim)]">0{i + 1}</span>
              <span className="w-12 h-12 rounded-[0.75rem] bg-[var(--accent)]/10 border border-[var(--accent)]/25 flex items-center justify-center text-[var(--accent)] group-hover:scale-105 transition-transform">
                <it.icon className="w-5 h-5" />
              </span>
              <h3 className="mt-6 text-[1.25rem] font-semibold text-[var(--ink)]">{it.title}</h3>
              <p className="mt-2 text-[1rem] leading-relaxed text-[var(--ink-dim)]">{it.body}</p>
            </motion.div>
          ))}
        </div>
        <Reveal className="mt-10 flex flex-wrap items-center gap-4">
          <Pill variant="accent" to="/pay">Optimize your agent</Pill>
          <span className="text-[0.9375rem] text-[var(--ink-dim)]">The price is shown line by line before you pay: compute for your agent, plus the optimizer's fee.</span>
        </Reveal>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- live demo */

const DOORS = [
  { icon: Play, title: 'Watch a live run', body: 'Pick an agent and a task. It is compiled to SIR, then run as written and optimized at the same moment, on 0G, and compared.', to: '/live', cta: 'Open the live run' },
  { icon: BarChart3, title: 'Results and workflows', body: 'Every study: held-out tasks, measured time and 0G cost, and the firewall verdict, including the changes it refused.', to: '/overview', cta: 'See the results' },
  { icon: Code2, title: 'Optimize your own agent', body: 'Paste your agent, see its price, pay in 0G, and watch it being optimized, then download the optimized code.', to: '/pay', cta: 'Start' },
];

function Demo() {
  return (
    <section id="demo" className="relative py-28 lg:py-36 border-t border-white/[.06]">
      <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8">
        <SectionLabel>Live demo</SectionLabel>
        <Reveal>
          <h2 className="mt-5 font-display text-[clamp(2.125rem,4.4vw,3.75rem)] leading-[1.02] text-[var(--ink)] max-w-[22ch]">See it for yourself, live.</h2>
        </Reveal>
        <div className="mt-12 grid md:grid-cols-3 gap-5">
          {DOORS.map((d, i) => (
            <motion.a
              key={d.title}
              href={`#${d.to}`}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-10% 0px' }}
              transition={{ duration: 0.7, ease: EASE, delay: i * 0.08 }}
              className={clsx(
                'spot group flex flex-col rounded-[1.125rem] border p-7 transition-colors',
                i === 2 ? 'border-[var(--accent)]/40 bg-[var(--accent)]/[.06] hover:border-[var(--accent)]/70' : 'border-white/10 bg-white/[.02] hover:border-white/25',
              )}
            >
              <d.icon className="w-6 h-6 text-[var(--accent)]" />
              <h3 className="mt-6 text-[1.375rem] font-semibold text-[var(--ink)]">{d.title}</h3>
              <p className="mt-2 text-[1rem] leading-relaxed text-[var(--ink-dim)] flex-1">{d.body}</p>
              <span className="mt-7 inline-flex items-center gap-2 text-[0.9375rem] text-[var(--ink)] group-hover:text-[var(--accent)] transition-colors">
                {d.cta} <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
              </span>
            </motion.a>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ results */

/* From RESULTS.md: measured on real provider calls; 0G costs are 0G's own bill per request. Held-out tasks only. */
const RESULTS = [
  { agent: 'Supplier-check agent', kind: 'LangGraph, 3 lines added', head: '3.48× faster', sub: '72% cheaper on 0G\'s bill · 20/20 still right', verdict: 'APPROVE' },
  { agent: 'Procurement agent', kind: 'plain Python, 2 lines added', head: '4.5× faster', sub: '87.5% cheaper on 0G\'s bill · 13.5/18 → 15/18 right', verdict: 'APPROVE' },
  { agent: 'Research workflow', kind: 'plan, search in parallel, answer', head: '1.99× faster', sub: '18% cheaper · 19/20 still right', verdict: 'APPROVE' },
  { agent: 'tau-bench retail', kind: 'refunds and exchanges', head: '70% cheaper', sub: 'but broke 5 tasks and took more irreversible actions', verdict: 'BLOCK' },
  { agent: 'Code-review workflow', kind: 'a step moved to a cheaper model', head: '72% cheaper', sub: 'but 10/10 → 4/10 right', verdict: 'BLOCK' },
];

function Results() {
  return (
    <section id="results" className="relative py-28 lg:py-36 border-t border-white/[.06]">
      <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-10 items-end">
          <div>
            <SectionLabel>Results</SectionLabel>
            <Reveal>
              <h2 className="mt-5 font-display text-[clamp(2.125rem,4.4vw,3.75rem)] leading-[1.02] text-[var(--ink)]">Measured, and refused when it breaks.</h2>
            </Reveal>
          </div>
          <Reveal delay={0.08}>
            <p className="text-[1.125rem] leading-[1.65] text-[var(--ink-dim)] max-w-[50ch]">
              Every number is measured on real model calls, on tasks the optimizer never tuned on. The firewall approves a change only if no
              answer breaks. The two biggest savings below were refused.
            </p>
          </Reveal>
        </div>
        <div className="mt-12 rounded-[1.25rem] border border-white/10 overflow-hidden">
          {RESULTS.map((r, i) => (
            <motion.div
              key={r.agent}
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: i * 0.05 }}
              className="grid grid-cols-[1.2fr_1fr_1.4fr_auto] items-center gap-6 px-7 py-5 border-b border-white/[.06] last:border-0 bg-white/[.015]"
            >
              <div>
                <div className="text-[1.0625rem] text-[var(--ink)] font-medium">{r.agent}</div>
                <div className="text-[0.8125rem] text-[var(--ink-dim)]">{r.kind}</div>
              </div>
              <div className={clsx('font-display text-[1.75rem] leading-none', r.verdict === 'APPROVE' ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)] line-through decoration-[var(--bad)]/70')}>{r.head}</div>
              <div className="text-[0.9375rem] text-[var(--ink-dim)]">{r.sub}</div>
              <span className={clsx('px-3 h-7 inline-flex items-center rounded-full text-[0.8125rem] font-semibold border',
                r.verdict === 'APPROVE' ? 'text-[var(--ok)] border-[var(--ok)]/40 bg-[var(--ok)]/10' : 'text-[var(--bad)] border-[var(--bad)]/40 bg-[var(--bad)]/10')}>{r.verdict}</span>
            </motion.div>
          ))}
        </div>
        <Reveal className="mt-8">
          <Pill variant="dark" to="/overview">All results and workflows</Pill>
        </Reveal>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------- footer */

function Footer() {
  return (
    <footer className="relative border-t border-white/[.06] bg-[#04060a]">
      <div className="max-w-[77.5rem] mx-auto px-5 sm:px-8 py-24">
        <h2 className="font-display text-[clamp(2.25rem,5vw,4.5rem)] leading-[.95] text-[var(--ink)]">
          Your agent, <span className="text-[var(--accent)]">faster and cheaper.</span>
          <br />Proven, and paid for on 0G.
        </h2>
        <div className="mt-10 flex flex-wrap gap-3">
          <Pill variant="accent" to="/pay">Optimize your agent</Pill>
          <Pill variant="dark" to="/live">Watch a live run</Pill>
          <Pill variant="dark" to="/overview">Results</Pill>
        </div>
        <div className="mt-20 pt-8 border-t border-white/[.06] flex flex-wrap items-center justify-between gap-4 text-[0.875rem] text-[var(--ink-dim)]">
          <span className="flex items-center gap-2.5"><Glyph size={18} /> Synapse0g · a compiler and optimizer for AI agents, on 0G</span>
          <div className="flex gap-6">
            <a href="#/pay" className="hover:text-[var(--ink)]">Console</a>
            <a href="#/jobs" className="hover:text-[var(--ink)]">Jobs and payments</a>
            <a href="#/connect" className="hover:text-[var(--ink)]">SDK and CLI</a>
            <button onClick={() => scrollToY(0)} className="hover:text-[var(--ink)] cursor-pointer">Back to top</button>
          </div>
        </div>
      </div>
    </footer>
  );
}

/* ------------------------------------------------------------------- page */

export default function Landing() {
  const still = useRef(0);
  useLenis((l) => {
    sound.air(Math.abs(l.velocity) / 60);
    clearTimeout(still.current);
    still.current = window.setTimeout(() => sound.air(0), 160);
  });
  useEffect(() => {
    document.title = 'Synapse0g: an agent that optimizes your agent, paid on 0G';
    document.documentElement.classList.add('landing-scale');
    return () => document.documentElement.classList.remove('landing-scale');
  }, []);
  return (
    <div className="relative bg-[var(--bg)] text-[var(--ink)] min-h-screen overflow-x-clip">
      <ScrollProgress />
      <Nav />
      <main className="relative z-10 bg-[var(--bg)]">
        <Hero />
        <WhatItDoes />
        <Optimizer />
        <Demo />
        <Lattice />
        <Passes />
        <Results />
      </main>
      <Footer />
    </div>
  );
}
