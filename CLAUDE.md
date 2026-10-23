# Working on Caracara

Caracara is an MCP server that lets AI agents work with a running Archicad through the Tapir
add-on and Archicad's JSON API. It is a community project: many people with different
workflows and setups depend on it, and their models are often under client confidentiality.
Correctness, safety and privacy come before features.

Every change must follow the rules in [REVIEW.md](REVIEW.md). They are imported here:

@REVIEW.md

## Layout

| Path | What lives there |
| :--- | :--- |
| `src/caracara_archicad_mcp/server.py` | Command-line options and environment variables, startup. |
| `src/caracara_archicad_mcp/app.py`, `context.py` | The MCP server instance and its shared state. |
| `tools/generated/` | One wrapper per Archicad command, written by `scripts/generate_tools.py`. **Never edit by hand.** |
| `tools/overrides.py` | Hand-written behaviour layered over generated commands. |
| `tools/custom/functions.py` | `archicad_call_tool` and discovery. Every call to Archicad goes through `archicad_call_tool`. |
| `tools/custom/meta_verbs.py` | `archicad_query` and `archicad_mutate`. |
| `tools/custom/models.py` | Pydantic models of the custom tools. |
| `result_registry.py` | Element sets (`setId`) kept in memory instead of sending GUID lists to the model. |
| `field_masking.py`, `pagination.py`, `jobs.py` | Smaller responses, paging, long-running commands. |
| `file_paths.py` | Confines every file Archicad writes to one output directory. |
| `tests/unit`, `tests/integration` | Tests. `tests/conftest.py` has `FakeArchicad`, an in-memory Archicad. |
| `scripts/` | Code generation, release and maintenance scripts. |

## Commands

Run these before every commit; CI runs the same:

```bash
uv sync --locked
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
uv run --locked pytest tests --cov --cov-fail-under=85
```

If you change `manifest.json`: `npx --yes @anthropic-ai/mcpb@2.1.2 validate manifest.json`.
After changing the `multiconn-archicad` pin: `uv run python scripts/generate_tools.py`.

## How to work

1. **Understand before changing.** Read the module you are changing, its tests, and the
   code that calls it. Search for an existing helper before writing one.
2. **Agree on the plan for anything non-trivial.** A new tool, a new setting, a new
   dependency or a change to how data leaves the user's machine needs the maintainer's
   go-ahead first. Say what you intend and why, then wait.
3. **Make the smallest change that does the job.** No unrequested features, options,
   refactors or "improvements" alongside it.
4. **Test it.** Write the test that proves the change, see it fail without the change,
   then run the full checks above. Nothing is "done" while a check is red.
5. **Re-read your own diff** as a reviewer would, against REVIEW.md, and fix what you find.
6. **Report honestly.** Say what you verified, what you could not (there is no Archicad in
   CI, so live behaviour is unverified until the maintainer tests it), and any shortcut you
   took.

## Never

- Merge a pull request, push to `master`, rewrite published history or force-push.
- Edit `tools/generated/`, weaken or delete a test, or skip a check to get green.
- Add a dependency, turn on a data-sending feature by default, or widen what read-only mode
  allows, without the maintainer's explicit agreement.
- Put secrets, tokens, real project data or personal data in code, tests, logs or commits.
