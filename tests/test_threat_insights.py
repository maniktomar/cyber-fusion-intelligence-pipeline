import pytest

from ai_insights.threat_insights import build_insight, explain_threat, score_threat


def _row(**overrides):
    row = {
        "EVENT_ID": "evt-1",
        "EVENT_TIMESTAMP": "2026-08-06 09:00:00",
        "EVENT_TYPE": "authentication",
        "ACTOR_ID": "USR-12345",
        "ACTOR_ROLE": "contractor",
        "TARGET_SYSTEM": "admin-console",
        "SOURCE_IP": "203.0.113.10",
        "SOURCE_COUNTRY": "IE",
        "OUTCOME": "success",
        "BYTES_OUT": 1000,
        "FAILED_ATTEMPTS_1H": 0,
        "REQUEST_RATE_1M": 5,
        "THREAT_REASON": "credential_brute_force",
        "SEVERITY": "HIGH",
        "MITRE_TACTIC": "TA0006:T1110",
    }
    row.update(overrides)
    return row


def test_critical_severity_escalation_scores_higher_than_medium_indicator():
    escalation = score_threat(
        _row(THREAT_REASON="unauthorized_privilege_escalation", SEVERITY="CRITICAL", EVENT_TYPE="privilege_change")
    )
    api_abuse = score_threat(
        _row(THREAT_REASON="api_abuse_burst", SEVERITY="MEDIUM", EVENT_TYPE="api_call", REQUEST_RATE_1M=400)
    )
    assert escalation[0] > api_abuse[0]
    assert escalation[1] == "CRITICAL"


def test_blocked_outcome_scores_lower_than_successful_outcome():
    succeeded, _ = score_threat(_row(OUTCOME="success"))
    blocked, _ = score_threat(_row(OUTCOME="blocked"))
    assert blocked < succeeded


def test_large_egress_volume_increases_score():
    smaller, _ = score_threat(_row(THREAT_REASON="large_data_egress", SEVERITY="CRITICAL", BYTES_OUT=600_000_000))
    larger, _ = score_threat(_row(THREAT_REASON="large_data_egress", SEVERITY="CRITICAL", BYTES_OUT=5_000_000_000))
    assert larger > smaller


@pytest.mark.parametrize("score_is_capped_row", [_row(SEVERITY="CRITICAL", THREAT_REASON="large_data_egress")])
def test_score_stays_within_bounds(score_is_capped_row):
    score, level = score_threat(score_is_capped_row)
    assert 0 <= score <= 100
    assert level in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def test_explanation_routes_high_risk_to_incident_response():
    explanation, action, queue = explain_threat(_row(), "CRITICAL")
    assert "USR-12345" in explanation
    assert "TA0006:T1110" in explanation
    assert action
    assert queue == "tier2_incident_response"


def test_low_risk_routes_to_monitoring_only():
    _, _, queue = explain_threat(_row(), "LOW")
    assert queue == "monitoring_only"


def test_build_insight_is_deterministic_for_the_same_event():
    first = build_insight(_row())
    second = build_insight(_row())
    assert first["insight_id"] == second["insight_id"]
    assert first["threat_reason"] == "credential_brute_force"
    assert first["model_provider"] == "rules_based_ai_baseline"
