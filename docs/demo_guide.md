# Demo Guide

Use this guide to demonstrate the project in interviews or portfolio reviews.

## Demo Story

1. A Python producer generates synthetic security events, with a small share carrying deliberate threat patterns and data-quality defects.
2. Kafka receives the event stream through the `security-events` topic.
3. PySpark Structured Streaming cleans, validates, deduplicates, applies detection rules, and aggregates events.
4. Snowflake stores curated facts, quarantined events, aggregate metrics, and AI threat insights.
5. Power BI visualizes KPIs, threat activity, data quality, and AI-generated recommendations.

## Quick Local Demo

Terminal 1:

```powershell
.\scripts\start_kafka.ps1
.\scripts\run_producer.ps1
```

Terminal 2:

```powershell
.\scripts\run_spark_console.ps1
```

Open Kafka UI:

```text
http://localhost:8080
```

## Snowflake Demo

Run the Snowflake SQL scripts:

```text
snowflake/01_create_database_schema.sql
snowflake/02_curated_tables.sql
snowflake/03_views_for_powerbi.sql
```

Terminal 1:

```powershell
.\scripts\start_kafka.ps1
.\scripts\run_producer.ps1
```

Terminal 2:

```powershell
.\scripts\run_spark_snowflake.ps1
```

After data lands in Snowflake:

```powershell
.\scripts\run_ai_insights.ps1
```

## Verification Queries

```sql
SELECT COUNT(*) FROM REALTIME_ANALYTICS.CURATED.FACT_SECURITY_EVENTS;
SELECT COUNT(*) FROM REALTIME_ANALYTICS.CURATED.ERROR_SECURITY_EVENTS;
SELECT COUNT(*) FROM REALTIME_ANALYTICS.CURATED.AGG_SECURITY_METRICS_1M;
SELECT COUNT(*) FROM REALTIME_ANALYTICS.CURATED.AI_THREAT_INSIGHTS;
SELECT * FROM REALTIME_ANALYTICS.MARTS.VW_REALTIME_KPIS;
SELECT * FROM REALTIME_ANALYTICS.MARTS.VW_DATA_QUALITY_SCORECARD;
SELECT * FROM REALTIME_ANALYTICS.MARTS.VW_AI_SECURITY_SUMMARY;

-- Which detection rules are firing
SELECT threat_reason, severity, COUNT(*) AS indicator_count
FROM REALTIME_ANALYTICS.MARTS.VW_THREAT_EVENTS
GROUP BY threat_reason, severity
ORDER BY indicator_count DESC;

-- Why records were quarantined
SELECT quality_failure_reason, COUNT(*) AS quarantined
FROM REALTIME_ANALYTICS.MARTS.VW_QUARANTINED_EVENTS
GROUP BY quality_failure_reason
ORDER BY quarantined DESC;
```

## Power BI Pages

- Security Operations Monitor
- Threat Activity
- Data Quality
- Egress and Access
- AI Threat Insights

## Cleanup

```powershell
.\scripts\stop_kafka.ps1
```
