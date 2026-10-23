"""Polymorphic Meta-Verbs for Archicad MCP.

Collapses 80+ granular CRUD endpoints down into two composable MCP tools:
1. archicad_query(target, filter, output): Handles discovery, criteria search, and metadata extraction.
2. archicad_mutate(target_set, action, payload): Handles property updates, geometric transformations, and deletions.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.functions import archicad_call_tool

log = logging.getLogger(__name__)

# Element identifiers returned inline by output='ids'; larger sets stay behind their setId.
MAX_INLINE_ELEMENTS = 1000
# Failed elements itemized in a mutation response; the rest are only counted.
MAX_ITEMIZED_FAILURES = 50


def _normalize_element_type(target: str) -> str:
    """Normalize element type names to standard Archicad capitalization."""
    type_map = {
        "wall": "Wall",
        "walls": "Wall",
        "column": "Column",
        "columns": "Column",
        "slab": "Slab",
        "slabs": "Slab",
        "beam": "Beam",
        "beams": "Beam",
        "door": "Door",
        "doors": "Door",
        "window": "Window",
        "windows": "Window",
        "roof": "Roof",
        "roofs": "Roof",
        "mesh": "Mesh",
        "meshes": "Mesh",
        "zone": "Zone",
        "zones": "Zone",
        "morph": "Morph",
        "morphs": "Morph",
        "object": "Object",
        "objects": "Object",
        "stair": "Stair",
        "stairs": "Stair",
        "railing": "Railing",
        "railings": "Railing",
        "curtainwall": "CurtainWall",
        "curtainwalls": "CurtainWall",
        "curtain_wall": "CurtainWall",
        "curtain_walls": "CurtainWall",
        "hatch": "Hatch",
        "hatches": "Hatch",
        "line": "Line",
        "lines": "Line",
        "polyline": "Polyline",
        "polylines": "Polyline",
        "arc": "Arc",
        "arcs": "Arc",
        "circle": "Circle",
        "circles": "Circle",
        "text": "Text",
        "texts": "Text",
        "label": "Label",
        "labels": "Label",
        "dimension": "Dimension",
        "dimensions": "Dimension",
    }
    cleaned = target.strip().lower()
    return type_map.get(cleaned, target.strip())


def _is_running(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "running" and "jobHandle" in result


def _element_id(elem: Any) -> dict[str, Any] | None:
    """Return the {'guid': ...} identifier of a stored element, or None if it has none."""
    elem_id = elem.get("elementId", elem) if isinstance(elem, dict) else elem
    if isinstance(elem_id, dict):
        guid = elem_id.get("guid")
    else:
        guid = getattr(elem_id, "guid", elem_id if isinstance(elem_id, str) else None)
    if not isinstance(guid, str) or not guid.strip():
        return None
    return {"guid": guid}


def _element_ids(elements: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split elements into usable identifiers and itemized failures for malformed entries."""
    ids: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, elem in enumerate(elements):
        elem_id = _element_id(elem)
        if elem_id is None:
            invalid.append({"index": index, "error": {"message": "Entry has no element GUID; skipped."}})
        else:
            ids.append(elem_id)
    return ids, invalid


def _itemize(result: Any, element_ids: list[dict[str, Any]], invalid: list[dict[str, Any]]) -> Any:
    """Attach per-element failures to a batch result without raising.

    Elements deleted, moved or locked after their set was created come back from
    Tapir as failed entries of 'executionResults' (positionally matching the
    request); they are listed in 'failedElements' so the batch result stays usable.
    """
    if _is_running(result) or not isinstance(result, dict):
        return result
    failures = list(invalid)
    execution_results = result.get("executionResults")
    if isinstance(execution_results, list):
        aligned = len(execution_results) == len(element_ids)
        for index, item in enumerate(execution_results):
            if isinstance(item, dict) and item.get("success") is False:
                failure: dict[str, Any] = {"error": item.get("error", {})}
                if aligned:
                    failure["elementId"] = element_ids[index]
                else:
                    failure["index"] = index
                failures.append(failure)
    if not failures:
        return result
    itemized = dict(result)
    itemized["failedCount"] = len(failures)
    itemized["failedElements"] = failures[:MAX_ITEMIZED_FAILURES]
    return itemized


