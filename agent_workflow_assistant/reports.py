from __future__ import annotations

import json
from datetime import datetime, timezone


def build_markdown_report(workflow: dict) -> str:
    lines = [
        "# AI Agent Workflow Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Objective: {workflow['objective']}",
        f"Template: {workflow.get('template_id', 'sales_anomaly')}",
        f"Mode: {workflow.get('llm_provider', 'demo')}",
        f"Status: {workflow['status']}",
        "",
        "## Agent Findings",
    ]
    for finding in workflow.get("agent_findings", []):
        lines.append(f"- **{finding['agent_role']}**: {finding['finding']}")

    lines.extend(["", "## Summary", "", workflow.get("final_summary", "")])
    lines.extend(["", "## Recommended Actions"])
    for action in workflow.get("recommended_next_actions", []):
        lines.append(f"- {action}")

    lines.extend(["", "## Chart Data", "", "```json"])
    lines.append(json.dumps(workflow.get("chart_data", {}), indent=2))
    lines.extend(["```", "", "## Explainability"])
    for step in workflow.get("steps", []):
        lines.append(f"- **{step.get('agent_role', 'Agent')}**: {step.get('explanation', {})}")

    lines.extend(["", "## Workflow Trace"])
    for step in workflow.get("steps", []):
        lines.extend(
            [
                f"### {step['name']}",
                f"- Agent: {step.get('agent_role', 'Agent')}",
                f"- Status: {step['status']}",
                f"- Tool: `{step['tool_name']}`",
                "",
                "```json",
                json.dumps(step.get("result"), indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines)
