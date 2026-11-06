// Before and after, side by side, with what each pass did between them. Hovering a change or a
// finding lights up the nodes it touched on both graphs. Every node keeps its place across the two.
import { useMemo, useState } from 'react';
import { ArrowRight, ShieldAlert, Zap } from 'lucide-react';
import type { Finding, Graph } from './types';
import { SirGraph, useAspect, usePairedLayout } from './graph/SirGraph';
import { NodeDetail } from './graph/NodeDetail';
import type { NodeTint } from './graph/SirNodeCard';
import { EffectLegend } from './graph/EffectLegend';
import { MonoLabel } from './ui';

export interface PassResult {
  pass_name: string;
  applied: boolean;
  transformations: { pass_name: string; nodes_before: string[]; nodes_after: string[]; description: string }[];
  findings: Finding[];
  estimate?: { note?: string | null } | null;
  refused?: string | null;
}

const PARALLEL: NodeTint = { color: 'var(--info)', label: 'runs in parallel' };

/** Nodes the parallelize pass grouped, as tints: concurrency you can see on the graph. */
function parallelTints(g: Graph | undefined): Record<string, NodeTint> {
  const groups = (g?.meta?.parallel_groups as string[][] | undefined) ?? [];
  const out: Record<string, NodeTint> = {};
  groups.forEach((grp, i) => grp.forEach((id) => { out[id] = { ...PARALLEL, label: groups.length > 1 ? `parallel group ${i + 1}` : PARALLEL.label }; }));
  return out;
}

