const form = document.querySelector("#workflow-form");
const runButton = document.querySelector("#run-button");
const statusPill = document.querySelector("#status-pill");
const runTitle = document.querySelector("#run-title");
const stepsEl = document.querySelector("#steps");
const stepTemplate = document.querySelector("#step-template");
const stepCount = document.querySelector("#step-count");
const summaryEl = document.querySelector("#summary");
const actionsEl = document.querySelector("#actions");
const findingsEl = document.querySelector("#findings");
const templateSelect = document.querySelector("#template-select");
const objectiveEl = document.querySelector("#objective");
const dataPathEl = document.querySelector("#data-path");
const fileUploadEl = document.querySelector("#file-upload");
const downloadJsonButton = document.querySelector("#download-json");
const downloadMarkdownButton = document.querySelector("#download-md");
const printPdfButton = document.querySelector("#print-pdf");
const demoModeInput = document.querySelector("input[name='llm-provider'][value='demo']");
const chartsEl = document.querySelector("#charts");
const explanationsEl = document.querySelector("#explanations");
const historyEl = document.querySelector("#run-history");

const dashboardEls = {
  total: document.querySelector("#dash-total"),
  completed: document.querySelector("#dash-completed"),
  failed: document.querySelector("#dash-failed"),
  upload: document.querySelector("#dash-upload"),
};

const metrics = {
  events: document.querySelector("#metric-events"),
  revenue: document.querySelector("#metric-revenue"),
  channel: document.querySelector("#metric-channel"),
  anomalies: document.querySelector("#metric-anomalies"),
};

let templates = [];
let latestRun = null;

const toolDescriptions = {
  load_jsonl_events: "Loads workflow evidence from the selected operational dataset.",
  profile_sales_events: "Profiles revenue, channels, categories, and possible anomalies.",
  review_risk_signals: "Reviews anomaly rate and assigns an operational risk level.",
  recommend_workflow_actions: "Turns analysis results into practical business next steps.",
};

function setStatus(status) {
  statusPill.className = `status-pill ${status}`;
  statusPill.textContent = status.charAt(0).toUpperCase() + status.slice(1);
}

function setExportState(enabled) {
  downloadJsonButton.disabled = !enabled;
  downloadMarkdownButton.disabled = !enabled;
  printPdfButton.disabled = !enabled;
}

function resetUi() {
  latestRun = null;
  setExportState(false);
  setStatus("running");
  runTitle.textContent = "Agent team is orchestrating the workflow";
  stepsEl.innerHTML = "";
  stepCount.textContent = "0/4 complete";
  summaryEl.textContent = "Planning steps, calling tools, reviewing risk, and preparing a summary...";
  actionsEl.innerHTML = "<li>Waiting for the agent team to finish the workflow.</li>";
  findingsEl.innerHTML = "<li>Agent findings will appear as the workflow completes.</li>";
  chartsEl.innerHTML = "<p class='empty-note'>Charts will appear after a workflow run.</p>";
  explanationsEl.innerHTML = "<p class='empty-note'>Each agent will explain its decision after the workflow completes.</p>";
  metrics.events.textContent = "--";
  metrics.revenue.textContent = "--";
  metrics.channel.textContent = "--";
  metrics.anomalies.textContent = "--";
}

function renderStep(step) {
  const node = stepTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add(step.status);
  node.querySelector("h4").textContent = step.name;
  node.querySelector("span").textContent = step.status;
  node.querySelector(".agent-label").textContent = step.agent_role;
  node.querySelector("p").textContent = toolDescriptions[step.tool_name] || "Runs a workflow tool.";
  node.querySelector("pre").textContent = JSON.stringify(step.result, null, 2);
  stepsEl.appendChild(node);
}

