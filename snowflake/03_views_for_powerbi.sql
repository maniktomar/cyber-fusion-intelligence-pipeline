USE DATABASE REALTIME_ANALYTICS;
USE SCHEMA MARTS;

CREATE OR REPLACE VIEW VW_REALTIME_KPIS AS
SELECT
  MAX(window_end) AS latest_window,
  SUM(event_count) AS events_last_hour,
  SUM(failure_count) AS failed_events_last_hour,
  SUM(blocked_count) AS blocked_events_last_hour,
  ROUND(SUM(failure_count) / NULLIF(SUM(event_count), 0), 4) AS failure_rate,
  SUM(total_bytes_out) AS bytes_egressed_last_hour,
  SUM(anomaly_count) AS threat_indicators_last_hour,
  MAX(max_severity_rank) AS peak_severity_rank
FROM CURATED.AGG_SECURITY_METRICS_1M
WHERE window_end >= DATEADD(hour, -1, CURRENT_TIMESTAMP());

CREATE OR REPLACE VIEW VW_SECURITY_TRENDS AS
SELECT
  window_start,
  window_end,
  event_type,
  target_system,
  source_country,
  event_count,
  failure_count,
  blocked_count,
  failure_rate,
  unique_actors,
  unique_source_ips,
  total_bytes_out,
  anomaly_count,
  max_severity_rank
FROM CURATED.AGG_SECURITY_METRICS_1M;

CREATE OR REPLACE VIEW VW_THREAT_EVENTS AS
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
FROM CURATED.FACT_SECURITY_EVENTS
WHERE is_anomaly = TRUE;

-- Quarantined records, exposed so data-quality issues stay visible rather than silent.
CREATE OR REPLACE VIEW VW_QUARANTINED_EVENTS AS
SELECT
  event_timestamp,
  ingestion_timestamp,
  event_id,
  event_type,
  actor_id,
  target_system,
  outcome,
  http_status,
  bytes_out,
  quality_failure_reason
FROM CURATED.ERROR_SECURITY_EVENTS;

CREATE OR REPLACE VIEW VW_DATA_QUALITY_SCORECARD AS
WITH counts AS (
  SELECT
    (SELECT COUNT(*) FROM CURATED.FACT_SECURITY_EVENTS
      WHERE ingestion_timestamp >= DATEADD(hour, -1, CURRENT_TIMESTAMP())) AS valid_events,
    (SELECT COUNT(*) FROM CURATED.ERROR_SECURITY_EVENTS
      WHERE ingestion_timestamp >= DATEADD(hour, -1, CURRENT_TIMESTAMP())) AS quarantined_events
)
SELECT
  CURRENT_TIMESTAMP() AS generated_at,
  valid_events,
  quarantined_events,
  valid_events + quarantined_events AS total_events,
  ROUND(valid_events / NULLIF(valid_events + quarantined_events, 0) * 100, 2) AS quality_score_pct
FROM counts;

CREATE OR REPLACE VIEW VW_AI_THREAT_INSIGHTS AS
SELECT
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
  threat_reason,
  severity,
  mitre_tactic,
  risk_score,
  risk_level,
  threat_explanation,
  recommended_action,
  triage_queue,
  model_provider
FROM CURATED.AI_THREAT_INSIGHTS;

CREATE OR REPLACE VIEW VW_AI_SECURITY_SUMMARY AS
WITH events AS (
  SELECT
    COUNT(*) AS total_events,
    COUNT_IF(is_anomaly) AS threat_indicators,
    COUNT_IF(severity = 'CRITICAL') AS critical_indicators,
    COUNT_IF(outcome = 'failure') AS failed_events,
    COUNT(DISTINCT actor_id) AS distinct_actors
  FROM CURATED.FACT_SECURITY_EVENTS
  WHERE event_timestamp >= DATEADD(hour, -1, CURRENT_TIMESTAMP())
),
top_threat AS (
  SELECT threat_reason, COUNT(*) AS indicator_count
  FROM CURATED.FACT_SECURITY_EVENTS
  WHERE is_anomaly = TRUE
    AND event_timestamp >= DATEADD(hour, -1, CURRENT_TIMESTAMP())
  GROUP BY threat_reason
  QUALIFY ROW_NUMBER() OVER (ORDER BY indicator_count DESC) = 1
),
top_target AS (
  SELECT target_system, SUM(anomaly_count) AS target_indicators
  FROM CURATED.AGG_SECURITY_METRICS_1M
  WHERE window_end >= DATEADD(hour, -1, CURRENT_TIMESTAMP())
  GROUP BY target_system
  QUALIFY ROW_NUMBER() OVER (ORDER BY target_indicators DESC) = 1
)
SELECT
  CURRENT_TIMESTAMP() AS generated_at,
  'In the last hour, the platform processed ' || COALESCE(total_events, 0) ||
  ' security events across ' || COALESCE(distinct_actors, 0) ||
  ' identities, with ' || COALESCE(failed_events, 0) ||
  ' failed outcomes. Detection rules raised ' || COALESCE(threat_indicators, 0) ||
  ' threat indicators, of which ' || COALESCE(critical_indicators, 0) ||
  ' were critical. The most frequent pattern was ' || COALESCE(top_threat.threat_reason, 'n/a') ||
  ' and the most targeted system was ' || COALESCE(top_target.target_system, 'n/a') || '.' AS security_summary,
  total_events,
  distinct_actors,
  failed_events,
  threat_indicators,
  critical_indicators,
  top_threat.threat_reason AS top_threat_pattern,
  top_target.target_system AS top_targeted_system
FROM events
LEFT JOIN top_threat ON TRUE
LEFT JOIN top_target ON TRUE;
