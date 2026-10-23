"""archicad_calculate: exact quantity calculations over a set of elements, written as read-only SQL.

The tool reads the elements' type, ID, story, layer and Archicad's own quantities (lengths,
areas, volumes, per building material), loads them into a throwaway in-memory database
(see caracara_archicad_mcp.calculation) and runs one SELECT. Only the query's result goes
back to the model, so a total over thousands of elements costs a few rows of context.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.calculation import CalculationError, Table, describe, run_query, snake_case, sqlite_type
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.functions import archicad_call_tool, archicad_get_job
from caracara_archicad_mcp.tools.custom.meta_verbs import _element_ids, _is_running

MAX_ELEMENTS = 100_000
# How long to keep waiting for Archicad to compute the data before giving up.
JOB_WAIT_LIMIT_SECONDS = 600.0
DETAIL_FIELDS = ["type", "id", "floorIndex", "layerIndex"]
UNITS = "Lengths are in meters, areas in square meters, volumes in cubic meters."

ELEMENT_COLUMNS = {
    "guid": "TEXT",
    "element_id": "TEXT",
    "type": "TEXT",
    "story_index": "INTEGER",
    "story": "TEXT",
    "layer": "TEXT",
}
COMPONENT_COLUMNS = {
    "guid": "TEXT",
    "type": "TEXT",
    "story_index": "INTEGER",
    "story": "TEXT",
    "layer": "TEXT",
    "material": "TEXT",
    "volume": "REAL",
    "projected_area": "REAL",
    "is_core": "INTEGER",
    "is_finish": "INTEGER",
}


async def _result(name: str, arguments: dict[str, Any]) -> Any:
    """Run a command, waiting for it to finish when Archicad takes longer than the async threshold."""
    result = await archicad_call_tool(name, arguments)
    deadline = time.monotonic() + JOB_WAIT_LIMIT_SECONDS
    while _is_running(result):
        if time.monotonic() > deadline:
            raise ToolError("Archicad is still computing the data; try again with fewer elements.")
        response = await archicad_get_job(result["jobHandle"])
        if response["status"] == "failed":
            raise ToolError(f"Archicad could not provide the data: {response.get('error')}")
        if response["status"] == "completed":
            result = response["result"]
    return result


async def _collect(name: str, arguments: dict[str, Any], field: str) -> list[Any]:
    """Every item of a paginated listing, in order."""
    result = await _result(name, arguments)
    if not isinstance(result, dict) or not isinstance(result.get(field), list):
        raise ToolError(f"Archicad returned no '{field}': {result}")
    items = list(result[field])
    token = result.get("next_page_token")
    while token:
        page = await archicad_call_tool(name, {**arguments, "page_token": token})
        items.extend(page.get(field, []))
        token = page.get("next_page_token")
    return items


async def _names(port: int, warnings: list[str]) -> tuple[dict[int, str], dict[int, str], dict[str, str]]:
    """Story names by index, layer names by index and building material names by GUID, as far as available."""
    stories: dict[int, str] = {}
    layers: dict[int, str] = {}
    materials: dict[str, str] = {}
    try:
        result = await _result("project_get_stories", {"port": port})
        if not isinstance(result, dict) or not isinstance(result.get("stories"), list):
            raise ToolError(f"Archicad returned no stories: {result}")
        stories = {int(story["index"]): story["name"] for story in result["stories"]}
    except (ToolError, KeyError, TypeError, ValueError) as error:
        warnings.append(f"Story names are missing: {error}")
    for attribute_type, names, key in (("Layer", layers, "index"), ("BuildingMaterial", materials, "guid")):
        try:
            for header in await _collect(
                "attributes_get_attributes_by_type",
                {"port": port, "params": {"attributeType": attribute_type}},
                "attributes",
            ):
                names[int(header["index"]) if key == "index" else header["attributeId"]["guid"]] = header["name"]
        except (ToolError, KeyError, TypeError, ValueError) as error:
            warnings.append(f"{attribute_type} names are missing: {error}")
    return stories, layers, materials


def _typed_columns(fixed: dict[str, str], rows: list[dict[str, Any]]) -> dict[str, str]:
    columns = dict(fixed)
    for row in rows:
        for name, value in row.items():
            if name not in columns and (kind := sqlite_type(value)) is not None:
                columns[name] = kind
    return columns


async def _load_tables(element_ids: list[dict[str, Any]], port: int, warnings: list[str]) -> list[Table]:
    elements = [{"elementId": elem_id} for elem_id in element_ids]
    details = await _collect(
        "elements_get_details_of_elements",
        {"port": port, "params": {"elements": elements, "fields": DETAIL_FIELDS}},
        "detailsOfElements",
    )
    quantities = await _collect(
        "elements_get_quantities_of_elements", {"port": port, "params": {"elements": elements}}, "quantitiesOfElements"
    )
    if len(details) != len(element_ids) or len(quantities) != len(element_ids):
        raise ToolError("Archicad returned data for a different number of elements than requested.")
    stories, layers, materials = await _names(port, warnings)

    element_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    for elem_id, detail, quantity in zip(element_ids, details, quantities, strict=True):
        detail = detail if isinstance(detail, dict) and "error" not in detail else {}
        quantity = quantity if isinstance(quantity, dict) and "error" not in quantity else {}
        story_index = int(detail["floorIndex"]) if isinstance(detail.get("floorIndex"), int | float) else None
        layer_index = int(detail["layerIndex"]) if isinstance(detail.get("layerIndex"), int | float) else None
        common = {
            "guid": elem_id["guid"],
            "type": detail.get("type") or quantity.get("type"),
            "story_index": story_index,
            "story": stories.get(story_index) if story_index is not None else None,
            "layer": layers.get(layer_index) if layer_index is not None else None,
        }
        row = {**common, "element_id": detail.get("id")}
        for name, value in (quantity.get("quantities") or {}).items():
            if isinstance(value, bool | int | float):
                row[snake_case(name)] = value
        element_rows.append(row)
        for component in quantity.get("composites") or []:
            material_guid = (component.get("buildingMaterialId") or {}).get("attributeId", {}).get("guid")
            component_rows.append(
                {
                    **common,
                    "material": materials.get(material_guid),
                    "volume": component.get("volume"),
                    "projected_area": component.get("projectedArea"),
                    "is_core": component.get("isCore"),
                    "is_finish": component.get("isFinish"),
                }
            )
    return [
        Table("elements", _typed_columns(ELEMENT_COLUMNS, element_rows), element_rows),
        Table("components", COMPONENT_COLUMNS, component_rows),
    ]


@mcp.tool(
    name="archicad_calculate",
    title="Calculate Quantities of Archicad Elements",
    description=(
        "Calculates exact totals over a set of elements with one read-only SQL SELECT, instead of adding "
        "numbers up yourself. target_set: a setId from archicad_query, or a list of elements. "
        "Tables: 'elements' (one row per element: guid, element_id, type, story_index, story, layer, and "
        "Archicad's quantities such as volume, gross_volume, length, area, height; columns depend on the "
        "element types) and 'components' (one row per building material skin: guid, type, story_index, story, "
        "layer, material, volume, projected_area, is_core, is_finish). "
        f"{UNITS} Call without sql to list the columns that exist for this set. "
        "Example: SELECT story, type, ROUND(SUM(volume), 2) AS m3 FROM elements GROUP BY story, type. "
        "The data stays on this computer in a temporary in-memory database; only the query result is returned."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_calculate(
    target_set: str | list[Any],
    sql: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    target_port = port or 19723
    if isinstance(target_set, str):
        elements = ResultRegistry.get_or_tool_error(target_set)
    elif isinstance(target_set, list):
        elements = target_set
    else:
        raise ToolError("Invalid target_set: must be a setId (str) or a list of elements.")
    element_ids, invalid = _element_ids(elements)
    if not element_ids:
        raise ToolError("The set contains no elements to calculate over.")
    if len(element_ids) > MAX_ELEMENTS:
        raise ToolError(f"The set has {len(element_ids)} elements; calculate over at most {MAX_ELEMENTS} at a time.")

    warnings: list[str] = []
    if invalid:
        warnings.append(f"{len(invalid)} entries have no element GUID and were left out.")
    tables = await _load_tables(element_ids, target_port, warnings)

    if sql is None:
        response: dict[str, Any] = {"tables": describe(tables), "units": UNITS}
    else:
        try:
            response = await asyncio.to_thread(run_query, tables, sql)
        except CalculationError as error:
            raise ToolError(f"{error} Available tables: {describe(tables)}") from None
    if warnings:
        response["warnings"] = warnings
    return response
