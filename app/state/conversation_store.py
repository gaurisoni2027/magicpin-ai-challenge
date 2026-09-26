"""
In-memory conversation state.
Tracks turns, intent, auto-reply detection, and suppression per conversation.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Turn:
    from_role: str          # "vera" | "merchant" | "customer"
    body: str
    turn_number: int


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str]
    trigger_id: Optional[str]
    suppression_key: Optional[str]

    turns: list[Turn] = field(default_factory=list)
    intent: str = "qualifying"          # qualifying | committed | declined | ended | waiting
    auto_reply_count: int = 0
    is_ended: bool = False
    is_suppressed: bool = False


class ConversationStore:
    def __init__(self):
        self._convs: dict[str, ConversationState] = {}
        self._merchant_auto_replies: dict[str, int] = {}

    def get(self, conversation_id: str) -> Optional[ConversationState]:
        return self._convs.get(conversation_id)

    def create(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str],
        trigger_id: Optional[str],
        suppression_key: Optional[str],
    ) -> ConversationState:
        state = ConversationState(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trigger_id,
            suppression_key=suppression_key,
        )
        self._convs[conversation_id] = state
        return state

    def get_or_create(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        suppression_key: Optional[str] = None,
    ) -> ConversationState:
        if conversation_id not in self._convs:
            return self.create(
                conversation_id, merchant_id, customer_id, trigger_id, suppression_key
            )
        return self._convs[conversation_id]

    def add_turn(
        self, conversation_id: str, from_role: str, body: str, turn_number: int
    ):
        state = self._convs.get(conversation_id)
        if state:
            state.turns.append(Turn(from_role=from_role, body=body, turn_number=turn_number))

    def mark_ended(self, conversation_id: str):
        state = self._convs.get(conversation_id)
        if state:
            state.is_ended = True
            state.intent = "ended"

    def mark_suppressed(self, conversation_id: str):
        state = self._convs.get(conversation_id)
        if state:
            state.is_suppressed = True

    def set_intent(self, conversation_id: str, intent: str):
        state = self._convs.get(conversation_id)
        if state:
            state.intent = intent

    def increment_auto_reply(self, conversation_id: str, merchant_id: Optional[str] = None) -> int:
        state = self._convs.get(conversation_id)
        conv_count = (state.auto_reply_count + 1) if state else 1
        if state:
            state.auto_reply_count = conv_count
        merch_count = 0
        if merchant_id:
            self._merchant_auto_replies[merchant_id] = self._merchant_auto_replies.get(merchant_id, 0) + 1
            merch_count = self._merchant_auto_replies[merchant_id]
        return max(conv_count, merch_count)

    def get_sent_bodies(self, conversation_id: str) -> list[str]:
        state = self._convs.get(conversation_id)
        if not state:
            return []
        return [t.body for t in state.turns if t.from_role == "vera"]
