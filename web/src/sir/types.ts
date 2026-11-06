// Shapes from docs/API_FOR_UI.md and docs/IR_SPEC.md. A field the backend does not know
// is absent or null; the UI renders it as "—", never as 0.

export type NodeKind = 'model' | 'tool' | 'retrieval' | 'transform' | 'control' | 'io';
export type Effect = 'pure' | 'read' | 'write' | 'external' | 'irreversible';
export type Determinism = 'deterministic' | 'seeded' | 'stochastic';

export const EFFECTS: Effect[] = ['pure', 'read', 'write', 'external', 'irreversible'];

export type Arg = { node: string; output?: string } | { value: unknown };

export interface NodeProfile {
  samples: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  tokens_in_mean?: number | null;
  tokens_out_mean?: number | null;
  cost_usd_mean?: number | null;
  failure_rate?: number;
  cache_hit_rate?: number;
  slack_ms?: number | null;
  /** measurements missing from some samples: the aggregate has a hole in it */
  partial?: string[] | null;
  tokens_in_variance?: number | null;
  tokens_out_variance?: number | null;
  latency_samples?: number[] | null;
}

/** What a pass did to a node, for badges on the optimized graph. */
export interface NodeMeta {
  impl?: string;
  memoize?: boolean;
  batch_group?: string;
  downgraded?: { from: string; to: string };
  pruned_args?: string[];
  [k: string]: unknown;
}

export interface SirNode {
  id: string;
  kind: NodeKind;
  target: string;
  args?: Record<string, Arg>;
  outputs?: string[];
  effect: Effect;
  determinism?: Determinism;
  reads?: string[];
  writes?: string[];
  cache_key_fields?: string[] | null;
  staleness_s?: number | null;
  region?: string | null;
  profile?: NodeProfile | null;
  origin?: Record<string, unknown>;
  meta?: NodeMeta;
}

export interface Region {
  id: string;
  kind: 'loop' | 'branch' | 'map';
  body: string[];
  condition?: { node: string; output?: string } | null;
  max_iterations?: number | null;
  over?: { node: string; output?: string } | { value: unknown } | null;
}

export interface DataEdge { from: string; to: string; output?: string; arg?: string }
export interface EffectEdge { from: string; to: string; reason?: string }

export interface Graph {
  runnable?: boolean;
  uses_stand_in?: boolean;
  id: string;
  name: string;
  sir_version: number;
  nodes: SirNode[];
  regions?: Region[];
  edges: { data: DataEdge[]; effect: EffectEdge[] };
  inputs?: Record<string, unknown>;
  outputs?: Record<string, { node: string; output?: string }>;
  derived_from?: string | null;
  pipeline?: string[];
  critical_path?: string[];
  critical_path_ms?: number | null;
  created_at?: string;
  n_nodes?: number;
  /** the task this graph really runs on, when the API knows one */
  default_task?: Record<string, unknown> | null;
  meta?: {
    max_concurrency?: number;
    /** nodes a pass removed: id -> reason */
    elided?: Record<string, string>;
    input_defaults?: Record<string, unknown>;
    [k: string]: unknown;
  };
}

export interface GraphSummary {
  /** false for a run measured elsewhere and stored: it can be read here, not run */
  runnable?: boolean;
  id: string;
  name: string;
  sir_version: number;
  n_nodes: number;
  derived_from: string | null;
  pipeline: string[];
  created_at: string;
}

export interface Profile {
  graph_id: string;
  executions: number;
  cold: boolean;
  total: {
    wall_p50_ms?: number | null;
    wall_p95_ms?: number | null;
    tokens_in?: number | null;
    tokens_out?: number | null;
    cost_usd?: number | null;
    provider_calls?: number | null;
    cache_hit_rate?: number | null;
  };
  critical_path: { node: string; latency_p50_ms: number; slack_ms?: number | null }[];
  bottlenecks: { node: string; share_of_critical_path: number; reason: string }[];
  noise_band_pct?: number | null;
  /** provenance of the noise band; null = not measured, never zero */
  noise?: { latency_pct: number | null; samples: number; split: string | null; cost_pct: number | null; method: string } | null;
  split?: string | null;
  nodes?: Record<string, NodeProfile>;
  /** The measured tokens priced at each model's listed price in models.yaml. Empty when tokens are unknown. */
  priced_at?: PricedAt[];
  /** CPU and memory of the Synapse Python process, median over recorded runs. Null for older runs. */
  resource?: ResourceUse | null;
}

