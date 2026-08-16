from __future__ import annotations

from confluent_kafka import Consumer, KafkaException

from utils.config import load_config
from utils.logging_utils import get_logger


logger = get_logger("consumer.kafka_debug_consumer", "logs/consumer.log")


def main() -> None:
    config = load_config()["kafka"]
    consumer = Consumer(
        {
            "bootstrap.servers": config["bootstrap_servers"],
            "group.id": "debug-consumer",
            "auto.offset.reset": "latest",
        }
    )
    consumer.subscribe([config["security_events_topic"]])
    logger.info("Listening for Kafka events", extra={"extra_fields": {"topic": config["security_events_topic"]}})

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                raise KafkaException(msg.error())
            print(msg.value().decode("utf-8"))
    finally:
        consumer.close()


if __name__ == "__main__":
    main()

