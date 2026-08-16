from __future__ import annotations

import json
import csv
import re
from io import BytesIO
from collections import Counter
from pathlib import Path
from typing import Any, Callable


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


def load_jsonl_events(tool_input: dict[str, Any]) -> dict[str, Any]:
    path = Path(tool_input.get("path", "data/sample_sales_events.jsonl"))
    limit = int(tool_input.get("limit", 25))
    events: list[dict[str, Any]] = []

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                if len(events) >= limit:
                    break
                events.append(dict(row))
    elif path.suffix.lower() == ".pdf":
        events.extend(_load_pdf_events(path, limit))
    elif path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        rows = payload if isinstance(payload, list) else payload.get("events", [])
        events.extend(rows[:limit])
    else:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if len(events) >= limit:
                    break
                if not line.strip():
                    continue
                events.append(json.loads(line))

    return {"event_count": len(events), "source_path": str(path), "events": events}


def _load_pdf_events(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF upload requires pypdf. Run: pip install -r requirements.txt") from exc

    reader = PdfReader(str(path))
    chunks: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        if len(chunks) >= limit:
            break
        text = (page.extract_text() or "").strip()
        extraction_method = "pypdf_text"
        ocr_note = ""
        if not text:
            text, ocr_note = _extract_ocr_text_from_pdf_page(path, page_number - 1)
            extraction_method = "ocr" if text else "ocr_unavailable"
            text = text.strip()
        if not text:
            text = ocr_note or "No text could be extracted from this PDF page."
        amount = _extract_first_amount(text)
        chunks.append(
            {
                "document_page": page_number,
                "category": "pdf_evidence",
                "sales_channel": "document",
                "quantity": 1,
                "order_amount": amount,
                "is_anomaly": _text_has_risk_signal(text),
                "extraction_method": extraction_method,
                "ocr_note": ocr_note,
                "text_excerpt": text[:900],
            }
        )
    return chunks


def _extract_ocr_text_from_pdf_page(path: Path, page_index: int) -> tuple[str, str]:
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        return "", f"OCR dependencies are missing: {exc}. Run: pip install -r requirements.txt"

    try:
        document = fitz.open(str(path))
        page = document.load_page(page_index)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.open(BytesIO(pixmap.tobytes("png")))
        return pytesseract.image_to_string(image), ""
    except pytesseract.TesseractNotFoundError:
        return (
            "",
            "Tesseract OCR engine is not installed or not on PATH. Install Tesseract, then restart the app.",
        )
    except Exception as exc:
        return "", f"OCR failed for page {page_index + 1}: {exc}"


def _extract_first_amount(text: str) -> float:
    match = re.search(r"(?:[$£€]\s*)?(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+(?:\.\d{2})?)", text)
    return float(match.group(1).replace(",", "")) if match else 0


def _text_has_risk_signal(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in ["anomaly", "incident", "failed", "error", "risk", "urgent"])


def _as_float(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _first_present(event: dict[str, Any], candidates: list[str]) -> str | None:
    normalized = {_normalize_key(key): key for key in event}
    for candidate in candidates:
        key = normalized.get(_normalize_key(candidate))
        if key:
            return key
    return None


def _normalize_key(key: str) -> str:
    return key.lower().replace(" ", "_").replace("-", "_")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "anomaly", "failed", "error"}


def profile_sales_events(tool_input: dict[str, Any]) -> dict[str, Any]:
    events = tool_input.get("events", [])
    sample = events[0] if events else {}
    amount_key = _first_present(sample, ["order_amount", "amount", "revenue", "total", "price", "value"])
    quantity_key = _first_present(sample, ["quantity", "qty", "units", "count"])
    channel_key = _first_present(sample, ["sales_channel", "channel", "source", "platform"])
    category_key = _first_present(sample, ["category", "product_category", "type", "segment"])
    anomaly_key = _first_present(sample, ["is_anomaly", "anomaly", "is_error", "status", "risk_flag"])

    total_revenue = sum(_as_float(event.get(amount_key, 0)) for event in events)
    channels = Counter(event.get(channel_key, "unknown") for event in events)
    categories = Counter(event.get(category_key, "unknown") for event in events)
    anomalies = [
        event
        for event in events
        if _as_float(event.get(amount_key, 0)) <= 0
        or _as_int(event.get(quantity_key, 1), 1) <= 0
        or _truthy(event.get(anomaly_key))
    ]

    return {
        "event_count": len(events),
        "total_revenue": round(total_revenue, 2),
        "top_channel": channels.most_common(1)[0][0] if channels else "unknown",
        "anomaly_count": len(anomalies),
        "detected_columns": {
            "amount": amount_key,
            "quantity": quantity_key,
            "channel": channel_key,
            "category": category_key,
            "anomaly": anomaly_key,
        },
        "channel_breakdown": dict(channels.most_common(6)),
        "category_breakdown": dict(categories.most_common(6)),
    }


def review_risk_signals(tool_input: dict[str, Any]) -> dict[str, Any]:
    profile = tool_input.get("profile", {})
    anomaly_count = int(profile.get("anomaly_count", 0))
    event_count = int(profile.get("event_count", 0))
    anomaly_rate = round((anomaly_count / event_count) * 100, 2) if event_count else 0
    risk_level = "high" if anomaly_rate >= 10 else "medium" if anomaly_count else "low"

    return {
        "risk_level": risk_level,
        "anomaly_rate_percent": anomaly_rate,
        "review_note": (
            "Escalate before publishing KPIs."
            if risk_level == "high"
            else "Continue monitoring and keep human review available."
        ),
    }


def recommend_workflow_actions(tool_input: dict[str, Any]) -> dict[str, Any]:
    profile = tool_input.get("profile", {})
    risk = tool_input.get("risk", {})
    actions = [
        "Create a one-page operations summary for stakeholders.",
        "Route high-risk anomalies to a human reviewer before escalation.",
    ]
    if profile.get("anomaly_count", 0):
        actions.insert(0, "Investigate anomalous transactions before publishing KPIs.")
    if profile.get("event_count", 0) < 10:
        actions.append("Increase sample size before making production decisions.")
    if risk.get("risk_level") == "high":
        actions.insert(0, "Pause automated downstream reporting until risk review is complete.")

    return {"actions": actions}


TOOL_REGISTRY: dict[str, ToolFn] = {
    "load_jsonl_events": load_jsonl_events,
    "profile_sales_events": profile_sales_events,
    "review_risk_signals": review_risk_signals,
    "recommend_workflow_actions": recommend_workflow_actions,
}
