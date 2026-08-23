"""Security helpers — user identity derivation for BYOK mode.

Phase 7: every caller authenticates with their own provider API key
(`Authorization: Bearer <key>`). That raw key must never be stored, so it is
reduced to a stable pseudo-id via HMAC-SHA256 keyed by a server-side secret
(`USER_ID_PEPPER`). Same key -> same user_id across requests/restarts; the
reverse mapping is infeasible without the pepper, and provider keys share
known prefixes, so the keyed-HMAC step matters (a bare SHA-256 would be far
more brute-forceable).

The pepper behaves like ADMIN_TOKEN: generate once (e.g. `python -c "import
secrets; print(secrets.token_hex(32))"`), keep it out of git, never rotate —
rotating would silently orphan every existing user's scoped cache history.
"""

from __future__ import annotations

import hashlib
import hmac

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
    return hmac.new(
        pepper.encode("utf-8"), api_key.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:24]
