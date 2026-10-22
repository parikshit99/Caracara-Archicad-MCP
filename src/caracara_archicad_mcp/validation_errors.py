"""Compact summaries of Pydantic validation failures for LLM-facing errors.

A batch of hundreds of elements with the same mistake would otherwise produce
hundreds of near-identical error lines. Errors are grouped by error type and
field path (list indices collapsed to '*') and reported once with a count.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

MAX_ERROR_GROUPS = 20
MAX_INPUT_CHARS = 80


def _field_path(loc: tuple[Any, ...]) -> str:
    return ".".join("*" if isinstance(part, int) else str(part) for part in loc) or "<root>"


def group_validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    """Group validation errors by (type, field path), most frequent first."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for detail in error.errors(include_url=False):
        key = (detail["type"], _field_path(detail["loc"]))
        group = groups.get(key)
        if group is None:
            group = groups[key] = {"error": key[0], "field": key[1], "message": detail["msg"], "occurrences": 1}
            # Echo the first offending value only when it is a short scalar.
            value = detail.get("input")
            if isinstance(value, (int, float, bool)) or (isinstance(value, str) and len(value) <= MAX_INPUT_CHARS):
                group["input"] = value
        else:
            group["occurrences"] += 1
    return sorted(groups.values(), key=lambda g: -g["occurrences"])


def summarize_validation_error(error: ValidationError) -> str:
    """Render grouped validation errors as a compact JSON string."""
    groups = group_validation_errors(error)
    summary: dict[str, Any] = {"errorCount": error.error_count(), "errors": groups[:MAX_ERROR_GROUPS]}
    if len(groups) > MAX_ERROR_GROUPS:
        summary["omittedGroups"] = len(groups) - MAX_ERROR_GROUPS
    return json.dumps(summary, separators=(",", ":"))
