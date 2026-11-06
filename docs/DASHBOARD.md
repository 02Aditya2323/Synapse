# synapse0g dashboard

The dashboard presents the project's agent optimization workflow: inspect an agent,
understand its effect-typed graph, compare proposed transformations, and examine
measured outcomes before accepting a change. This document describes the current
implementation, rather than a future design or a claim about implementation origins.

## Pages and navigation

The console shell (`web/src/components/Shell.jsx`) has a fixed-width, sticky sidebar
with the SYNAPSE0G wordmark, a branching signal glyph, six compiler navigation entries and separate Pay and run and Funding and jobs entries, and
an API connection indicator. Selection is highlighted with an orange marker.

| Page | Purpose |
| --- | --- |
| Live run | Run the supplier app as written and compiled on 0G, with call events, two live lanes, a shared-clock timeline, and measured outcomes. |
| Pay and run | Quote and confirm a supplier request payment, monitor the paid run, inspect generated Python, and download its delivery ZIP. |
| Funding and jobs | Inspect funding evidence, the local service ledger, budget modes, and queued jobs. |
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
| `/api/live/tasks` | Held-out supplier requests for live runs |
| `/api/live` (POST) | Start a request, optionally cold |
| `/api/live/:id?since=n` | Poll incremental run events |
| `/api/agent`, `/api/jobs` | Service status and job queue; POST jobs to submit work |
| `/api/funding` | Funding stages and their available evidence |
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

## Live runs and funding

The Live run page (`web/src/pages/LiveRun.jsx`) polls incremental events from
`synapse0g/live.py`. The baseline uses an empty call policy; the compiled arm uses
the saved supplier study's selected call policy. Both run concurrently on the same
request. The compiled graph is reused within the server process; “start cold” clears
the live step cache and recompiles. Compilation has its own calls and timing.
The compiled arm runs in benchmark mode and skips the irreversible order step;
the baseline may place an order in the synthetic test marketplace.

A live request is a demonstration, not a new held-out firewall study. The interface
shows the saved study verdict separately from the current request's measurements.
Event delivery uses polling, not a server-sent-event connection. Environment loading
for these provider calls happens inside the live worker.

Funding and jobs (`web/src/pages/PaidAgent.jsx`) distinguishes token identity,
trades, fee splits, delivered credit, spendable credit, and inference receipts.
Unconfigured or unreadable stages remain explicit. The job service currently uses
a local ledger; the end-to-end token-to-compute integration is not established by
the dashboard alone. See [COMFY.md](COMFY.md) for recorded integration findings and
remaining deployment work. External platform names identify integrations, not the
origin of this project's dashboard.

The repository also supports a plain-Python procurement agent and its saved studies.
Its document fixtures, supported compilation shapes, and measured results are
described in the README and RESULTS.md; the Live run page remains the supplier demo.

## Paid runs and delivery assets

`web/src/pages/Pay.jsx` implements `#/pay`. It uses `/api/pay/quote`,
`/api/pay/confirm`, `/api/pay/resume`, `/api/pay/history`, and `/api/pay/<quote>`;
`/api/pay/<quote>/package` serves the delivery archive. Generated code appears with
line markers for its transformations. The current paid workflow is the bundled
supplier agent, with request selection rather than user source upload.

The page displays which component enforces the key cap and distinguishes team test
payments from customer payments. See [PAYMENTS.md](PAYMENTS.md) for the scope of the
server-side cap, delivery dependencies, and the direct-payment flow. Comfy's token
route remains separate and is documented as on hold in that integration guide.

Token artwork is stored in `assets/synapse-token.png` and
`assets/synapse-token-256.png`. These are token identity assets referenced by the
comfy setup, not the console's branching SVG glyph. Their filenames alone do not
establish authorship or licensing history.
