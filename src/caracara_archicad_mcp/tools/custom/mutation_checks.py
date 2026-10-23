"""Read-only checks of what archicad_mutate is about to do, run before anything changes.

Archicad skips elements it may not change (locked layer, or not reserved by this user in
Teamwork), and deletes the doors, windows, skylights and openings of a deleted host with it.
These checks find both in advance, so a dry run can report them and a delete that would
remove hosted elements can wait for the user's confirmation.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.element_sets import element_id
from caracara_archicad_mcp.tools.custom.functions import archicad_call_tool, is_running

# Element types Archicad deletes together with the element that hosts them.
HOSTED_ELEMENT_TYPES = ("Door", "Window", "Skylight", "Opening")
# Elements itemized in a report; the rest are only counted.
MAX_LISTED = 50


def _guids(elements: list[Any]) -> list[str]:
    return [elem_id["guid"] for elem in elements if (elem_id := element_id(elem)) is not None]


def _as_elements(element_ids: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"elementId": e} for e in element_ids]


async def not_editable(element_ids: list[dict[str, Any]], port: int) -> tuple[list[dict[str, Any]], str | None]:
    """The elements Archicad would refuse to change, and a warning when that could not be checked."""
    try:
        result = await archicad_call_tool(
            "elements_filter_elements",
            {"port": port, "params": {"elements": _as_elements(element_ids), "filters": ["IsEditable"]}},
        )
    except ToolError as error:
        return [], f"Locked elements were not checked: {error}"
    if is_running(result):
        return [], "The editability check is still running in Archicad, so locked elements were not checked."
    set_id = result.get("setId", "")
    if not ResultRegistry.has(set_id):
        return [], "Locked elements were not checked: Archicad gave no answer."
    editable = set(_guids(ResultRegistry.get(set_id)))
    ResultRegistry.delete(set_id)
    return [e for e in element_ids if e["guid"] not in editable], None


async def hosted_elements(element_ids: list[dict[str, Any]], port: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Doors, windows, skylights and openings outside the target that a delete would remove with their hosts,
    and the hosted types that could not be checked."""
    targets = {e["guid"] for e in element_ids}
    hosted: dict[str, str] = {}
    unchecked: list[str] = []
    for element_type in HOSTED_ELEMENT_TYPES:
        try:
            result = await archicad_call_tool(
                "elements_get_connected_elements",
                {"port": port, "params": {"elements": _as_elements(element_ids), "connectedElementType": element_type}},
            )
        except ToolError as error:
            unchecked.append(f"{element_type}: {error}")
            continue
        if is_running(result) or not isinstance(result.get("connectedElements"), list):
            unchecked.append(f"{element_type}: {result.get('error', {}).get('message', 'no answer from Archicad')}")
            continue
        for owner in result["connectedElements"]:
            for guid in _guids(owner.get("elements", []) if isinstance(owner, dict) else []):
                if guid not in targets:
                    hosted.setdefault(guid, element_type)
    return [{"elementId": {"guid": guid}, "type": kind} for guid, kind in hosted.items()], unchecked


def describe_hosted(hosted: list[dict[str, Any]]) -> str:
    """'2 doors, 1 window'."""
    counts: dict[str, int] = {}
    for item in hosted:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    return ", ".join(f"{count} {kind.lower()}{'s' if count != 1 else ''}" for kind, count in counts.items())


async def preflight(
    action: str,
    element_ids: list[dict[str, Any]],
    invalid: list[dict[str, Any]],
    port: int,
    *,
    changes_model: bool,
    deletes: bool,
) -> dict[str, Any]:
    """What a mutation would affect, without changing anything. Highlighting changes no model data."""
    report: dict[str, Any] = {"action": action, "elementCount": len(element_ids)}
    warnings: list[str] = []
    if invalid:
        report["invalidEntries"] = invalid[:MAX_LISTED]
        warnings.append(f"{len(invalid)} entries have no element GUID and will be skipped.")

    if element_ids and changes_model:
        locked, unchecked = await not_editable(element_ids, port)
        if unchecked:
            warnings.append(unchecked)
        if locked:
            report["notEditableCount"] = len(locked)
            report["notEditable"] = locked[:MAX_LISTED]
            warnings.append(
                f"{len(locked)} of {len(element_ids)} elements cannot be changed: they are on a locked layer, "
                "or, in a Teamwork project, not reserved by you. Archicad will skip them."
            )

    if element_ids and deletes:
        hosted, unchecked_types = await hosted_elements(element_ids, port)
        report["hostedElementCount"] = len(hosted)
        if hosted:
            report["hostedElements"] = hosted[:MAX_LISTED]
            warnings.append(f"Deleting these elements also deletes {describe_hosted(hosted)} placed in them.")
        if unchecked_types:
            report["uncheckedHostedTypes"] = unchecked_types

    report["warnings"] = warnings
    return report
