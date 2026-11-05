"""Typed property cells from the official API's answers, and Tapir's display strings for what it cannot read."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp.property_values import (
    TAPIR_NOT_AVAILABLE_CODE,
    Definition,
    definitions,
    fill_from_tapir,
    official_cell,
    official_rows,
    tapir_cell,
)

LENGTH = Definition("length")
SHAPE = Definition("singleEnum", {"Straight": "gerade", "Curved": "gebogen"})


def value(**fields):
    return {"propertyValue": fields}


@pytest.mark.parametrize(
    ("entry", "definition", "cell"),
    [
        (value(type="length", status="normal", value=0.545), LENGTH, {"value": 0.545}),
        (value(type="length", status="userUndefined"), LENGTH, {"status": "userUndefined"}),
        (value(type="length", status="notAvailable"), LENGTH, {"status": "notAvailable"}),
        (value(type="length", status="notEvaluated"), LENGTH, {"status": "notEvaluated"}),
        (
            value(
                type="singleEnum", status="normal", value={"type": "nonLocalizedValue", "nonLocalizedValue": "Curved"}
            ),
            SHAPE,
            {"value": "gebogen"},
        ),
        (
            value(type="singleEnum", status="normal", value={"type": "displayValue", "displayValue": "EI 30"}),
            Definition("singleEnum"),
            {"value": "EI 30"},
        ),
        (
            value(
                type="multiEnum",
                status="normal",
                value=[{"enumValueId": {"type": "nonLocalizedValue", "nonLocalizedValue": "Straight"}}],
            ),
            SHAPE,
            {"value": ["gerade"]},
        ),
        # A name the definition does not list is still the value's own name.
        (
            value(type="singleEnum", status="normal", value={"type": "nonLocalizedValue", "nonLocalizedValue": "X"}),
            SHAPE,
            {"value": "X"},
        ),
    ],
)
def test_official_values_keep_their_type_and_status(entry, definition, cell):
    assert official_cell(entry, definition) == cell


@pytest.mark.parametrize(
    "entry", [{"error": {"code": 1, "message": "no"}}, value(type="length", status="normal"), None]
)
def test_an_official_value_it_could_not_read_is_left_for_tapir(entry):
    assert official_cell(entry, LENGTH) is None


@pytest.mark.parametrize(
    ("entry", "cell"),
    [
        ({"propertyValue": {"value": "0,55"}}, {"value": "0,55", "typed": False}),
        ({"propertyValue": {"value": "<Undefined>"}}, {"status": "userUndefined", "typed": False}),
        (
            {"error": {"code": TAPIR_NOT_AVAILABLE_CODE, "message": "Not available or not evaluated property"}},
            {"status": "notAvailable", "typed": False},
        ),
        ({"error": {"code": 1, "message": "broken"}}, {"error": "broken", "typed": False}),
        ({}, {"error": "Tapir gave neither a value nor an error", "typed": False}),
    ],
)
def test_tapir_values_are_display_strings(entry, cell):
    assert tapir_cell(entry) == cell


def test_definitions_give_types_and_enum_texts():
    answer = {
        "propertyDefinitions": [
            {"propertyDefinition": {"type": "length"}},
            {
                "propertyDefinition": {
                    "type": "singleEnum",
                    "possibleEnumValues": [
                        {
                            "enumValue": {
                                "enumValueId": {"type": "nonLocalizedValue", "nonLocalizedValue": "Straight"},
                                "displayValue": "gerade",
                            }
                        }
                    ],
                }
            },
        ]
    }
    found = definitions(answer, ["p1", "p2"])
    assert found == {"p1": Definition("length"), "p2": Definition("singleEnum", {"Straight": "gerade"})}
    assert found["p1"].unit() == "m"
    assert Definition("areaList").unit() == "m²"
    assert Definition("string").unit() is None


def test_an_unknown_property_is_refused():
    with pytest.raises(ToolError, match="Property p2 does not exist"):
        definitions({"propertyDefinitions": [{"propertyDefinition": {"type": "length"}}, {"error": {}}]}, ["p1", "p2"])


def test_an_answer_of_the_wrong_length_is_refused():
    with pytest.raises(ToolError, match="different number"):
        official_rows({"propertyValuesForElements": []}, ["e1"], ["p1"], {"p1": LENGTH})


def test_tapir_fills_only_the_cells_the_official_api_could_not_read():
    official = {
        "propertyValuesForElements": [
            {"propertyValues": [value(type="length", status="normal", value=3), {"error": {"code": 1}}]},
            {"error": {"code": 7203, "message": "Element not supported"}},
            {"error": {"code": 7204, "message": "Element not found"}},
        ]
    }
    rows = official_rows(official, ["wall", "panel", "gone"], ["p1", "p2"], {"p1": LENGTH, "p2": LENGTH})
    assert rows == {"wall": [{"value": 3}, None], "panel": [None, None], "gone": [None, None]}

    tapir = {
        "propertyValuesForElements": [
            {"propertyValues": [{"propertyValue": {"value": "3,00"}}, {"propertyValue": {"value": "0,20"}}]},
            {"propertyValues": [{"propertyValue": {"value": "1,20"}}, {"propertyValue": {"value": "<Undefined>"}}]},
            {"error": {"code": -2130313115, "message": "Failed to get property values for element"}},
        ]
    }
    unreadable = fill_from_tapir(rows, tapir, ["wall", "panel", "gone"])

    assert unreadable == ["gone"]
    assert rows == {
        "wall": [{"value": 3}, {"value": "0,20", "typed": False}],
        "panel": [{"value": "1,20", "typed": False}, {"status": "userUndefined", "typed": False}],
    }
