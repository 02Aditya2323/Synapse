# Deploying synapse0g on Render

One Docker web service runs everything: the landing page, the console, the API, the payment desk
(its deposit watcher included) and the optimizer agent's paid jobs.

## Files

| File | What it does |
|---|---|
| `Dockerfile` | builds the web app (node), then installs synapse0g with its agent extras (python 3.11) |
| `deploy/start.sh` | seeds `/app/data` on first start (the recorded compile trace, the last 0G catalog), then serves on `$PORT` |
| `render.yaml` | the Render blueprint: the service, its health check (`/api/health`), its secrets and a 1 GB disk at `/app/data` |
| `.dockerignore` | keeps `.env`, `.venv`, `data/` and `node_modules` out of the image |

## Steps

1. Push the repository to GitHub (the blueprint deploys from it).
2. Render dashboard → **New → Blueprint** → pick the repository. Render reads `render.yaml`.
3. Set the secrets it asks for (never in the repo):
   - `ZG_API_KEY`: the team's 0G key, used by the live runs (the local `.env` calls it `0G_API_KEY`;
     Render names cannot start with a digit, and synapse0g reads either name)
   - `ZG_AGENT_KEY`: the optimizer agent's capped Router key; paid jobs spend only through it
   - `ZG_MANAGEMENT_KEY`: read-only management key (`account:read`, `keys:read`)
   - `GROQ_API_KEY`: optional
4. Deploy. The first build takes a few minutes. The site is then at `https://<service>.onrender.com`:
   `/` is the landing page, `/#/pay` the console.

## State

Jobs, payments, the spend ledger, deliveries and job progress are SQLite and files under
`/app/data`, on the persistent disk, so they survive restarts and deploys. A disk needs a paid
plan (Starter). On the free plan, remove `disk:` from `render.yaml`: the service works, but its
state resets on every restart and it sleeps after 15 idle minutes. Payments are on chain either
way: a deposit made while the service slept is found by tx hash, or by the watcher when it wakes.

Keep one instance: spending reservations are coordinated inside one process and one SQLite file.

## Security notes

- Keys are read from the environment on the server and never returned by any endpoint.
- "Your code" runs the pasted agent on the server, to compile and optimize it. On a public URL
  that means running a stranger's code: fine for a demo you control, not for open sign-ups
  without a sandbox.
