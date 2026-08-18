"""Wiring for the shared, process-wide singletons.

Kept behind `lru_cache`d factories so FastAPI's `dependency_overrides` can swap
any of them out in tests without the app importing test code.
"""

from __future__ import annotations

from functools import lru_cache

import anthropic

from app.auth.oauth import ZendeskOAuthClient
from app.auth.state import OAuthStateStore
from app.auth.token_store import EncryptedTokenStore
from app.config import Settings, get_settings
from app.llm.client import StructuredLLMClient
from app.slack.client import SlackNotifier
from app.triage.knowledge_base import KnowledgeBase
from app.triage.service import TriageService
from app.webhooks.processor import TicketProcessor
from app.zendesk.client import ZendeskClient


@lru_cache
def get_token_store() -> EncryptedTokenStore:
    settings = get_settings()
    return EncryptedTokenStore(settings.token_store_path, settings.token_encryption_key)


@lru_cache
def get_state_store() -> OAuthStateStore:
    return OAuthStateStore(ttl_seconds=get_settings().oauth_state_ttl_seconds)


def get_oauth_client() -> ZendeskOAuthClient:
    return ZendeskOAuthClient(get_settings(), get_token_store())


def get_app_settings() -> Settings:
    return get_settings()


@lru_cache
def get_knowledge_base() -> KnowledgeBase:
    return KnowledgeBase.from_file(get_settings().knowledge_base_path)


@lru_cache
def get_llm_client() -> StructuredLLMClient:
    settings = get_settings()
    # An empty key falls through to the SDK's own environment lookup, so
    # exporting ANTHROPIC_API_KEY still works for anyone who prefers that.
    client = (
        anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2)
        if settings.anthropic_api_key
        else None
    )
    return StructuredLLMClient(client)


def get_triage_service() -> TriageService:
    return TriageService(get_llm_client(), get_knowledge_base())


def get_zendesk_client() -> ZendeskClient:
    return ZendeskClient(get_oauth_client())


def get_slack_notifier() -> SlackNotifier:
    return SlackNotifier(get_settings().slack_webhook_url)


def get_ticket_processor() -> TicketProcessor:
    settings = get_settings()
    return TicketProcessor(
        get_zendesk_client(),
        get_triage_service(),
        get_slack_notifier(),
        ticket_url_template=f"{settings.zendesk_base_url}/agent/tickets/{{ticket_id}}",
    )
