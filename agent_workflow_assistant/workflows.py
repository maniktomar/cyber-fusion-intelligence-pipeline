from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from agent_workflow_assistant.llm import LLMClient, build_llm_client
from agent_workflow_assistant.models import AgentFinding, StepStatus, WorkflowRequest, WorkflowRun
from agent_workflow_assistant.planner import create_plan
from agent_workflow_assistant.tools import TOOL_REGISTRY


def run_workflow(
    objective: str,
    context: dict | None = None,
    llm_provider: str = "demo",
    llm_client: LLMClient | None = None,
) -> WorkflowRun:
    run_id = f"run-{uuid4().hex[:10]}"
    created_at = datetime.now(timezone.utc).isoformat()
    request = WorkflowRequest(objective=objective, context=context or {})
    plan = create_plan(request)
    shared_state: dict = {}

    for step in plan.steps:
        step.status = StepStatus.RUNNING
        tool = TOOL_REGISTRY[step.tool_name]
        tool_input = dict(step.tool_input)

        if step.tool_name == "profile_sales_events":
            tool_input["events"] = shared_state.get("events", [])
        if step.tool_name == "recommend_workflow_actions":
            tool_input["profile"] = shared_state.get("profile", {})
            tool_input["risk"] = shared_state.get("risk", {})
        if step.tool_name == "review_risk_signals":
            tool_input["profile"] = shared_state.get("profile", {})

        try:
            step.result = tool(tool_input)
            step.explanation = _build_step_explanation(step)
            step.status = StepStatus.COMPLETED
        except Exception as exc:
            step.result = {"error": str(exc)}
            step.explanation = {
                "decision": "Step failed before completion.",
                "confidence": "low",
                "why": str(exc),
            }
            step.status = StepStatus.FAILED
            break

        if step.tool_name == "load_jsonl_events":
            shared_state["events"] = step.result.get("events", [])
        if step.tool_name == "profile_sales_events":
            shared_state["profile"] = step.result
        if step.tool_name == "review_risk_signals":
            shared_state["risk"] = step.result
        if step.tool_name == "recommend_workflow_actions":
            shared_state["actions"] = step.result.get("actions", [])

    status = (
        StepStatus.COMPLETED
        if all(step.status == StepStatus.COMPLETED for step in plan.steps)
        else StepStatus.FAILED
    )
    evidence = _build_evidence(shared_state)
    client = llm_client or build_llm_client(llm_provider)

    return WorkflowRun(
        run_id=run_id,
        created_at=created_at,
        objective=objective,
        template_id=plan.template_id,
        llm_provider=llm_provider,
        status=status,
        steps=plan.steps,
        agent_findings=_build_agent_findings(plan.steps),
        chart_data=_build_chart_data(shared_state),
        final_summary=client.summarize(objective, evidence),
        recommended_next_actions=shared_state.get("actions", []),
    )


def _build_evidence(shared_state: dict) -> list[str]:
    profile = shared_state.get("profile", {})
    if not profile:
        return []
    return [
        f"processed {profile.get('event_count', 0)} events",
        f"total revenue was {profile.get('total_revenue', 0)}",
        f"top channel was {profile.get('top_channel', 'unknown')}",
        f"detected {profile.get('anomaly_count', 0)} anomalies",
    ]


def _build_agent_findings(steps) -> list[AgentFinding]:
    findings: list[AgentFinding] = []
    for step in steps:
        if not step.result or step.status != StepStatus.COMPLETED:
            continue
        if step.tool_name == "load_jsonl_events":
            finding = f"Loaded {step.result.get('event_count', 0)} records for analysis."
        elif step.tool_name == "profile_sales_events":
            finding = (
                f"Profiled revenue {step.result.get('total_revenue', 0)} "
                f"with {step.result.get('anomaly_count', 0)} anomalies."
            )
        elif step.tool_name == "review_risk_signals":
            finding = f"Rated workflow risk as {step.result.get('risk_level', 'unknown')}."
        else:
            finding = f"Prepared {len(step.result.get('actions', []))} recommended actions."
        findings.append(AgentFinding(agent_role=step.agent_role, finding=finding))
    return findings


def _build_step_explanation(step) -> dict:
    result = step.result or {}
    if step.tool_name == "load_jsonl_events":
        return {
            "input_used": result.get("source_path"),
            "decision": f"Loaded {result.get('event_count', 0)} records for downstream analysis.",
            "confidence": "high" if result.get("event_count", 0) else "low",
            "why": "The workflow needs structured evidence before other agents can reason over it.",
        }
    if step.tool_name == "profile_sales_events":
        return {
            "input_used": result.get("detected_columns", {}),
            "decision": f"Detected {result.get('anomaly_count', 0)} anomalies across {result.get('event_count', 0)} records.",
            "confidence": "medium" if result.get("detected_columns", {}).get("amount") else "low",
            "why": "Column detection maps uploaded data into revenue, channel, category, and anomaly signals.",
        }
    if step.tool_name == "review_risk_signals":
        return {
            "input_used": f"{result.get('anomaly_rate_percent', 0)}% anomaly rate",
            "decision": f"Assigned {result.get('risk_level', 'unknown')} risk.",
            "confidence": "high",
            "why": result.get("review_note", "Risk is derived from anomaly concentration."),
        }
    return {
        "input_used": f"{len(result.get('actions', []))} recommended actions",
        "decision": "Prepared stakeholder-ready next steps.",
        "confidence": "high",
        "why": "Actions combine operational summary needs, review guardrails, and data sufficiency.",
    }


def _build_chart_data(shared_state: dict) -> dict:
    profile = shared_state.get("profile", {})
    risk = shared_state.get("risk", {})
    event_count = int(profile.get("event_count", 0) or 0)
    anomaly_count = int(profile.get("anomaly_count", 0) or 0)
    return {
        "channels": profile.get("channel_breakdown", {}),
        "categories": profile.get("category_breakdown", {}),
        "risk": {
            "level": risk.get("risk_level", "unknown"),
            "anomaly_rate_percent": risk.get("anomaly_rate_percent", 0),
        },
        "quality": {
            "normal_events": max(event_count - anomaly_count, 0),
            "anomaly_events": anomaly_count,
        },
    }
