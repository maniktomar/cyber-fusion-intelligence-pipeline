from api.agent_workflow_api import (
    AgentWorkflowPayload,
    ReportPayload,
    markdown_report,
    run_agent_workflow,
    templates,
)


def test_agent_workflow_api_runs_workflow():
    payload = run_agent_workflow(
        AgentWorkflowPayload(
            objective="Analyze sales workflow data for demo",
            data_path="data/sample_sales_events.jsonl",
            limit=2,
            template_id="sales_anomaly",
            llm_provider="demo",
        )
    )

    assert payload["status"] == "completed"
    assert len(payload["steps"]) == 4
    assert payload["run_id"].startswith("run-")
    assert payload["chart_data"]


def test_agent_workflow_api_lists_templates():
    payload = templates()
    assert {template["id"] for template in payload} >= {"sales_anomaly", "incident_response"}


def test_agent_workflow_api_dashboard_has_history():
    workflow = run_agent_workflow(
        AgentWorkflowPayload(
            objective="Analyze dashboard history",
            data_path="data/sample_sales_events.jsonl",
            limit=2,
        )
    )
    from api.agent_workflow_api import dashboard, run_detail

    payload = dashboard()
    assert payload["total_runs"] >= 1
    assert run_detail(workflow["run_id"])["run_id"] == workflow["run_id"]


def test_agent_workflow_api_builds_markdown_report():
    workflow = run_agent_workflow(
        AgentWorkflowPayload(
            objective="Analyze sales workflow data for report",
            data_path="data/sample_sales_events.jsonl",
            limit=2,
        )
    )
    report = markdown_report(ReportPayload(workflow=workflow))

    assert "# AI Agent Workflow Report" in report
    assert "Agent Findings" in report


def test_agent_workflow_api_returns_clear_openai_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    try:
        run_agent_workflow(
            AgentWorkflowPayload(
                objective="Analyze sales workflow data with OpenAI",
                data_path="data/sample_sales_events.jsonl",
                limit=2,
                llm_provider="openai",
            )
        )
    except Exception as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected OpenAI mode to require OPENAI_API_KEY")
