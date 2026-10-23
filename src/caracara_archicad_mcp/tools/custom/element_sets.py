"""Turning what a tool receives as 'target_set' into element identifiers.

Tools accept either a setId from archicad_query or a list of elements, and elements arrive
in several shapes ({'elementId': {'guid': ...}}, {'guid': ...}, a bare GUID string).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.result_registry import ResultRegistry


def element_id(elem: Any) -> dict[str, Any] | None:
    """Return the {'guid': ...} identifier of a stored element, or None if it has none."""
    elem_id = elem.get("elementId", elem) if isinstance(elem, dict) else elem
    if isinstance(elem_id, dict):
        guid = elem_id.get("guid")
    else:
        guid = getattr(elem_id, "guid", elem_id if isinstance(elem_id, str) else None)
    if not isinstance(guid, str) or not guid.strip():
        return None
    return {"guid": guid}


def element_ids(elements: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split elements into usable identifiers and itemized failures for malformed entries."""
    ids: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, elem in enumerate(elements):
        elem_id = element_id(elem)
        if elem_id is None:
            invalid.append({"index": index, "error": {"message": "Entry has no element GUID; skipped."}})
        else:
            ids.append(elem_id)
    return ids, invalid


def target_elements(target_set: str | list[Any], label: str = "target_set") -> list[Any]:
    """The elements a setId stands for, or the given list itself."""
    if isinstance(target_set, str):
        return ResultRegistry.get_or_tool_error(target_set)
    if isinstance(target_set, list):
        return target_set
    raise ToolError(f"Invalid {label}: must be a setId (str) or a list of elements.")
