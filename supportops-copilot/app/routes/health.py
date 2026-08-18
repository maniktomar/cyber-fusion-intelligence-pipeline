from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness probe. Deliberately does not touch Zendesk or the LLM."""
    return {"status": "ok"}
