#!/bin/sh
# Start the service. /app/data is where jobs, payments, the spend ledger and deliveries live: on Render, a
# persistent disk is mounted there. On first start it is seeded with the recorded compile trace (so the first
# paid job does not pay to compile the example agent again) and the last 0G catalog read.
set -e
mkdir -p /app/data
for f in paid_trace.json catalog_cache.json; do
  [ -f "/app/data/$f" ] || cp "/app/deploy/seed/$f" "/app/data/$f"
done
exec python -m synapse0g.cli ui --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