export function Compare({ before, after, passes, beforeLabel = 'As written', afterLabel = 'After the passes', height = 440, asWritten }: {
  before: Graph; after: Graph; passes: PassResult[]; beforeLabel?: string; afterLabel?: string; height?: number;
  /** the order the code runs its steps in, when it differs from the data flow (a framework chain) */
  asWritten?: string[];
}) {
  const [hover, setHover] = useState<{ before: string[]; after: string[] } | null>(null);
  const [selected, setSelected] = useState<{ side: 'before' | 'after'; id: string } | null>(null);
  const pane_ = useAspect<HTMLDivElement>();
  const { left, right } = usePairedLayout(before, after, pane_.aspect);
  // the input and the final-state collector are bookkeeping, not work: their findings say nothing
  const findings = useMemo(() => passes.flatMap((p) => p.findings).filter((f) => f.precondition !== 'not the input node'), [passes]);
  const flagged = useMemo(() => [...new Set(findings.map((f) => f.node))], [findings]);
  // a change that touches only bookkeeping (the input, the final-state collector) changes no work
  const bookkeeping = new Set(before.nodes.filter((n) => n.target === 'workflow_input' || n.target === 'langgraph_state').map((n) => n.id));
  const transformations = passes.filter((p) => p.applied).flatMap((p) => p.transformations)
    .filter((t) => !t.nodes_before.length || t.nodes_before.some((id) => !bookkeeping.has(id)));
  const declined = passes.filter((p) => !p.applied && !p.findings.length && p.estimate?.note);
  const tints = useMemo(() => parallelTints(after), [after]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const groups = useMemo(() => {
    const by = new Map<string, typeof transformations>();
    for (const t of transformations) by.set(t.pass_name, [...(by.get(t.pass_name) ?? []), t]);
    return [...by.entries()];
  }, [transformations]);
  const conc = (g: Graph) => g.meta?.max_concurrency ?? 1;
  const node = selected && (selected.side === 'before' ? before : after).nodes.find((n) => n.id === selected.id);

  const pane = (side: 'before' | 'after') => {
    const g = side === 'before' ? before : after;
    return (
      <div className="flex flex-col border-b border-[var(--line)] last:border-b-0">
        <div className="h-10 px-4 flex items-center gap-2 border-b border-[var(--line)]">
          <span className="text-[13px] font-semibold text-[var(--text-strong)]">{side === 'before' ? beforeLabel : afterLabel}</span>
          <span className="text-[12px] text-[var(--faint)]">{g.nodes.length} nodes · concurrency {conc(g)}</span>
          {side === 'after' && Object.keys(tints).length > 0 && (
            <span className="ml-auto flex items-center gap-1.5 text-[12px] text-[var(--info)]"><span className="w-2.5 h-2.5 rounded-[3px] border border-[var(--info)]" />runs in parallel</span>
          )}
        </div>
        {side === 'before' && asWritten && asWritten.length > 1 && (
          <div className="px-4 py-2 border-b border-[var(--line)] text-[12px] text-[var(--muted)] flex flex-wrap items-center gap-1">
            <span className="text-[var(--faint)] mr-1">the code runs</span>
            {asWritten.map((s, i) => (
              <span key={s} className="inline-flex items-center gap-1 font-mono text-[11.5px]">
                {i > 0 && <ArrowRight className="w-3 h-3 text-[var(--ghost)]" />}{s}
              </span>
            ))}
            <span className="text-[var(--faint)] ml-1">one at a time. The edges are what each step really reads.</span>
          </div>
        )}
        <div ref={side === 'before' ? pane_.ref : undefined} className="relative" style={{ height }}>
          {side === 'before' && left && <SirGraph graph={before} layout={left} highlight={hover?.before} flagged={flagged} showCriticalPath={false}
            selected={selected?.side === 'before' ? selected.id : null} onSelect={(id) => setSelected(id ? { side: 'before', id } : null)} />}
          {side === 'after' && right && <SirGraph graph={after} layout={right} highlight={hover?.after} flagged={flagged} tints={tints} showCriticalPath={false}
            selected={selected?.side === 'after' ? selected.id : null} onSelect={(id) => setSelected(id ? { side: 'after', id } : null)} />}
          {node && selected?.side === side && (
            <div className="absolute top-0 right-0 bottom-0 w-[340px] z-10 overflow-y-auto">
              <NodeDetail graph={g} node={node} findings={findings.filter((f) => f.node === node.id)} onClose={() => setSelected(null)} />
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="panel overflow-hidden">
      <div className="grid lg:grid-cols-[1fr_340px]">
        <div className="min-w-0 lg:border-r border-[var(--line)]">
          {pane('before')}
          {pane('after')}
        </div>
        <div className="flex flex-col bg-[var(--panel)] overflow-y-auto lg:max-h-[var(--compare-h)]" style={{ ['--compare-h' as string]: `${2 * height + 80 + (asWritten ? 40 : 0)}px` }}>
          <div className="p-4 border-b border-[var(--line)]">
            <MonoLabel>Pipeline</MonoLabel>
            <div className="mt-1.5 font-mono text-[13px] text-[var(--text-strong)] flex flex-wrap items-center gap-1">
              {passes.filter((p) => p.applied).length === 0 && <span className="text-[var(--faint)] font-sans">No pass changed anything</span>}
              {passes.filter((p) => p.applied).map((p, i) => (
                <span key={p.pass_name} className="inline-flex items-center gap-1">
                  {i > 0 && <ArrowRight className="w-3 h-3 text-[var(--ghost)]" />}{p.pass_name}
                </span>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {[
                ['Steps', `${before.nodes.length} → ${after.nodes.length}`],
                ['Concurrency', `${conc(before)} → ${conc(after)}`],
              ].map(([k, v]) => (
                <div key={k} className="rounded-[var(--radius-sm)] border border-[var(--line)] bg-[var(--bg)] px-3 py-2">
                  <div className="text-[11.5px] text-[var(--faint)]">{k}</div>
                  <div className="font-mono text-[15px] text-[var(--text-strong)]">{v}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="p-4 border-b border-[var(--line)]">
            <MonoLabel>Transformations ({transformations.length})</MonoLabel>
            <ol className="mt-2 space-y-1">
              {groups.map(([name, ts]) => ts.length > 2 && !open.has(name) ? (
                <li key={name} onMouseEnter={() => setHover({ before: ts.flatMap((t) => t.nodes_before), after: ts.flatMap((t) => t.nodes_after) })} onMouseLeave={() => setHover(null)}
                  className="rounded-[var(--radius-sm)] p-2 -mx-2 hover:bg-[var(--raised)] cursor-default">
                  <div className="flex items-center gap-1.5 font-mono text-[12px] text-[var(--accent)]"><Zap className="w-3 h-3" strokeWidth={2} />{name} × {ts.length}</div>
                  <div className="text-[12.5px] text-[var(--text)] mt-0.5 [overflow-wrap:anywhere]">{ts[0].description}</div>
                  <button onClick={() => setOpen(new Set([...open, name]))} className="text-[12px] text-[var(--info)] mt-1 hover:underline">and {ts.length - 1} more like it</button>
                </li>
              ) : ts.map((t, i) => (
                <li key={`${name}${i}`} onMouseEnter={() => setHover({ before: t.nodes_before, after: t.nodes_after })} onMouseLeave={() => setHover(null)}
                  className="rounded-[var(--radius-sm)] p-2 -mx-2 hover:bg-[var(--raised)] cursor-default">
                  <div className="flex items-center gap-1.5 font-mono text-[12px] text-[var(--accent)]">
                    <Zap className="w-3 h-3" strokeWidth={2} />{t.pass_name}
                  </div>
                  <div className="text-[12.5px] text-[var(--text)] mt-0.5 [overflow-wrap:anywhere]">{t.description}</div>
                </li>
              )))}
            </ol>
          </div>
          <div className="p-4 border-b border-[var(--line)]">
            <MonoLabel className="!text-[var(--warn)]">Not optimized, and why ({findings.length})</MonoLabel>
            {findings.length === 0 && <p className="text-[12px] text-[var(--faint)] mt-2">Every precondition checked held.</p>}
            <ul className="mt-2 space-y-1">
              {findings.map((f, i) => (
                <li key={i} onMouseEnter={() => setHover({ before: [f.node], after: [f.node] })} onMouseLeave={() => setHover(null)}
                  className="rounded-[var(--radius-sm)] p-2 -mx-2 hover:bg-[var(--raised)] cursor-default">
                  <div className="flex items-center gap-1.5 font-mono text-[11.5px] text-[var(--warn)]">
                    <ShieldAlert className="w-3.5 h-3.5" strokeWidth={1.75} />{f.node} / {f.pass_name}
                  </div>
                  <div className="text-[12.5px] text-[var(--text)] mt-0.5 [overflow-wrap:anywhere]">{f.message}</div>
                </li>
              ))}
            </ul>
          </div>
          {declined.length > 0 && (
            <div className="p-4">
              <MonoLabel>Allowed, but nothing to change ({declined.length})</MonoLabel>
              <ul className="mt-2 space-y-1.5">
                {declined.map((d) => (
                  <li key={d.pass_name} className="text-[12.5px]">
                    <span className="font-mono text-[11.5px] text-[var(--muted)]">{d.pass_name}</span>
                    <div className="text-[var(--text)]">{d.estimate?.note}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
      <div className="border-t border-[var(--line)] px-4 py-2.5"><EffectLegend compact /></div>
    </div>
  );
}
