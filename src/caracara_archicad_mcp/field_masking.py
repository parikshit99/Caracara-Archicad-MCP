"""Keeping only the requested fields of element details.

Element details carry polygons and other bulky data the model rarely needs, so reads
can name the fields to return. Names are matched exactly (or as dot paths) rather
than guessed; requested names that no element has are reported back.
"""

from __future__ import annotations

from typing import Any

# Archicad's element details call a story a floor; agents ask for either.
STORY_ALIASES = {"storyIndex": "floorIndex", "floorIndex": "storyIndex"}


def _lookup(item: dict[str, Any], field: str) -> tuple[bool, Any]:
    """The value at a field name or dot path ('details.thickness'), and whether it exists."""
    current: Any = item
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _place(projected: dict[str, Any], field: str, value: Any) -> None:
    """Store a value under its field name, nesting dot paths as in the source."""
    *parents, last = field.split(".")
    for part in parents:
        projected = projected.setdefault(part, {})
    projected[last] = value


def project_element(item: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Keep only the requested fields of an element: exact names, dot paths, and storyIndex/floorIndex."""
    if not fields:
        return item

    projected: dict[str, Any] = {}
    for field in fields:
        found, value = _lookup(item, field)
        if not found and field in STORY_ALIASES:
            found, value = _lookup(item, STORY_ALIASES[field])
        if found:
            _place(projected, field, value)

    # Per-element failures (e.g. an element deleted after its set was created)
    # must survive projection, or the caller cannot tell a ghost from a match.
    if "error" in item and "error" not in projected:
        projected["error"] = item["error"]

    return projected


def _unknown_fields(elements: list[Any], fields: list[str]) -> list[str]:
    """Requested fields that no element has, so the caller can correct the name instead of guessing."""
    found = {field for elem in elements if isinstance(elem, dict) for field in fields if _lookup(elem, field)[0]}
    return [field for field in fields if field not in found]


def prune_payload(
    payload: Any,
    fields: list[str] | None,
    list_keys: tuple[str, ...] = (
        "detailsOfElements",
        "elements",
        "properties",
        "classificationItems",
        "geometries3D",
    ),
) -> Any:
    """Prune an Archicad command payload according to the requested fields.

    Works on responses containing lists of element details (e.g. 'detailsOfElements'),
    preserving top-level metadata like pagination tokens.
    """
    if not fields:
        return payload

    if isinstance(payload, dict):
        result = dict(payload)
        found_target_list = False
        for key in list_keys:
            if key in payload and isinstance(payload[key], list):
                found_target_list = True
                result[key] = [
                    project_element(elem, fields) if isinstance(elem, dict) else elem for elem in payload[key]
                ]
                if unknown := _unknown_fields(result[key], fields):
                    result["unknownFields"] = unknown

        if not found_target_list:
            # If no known list key is present, check if payload itself is a single element dictionary
            return project_element(payload, fields)

        return result

    if isinstance(payload, list):
        return [project_element(elem, fields) if isinstance(elem, dict) else elem for elem in payload]

    return payload
