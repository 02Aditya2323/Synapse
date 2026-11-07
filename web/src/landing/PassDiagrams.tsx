// Before → after diagrams for the twelve passes. Each is a picture of what the pass
// does to a workflow, small enough to read at a glance. A light pulse runs along the
// connections. Nothing here is a measurement.
import type { ReactNode } from 'react';

const INK = '#e9e5de';
const DIM = 'rgba(233,229,222,.5)';
const LINE = 'rgba(255,255,255,.2)';

function Box({ x, y, w = 50, h = 24, t, c = '#8b95a5', dim, glow }: { x: number; y: number; w?: number; h?: number; t: string; c?: string; dim?: boolean; glow?: boolean }) {
  return (
    <g opacity={dim ? 0.28 : 1}>
      {glow && <rect x={x - 3} y={y - 3} width={w + 6} height={h + 6} rx={8} fill="none" stroke={c} strokeOpacity={0.35} />}
      <rect x={x} y={y} width={w} height={h} rx={6} fill="#0c1018" stroke={c} strokeOpacity={0.85} strokeWidth={1.1} />
      <rect x={x + 8} y={y} width={w - 16} height={1.6} fill={c} />
      <text x={x + w / 2} y={y + h / 2 + 3.6} textAnchor="middle" fontSize={10} fontWeight={500} fill={INK} fontFamily="var(--font-sans)">
        {t}
      </text>
    </g>
  );
}

function L({ d, delay = 0, dashed }: { d: string; delay?: number; dashed?: boolean }) {
  return (
    <>
      <path d={d} fill="none" stroke={LINE} strokeWidth={1.2} strokeDasharray={dashed ? '3 3' : undefined} />
      <path d={d} fill="none" pathLength={100} className="pd-pulse" stroke="var(--accent)" strokeWidth={1.8} strokeLinecap="round" style={{ animationDelay: `${delay}s` }} />
    </>
  );
}

function Note({ x, y, t, c = DIM, anchor = 'middle' }: { x: number; y: number; t: string; c?: string; anchor?: 'start' | 'middle' | 'end' }) {
  return (
    <text x={x} y={y} textAnchor={anchor} fontSize={9.5} fill={c} fontFamily="var(--font-sans)">
      {t}
    </text>
  );
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 300 150" className="w-full h-auto" aria-hidden>
      <text x={10} y={14} fontSize={10} fill={DIM} fontFamily="var(--font-sans)">Before</text>
      <text x={170} y={14} fontSize={10} fill="var(--accent)" fontFamily="var(--font-sans)">After</text>
      <line x1={150} y1={22} x2={150} y2={140} stroke="rgba(255,255,255,.07)" />
      <path d="M144 78 l6 -5 l6 5" fill="none" stroke="rgba(255,255,255,.25)" transform="rotate(90 150 75)" />
      {children}
    </svg>
  );
}

const READ = '#60a5fa';
const PURE = '#2dd4bf';
const MODEL = '#c4b5fd';

