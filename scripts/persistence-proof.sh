#!/usr/bin/env bash
# Persistence proof for the Lightsail deployment (runs ON the host).
# MISS -> HIT -> paraphrase HIT -> container restart -> HIT -> host reboot -> HIT
set -u
BASE="http://127.0.0.1"

ask() {
  curl -s -X POST "$BASE/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"gpt-3.5-turbo\",\"messages\":[{\"role\":\"user\",\"content\":\"$1\"}]}" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("cache_metadata"))'
}

echo "== 1. fresh prompt (expect MISS) =="
ask "What is the capital of France?"
echo "== 2. repeat (expect HIT 1.0) =="
ask "What is the capital of France?"
echo "== 3. paraphrase (expect semantic HIT) =="
ask "Tell me the capital of France."
echo "== metrics =="
curl -s "$BASE/metrics"
echo