async def _all_pages(name: str, arguments: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """Call a paginated listing and follow next_page_token until every element is collected."""
    result = await archicad_call_tool(name, arguments)
    if _is_running(result):
        return result, []
    elements = list(result.get("elements", []))
    token = result.get("next_page_token")
    while token:
        page = await archicad_call_tool(name, {**arguments, "page_token": token})
        elements.extend(page.get("elements", []))
        token = page.get("next_page_token")
    return result, elements


def _vector(payload: dict[str, Any]) -> dict[str, float]:
    vec = payload.get("vector", payload)
    if not isinstance(vec, dict):
        raise ToolError("'vector' must be an object with numeric 'x', 'y' and 'z'.")
    try:
        return {axis: float(vec.get(axis, 0.0)) for axis in ("x", "y", "z")}
    except (TypeError, ValueError):
        raise ToolError("'vector' components 'x', 'y' and 'z' must be numbers.") from None


@mcp.tool(
    name="archicad_query",
    title="Query Archicad Elements & Metadata",
    description=(
        "Finds elements and reads their metadata. "
        "target: an element type ('Wall', 'Slab', 'Door', ...) or 'all', 'selected', 'classified', 'filter'. "
        "filter: optional criteria, e.g. {'filters': ['IsVisibleIn3D']} or {'classificationItemId': ...}. "
        "output: 'set' (default) returns an opaque setId and count; 'ids' returns element identifiers "
        f"(up to {MAX_INLINE_ELEMENTS}); a list of field names (e.g. ['id', 'storyIndex']) returns only those fields."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_query(  # noqa: C901 - predates REVIEW.md R7; split when next changed
    target: str,
    filter: dict[str, Any] | None = None,
    output: list[str] | str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    """Execute a polymorphic query in Archicad."""
    target_port = port or 19723
    target_lower = target.strip().lower()
    set_id: str | None = None
    raw_elements: list[dict[str, Any]] = []
    element_type: str = target

    # 1. Routing to appropriate discovery tool
    if target_lower in ("selected", "selection"):
        raw_res, raw_elements = await _all_pages("elements_get_selected_elements", {"port": target_port})
        element_type = "Selected"

    elif target_lower in ("all", "all_elements", "elements"):
        params: dict[str, Any] = {}
        if filter and "filters" in filter:
            params["filters"] = filter["filters"]
        raw_res, raw_elements = await _all_pages("elements_get_all_elements", {"port": target_port, "params": params})
        element_type = "Element"

    elif target_lower in ("classified", "classification") or (filter and "classificationItemId" in filter):
        if not filter or "classificationItemId" not in filter:
            raise ToolError("When target is 'classified', filter must include 'classificationItemId'.")
        raw_res = await archicad_call_tool(
            "elements_get_elements_by_classification",
            {"port": target_port, "params": {"classificationItemId": filter["classificationItemId"]}},
        )
        set_id = raw_res.get("setId")
        if set_id and ResultRegistry.has(set_id):
            raw_elements = ResultRegistry.get(set_id)
        else:
            raw_elements = raw_res.get("elements", [])
        element_type = "Classified"

    elif target_lower in ("filter", "filtered") or (filter and "criterias" in filter and "elements" in filter):
        if not filter:
            raise ToolError("When target is 'filter', filter dictionary must be provided.")
        raw_res = await archicad_call_tool("elements_filter_elements", {"port": target_port, "params": filter})
        set_id = raw_res.get("setId")
        if set_id and ResultRegistry.has(set_id):
            raw_elements = ResultRegistry.get(set_id)
        else:
            raw_elements = raw_res.get("elements", [])
        element_type = "Filtered"

    else:
        element_type = _normalize_element_type(target)
        params = {"elementType": element_type}
        if filter and "filters" in filter:
            params["filters"] = filter["filters"]
        raw_res = await archicad_call_tool("elements_get_elements_by_type", {"port": target_port, "params": params})
        set_id = raw_res.get("setId")
        if set_id and ResultRegistry.has(set_id):
            raw_elements = ResultRegistry.get(set_id)
        else:
            raw_elements = raw_res.get("elements", [])

    if _is_running(raw_res):
        # The command's own wrapper registers the set when the job completes.
        return raw_res

    # 2. Ensure stored in ResultRegistry
    if not set_id or not ResultRegistry.has(set_id):
        set_id = ResultRegistry.register(raw_elements, element_type=element_type)

    count = len(raw_elements)

    # 3. Output formatting & metadata projection
    if output in (None, "set", "count"):
        return {
            "setId": set_id,
            "count": count,
            "elementType": element_type,
        }

    if output in ("ids", "elements", "guid", "guids"):
        response: dict[str, Any] = {
            "setId": set_id,
            "count": count,
            "elementType": element_type,
            "elements": raw_elements[:MAX_INLINE_ELEMENTS],
        }
        if count > MAX_INLINE_ELEMENTS:
            response["truncated"] = True
        return response

    if isinstance(output, list) or output in ("details", "full"):
        # Metadata extraction with field projection
        fields = output if isinstance(output, list) else None
        details_params: dict[str, Any] = {"elements": raw_elements}
        if fields:
            details_params["fields"] = fields

        details_res = await archicad_call_tool(
            "elements_get_details_of_elements",
            {"port": target_port, "params": details_params},
        )
        response = {"setId": set_id, "count": count, "elementType": element_type}
        if _is_running(details_res):
            return {**response, **details_res}
        response["details"] = details_res.get("detailsOfElements", [])
        if details_res.get("next_page_token"):
            response["next_page_token"] = details_res["next_page_token"]
        return response

    raise ToolError(
        f"Unsupported output specification '{output}'. Supported: 'set', 'count', 'ids', or list of field names."
    )


@mcp.tool(
    name="archicad_mutate",
    title="Mutate Archicad Elements",
    description=(
        "Changes or deletes the elements of a set, as one Undo step per call. "
        "target_set: a setId from archicad_query, or a list of elements. "
        "action and payload: 'set_property' {'propertyId', 'propertyValue'} or {'propertyValues': [...]}; "
        "'move' {'vector': {'x','y','z'}}; 'rotate' {'rotation': {...}}; 'set_details' {'details': {...}}; "
        "'highlight' {'highlightedColors', 'wireframe3D'}; 'delete' {}. "
        "Elements that no longer exist or cannot be changed are listed in 'failedElements'."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def archicad_mutate(  # noqa: C901 - predates REVIEW.md R7; split when next changed
    target_set: str | list[Any],
    action: str,
    payload: dict[str, Any] | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    """Execute a polymorphic mutation on Archicad elements."""
    target_port = port or 19723

    # 1. Resolve elements from target_set
    if isinstance(target_set, str):
        elements = ResultRegistry.get_or_tool_error(target_set)
        set_id = target_set
    elif isinstance(target_set, list):
        elements = target_set
        set_id = ResultRegistry.register(elements)
    else:
        raise ToolError("Invalid target_set: must be a set_id handle (str) or a list of element dictionaries.")

    action_lower = action.strip().lower()
    element_ids, invalid = _element_ids(elements)

    # 2. Route based on action. Every action is one Tapir command, which Tapir runs
    # as a single undoable command in Archicad (one Undo step per batch).
    if action_lower in ("delete", "destroy", "remove"):
        result = await archicad_call_tool(
            "elements_delete_elements",
            {"port": target_port, "params": {"elements": [{"elementId": e} for e in element_ids]}},
        )
        return _itemize(result, element_ids, invalid)

    if action_lower in ("highlight", "select"):
        highlight_params: dict[str, Any] = {"elements": [{"elementId": e} for e in element_ids]}
        if payload:
            for k in ("highlightedColors", "wireframe3D", "nonHighlightedColor"):
                if k in payload:
                    highlight_params[k] = payload[k]
        result = await archicad_call_tool(
            "elements_highlight_elements",
            {"port": target_port, "params": highlight_params},
        )
        return _itemize(result, element_ids, invalid)

    if action_lower in ("set_property", "set_properties", "update_property", "update_properties", "property_update"):
        if not payload:
            raise ToolError("Payload must be provided for 'set_property' action.")
        prop_params = dict(payload)
        prop_params["targetSetId"] = set_id
        result = await archicad_call_tool(
            "properties_set_property_values_of_elements",
            {"port": target_port, "params": prop_params},
        )
        return _itemize(result, element_ids, invalid)

    if action_lower in ("move", "translate"):
        if not payload:
            raise ToolError("Payload must be provided for 'move' action (e.g. {'x': 1.0, 'y': 0.0, 'z': 0.0}).")
        move_vector = _vector(payload)
        copy_val = payload.get("copy", False)
        move_vectors = [{"elementId": elem_id, "moveVector": move_vector, "copy": copy_val} for elem_id in element_ids]
        result = await archicad_call_tool(
            "elements_move_elements",
            {"port": target_port, "params": {"elementsWithMoveVectors": move_vectors}},
        )
        return _itemize(result, element_ids, invalid)

    if action_lower in ("rotate",):
        if not payload:
            raise ToolError("Payload must be provided for 'rotate' action.")
        rot = payload.get("rotation", payload)
        copy_val = payload.get("copy", False)
        rotations = [{"elementId": elem_id, "rotation": rot, "copy": copy_val} for elem_id in element_ids]
        result = await archicad_call_tool(
            "elements_rotate_elements",
            {"port": target_port, "params": {"elementsWithRotations": rotations}},
        )
        return _itemize(result, element_ids, invalid)

    if action_lower in ("set_details", "update_details"):
        if not payload:
            raise ToolError("Payload must be provided for 'set_details' action.")
        details = payload.get("details", payload)
        if not isinstance(details, dict):
            raise ToolError("'details' must be an object.")
        items = [{"elementId": elem_id, "details": details} for elem_id in element_ids]
        result = await archicad_call_tool(
            "elements_set_details_of_elements",
            {"port": target_port, "params": {"elementsWithDetails": items}},
        )
        return _itemize(result, element_ids, invalid)

    raise ToolError(
        f"Unsupported action '{action}'. "
        "Supported actions: 'delete', 'highlight', 'set_property', 'move', 'rotate', 'set_details'."
    )
