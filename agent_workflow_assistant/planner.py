from __future__ import annotations

from agent_workflow_assistant.models import WorkflowPlan, WorkflowRequest, WorkflowStep
from agent_workflow_assistant.templates import get_template


def create_plan(request: WorkflowRequest) -> WorkflowPlan:
    data_path = request.context.get("data_path", "data/sample_sales_events.jsonl")
    limit = request.context.get("limit", 25)
    template_id = request.context.get("template_id", "sales_anomaly")
    template = get_template(template_id)

    return WorkflowPlan(
        objective=request.objective,
        template_id=template_id,
        steps=[
            WorkflowStep(
                name=f"Plan {template['name']}",
                agent_role="Planner Agent",
                tool_name="load_jsonl_events",
                tool_input={"path": data_path, "limit": limit},
            ),
            WorkflowStep(
                name="Analyze workflow evidence",
                agent_role="Data Analyst Agent",
                tool_name="profile_sales_events",
            ),
            WorkflowStep(
                name="Review risk signals",
                agent_role="Risk Reviewer Agent",
                tool_name="review_risk_signals",
            ),
            WorkflowStep(
                name="Recommend next actions",
                agent_role="Action Recommender Agent",
                tool_name="recommend_workflow_actions",
            ),
        ],
    )
