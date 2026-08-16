from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from typing import Any

import snowflake.connector
from snowflake.connector import DictCursor

from utils.config import load_config
from utils.logging_utils import get_logger


logger = get_logger("ai_insights.threat_insights", "logs/ai_insights.log")


def connect_to_snowflake():
    config = load_config()["snowflake"]
    return snowflake.connector.connect(
        account=config["account"],
        user=config["user"],
        password=config["password"],
        role=config["role"],
        warehouse=config["warehouse"],
        database=config["database"],
        schema=config["schema"],
    )


def fetch_untriaged_threats(limit: int) -> list[dict[str, Any]]:
    sql = """
    SELECT
      event_timestamp,
      event_id,
      event_type,
      actor_id,
      actor_role,
      target_system,
      source_ip,
      source_country,
      outcome,
      bytes_out,
      failed_attempts_1h,
      request_rate_1m,
      threat_reason,
      severity,
      mitre_tactic
    FROM REALTIME_ANALYTICS.MARTS.VW_THREAT_EVENTS t
    WHERE NOT EXISTS (
      SELECT 1
      FROM REALTIME_ANALYTICS.CURATED.AI_THREAT_INSIGHTS i
      WHERE i.event_id = t.event_id
    )
    ORDER BY event_timestamp DESC
    LIMIT %(limit)s
    """
    with connect_to_snowflake() as conn:
        cursor = conn.cursor(DictCursor)
        try:
            cursor.execute(sql, {"limit": limit})
            return list(cursor.fetchall())
        finally:
            cursor.close()


def score_threat(row: dict[str, Any]) -> tuple[int, str]:
    """Combine the pipeline severity with event context into a 0-100 analyst risk score."""
    reason = str(row.get("THREAT_REASON") or "").lower()
    severity = str(row.get("SEVERITY") or "NONE").upper()
    bytes_out = int(row.get("BYTES_OUT") or 0)
    failed_attempts = int(row.get("FAILED_ATTEMPTS_1H") or 0)
    request_rate = int(row.get("REQUEST_RATE_1M") or 0)
    outcome = str(row.get("OUTCOME") or "").lower()

    score = {"CRITICAL": 70, "HIGH": 55, "MEDIUM": 40, "LOW": 25}.get(severity, 20)

    if "privilege_escalation" in reason:
        score += 20
    if "data_egress" in reason:
        score += 15 if bytes_out < 2_000_000_000 else 25
    if "brute_force" in reason:
        score += 10 if failed_attempts < 30 else 20
    if "api_abuse" in reason:
        score += 10 if request_rate < 1_000 else 18
    if "unusual_geo" in reason or "without_mfa" in reason:
        score += 12
    if "logging_disabled" in reason:
        score += 20
    # A successful outcome means the activity actually landed, not just that it was attempted.
    if outcome == "success":
        score += 8
    elif outcome == "blocked":
        score -= 10

    score = max(0, min(score, 100))
    if score >= 80:
        return score, "CRITICAL"
    if score >= 60:
        return score, "HIGH"
    if score >= 40:
        return score, "MEDIUM"
    return score, "LOW"


def explain_threat(row: dict[str, Any], risk_level: str) -> tuple[str, str, str]:
    """Return the analyst-facing narrative, recommended action, and triage queue."""
    reason = row.get("THREAT_REASON") or "unclassified indicator"
    actor = row.get("ACTOR_ID")
    actor_role = row.get("ACTOR_ROLE")
    target = row.get("TARGET_SYSTEM")
    country = row.get("SOURCE_COUNTRY")
    source_ip = row.get("SOURCE_IP")
    outcome = row.get("OUTCOME")
    mitre = row.get("MITRE_TACTIC") or "unmapped"

    explanation = (
        f"Identity {actor} ({actor_role}) triggered the {reason} rule against {target}. "
        f"The event originated from {source_ip} in {country} and the outcome was {outcome}. "
        f"It maps to MITRE ATT&CK {mitre} and is assessed as {risk_level} risk based on "
        f"severity, volume, and whether the activity succeeded."
    )

    actions = {
        "credential_brute_force": "Lock or step-up authentication for the identity, block the source IP, and confirm whether any attempt succeeded.",
        "unauthorized_privilege_escalation": "Revoke the granted role immediately, verify the change against an approved ticket, and review the approver's account.",
        "large_data_egress": "Suspend the export session, quantify what left the environment, and engage data protection review.",
        "api_abuse_burst": "Apply rate limiting to the client credential and verify whether the traffic is a misconfigured integration or scripted abuse.",
        "unusual_geo_access": "Challenge the session with MFA, confirm travel with the user, and review other activity from the same source.",
        "audit_logging_disabled": "Re-enable logging, treat the gap window as untrusted, and investigate the account that changed the policy.",
        "privileged_access_without_mfa": "Enforce MFA on the privileged account and review what actions were taken during the session.",
        "off_hours_privileged_activity": "Confirm the activity with the account owner and check it against the change calendar.",
    }
    action = actions.get(
        str(reason),
        "Review the event against baseline behaviour for this identity and target system.",
    )

    if risk_level in {"CRITICAL", "HIGH"}:
        triage_queue = "tier2_incident_response"
    elif risk_level == "MEDIUM":
        triage_queue = "tier1_soc_review"
    else:
        triage_queue = "monitoring_only"

    return explanation, action, triage_queue