export interface PricedAt { model: string; provider: string; cost_usd: number }
export interface ResourceUse { samples: number; cpu_ms_p50: number | null; rss_mb_p50: number | null; scope: string | null }

export interface Transformation {
  pass_name: string;
  nodes_before: string[];
  nodes_after: string[];
  description: string;
}

export interface Finding {
  node: string;
  pass_name: string;
  precondition: string;
  message: string;
}

export interface OptimizeResult {
  graph_id: string;
  derived_from: string;
  strategy: string;
  pipeline: string[];
  transformations: Transformation[];
  predicted?: null | {
    d_latency_ms?: number | null;
    d_tokens?: number | null;
    d_cost_usd?: number | null;
    risk?: number | null;
    confidence?: number | null;
  };
  findings: Finding[];
  /** passes that were legal but found nothing, with a human reason */
  declined?: { pass_name: string; reason: string }[];
  /** passes whose output was discarded because it changed behaviour: should be empty */
  refused?: { pass_name: string; reason: string }[];
  /** the search ran both versions: these are measurements, not predictions */
  measured?: {
    wall_before_ms?: number | null;
    wall_after_ms?: number | null;
    tokens_before?: number | null;
    tokens_after?: number | null;
    cost_before_usd?: number | null;
    cost_after_usd?: number | null;
    noise_band_pct?: number | null;
    candidates_tried?: number | null;
  } | null;
  demo?: boolean;
  verdict?: Verdict | null;
  verdict_note?: string | null;
}

export interface PassInfo { name: string; description: string; precondition: string; on_by_default?: boolean; takes_effect_at_runtime?: boolean }

export interface Explain {
  pass: string;
  description: string;
  precondition: string;
  applied_to: string[];
  transformations: Transformation[];
  findings: Finding[];
  estimate?: { d_latency_ms?: number | null; d_tokens?: number | null; d_cost_usd?: number | null; risk?: number | null; confidence?: number | null; note?: string } | null;
  refused?: unknown;
}

export interface ModelInfo {
  id: string;
  provider: string;
  tag: string;
  determinism: string;
  price_per_mtok: { in: number; out: number } | null;
  fallback: string | null;
  batch: boolean;
}

export interface Measured { wall_p50_ms?: number | null; tokens?: number | null; cost_usd?: number | null }

export interface Verdict {
  decision: 'APPROVE' | 'BLOCK';
  reason: string;
  relation: string;
  split: string;
  /** cold, warm or both: what the runs behind this verdict were. Older stored verdicts lack it. */
  condition?: 'cold' | 'warm' | 'cold+warm' | null;
  n_tasks: number;
  unchanged: number;
  fixed: number;
  broken: number;
  crashed: number;
  d_latency_pct?: number | null;
  d_tokens_pct?: number | null;
  d_cost_pct?: number | null;
  noise_band_pct?: number | null;
  invariant_failures: unknown[];
  cold?: Measured | null;
  warm?: Measured | null;
}

export interface TaskResult {
  task_id: string;
  status: 'unchanged' | 'fixed' | 'broken' | 'crashed';
  before?: unknown;
  after?: unknown;
  /** ndiff lines where the answers differ ("- old", "+ new"). Empty when they match. */
  diff?: string[];
  note?: string | null;
}

/** POST /api/graphs/{id}/force: one chosen pass, applied without search, then judged by the firewall. */
export interface ForceResult {
  pass: string;
  applied: boolean;
  findings: Finding[];
  note: string | null;
  graph_id?: string;
  verdict: Verdict | null;
  per_task?: TaskResult[];
  demo?: boolean;
}

/** GET /api/graphs/{id}/receipt: a tamper-evident record of a firewall verdict. */
export interface Receipt {
  body: {
    receipt_version: number;
    workflow: string;
    original_graph: string;
    optimized_graph: string;
    pipeline: string[];
    verdict: Verdict;
    per_task: { task_id: string; status: TaskResult['status'] }[];
    fake_provider: boolean;
  };
  sha256: string;
  signature: string | null;
}

export interface VerifyResult { verdict: Verdict; per_task: TaskResult[] }

export interface CompareRow {
  variant: string;
  cold: boolean;
  wall_p50_ms?: number | null;
  wall_p95_ms?: number | null;
  tokens?: number | null;
  cost_usd?: number | null;
  accuracy?: number | null;
  provider_calls?: number | null;
  samples?: number | null;
  cache_hits?: number | null;
}

