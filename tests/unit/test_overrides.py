"""Hand-written command behaviour must live outside the generated modules."""

import importlib.util
import re
from pathlib import Path

import pytest

from caracara_archicad_mcp.tools import overrides
from caracara_archicad_mcp.tools.tool_registry import TOOL_CALLABLE_REGISTRY, override_tool_for_dispatch

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "src" / "caracara_archicad_mcp" / "tools" / "generated"

OVERRIDDEN = {
    "elements_get_elements_by_type": overrides.get_elements_by_type,
    "elements_filter_elements": overrides.filter_elements,
    "elements_get_elements_by_classification": overrides.get_elements_by_classification,
    "elements_delete_elements": overrides.delete_elements,
    "elements_highlight_elements": overrides.highlight_elements,
    "properties_set_property_values_of_elements": overrides.set_property_values_of_elements,
    "elements_get_details_of_elements": overrides.get_details_of_elements,
    "properties_get_details_of_properties": overrides.get_details_of_properties,
}


def test_generated_modules_only_import_the_registry_from_this_package():
    allowed = {"caracara_archicad_mcp.tools.tool_registry", "caracara_archicad_mcp.tools.generated"}
    offenders = []
    for path in GENERATED.rglob("*.py"):
        for module in re.findall(
            r"^from (caracara_archicad_mcp\S*) import", path.read_text(encoding="utf-8"), re.MULTILINE
        ):
            if not any(module == a or module.startswith(a + ".") for a in allowed):
                offenders.append(f"{path.relative_to(GENERATED)}: {module}")
    assert offenders == []


@pytest.mark.parametrize(("name", "func"), OVERRIDDEN.items())
def test_overrides_replace_generated_callables(name, func):
    # The tapir_archicad_mcp shim can import the package twice, so compare by origin, not identity.
    registered = TOOL_CALLABLE_REGISTRY[name].callable
    assert registered.__module__.endswith("tools.overrides")
    assert registered.__qualname__ == func.__qualname__


def test_override_keeps_generated_pagination():
    assert TOOL_CALLABLE_REGISTRY["elements_get_details_of_elements"].pagination_field == "detailsOfElements"


def test_overriding_an_unknown_command_fails_loudly():
    with pytest.raises(RuntimeError, match="no generated command"):
        override_tool_for_dispatch("elements_no_such_command", lambda conn_header: None)


def test_generator_writes_into_the_caracara_package():
    spec = importlib.util.spec_from_file_location(
        "generator_config_under_test", ROOT / "scripts" / "generator_config.py"
    )
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    for source in (config.TAPIR_CONFIG, config.OFFICIAL_CONFIG):
        assert source.output_dir.resolve().is_relative_to(GENERATED)
    assert "tapir_archicad_mcp" not in (ROOT / "scripts" / "generate_tools.py").read_text(encoding="utf-8")
