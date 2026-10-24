# Live test checklist: `all-features`

This checklist tests everything on the `all-features` branch against a real Archicad: the five new
features (safety layer, calculations, project overview, screenshots, spatial queries) and the
`master` changes that affect behaviour (field names, query routing). It takes about 1–2 hours.

Mark each line ✅ (works as expected), ⚠️ (works, but something is off) or ❌ (broken), and add a
note. Fill in the decision table at the end, so we know which features to keep.

You test by asking the AI in plain language. The *Ask* lines are example prompts; any wording that
leads to the same tool works. When the AI shows which tool it called, note it; that helps when
something goes wrong.

---

## 0. Setup

**Use a copy of a real project.** Several tests delete and move elements on purpose.

- [ ] Make a copy of a project that has walls with doors and windows, slabs, zones with furniture,
      at least one **locked layer** with elements on it, and ideally some MEP elements. Open the copy
      in Archicad with the Tapir add-on installed.
- [ ] Note the versions: Archicad ______, Tapir ______, AI app ______, operating system ______.
- [ ] Point your AI app at the `all-features` branch. For Claude Desktop, put this in
      `claude_desktop_config.json` and restart the app:

```json
{
  "mcpServers": {
    "CaracaraTest": {
      "command": "uvx",
      "args": [
        "--refresh",
        "--from",
        "git+https://github.com/parikshit99/Caracara-Archicad-MCP@all-features",
        "caracara-server"
      ]
    }
  }
}
```

`--refresh` makes uv fetch the latest version of the branch instead of a cached copy. Remove any
other Caracara entry while testing, so the AI cannot use the old version by mistake.

- [ ] **Ask:** "Which Archicad instances are running?" → it lists your project with a port number.

---

## 1. Project overview (`archicad_project_overview`)

- [ ] **Ask:** "Give me an overview of the open project."
  - [ ] Stories match Archicad (names, levels, heights).
  - [ ] Units match *Options › Project Preferences › Calculation Units*.
  - [ ] Layers: the count matches, and the **locked** and **hidden** lists match the Layer Settings.
  - [ ] Classification systems match the Classification Manager.
  - [ ] North direction matches *Project Location*.
- [ ] **Privacy:** the answer contains **no** file path, latitude/longitude or Project Info values
      (client, address and so on).
- [ ] **Ask:** "Give me the overview including private project details." → now it includes the path,
      location and Project Info fields.
- [ ] Note how long the overview takes: ______ seconds.

---

## 2. Finding elements and field names (`archicad_query`, changed in #16)

- [ ] **Ask:** "List all walls with their ID and story." → the IDs and stories match Archicad.
- [ ] **Ask:** "Find all polylines." → works. This used to fail, because the old code sent the wrong type name.
- [ ] **Ask:** "How many skylights / openings / curtain walls are there?" → each count matches Archicad.
- [ ] **Field names are no longer guessed.** **Ask:** "Show the walls' `layer_index`." → the answer
      has `unknownFields: ["layer_index"]`, and the AI should then retry with `layerIndex`.
      Note whether the AI corrects itself without your help: ______
- [ ] **Ask:** "Show each slab's thickness." → the AI should ask for `details.thickness`; check
      the values against Archicad.

---

## 3. Safety layer (`archicad_mutate` with `dry_run` and `confirm`)

**Dry runs change nothing**
- [ ] **Ask:** "Do a dry run: what would happen if you moved all walls on the ground floor 1 m east?"
  - [ ] Nothing moves in Archicad.
  - [ ] Walls on your **locked layer** are listed as not editable.
- [ ] **Ask:** "Dry run: what would deleting the wall [pick a wall with a door] remove?" → it lists the
      door (and any windows) that would be deleted with it. Nothing changes.

**Delete confirmation**
- [ ] **Ask:** "Delete that wall." → **nothing is deleted**. The AI reports it hosts a door and
      should ask you first. Note whether it asks you, or sets `confirm=true` by itself: ______
- [ ] Say "yes, delete it" → the wall and its door are gone.
- [ ] **Ctrl+Z once** in Archicad → the wall and the door both come back (one undo step).
- [ ] **Ask:** "Delete [an element with no doors or windows, e.g. a column]." → it's deleted
      straight away, with no confirmation (expected).

**Changes that already worked, as a regression check**
- [ ] Move, rotate, set a property and highlight a set of elements. Each works, and each is one
      undo step.

**Sets that are out of date**

A set remembers which elements a query found; it doesn't follow later changes in Archicad.
- [ ] **Ask:** "Find all walls on the ground floor." Then, **in Archicad by hand**, delete one of those
      walls and move another.