export interface Compare {
  original: string;
  optimized: string;
  split: string;
  n_tasks: number;
  relation: string;
  noise_band_pct?: number | null;
  rows: CompareRow[];
  speedup?: number | null;
  cost_reduction_pct?: number | null;
  accuracy_delta_pp?: number | null;
  caveat?: string | null;
}

export interface BenchWorkflow {
  split?: string;
  model?: string | null;
  verdict_reason?: string | null;
  reportable?: boolean;
  repeat_flips?: { broke: number; fixed: number; tasks: number } | null;
  name: string;
  n_tasks: number;
  relation: string;
  noise_band_pct?: number | null;
  pipeline: string[];
  verdict: string;
  rows: CompareRow[];
}

export interface AblationRow {
  removed: string;
  pipeline: string[];
  samples: number;
  crashed: number;
  wall_p50_ms: number | null;
  tokens_in: number | null;
  cost_usd?: number | null;
  passed: number;
  speedup: number | null;
  verdict: string | null;
  verdict_reason: string | null;
  broken: number | null;
  fixed: number | null;
  effect: { slower_pct: number | null; d_tokens_pct: number | null; within_noise: boolean | null } | null;
}

export interface Ablation {
  meta: { workflow: string; split: string; model: string; relation: string; n_tasks: number; pipeline: string[] };
  noise_band_pct: number | null;
  repeat_flips: { broke: number; fixed: number; tasks: number } | null;
  rows: AblationRow[];
}

export interface BenchReport {
  generated_at: string;
  split: string;
  workflows: BenchWorkflow[];
  chaos: { id: string; planted: string; caught_by: string; verdict: string }[];
  /** Absent on a report published before ablations existed. */
  ablations?: Ablation[];
}

export interface Health { status: string; version?: string; sir_version: number; store_revision?: unknown; graphs?: number; executions?: number }

export type StreamEvent =
  | { type: 'start'; execution_id: string; graph_id?: string; n_nodes: number; concurrency_limit?: number; mode?: string }
  | { type: 'node_start'; node: string; t_ms: number; worker?: number; kind?: string; effect?: string }
  | { type: 'node_end'; node: string; t_ms: number; latency_ms?: number; cached?: boolean; tokens_in?: number | null; tokens_out?: number | null }
  | { type: 'node_skip'; node: string; t_ms?: number; reason: string }
  | { type: 'node_stub'; node: string; t_ms: number; effect: string; reason: string }
  | { type: 'cache_hit'; node: string; t_ms: number; age_s?: number }
  | { type: 'map'; region: string; width: number; nodes: string[]; t_ms?: number }
  | { type: 'loop'; region: string; iterations: number; cap?: number | null; t_ms?: number }
  | { type: 'loop_cutoff'; region: string; iterations: number; reason: string; t_ms?: number }
  | { type: 'branch'; region: string; taken: boolean; nodes: string[]; t_ms?: number }
  | { type: 'speculate'; node: string; region: string; t_ms?: number }
  | { type: 'speculate_discard'; node: string; t_ms?: number }
  | { type: 'end'; execution_id: string; status?: string; wall_ms: number; tokens?: number | null; cost_usd?: number | null; cache_hits?: number | null; error?: string | null }
  | { type: 'error'; node?: string; t_ms?: number; message: string };

export interface AgentsReport {
  agents: { name: string; nodes: string[]; writes: string[]; reads: string[] }[];
  handoffs: { from: string; to: string; output: string }[];
  pairs: { agents: string[]; may_overlap: boolean; reason: string; shared_state?: string[] }[];
}

export interface StorageReport {
  mode: string;
  bytes: Record<string, number>;
  total_mb: number;
  cache_hits_recorded: number;
  tokens_saved: number | null;
  cost_saved_usd: number | null;
  bytes_per_token_saved: number | null;
  limits: { retain_days: number; cache_mb: number; capture_full: boolean };
}

export type LegalStatus = 'yes' | 'cond' | 'no';
export interface LatticeTable {
  transforms: string[];
  tiers: { effect: Effect; legal: Record<string, { status: LegalStatus; why: string | null }> }[];
}

export interface GraphSource { file: string; function: string; code: string }

/* ---------------------------------------------------------------- usps (docs/ADVAY_UI_BRIEF.md) */

export interface ChaosFault { name: string; description: string }

