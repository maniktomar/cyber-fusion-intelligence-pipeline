from __future__ import annotations

from typing import Any


WORKFLOW_TEMPLATES: dict[str, dict[str, Any]] = {
    "sales_anomaly": {
        "name": "Sales Anomaly Investigation",
        "objective": "Analyze sales workflow data and recommend operational next steps",
        "description": "Profiles sales events, anomaly indicators, and business next actions.",
    },
    "incident_response": {
        "name": "Incident Response Workflow",
        "objective": "Triage operational incident evidence and recommend escalation actions",
        "description": "Uses the same agent chain to frame incidents, risk, and response owners.",
    },
    "customer_support": {
        "name": "Customer Support Triage",
        "objective": "Analyze customer support signals and recommend resolution priorities",
        "description": "Demonstrates how the workflow can adapt to service operations.",
    },
    "project_review": {
        "name": "Resume Project Review",
        "objective": "Review project evidence and recommend improvements for an interview demo",
        "description": "Positions the assistant as a portfolio and product-readiness reviewer.",
    },
}


def get_template(template_id: str) -> dict[str, Any]:
    return WORKFLOW_TEMPLATES.get(template_id, WORKFLOW_TEMPLATES["sales_anomaly"])


def list_templates() -> list[dict[str, str]]:
    return [
        {"id": template_id, **template}
        for template_id, template in WORKFLOW_TEMPLATES.items()
    ]
