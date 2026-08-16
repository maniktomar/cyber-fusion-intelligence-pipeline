from __future__ import annotations

import argparse
import json

from agent_workflow_assistant.workflows import run_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI Agent Workflow Assistant.")
    parser.add_argument(
        "--objective",
        default="Analyze sales workflow data and recommend operational next steps",
    )
    parser.add_argument("--data-path", default="data/sample_sales_events.jsonl")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--llm-provider", choices=["demo", "openai"], default="demo")
    args = parser.parse_args()

    run = run_workflow(
        objective=args.objective,
        context={"data_path": args.data_path, "limit": args.limit},
        llm_provider=args.llm_provider,
    )
    print(json.dumps(run.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
