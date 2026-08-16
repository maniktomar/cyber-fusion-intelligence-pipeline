from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from faker import Faker


fake = Faker()

EVENT_TYPES = ["authentication", "data_access", "api_call", "privilege_change", "config_change"]
EVENT_TYPE_WEIGHTS = [0.45, 0.2, 0.2, 0.08, 0.07]

TARGET_SYSTEMS = [
    {"target_system": "identity-provider", "target_resource": "sso/session"},
    {"target_system": "hr-portal", "target_resource": "employee/records"},
    {"target_system": "finance-erp", "target_resource": "ledger/journals"},
    {"target_system": "customer-crm", "target_resource": "accounts/contacts"},
    {"target_system": "cloud-storage", "target_resource": "buckets/exports"},
    {"target_system": "admin-console", "target_resource": "iam/roles"},
]

ACTOR_ROLES = ["analyst", "engineer", "support", "finance", "contractor", "domain_admin", "service_account"]
PRIVILEGED_ROLES = {"domain_admin", "service_account"}
DEPARTMENTS = ["engineering", "finance", "operations", "hr", "security", "sales"]

AUTH_METHODS = ["password", "sso", "mfa_push", "api_key", "certificate"]
DEVICE_TYPES = ["managed_laptop", "byod_mobile", "server", "unmanaged_desktop"]
COMMON_COUNTRIES = ["IE", "GB", "US", "DE", "IN", "ES"]
RARE_COUNTRIES = ["RU", "KP", "IR", "BY", "NG"]

ACTIONS = {
    "authentication": ["login", "logout", "token_refresh", "mfa_challenge"],
    "data_access": ["read", "export", "download", "query"],
    "api_call": ["GET /v1/accounts", "POST /v1/transfers", "GET /v1/reports", "DELETE /v1/sessions"],
    "privilege_change": ["role_granted", "role_revoked", "group_membership_added", "permission_elevated"],
    "config_change": ["policy_updated", "firewall_rule_changed", "logging_disabled", "key_rotated"],
}

# Threat patterns injected at `anomaly_rate` so downstream detection logic has signal to find.
THREAT_PATTERNS = [
    "credential_brute_force",
    "privilege_escalation",
    "data_exfiltration",
    "api_abuse",
    "unusual_geo_access",
]

# Data-quality defects injected at `invalid_rate` so the quarantine path has records to catch.
QUALITY_DEFECTS = ["unknown_event_type", "missing_actor", "negative_bytes", "invalid_status_code", "null_target"]

# Contract shared with the Spark validation rules in pyspark_jobs.transformations.
ACCEPTED_FIELDS = {
    "event_type": EVENT_TYPES,
    "outcome": ["success", "failure", "blocked"],
}


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def generate_security_event(anomaly_rate: float = 0.05, invalid_rate: float = 0.02) -> dict[str, Any]:
    """Generate one synthetic security-operations event.

    A small share of events carry deliberate threat patterns (`anomaly_rate`) and a
    separate share carry data-quality defects (`invalid_rate`) so the streaming job
    exercises both its detection rules and its quarantine path.
    """
    event_type = random.choices(EVENT_TYPES, weights=EVENT_TYPE_WEIGHTS, k=1)[0]
    target = random.choice(TARGET_SYSTEMS)
    actor_role = random.choice(ACTOR_ROLES)
    is_privileged = actor_role in PRIVILEGED_ROLES
    auth_method = random.choice(AUTH_METHODS)
    outcome = random.choices(["success", "failure", "blocked"], weights=[0.82, 0.14, 0.04], k=1)[0]

    event_time = datetime.now(UTC)
    bytes_out = random.randint(0, 250_000) if event_type == "data_access" else random.randint(0, 20_000)
    failed_attempts_1h = random.randint(0, 3) if outcome == "failure" else 0
    request_rate_1m = random.randint(1, 40)
    http_status = 200 if outcome == "success" else random.choice([401, 403, 429])
    source_country = random.choice(COMMON_COUNTRIES)
    mfa_used = auth_method in {"mfa_push", "certificate"} or random.random() < 0.6

    threat_pattern = random.choice(THREAT_PATTERNS) if random.random() < anomaly_rate else None

    if threat_pattern == "credential_brute_force":
        event_type = "authentication"
        outcome = "failure"
        auth_method = "password"
        mfa_used = False
        failed_attempts_1h = random.randint(12, 60)
        http_status = 401
    elif threat_pattern == "privilege_escalation":
        event_type = "privilege_change"
        outcome = "success"
        actor_role = random.choice(["contractor", "support", "analyst"])
        is_privileged = False
        http_status = 200
        # Off-hours escalation is the classic tell, so bias the timestamp accordingly.
        event_time = event_time.replace(hour=random.choice([1, 2, 3, 23]))
    elif threat_pattern == "data_exfiltration":
        event_type = "data_access"
        outcome = "success"
        target = {"target_system": "cloud-storage", "target_resource": "buckets/exports"}
        bytes_out = random.randint(750_000_000, 8_000_000_000)
        http_status = 200
    elif threat_pattern == "api_abuse":
        event_type = "api_call"
        request_rate_1m = random.randint(400, 5_000)
        outcome = random.choice(["failure", "blocked"])
        http_status = 429
    elif threat_pattern == "unusual_geo_access":
        outcome = "success"
        source_country = random.choice(RARE_COUNTRIES)
        mfa_used = False
        http_status = 200

    actor_id = f"USR-{random.randint(10000, 99999)}"
    event = {
        "event_id": str(uuid.uuid4()),
        "event_time": _iso(event_time),
        "event_type": event_type,
        "action": random.choice(ACTIONS[event_type]),
        "actor_id": actor_id,
        "actor_email": fake.email(),
        "actor_role": actor_role,
        "actor_department": random.choice(DEPARTMENTS),
        "is_privileged_actor": is_privileged,
        "session_id": f"SES-{uuid.uuid4().hex[:12].upper()}",
        "source_ip": fake.ipv4_public(),
        "source_country": source_country,
        "device_type": random.choice(DEVICE_TYPES),
        "user_agent": fake.user_agent(),
        "target_system": target["target_system"],
        "target_resource": target["target_resource"],
        "auth_method": auth_method,
        "mfa_used": mfa_used,
        "outcome": outcome,
        "http_status": http_status,
        "bytes_out": bytes_out,
        "failed_attempts_1h": failed_attempts_1h,
        "request_rate_1m": request_rate_1m,
        "detection_source": random.choice(["siem", "cloud_audit_log", "edr", "api_gateway"]),
        "is_test_event": False,
        "source_system": "simulated-siem",
    }

    if random.random() < invalid_rate:
        defect = random.choice(QUALITY_DEFECTS)
        if defect == "unknown_event_type":
            event["event_type"] = random.choice(["", "unknown", "syslog_blob"])
        elif defect == "missing_actor":
            event["actor_id"] = ""
        elif defect == "negative_bytes":
            event["bytes_out"] = -random.randint(1, 5_000)
        elif defect == "invalid_status_code":
            event["http_status"] = random.choice([0, 99, 999])
        elif defect == "null_target":
            event["target_system"] = None

    return event


def generate_late_event(max_delay_minutes: int = 20, **kwargs: Any) -> dict[str, Any]:
    """Generate an event stamped in the past, for exercising watermark/late-arrival handling."""
    event = generate_security_event(**kwargs)
    delay = timedelta(minutes=random.randint(1, max_delay_minutes))
    event["event_time"] = _iso(datetime.now(UTC) - delay)
    return event
