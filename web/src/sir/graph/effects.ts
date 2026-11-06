import type { Effect, NodeKind } from '../types';
import { Bot, Wrench, Search, Shuffle, GitBranch, ArrowRightLeft, type LucideIcon } from 'lucide-react';

export const EFFECT_META: Record<Effect, { color: string; label: string; rule: string }> = {
  pure: { color: 'var(--fx-pure)', label: 'pure', rule: 'any transform: CSE, memoize, reorder, parallelize, elide' },
  read: { color: 'var(--fx-read)', label: 'read', rule: 'memoize within a staleness window, reorder among reads, batch' },
  write: { color: 'var(--fx-write)', label: 'write', rule: 'ordered per state key; parallel only with disjoint keys' },
  external: { color: 'var(--fx-external)', label: 'external', rule: 'never re-run, reordered, cached or speculated' },
  irreversible: {
    color: 'var(--fx-irreversible)',
    label: 'irreversible',
    rule: 'as external, and never executed during optimization, verification or benchmarks',
  },
};

export const effectColor = (e: Effect | string) => EFFECT_META[e as Effect]?.color ?? 'var(--fx-external)';

export const KIND_ICON: Record<NodeKind, LucideIcon> = {
  model: Bot,
  tool: Wrench,
  retrieval: Search,
  transform: Shuffle,
  control: GitBranch,
  io: ArrowRightLeft,
};
