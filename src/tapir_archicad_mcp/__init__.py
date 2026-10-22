"""Backwards-compatibility shim redirecting tapir_archicad_mcp to caracara_archicad_mcp."""

import caracara_archicad_mcp

# Share package search path so submodules (e.g. tapir_archicad_mcp.server) resolve directly
__path__ = list(caracara_archicad_mcp.__path__)

# Re-export all root-level attributes
from caracara_archicad_mcp import *  # noqa: F403
