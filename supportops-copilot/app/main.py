"""SupportOps Copilot — FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.dependencies import get_knowledge_base
from app.routes import auth, health, sidebar, webhooks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


class StartupCheckFailed(RuntimeError):
    """A dependency the service cannot run without is missing or unusable."""


def run_startup_checks() -> None:
    """Fail loudly at boot rather than quietly on the first ticket.

    This exists because of a real bug: the knowledge base was missing from the
    Docker image, and the only symptom was a 500 on the first webhook. FastAPI
    resolves a route's dependencies before running its body, so the missing file
    took down the *webhook signature check* too -- an unauthenticated request
    got a stack trace instead of a 401.

    Loading it here turns that into a container that refuses to start, which is
    both easier to diagnose and impossible to miss.
    """
    settings = get_settings()
    try:
        kb = get_knowledge_base()
    except FileNotFoundError as exc:
        raise StartupCheckFailed(
            f"Knowledge base not found at {settings.knowledge_base_path!r}. "
            "The service cannot triage without it."
        ) from exc
    except ValueError as exc:
        raise StartupCheckFailed(f"Knowledge base is unusable: {exc}") from exc

    logger.info("Knowledge base loaded: %d articles (synthetic=%s)", len(kb), kb.synthetic)

    # Warn rather than refuse: these are optional or only needed once a real
    # integration is wired up, and a developer should be able to boot without them.
    for name, value in (
        ("ZENDESK_WEBHOOK_SECRET", settings.zendesk_webhook_secret),
        ("ZENDESK_APP_SECRET", settings.zendesk_app_secret),
        ("TOKEN_ENCRYPTION_KEY", settings.token_encryption_key),
    ):
        if not value:
            logger.warning(
                "%s is not set; the features that depend on it will reject "
                "every request rather than run unauthenticated.",
                name,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_startup_checks()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="SupportOps Copilot",
        description=(
            "AI-assisted Zendesk ticket triage. Fails safe and visibly: when the "
            "model is unsure or unavailable, tickets are flagged for manual "
            "triage and otherwise left untouched."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(webhooks.router)
    app.include_router(sidebar.router)
    return app


app = create_app()
