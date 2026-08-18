"""The `state` parameter is the CSRF defence; these are its adversarial cases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.auth.state import OAuthStateStore

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def test_issued_state_is_accepted_once():
    store = OAuthStateStore(ttl_seconds=600)
    state = store.issue(now=NOW)
    assert store.consume(state, now=NOW) is True


def test_replaying_a_state_is_rejected():
    store = OAuthStateStore(ttl_seconds=600)
    state = store.issue(now=NOW)
    store.consume(state, now=NOW)
    assert store.consume(state, now=NOW) is False


def test_unknown_state_is_rejected():
    store = OAuthStateStore(ttl_seconds=600)
    store.issue(now=NOW)
    assert store.consume("attacker-supplied-value", now=NOW) is False


def test_empty_state_is_rejected():
    store = OAuthStateStore(ttl_seconds=600)
    assert store.consume("", now=NOW) is False


def test_expired_state_is_rejected():
    store = OAuthStateStore(ttl_seconds=600)
    state = store.issue(now=NOW)
    assert store.consume(state, now=NOW + timedelta(seconds=601)) is False


def test_state_just_inside_ttl_is_accepted():
    store = OAuthStateStore(ttl_seconds=600)
    state = store.issue(now=NOW)
    assert store.consume(state, now=NOW + timedelta(seconds=599)) is True


def test_issued_states_are_unique_and_high_entropy():
    store = OAuthStateStore()
    values = {store.issue() for _ in range(200)}
    assert len(values) == 200
    assert all(len(v) >= 32 for v in values)


def test_expired_states_are_purged_rather_than_accumulating():
    store = OAuthStateStore(ttl_seconds=60)
    for _ in range(5):
        store.issue(now=NOW)
    assert len(store) == 5
    store.issue(now=NOW + timedelta(seconds=61))
    assert len(store) == 1


def test_concurrent_logins_do_not_invalidate_each_other():
    store = OAuthStateStore(ttl_seconds=600)
    first = store.issue(now=NOW)
    second = store.issue(now=NOW)
    assert store.consume(second, now=NOW) is True
    assert store.consume(first, now=NOW) is True
