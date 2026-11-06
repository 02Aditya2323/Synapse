import { X } from 'lucide-react';
import type { Finding, Graph, SirNode } from '../types';
import { int, ms, preview, rate, usd } from '../format';
import { EffectChip, MonoLabel } from '../ui';
import { EFFECT_META } from './effects';

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 border-b border-[var(--line)] last:border-0">
      <span className="text-[12px] text-[var(--faint)]">{k}</span>
      <span className="font-mono text-[12px] text-[var(--text)] text-right break-all">{v}</span>
    </div>
  );
}

/** Trim an absolute origin path to the repo-relative part ("bench/workflows/x.py"). */
function repoPath(file: string) {
  const f = file.replace(/\\/g, '/');
  const m = f.match(/(?:^|\/)((?:bench|synapse|examples|tests)\/.*)$/);
  return m ? m[1] : f.split('/').slice(-2).join('/');
}

export function NodeDetail({ graph, node, findings = [], onClose }: {
  graph: Graph; node: SirNode; findings?: Finding[]; onClose: () => void;
}) {
  // samples == 0 means nothing was measured; the backend's zero defaults are not measurements
  const p = node.profile && node.profile.samples > 0 ? node.profile : null;
  const incomingEffect = graph.edges.effect.filter((e) => e.to === node.id);
  const outgoingEffect = graph.edges.effect.filter((e) => e.from === node.id);
  const mine = findings.filter((f) => f.node === node.id);
  const onCrit = graph.critical_path?.includes(node.id);

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex items-start justify-between gap-2 px-4 pt-4">
        <div className="min-w-0">
          <div className="text-[12px] text-[var(--faint)]">
            <span className="font-mono">{node.id}</span>, {node.kind}
          </div>
          <div className="text-[16px] font-semibold text-[var(--text-strong)] break-all">{node.target}</div>
        </div>
        <button onClick={onClose} className="text-[var(--faint)] hover:text-[var(--text)] cursor-pointer p-1" aria-label="Close">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="px-4 pt-3 flex flex-wrap gap-2 items-center">
        <EffectChip effect={node.effect} />
        {node.determinism && <span className="text-[12px] text-[var(--muted)]">{node.determinism}</span>}
        {onCrit && <span className="text-[12px] text-[var(--crit)]">On the critical path</span>}
      </div>
      <p className="px-4 pt-2 text-[12px] text-[var(--muted)] leading-relaxed">
        <span style={{ color: EFFECT_META[node.effect]?.color }}>{node.effect}</span>: {EFFECT_META[node.effect]?.rule}
      </p>

      {(() => {
        const m = node.meta ?? {};
        const elided = graph.meta?.elided?.[node.id];
        const rows: [string, string][] = [];
        if (m.agent) rows.push(['agent', String(m.agent)]);
        if (m.impl) rows.push(['declared as', String(m.impl)]);
        if (m.memoize) rows.push(['memoize', 'can be served from the cache']);
        if (m.downgraded) rows.push(['downgrade', `${m.downgraded.from} → ${m.downgraded.to}`]);
        if (m.batch_group) rows.push(['batch', `group ${m.batch_group}`]);
        if (m.pruned_args?.length) rows.push(['context_prune', `dropped ${m.pruned_args.join(', ')}`]);
        if (elided) rows.push(['elided', elided]);
        if (!rows.length) return null;
        return (
          <div className="mx-4 mt-4 rounded-[var(--radius-sm)] border border-[var(--accent)]/30 bg-[var(--accent)]/[.05] p-3">
            <MonoLabel className="!text-[var(--accent)]">What the optimizer did here</MonoLabel>
            {rows.map(([k, v]) => (
              <div key={k} className="mt-1.5 flex gap-3 text-[12.5px]">
                <span className="text-[12px] text-[var(--accent)] w-24 shrink-0">{k}</span>
                <span className="text-[var(--text)]">{v}</span>
              </div>
            ))}
          </div>
        );
      })()}

      {mine.length > 0 && (
        <div className="mx-4 mt-4 rounded-[var(--radius-sm)] border border-[var(--warn)]/40 bg-[var(--warn-bg)] p-3">
          <MonoLabel className="!text-[var(--warn)]">Why this was not optimized</MonoLabel>
          {mine.map((f, i) => (
            <div key={i} className="mt-2 text-[12.5px] text-[var(--text)]">
              <span className="font-mono text-[11px] text-[var(--warn)]">
                {f.pass_name}: {f.precondition}
              </span>
              <div>{f.message}</div>
            </div>
          ))}
        </div>
      )}

      <div className="px-4 pt-5">
        <MonoLabel>Measured profile</MonoLabel>
        {p ? (
          <div className="mt-1">
            <Row k="Runs" v={p.samples} />
            <Row k="Latency p50" v={ms(p.latency_p50_ms)} />
            <Row k="Latency p95" v={ms(p.latency_p95_ms)} />
            <Row k="Tokens in (mean)" v={int(p.tokens_in_mean)} />
            <Row k="Tokens out (mean)" v={int(p.tokens_out_mean)} />
            <Row k="Cost (mean)" v={usd(p.cost_usd_mean)} />
            <Row k="Failure rate" v={rate(p.failure_rate, 1)} />
            <Row k="Cache hit rate" v={rate(p.cache_hit_rate, 1)} />
            <Row k="Slack" v={ms(p.slack_ms)} />
          </div>
        ) : (
          <p className="mt-2 text-[12.5px] text-[var(--faint)]">Not profiled yet.</p>
        )}
      </div>

      <div className="px-4 pt-5">
        <MonoLabel>Arguments</MonoLabel>
        <div className="mt-1">
          {Object.entries(node.args ?? {}).length === 0 && <p className="text-[12.5px] text-[var(--faint)] mt-1">None</p>}
          {Object.entries(node.args ?? {}).map(([k, a]) => (
            <Row
              key={k}
              k={k}
              v={'node' in a ? <span className="text-[var(--info)]">← {a.node}.{a.output ?? 'value'}</span> : preview(a.value, 60)}
            />
          ))}
        </div>
      </div>

      {(node.reads?.length || node.writes?.length || node.staleness_s != null || node.cache_key_fields) && (
        <div className="px-4 pt-5">
          <MonoLabel>State</MonoLabel>
          <div className="mt-1">
            {!!node.reads?.length && <Row k="Reads" v={node.reads.join(', ')} />}
            {!!node.writes?.length && <Row k="Writes" v={node.writes.join(', ')} />}
            {node.staleness_s != null && <Row k="Staleness window" v={`${node.staleness_s} s`} />}
            {node.cache_key_fields && <Row k="Cache key fields" v={node.cache_key_fields.join(', ') || '(none)'} />}
          </div>
        </div>
      )}

      {(incomingEffect.length > 0 || outgoingEffect.length > 0) && (
        <div className="px-4 pt-5">
          <MonoLabel>Ordering constraints</MonoLabel>
          <ul className="mt-2 space-y-1.5">
            {[...incomingEffect.map((e) => ({ e, dir: 'after' })), ...outgoingEffect.map((e) => ({ e, dir: 'before' }))].map(
              ({ e, dir }, i) => (
                <li key={i} className="text-[12px] text-[var(--muted)]">
                  Must run <span className="text-[var(--text)]">{dir}</span>{' '}
                  <span className="font-mono text-[var(--text)]">{dir === 'after' ? e.from : e.to}</span>
                  {e.reason && <span className="text-[var(--faint)]">: {e.reason}</span>}
                </li>
              ),
            )}
          </ul>
        </div>
      )}

      {node.origin && Object.keys(node.origin).length > 0 && (
        <div className="px-4 pt-5 pb-6">
          <MonoLabel>Origin</MonoLabel>
          <div className="mt-1 font-mono text-[12px] text-[var(--muted)] break-all">
            {'file' in node.origin
              ? `${repoPath(String(node.origin.file))}:${String(node.origin.line ?? '')}`
              : JSON.stringify(node.origin)}
          </div>
        </div>
      )}
    </div>
  );
}
