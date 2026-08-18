"""Startup checks.

Regression cover for a bug a container smoke test found: the knowledge base was
missing from the Docker image, and the only symptom was a 500 on the first
webhook -- including on *unauthenticated* ones, because FastAPI resolves a
route's dependencies before running its body, so the missing file took down the
signature check too.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.dependencies import get_knowledge_base
from app.main import StartupCheckFailed, run_startup_checks


@pytest.fixture(autouse=True)
def _clear_caches():
    """These read `lru_cache`d singletons; each test needs a clean slate."""
    get_settings.cache_clear()
    get_knowledge_base.cache_clear()
    yield
    get_settings.cache_clear()
    get_knowledge_base.cache_clear()


class TestKnowledgeBaseCheck:
    def test_passes_with_the_real_corpus(self):
        run_startup_checks()

    def test_a_missing_knowledge_base_stops_startup(self, monkeypatch):
        """The container must refuse to start, not serve broken triage."""
        monkeypatch.setenv("KNOWLEDGE_BASE_PATH", "./data/does-not-exist.json")

        with pytest.raises(StartupCheckFailed, match="not found"):
            run_startup_checks()

    def test_the_error_names_the_path_it_looked_for(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_BASE_PATH", "./nope/kb.json")

        with pytest.raises(StartupCheckFailed) as exc:
            run_startup_checks()
        assert "nope/kb.json" in str(exc.value)

    def test_an_empty_knowledge_base_stops_startup(self, tmp_path, monkeypatch):
        empty = tmp_path / "kb.json"
        empty.write_text('{"synthetic": true, "articles": []}', encoding="utf-8")
        monkeypatch.setenv("KNOWLEDGE_BASE_PATH", str(empty))

        with pytest.raises(StartupCheckFailed, match="unusable"):
            run_startup_checks()


class TestOptionalSecretWarnings:
    def test_missing_secrets_warn_but_do_not_stop_startup(self, monkeypatch, caplog):
        """A developer must be able to boot without a Zendesk account."""
        for name in (
            "ZENDESK_WEBHOOK_SECRET",
            "ZENDESK_APP_SECRET",
            "TOKEN_ENCRYPTION_KEY",
        ):
            monkeypatch.setenv(name, "")

        with caplog.at_level("WARNING"):
            run_startup_checks()

        assert "ZENDESK_WEBHOOK_SECRET" in caplog.text
        assert "ZENDESK_APP_SECRET" in caplog.text

    def test_the_warning_says_the_feature_fails_closed(self, monkeypatch, caplog):
        monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "")
        with caplog.at_level("WARNING"):
            run_startup_checks()
        assert "reject" in caplog.text

    def test_no_warnings_when_everything_is_configured(self, monkeypatch, caplog):
        monkeypatch.setenv("ZENDESK_WEBHOOK_SECRET", "whsec_x")
        monkeypatch.setenv("ZENDESK_APP_SECRET", "app_secret_x")
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "key_x")

        with caplog.at_level("WARNING"):
            run_startup_checks()
        assert "is not set" not in caplog.text


class TestLoggingOnSuccess:
    def test_logs_the_article_count_and_synthetic_flag(self, caplog):
        """Makes it obvious in container logs that the corpus is the fake one."""
        with caplog.at_level("INFO"):
            run_startup_checks()

        assert "Knowledge base loaded" in caplog.text
        assert "synthetic=True" in caplog.text
