"""Property values as typed cells: the official API's answer first, Tapir's for what it cannot read.

The official GetPropertyValuesOfElements gives typed values in SI units with a status that keeps
"the user left it undefined", "not available for this element" and "not evaluated" apart. Tapir's
command gives display strings, and on Archicad 29 it answered "not available" for built-in values
of a wall that the official API read (recorded by DaviiiiiDave/tapir-archicad-MCP). The official
API rejects sub-elements and drafting elements (error 7203), which Tapir can read, so those cells
come from Tapir and are marked untyped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

# SI units of the official value types; the list types (lengthList, ...) use the same unit.
UNITS = {"length": "m", "area": "m²", "volume": "m³", "angle": "rad"}
# What Tapir shows for a value the user left undefined.
TAPIR_UNDEFINED = "<Undefined>"
# Tapir's "Not available or not evaluated property"; it cannot tell the two apart.
TAPIR_NOT_AVAILABLE_CODE = -2130312908
STATUSES = ("userUndefined", "notAvailable", "notEvaluated")


@dataclass(frozen=True)
class Definition:
    """What a property's definition says about its values: the official type and its enum display texts."""

    value_type: str
    enum_texts: dict[str, str] = field(default_factory=dict)  # nonLocalizedValue -> displayValue

    def unit(self) -> str | None:
        return UNITS.get(self.value_type.removesuffix("List"))


def _entries(answer: Any, key: str, count: int, source: str) -> list[Any]:
    entries = answer.get(key) if isinstance(answer, dict) else None
    if not isinstance(entries, list) or len(entries) != count:
        raise ToolError(f"{source} answered for a different number of items than asked; no values were returned.")
    return entries


def definitions(answer: Any, property_guids: list[str]) -> dict[str, Definition]:
    """The definitions from the official GetDetailsOfProperties answer, by property GUID."""
    found: dict[str, Definition] = {}
    entries = _entries(answer, "propertyDefinitions", len(property_guids), "Archicad")
    for guid, entry in zip(property_guids, entries, strict=True):
        definition = entry.get("propertyDefinition") if isinstance(entry, dict) else None
        if not isinstance(definition, dict) or "type" not in definition:
            raise ToolError(f"Property {guid} does not exist in this project. Find property IDs by name first.")
        found[guid] = Definition(definition["type"], _enum_texts(definition.get("possibleEnumValues")))
    return found


def _enum_texts(possible: Any) -> dict[str, str]:
    texts: dict[str, str] = {}
    for item in possible if isinstance(possible, list) else []:
        enum_value = item.get("enumValue") if isinstance(item, dict) else None
        enum_id = enum_value.get("enumValueId") if isinstance(enum_value, dict) else None
        if isinstance(enum_id, dict) and enum_id.get("nonLocalizedValue") and enum_value.get("displayValue"):
            texts[enum_id["nonLocalizedValue"]] = enum_value["displayValue"]
    return texts


def _enum_text(enum_id: Any, definition: Definition) -> Any:
    if not isinstance(enum_id, dict):
        return enum_id
    if enum_id.get("type") == "displayValue":
        return enum_id.get("displayValue")
    name = enum_id.get("nonLocalizedValue")
    return definition.enum_texts.get(name, name)


def official_cell(entry: Any, definition: Definition) -> dict[str, Any] | None:
    """One official value as a cell, or None when the official API could not read it."""
    value = entry.get("propertyValue") if isinstance(entry, dict) else None
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status in STATUSES:
        return {"status": status}
    if status != "normal" or "value" not in value:
        return None
    content = value["value"]
    if value.get("type") == "singleEnum":
        content = _enum_text(content, definition)
    elif value.get("type") == "multiEnum" and isinstance(content, list):
        content = [
            _enum_text(item.get("enumValueId") if isinstance(item, dict) else item, definition) for item in content
        ]
    return {"value": content}


def tapir_cell(entry: Any) -> dict[str, Any]:
    """One value from Tapir's GetPropertyValuesOfElements: a display string, not a typed value."""
    if isinstance(entry, dict) and isinstance(entry.get("error"), dict):
        error = entry["error"]
        if error.get("code") == TAPIR_NOT_AVAILABLE_CODE:
            return {"status": "notAvailable", "typed": False}
        return {"error": str(error.get("message", "Tapir could not read this value")), "typed": False}
    value = entry.get("propertyValue", {}).get("value") if isinstance(entry, dict) else None
    if value is None:
        return {"error": "Tapir gave neither a value nor an error", "typed": False}
    if value == TAPIR_UNDEFINED:
        return {"status": "userUndefined", "typed": False}
    return {"value": value, "typed": False}


def official_rows(
    answer: Any, element_guids: list[str], property_guids: list[str], found: dict[str, Definition]
) -> dict[str, list[dict[str, Any] | None]]:
    """The official cells of each element; None marks a cell that is to be read through Tapir."""
    rows: dict[str, list[dict[str, Any] | None]] = {}
    entries = _entries(answer, "propertyValuesForElements", len(element_guids), "Archicad")
    for guid, entry in zip(element_guids, entries, strict=True):
        values = entry.get("propertyValues") if isinstance(entry, dict) else None
        if not isinstance(values, list) or len(values) != len(property_guids):
            rows[guid] = [None] * len(property_guids)
            continue
        rows[guid] = [official_cell(value, found[g]) for value, g in zip(values, property_guids, strict=True)]
    return rows


def fill_from_tapir(rows: dict[str, list[dict[str, Any] | None]], answer: Any, element_guids: list[str]) -> list[str]:
    """Fill the cells the official API could not read with Tapir's. Elements neither could read are taken out
    of the rows and returned."""
    unreadable: list[str] = []
    entries = _entries(answer, "propertyValuesForElements", len(element_guids), "Tapir")
    for guid, entry in zip(element_guids, entries, strict=True):
        values = entry.get("propertyValues") if isinstance(entry, dict) else None
        row = rows[guid]
        if not isinstance(values, list) or len(values) != len(row):
            if all(cell is None for cell in row):
                unreadable.append(guid)
                del rows[guid]
                continue
            values = [{"error": {"message": "Tapir could not read this element"}}] * len(row)
        rows[guid] = [tapir_cell(value) if cell is None else cell for cell, value in zip(row, values, strict=True)]
    return unreadable
