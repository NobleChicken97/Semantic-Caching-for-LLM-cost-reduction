#!/usr/bin/env bash
# One-time Lightsail host provisioner (Ubuntu 22.04, Small 2GB).
# Run ONCE over SSH as ubuntu:  curl/upload this file, then `bash lightsail-provision.sh`.
# Idempotent: safe to re-run (only creates what is missing, never overwrites .env).
set -euo pipefail

APP_DIR="/srv/semcache/app"

echo "==> apt + Docker"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg awscli ufw
sudo install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -y
fi
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER" || true

echo "==> swap (emergency buffer only; 2GB RAM is primary)"
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo "==> app directory + repo"
sudo mkdir -p /srv/semcache
sudo chown -R "$USER:$USER" /srv/semcache
if [ ! -d "$APP_DIR/.git" ]; then
  git clone https://github.com/NobleChicken97/Semantic-Caching-for-LLM-cost-reduction.git "$APP_DIR"
fi

echo "==> .env (created once, never overwritten)"
if [ ! -f "$APP_DIR/.env" ]; then
  ADMIN_TOKEN="$(openssl rand -hex 24)"
  PEPPER="$(openssl rand -hex 32)"
  cat > "$APP_DIR/.env" <<EOF
# Generated once on $(date -u +%F). Keep out of git.
MOCK_LLM=true
DOMAIN=localhost
ADMIN_TOKEN=$ADMIN_TOKEN
USER_ID_PEPPER=$PEPPER
SIMILARITY_THRESHOLD=0.85
CACHE_TTL_SECONDS=3600
ECR_URI=522412052856.dkr.ecr.us-east-1.amazonaws.com/semantic-cache-proxy
EOF
  echo "WROTE $APP_DIR/.env — save ADMIN_TOKEN somewhere safe:"
  grep ADMIN_TOKEN "$APP_DIR/.env"
else
  echo ".env exists — leaving untouched."
fi

echo "==> firewall (22/80/443 only; app port 8000 stays internal)"
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable || true
sudo ufw status

echo "DONE. Next: log out/in (docker group), then run scripts/deploy-lightsail.sh"
echo "or push to main and let GitHub Actions deploy."
