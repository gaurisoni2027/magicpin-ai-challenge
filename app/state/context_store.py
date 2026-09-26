"""
In-memory context store.
Keyed by (scope, context_id).
Implements version-aware idempotency per the judge contract.
"""
from datetime import datetime, timezone
from typing import Any, Optional


class ContextStore:
    """
    Thread-safe (single-process) in-memory store.
    Rules:
    - Same (scope, context_id, version) → idempotent / stale → return current_version
    - Higher version → replace atomically
    - Lower version never overwrites newer → return current_version (stale)
    """

    def __init__(self):
        # (scope, context_id) -> {"version": int, "payload": dict, "updated_at": str}
        self._store: dict[tuple[str, str], dict] = {}

    # ── Public interface ────────────────────────────────────────────────────

    def upsert(
        self, scope: str, context_id: str, version: int, payload: dict
    ) -> tuple[bool, int]:
        """
        Returns (accepted, current_version).
        accepted=True  → stored (new or higher version, or idempotent re-post)
        accepted=False → stale (current_version > incoming version)
        """
        key = (scope, context_id)
        cur = self._store.get(key)
        if cur is not None:
            if version < cur["version"]:
                return False, cur["version"]
            elif version == cur["version"]:
                # Idempotent re-post per challenge brief: no-op, accepted
                return True, version

        self._store[key] = {
            "version": version,
            "payload": payload,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return True, version

    def get(self, scope: str, context_id: str) -> Optional[dict]:
        """Returns the payload dict or None."""
        entry = self._store.get((scope, context_id))
        return entry["payload"] if entry else None

    def get_entry(self, scope: str, context_id: str) -> Optional[dict]:
        """Returns the full entry (version + payload + updated_at) or None."""
        return self._store.get((scope, context_id))

    def counts(self) -> dict[str, int]:
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        for (scope, _) in self._store:
            if scope in counts:
                counts[scope] += 1
        return counts

    def all_of_scope(self, scope: str) -> dict[str, dict]:
        """Return {context_id: payload} for a given scope."""
        return {
            cid: entry["payload"]
            for (s, cid), entry in self._store.items()
            if s == scope
        }

    def all_triggers(self) -> dict[str, dict]:
        return self.all_of_scope("trigger")
