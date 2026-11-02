"""properties_get_typed_property_values_of_elements through archicad_read_tool, against FakeArchicad."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp import settings
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.functions import archicad_read_tool

COMMAND = "properties_get_typed_property_values_of_elements"
WALL, PANEL = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"
THICKNESS, FIRE = "00000000-0000-0000-0000-0000000000f1", "00000000-0000-0000-0000-0000000000f2"
PROPERTIES = [{"propertyId": {"guid": THICKNESS}}, {"propertyId": {"guid": FIRE}}]


@pytest.fixture(autouse=True)
def archicad(fake_archicad):
    ResultRegistry.clear()
    fake_archicad.on_tapir_command(
        "API.GetDetailsOfProperties",
        {
            "propertyDefinitions": [
                {"propertyDefinition": {"type": "length"}},
                {"propertyDefinition": {"type": "string"}},
            ]
        },
    )
    fake_archicad.on_tapir_command(
        "API.GetPropertyValuesOfElements",
        {
            "propertyValuesForElements": [
                {
                    "propertyValues": [
                        {"propertyValue": {"type": "length", "status": "normal", "value": 0.3}},
                        {"propertyValue": {"type": "string", "status": "userUndefined"}},
                    ]
                },
                {"error": {"code": 7203, "message": "Element not supported"}},
            ]
        },
    )
    fake_archicad.on_tapir_command(
        "GetPropertyValuesOfElements",
        {"propertyValuesForElements": [{"propertyValues": [{"propertyValue": {"value": "0,05"}}] * 2}]},
    )
    yield fake_archicad
    ResultRegistry.clear()
    settings.set_read_only(None)


async def read(arguments: dict) -> dict:
    return (await archicad_read_tool(COMMAND, {"port": 19723, "params": arguments})).structured_content


@pytest.mark.asyncio
async def test_typed_values_with_tapir_only_for_what_the_official_api_cannot_read(archicad):
    set_id = ResultRegistry.register([{"elementId": {"guid": WALL}}, {"elementId": {"guid": PANEL}}], "Wall")

    result = await read({"targetSetId": set_id, "properties": PROPERTIES})

    assert result == {
        "properties": [
            {"propertyId": {"guid": THICKNESS}, "valueType": "length", "unit": "m"},
            {"propertyId": {"guid": FIRE}, "valueType": "string"},
        ],
        "elements": [
            {"elementId": {"guid": WALL}, "values": [{"value": 0.3}, {"status": "userUndefined"}]},
            {
                "elementId": {"guid": PANEL},
                "values": [{"value": "0,05", "typed": False}, {"value": "0,05", "typed": False}],
            },
        ],
    }
    tapir_reads = [params for command, params in archicad.calls if command == "GetPropertyValuesOfElements"]
    assert tapir_reads == [{"elements": [{"elementId": {"guid": PANEL}}], "properties": PROPERTIES}]
    official = dict(archicad.calls)["API.GetDetailsOfProperties"]
    assert official == {"properties": PROPERTIES}


@pytest.mark.asyncio
async def test_tapir_is_not_asked_when_the_official_api_read_everything(archicad):
    archicad.on_tapir_command(
        "API.GetDetailsOfProperties", {"propertyDefinitions": [{"propertyDefinition": {"type": "length"}}]}
    )
    archicad.on_tapir_command(
        "API.GetPropertyValuesOfElements",
        {
            "propertyValuesForElements": [
                {"propertyValues": [{"propertyValue": {"type": "length", "status": "notAvailable"}}]}
            ]
        },
    )

    result = await read({"elements": [{"elementId": {"guid": WALL}}], "properties": PROPERTIES[:1]})

    assert result["elements"] == [{"elementId": {"guid": WALL}, "values": [{"status": "notAvailable"}]}]
    assert "GetPropertyValuesOfElements" not in [command for command, _ in archicad.calls]


@pytest.mark.asyncio
async def test_it_works_in_read_only_mode(archicad):
    settings.set_read_only(True)
    result = await read(
        {"elements": [{"elementId": {"guid": WALL}}, {"elementId": {"guid": PANEL}}], "properties": PROPERTIES}
    )
    assert len(result["elements"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"elements": [], "properties": PROPERTIES}, "non-empty"),
        ({"elements": [{"guid": WALL}], "properties": PROPERTIES}, "elementId"),
        ({"elements": [{"elementId": {"guid": f"g{i}"}} for i in range(501)], "properties": PROPERTIES}, "at most 500"),
        ({"elements": [{"elementId": {"guid": WALL}}], "properties": []}, "properties"),
    ],
)
async def test_bad_requests_are_refused_before_reading(archicad, arguments, message):
    with pytest.raises(ToolError, match=message):
        await read(arguments)
    assert archicad.calls == []