/** POST /graphs/{id}/chaos. Fields beyond the first block depend on the fault. */
export interface ChaosResult {
  fault: string;
  description?: string;
  workflow?: string;
  applicable?: boolean;
  caught?: boolean;
  summary?: string;
  note?: string;
  // unsafe_cache
  verdict?: Verdict;
  per_task?: TaskResult[];
  // relabel_effect
  node?: string;
  target?: string;
  refused?: boolean;
  because?: string;
  // crash
  crashed_at?: string;
  first_status?: string;
  resume_status?: string;
  steps_reused?: number;
  // slow_step
  slowed?: string;
  wall_before_ms?: number | null;
  wall_after_ms?: number | null;
  same_answer?: boolean;
  // new_input
  steps?: RerunStep[];
  reused?: number;
  ran?: number;
}

export interface RerunStep { node: string; target: string; state: string; why: string; tokens: number | null }

export interface RerunResult {
  status: string;
  error: string | null;
  task?: Record<string, unknown>;
  steps: RerunStep[];
  tokens_spent: number | null;
  wall_ms: number | null;
  outputs?: Record<string, unknown> | null;
}

export interface ImportResult {
  graph_id: string;
  format: string;
  nodes: number;
  waste: {
    steps?: number;
    removable: { node: string; target: string; reason: string }[];
    tokens_in_removable: number | null;
    tokens_out_removable: number | null;
    priced_at: PricedAt[];
    serial_ms: number | null;
    dependency_bound_ms: number | null;
    waiting_ms: number | null;
    refused: unknown[];
    rewrites: string[];
  };
}

export interface BreakEven { runs: number | null; never: boolean }
export interface Ledger {
  spent: { runs: number | null; tokens: number | null; cost_usd: number | null; wall_ms: number | null };
  saved_per_run: { tokens: number | null; cost_usd: number | null; wall_ms: number | null };
  break_even: { tokens: BreakEven; cost: BreakEven; wall: BreakEven };
  fake_provider?: boolean;
}

export interface ReplayResult {
  status: string;
  error?: string | null;
  decision?: 'PASS' | 'BLOCK';
  kind?: string;
  tokens_recording_baseline?: number | null;
  tokens_checking_candidate?: number | null;
  new_model_calls?: number | null;
  tasks?: number;
  broken?: string[];
  changed_answers?: string[];
  planned_ms_before?: number | null;
  planned_ms_after?: number | null;
  rows?: { task_id: string; same_answer: boolean; passed_before: boolean | null; passed_after: boolean | null; new_model_calls: number | null }[];
  note?: string;
}

export interface FrontierPoint {
  pipeline: string[];
  wall_p50_ms: number | null;
  tokens: number | null;
  cost_usd: number | null;
  passed: number;
  tasks: number;
  crashed: number;
  on_frontier: boolean;
}
export interface FrontierResult { points: FrontierPoint[]; choice: { found: boolean; plan: FrontierPoint | null; note: string } }
export interface FrontierBody { passes?: string[]; max_cost_pct?: number; max_tokens_pct?: number; max_wall_pct?: number }

export interface PassPredictionStats {
  predictions: number;
  measured: number;
  untested: number;
  mean_abs_error_pp: number | null;
  mean_error_pp: number | null;
  mean_predicted_pct: number | null;
  mean_measured_pct: number | null;
}
export interface Predictions { searches: number; workflows: string[]; per_pass: Record<string, PassPredictionStats>; demo: boolean; note: string }

export interface Ceiling {
  serial_ms: number | null;
  floor_ms: number | null;
  now_ms: number | null;
  max_speedup: number | null;
  headroom_pct: number | null;
  chain: string[];
  note: string;
}

export interface Counterexample {
  found: boolean;
  task_id?: string | null;
  input?: Record<string, unknown> | null;
  changed_steps?: string[];
  smallest_breaking_change?: string[];
  targets?: string[];
  before?: unknown;
  after?: unknown;
  note?: string;
}

export interface Quarantine { passes: Record<string, { reason: string; at: number | string | null }> }

export interface TimelineStep {
  node: string;
  target: string;
  effect?: string;
  started_ms: number | null;
  ended_ms: number | null;
  cached: boolean;
  stubbed: boolean;
  discarded: boolean;
  speculative: boolean;
  tokens: number | null;
  tokens_so_far: number | null;
  output_digest: string | null;
  error: string | null;
}
export interface Timeline { execution_id: string; graph_id: string; task_id: string | null; status: string; wall_ms: number | null; steps: TimelineStep[] }
export interface Capsule { body: Record<string, unknown>; sha256: string }

export interface SemanticResult {
  decision: 'PASS' | 'BLOCK' | 'NO_HITS';
  threshold: number;
  semantic_hits: number;
  broken: string[];
  tokens_without: number | null;
  tokens_with: number | null;
  tasks?: number;
  note: string;
}
