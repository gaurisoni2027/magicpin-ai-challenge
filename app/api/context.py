"""
POST /v1/context — receive a context push from the judge.
Implements version-aware idempotency.
"""
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any

from app.state.context_store import ContextStore

router = APIRouter()

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str = ""


def make_context_router(ctx: ContextStore) -> APIRouter:
    r = APIRouter()

    @r.post("/context")
    async def push_context(body: ContextBody):
        if body.scope not in VALID_SCOPES:
            return JSONResponse(
                status_code=400,
                content={
                    "accepted": False,
                    "reason": "invalid_scope",
                    "details": f"scope must be one of {sorted(VALID_SCOPES)}",
                },
            )

        accepted, current_version = ctx.upsert(
            body.scope, body.context_id, body.version, body.payload
        )

        if not accepted:
            return JSONResponse(
                status_code=409,
                content={
                    "accepted": False,
                    "reason": "stale_version",
                    "current_version": current_version,
                },
            )

        return {
            "accepted": True,
            "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }

    return r
