"""Black-box smoke suite against a RUNNING semantic-cache-proxy server.

This is the CI/deploy acceptance gate: it talks to the server purely over
HTTP (no internals imported), so it validates the same contract a real
OpenAI SDK client would see.

Usage:
    python scripts/smoke_test.py [BASE_URL]

Requires MOCK_LLM=true on the target server (zero-spend by design).
Exits 0 when every check passes, 1 otherwise.

Covered, in order:
  1. /health liveness (retried while the server boots)
  2. OpenAI response-shape contract on a fresh prompt (MISS)
  3. Exact-repeat -> HIT with similarity 1.0 and identical answer
  4. Paraphrase -> semantic HIT above threshold
  5. Same messages, different model -> MISS (cache-key isolation)
  6. X-Cache-Bypass header -> BYPASS outcome
  7. /metrics accounting matches the requests just made
  8. /logs/recent contains every outcome with sane latencies
  9. /cache/purge clears entries; a purged prompt misses again
"""

from __future__ import annotations

import sys
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = 60.0
BOOT_DEADLINE_S = 120.0

_failures: list[str] = []
_passed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failures.append(f"{name} :: {detail}")
        print(f"  FAIL  {name}  [{detail}]")


def chat(
    client: httpx.Client,
    content: str,
    model: str = "gpt-3.5-turbo",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return client.post(
        f"{BASE}/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": content}]},
        headers=headers or {},
        timeout=REQUEST_TIMEOUT,
    )


def wait_until_healthy(client: httpx.Client) -> bool:
    print(f"Waiting for {BASE}/health (up to {int(BOOT_DEADLINE_S)}s) ...")
    deadline = time.time() + BOOT_DEADLINE_S
    while time.time() < deadline:
        try:
            r = client.get(f"{BASE}/health", timeout=5.0)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1.5)
    return False


def main() -> int:
    with httpx.Client() as client:
        # --- 0. Boot wait + clean slate -----------------------------------
        if not wait_until_healthy(client):
            print(f"FATAL: {BASE}/health never became healthy")
            return 1
        print("Server is healthy.\n")

        purge = client.post(f"{BASE}/cache/purge", json={}, timeout=REQUEST_TIMEOUT)
        check(
            "purge endpoint reachable (auth off)",
            purge.status_code == 200,
            f"status={purge.status_code}",
        )

        baseline = client.get(f"{BASE}/metrics", timeout=REQUEST_TIMEOUT).json()
        t0_requests = baseline["total_requests"]

        unique = f"smoke-{uuid.uuid4().hex[:10]}: what is the capital of France?"

        # --- 1. Fresh prompt: MISS + OpenAI shape -------------------------
        r1 = chat(client, unique)
        check(
            "fresh prompt returns 200",
            r1.status_code == 200,
            f"status={r1.status_code} body={r1.text[:200]}",
        )
        b1 = r1.json()
        required_keys = {
            "id",
            "object",
            "created",
            "model",
            "choices",
            "usage",
            "cache_metadata",
        }
        check(
            "response carries full OpenAI contract",
            required_keys <= set(b1),
            f"missing={required_keys - set(b1)}",
        )
        check("object == chat.completion", b1.get("object") == "chat.completion")
        check(
            "choice has assistant role",
            b1["choices"][0]["message"]["role"] == "assistant",
        )
        check("answer text non-empty", len(b1["choices"][0]["message"]["content"]) > 0)
        check(
            "first call is MISS",
            b1["cache_metadata"]["outcome"] == "MISS",
            str(b1["cache_metadata"]),
        )
        check("model echoed back correctly", b1["model"] == "gpt-3.5-turbo")

        # --- 2. Exact repeat: HIT ------------------------------------------
        r2 = chat(client, unique)
        b2 = r2.json()
        check(
            "exact repeat is HIT",
            b2["cache_metadata"]["outcome"] == "HIT",
            str(b2.get("cache_metadata")),
        )
        check(
            "exact repeat similarity == 1.0",
            b2["cache_metadata"].get("similarity_score") == 1.0,
            str(b2["cache_metadata"]),
        )
        check(
            "cached answer identical to original",
            b2["choices"][0]["message"]["content"]
            == b1["choices"][0]["message"]["content"],
        )

        # --- 3. Paraphrase: semantic HIT ------------------------------------
        r3 = chat(client, "please tell me the capital of France")
        b3 = r3.json()
        check(
            "paraphrase is semantic HIT",
            b3["cache_metadata"]["outcome"] == "HIT",
            str(b3.get("cache_metadata")),
        )
        score = b3["cache_metadata"].get("similarity_score") or 0.0
        check("paraphrase similarity >= 0.80", score >= 0.80, f"score={score}")

        # --- 4. Cross-model isolation ---------------------------------------
        r4 = chat(client, unique, model="gpt-4o-mini")
        b4 = r4.json()
        check(
            "same prompt, other model is MISS (key isolation)",
            b4["cache_metadata"]["outcome"] == "MISS",
            str(b4.get("cache_metadata")),
        )
        check(
            "cross-model response claims requested model", b4["model"] == "gpt-4o-mini"
        )

        # --- 5. Bypass header -------------------------------------------------
        r5 = chat(client, unique, headers={"X-Cache-Bypass": "true"})
        b5 = r5.json()
        check(
            "bypass header forces BYPASS",
            b5["cache_metadata"]["outcome"] == "BYPASS",
            str(b5.get("cache_metadata")),
        )

        # --- 6. Metrics accounting (5 requests so far: MISS, HIT, paraphrase
        #     HIT, cross-model MISS, BYPASS) -----------------------------------
        m = client.get(f"{BASE}/metrics", timeout=REQUEST_TIMEOUT).json()
        check(
            "metrics counted exactly our 5 requests",
            m["total_requests"] == t0_requests + 5,
            f"before={t0_requests} after={m['total_requests']}",
        )
        check("hit_rate > 0", m["hit_rate"] > 0, f"hit_rate={m['hit_rate']}")

        # --- 7. Logs recent -------------------------------------------------------
        logs = client.get(
            f"{BASE}/logs/recent?limit=50", timeout=REQUEST_TIMEOUT
        ).json()["logs"]
        ours = logs[:5]  # exactly the requests made since the metrics baseline
        outcomes = {entry["outcome"] for entry in ours}
        check(
            "logs contain HIT, MISS and BYPASS rows",
            {"HIT", "MISS", "BYPASS"} <= outcomes,
            f"got={outcomes}",
        )
        check(
            "all logged latencies are non-negative",
            all(entry["latency_ms"] >= 0 for entry in ours),
        )

        # --- 8. Purge effectiveness -------------------------------------------------
        purged = client.post(
            f"{BASE}/cache/purge", json={}, timeout=REQUEST_TIMEOUT
        ).json()
        check(
            "purge removed at least our entries",
            purged["purged_count"] >= 2,
            str(purged),
        )
        r_after = chat(client, unique)
        check(
            "purged prompt misses again",
            r_after.json()["cache_metadata"]["outcome"] == "MISS",
            str(r_after.json().get("cache_metadata")),
        )

    print(f"\nSmoke summary: {_passed} passed, {len(_failures)} failed")
    if _failures:
        print("Failed checks:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
