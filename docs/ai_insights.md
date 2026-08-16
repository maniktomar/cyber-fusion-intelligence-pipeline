# AI Threat Insights

This project includes an AI-style insights layer that turns threat indicator rows into analyst-readable explanations, risk scores, recommended actions, and triage queue routing.

## What It Adds

- `CURATED.AI_THREAT_INSIGHTS` Snowflake table.
- `MARTS.VW_AI_THREAT_INSIGHTS` Power BI view.
- `MARTS.VW_AI_SECURITY_SUMMARY` plain-English hourly summary view.
- `ai_insights/threat_insights.py` Python generator.

The first version uses a deterministic rules-based AI baseline so it is easy to run, explain, and demo without a paid LLM dependency. The module is intentionally isolated so a future LLM provider can replace only the explanation function.

## Scoring Model

The risk score starts from the pipeline severity (CRITICAL 70, HIGH 55, MEDIUM 40, LOW 25) and adjusts for event context:

- Privilege escalation, disabled audit logging: +20
- Data egress: +15, or +25 above 2 GB
- Brute force: +10, or +20 above 30 failed attempts
- API abuse: +10, or +18 above 1,000 requests per minute
- Unusual geography or missing MFA: +12
- Outcome succeeded: +8; outcome blocked: -10

Scores map to `CRITICAL` (80+), `HIGH` (60+), `MEDIUM` (40+), and `LOW`. Risk level drives the triage queue: `tier2_incident_response` for critical and high, `tier1_soc_review` for medium, `monitoring_only` for low.

## Run It

After the streaming pipeline has written threat rows to Snowflake:

```powershell
.\.venv\Scripts\Activate.ps1
python -m ai_insights.threat_insights --limit 100
```

Then verify in Snowflake:

```sql
SELECT COUNT(*)
FROM REALTIME_ANALYTICS.CURATED.AI_THREAT_INSIGHTS;

SELECT *
FROM REALTIME_ANALYTICS.MARTS.VW_AI_THREAT_INSIGHTS
ORDER BY generated_at DESC
LIMIT 20;

SELECT *
FROM REALTIME_ANALYTICS.MARTS.VW_AI_SECURITY_SUMMARY;
```

Insight IDs are hashed from the event and threat reason, so re-running the job is idempotent.

## Power BI Page

Add a page named `AI Threat Insights`.

Recommended visuals:

- Card: average `RISK_SCORE`.
- Bar chart: count of events by `RISK_LEVEL`.
- Bar chart: count of events by `TRIAGE_QUEUE`.
- Table: `ACTOR_ID`, `RISK_LEVEL`, `THREAT_REASON`, `MITRE_TACTIC`, `THREAT_EXPLANATION`, `RECOMMENDED_ACTION`.
- Multi-row card or table: `VW_AI_SECURITY_SUMMARY.SECURITY_SUMMARY`.

## Limitations

Scoring is rules-based and deterministic. It prioritises events for human review; it does not confirm incidents, and it has no feedback loop from analyst outcomes.
