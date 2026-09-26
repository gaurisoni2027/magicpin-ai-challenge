"""
Vera Bot — FastAPI application entry point.
Run: uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
import logging
from fastapi import FastAPI

from app.state.context_store import ContextStore
from app.state.conversation_store import ConversationStore
from app.state.suppression_store import SuppressionStore
from app.api.health import make_health_router
from app.api.metadata import make_metadata_router
from app.api.context import make_context_router
from app.api.tick import make_tick_router
from app.api.reply import make_reply_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

# ── Shared state (in-memory, single-process) ──────────────────────────────────
ctx = ContextStore()
conversations = ConversationStore()
suppression = SuppressionStore()

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Vera — magicpin AI Challenge Bot",
    version="1.0.0",
    description="Evidence-first, decision-first merchant engagement engine.",
)

PREFIX = "/v1"
app.include_router(make_health_router(ctx), prefix=PREFIX)
app.include_router(make_metadata_router(), prefix=PREFIX)
app.include_router(make_context_router(ctx), prefix=PREFIX)
app.include_router(make_tick_router(ctx, conversations, suppression), prefix=PREFIX)
app.include_router(make_reply_router(ctx, conversations, suppression), prefix=PREFIX)


@app.get("/")
async def root():
    return {"service": "Vera Bot", "version": "1.0.0", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    from app.config import HOST, PORT
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)

