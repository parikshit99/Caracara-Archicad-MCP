"""archicad_spatial_query: which elements are near, inside or colliding with which others.

'near' and 'inside' use the elements' 3D bounding boxes from Archicad and a spatial index
(caracara_archicad_mcp.spatial), so thousands of elements are compared in a fraction of a
second. Boxes approximate the shapes. 'collides' asks Archicad for an exact check of the real
geometry instead (GetCollisions). The matching elements come back as new element sets, ready
for archicad_query, archicad_calculate or archicad_mutate.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.spatial import Box, containers_of, pairs_within
from caracara_archicad_mcp.tools.custom.functions import archicad_call_tool, archicad_get_job
from caracara_archicad_mcp.tools.custom.meta_verbs import _element_ids, _is_running

MAX_ELEMENTS = 100_000
MAX_LISTED = 20
JOB_WAIT_LIMIT_SECONDS = 600.0


def _resolve(target: str | list[Any], label: str) -> list[dict[str, Any]]:
    if isinstance(target, str):
        elements = ResultRegistry.get_or_tool_error(target)
    elif isinstance(target, list):
        elements = target
    else:
        raise ToolError(f"{label} must be a setId (str) or a list of elements.")
    element_ids, _ = _element_ids(elements)
    if not element_ids:
        raise ToolError(f"{label} contains no elements.")
    if len(element_ids) > MAX_ELEMENTS:
        raise ToolError(f"{label} has {len(element_ids)} elements; use at most {MAX_ELEMENTS}.")
    return element_ids


async def _finished(name: str, arguments: dict[str, Any]) -> Any:
    """Run a read command, waiting for it when Archicad takes longer than the async threshold."""
    result = await archicad_call_tool(name, arguments)
    deadline = time.monotonic() + JOB_WAIT_LIMIT_SECONDS
    while _is_running(result):
        if time.monotonic() > deadline:
            raise ToolError("Archicad is still computing; try again with fewer elements.")
        response = await archicad_get_job(result["jobHandle"])
        if response["status"] == "failed":
            raise ToolError(f"Archicad could not answer: {response.get('error')}")
        if response["status"] == "completed":
            result = response["result"]
    return result


async def _boxes(element_ids: list[dict[str, Any]], port: int) -> tuple[list[Box], int]:
    """The 3D bounding boxes of the elements, and how many elements have none (2D-only elements)."""
    result = await _finished(
        "elements_get3_d_bounding_boxes",
        {"port": port, "params": {"elements": [{"elementId": e} for e in element_ids]}},
    )
    items = result.get("boundingBoxes3D") if isinstance(result, dict) else None
    if not isinstance(items, list) or len(items) != len(element_ids):
        raise ToolError(f"Archicad returned no bounding boxes: {result}")
    boxes: list[Box] = []
    for elem_id, item in zip(element_ids, items, strict=True):
        box = item.get("boundingBox3D") if isinstance(item, dict) else None
        if isinstance(box, dict):
            boxes.append(
                Box(elem_id["guid"], box["xMin"], box["yMin"], box["zMin"], box["xMax"], box["yMax"], box["zMax"])
            )
    return boxes, len(element_ids) - len(boxes)


def _new_set(guids: set[str] | list[str], label: str) -> str | None:
    return (
        ResultRegistry.register([{"elementId": {"guid": g}} for g in sorted(guids)], element_type=label)
        if guids
        else None
    )


async def _near(targets: list[dict], others: list[dict], distance: float, port: int) -> dict[str, Any]:
    target_boxes, target_missing = await _boxes(targets, port)
    other_boxes, other_missing = await _boxes(others, port)
    found = pairs_within(target_boxes, other_boxes, distance, keep=MAX_LISTED)
    response: dict[str, Any] = {
        "relation": "near",
        "distanceMeters": distance,
        "pairCount": found.pair_count,
        "matchedCount": len(found.matched),
        "matchedSetId": _new_set(found.matched, "Near"),
        "otherMatchedCount": len(found.other_matched),
        "otherMatchedSetId": _new_set(found.other_matched, "NearOther"),
        "nearest": [
            {"element": {"guid": a.key}, "other": {"guid": b.key}, "gapMeters": round(gap, 4)}
            for a, b, gap in found.nearest
        ],
        "note": "Measured between bounding boxes, which can be larger than diagonal or irregular elements.",
    }
    if target_missing or other_missing:
        response["withoutBoxCount"] = target_missing + other_missing
    return response


async def _inside(targets: list[dict], containers: list[dict], port: int) -> dict[str, Any]:
    target_boxes, target_missing = await _boxes(targets, port)
    container_boxes, container_missing = await _boxes(containers, port)
    holders = containers_of(target_boxes, container_boxes)
    per_container = Counter(holder for keys in holders.values() for holder in keys)
    response: dict[str, Any] = {
        "relation": "inside",
        "insideCount": len(holders),
        "insideSetId": _new_set(list(holders), "Inside"),
        "outsideCount": len(target_boxes) - len(holders),
        "byContainer": [
            {"container": {"guid": key}, "count": count} for key, count in per_container.most_common(MAX_LISTED)
        ],
        "note": "An element counts as inside when the center of its bounding box lies in the container's "
        "bounding box; for L-shaped or sloped containers check the result.",
    }
    if target_missing or container_missing:
        response["withoutBoxCount"] = target_missing + container_missing
    return response


async def _collides(targets: list[dict], others: list[dict], port: int) -> dict[str, Any]:
    result = await _finished(
        "elements_get_collisions",
        {
            "port": port,
            "params": {
                "elementsGroup1": [{"elementId": e} for e in targets],
                "elementsGroup2": [{"elementId": e} for e in others],
            },
        },
    )
    collisions = result.get("collisions") if isinstance(result, dict) else None
    if not isinstance(collisions, list):
        raise ToolError(f"Archicad returned no collision result: {result}")
    matched = {c["elementId1"]["guid"] for c in collisions}
    other_matched = {c["elementId2"]["guid"] for c in collisions}
    return {
        "relation": "collides",
        "collisionCount": len(collisions),
        "bodyCollisionCount": sum(1 for c in collisions if c.get("hasBodyCollision")),
        "clearanceCollisionCount": sum(1 for c in collisions if c.get("hasClearenceCollision")),
        "matchedCount": len(matched),
        "matchedSetId": _new_set(matched, "Colliding"),
        "otherMatchedCount": len(other_matched),
        "otherMatchedSetId": _new_set(other_matched, "CollidingOther"),
        "examples": [
            {
                "element": c["elementId1"],
                "other": c["elementId2"],
                "body": c.get("hasBodyCollision"),
                "clearance": c.get("hasClearenceCollision"),
            }
            for c in collisions[:MAX_LISTED]
        ],
    }


@mcp.tool(
    name="archicad_spatial_query",
    title="Find Elements Near, Inside or Colliding With Others",
    description=(
        "Relates two sets of elements in space. target_set and other_set: setIds from archicad_query, or lists of "
        "elements (the same set may be given twice). relation 'near': pairs whose bounding boxes are at most "
        "distance meters apart (0 = touching or overlapping), e.g. conduits within 0.5 m of walls. 'inside': "
        "target elements whose bounding-box center lies within an element of other_set, e.g. furniture in zones. "
        "'collides': Archicad's exact check of the real geometry, including MEP clearance zones. "
        "Returns counts, the nearest pairs or examples, and the matching elements as new setIds."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_spatial_query(
    relation: Literal["near", "inside", "collides"],
    target_set: str | list[Any],
    other_set: str | list[Any],
    distance: float = 0.0,
    port: int | None = None,
) -> dict[str, Any]:
    target_port = port or 19723
    if distance < 0:
        raise ToolError("distance must be zero or more meters.")
    targets = _resolve(target_set, "target_set")
    others = _resolve(other_set, "other_set")
    if relation == "near":
        return await _near(targets, others, distance, target_port)
    if relation == "inside":
        return await _inside(targets, others, target_port)
    if relation == "collides":
        return await _collides(targets, others, target_port)
    raise ToolError("relation must be 'near', 'inside' or 'collides'.")
