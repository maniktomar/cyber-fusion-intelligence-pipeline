from producer.event_generator import (
    ACCEPTED_FIELDS,
    generate_late_event,
    generate_security_event,
)


def test_generate_security_event_has_required_fields():
    event = generate_security_event(anomaly_rate=0, invalid_rate=0)
    required = {
        "event_id",
        "event_time",
        "event_type",
        "actor_id",
        "source_ip",
        "target_system",
        "outcome",
        "bytes_out",
    }
    assert required.issubset(event)
    assert event["event_type"] in ACCEPTED_FIELDS["event_type"]
    assert event["outcome"] in ACCEPTED_FIELDS["outcome"]
    assert event["bytes_out"] >= 0
    assert event["actor_id"]


def test_threat_patterns_are_injected_when_rate_is_one():
    events = [generate_security_event(anomaly_rate=1.0, invalid_rate=0) for _ in range(50)]
    # Every event carries a planted pattern, so at least one detection signal must be present.
    assert any(e["failed_attempts_1h"] >= 10 for e in events) or any(e["bytes_out"] >= 500_000_000 for e in events)
    assert all(e["event_type"] in ACCEPTED_FIELDS["event_type"] for e in events)


def test_quality_defects_are_injected_when_rate_is_one():
    events = [generate_security_event(anomaly_rate=0, invalid_rate=1.0) for _ in range(50)]
    defective = [
        e
        for e in events
        if e["event_type"] not in ACCEPTED_FIELDS["event_type"]
        or not e["actor_id"]
        or e["bytes_out"] < 0
        or e["target_system"] is None
        or e["http_status"] not in range(100, 600)
    ]
    assert len(defective) == len(events)


def test_late_event_is_stamped_in_the_past():
    late = generate_late_event(max_delay_minutes=20, anomaly_rate=0, invalid_rate=0)
    current = generate_security_event(anomaly_rate=0, invalid_rate=0)
    assert late["event_time"] < current["event_time"]
