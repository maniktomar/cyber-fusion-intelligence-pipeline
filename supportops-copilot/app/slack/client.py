"""Slack notifier, deliberately unable to break ticket processing.

The decoupling is the requirement, so it is enforced in three ways rather than
promised in a comment:

* `notify` returns a bool and never raises. Every exception is caught, including
  the ones we did not anticipate.
* The timeout is short. Slack being slow must not hold a worker open behind a
  ticket that is already fully processed.
* There are no retries. A resolution notification is a nicety; re-queueing one
  behind a Slack outage would build a backlog of stale messages that arrive
  hours later claiming to be news.

An unconfigured webhook URL is a no-op, not an error -- Slack is optional.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


class SlackNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self._http_client = http_client

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    async def notify(self, payload: dict) -> bool:
        """Post a message. True if Slack accepted it; False for any failure."""
        if not self.configured:
            logger.debug("Slack webhook URL is not set; skipping notification.")
            return False

        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

        try:
            response = await client.post(self.webhook_url, json=payload)
        except Exception:
            # Bare `except` on purpose. This function's contract is that it
            # cannot break the caller, and a contract with exceptions is not one.
            logger.warning("Slack notification failed to send.", exc_info=True)
            return False
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 300:
            logger.warning(
                "Slack rejected the notification (%s): %s",
                response.status_code,
                response.text[:200],
            )
            return False
        return True
