# AI Agent Workflow Assistant Demo Script

## 2-Minute Interview Walkthrough

1. Open the app at `http://127.0.0.1:8050`.

2. Start with the product framing:

   "This is an AI Agent Workflow Assistant that turns a business objective and
   uploaded evidence into an auditable multi-agent workflow. It is designed for
   operational teams that need structured recommendations, not just a chatbot."

3. Point out the dashboard:

   "The top cards and run history make this feel like an internal AI operations
   tool. Every run is saved locally, so I can reopen previous decisions and
   export reports."

4. Select a workflow template:

   "Templates let the same agentic architecture adapt to sales anomaly
   investigation, incident response, support triage, or project review."

5. Upload or use sample evidence:

   "The assistant accepts CSV, JSON, and JSONL files. It detects common column
   names like amount, revenue, channel, category, and anomaly flags so uploaded
   files do not need to match one rigid schema."

6. Run in Demo mode:

   "Demo mode is deterministic and reliable for interviews. OpenAI mode is
   available when an API key is configured."

7. Explain the agents:

   "The Planner Agent loads the evidence, the Data Analyst Agent profiles it,
   the Risk Reviewer Agent assigns risk, and the Action Recommender Agent
   produces stakeholder-ready next steps."

8. Show explainability and charts:

   "Each step exposes input used, decision, confidence, and reasoning. The
   charts give a quick visual read on channels, categories, risk, and data
   quality."

9. Export a report:

   "Finally, the run can be exported as JSON, Markdown, or printed to PDF. That
   makes the assistant useful beyond the browser session."

## Strong Resume Bullet

Built a multi-agent AI Workflow Assistant with FastAPI, Python, frontend
dashboarding, upload-based evidence analysis, run history, explainability,
charting, OpenAI-compatible summarization, and report export for operational
decision support.
