"""Structural rules from REVIEW.md that a linter cannot check, enforced on every change."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "caracara_archicad_mcp"
GENERATED = PACKAGE / "tools" / "generated"
# The only modules allowed to talk to the Archicad connection directly (REVIEW.md R5).
CONNECTION_MODULES = {PACKAGE / "tools" / "overrides.py"}
CONNECTION_METHODS = {"post_tapir_command", "post_command"}


def _modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(PACKAGE.rglob("*.py"))
        if not path.is_relative_to(GENERATED)
    ]


def _where(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(PACKAGE.parent)}:{node.lineno}"


def test_no_private_names_are_imported_across_modules():
    """REVIEW.md R4: names starting with '_' stay inside their module."""
    offenders = [
        f"{_where(path, node)} imports {alias.name} from {node.module}"
        for path, tree in _modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("caracara_archicad_mcp")
        for alias in node.names
        if alias.name.startswith("_") and not alias.name.startswith("__")
    ]
    assert not offenders, "Make these names public in a shared module instead:\n" + "\n".join(offenders)


def test_only_the_command_layer_talks_to_archicad_directly():
    """REVIEW.md R5: everything else goes through archicad_call_tool."""
    offenders = [
        f"{_where(path, node)} calls {node.func.attr}"
        for path, tree in _modules()
        if path not in CONNECTION_MODULES
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CONNECTION_METHODS
    ]
    assert not offenders, "Call archicad_call_tool instead:\n" + "\n".join(offenders)


def test_generated_wrappers_are_not_imported_by_hand_written_code():
    """REVIEW.md R6: hand-written code reaches generated wrappers through the tool registry."""
    offenders = [
        f"{_where(path, node)} imports {node.module}"
        for path, tree in _modules()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("caracara_archicad_mcp.tools.generated.")
    ]
    assert not offenders, "\n".join(offenders)
