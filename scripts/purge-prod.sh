#!/usr/bin/env bash
# One-shot production purge (runs ON the host). Reads ADMIN_TOKEN from the
# host .env itself so the secret never travels in a command line.
# Used for the Phase 9 migration (stale prefixed vectors) and anytime a
# clean cache is needed. Log history is preserved by FK-detach design.
set -euo pipefail
cd /srv/semcache/app
TOKEN=$(grep ADMIN_TOKEN .env | cut -d= -f2)
curl -s -X POST http://127.0.0.1/cache/purge \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
echo
curl -s http://127.0.0.1/metrics
echo