export const PASS_DIAGRAMS: Record<string, ReactNode> = {
  cse: (
    <Frame>
      <Box x={10} y={63} w={34} t="in" />
      <L d="M44 75 C56 75 56 47 68 47" />
      <L d="M44 75 C56 75 56 103 68 103" delay={0.3} />
      <Box x={68} y={35} w={66} t="search(q)" c={READ} />
      <Box x={68} y={91} w={66} t="search(q)" c={READ} />
      <Box x={170} y={63} w={34} t="in" />
      <L d="M204 75 H224" />
      <Box x={224} y={63} w={66} t="search(q)" c={READ} glow />
      <Note x={257} y={108} t="one call, used twice" c="var(--accent)" />
    </Frame>
  ),
  memoize: (
    <Frame>
      <Note x={10} y={46} t="run 1" anchor="start" />
      <Box x={40} y={34} w={90} t="ask the model" c={MODEL} />
      <Note x={10} y={98} t="run 2" anchor="start" />
      <Box x={40} y={86} w={90} t="ask the model" c={MODEL} />
      <Note x={170} y={46} t="run 1" anchor="start" />
      <Box x={198} y={34} w={64} t="ask model" c={MODEL} />
      <L d="M262 46 H274 V62" />
      <rect x={254} y={62} width={40} height={22} rx={5} fill="#0c1018" stroke="var(--accent)" />
      <Note x={274} y={76.5} t="cache" c="var(--accent)" />
      <Note x={170} y={110} t="run 2" anchor="start" />
      <L d="M274 84 V108 H230" delay={0.6} />
      <Box x={198} y={98} w={32} h={20} t="hit" c="var(--accent)" glow />
    </Frame>
  ),
  parallelize: (
    <Frame>
      <Box x={8} y={63} w={36} t="search" c={READ} />
      <L d="M44 75 H52" />
      <Box x={52} y={63} w={36} t="search" c={READ} />
      <L d="M88 75 H96" delay={0.3} />
      <Box x={96} y={63} w={36} t="search" c={READ} />
      <Note x={70} y={110} t="one after another" />
      <L d="M172 75 C184 75 184 40 196 40" />
      <L d="M172 75 H196" />
      <L d="M172 75 C184 75 184 110 196 110" />
      <circle cx={172} cy={75} r={3} fill="var(--accent)" />
      <Box x={196} y={28} w={60} t="search" c={READ} glow />
      <Box x={196} y={63} w={60} t="search" c={READ} glow />
      <Box x={196} y={98} w={60} t="search" c={READ} glow />
      <Note x={276} y={78} t="at once" c="var(--accent)" />
    </Frame>
  ),
  context_prune: (
    <Frame>
      <rect x={14} y={30} width={60} height={92} rx={6} fill="#0c1018" stroke="rgba(255,255,255,.25)" />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <rect key={i} x={22} y={42 + i * 12} width={i % 2 ? 36 : 44} height={4} rx={2} fill="rgba(255,255,255,.35)" />
      ))}
      <L d="M74 76 H92" />
      <Box x={92} y={64} w={44} t="model" c={MODEL} />
      <Note x={44} y={136} t="full prompt" />
      <rect x={174} y={30} width={60} height={92} rx={6} fill="#0c1018" stroke="var(--accent)" strokeOpacity={0.7} />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <rect key={i} x={182} y={42 + i * 12} width={i % 2 ? 36 : 44} height={4} rx={2} fill={i === 1 || i === 4 ? 'var(--accent)' : 'rgba(255,255,255,.08)'} />
      ))}
      <L d="M234 76 H250" />
      <Box x={250} y={64} w={44} t="model" c={MODEL} />
      <Note x={204} y={136} t="only what is used" c="var(--accent)" />
    </Frame>
  ),
  context_trim: (
    <Frame>
      {[0, 1, 2].map((i) => (
        <rect key={i} x={14} y={40 + i * 24} width={110} height={10} rx={3} fill="rgba(255,255,255,.3)" />
      ))}
      <Note x={70} y={122} t="long messages, sent whole" />
      {[0, 1, 2].map((i) => (
        <g key={i}>
          <rect x={174} y={40 + i * 24} width={64} height={10} rx={3} fill="var(--accent)" fillOpacity={0.75} />
          <rect x={238} y={40 + i * 24} width={46} height={10} rx={3} fill="rgba(255,255,255,.06)" />
        </g>
      ))}
      <path d="M238 32 V108" stroke="var(--accent)" strokeDasharray="3 3" />
      <Note x={229} y={122} t="clipped, then tested" c="var(--accent)" />
    </Frame>
  ),
  downgrade: (
    <Frame>
      <Box x={20} y={48} w={100} h={54} t="large model" c={MODEL} />
      <Note x={70} y={122} t="slow, costly" />
      <Box x={196} y={60} w={62} h={30} t="small model" c={MODEL} glow />
      <Note x={227} y={110} t="same answers on the tests" c="var(--accent)" />
      <L d="M258 75 H284" />
    </Frame>
  ),
  loop_cutoff: (
    <Frame>
      <Box x={22} y={63} w={56} t="refine" c={MODEL} />
      <path d="M78 70 C110 70 110 34 50 34 C30 34 30 50 34 63" fill="none" stroke={LINE} />
      <path d="M78 70 C110 70 110 34 50 34 C30 34 30 50 34 63" fill="none" pathLength={100} className="pd-pulse" stroke="var(--accent)" strokeWidth={1.8} />
      <Note x={70} y={110} t="10 rounds" />
      <Box x={180} y={63} w={56} t="refine" c={MODEL} />
      <path d="M236 70 C262 70 262 40 212 40 C196 40 194 52 196 63" fill="none" stroke={LINE} />
      <L d="M236 81 H290" delay={0.4} />
      <Note x={262} y={100} t="stops when nothing" c="var(--accent)" />
      <Note x={262} y={111} t="changes any more" c="var(--accent)" />
    </Frame>
  ),
  dce: (
    <Frame>
      <Box x={8} y={63} w={34} t="in" />
      <L d="M42 75 C54 75 54 47 66 47" />
      <L d="M42 75 C54 75 54 103 66 103" delay={0.3} />
      <Box x={66} y={35} w={64} t="summarize" c={MODEL} />
      <Box x={66} y={91} w={64} t="draft notes" c={MODEL} />
      <Note x={98} y={130} t="result never used" />
      <Box x={168} y={63} w={34} t="in" />
      <L d="M202 75 C214 75 214 47 226 47" />
      <Box x={226} y={35} w={64} t="summarize" c={MODEL} glow />
      <Box x={226} y={91} w={64} t="draft notes" c={MODEL} dim />
      <path d="M232 95 L284 113 M284 95 L232 113" stroke="var(--bad)" strokeWidth={1.4} />
    </Frame>
  ),
  batch: (
    <Frame>
      {[34, 70, 106].map((y, i) => (
        <g key={y}>
          <Box x={8} y={y - 12} w={40} t="embed" c={PURE} />
          <L d={`M48 ${y} H98`} delay={i * 0.25} />
        </g>
      ))}
      <Box x={98} y={58} w={40} h={84} t="API" />
      <Note x={50} y={136} t="three requests" />
      {[34, 70, 106].map((y) => (
        <g key={y}>
          <Box x={168} y={y - 12} w={40} t="embed" c={PURE} />
          <path d={`M208 ${y} C222 ${y} 222 70 236 70`} fill="none" stroke={LINE} />
        </g>
      ))}
      <L d="M236 70 H252" />
      <Box x={252} y={46} w={40} h={48} t="API" glow c="var(--accent)" />
      <Note x={230} y={136} t="one request" c="var(--accent)" />
    </Frame>
  ),
  speculate: (
    <Frame>
      <Box x={10} y={40} w={56} t="decide" c={MODEL} />
      <L d="M66 52 H78 V98 H82" />
      <Box x={82} y={86} w={50} t="fetch" c={READ} />
      <Note x={70} y={132} t="fetch waits" />
      <Box x={170} y={40} w={56} t="decide" c={MODEL} />
      <Box x={170} y={86} w={56} t="fetch" c={READ} glow />
      <L d="M226 52 H262" />
      <L d="M226 98 C244 98 244 52 262 52" dashed delay={0.3} />
      <circle cx={266} cy={52} r={4} fill="var(--accent)" />
      <Note x={230} y={132} t="fetch starts early" c="var(--accent)" />
    </Frame>
  ),
  constfold: (
    <Frame>
      <Box x={14} y={63} w={50} t="2 + 3" c={PURE} />
      <L d="M64 75 H86" />
      <Box x={86} y={63} w={46} t="use it" />
      <Note x={70} y={110} t="computed every run" />
      <Box x={180} y={63} w={34} t="5" c={PURE} glow />
      <L d="M214 75 H240" />
      <Box x={240} y={63} w={46} t="use it" />
      <Note x={235} y={110} t="computed once, ahead" c="var(--accent)" />
    </Frame>
  ),
  early_exit: (
    <Frame>
      <Box x={6} y={63} w={34} t="draft" c={MODEL} />
      <L d="M40 75 H50" />
      <Box x={50} y={63} w={38} t="check" c={MODEL} />
      <L d="M88 75 H98" delay={0.3} />
      <Box x={98} y={63} w={38} t="polish" c={MODEL} />
      <Box x={166} y={63} w={34} t="draft" c={MODEL} />
      <L d="M200 75 H210" />
      <Box x={210} y={63} w={38} t="check" c={MODEL} glow />
      <Box x={256} y={63} w={38} t="polish" c={MODEL} dim />
      <L d="M229 63 V36 H290" delay={0.4} />
      <Note x={262} y={30} t="confident: done" c="var(--accent)" />
    </Frame>
  ),
};

