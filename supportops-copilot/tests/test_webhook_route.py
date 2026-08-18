"""The webhook endpoint, driven through the real FastAPI app."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_app_settings, get_ticket_processor
from app.main import create_app
from app.webhooks.events import EventType
from app.webhooks.signature import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    compute_signature,
)

SECRET = "whsec_route_secret"


def body_for(event_type: str = "zen:event-type:ticket.created", ticket_id: int = 4242):
    return json.dumps({"type": event_type, "detail": {"id": ticket_id}}).encode()


class SpyProcessor:
    def __init__(self):
        self.processed = []
        self.notified = []

    async def process(self, event):
        self.processed.append(event)

    async def notify_resolution(self, event):
        self.notified.append(event)


@pytest.fixture
def processor() -> SpyProcessor:
    return SpyProcessor()


@pytest.fixture
def client(settings, processor) -> TestClient:
    settings.zendesk_webhook_secret = SECRET
    app = create_app()
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_ticket_processor] = lambda: processor
    with TestClient(app) as c:
        yield c


def post(client, body: bytes, *, timestamp: str = "", secret: str = SECRET, sign=True):
    from datetime import UTC, datetime

    timestamp = timestamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"content-type": "application/json"}
    if sign:
        headers[SIGNATURE_HEADER] = compute_signature(secret, timestamp, body)
        headers[TIMESTAMP_HEADER] = timestamp
    return client.post("/webhooks/zendesk", content=body, headers=headers)


class TestAcceptedDeliveries:
    def test_a_signed_ticket_created_event_is_queued(self, client, processor):
        response = post(client, body_for())

        assert response.status_code == 202
        assert response.json()["queued"] is True
        assert len(processor.processed) == 1
        assert processor.processed[0].ticket_id == 4242

    def test_a_signed_ticket_updated_event_is_queued(self, client, processor):
        post(client, body_for("zen:event-type:ticket.updated"))
        assert processor.processed[0].type is EventType.TICKET_UPDATED

    def test_a_solved_event_notifies_rather_than_triaging(self, client, processor):
        response = post(client, body_for("zen:event-type:ticket.solved"))

        assert response.status_code == 202
        assert processor.processed == []
        assert len(processor.notified) == 1
        assert processor.notified[0].ticket_id == 4242

    def test_the_resolution_path_is_separate_from_triage(self, client, processor):
        """A solved event must never enter the triage path, and vice versa: the
        two are queued independently so a Slack outage cannot touch triage."""
        post(client, body_for("zen:event-type:ticket.created"))
        post(client, body_for("zen:event-type:ticket.solved"))

        assert len(processor.processed) == 1
        assert len(processor.notified) == 1

    def test_an_unknown_event_type_is_acknowledged_but_does_nothing(
        self, client, processor
    ):
        response = post(client, body_for("zen:event-type:user.created"))

        assert response.status_code == 202
        assert response.json()["queued"] is False
        assert processor.processed == []
        assert processor.notified == []


class TestRejectedDeliveries:
    def test_an_unsigned_request_is_rejected(self, client, processor):
        response = post(client, body_for(), sign=False)

        assert response.status_code == 401
        assert processor.processed == []

    def test_a_forged_signature_is_rejected(self, client, processor):
        response = post(client, body_for(), secret="whsec_attacker_guess")

        assert response.status_code == 401
        assert processor.processed == []

    def test_a_tampered_body_is_rejected(self, client, processor):
        """Sign one payload, send another -- the classic swap."""
        from datetime import UTC, datetime

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = client.post(
            "/webhooks/zendesk",
            content=body_for(ticket_id=9999),
            headers={
                SIGNATURE_HEADER: compute_signature(SECRET, timestamp, body_for()),
                TIMESTAMP_HEADER: timestamp,
                "content-type": "application/json",
            },
        )

        assert response.status_code == 401
        assert processor.processed == []

    def test_a_stale_delivery_is_rejected(self, client, processor):
        response = post(client, body_for(), timestamp="2020-01-01T00:00:00Z")

        assert response.status_code == 401
        assert processor.processed == []

    def test_rejections_do_not_say_why(self, client):
        """Telling an attacker which check failed is free information."""
        unsigned = post(client, body_for(), sign=False)
        forged = post(client, body_for(), secret="whsec_wrong")
        stale = post(client, body_for(), timestamp="2020-01-01T00:00:00Z")

        details = {r.json()["detail"] for r in (unsigned, forged, stale)}
        assert len(details) == 1

    def test_an_unset_secret_rejects_every_delivery(self, settings, processor):
        settings.zendesk_webhook_secret = ""
        app = create_app()
        app.dependency_overrides[get_app_settings] = lambda: settings
        app.dependency_overrides[get_ticket_processor] = lambda: processor
        with TestClient(app) as c:
            assert post(c, body_for(), secret="").status_code == 401
        assert processor.processed == []


class TestMalformedPayloads:
    def test_signed_but_not_json_is_a_400(self, client, processor):
        response = post(client, b"not json at all")

        assert response.status_code == 400
        assert processor.processed == []

    def test_signed_json_with_no_ticket_id_is_a_400(self, client, processor):
        response = post(client, json.dumps({"type": "ticket.created"}).encode())

        assert response.status_code == 400
        assert "no ticket id" in response.json()["detail"]
        assert processor.processed == []

    def test_a_non_integer_ticket_id_is_a_400(self, client, processor):
        body = json.dumps({"type": "ticket.created", "detail": {"id": "abc"}}).encode()
        assert post(client, body).status_code == 400
        assert processor.processed == []

    def test_a_negative_ticket_id_is_a_400(self, client, processor):
        body = json.dumps({"type": "ticket.created", "detail": {"id": -5}}).encode()
        assert post(client, body).status_code == 400

    def test_a_json_array_body_is_a_400(self, client, processor):
        assert post(client, b"[1, 2, 3]").status_code == 400
        assert processor.processed == []

    def test_malformed_payloads_are_rejected_before_any_work(self, client, processor):
        """A valid signature proves origin, not shape."""
        for body in (b"{}", b"[]", b'{"detail": {}}', b'{"detail": "nope"}'):
            assert post(client, body).status_code == 400
        assert processor.processed == []