async function renderStepsWithTimeline(run, animate = true) {
  stepsEl.innerHTML = "";
  for (const step of run.steps) {
    if (animate) {
      renderStep({ ...step, status: "running", result: { message: "Running..." } });
      await new Promise((resolve) => setTimeout(resolve, 220));
      stepsEl.lastElementChild.remove();
    }
    renderStep(step);
    const completed = stepsEl.querySelectorAll(".step-card.completed").length;
    stepCount.textContent = `${completed}/${run.steps.length} complete`;
  }
}

function renderMetrics(run) {
  const profile = run.steps.find((step) => step.tool_name === "profile_sales_events")?.result || {};
  metrics.events.textContent = profile.event_count ?? "--";
  metrics.revenue.textContent = profile.total_revenue ? profile.total_revenue.toLocaleString() : "--";
  metrics.channel.textContent = profile.top_channel || "--";
  metrics.anomalies.textContent = profile.anomaly_count ?? "--";
}

function renderFindings(run) {
  findingsEl.innerHTML = "";
  run.agent_findings.forEach((finding) => {
    const item = document.createElement("li");
    item.textContent = `${finding.agent_role}: ${finding.finding}`;
    findingsEl.appendChild(item);
  });
}

function renderCharts(run) {
  chartsEl.innerHTML = "";
  const chartData = run.chart_data || {};
  renderBarChart("Revenue Signals By Channel", chartData.channels || {});
  renderBarChart("Events By Category", chartData.categories || {});
  renderBarChart("Data Quality", chartData.quality || {});
  const risk = chartData.risk || {};
  const riskBlock = document.createElement("div");
  riskBlock.className = "chart-block";
  const riskTitle = document.createElement("h4");
  riskTitle.textContent = "Risk Level";
  const riskText = document.createElement("p");
  riskText.className = "empty-note";
  riskText.textContent = `${risk.level || "unknown"} risk, ${risk.anomaly_rate_percent || 0}% anomaly rate`;
  riskBlock.append(riskTitle, riskText);
  chartsEl.appendChild(riskBlock);
}

function renderBarChart(title, data) {
  const block = document.createElement("div");
  block.className = "chart-block";
  const values = Object.entries(data);
  const max = Math.max(...values.map(([, value]) => Number(value) || 0), 1);
  const heading = document.createElement("h4");
  heading.textContent = title;
  block.appendChild(heading);
  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "empty-note";
    empty.textContent = "No chart data available.";
    block.appendChild(empty);
  }
  values.forEach(([label, value]) => {
    const width = Math.max(4, Math.round(((Number(value) || 0) / max) * 100));
    const row = document.createElement("div");
    row.className = "chart-row";
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.width = `${width}%`;
    track.appendChild(fill);
    const valueEl = document.createElement("strong");
    valueEl.textContent = value;
    row.append(labelEl, track, valueEl);
    block.appendChild(row);
  });
  chartsEl.appendChild(block);
}

function renderExplanations(run) {
  explanationsEl.innerHTML = "";
  run.steps.forEach((step) => {
    const explanation = step.explanation || {};
    const card = document.createElement("article");
    card.className = "explanation-card";
    const heading = document.createElement("h4");
    heading.textContent = step.agent_role;
    const list = document.createElement("dl");
    [
      ["Input Used", explanation.input_used],
      ["Decision", explanation.decision],
      ["Confidence", explanation.confidence],
      ["Why", explanation.why],
    ].forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = formatExplanationValue(value);
      list.append(term, detail);
    });
    card.append(heading, list);
    explanationsEl.appendChild(card);
  });
}

