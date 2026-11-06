import { memo } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { clsx } from 'clsx';
import { ArrowDownRight, Layers, Lock, Repeat2, Scissors, ShieldAlert, SkipForward } from 'lucide-react';
import type { Region, SirNode } from '../types';
import type { InstanceState, NodeRun } from '../stream';
import { ms } from '../format';
import { KIND_ICON, effectColor } from './effects';

export const NODE_W = 228;
export const NODE_H = 74;

export type SirNodeData = {
  node: SirNode;
  /** the graph name, stripped from targets like "research_fanout.plan_prompt" */
  graphName: string;
  direction: 'LR' | 'TB';
  critical: boolean;
  highlighted: boolean;
  dimmed: boolean;
  flagged: boolean; // has a finding (why it was not optimized)
  selected: boolean;
  /** graph.meta.elided[node.id]: a pass removed this node in the optimized form */
  elided?: string;
  run?: NodeRun;
  runMode: boolean; // a run is in progress or finished: un-run nodes look pending
  /** an outside verdict on this node (what reran, which steps a fault touched): frame, glow, label */
  tint?: NodeTint;
};

export interface NodeTint { color: string; label: string; title?: string }

export type SirFlowNode = Node<SirNodeData, 'sir'>;

/** "research_fanout.plan_prompt" -> "plan_prompt" when the prefix is the workflow's own name. */
export function shortTarget(target: string, graphName: string) {
  const base = graphName.replace(/\s*\(optimized\)\s*$/, '');
  return target.startsWith(`${base}.`) ? target.slice(base.length + 1) : target;
}

/** The label a person reads: the declaration (`meta.impl`) when several nodes share a model id. */
export function nodeLabel(node: SirNode, graphName: string) {
  // a framework step is named by its own node name, not by the runtime's implementation key
  if (node.target.startsWith('langgraph:')) return node.target.slice('langgraph:'.length);
  if (node.target === 'langgraph_state') return 'final state';
  if (node.target === 'workflow_input') return 'input';
  if (typeof node.meta?.label === 'string') return node.meta.label;
  const impl = node.meta?.impl;
  return impl && !impl.startsWith('langgraph.') ? impl : shortTarget(node.target, graphName);
}

const PIP: Record<InstanceState, string> = {
  pending: 'var(--line-strong)',
  running: 'var(--accent)',
  done: 'var(--ok)',
  cached: 'var(--info)',
  error: 'var(--bad)',
};

/** One cell per runtime instance of this node. Six lit at once is parallelism you can see. */
function Pips({ run }: { run: NodeRun }) {
  const shown = run.instances.slice(0, 12);
  return (
    <span className="flex items-center gap-[2px]" title={`${run.instances.length} instances, ${run.active} running`}>
      {shown.map((i) => (
        <span
          key={i.id}
          className={clsx('w-[6px] h-[9px] rounded-[1px]', i.state === 'running' && 'animate-rec-pulse')}
          style={{ background: PIP[i.state], boxShadow: i.state === 'running' ? `0 0 6px ${PIP.running}` : undefined }}
        />
      ))}
      {run.instances.length > shown.length && <span className="text-[var(--faint)]">+{run.instances.length - shown.length}</span>}
    </span>
  );
}

const CHAMFER = 'polygon(9px 0, calc(100% - 9px) 0, 100% 9px, 100% calc(100% - 9px), calc(100% - 9px) 100%, 9px 100%, 0 calc(100% - 9px), 0 9px)';

function Badges({ node }: { node: SirNode }) {
  const m = node.meta ?? {};
  const b: { icon: React.ReactNode; title: string; text?: string }[] = [];
  if (m.memoize) b.push({ icon: <Repeat2 className="w-3 h-3" />, title: 'memoize: may be served from the content-addressed cache' });
  if (m.downgraded) b.push({ icon: <ArrowDownRight className="w-3 h-3" />, title: `downgrade: ${m.downgraded.from} → ${m.downgraded.to}`, text: m.downgraded.to });
  if (m.batch_group) b.push({ icon: <Layers className="w-3 h-3" />, title: `batch: group ${m.batch_group}` });
  if (m.pruned_args?.length) b.push({ icon: <Scissors className="w-3 h-3" />, title: `context_prune: dropped ${m.pruned_args.join(', ')}` });
  if (!b.length) return null;
  return (
    <span className="flex items-center gap-1">
      {b.map((x, i) => (
        <span key={i} title={x.title} className="flex items-center gap-0.5 h-4 px-1 rounded-[3px] bg-[var(--accent)]/12 text-[var(--accent)] font-mono text-[9.5px] normal-case tracking-normal">
          {x.icon}
          {x.text}
        </span>
      ))}
    </span>
  );
}

