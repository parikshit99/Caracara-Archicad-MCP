"""Naming elements by the Element ID the user sees in Archicad."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.tools.custom.element_sets import element_labels

WALL = "00000000-0000-0000-0000-000000000001"
GONE = "00000000-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_element_labels_name_existing_elements_and_mark_missing_ones(fake_archicad):
    fake_archicad.on_tapir_command(
        "GetDetailsOfElements",
        {
            "detailsOfElements": [
                {"id": "SW - 001"},
                {"error": {"code": -2130313115, "message": "Element does not exist."}},
            ]
        },
    )

    labels = await element_labels([WALL, GONE], fake_archicad.port)

    assert labels == {WALL: "SW - 001", GONE: None}
    command, params = fake_archicad.calls[0]
    assert command == "GetDetailsOfElements"
    assert params["fields"] == ["id"]


@pytest.mark.asyncio
async def test_element_labels_ask_nothing_for_no_elements(fake_archicad):
    assert await element_labels([], fake_archicad.port) == {}
    assert fake_archicad.calls == []


@pytest.mark.asyncio
async def test_element_labels_refuse_a_misaligned_answer(fake_archicad):
    fake_archicad.on_tapir_command("GetDetailsOfElements", {"detailsOfElements": [{"id": "SW - 001"}]})

    with pytest.raises(ToolError, match="different number of elements"):
        await element_labels([WALL, GONE], fake_archicad.port)
