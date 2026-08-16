from __future__ import annotations

import os
from typing import Protocol


class LLMClient(Protocol):
    def summarize(self, objective: str, evidence: list[str]) -> str:
        ...


class DemoLLMClient:
    """Deterministic local summarizer used for demos and tests."""

    def summarize(self, objective: str, evidence: list[str]) -> str:
        useful_evidence = [item for item in evidence if item]
        if not useful_evidence:
            return f"Completed workflow for: {objective}"
        return (
            f"Completed workflow for: {objective}. "
            f"Key findings: {'; '.join(useful_evidence[:3])}."
        )


class OpenAILangChainClient:
    """Lazy LangChain/OpenAI adapter so local demos work without an API key."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for OpenAI mode.")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Install langchain-openai to use OpenAI mode: "
                "pip install langchain-openai"
            ) from exc

        self._chat = ChatOpenAI(model=model, temperature=0.2)

    def summarize(self, objective: str, evidence: list[str]) -> str:
        prompt = (
            "You are an AI workflow assistant. Summarize the completed workflow "
            "for a hiring-manager-friendly portfolio demo.\n\n"
            f"Objective: {objective}\n"
            f"Evidence:\n- " + "\n- ".join(evidence)
        )
        response = self._chat.invoke(prompt)
        return str(response.content)


def build_llm_client(provider: str = "demo") -> LLMClient:
    if provider == "openai":
        return OpenAILangChainClient()
    return DemoLLMClient()
