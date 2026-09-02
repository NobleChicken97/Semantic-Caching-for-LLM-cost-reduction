"""Security helpers — user identity derivation for BYOK mode.

Phase 7: every caller authenticates with their own provider API key
(`Authorization: Bearer <key>`). That raw key must never be stored, so it is
reduced to a stable pseudo-id via a keyed BLAKE2b MAC using a server-side
secret (`USER_ID_PEPPER`). Same key -> same user_id across requests/restarts;
the reverse mapping is infeasible without the pepper, and provider keys share
known prefixes, so the keyed-MAC step matters (a bare hash would be far more
brute-forceable). Keyed BLAKE2b replaces the original HMAC-SHA256
construction (2026-09-02, service <1 day old — one-time user_id rotation with
no real users affected); it is a native keyed MAC, not a bare hash applied to
secret material, and passes CodeQL's sensitive-data crypto checks.

The pepper behaves like ADMIN_TOKEN: generate once (e.g. `python -c "import
secrets; print(secrets.token_hex(32))"`), keep it out of git, never rotate —
rotating (or changing the derivation, as above) would silently orphan every
existing user's scoped cache history.
"""

from __future__ import annotations

import hashlib

from .config import get_settings

# Cache scope for traffic without any caller key — i.e. MOCK_LLM=true local /
# CI usage. Pre-BYOK databases are migrated under this same id so historical
# entries remain reachable in single-user deployments.
LOCAL_USER_ID = "local"


def derive_user_id(api_key: str) -> str:
    """Map a caller API key to a stable, non-reversible user_id."""
    if not api_key:
        raise ValueError("cannot derive user_id from an empty API key")
    pepper = get_settings().user_id_pepper
    return hashlib.blake2b(
        api_key.encode("utf-8"),
        key=pepper.encode("utf-8"),
        digest_size=12,
    ).hexdigest()
