from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_workflow_assistant.reports import build_markdown_report
from agent_workflow_assistant.storage import build_dashboard, get_run, load_runs, save_run
from agent_workflow_assistant.templates import list_templates
from agent_workflow_assistant.workflows import run_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "agent_workflow_assistant" / "web"
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"

app = FastAPI(title="AI Agent Workflow Assistant")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class AgentWorkflowPayload(BaseModel):
    objective: str = Field(..., min_length=5)
    data_path: str = "data/sample_sales_events.jsonl"
    limit: int = Field(default=25, ge=1, le=500)
    template_id: str = "sales_anomaly"
    llm_provider: str = Field(default="demo", pattern="^(demo|openai)$")


class UploadPayload(BaseModel):
    filename: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    encoding: str = "text"


class ReportPayload(BaseModel):
    workflow: dict[str, Any]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/templates")
def templates() -> list[dict[str, str]]:
    return list_templates()


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return build_dashboard()


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    return load_runs()


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    workflow = get_run(run_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Run not found.")
    return workflow


@app.post("/api/uploads/events")
def upload_events(payload: UploadPayload) -> dict[str, str]:
    suffix = Path(payload.filename).suffix.lower()
    if suffix not in {".csv", ".json", ".jsonl", ".ndjson", ".pdf"}:
        raise HTTPException(status_code=400, detail="Upload a CSV, JSON, JSONL, or PDF file.")

    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(payload.filename).name)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_DIR / safe_name
    if payload.encoding == "base64":
        import base64

        destination.write_bytes(base64.b64decode(payload.content))
    else:
        destination.write_text(payload.content, encoding="utf-8")
    return {"data_path": str(destination.relative_to(PROJECT_ROOT)).replace("\\", "/")}


@app.post("/api/workflows/run")
def run_agent_workflow(payload: AgentWorkflowPayload) -> dict:
    try:
        workflow = run_workflow(
            objective=payload.objective,
            context={
                "data_path": payload.data_path,
                "limit": payload.limit,
                "template_id": payload.template_id,
            },
            llm_provider=payload.llm_provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"Dataset not found: {payload.data_path}") from exc
    workflow_payload = workflow.model_dump(mode="json")
    save_run(workflow_payload)
    return workflow_payload


@app.post("/api/reports/markdown", response_class=PlainTextResponse)
def markdown_report(payload: ReportPayload) -> str:
    return build_markdown_report(payload.workflow)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("AGENT_API_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_API_PORT", "8050")),
    )
