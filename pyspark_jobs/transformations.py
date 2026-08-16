from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


ACCEPTED_EVENT_TYPES = ["authentication", "data_access", "api_call", "privilege_change", "config_change"]
ACCEPTED_OUTCOMES = ["success", "failure", "blocked"]
HIGH_RISK_COUNTRIES = ["RU", "KP", "IR", "BY", "NG"]

SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def clean_security_events(events: DataFrame, max_bytes_out: float = 10_000_000_000.0) -> DataFrame:
    """Normalise raw events and attach the quality flags that drive the quarantine split."""
    return (
        events.withColumn("event_timestamp", F.to_timestamp("event_time"))
        .withColumn("ingestion_timestamp", F.current_timestamp())
        .withColumn("actor_email", F.lower(F.trim("actor_email")))
        .withColumn("event_type", F.lower(F.trim("event_type")))
        .withColumn("outcome", F.lower(F.trim("outcome")))
        .withColumn("target_system", F.lower(F.trim("target_system")))
        .withColumn("source_country", F.upper(F.trim("source_country")))
        .withColumn("event_hour", F.hour("event_timestamp"))
        .withColumn("is_valid_event_type", F.col("event_type").isin(ACCEPTED_EVENT_TYPES))
        .withColumn("is_valid_outcome", F.col("outcome").isin(ACCEPTED_OUTCOMES))
        .withColumn("is_valid_actor", F.col("actor_id").isNotNull() & (F.length(F.trim("actor_id")) > 0))
        .withColumn("is_valid_target", F.col("target_system").isNotNull() & (F.length("target_system") > 0))
        .withColumn("is_valid_bytes", (F.col("bytes_out") >= 0) & (F.col("bytes_out") <= max_bytes_out))
        .withColumn(
            "is_valid_status",
            F.col("http_status").isNull() | ((F.col("http_status") >= 100) & (F.col("http_status") <= 599)),
        )
        .withColumn(
            "quality_status",
            F.when(
                F.col("is_valid_event_type")
                & F.col("is_valid_outcome")
                & F.col("is_valid_actor")
                & F.col("is_valid_target")
                & F.col("is_valid_bytes")
                & F.col("is_valid_status")
                & F.col("event_timestamp").isNotNull(),
                F.lit("valid"),
            ).otherwise(F.lit("invalid")),
        )
        .withColumn(
            "quality_failure_reason",
            F.concat_ws(
                ",",
                F.when(~F.col("is_valid_event_type"), F.lit("unknown_event_type")),
                F.when(~F.col("is_valid_outcome"), F.lit("unknown_outcome")),
                F.when(~F.col("is_valid_actor"), F.lit("missing_actor")),
                F.when(~F.col("is_valid_target"), F.lit("missing_target")),
                F.when(~F.col("is_valid_bytes"), F.lit("bytes_out_out_of_range")),
                F.when(~F.col("is_valid_status"), F.lit("invalid_http_status")),
                F.when(F.col("event_timestamp").isNull(), F.lit("unparseable_event_time")),
            ),
        )
    )


def deduplicate_events(events: DataFrame, watermark_delay: str) -> DataFrame:
    """Drop repeated event_ids inside the watermark window (at-least-once sources replay)."""
    return events.withWatermark("event_timestamp", watermark_delay).dropDuplicates(["event_id", "event_timestamp"])