function formatExplanationValue(value) {
  if (value === undefined || value === null || value === "") return "--";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function renderRun(run, animate = true) {
  latestRun = run;
  await renderStepsWithTimeline(run, animate);
  setStatus(run.status);
  runTitle.textContent = run.status === "completed" ? "Workflow completed" : "Workflow needs attention";
  summaryEl.textContent = run.final_summary;
  renderMetrics(run);
  renderFindings(run);
  renderCharts(run);
  renderExplanations(run);

  actionsEl.innerHTML = "";
  run.recommended_next_actions.forEach((action) => {
    const item = document.createElement("li");
    item.textContent = action;
    actionsEl.appendChild(item);
  });
  setExportState(true);
}

async function refreshDashboard() {
  const response = await fetch("/api/dashboard");
  const dashboard = await response.json();
  dashboardEls.total.textContent = dashboard.total_runs;
  dashboardEls.completed.textContent = dashboard.completed_runs;
  dashboardEls.failed.textContent = dashboard.failed_runs;
  dashboardEls.upload.textContent = dashboard.last_uploaded_file || "--";
  renderHistory(dashboard.recent_runs || []);
}

function renderHistory(runs) {
  historyEl.innerHTML = "";
  if (!runs.length) {
    historyEl.innerHTML = "<button type='button' disabled>No saved runs yet</button>";
    return;
  }
  runs.forEach((run) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${run.template_id || "workflow"} - ${run.status}`;
    const meta = document.createElement("small");
    meta.textContent = `${new Date(run.created_at).toLocaleString()} | ${run.run_id}`;
    button.appendChild(meta);
    button.addEventListener("click", () => renderRun(run, false));
    historyEl.appendChild(button);
  });
}

function downloadText(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function uploadSelectedFile() {
  const file = fileUploadEl.files[0];
  if (!file) {
    return dataPathEl.value;
  }
  const isPdf = file.name.toLowerCase().endsWith(".pdf");
  const content = isPdf ? await fileToBase64(file) : await file.text();
  const response = await fetch("/api/uploads/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, content, encoding: isPdf ? "base64" : "text" }),
  });
  if (!response.ok) {
    const errorPayload = await response.json().catch(() => ({}));
    throw new Error(errorPayload.detail || "Upload failed. Use CSV, JSON, JSONL, or PDF.");
  }
  const payload = await response.json();
  dataPathEl.value = payload.data_path;
  return payload.data_path;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function loadTemplates() {
  const response = await fetch("/api/templates");
  templates = await response.json();
  templateSelect.innerHTML = "";
  templates.forEach((template) => {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = template.name;
    templateSelect.appendChild(option);
  });
}

templateSelect.addEventListener("change", () => {
  const template = templates.find((item) => item.id === templateSelect.value);
  if (template) {
    objectiveEl.value = template.objective;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetUi();
  runButton.disabled = true;

  try {
    const dataPath = await uploadSelectedFile();
    const payload = {
      objective: objectiveEl.value,
      data_path: dataPath,
      limit: Number(document.querySelector("#limit").value),
      template_id: templateSelect.value,
      llm_provider: document.querySelector("input[name='llm-provider']:checked").value,
    };

    const response = await fetch("/api/workflows/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      throw new Error(errorPayload.detail || `Request failed with status ${response.status}`);
    }
    await renderRun(await response.json());
    await refreshDashboard();
  } catch (error) {
    setStatus("failed");
    runTitle.textContent = "Workflow failed";
    summaryEl.textContent = error.message;
    actionsEl.innerHTML = "<li>Check the dataset path, upload format, or OpenAI API key and try again.</li>";
  } finally {
    runButton.disabled = false;
  }
});

downloadJsonButton.addEventListener("click", () => {
  if (!latestRun) return;
  downloadText("agent-workflow-report.json", JSON.stringify(latestRun, null, 2), "application/json");
});

downloadMarkdownButton.addEventListener("click", async () => {
  if (!latestRun) return;
  const response = await fetch("/api/reports/markdown", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workflow: latestRun }),
  });
  downloadText("agent-workflow-report.md", await response.text(), "text/markdown");
});

printPdfButton.addEventListener("click", () => {
  if (!latestRun) return;
  window.print();
});

setStatus("idle");
setExportState(false);
demoModeInput.checked = true;
loadTemplates();
refreshDashboard();
