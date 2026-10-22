"""Tests verifying backwards-compatibility of the tapir_archicad_mcp import shim."""


def test_root_import():
    import tapir_archicad_mcp

    assert hasattr(tapir_archicad_mcp, "__path__")


def test_submodule_imports():
    import tapir_archicad_mcp.server as tapir_server

    assert callable(tapir_server.main)


def test_app_and_jobs_imports():
    from tapir_archicad_mcp.app import mcp
    from tapir_archicad_mcp.jobs import Job

    assert mcp is not None
    assert Job is not None


def test_meta_verbs_import():
    from tapir_archicad_mcp.tools.custom.meta_verbs import archicad_mutate, archicad_query

    assert callable(archicad_query)
    assert callable(archicad_mutate)
