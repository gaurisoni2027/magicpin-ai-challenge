"""
GET /v1/metadata — bot identity and approach.
"""
from fastapi import APIRouter
from app.config import TEAM_NAME, TEAM_MEMBERS, CONTACT_EMAIL, VERSION, SUBMITTED_AT, LLM_PROVIDER, LLM_MODEL, PROVIDER_DEFAULTS


def make_metadata_router() -> APIRouter:
    r = APIRouter()

    @r.get("/metadata")
    async def metadata():
        model = LLM_MODEL or PROVIDER_DEFAULTS.get(LLM_PROVIDER, "unknown")
        return {
            "team_name": TEAM_NAME,
            "team_members": TEAM_MEMBERS,
            "model": f"{LLM_PROVIDER}/{model}",
            "approach": (
                "Hybrid: deterministic trigger router + evidence builder → LLM composer → "
                "deterministic validator → safe fallback. Evidence-first: LLM only sees "
                "facts extracted from context, preventing hallucination."
            ),
            "contact_email": CONTACT_EMAIL,
            "version": VERSION,
            "submitted_at": SUBMITTED_AT,
        }

    return r
