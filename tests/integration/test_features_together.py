"""How the features behave together: read-only mode with calculations, the overview and pictures."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from caracara_archicad_mcp import file_paths, safety
from caracara_archicad_mcp.result_registry import ResultRegistry
from caracara_archicad_mcp.tools.custom import capture
from caracara_archicad_mcp.tools.custom.calculate import archicad_calculate
from caracara_archicad_mcp.tools.custom.capture import archicad_capture_view
from caracara_archicad_mcp.tools.custom.overview import archicad_project_overview

GUID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def read_only(tmp_path):
    safety.set_read_only(True)
    capture.set_images_allowed(True)
    file_paths.set_output_root(tmp_path)
    ResultRegistry.clear()
    yield
    safety.set_read_only(None)
    capture.set_images_allowed(None)
    file_paths.set_output_root(None)
    ResultRegistry.clear()


@pytest.mark.asyncio
async def test_calculations_work_in_read_only_mode(fake_archicad):
    fake_archicad.on_tapir_command(
        "GetDetailsOfElements",
        {"detailsOfElements": [{"type": "Wall", "id": "W-1", "floorIndex": 0, "layerIndex": 1}]},
    )
    fake_archicad.on_tapir_command(
        "GetQuantitiesOfElements",
        {"quantitiesOfElements": [{"type": "Wall", "quantities": {"volume": 2.5}, "composites": []}]},
    )
    fake_archicad.on_tapir_command("GetStories", {"stories": []})
    fake_archicad.on_tapir_command("GetAttributesByType", {"attributes": []})

    result = await archicad_calculate([{"elementId": {"guid": GUID}}], "SELECT SUM(volume) FROM elements")

    assert result["rows"] == [[2.5]]


@pytest.mark.asyncio
async def test_overview_works_in_read_only_mode(fake_archicad):
    fake_archicad.tapir_info = SimpleNamespace(version="1.7.2", is_older=False, is_newer=False)
    for command in ("GetStories", "GetCalculationUnits", "GetAttributesByType", "GetLayers"):
        fake_archicad.on_tapir_command(command, {})
    fake_archicad.on_tapir_command("API.GetAllClassificationSystems", {"classificationSystems": []})
    fake_archicad.on_tapir_command("GetGeoLocation", {"projectLocation": {"north": 0.0}})

    overview = await archicad_project_overview(port=fake_archicad.port)

    assert overview["project"]["name"] == "FakeProject"
    assert not any("read-only" in warning for warning in overview.get("warnings", []))


@pytest.mark.asyncio
async def test_element_previews_work_in_read_only_mode(fake_archicad):
    fake_archicad.on_tapir_command("GetElementPreviewImage", {"previewImage": base64.b64encode(b"jpeg").decode()})
    image = await archicad_capture_view(element={"guid": GUID})
    assert base64.b64decode(image.to_image_content().data) == b"jpeg"


@pytest.mark.asyncio
async def test_window_captures_are_blocked_in_read_only_mode(fake_archicad):
    """Saving the window writes a file, which read-only mode does not allow."""
    with pytest.raises(ToolError, match="read-only mode"):
        await archicad_capture_view()
    assert fake_archicad.calls == []
