from agent_workflow_assistant.models import StepStatus
from agent_workflow_assistant.workflows import run_workflow


def test_agent_workflow_runs_demo_mode():
    run = run_workflow(
        "Analyze sales workflow data and recommend operational next steps",
        context={"limit": 5},
    )

    assert run.status == StepStatus.COMPLETED
    assert len(run.steps) == 4
    assert run.agent_findings
    assert run.chart_data["quality"]
    assert all(step.explanation for step in run.steps)
    assert run.recommended_next_actions
    assert "Completed workflow" in run.final_summary


def test_agent_workflow_uses_data_path_context():
    run = run_workflow(
        "Profile a small event batch",
        context={"data_path": "data/sample_sales_events.jsonl", "limit": 2},
    )

    profile_step = run.steps[1]
    assert profile_step.result["event_count"] == 2


def test_agent_workflow_detects_common_csv_columns(tmp_path):
    csv_path = tmp_path / "events.csv"
    csv_path.write_text(
        "amount,qty,channel,segment,status\n100,2,email,software,ok\n-5,1,web,hardware,error\n",
        encoding="utf-8",
    )

    run = run_workflow(
        "Profile uploaded CSV evidence",
        context={"data_path": str(csv_path), "limit": 10},
    )

    profile = run.steps[1].result
    assert profile["detected_columns"]["amount"] == "amount"
    assert profile["detected_columns"]["channel"] == "channel"
    assert profile["anomaly_count"] == 1


def test_agent_workflow_accepts_pdf_evidence(tmp_path):
    from pypdf import PdfWriter

    pdf_path = tmp_path / "incident.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf_path.open("wb") as file:
        writer.write(file)

    run = run_workflow(
        "Analyze uploaded PDF evidence",
        context={"data_path": str(pdf_path), "limit": 5},
    )

    assert run.status == StepStatus.COMPLETED
    assert run.steps[0].result["source_path"].endswith("incident.pdf")
    assert run.steps[0].result["events"][0]["extraction_method"] in {"ocr", "ocr_unavailable"}
