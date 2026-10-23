"""The weekly Tapir command check must read Tapir's reference and our source correctly."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import get_args

import pytest
from multiconn_archicad.clients.core.literal_commands import TapirCommandType

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
# The script imports generator_config the way generate_tools.py does, from its own directory.
sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location("check_tapir_commands", SCRIPTS_DIR / "check_tapir_commands.py")
check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check
SPEC.loader.exec_module(check)


def definitions(*groups: list[str]) -> str:
    data = [{"name": f"Group {i}", "commands": [{"name": name} for name in names]} for i, names in enumerate(groups)]
    return f"var gCommands = {json.dumps(data, indent=4)};\n"


def test_parses_command_names_across_groups():
    assert check.parse_command_definitions(definitions(["A", "B"], ["C"])) == {"A", "B", "C"}


@pytest.mark.parametrize("text", ["[]", "var gCommands = [];", 'var gCommands = [{"name": "G", "commands": []}];'])
def test_unexpected_or_empty_reference_fails_loudly(text):
    with pytest.raises(ValueError):
        check.parse_command_definitions(text)


def test_latest_release_ignores_other_tags_and_sorts_numerically():
    output = "\n".join(f"abc123\trefs/tags/{tag}" for tag in ["1.7.2", "1.10.0", "1.9.9", "ac-addon-0.1.3", "nightly"])
    assert check.latest_release_tag(output) == "1.10.0"


def test_no_release_tags_fails():
    with pytest.raises(ValueError):
        check.latest_release_tag("abc123\trefs/tags/ac-addon-0.1.3")


def test_finds_keyword_and_positional_tapir_calls(tmp_path):
    (tmp_path / "tools.py").write_text(
        "def a(conn):\n"
        "    conn.core.post_tapir_command(command='GetAll', parameters={})\n"
        "    conn.core.post_tapir_command('Create')\n"
        "async def b(conn):\n"
        "    await conn.core.post_tapir_command_async(command='Delete')\n"
        "    conn.core.post_command(command='API.IsAlive')\n",
        encoding="utf-8",
    )
    assert check.wrapped_commands(tmp_path) == {"GetAll", "Create", "Delete"}


def test_finds_every_tapir_command_the_server_wraps():
    # Everything multiconn models is either generated into a tool or excluded on purpose.
    expected = set(get_args(TapirCommandType)) - set(check.TAPIR_CONFIG.commands_to_exclude)
    assert check.wrapped_commands(check.SOURCE_DIR) == expected


def test_compare_sorts_differences_into_their_sections():
    result = check.compare(
        "1.8.0",
        released={"Kept", "NewReleased", "Excluded"},
        on_branch={"Kept", "NewReleased", "Excluded", "Upcoming"},
        wrapped={"Kept", "Gone"},
        excluded={"Excluded", "OldExclusion"},
    )
    assert result.new_in_release == ["NewReleased"]
    assert result.new_on_branch == ["Upcoming"]
    assert result.removed == ["Gone"]
    assert result.stale_exclusions == ["OldExclusion"]
    report = check.render_report(result, wrapped_count=2, excluded_count=2)
    assert "### New in Tapir 1.8.0 (1)" in report
    assert "- `Upcoming`" in report
    assert "Nothing to do." not in report


def test_in_sync_report_says_nothing_to_do():
    result = check.compare("1.8.0", {"A", "X"}, {"A", "X"}, wrapped={"A"}, excluded={"X"})
    assert result.in_sync
    assert "Nothing to do." in check.render_report(result, 1, 1)


def test_network_failure_is_reported_as_a_failed_check(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(check.subprocess, "run", fail)
    status, report = check.run()
    assert status == check.CHECK_FAILED
    assert "network unreachable" in report
