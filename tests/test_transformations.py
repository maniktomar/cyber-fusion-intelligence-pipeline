"""Batch-mode tests for the streaming transformations.

The same functions run in the streaming job; exercising them on a static DataFrame
keeps the detection rules and quality gates under test without a Kafka broker.
"""

from __future__ import annotations

import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402

from pyspark_jobs.transformations import add_threat_flags, clean_security_events  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.appName("cyber-fusion-tests")
        .master("local[1]")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    yield session
    session.stop()


def _event(**overrides):
    event = {
        "event_id": "evt-1",
        "event_time": "2026-08-06T09:15:00+00:00",
        "event_type": "authentication",
        "action": "login",
        "actor_id": "USR-12345",
        "actor_email": " Analyst@Example.COM ",
        "actor_role": "analyst",
        "actor_department": "security",
        "is_privileged_actor": False,
        "session_id": "SES-1",
        "source_ip": "203.0.113.10",
        "source_country": "ie",
        "device_type": "managed_laptop",
        "user_agent": "agent",
        "target_system": " Identity-Provider ",
        "target_resource": "sso/session",
        "auth_method": "password",
        "mfa_used": True,
        "outcome": "success",
        "http_status": 200,
        "bytes_out": 1024,
        "failed_attempts_1h": 0,
        "request_rate_1m": 5,
        "detection_source": "siem",
        "is_test_event": False,
        "source_system": "simulated-siem",
    }
    event.update(overrides)
    return event


def _process(spark, events):
    df = spark.createDataFrame(events)
    return add_threat_flags(clean_security_events(df)).collect()


def test_clean_normalises_and_marks_valid(spark):
    row = _process(spark, [_event()])[0]
    assert row["quality_status"] == "valid"
    assert row["actor_email"] == "analyst@example.com"
    assert row["target_system"] == "identity-provider"
    assert row["source_country"] == "IE"
    assert row["event_hour"] == 9
    assert row["is_anomaly"] is False
    assert row["severity"] == "NONE"


@pytest.mark.parametrize(
    "overrides,expected_reason",
    [
        ({"event_type": "syslog_blob"}, "unknown_event_type"),
        ({"actor_id": ""}, "missing_actor"),
        ({"bytes_out": -5}, "bytes_out_out_of_range"),
        ({"http_status": 999}, "invalid_http_status"),
        ({"outcome": "weird"}, "unknown_outcome"),
    ],
)
def test_quality_defects_are_quarantined_with_a_reason(spark, overrides, expected_reason):
    row = _process(spark, [_event(**overrides)])[0]
    assert row["quality_status"] == "invalid"
    assert expected_reason in row["quality_failure_reason"]


@pytest.mark.parametrize(
    "overrides,expected_reason,expected_severity",
    [
        (
            {"outcome": "failure", "failed_attempts_1h": 25},
            "credential_brute_force",
            "HIGH",
        ),
        (
            {"event_type": "privilege_change", "action": "permission_elevated", "is_privileged_actor": False},
            "unauthorized_privilege_escalation",
            "CRITICAL",
        ),
        (
            {"event_type": "data_access", "action": "export", "bytes_out": 900_000_000},
            "large_data_egress",
            "CRITICAL",
        ),
        (
            {"event_type": "api_call", "action": "GET /v1/reports", "request_rate_1m": 800},
            "api_abuse_burst",
            "MEDIUM",
        ),
        ({"source_country": "RU"}, "unusual_geo_access", "HIGH"),
        (
            {"event_type": "config_change", "action": "logging_disabled"},
            "audit_logging_disabled",
            "CRITICAL",
        ),
        (
            {"is_privileged_actor": True, "mfa_used": False},
            "privileged_access_without_mfa",
            "MEDIUM",
        ),
    ],
)
def test_detection_rules_flag_expected_patterns(spark, overrides, expected_reason, expected_severity):
    row = _process(spark, [_event(**overrides)])[0]
    assert row["is_anomaly"] is True
    assert row["threat_reason"] == expected_reason
    assert row["severity"] == expected_severity
    assert row["mitre_tactic"]


def test_off_hours_privileged_activity_is_flagged(spark):
    row = _process(
        spark,
        [_event(event_time="2026-08-06T02:30:00+00:00", is_privileged_actor=True, mfa_used=True)],
    )[0]
    assert row["threat_reason"] == "off_hours_privileged_activity"
    assert row["severity"] == "LOW"