function SirNodeCardImpl({ data }: NodeProps<SirFlowNode>) {
  const { node, graphName, direction, critical, highlighted, dimmed, flagged, selected, elided, run, runMode, tint } = data;
  const Icon = KIND_ICON[node.kind] ?? KIND_ICON.tool;
  const color = effectColor(node.effect);
  const irreversible = node.effect === 'irreversible';
  const state = run?.state;
  const pending = runMode && !run;
  const profiled = !!node.profile && node.profile.samples > 0;
  const stubbed = state === 'stubbed';

  const glow = tint
    ? `drop-shadow(0 0 9px ${tint.color})`
    : state === 'running'
      ? 'drop-shadow(0 0 10px var(--accent))'
      : stubbed
        ? `drop-shadow(0 0 12px ${color})`
        : state === 'cached'
          ? 'drop-shadow(0 0 6px var(--info))'
          : undefined;
  const frame = tint
    ? tint.color
    : state === 'running'
      ? 'var(--accent)'
      : stubbed
        ? color
        : state === 'error'
          ? 'var(--bad)'
          : highlighted
            ? 'var(--text-strong)'
            : selected
              ? 'var(--text)'
              : critical
                ? 'var(--crit)'
                : irreversible
                  ? 'var(--fx-irreversible)'
                  : 'var(--line-strong)';

  const target = direction === 'LR' ? Position.Left : Position.Top;
  const source = direction === 'LR' ? Position.Right : Position.Bottom;

  return (
    <div
      className={clsx('relative transition-[opacity,filter] duration-200', (dimmed || pending || state === 'skipped' || elided) && 'opacity-30')}
      style={{ width: NODE_W, height: NODE_H, filter: glow }}
      title={`${node.id} · ${node.kind} · ${node.target} · effect ${node.effect}${run?.note ? `\n${run.note}` : ''}${elided ? `\nelided: ${elided}` : ''}${tint?.title ? `\n${tint.title}` : ''}`}
    >
      {/* frame: a 1.5px chamfered border drawn as the outer shape, the body inset inside it */}
      <div className="absolute inset-0" style={{ clipPath: CHAMFER, background: frame }} />
      <div
        className={clsx('absolute inset-[1.5px] bg-[var(--panel)]', irreversible && 'fx-irreversible-hatch')}
        style={{
          clipPath: CHAMFER,
          background: state === 'done' ? 'color-mix(in srgb, var(--ok) 9%, var(--panel))' : stubbed ? `color-mix(in srgb, ${color} 12%, var(--panel))` : undefined,
        }}
      />
      {/* pin rails */}
      <div
        className="absolute top-[18px] bottom-[18px] -left-[4px] w-[4px] opacity-60"
        style={{ backgroundImage: `repeating-linear-gradient(180deg, ${color} 0 2px, transparent 2px 6px)` }}
      />
      <div
        className="absolute top-[18px] bottom-[18px] -right-[4px] w-[4px] opacity-60"
        style={{ backgroundImage: `repeating-linear-gradient(180deg, ${color} 0 2px, transparent 2px 6px)` }}
      />
      <div className="absolute top-0 left-[22px] right-[22px] h-[2px]" style={{ background: color }} />
      <Handle type="target" position={target} className="!w-1.5 !h-1.5 !min-w-0 !border-0 !bg-[var(--ghost)]" />
      <div className="relative h-full pl-3.5 pr-3 pt-2.5 pb-2 flex flex-col justify-between">
        <div className="flex items-center gap-1.5 min-w-0">
          <Icon className="w-3.5 h-3.5 shrink-0" strokeWidth={1.75} style={{ color }} />
          {node.id !== nodeLabel(node, graphName) && node.id !== 'n_state' && <span className="font-mono text-[10.5px] text-[var(--faint)] shrink-0">{node.id}</span>}
          <span className={clsx('text-[13px] font-semibold truncate', elided ? 'line-through text-[var(--muted)]' : 'text-[var(--text-strong)]')}>
            {nodeLabel(node, graphName)}
          </span>
          <span className="ml-auto flex items-center gap-1 shrink-0">
            <Badges node={node} />
            {flagged && <ShieldAlert className="w-3.5 h-3.5 text-[var(--warn)]" strokeWidth={1.75} />}
          </span>
        </div>
        {node.meta?.impl && (
          <div className="-mt-1 font-mono text-[10px] text-[var(--ghost)] truncate">{node.target}</div>
        )}
        {!!node.meta?.agent && (
          <div className="-mt-1 font-mono text-[10px] text-[var(--info)] truncate">@{String(node.meta.agent)}</div>
        )}
        <div className="flex items-center gap-2 text-[11px]">
          <span style={{ color }} className={clsx(irreversible && 'font-semibold')}>
            {node.effect}
          </span>
          {node.determinism === 'stochastic' && <span className="text-[var(--faint)]">stochastic</span>}
          <span className="ml-auto flex items-center gap-1.5 text-[var(--muted)]">
            {run && run.instances.length > 1 && <Pips run={run} />}
            {state === 'running' && run!.instances.length <= 1 && <span className="text-[var(--accent)]">running</span>}
            {state === 'cached' && <span className="text-[var(--info)]">cache hit</span>}
            {stubbed && (
              <span className="flex items-center gap-1 font-semibold" style={{ color }}>
                <Lock className="w-3 h-3" /> not run
              </span>
            )}
            {state === 'skipped' && (
              <>
                <SkipForward className="w-3 h-3" /> skipped
              </>
            )}
            {state === 'error' && <span className="text-[var(--bad)]">error</span>}
            {state === 'done' && run?.start_ms !== undefined && run?.end_ms !== undefined && (
              <span className="font-mono text-[var(--ok)]">{ms(run.end_ms - run.start_ms)}</span>
            )}
            {tint && <span className="font-semibold" style={{ color: tint.color }}>{tint.label}</span>}
            {!tint && !state && elided && <span className="text-[var(--muted)]">elided</span>}
            {!tint && !state && !elided && profiled && <span className="font-mono">p50 {ms(node.profile!.latency_p50_ms)}</span>}
          </span>
        </div>
      </div>
      <Handle type="source" position={source} className="!w-1.5 !h-1.5 !min-w-0 !border-0 !bg-[var(--ghost)]" />
    </div>
  );
}

