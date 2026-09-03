#!/usr/bin/env bash
# Fast-loop dashboard refresh: copy a local index.html into the RUNNING
# container (no rebuild, no restart, no pipeline wait). Durability still
# comes from git push + CI + ECR afterwards.
set -euo pipefail
docker cp /tmp/index.html app-app-1:/app/src/proxy/static/index.html
TOKEN=$(grep ADMIN_TOKEN /srv/semcache/app/.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1/dashboard | grep -c -e sweep-wrap -e hero-tiles -e gauge-val
echo fast-loop-ok