def add_threat_flags(events: DataFrame) -> DataFrame:
    """Attach threat reason, severity, and MITRE ATT&CK tactic to each event.

    These are deterministic indicators for analyst triage, not confirmed detections;
    scoring and narrative explanation happen downstream in the AI insights job.
    """
    threat_reason = (
        F.when(
            (F.col("event_type") == "authentication")
            & (F.col("outcome") == "failure")
            & (F.col("failed_attempts_1h") >= 10),
            F.lit("credential_brute_force"),
        )
        .when(
            (F.col("event_type") == "privilege_change")
            & (F.col("outcome") == "success")
            & (~F.coalesce(F.col("is_privileged_actor"), F.lit(False))),
            F.lit("unauthorized_privilege_escalation"),
        )
        .when(
            (F.col("event_type") == "data_access") & (F.col("bytes_out") >= 500_000_000),
            F.lit("large_data_egress"),
        )
        .when(
            (F.col("event_type") == "api_call") & (F.col("request_rate_1m") >= 300),
            F.lit("api_abuse_burst"),
        )
        .when(
            F.col("source_country").isin(HIGH_RISK_COUNTRIES) & (F.col("outcome") == "success"),
            F.lit("unusual_geo_access"),
        )
        .when(
            (F.col("event_type") == "config_change") & (F.col("action") == "logging_disabled"),
            F.lit("audit_logging_disabled"),
        )
        .when(
            F.coalesce(F.col("is_privileged_actor"), F.lit(False))
            & (F.col("outcome") == "success")
            & (~F.coalesce(F.col("mfa_used"), F.lit(False))),
            F.lit("privileged_access_without_mfa"),
        )
        .when(
            F.coalesce(F.col("is_privileged_actor"), F.lit(False))
            & (F.col("outcome") == "success")
            & ((F.col("event_hour") < 6) | (F.col("event_hour") >= 22)),
            F.lit("off_hours_privileged_activity"),
        )
        .otherwise(F.lit(None))
    )

    severity = (
        F.when(F.col("threat_reason").isNull(), F.lit("NONE"))
        .when(
            F.col("threat_reason").isin("unauthorized_privilege_escalation", "large_data_egress", "audit_logging_disabled"),
            F.lit("CRITICAL"),
        )
        .when(F.col("threat_reason").isin("credential_brute_force", "unusual_geo_access"), F.lit("HIGH"))
        .when(F.col("threat_reason").isin("api_abuse_burst", "privileged_access_without_mfa"), F.lit("MEDIUM"))
        .otherwise(F.lit("LOW"))
    )

    mitre_tactic = (
        F.when(F.col("threat_reason") == "credential_brute_force", F.lit("TA0006:T1110"))
        .when(F.col("threat_reason") == "unauthorized_privilege_escalation", F.lit("TA0004:T1078"))
        .when(F.col("threat_reason") == "large_data_egress", F.lit("TA0010:T1041"))
        .when(F.col("threat_reason") == "api_abuse_burst", F.lit("TA0040:T1499"))
        .when(F.col("threat_reason") == "unusual_geo_access", F.lit("TA0001:T1078.004"))
        .when(F.col("threat_reason") == "audit_logging_disabled", F.lit("TA0005:T1562.008"))
        .when(F.col("threat_reason") == "privileged_access_without_mfa", F.lit("TA0001:T1078"))
        .when(F.col("threat_reason") == "off_hours_privileged_activity", F.lit("TA0003:T1078"))
        .otherwise(F.lit(None))
    )

    severity_rank = (
        F.when(F.col("severity") == "CRITICAL", F.lit(4))
        .when(F.col("severity") == "HIGH", F.lit(3))
        .when(F.col("severity") == "MEDIUM", F.lit(2))
        .when(F.col("severity") == "LOW", F.lit(1))
        .otherwise(F.lit(0))
    )

    return (
        events.withColumn("threat_reason", threat_reason)
        .withColumn("is_anomaly", F.col("threat_reason").isNotNull())
        .withColumn("severity", severity)
        .withColumn("severity_rank", severity_rank)
        .withColumn("mitre_tactic", mitre_tactic)
    )


def aggregate_security_metrics(valid_events: DataFrame, watermark_delay: str) -> DataFrame:
    """One-minute KPI windows for the Power BI security operations dashboard."""
    return (
        valid_events.withWatermark("event_timestamp", watermark_delay)
        .groupBy(
            F.window("event_timestamp", "1 minute"),
            "event_type",
            "target_system",
            "source_country",
        )
        .agg(
            F.count("*").alias("event_count"),
            F.sum(F.when(F.col("outcome") == "failure", 1).otherwise(0)).alias("failure_count"),
            F.sum(F.when(F.col("outcome") == "blocked", 1).otherwise(0)).alias("blocked_count"),
            F.approx_count_distinct("actor_id").alias("unique_actors"),
            F.approx_count_distinct("source_ip").alias("unique_source_ips"),
            F.sum("bytes_out").alias("total_bytes_out"),
            F.sum(F.when(F.col("is_anomaly"), 1).otherwise(0)).alias("anomaly_count"),
            F.max("severity_rank").alias("max_severity_rank"),
        )
        .select(
            F.col("window.start").alias("window_start"),
            F.col("window.end").alias("window_end"),
            "event_type",
            "target_system",
            "source_country",
            "event_count",
            "failure_count",
            "blocked_count",
            F.round(F.col("failure_count") / F.col("event_count"), 4).alias("failure_rate"),
            "unique_actors",
            "unique_source_ips",
            "total_bytes_out",
            "anomaly_count",
            "max_severity_rank",
        )
    )
