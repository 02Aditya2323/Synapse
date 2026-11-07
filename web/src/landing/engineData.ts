// Generated from the compiler itself (synapse0g.graph.passes.all_passes, synapse0g.graph.core.lattice.table),
// so the landing page shows the real passes and rules even when no server is running. Regenerate if they change.
import type { LatticeTable, PassInfo } from '../types/api';

export const PASSES: PassInfo[] = [
 {
  "name": "constfold",
  "description": "Evaluate pure deterministic transforms over literals at compile time.",
  "precondition": "kind == transform, effect == pure, determinism == deterministic, every argument a literal.",
  "on_by_default": true
 },
 {
  "name": "dce",
  "description": "Remove nodes whose results nothing uses.",
  "precondition": "No node in the removed cone is external or irreversible (an unused side effect is not dead).",
  "on_by_default": true
 },
 {
  "name": "context_prune",
  "description": "Drop arguments a model call's prompt never references.",
  "precondition": "The dropped argument is not reachable in the data flow from this node's prompt template, and the node is not stochastic. Verified against the corpus before it ships.",
  "on_by_default": true
 },
 {
  "name": "cse",
  "description": "Collapse nodes that compute the same thing into one.",
  "precondition": "Both nodes are pure, or both are read and within staleness_s; both deterministic or identically seeded. Never external or irreversible.",
  "on_by_default": true
 },
 {
  "name": "loop_cutoff",
  "description": "Stop a loop once two successive iterations produce the same state.",
  "precondition": "The loop body contains no external or irreversible node, and the state hash covers everything the continuation condition reads.",
  "on_by_default": true
 },
 {
  "name": "memoize",
  "description": "Serve a node from the content-addressed cache instead of executing it.",
  "precondition": "pure, or read with a cache entry younger than staleness_s; deterministic or identically seeded; the cache key covers every input that can change the output.",
  "on_by_default": true
 },
 {
  "name": "parallelize",
  "description": "Run independent work concurrently, up to a global and per-provider limit.",
  "precondition": "No data edge and no effect edge between the nodes; write nodes only with disjoint write sets; external and irreversible nodes never participate.",
  "on_by_default": true
 },
 {
  "name": "downgrade",
  "description": "Move a model call to a cheaper registry model.",
  "precondition": "EMPIRICAL: re-running the corpus with only this node downgraded still passes the equivalence relation. Checked by execution in correctness/, never by analysis.",
  "on_by_default": true
 },
 {
  "name": "batch",
  "description": "Fuse several calls to one target into a single provider round-trip.",
  "precondition": "The target declares batch support in models.yaml; every call is pure or read; no effect edge between them; no shared conversational state.",
  "on_by_default": false
 },
 {
  "name": "context_trim",
  "description": "Clip the context sent to a model call, and keep the change only if the firewall approves.",
  "precondition": "EMPIRICAL: the corpus still passes the equivalence relation with the clipped prompts. Checked by execution in correctness/, never by analysis. Never on an unseeded stochastic call.",
  "on_by_default": false
 },
 {
  "name": "early_exit",
  "description": "End the workflow early when a calibrated confidence signal is high enough.",
  "precondition": "The threshold is calibrated on the calib split (never test), and the measured accuracy drop on test is within a declared tolerance. This pass trades accuracy for cost and declares a tolerance rather than claiming equivalence.",
  "on_by_default": false
 },
 {
  "name": "speculate",
  "description": "Start a node before it is known to be needed, inside a budget, and discard it if not.",
  "precondition": "pure or read; inside a branch region; a speculation budget is configured; the discard path is free. Never write, external or irreversible.",
  "on_by_default": false
 }
];