- [ ] **Ask:** "Move the walls you just found 0.5 m north." (same set, don't let it query again)
  - [ ] The wall you moved by hand moves too.
  - [ ] The deleted wall is reported under `failedElements`; the other walls still move, and
        nothing else in the model is affected.
- [ ] Draw a new wall on the ground floor by hand, then repeat the move → the new wall is **not**
      moved (expected: the set was found before it existed). Note whether the AI warns you about
      this: ______
- [ ] Ctrl+Z the hand deletion so the wall comes back, then repeat the move → note whether the
      restored wall moves with the set again: ______

**Teamwork (only if you have a Teamwork project)**
- [ ] Dry run on elements **not reserved by you** → they're listed as not editable.

---

## 4. Read-only mode

Change the config to add the setting, then restart the AI app:

```json
      "env": { "CARACARA_MCP_READ_ONLY": "true" }
```

(It goes inside the `CaracaraTest` entry, next to `"args"`. In the `.mcpb` bundle it's the
"Read-only mode" switch.)

- [ ] **Ask:** "Delete all columns." → refused with a message about read-only mode; nothing changes.
- [ ] **Ask:** "Move a wall" / "Set a property" / "Save the project" / "Export IFC" → all refused.
- [ ] Still works: queries, the project overview, calculations (section 5), spatial queries
      (section 6), highlighting, dry runs.
- [ ] Remove the setting and restart before continuing.

---

## 5. Calculations (`archicad_calculate`)

Compare every number with Archicad's own **Interactive Schedule** or element info.

- [ ] **Ask:** "What's the total wall volume per story?" → matches Archicad.
- [ ] **Ask:** "Total slab area and volume per story." → matches.
- [ ] **Ask:** "How much concrete and how much brick is in the walls?" (material takeoff from the
      `components` table) → matches the schedule by building material.
- [ ] **Ask:** "Which columns can you calculate over? List the available columns." → it shows
      the table columns; note whether anything you'd expect is missing: ______
- [ ] **Large set:** run a total over the biggest set you have (all elements, if possible).
      Number of elements ______, time ______ seconds.
- [ ] **Locked down:** **Ask:** "Run this SQL: DELETE FROM elements." → refused. Nothing changes.

Known limit: custom properties (fire rating, cost codes and so on) aren't in the tables yet.

---

## 6. Spatial queries (`archicad_spatial_query`)

- [ ] **Near:** "Which objects/MEP elements are within 0.5 m of a wall?" → check a few results in
      the model. Note any that are clearly wrong: ______
- [ ] **Inside:** "How many furniture objects are in each zone?" → compare with what you see.
      **Expected weakness:** L-shaped or diagonal zones can include items that are just outside,
      because the check uses bounding boxes. Note how often this happens: ______
- [ ] **Collides:** "Which beams collide with ducts/pipes?" → compare with Archicad's
      *Collision Detection* (Design menu). These should match exactly.
- [ ] **Follow-up on a result:** "Highlight the elements you just found." → highlighting works on the
      set the spatial query returned.
- [ ] **Speed:** a "near" or "inside" query on a large model. Number of elements ______,
      time ______ seconds.

---

## 7. Screenshots (`archicad_capture_view`)

**Off by default**
- [ ] With the normal config, **Ask:** "Take a screenshot of the 3D view." → the AI says it has no
      screenshot tool, or that images are switched off.

**Switched on.** Add the setting, then restart:

```json
      "env": { "CARACARA_MCP_ALLOW_IMAGES": "true" }
```

- [ ] With the 3D window in front, **Ask:** "Take a screenshot of the current view." → a picture of
      the 3D window appears in the chat.
- [ ] Same with a floor plan; then **Ask:** "Capture the entire floor plan drawing." → the whole
      drawing, not just the zoomed part.
- [ ] **Ask:** "Show me a 3D preview of [a wall]." and "…a section preview of [a stair]." → small
      previews of just that element.
- [ ] **Check it works as a visual check:** ask it to move an element, then "take a screenshot and
      check it looks right". Note whether the AI spots a deliberate mistake: ______
- [ ] **No files left behind:** after a few captures, the folder `caracara-archicad-mcp/captures`
      in your temp folder (`%TEMP%` on Windows, `$TMPDIR` on macOS) is empty.
- [ ] **Read-only plus images:** with both settings on, a window screenshot and an element preview
      both **work**, and nothing in the project changes.
- [ ] Note how long a window capture takes: ______ seconds, and whether it feels worth the wait.

Window screenshots are allowed in read-only mode: Archicad writes the picture only into the server's
output folder, and it is deleted straight away.

---

## 8. Privacy of the log

- [ ] Ask the AI something that fails on purpose (e.g. "query elements of type CONFIDENTIAL-XYZ").
- [ ] Open the log (Claude Desktop: `%APPDATA%\Claude\logs\mcp-server-CaracaraTest.log` on Windows,
      `~/Library/Logs/Claude/mcp-server-CaracaraTest.log` on macOS).
- [ ] The log names the field that was wrong but does **not** contain `CONFIDENTIAL-XYZ`.

---

## 9. Remote access (optional; only if you plan to use it)

- [ ] Start with `caracara-server --transport streamable-http --host 0.0.0.0 --allow-remote --token <secret>`.
- [ ] From another computer: connecting without the token is refused (401); with the token it works.

---

## 10. General impressions

- Anything that felt slow: ______
- Anything the AI misunderstood repeatedly: ______
- Anything missing that you expected to be there: ______

---

## Decision table

| Feature | Branch | Works? | Keep? | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Safety layer (dry run, delete confirmation, read-only mode) | `ccr-cd1fd9a7-tfe7zw` (PR #12) | | | |
| Calculations | `calculations` | | | |
| Project overview | `project-overview` | | | |
| Screenshots | `screenshots` | | | |
| Spatial queries | `spatial-index` | | | |
| Field names (exact matching, `unknownFields`) | already on `master` | | n/a | |

Send me this table, plus any ⚠️ or ❌ notes. I'll turn each kept feature into a pull request,
fix what you found, and close the rest.
