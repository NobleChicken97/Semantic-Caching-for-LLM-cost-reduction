#!/usr/bin/env bash
# Live verify for dashboard redesign (runs ON the host).
set -u
cd /srv/semcache/app
echo "== git =="
git log --oneline -1
echo "== health =="
curl -s http://127.0.0.1/health
echo
echo "== dashboard markers =="
TOKEN=$(grep ADMIN_TOKEN .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1/dashboard -o /tmp/d.html
echo "bytes: $(wc -c < /tmp/d.html)"
for m in tune-panel chart-trend anime.esm.min.js theme-toggle m-speedup chart-sweep side-nav section-title "fonts.googleapis.com/css2?family=Inter" "76.8% 0.233 130.85"; do
  printf "%s: %s\n" "$m" "$(grep -c "$m" /tmp/d.html)"
done
echo "== containers =="
docker ps --format '{{.Names}} {{.Status}}'
