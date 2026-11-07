// Motion primitives. All of them animate content that is already real; none of them
// invents a value. CountUp counts towards the number the API sent and nothing else.
import { useEffect, useRef, useState, type ReactNode, type CSSProperties } from 'react';
import { motion, useInView, useMotionValue, useReducedMotion, useScroll, useSpring } from 'motion/react';
import { clsx } from 'clsx';
import { ArrowUpRight } from 'lucide-react';
import Lenis from 'lenis';

export const EASE = [0.22, 1, 0.36, 1] as const;

/** One pointer listener for the whole app: lights the `.spot` element under the cursor. */
export function useSpotlight() {
  useEffect(() => {
    let current: HTMLElement | null = null;
    const move = (e: PointerEvent) => {
      const el = (e.target as HTMLElement | null)?.closest?.('.spot') as HTMLElement | null;
      if (current && current !== el) current.style.setProperty('--spot-o', '0');
      current = el;
      if (!el) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty('--mx', `${e.clientX - r.left}px`);
      el.style.setProperty('--my', `${e.clientY - r.top}px`);
      el.style.setProperty('--spot-o', '1');
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => window.removeEventListener('pointermove', move);
  }, []);
}

/**
 * Counts from 0 to `value` once, when it scrolls into view. `format` renders every
 * intermediate frame, so units and separators stay correct while it runs. A null value
 * renders "—" immediately: there is nothing to count towards.
 */
/**
 * Counts up to `value` once it is on screen. Before that it renders the real value invisibly, so the
 * layout holds and no number the API did not send (such as 0) is ever readable on the page.
 * `from` is where the count starts: 1 for a speedup, since 1× is "no change".
 */
export function CountUp({ value, format, duration = 900, className, from = 0 }: {
  value: number | null | undefined;
  format: (v: number) => string;
  duration?: number;
  className?: string;
  from?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: '-10% 0px' });
  const reduce = useReducedMotion();
  const [shown, setShown] = useState<number | null>(null);
  useEffect(() => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return;
    if (reduce || !inView) {
      if (reduce) setShown(value);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / duration);
      const eased = 1 - Math.pow(1 - k, 4);
      setShown(k >= 1 ? value : start + (value - start) * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    const start = Math.min(from, value);
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, inView, reduce, duration, from]);
  const real = typeof value === 'number' && Number.isFinite(value);
  return (
    <span ref={ref} className={className} style={real && shown === null ? { visibility: 'hidden' } : undefined}>
      {!real ? '—' : format(shown ?? value)}
    </span>
  );
}

/** Words rise out of a mask, staggered. The signature entrance for display type. */
export function SplitReveal({ text, className, delay = 0, stagger = 0.06, as = 'span', wordClassName }: {
  text: string;
  className?: string;
  delay?: number;
  stagger?: number;
  as?: 'span' | 'h1' | 'h2' | 'h3' | 'p' | 'div';
  wordClassName?: (word: string, i: number) => string | undefined;
}) {
  const Tag = motion[as];
  // a newline in `text` forces a line break; each line keeps its own words
  const lines = text.split('\n');
  return (
    <Tag
      className={className}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: '-8% 0px' }}
      transition={{ staggerChildren: stagger, delayChildren: delay }}
      aria-label={text.replace(/\n/g, ' ')}
    >
      {lines.map((line, li) => {
        const words = line.split(' ');
        return (
          <span key={li} className={lines.length > 1 ? 'block' : undefined}>
            {words.map((w, i) => (
              <span key={i} className="inline-block overflow-hidden align-bottom pb-[0.08em] -mb-[0.08em]" aria-hidden>
                <motion.span
                  className={clsx('inline-block will-change-transform', wordClassName?.(w, i))}
                  variants={{ hidden: { y: '105%', rotate: 4 }, show: { y: '0%', rotate: 0 } }}
                  transition={{ duration: 0.9, ease: EASE }}
                >
                  {w}
                </motion.span>
                {i < words.length - 1 && ' '}
              </span>
            ))}
          </span>
        );
      })}
    </Tag>
  );
}

/** Fade + rise once in view. */
export function Reveal({ children, delay = 0, y = 18, className, style }: {
  children: ReactNode; delay?: number; y?: number; className?: string; style?: CSSProperties;
}) {
  return (
    <motion.div
      className={className}
      style={style}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-8% 0px' }}
      transition={{ duration: 0.8, ease: EASE, delay }}
    >
      {children}
    </motion.div>
  );
}

