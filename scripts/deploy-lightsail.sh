#!/usr/bin/env bash
# Pull latest image from ECR and restart the stack. Run ON the Lightsail host
# from /srv/semcache/app, or remotely via GitHub Actions (.github/workflows/deploy.yml).
set -euo pipefail

cd /srv/semcache/app
git pull --ff-only || echo "WARN: git pull failed (local changes?) — continuing with current compose files."

# ECR auth: host needs ~/.aws credentials for an IAM user/role with
# AmazonEC2ContainerRegistryReadOnly (provision step documents this).
# Token lasts 12h; login is per-run, nothing long-lived is stored by Docker.
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 522412052856.dkr.ecr.us-east-1.amazonaws.com

set -a
# shellcheck disable=SC1091
source .env
set +a

docker compose pull app || docker compose build app
docker compose up -d
docker image prune -f

echo "==> waiting for /health (max ~3 min incl. cold model load)"
for i in $(seq 1 36); do
  if curl -fsS http://127.0.0.1:8000/health | grep -q '"status":"ok"'; then
    echo "HEALTHY after ~$((i * 5))s"
    curl -s http://127.0.0.1:8000/health
    echo
    docker compose ps
    exit 0
  fi
  sleep 5
done
echo "ERROR: service did not turn healthy — showing logs:"
docker compose logs --tail=100 app
exit 1
