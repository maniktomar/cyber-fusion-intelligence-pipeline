from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.auth.oauth import ZendeskOAuthClient
from app.auth.state import OAuthStateStore
from app.auth.token_store import EncryptedTokenStore
from app.config import Settings


@pytest.fixture
def encryption_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def settings(tmp_path, encryption_key) -> Settings:
    """Settings built explicitly so tests never read a developer's real .env."""
    return Settings(
        _env_file=None,
        zendesk_subdomain="acme-sandbox",
        zendesk_client_id="supportops_copilot",
        zendesk_client_secret="s3cret",
        zendesk_redirect_uri="http://localhost:8000/auth/zendesk/callback",
        zendesk_scopes="read write",
        token_encryption_key=encryption_key,
        token_store_path=str(tmp_path / "tokens.enc"),
        oauth_state_ttl_seconds=600,
        token_refresh_leeway_seconds=300,
    )


@pytest.fixture
def token_store(settings) -> EncryptedTokenStore:
    return EncryptedTokenStore(settings.token_store_path, settings.token_encryption_key)


@pytest.fixture
def state_store(settings) -> OAuthStateStore:
    return OAuthStateStore(ttl_seconds=settings.oauth_state_ttl_seconds)


@pytest.fixture
def oauth_client(settings, token_store) -> ZendeskOAuthClient:
    return ZendeskOAuthClient(settings, token_store)
