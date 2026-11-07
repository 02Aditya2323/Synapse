// Small landing chrome shared by the sections: the glyph, section labels, the copyable
// command and the BLOCK stamp. Copy only; no measured number lives here.
import { useState } from 'react';
import { motion } from 'motion/react';
import { clsx } from 'clsx';
import { Check, Copy } from 'lucide-react';
import { EASE, Reveal } from '../lib/motion';

/* ------------------------------------------------------------------------ chrome */

export function Glyph({ size = 22 }: { size?: number }) {
  return (
    <svg style={{ width: `${size / 16}rem`, height: `${size / 16}rem` }} viewBox="0 0 32 32" fill="none" className="shrink-0" aria-hidden>
      <circle cx="16" cy="16" r="14.5" stroke="var(--accent)" strokeWidth="1.5" />
      <circle cx="9" cy="16" r="2.4" fill="var(--accent)" />
      <circle cx="22" cy="9.5" r="2.4" fill="var(--ink)" />
      <circle cx="22" cy="22.5" r="2.4" fill="var(--ink)" />
      <path d="M11.4 16h3.6l4.6-5.4M15 16l4.6 5.4" stroke="var(--ink)" strokeWidth="1.4" />
    </svg>
  );
}

export function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <Reveal className={clsx('flex items-center gap-3 text-[0.8438rem] font-medium text-[var(--accent)]', className)}>
      <motion.span
        className="h-px w-8 bg-[var(--accent)] origin-left"
        initial={{ scaleX: 0 }}
        whileInView={{ scaleX: 1 }}
        viewport={{ once: true }}
        transition={{ duration: 0.8, ease: EASE, delay: 0.15 }}
      />
      <span>{children}</span>
    </Reveal>
  );
}

export function CopyCommand({ cmd }: { cmd: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(cmd).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        });
      }}
      className="group w-full flex items-center gap-3 h-12 pl-4 pr-3 rounded-[0.625rem] border border-white/10 bg-black/50 backdrop-blur font-mono text-[0.8125rem] text-left hover:border-white/25 transition-colors cursor-pointer"
      title="Copy"
    >
      <span className="text-[var(--accent)]">$</span>
      <span className="text-[var(--ink)] truncate">{cmd}</span>
      <span className="ml-auto text-[var(--ink-dim)] group-hover:text-[var(--ink)]">
        {done ? <Check className="w-4 h-4 text-[var(--ok)]" /> : <Copy className="w-4 h-4" />}
      </span>
    </button>
  );
}
