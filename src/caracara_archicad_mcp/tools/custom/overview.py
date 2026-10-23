"""archicad_project_overview: the project facts an agent needs before working, in one read-only call.

Instead of spending several tool calls on "which stories, layers and units are there?", the
agent asks once. Details that identify a person or a site (file path, geographic
coordinates, the Project Info fields such as client and address) are left out unless the
caller asks for them with include_private=true.
"""

from __future__ import annotations

import math
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from caracara_archicad_mcp.app import mcp
from caracara_archicad_mcp.tools.custom.functions import _build_ready_instance, _get_header, archicad_call_tool
from caracara_archicad_mcp.tools.custom.meta_verbs import _is_running

MAX_LISTED_LAYERS = 500


async def _read(name: str, arguments: dict[str, Any], field: str) -> Any:
    """The given field of a read command's result, following pages for list fields."""
    result = await archicad_call_tool(name, arguments)
    if _is_running(result):
        raise ToolError("Archicad is still computing it; ask again in a moment")
    if not isinstance(result, dict) or field not in result:
        raise ToolError(f"Archicad returned no '{field}'")
    value = result[field]
    token = result.get("next_page_token")
    while token and isinstance(value, list):
        page = await archicad_call_tool(name, {**arguments, "page_token": token})
        value = value + page.get(field, [])
        token = page.get("next_page_token")
    return value


async def _stories(port: int) -> list[dict[str, Any]]:
    stories = await _read("project_get_stories", {"port": port}, "stories")
    return [
        {key: story.get(key) for key in ("index", "name", "level", "height") if story.get(key) is not None}
        for story in stories
    ]


async def _units(port: int) -> dict[str, Any]:
    result = await archicad_call_tool("project_get_calculation_units", {"port": port})
    if not isinstance(result, dict) or "length" not in result:
        raise ToolError("Archicad returned no calculation units")
    return {kind: result[kind] for kind in ("length", "area", "volume", "angle") if kind in result}


async def _layers(port: int) -> dict[str, Any]:
    headers = await _read(
        "attributes_get_attributes_by_type", {"port": port, "params": {"attributeType": "Layer"}}, "attributes"
    )
    details = await _read(
        "attributes_get_layers",
        {
            "port": port,
            "params": {
                "attributeIds": [{"attributeId": header["attributeId"]} for header in headers],
                "fields": ["isHidden", "isLocked"],
            },
        },
        "layers",
    )
    layers = [layer for layer in details if isinstance(layer, dict) and "name" in layer]
    names = [layer["name"] for layer in layers]
    summary: dict[str, Any] = {
        "count": len(names),
        "names": names[:MAX_LISTED_LAYERS],
        "locked": [layer["name"] for layer in layers if layer.get("isLocked")],
        "hidden": [layer["name"] for layer in layers if layer.get("isHidden")],
    }
    if len(names) > MAX_LISTED_LAYERS:
        summary["truncated"] = True
    return summary


async def _classification_systems(port: int) -> list[dict[str, Any]]:
    systems = await _read("classifications_get_all_classification_systems", {"port": port}, "classificationSystems")
    return [{"name": system.get("name"), "version": system.get("version")} for system in systems]


async def _geo_location(port: int) -> dict[str, Any]:
    location = await _read("project_get_geo_location", {"port": port}, "projectLocation")
    return location if isinstance(location, dict) else {}


async def _project_info(port: int) -> dict[str, str]:
    fields = await _read("project_get_project_info_fields", {"port": port}, "fields")
    return {field["projectInfoName"]: field["projectInfoValue"] for field in fields if field.get("projectInfoValue")}


@mcp.tool(
    name="archicad_project_overview",
    title="Archicad Project Overview",
    description=(
        "Returns the facts needed before working on a project, in one call: Archicad and Tapir versions, "
        "project name and type, stories (index, name, level, height), calculation units, north direction, "
        "layers (names, and which are locked or hidden) and classification systems. "
        "include_private=true also returns the project's file path, its geographic location and the "
        "Project Info fields (client, address, ...); leave it off unless the task needs them."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def archicad_project_overview(port: int | None = None, include_private: bool = False) -> dict[str, Any]:
    target_port = port or 19723
    instance = _build_ready_instance(target_port, _get_header(target_port)).model_dump(by_alias=True)
    overview: dict[str, Any] = {
        "project": {
            "name": instance["projectName"],
            "type": instance["projectType"],
            "archicadVersion": instance["archicadVersion"],
            "tapirVersion": instance["tapirVersion"],
        }
    }
    if include_private and instance["projectPath"]:
        overview["project"]["path"] = instance["projectPath"]

    warnings: list[str] = []
    sections = {
        "stories": _stories,
        "units": _units,
        "layers": _layers,
        "classificationSystems": _classification_systems,
    }
    for section, read in sections.items():
        try:
            overview[section] = await read(target_port)
        except (ToolError, KeyError, TypeError) as error:
            warnings.append(f"{section} unavailable: {error}")

    try:
        location = await _geo_location(target_port)
        if isinstance(location.get("north"), int | float):
            overview["northDegrees"] = round(math.degrees(location["north"]), 2)
        if include_private:
            overview["location"] = {
                key: location[key] for key in ("latitude", "longitude", "altitude") if key in location
            }
    except (ToolError, KeyError, TypeError) as error:
        warnings.append(f"location unavailable: {error}")

    if include_private:
        try:
            overview["projectInfo"] = await _project_info(target_port)
        except (ToolError, KeyError, TypeError) as error:
            warnings.append(f"projectInfo unavailable: {error}")

    if warnings:
        overview["warnings"] = warnings
    return overview
