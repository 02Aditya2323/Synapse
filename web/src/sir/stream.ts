// Per-node run state for drawing a run on the graph.

export type NodeRunState = 'running' | 'done' | 'cached' | 'skipped' | 'stubbed' | 'error';
export type InstanceState = 'pending' | 'running' | 'done' | 'cached' | 'error';

export interface NodeRun {
  state: NodeRunState;
  /** instances running right now (a map region emits n2#0, n2#1, … for one SIR node) */
  active: number;
  /** every instance, in index order, with its latest state; `map` pre-allocates them as pending */
  instances: { id: string; state: InstanceState }[];
  start_ms?: number;
  end_ms?: number;
  /** a sentence from the backend, rendered verbatim (stub reason, skip reason, error) */
  note?: string;
  effect?: string;
}
