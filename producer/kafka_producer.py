from __future__ import annotations

import json
import signal
import time
from typing import Any

from confluent_kafka import Producer
from tenacity import retry, stop_after_attempt, wait_exponential

from producer.event_generator import generate_security_event
from utils.config import load_config
from utils.logging_utils import get_logger


logger = get_logger("producer.kafka_producer", "logs/producer.log")
running = True


def _handle_shutdown(_: int, __: Any) -> None:
    global running
    running = False
    logger.info("Shutdown requested; flushing producer")


def delivery_report(error: Exception | None, message: Any) -> None:
    if error:
        logger.error("Kafka delivery failed", extra={"extra_fields": {"error": str(error)}})
    else:
        logger.info(
            "Kafka event delivered",
            extra={"extra_fields": {"topic": message.topic(), "partition": message.partition()}},
        )


@retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(5))
def produce_event(producer: Producer, topic: str, event: dict[str, Any]) -> None:
    producer.produce(
        topic=topic,
        # Keying by actor keeps one user's events in order on a single partition.
        key=event["actor_id"] or event["event_id"],
        value=json.dumps(event).encode("utf-8"),
        callback=delivery_report,
    )
    producer.poll(0)


def main() -> None:
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    config = load_config()
    kafka_config = config["kafka"]
    producer = Producer(
        {
            "bootstrap.servers": kafka_config["bootstrap_servers"],
            "client.id": "security-event-producer",
            "enable.idempotence": True,
            "acks": "all",
            "retries": 5,
        }
    )

    topic = kafka_config["security_events_topic"]
    events_per_second = int(kafka_config["producer_events_per_second"] or 5)
    sleep_seconds = 1 / max(events_per_second, 1)
    logger.info("Starting producer", extra={"extra_fields": {"topic": topic}})

    while running:
        event = generate_security_event()
        produce_event(producer, topic, event)
        time.sleep(sleep_seconds)

    producer.flush(timeout=30)
    logger.info("Producer stopped")


if __name__ == "__main__":
    main()

