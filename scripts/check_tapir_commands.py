"""Compare the Tapir Add-On's command list with the Tapir commands this server wraps.

Our tools are generated from the command schemas that multiconn_archicad publishes, so a
command Tapir adds only reaches us after a multiconn release. This check reads Tapir's own
command reference and reports what we do not have yet:

- commands in Tapir's latest release that we neither wrap nor exclude on purpose,
- commands on Tapir's default branch that are not released yet,
- commands we call that Tapir no longer has,
- entries of commands_to_exclude that Tapir no longer has.

Writes a Markdown report and exits 0 when in sync, 1 when something needs attention and 2
when the check itself could not run (for example, Tapir moved or reformatted its files).

Usage:

    uv run python scripts/check_tapir_commands.py [--report report.md]
"""

import argparse
import ast
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from generator_config import ROOT_DIR, TAPIR_CONFIG

TAPIR_REPO_URL = "https://github.com/ENZYME-APD/tapir-archicad-automation"
TAPIR_DEFAULT_BRANCH = "master"
COMMAND_DEFINITIONS_URL = (
    "https://raw.githubusercontent.com/ENZYME-APD/tapir-archicad-automation/{ref}/docs/archicad-addon/"
    "command_definitions.js"
)
SOURCE_DIR = ROOT_DIR / "src" / "caracara_archicad_mcp"
TAPIR_CALL_METHODS = {"post_tapir_command", "post_tapir_command_async"}
# Add-on releases are tagged 1.7.2; tags such as ac-addon-0.1.3 belong to other parts of the repo.
RELEASE_TAG_RE = re.compile(r"\d+(?:\.\d+)+")

IN_SYNC, NEEDS_ATTENTION, CHECK_FAILED = 0, 1, 2


@dataclass
class Comparison:
    release: str
    new_in_release: list[str]
    new_on_branch: list[str]
    removed: list[str]
    stale_exclusions: list[str]

    @property
    def in_sync(self) -> bool:
        return not (self.new_in_release or self.new_on_branch or self.removed or self.stale_exclusions)


def latest_release_tag(ls_remote_output: str) -> str:
    """Return the highest numbered release tag from `git ls-remote --tags --refs` output."""
    tags = [line.rsplit("refs/tags/", 1)[-1] for line in ls_remote_output.splitlines() if "refs/tags/" in line]
    releases = [tag for tag in tags if RELEASE_TAG_RE.fullmatch(tag)]
    if not releases:
        raise ValueError("No release tags found in Tapir's repository.")
    return max(releases, key=lambda tag: tuple(int(part) for part in tag.split(".")))


def parse_command_definitions(text: str) -> set[str]:
    """Return the command names of Tapir's command_definitions.js (`var gCommands = [...];`)."""
    match = re.fullmatch(r"\s*var\s+gCommands\s*=\s*(.*?);?\s*", text, re.DOTALL)
    if not match:
        raise ValueError("command_definitions.js no longer starts with 'var gCommands ='.")
    groups = json.loads(match[1])
    names = {command["name"] for group in groups for command in group["commands"]}
    if not names:
        raise ValueError("command_definitions.js lists no commands.")
    return names


def wrapped_commands(source_dir: Path) -> set[str]:
    """Return every Tapir command name passed to post_tapir_command in the server's source."""
    names = set()
    for path in source_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) in TAPIR_CALL_METHODS):
                continue
            command = next((kw.value for kw in node.keywords if kw.arg == "command"), None)
            if command is None and node.args:
                command = node.args[0]
            if isinstance(command, ast.Constant) and isinstance(command.value, str):
                names.add(command.value)
    if not names:
        raise ValueError(f"Found no post_tapir_command calls under {source_dir}.")
    return names


def compare(release: str, released: set[str], on_branch: set[str], wrapped: set[str], excluded: set[str]) -> Comparison:
    known = wrapped | excluded
    in_tapir = released | on_branch
    return Comparison(
        release=release,
        new_in_release=sorted(released - known),
        new_on_branch=sorted(on_branch - released - known),
        removed=sorted(wrapped - in_tapir),
        stale_exclusions=sorted(excluded - in_tapir),
    )


def _bullets(names: list[str]) -> list[str]:
    return [f"- `{name}`" for name in names]


def render_report(result: Comparison, wrapped_count: int, excluded_count: int) -> str:
    release_url = f"{TAPIR_REPO_URL}/releases/tag/{result.release}"
    lines = [
        f"Compared with Tapir [{result.release}]({release_url}) and its `{TAPIR_DEFAULT_BRANCH}` branch. "
        f"We wrap {wrapped_count} Tapir commands and leave out {excluded_count} on purpose "
        "(`commands_to_exclude` in `scripts/generator_config.py`).",
        "",
    ]
    if result.in_sync:
        lines.append("Every Tapir command is either wrapped or left out on purpose. Nothing to do.")
        return "\n".join(lines) + "\n"

    if result.new_in_release:
        lines += [
            f"### New in Tapir {result.release} ({len(result.new_in_release)})",
            "",
            "Released commands we do not offer yet. They arrive once a `multiconn-archicad` release includes "
            "them: bump the pin in `pyproject.toml`, run `uv lock` and `uv run python scripts/generate_tools.py`. "
            "To leave one out instead, add it to `commands_to_exclude` with the reason.",
            "",
            *_bullets(result.new_in_release),
            "",
        ]
    if result.new_on_branch:
        lines += [
            f"### Coming in Tapir's next release ({len(result.new_on_branch)})",
            "",
            f"On Tapir's `{TAPIR_DEFAULT_BRANCH}` branch but not released yet. Nothing to do until they are released.",
            "",
            *_bullets(result.new_on_branch),
            "",
        ]
    if result.removed:
        lines += [
            f"### Removed from Tapir ({len(result.removed)})",
            "",
            "We still call these, but Tapir no longer lists them. Calls to them will fail once users update Tapir.",
            "",
            *_bullets(result.removed),
            "",
        ]
    if result.stale_exclusions:
        lines += [
            f"### Exclusions to clean up ({len(result.stale_exclusions)})",
            "",
            "Listed in `commands_to_exclude`, but Tapir no longer has them. They can be deleted from the list.",
            "",
            *_bullets(result.stale_exclusions),
            "",
        ]
    return "\n".join(lines)


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8")


def run() -> tuple[int, str]:
    try:
        ls_remote = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", TAPIR_REPO_URL],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
        release = latest_release_tag(ls_remote)
        released = parse_command_definitions(fetch_text(COMMAND_DEFINITIONS_URL.format(ref=release)))
        on_branch = parse_command_definitions(fetch_text(COMMAND_DEFINITIONS_URL.format(ref=TAPIR_DEFAULT_BRANCH)))
        wrapped = wrapped_commands(SOURCE_DIR)
    except Exception as error:  # noqa: BLE001 - any failure means the check needs a person to look at it
        return CHECK_FAILED, (
            "The check could not compare the command lists, so it needs a look: Tapir may have moved or "
            f"reformatted its command reference.\n\n```\n{type(error).__name__}: {error}\n```\n"
        )
    excluded = set(TAPIR_CONFIG.commands_to_exclude)
    result = compare(release, released, on_branch, wrapped, excluded)
    return (IN_SYNC if result.in_sync else NEEDS_ATTENTION), render_report(result, len(wrapped), len(excluded))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, help="Also write the Markdown report to this file.")
    args = parser.parse_args()
    status, report = run()
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    return status


if __name__ == "__main__":
    sys.exit(main())