export const LATTICE: LatticeTable = {
 "transforms": [
  "CSE",
  "MEMOIZE",
  "REORDER",
  "PARALLELIZE",
  "BATCH",
  "SPECULATE",
  "ELIDE"
 ],
 "tiers": [
  {
   "effect": "pure",
   "legal": {
    "CSE": {
     "status": "cond",
     "why": "determinism!=stochastic"
    },
    "MEMOIZE": {
     "status": "cond",
     "why": "determinism!=stochastic"
    },
    "REORDER": {
     "status": "yes",
     "why": null
    },
    "PARALLELIZE": {
     "status": "yes",
     "why": null
    },
    "BATCH": {
     "status": "yes",
     "why": null
    },
    "SPECULATE": {
     "status": "yes",
     "why": null
    },
    "ELIDE": {
     "status": "yes",
     "why": null
    }
   }
  },
  {
   "effect": "read",
   "legal": {
    "CSE": {
     "status": "cond",
     "why": "read requires staleness_s"
    },
    "MEMOIZE": {
     "status": "cond",
     "why": "read requires staleness_s"
    },
    "REORDER": {
     "status": "yes",
     "why": null
    },
    "PARALLELIZE": {
     "status": "yes",
     "why": null
    },
    "BATCH": {
     "status": "yes",
     "why": null
    },
    "SPECULATE": {
     "status": "yes",
     "why": null
    },
    "ELIDE": {
     "status": "yes",
     "why": null
    }
   }
  },
  {
   "effect": "write",
   "legal": {
    "CSE": {
     "status": "no",
     "why": "this node mutates workflow state (unspecified keys); re-using a result would skip the write."
    },
    "MEMOIZE": {
     "status": "no",
     "why": "this node mutates workflow state (unspecified keys); re-using a result would skip the write."
    },
    "REORDER": {
     "status": "cond",
     "why": "disjoint writes"
    },
    "PARALLELIZE": {
     "status": "cond",
     "why": "disjoint writes"
    },
    "BATCH": {
     "status": "no",
     "why": "this node has effect write; fusing side-effecting calls would change how many times the effect happens."
    },
    "SPECULATE": {
     "status": "no",
     "why": "this node has effect write; if the speculation turns out not to be needed the effect cannot be taken back, so it is never started early."
    },
    "ELIDE": {
     "status": "cond",
     "why": "nothing reads its keys"
    }
   }
  },
  {
   "effect": "external",
   "legal": {
    "CSE": {
     "status": "no",
     "why": "this node changes the world outside the workflow; re-using a previous result would skip that effect, so it is never reused."
    },
    "MEMOIZE": {
     "status": "no",
     "why": "this node changes the world outside the workflow; re-using a previous result would skip that effect, so it is never reused."
    },
    "REORDER": {
     "status": "no",
     "why": "this node has an observable side effect; its position relative to other side-effecting nodes is fixed and never reordered."
    },
    "PARALLELIZE": {
     "status": "no",
     "why": "this node has an observable side effect; its position relative to other side-effecting nodes is fixed and never reordered."
    },
    "BATCH": {
     "status": "no",
     "why": "this node has effect external; fusing side-effecting calls would change how many times the effect happens."
    },
    "SPECULATE": {
     "status": "no",
     "why": "this node has effect external; if the speculation turns out not to be needed the effect cannot be taken back, so it is never started early."
    },
    "ELIDE": {
     "status": "no",
     "why": "this node is external: its value is unused but its effect is the point, so it is never removed as dead."
    }
   }
  },
  {
   "effect": "irreversible",
   "legal": {
    "CSE": {
     "status": "no",
     "why": "this node is irreversible (it moves money or destroys data); it is never cached, re-ordered or re-run."
    },
    "MEMOIZE": {
     "status": "no",
     "why": "this node is irreversible (it moves money or destroys data); it is never cached, re-ordered or re-run."
    },
    "REORDER": {
     "status": "no",
     "why": "this node has an observable side effect; its position relative to other side-effecting nodes is fixed and never reordered."
    },
    "PARALLELIZE": {
     "status": "no",
     "why": "this node has an observable side effect; its position relative to other side-effecting nodes is fixed and never reordered."
    },
    "BATCH": {
     "status": "no",
     "why": "this node has effect irreversible; fusing side-effecting calls would change how many times the effect happens."
    },
    "SPECULATE": {
     "status": "no",
     "why": "this node has effect irreversible; if the speculation turns out not to be needed the effect cannot be taken back, so it is never started early."
    },
    "ELIDE": {
     "status": "no",
     "why": "this node is irreversible: its value is unused but its effect is the point, so it is never removed as dead."
    }
   }
  }
 ]
};
