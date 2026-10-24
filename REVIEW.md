# Rules for every change

Each change, whoever or whatever wrote it, is reviewed against these rules. Cite them by
number in reviews ("breaks R4"). Rules marked **(checked)** are enforced by CI; the rest are
for the author and the reviewer.

## Scope

- **R1 One purpose per pull request.** No drive-by refactors, renames or reformatting of
  code the change does not need. A large feature is split into reviewable steps.
- **R2 Build only what was asked for.** No speculative options, flags, extension points or
  "while I was here" features.

## Code

- **R3 Reuse before writing.** Search first. A helper needed in a second module is moved to a
  shared module and imported from there, never copied.
- **R4 No private imports across modules (checked).** Names starting with `_` stay inside
  their module. If another module needs one, make it public in a shared place.
- **R5 Every call to Archicad goes through `archicad_call_tool`** (checked), which validates
  arguments, applies read-only mode and path confinement, and handles long-running jobs.
  Only `tools/generated/` and `tools/overrides.py` talk to the connection directly.
- **R6 `tools/generated/` is generator output (checked).** Change behaviour in
  `tools/overrides.py`, or change the generator.
- **R7 Keep functions small and plain (checked).** Cyclomatic complexity above 12 fails CI.
  The few existing exceptions are marked `# noqa: C901`; do not add new ones; split the
  function instead.
- **R8 No dead code (checked in part).** No commented-out code, no unreachable branches, no
  unused parameters, no `print` in `src/`, no TODO without a linked issue.
- **R9 Handle errors on purpose.** Catch the specific exceptions you expect. Never swallow
  one silently: report it to the model as a `ToolError` that says what to do next, or as a
  warning in the result.
- **R10 Comments explain why, not what.** No comments that narrate the change ("added",
  "new", "now"), restate the code, or address the reviewer. Match the comment density of the
  surrounding code.
- **R11 No new dependency without the maintainer's agreement.** Prefer the standard library;
  a dependency is a permanent maintenance and supply-chain cost for every user.
- **R12 Names use Archicad and domain terms** (element, story, layer, set). No `helper`,
  `utils`, `manager`, `data2`, `new_`, `tmp`.

## Safety and privacy

- **R13 Nothing leaves the user's machine unless a tool returns it.** No telemetry, no
  network calls other than to Archicad, no logging of model data (element lists, property
  values, images, project info) above DEBUG.
- **R14 New kinds of data are off by default.** Anything that sends something the server did
  not send before (pictures, location, client details) needs an explicit user setting or
  parameter, and its tool description says what is sent.
- **R15 Read-only mode stays closed.** A tool that only reads uses only `Get…`, `Is…` and
  `Filter…` commands. Widening what read-only mode allows needs the maintainer's agreement.
- **R16 Files are written only inside the output directory**, through `file_paths`.
- **R17 Tool annotations are true.** `readOnlyHint`, `destructiveHint` and `idempotentHint`
  match what the tool actually does.

## Tests

- **R18 Every behaviour change has a test that fails without it.** Test through the tool
  function with `FakeArchicad`; test pure algorithms directly.
- **R19 Never weaken a test to make a change pass.** No deleting, skipping, loosening
  assertions or raising tolerances without saying why in the pull request.
- **R20 Coverage stays at or above 85% (checked).**

## Documentation and history

- **R21 Docs move with the code.** A new or changed tool, option or setting updates the
  README tables and, for settings, `manifest.json`, in the same pull request.
- **R22 Commit messages say why**, not only what, and pull request descriptions state what
  was verified and what was not (live Archicad behaviour is never verified by CI).

## Upstream

- **R23 Stay close to upstream.** New behaviour goes in new modules; files that came from
  tapir-archicad-MCP change only where a hook is needed, and are never reformatted or tidied.
  Where upstream has the same feature, use upstream's. See "Upstream" in CLAUDE.md.
