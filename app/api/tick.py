"""
POST /v1/tick — judge wakes bot; bot inspects triggers and decides what to send.
"""
import logging
from fastapi import APIRouter
from pydantic import BaseModel

from app.engine.composer import compose
from app.state.context_store import ContextStore
from app.state.conversation_store import ConversationStore
from app.state.suppression_store import SuppressionStore

logger = logging.getLogger(__name__)


class TickBody(BaseModel):
    now: str = ""
    available_triggers: list[str] = []


def make_tick_router(
    ctx: ContextStore,
    conversations: ConversationStore,
    suppression: SuppressionStore,
) -> APIRouter:
    r = APIRouter()

    @r.post("/tick")
    async def tick(body: TickBody):
        actions = []

        for trigger_id in body.available_triggers:
            # Cap at 20 actions per tick (judge limit)
            if len(actions) >= 20:
                break

            trigger = ctx.get("trigger", trigger_id)
            if not trigger:
                logger.debug("Trigger %s not found in context store", trigger_id)
                continue

            merchant_id = trigger.get("merchant_id", "")
            customer_id = trigger.get("customer_id")

            merchant = ctx.get("merchant", merchant_id) if merchant_id else None
            payload = trigger.get("payload", {})
            category_slug = (
                (merchant.get("category_slug", "") if merchant else "")
                or payload.get("category", "")
                or payload.get("category_slug", "")
            )
            category = ctx.get("category", category_slug) if category_slug else None
            customer = ctx.get("customer", customer_id) if customer_id else None

            try:
                action = compose(
                    trigger_id=trigger_id,
                    trigger=trigger,
                    merchant=merchant or {},
                    category=category or {},
                    customer=customer,
                    suppression_store=suppression,
                    conversation_store=conversations,
                    now=body.now,
                )
                if action:
                    actions.append(action)
            except Exception as e:
                logger.error("Compose error for trigger %s: %s", trigger_id, e, exc_info=True)

        return {"actions": actions}

    return r
