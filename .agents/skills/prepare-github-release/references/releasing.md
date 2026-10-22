# Release procedure

## Automatic releases

-   **Monthly stable release** (`.github/workflows/monthly-release.yml`): on the
    first Friday of each month at 04:30 UTC, if `master` has commits since the
    last `v*` tag, CI runs every gate, bumps the version to `YEAR.MONTH.PATCH`
    (for example `2026.11.0`; PATCH counts releases already made that month),
    commits and tags it on `master` as `github-actions[bot]`, publishes the
    GitHub release with generated notes and all files, and uploads to PyPI.
    The push is atomic and is rejected if `master` moved after the gates ran.
    Run it manually with `dry_run` (the default) to rehearse without pushing,
    or with `force` to release outside the schedule.
-   **Weekly pre-release** (`.github/workflows/weekly-build.yml`): every Friday,
    if `master` changed, the rolling `weekly` pre-release is replaced with a
    fresh build. It is never published to PyPI.

PyPI uploads from the monthly workflow need a Trusted Publisher entry for the
workflow filename `monthly-release.yml`; until one exists, that step fails
without failing the release.

## Manual releases

The local helper requires Python 3.12+, `uv`, Git and an authenticated GitHub CLI
(`gh auth login`). It can be run directly or through an agent using this skill.

Before generation, update the exact MultiConn dependency, refresh the lock and
sync the environment. Wait until that MultiConn version is available on PyPI.
Generator metadata comes from the corresponding Git tag. Review the generated
changes before preparing this package's release.

```powershell
git fetch origin --tags
python scripts/release.py bump VERSION
# Review/commit the version files with the source changes; review/merge and push.
python scripts/release.py check
python scripts/release.py draft --previous vPREVIOUS --notes-file PATH_TO_SUMMARY.md
```

Choose this package's version independently of MultiConn. `bump` updates both
`pyproject.toml` and `manifest.json`, then refreshes `uv.lock` while retaining
existing dependency resolutions.

The notes file contains the descriptive summary following
[the release-note style](release-notes.md). `draft` appends GitHub's exact generated
PR list, authors, contributor credits and comparison link. It targets committed,
pushed HEAD and never moves an existing tag. Use an OS temporary file for notes;
no local release-state directory is needed.

Review the draft in GitHub. Edit it, discard it or click **Publish release**.
Publishing triggers `.github/workflows/release.yml`, which requires a successful
`test.yml` push run on `master` for the exact tagged commit, then
builds and validates the wheel, source distribution and MCPB, uploads the Python
packages to PyPI, and attaches all release files to GitHub. Both the versioned
MCPB and `tapir-archicad.mcpb` download alias are included. The release is visible
while the job runs; check Actions for completion.

The full test suite runs in normal CI rather than again during publishing. If CI
is missing, still running or unsuccessful, publishing stops before building or
uploading. Once CI passes, rerun the release workflow for the same tag.

## One-time publisher setup

Configure a [PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
for `tapir-archicad-mcp` with owner `SzamosiMate`, repository
`tapir-archicad-MCP` and workflow filename `release.yml`. Leave the environment
field empty; this workflow does not specify an environment. No PyPI API token
needs to be stored in GitHub.

Merge the automation before using it for a release. To retry a failed publisher,
re-run the workflow or manually run **Publish release** with the existing published
tag. Drafts are rejected. Identical PyPI uploads can be retried; investigate
conflicting files instead of deleting or replacing a published version. GitHub
attachments are replaced by the same tag's build on retry.
