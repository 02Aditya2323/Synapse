// What each effect tier means, in plain words. Which rewrites a tier allows comes from
// GET /api/lattice, and the passes come from GET /api/passes.
import type { Effect } from '../types/api';

export const TIERS: {
  effect: Effect;
  meaning: string;
  example: string;
  verdict: string;
}[] = [
  {
    effect: 'pure',
    meaning: 'Same inputs, same outputs. Nothing observable happens.',
    example: 'a seeded model call, a parse, a string transform',
    verdict: 'Every rewrite is allowed.',
  },
  {
    effect: 'read',
    meaning: 'Observes the outside world, which may change between calls.',
    example: 'web search, vector retrieval, reading a file',
    verdict: 'Answers are reused only while they are still fresh.',
  },
  {
    effect: 'write',
    meaning: 'Mutates state inside the workflow.',
    example: "an agent's scratchpad or memory",
    verdict: 'Kept in order. Runs side by side only when the notes never overlap.',
  },
  {
    effect: 'external',
    meaning: 'Mutates the world outside the workflow.',
    example: 'a POST to an API, sending an email, writing a file',
    verdict: 'Never repeated, reordered, cached or started early.',
  },
  {
    effect: 'irreversible',
    meaning: 'Moves money, or destroys something that cannot come back.',
    example: 'placing an order, charging a card, deleting a record',
    verdict: 'Never even run while Synapse is optimizing, testing or benchmarking.',
  },
];
