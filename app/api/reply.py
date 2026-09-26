"""
POST /v1/reply — receive a merchant/customer reply and return next action.
"""
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.engine.composer import compose_reply
from app.state.context_store import ContextStore
from app.state.conversation_store import ConversationStore
from app.state.suppression_store import SuppressionStore

logger = logging.getLogger(__name__)


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: str = ""
    turn_number: int = 2


def make_reply_router(
    ctx: ContextStore,
    conversations: ConversationStore,
    suppression: SuppressionStore,
) -> APIRouter:
    r = APIRouter()

    @r.post("/reply")
    async def reply(body: ReplyBody):
        merchant_id = body.merchant_id or ""
        try:
            response = compose_reply(
                conversation_id=body.conversation_id,
                merchant_id=merchant_id,
                customer_id=body.customer_id,
                merchant_message=body.message,
                turn_number=body.turn_number,
                conversation_store=conversations,
                suppression_store=suppression,
                context_store=ctx,
            )
            return response
        except Exception as e:
            logger.error("Reply error for conv %s: %s", body.conversation_id, e, exc_info=True)
            # Safe default
            return {
                "action": "send",
                "body": "Got it — let me follow up with the details shortly.",
                "cta": "open_ended",
                "rationale": "Error recovery: safe continuation.",
            }

    return r
