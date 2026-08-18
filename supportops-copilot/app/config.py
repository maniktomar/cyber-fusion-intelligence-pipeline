"""Application settings, loaded from the environment / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    zendesk_subdomain: str = "example"
    zendesk_client_id: str = "supportops_copilot"
    zendesk_client_secret: str = ""
    zendesk_redirect_uri: str = "http://localhost:8000/auth/zendesk/callback"
    zendesk_scopes: str = "read write"

    # Blank means "ask Zendesk for a non-expiring token" (its historical default).
    zendesk_token_expires_in: int | None = None

    # Shared secret Zendesk signs webhook payloads with (Admin Center ->
    # Apps and integrations -> Webhooks -> your webhook -> signing secret).
    zendesk_webhook_secret: str = ""
    webhook_tolerance_seconds: int = Field(default=300, gt=0)

    token_encryption_key: str = ""
    token_store_path: str = "./data/tokens.enc"
    knowledge_base_path: str = "./data/knowledge_base.json"

    # Slack Incoming Webhook. Blank disables notifications entirely -- Slack is
    # optional and its absence must never be an error.
    slack_webhook_url: str = ""

    # Shared secret for the Zendesk App (Admin Center -> Apps -> your app).
    # Blank rejects every sidebar request rather than serving them unauthenticated.
    # Read through Settings rather than left to the SDK's own os.environ
    # lookup: pydantic-settings loads .env into this object and never touches
    # os.environ, so a key placed in .env would otherwise be invisible to the
    # Anthropic client and fail with a confusing "no API key" at first triage.
    anthropic_api_key: str = ""

    zendesk_app_secret: str = ""
    zendesk_app_issuer: str = ""

    # How long an unconsumed OAuth `state` value stays valid.
    oauth_state_ttl_seconds: int = Field(default=600, gt=0)

    # Refresh this many seconds before the access token actually expires.
    token_refresh_leeway_seconds: int = Field(default=300, ge=0)

    @field_validator("zendesk_token_expires_in", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def zendesk_base_url(self) -> str:
        return f"https://{self.zendesk_subdomain}.zendesk.com"

    @property
    def authorize_url(self) -> str:
        return f"{self.zendesk_base_url}/oauth/authorizations/new"

    @property
    def token_url(self) -> str:
        return f"{self.zendesk_base_url}/api/v2/oauth/tokens"


@lru_cache
def get_settings() -> Settings:
    return Settings()
