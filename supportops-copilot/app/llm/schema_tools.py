"""Turn a Pydantic model into a schema the structured-outputs API will accept.

The API requires `additionalProperties: false` on every object and does not
support a number of JSON Schema keywords Pydantic emits by default. Rather than
hand-maintaining a parallel dict per model, we derive it and strip.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Keywords the structured-outputs API rejects. Pydantic emits several of these
# from Field(ge=..., le=..., max_length=...), which we still want for local
# validation -- so they are enforced client-side and removed from the wire schema.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "default",
    }
)


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for `model`, tightened for the structured-outputs API."""
    return _tighten(model.model_json_schema())


def _tighten(node: Any) -> Any:
    if isinstance(node, list):
        return [_tighten(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_KEYWORDS:
            continue
        out[key] = _tighten(value)

    if out.get("type") == "object":
        out["additionalProperties"] = False
        # Every property must be required: the API has no notion of an optional
        # key, and a model that omits a field would fail our own validation
        # anyway. Defaults are applied client-side after parsing.
        properties = out.get("properties")
        if isinstance(properties, dict):
            out["required"] = list(properties)
    return out