/** Plain-English names for the passes, for people who do not read compiler papers. */
export const PASS_TITLES: Record<string, { title: string; what: string }> = {
  cse: { title: 'Merge duplicate work', what: 'Two steps asking the same question become one.' },
  memoize: { title: 'Remember answers', what: 'A repeated question is answered from a cache instead of asked again.' },
  parallelize: { title: 'Run steps side by side', what: 'Steps that do not need each other run at the same time.' },
  context_prune: { title: 'Drop unused context', what: 'Inputs the prompt never shows the model are no longer passed along.' },
  context_trim: { title: 'Shorten long messages', what: 'Very long messages are clipped, so each call sends fewer tokens.' },
  downgrade: { title: 'Use a cheaper model', what: 'A smaller model takes over a step, only if every test still passes.' },
  loop_cutoff: { title: 'Stop loops early', what: 'A loop ends as soon as another round would change nothing.' },
  dce: { title: 'Remove dead steps', what: 'A step whose result nobody uses is removed.' },
  batch: { title: 'Bundle requests', what: 'Many small calls to the same service go out as one.' },
  speculate: { title: 'Start likely work early', what: 'Work that will probably be needed starts before it is asked for.' },
  constfold: { title: 'Pre-compute fixed values', what: 'Anything that is always the same is worked out once, ahead of time.' },
  early_exit: { title: 'Finish when confident', what: 'Stop early when the answer is already good enough.' },
};