/** Pill with a round arrow badge that slides on hover (e-CESA). */
export function Pill({ to, href, children, variant = 'light', onClick, className }: {
  to?: string; href?: string; children: ReactNode; variant?: 'light' | 'dark' | 'accent'; onClick?: () => void; className?: string;
}) {
  const cls = clsx(
    'group relative inline-flex items-center gap-3 h-12 pl-6 pr-1.5 rounded-full text-[0.875rem] font-medium transition-[background,border-color,transform] duration-300 cursor-pointer select-none active:scale-[.98]',
    variant === 'light' && 'bg-[var(--ink)] text-black hover:bg-white',
    variant === 'dark' && 'border border-[var(--line-strong)] text-[var(--ink)] hover:border-[var(--ink-dim)] bg-black/30 backdrop-blur',
    variant === 'accent' && 'bg-[var(--accent)] text-black hover:brightness-110',
    className,
  );
  const badge = (
    <span
      className={clsx(
        'relative w-9 h-9 rounded-full overflow-hidden flex items-center justify-center',
        variant === 'light' ? 'bg-black text-white' : variant === 'accent' ? 'bg-black text-[var(--accent)]' : 'bg-[var(--accent)] text-black',
      )}
    >
      <ArrowUpRight className="w-4 h-4 transition-transform duration-500 ease-[cubic-bezier(.22,1,.36,1)] group-hover:translate-x-6 group-hover:-translate-y-6" />
      <ArrowUpRight className="w-4 h-4 absolute -translate-x-6 translate-y-6 transition-transform duration-500 ease-[cubic-bezier(.22,1,.36,1)] group-hover:translate-x-0 group-hover:translate-y-0" />
    </span>
  );
  if (to) return <a href={to.startsWith('#') ? to : `#${to}`} className={cls} onClick={onClick}>{children}{badge}</a>;
  if (href) return <a href={href} className={cls} onClick={onClick}>{children}{badge}</a>;
  return <button type="button" className={cls} onClick={onClick}>{children}{badge}</button>;
}

/** The thin accent line across the top that tracks scroll, with a glowing head. */
export function ScrollProgress() {
  const { scrollYProgress } = useScroll();
  const x = useSpring(scrollYProgress, { stiffness: 140, damping: 30, mass: 0.3 });
  return (
    <motion.div
      className="fixed top-0 left-0 right-0 h-[0.125rem] z-[90] origin-left bg-[var(--accent)]"
      style={{ scaleX: x, boxShadow: '0 0 12px var(--accent)' }}
    />
  );
}

/**
 * 0 → 1 as a tall section scrolls past a pinned (sticky) viewport: 0 when its top meets
 * the top of the screen, 1 when its bottom meets the bottom. Measured from the element's
 * rect on every scroll, so it is right under Lenis, sticky children and any root height.
 */
export function useSectionProgress(ref: React.RefObject<HTMLElement | null>) {
  const mv = useMotionValue(0);
  useEffect(() => {
    let raf = 0;
    const measure = () => {
      raf = 0;
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const span = r.height - window.innerHeight;
      mv.set(span > 0 ? Math.min(1, Math.max(0, -r.top / span)) : 0);
    };
    const on = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener('scroll', on, { passive: true });
    window.addEventListener('resize', on);
    return () => {
      window.removeEventListener('scroll', on);
      window.removeEventListener('resize', on);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [ref, mv]);
  return mv;
}

/** Lenis smooth scroll for long pages. Off under reduced motion. */
export function useLenis(onScroll?: (l: Lenis) => void) {
  const reduce = useReducedMotion();
  const cb = useRef(onScroll);
  cb.current = onScroll;
  useEffect(() => {
    if (reduce) return;
    // Marks the page as a Lenis page; the height rule in fx.css keys on this, not on Lenis's own
    // class, which a late scroll event can put back after destroy().
    document.documentElement.classList.add('smooth-page');
    const lenis = new Lenis({ lerp: 0.085, wheelMultiplier: 0.9, touchMultiplier: 1.4, smoothWheel: true, syncTouch: false });
    lenis.on('scroll', () => cb.current?.(lenis));
    let raf = 0;
    const loop = (t: number) => {
      lenis.raf(t);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    (window as unknown as { __lenis?: Lenis }).__lenis = lenis;
    return () => {
      cancelAnimationFrame(raf);
      lenis.destroy();
      // Lenis leaves its classes on <html>; with them, `html.lenis { height: auto }` would collapse
      // every full-height page opened after the landing (the console shrank to its content).
      document.documentElement.classList.remove('smooth-page', 'lenis', 'lenis-smooth', 'lenis-scrolling', 'lenis-stopped');
      delete (window as unknown as { __lenis?: Lenis }).__lenis;
    };
  }, [reduce]);
}

/** Scroll to a page offset through Lenis when it is running, natively otherwise. */
export function scrollToY(y: number, duration = 1.4) {
  const lenis = (window as unknown as { __lenis?: Lenis }).__lenis;
  if (lenis) lenis.scrollTo(y, { duration });
  else window.scrollTo({ top: y, behavior: 'smooth' });
}

export function scrollToId(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const lenis = (window as unknown as { __lenis?: Lenis }).__lenis;
  // Land just under the fixed nav, whatever height it has at this screen size.
  const nav = document.querySelector('header')?.getBoundingClientRect().height ?? 64;
  if (lenis) lenis.scrollTo(el, { offset: -nav, duration: 1.6 });
  else el.scrollIntoView({ behavior: 'smooth' });
}

/** How much the landing page is scaled up (1 at 16px root, 2 at 32px); see html.landing-scale.
 * Measured widths are in screen px, so text-size math must multiply by this. */
export function rootScale() {
  if (typeof document === 'undefined') return 1;
  return (parseFloat(getComputedStyle(document.documentElement).fontSize) || 16) / 16;
}
