"""
GET /v1/healthz — liveness probe.
"""
import time
from fastapi import APIRouter
from app.state.context_store import ContextStore

START = time.time()


def make_health_router(ctx: ContextStore) -> APIRouter:
    r = APIRouter()

    @r.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "uptime_seconds": int(time.time() - START),
            "contexts_loaded": ctx.counts(),
        }

    return r