def build_insight(row: dict[str, Any]) -> dict[str, Any]:
    risk_score, risk_level = score_threat(row)
    explanation, action, triage_queue = explain_threat(row, risk_level)
    generated_at = datetime.now(UTC).replace(tzinfo=None)
    insight_key = f"{row.get('EVENT_ID')}|{row.get('THREAT_REASON')}"
    insight_id = hashlib.sha256(insight_key.encode("utf-8")).hexdigest()[:32]

    return {
        "insight_id": insight_id,
        "generated_at": generated_at,
        "event_timestamp": row.get("EVENT_TIMESTAMP"),
        "event_id": row.get("EVENT_ID"),
        "event_type": row.get("EVENT_TYPE"),
        "actor_id": row.get("ACTOR_ID"),
        "actor_role": row.get("ACTOR_ROLE"),
        "target_system": row.get("TARGET_SYSTEM"),
        "source_ip": row.get("SOURCE_IP"),
        "source_country": row.get("SOURCE_COUNTRY"),
        "outcome": row.get("OUTCOME"),
        "bytes_out": row.get("BYTES_OUT"),
        "failed_attempts_1h": row.get("FAILED_ATTEMPTS_1H"),
        "request_rate_1m": row.get("REQUEST_RATE_1M"),
        "threat_reason": row.get("THREAT_REASON"),
        "severity": row.get("SEVERITY"),
        "mitre_tactic": row.get("MITRE_TACTIC"),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "threat_explanation": explanation,
        "recommended_action": action,
        "triage_queue": triage_queue,
        "model_provider": "rules_based_ai_baseline",
    }


def write_insights(insights: list[dict[str, Any]]) -> int:
    if not insights:
        return 0

    sql = """
    INSERT INTO REALTIME_ANALYTICS.CURATED.AI_THREAT_INSIGHTS (
      insight_id,
      generated_at,
      event_timestamp,
      event_id,
      event_type,
      actor_id,
      actor_role,
      target_system,
      source_ip,
      source_country,
      outcome,
      bytes_out,
      failed_attempts_1h,
      request_rate_1m,
      threat_reason,
      severity,
      mitre_tactic,
      risk_score,
      risk_level,
      threat_explanation,
      recommended_action,
      triage_queue,
      model_provider
    )
    SELECT
      %(insight_id)s,
      %(generated_at)s,
      %(event_timestamp)s,
      %(event_id)s,
      %(event_type)s,
      %(actor_id)s,
      %(actor_role)s,
      %(target_system)s,
      %(source_ip)s,
      %(source_country)s,
      %(outcome)s,
      %(bytes_out)s,
      %(failed_attempts_1h)s,
      %(request_rate_1m)s,
      %(threat_reason)s,
      %(severity)s,
      %(mitre_tactic)s,
      %(risk_score)s,
      %(risk_level)s,
      %(threat_explanation)s,
      %(recommended_action)s,
      %(triage_queue)s,
      %(model_provider)s
    WHERE NOT EXISTS (
      SELECT 1
      FROM REALTIME_ANALYTICS.CURATED.AI_THREAT_INSIGHTS
      WHERE insight_id = %(insight_id)s
    )
    """
    with connect_to_snowflake() as conn:
        cursor = conn.cursor()
        try:
            inserted = 0
            for insight in insights:
                cursor.execute(sql, insight)
                inserted += cursor.rowcount
            return inserted
        finally:
            cursor.close()


def run(limit: int) -> None:
    rows = fetch_untriaged_threats(limit)
    insights = [build_insight(row) for row in rows]
    written = write_insights(insights)
    logger.info(
        "Generated AI threat insights",
        extra={"extra_fields": {"fetched": len(rows), "written": written}},
    )
    print(f"Generated {len(insights)} insights; inserted {written} new rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate AI-style threat triage insights from Snowflake events.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum untriaged threat rows to process.")
    args = parser.parse_args()
    run(args.limit)
