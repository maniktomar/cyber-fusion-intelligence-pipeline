# Power BI Dashboard Guide

Connect Power BI Desktop to Snowflake using `Get Data > Snowflake`.

Recommended tables or views:

- `MARTS.VW_REALTIME_KPIS`
- `MARTS.VW_SECURITY_TRENDS`
- `MARTS.VW_THREAT_EVENTS`
- `MARTS.VW_QUARANTINED_EVENTS`
- `MARTS.VW_DATA_QUALITY_SCORECARD`
- `MARTS.VW_AI_THREAT_INSIGHTS`

Suggested report pages:

1. Security Operations Monitor
   - Cards: events last hour, failed events, blocked events, threat indicators, peak severity.
   - Line chart: event count by `window_start`.
   - Slicer: event type, target system, source country.

2. Threat Activity
   - Column chart: threat indicator count by `threat_reason`.
   - Bar chart: indicators by `severity` and `target_system`.
   - Map or bar chart: events by `source_country`.
   - Table: threat events with actor, target, reason, severity, MITRE tactic.

3. Data Quality
   - KPI: `quality_score_pct` from `VW_DATA_QUALITY_SCORECARD`.
   - Column chart: quarantined event count by `quality_failure_reason`.
   - Table: recent quarantined records with ingestion time.

4. Egress and Access
   - Line chart: `total_bytes_out` by window.
   - Matrix: event type, target system, event count, failure rate, unique actors.
   - Table: top actors by failed authentication count.

5. AI Threat Insights
   - Card: average risk score.
   - Bar chart: event count by risk level and triage queue.
   - Table: threat explanation and recommended action.
   - Text/table visual: hourly summary from `MARTS.VW_AI_SECURITY_SUMMARY`.

Refresh options:

- Import mode is simplest for portfolio demos.
- DirectQuery is better for near real-time reporting against Snowflake.
- In the Power BI Service, configure scheduled refresh or automatic page refresh where supported.
