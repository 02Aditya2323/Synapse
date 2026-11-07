export type Effect = 'pure' | 'read' | 'write' | 'external' | 'irreversible';
export interface PassInfo { name: string; description: string; precondition: string; on_by_default?: boolean; takes_effect_at_runtime?: boolean }
export type LegalStatus = 'yes' | 'cond' | 'no';
export interface LatticeTable {
  transforms: string[];
  tiers: { effect: Effect; legal: Record<string, { status: LegalStatus; why: string | null }> }[];
}
