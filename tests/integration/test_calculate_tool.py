"""archicad_calculate end to end: Archicad data in, exact SQL answers out."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom.calculate import archicad_calculate

CONCRETE = "00000000-0000-0000-0000-00000000c0c0"
BRICK = "00000000-0000-0000-0000-00000000b0b0"


def _guid(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


@pytest.fixture(autouse=True)
def clean_registry():
    ResultRegistry.clear()
    yield
    ResultRegistry.clear()


@pytest.fixture
def model(fake_archicad):
    """A small model: elements by GUID with their type, story, layer, quantities and skins."""
    elements: dict[str, dict] = {}

    def details(params):
        return {
            "detailsOfElements": [
                {
                    "type": elements[item["elementId"]["guid"]]["type"],
                    "id": elements[item["elementId"]["guid"]]["id"],
                    "floorIndex": elements[item["elementId"]["guid"]]["story"],
                    "layerIndex": 1,
                }
                for item in params["elements"]
            ]
        }

    def quantities(params):
        out = []
        for item in params["elements"]:
            element = elements[item["elementId"]["guid"]]
            if element.get("quantities") is None:
                out.append({"error": {"code": 1, "message": "no quantities for this type"}})
                continue
            out.append(
                {
                    "type": element["type"],
                    "quantities": element["quantities"],
                    "composites": [
                        {
                            "buildingMaterialId": {"attributeId": {"guid": material}},
                            "volume": volume,
                            "projectedArea": 0.0,
                            "isCore": material == CONCRETE,
                            "isFinish": False,
                        }
                        for material, volume in element.get("skins", [])
                    ],
                }
            )
        return {"quantitiesOfElements": out}

    def attributes(params):
        if params["attributeType"] == "Layer":
            headers = [(1, _guid(901), "Structural")]
        else:
            headers = [(1, CONCRETE, "Concrete"), (2, BRICK, "Brick")]
        return {
            "attributes": [
                {"attributeId": {"guid": guid}, "index": index, "name": name, "modificationTime": 0}
                for index, guid, name in headers
            ]
        }

    fake_archicad.on_tapir_command("GetDetailsOfElements", details)
    fake_archicad.on_tapir_command("GetQuantitiesOfElements", quantities)
    fake_archicad.on_tapir_command("GetAttributesByType", attributes)
    fake_archicad.on_tapir_command(
        "GetStories",
        {
            "firstStory": 0,
            "lastStory": 1,
            "actStory": 0,
            "skipNullFloor": False,
            "stories": [
                {"index": 0, "floorId": 1, "dispOnSections": True, "level": 0.0, "height": 3.0, "name": "Ground"},
                {"index": 1, "floorId": 2, "dispOnSections": True, "level": 3.0, "height": 3.0, "name": "First"},
            ],
        },
    )
    return elements


def _add(model, i, element_type, story, quantities=None, skins=()):
    model[_guid(i)] = {"type": element_type, "id": f"{element_type[0]}-{i}", "story": story}
    if quantities is not None:
        model[_guid(i)] |= {"quantities": quantities, "skins": list(skins)}
    return {"elementId": {"guid": _guid(i)}}


@pytest.mark.asyncio
async def test_totals_per_story_and_material(fake_archicad, model):
    targets = [
        _add(model, 1, "Wall", 0, {"volume": 1.5, "grossVolume": 1.75}, [(CONCRETE, 1.0), (BRICK, 0.5)]),
        _add(model, 2, "Wall", 1, {"volume": 2.25, "grossVolume": 2.5}, [(BRICK, 2.25)]),
        _add(model, 3, "Slab", 0, {"volume": 3.0}, [(CONCRETE, 3.0)]),
        _add(model, 4, "Door", 0),
    ]
    set_id = ResultRegistry.register(targets, element_type="Element")

    by_story = await archicad_calculate(
        set_id, "SELECT story, SUM(volume) FROM elements GROUP BY story ORDER BY story", port=fake_archicad.port
    )
    by_material = await archicad_calculate(
        set_id,
        "SELECT material, SUM(volume) FROM components GROUP BY material ORDER BY material",
        port=fake_archicad.port,
    )
    doors = await archicad_calculate(
        set_id, "SELECT element_id, layer, volume FROM elements WHERE type = 'Door'", port=fake_archicad.port
    )

    assert by_story["rows"] == [["First", 2.25], ["Ground", 4.5]]
    assert by_material["rows"] == [["Brick", 2.75], ["Concrete", 4.0]]
    assert doors["rows"] == [["D-4", "Structural", None]]
    # Only reads reach Archicad.
    assert {command for command, _ in fake_archicad.calls} <= {
        "GetDetailsOfElements",
        "GetQuantitiesOfElements",
        "GetStories",
        "GetAttributesByType",
    }


@pytest.mark.asyncio
async def test_without_sql_lists_the_columns(fake_archicad, model):
    targets = [_add(model, 1, "Wall", 0, {"volume": 1.5, "grossVolume": 1.75, "length": 4.0})]

    result = await archicad_calculate(targets, port=fake_archicad.port)

    columns = result["tables"]["elements"]["columns"]
    assert {"guid", "element_id", "type", "story", "layer", "volume", "gross_volume", "length"} <= set(columns)
    assert columns["volume"] == "REAL"
    assert result["tables"]["components"]["rowCount"] == 0
    assert "cubic meters" in result["units"]


@pytest.mark.asyncio
async def test_large_sets_are_read_across_pages(fake_archicad, model):
    targets = [_add(model, i, "Column", i % 2, {"volume": 0.25}) for i in range(1, 251)]

    result = await archicad_calculate(targets, "SELECT COUNT(*), SUM(volume) FROM elements", port=fake_archicad.port)

    assert result["rows"] == [[250, 62.5]]


@pytest.mark.asyncio
async def test_bad_sql_explains_and_lists_the_tables(fake_archicad, model):
    targets = [_add(model, 1, "Wall", 0, {"volume": 1.5})]
    with pytest.raises(ToolError, match=r"no such column: weight.*Available tables"):
        await archicad_calculate(targets, "SELECT SUM(weight) FROM elements", port=fake_archicad.port)


@pytest.mark.asyncio
async def test_writes_are_refused(fake_archicad, model):
    targets = [_add(model, 1, "Wall", 0, {"volume": 1.5})]
    with pytest.raises(ToolError, match="read-only SELECT"):
        await archicad_calculate(targets, "DELETE FROM elements", port=fake_archicad.port)


@pytest.mark.asyncio
async def test_missing_names_leave_the_numbers_usable(fake_archicad, model):
    fake_archicad.on_tapir_command("GetStories", {"error": {"code": 1, "message": "unavailable"}})
    targets = [_add(model, 1, "Wall", 2, {"volume": 1.5})]

    result = await archicad_calculate(
        targets, "SELECT story_index, story, volume FROM elements", port=fake_archicad.port
    )

    assert result["rows"] == [[2, None, 1.5]]
    assert any("Story names are missing" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_empty_and_unknown_sets_are_refused(fake_archicad):
    with pytest.raises(ToolError, match="no elements"):
        await archicad_calculate([], "SELECT 1", port=fake_archicad.port)
    with pytest.raises(ToolError, match="not found"):
        await archicad_calculate("set_missing_01", "SELECT 1", port=fake_archicad.port)
