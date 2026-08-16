from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_HISTORY_PATH = PROJECT_ROOT / "data" / "agent_runs.json"


def load_runs() -> list[dict[str, Any]]:
    if not RUN_HISTORY_PATH.exists():
        return []
    with RUN_HISTORY_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_run(workflow: dict[str, Any]) -> None:
    RUN_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    runs = load_runs()
    runs.insert(0, workflow)
    with RUN_HISTORY_PATH.open("w", encoding="utf-8") as file:
        json.dump(runs[:50], file, indent=2)


def get_run(run_id: str) -> dict[str, Any] | None:
    return next((run for run in load_runs() if run.get("run_id") == run_id), None)


def build_dashboard() -> dict[str, Any]:
    runs = load_runs()
    completed = sum(1 for run in runs if run.get("status") == "completed")
    failed = sum(1 for run in runs if run.get("status") == "failed")
    last_run = runs[0] if runs else None
    last_upload = _last_upload_path(runs)
    return {
        "total_runs": len(runs),
        "completed_runs": completed,
        "failed_runs": failed,
        "last_uploaded_file": last_upload or "--",
        "latest_report": last_run.get("run_id") if last_run else "--",
        "recent_runs": runs[:8],
    }


def _last_upload_path(runs: list[dict[str, Any]]) -> str | None:
    for run in runs:
        first_step = next(
            (step for step in run.get("steps", []) if step.get("tool_name") == "load_jsonl_events"),
            None,
        )
        source_path = (first_step or {}).get("result", {}).get("source_path")
        if source_path and "uploads" in source_path:
            return source_path
    return None