export const SirNodeCard = memo(SirNodeCardImpl);

export type RegionData = { region: Region; width: number; height: number; label?: string };
export type RegionFlowNode = Node<RegionData, 'region'>;

/** A dashed box around a map / loop / branch region's body. Behind the nodes, not clickable. */
function RegionBoxImpl({ data }: NodeProps<RegionFlowNode>) {
  const { region, width, height, label } = data;
  const over = region.over && 'node' in region.over ? `${region.over.node}.${region.over.output ?? 'value'}` : null;
  const text =
    label ??
    (region.kind === 'map'
      ? `map${over ? ` over ${over}` : ''}`
      : region.kind === 'loop'
        ? `loop${region.max_iterations ? ` ≤ ${region.max_iterations}` : ''}`
        : 'branch');
  return (
    <div className="relative pointer-events-none" style={{ width, height }}>
      <div className="absolute inset-0 border border-dashed border-[var(--line-strong)] rounded-[4px] bg-[var(--text)]/[.015]" />
      <div
        className="absolute left-2.5 top-0.5 right-2 text-[11px] text-[var(--muted)] whitespace-nowrap overflow-hidden text-ellipsis"
        title={region.kind === 'map' ? 'The body runs once per item. Parallelize runs the items concurrently.' : region.id}
      >
        {text}
      </div>
    </div>
  );
}

export const RegionBox = memo(RegionBoxImpl);
