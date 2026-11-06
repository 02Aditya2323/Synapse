# synapse0g dashboard

The dashboard presents the project's agent optimization workflow: inspect an agent,
understand its effect-typed graph, compare proposed transformations, and examine
measured outcomes before accepting a change. This document describes the current
implementation, rather than a future design or a claim about implementation origins.

## Pages and navigation

The console shell (`web/src/components/Shell.jsx`) has a fixed-width, sticky sidebar
with the SYNAPSE0G wordmark, a branching signal glyph, five navigation entries, and
an API connection indicator. Selection is highlighted with an orange marker.

| Page | Purpose |
| --- | --- |
| Results | Lead with the compiled supplier-check study and show research, maths, and blocked retail results. Open a result for its detailed evidence. |
| Connect an agent | Inspect pasted Python or a local path, choose a sample agent, or read the client wrapping and LangGraph compilation examples. |
| Recorded runs | Inspect calls captured by the wrapper, repeated calls, graph transformations, and estimated opportunities. |
| Workflows | Explore the five bundled workflow graphs and the transformations and refusals produced by their passes. |
| Run history | Browse saved runs, including unsuccessful and invalid runs. |

The page selection lives in `web/src/store.js` and uses URL hashes. Result details
are rendered by `web/src/components/Result.jsx`.

## Visual language

`web/src/styles/tokens.css` defines the shared palette and typography;
`web/src/index.css` applies these to the console and maps Tailwind colour names.
The background is near-black (`#05070b`), with raised navy panels, restrained borders,
and orange (`#f97316`) for navigation and emphasis. Large result numbers establish
hierarchy; secondary labels identify units, providers, and the meaning of each metric.

Geist Sans supplies interface text, JetBrains Mono supplies code and numerical detail,
Archivo supplies display headings, and Instrument Serif is available for editorial
accents. Fonts are loaded through the project's Fontsource packages.

Verdict colours have specific meanings: green for approval, red for blocks or broken
tasks, amber for warnings, and blue for informational highlights. Effect colours are
separate: pure is teal, read is blue, write is lavender, external is amber, and
irreversible is red. Labels and legends explain these categories alongside colour.

The sidebar selection uses a spring transition. Background light and surface effects
are defined in the CSS files; reduced-motion rules suppress the console sky animation.
The current shell uses a 248 px sidebar and a main content width capped at 1400 px.
Some content grids adapt at breakpoints; the shell does not implement a mobile drawer.

## Graph presentation

`web/src/sir/Compare.tsx` places the before and after graphs vertically in one panel,
with transformations and findings alongside them on wide layouts. A shared layout
helps retain node positions across the comparison. Headers show node counts and
concurrency. When supplied, the original code's execution order is shown separately
from the graph's data dependencies.

Hovering a transformation or finding highlights affected nodes. Selecting a node
opens its detail panel. Parallel groups receive an informational tint; findings and
effect labels explain why a step was protected. These views use the SIR data supplied
by the backend, with graph rendering and layout in `web/src/sir/graph/`.

## Evidence and wording

Inspection and recording show opportunities; compilation executes a rewritten graph.
The interface must distinguish graph-derived bounds and proposed savings from measured
results. Recorded runs explicitly describe their dependency time as a bound, not an
achieved speedup. Approval and refusal remain visible alongside savings.

The supplier study uses a synthetic marketplace. Its saved comparison distinguishes
the sequential LangGraph app, a manually wired fan-out, and the compiled app. The
reported median times are 55.5 s, 30.6 s, and 15.4 s respectively. The hand-wired arm
improves speed but does not match the compiled arm. See `RESULTS.md` for the study's
scope, billing evidence, task outcomes, and differences in order execution.

Keep benchmark values tied to their saved reports. When results or presentation
change, update this document and the README together; do not describe a new run using
numbers from a different study.

## Data and implementation

`web/src/api.js` connects the interface to these endpoints:

| Endpoint | Use |
| --- | --- |
| `/api/feed` | Featured results, workflow data, and history |
| `/api/health` | API status, polled every 15 seconds |
| `/api/samples` | Sample agent source for inspection |
| `/api/map` | Submit source or a path for code inspection |
| `/api/observed` | Recorded run list, polled every 10 seconds |
| `/api/observed/:id` | A selected recorded run |

Backend routes live in `synapse0g/server.py`; result assembly lives in
`synapse0g/showcase.py`. Saved evidence lives under `reports/`. Loading, empty, and
error states are part of the interface and should remain explicit.

The frontend uses React, Tailwind CSS, Zustand, TanStack Query, React Flow, Dagre,
Lucide icons, and Motion. These dependencies support the project's console layout,
graph interactions, and visual styling; their names describe actual implementation
dependencies.

## Maintaining the design

Change shared tokens before introducing page-specific colours. Preserve effect and
verdict meanings, readable units, graph legends, and the distinction between estimates
and measurements. Review the affected page and its loading/error states when changing
layout or interaction. Documentation-only changes should not alter agent execution,
optimization passes, or saved benchmark evidence.
