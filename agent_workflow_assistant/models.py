from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowRequest(BaseModel):
    objective: str = Field(..., min_length=5)
    context: dict[str, Any] = Field(default_factory=dict)


class WorkflowStep(BaseModel):
    name: str
    agent_role: str
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    result: dict[str, Any] | None = None
    explanation: dict[str, Any] = Field(default_factory=dict)


class AgentFinding(BaseModel):
    agent_role: str
    finding: str


class WorkflowPlan(BaseModel):
    objective: str
    template_id: str = "sales_anomaly"
    steps: list[WorkflowStep]


class WorkflowRun(BaseModel):
    run_id: str
    created_at: str
    objective: str
    template_id: str = "sales_anomaly"
    llm_provider: str = "demo"
    status: StepStatus
    steps: list[WorkflowStep]
    agent_findings: list[AgentFinding] = Field(default_factory=list)
    chart_data: dict[str, Any] = Field(default_factory=dict)
    final_summary: str
    recommended_next_actions: list[str] = Field(default_factory=list)
