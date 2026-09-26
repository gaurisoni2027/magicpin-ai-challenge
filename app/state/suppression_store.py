"""
Suppression store — prevents duplicate sends within the same suppression key.
Also tracks ended conversations so tick doesn't re-initiate them.
"""
from typing import Optional


class SuppressionStore:
    def __init__(self):
        # suppression_key -> conversation_id that consumed it
        self._keys: dict[str, str] = {}
        # merchant_id -> set of suppressed suppression_keys
        self._merchant_keys: dict[str, set] = {}
        # conversation_ids that ended due to explicit decline/hostility
        self._ended: set[str] = set()
        # merchant_ids globally suppressed (hostile/stop)
        self._suppressed_merchants: dict[str, int] = {}  # merchant_id -> seconds remaining

    def is_suppressed(self, suppression_key: str) -> bool:
        return suppression_key in self._keys

    def suppress(self, suppression_key: str, conversation_id: str, merchant_id: Optional[str] = None):
        self._keys[suppression_key] = conversation_id
        if merchant_id:
            self._merchant_keys.setdefault(merchant_id, set()).add(suppression_key)

    def mark_ended(self, conversation_id: str):
        self._ended.add(conversation_id)

    def is_ended(self, conversation_id: str) -> bool:
        return conversation_id in self._ended

    def suppress_merchant(self, merchant_id: str, seconds: int = 86400 * 30):
        """Globally suppress a merchant (hostile opt-out)."""
        self._suppressed_merchants[merchant_id] = seconds

    def is_merchant_suppressed(self, merchant_id: str) -> bool:
        return merchant_id in self._suppressed_merchants
