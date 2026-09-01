---
name: ClaudeProjectExport
description: >
  Extract specific projects from a Claude.ai data export ZIP file. Lists all projects
  in the export, lets the user pick which to extract, and saves project knowledge docs
  and related conversations to chosen directories. Trigger with /ClaudeProjectExport.
  Use when the user has a Claude.ai export ZIP and wants to pull project data to their
  local drive, mentions "claude export", "extract projects from export", or wants to
  migrate Claude.ai project context into Claude Code.
user_invocable: true
---

# Claude Export Extractor — Claude Code Skill

Extract projects from a Claude.ai data export ZIP into local directories for use with Claude Code.

## What This Skill Does

Claude.ai projects contain knowledge docs, prompt templates, and conversation history. This skill
extracts specific projects from an export ZIP into organized local folders so the content can be
used as context for Claude Code work.

**Output structure per project:**
```
<output_dir>/
  project_knowledge/          # Knowledge docs, attachments, prompt template
    _project_metadata.json    # Name, UUID, dates, counts, and how conversations were matched
    _prompt_template.md       # Project custom instructions (if any)
    <knowledge files>...      # All uploaded docs
  conversations/              # Related conversation history as markdown
    <conversation>.md ...     # One file per conversation
  files/                      # Documents Claude wrote, one folder per conversation
    <conversation>/           #   _manifest.json says where each came from
  thinking/                   # Only with --thinking or --faithful; same filenames as above
  raw/                        # Only with --faithful; the source records, verbatim
```

## The one thing to get right

The export format carries **no link between a conversation and its project**. Left to itself the
extractor guesses from name similarity, and on a measured real export that reached 41 of 234
conversations — with false positives among them. A mapping file fetched from the user's browser
raises that to 156, exactly. So Step 2 is not optional politeness: skipping it means handing the
user a worse result than the tool is capable of, without telling them.

## Workflow

### Step 1: Locate the ZIP

Ask the user for the path to their Claude.ai export ZIP file. Common locations:
- `~/Downloads/`
- `C:\Downloads\`
- The user may have already mentioned it

Verify the file exists before proceeding.

### Step 2: Offer exact conversation matching

Ask whether the user can open [claude.ai](https://claude.ai) in a signed-in browser.

**If yes**, walk them through it:

1. Read `~/.claude/skills/ClaudeProjectExport/fetch_mapping.js` and give them its contents to
   paste — they need the text, not a path they'd have to go find. If that file isn't there,
   the install predates it: ask them to re-run the `cp` line from the repo's README, which now
   copies it too.
2. They open DevTools (`F12`, or `Cmd-Opt-I` on macOS) → **Console** on a claude.ai tab. On
   Firefox the first paste is blocked: they press `Ctrl+V`, type `allow pasting`, press Enter,
   then paste again.
3. It downloads `mapping.json`. Ask them for the path.
4. Pass `--mapping "<path>"` on **every** extractor command from here on.

Two things worth saying while they wait:

- If they have not yet requested the data export, they should request it *first* and fetch the
  mapping while waiting for the email. The export is a snapshot taken at request time, so a
  mapping fetched afterwards covers everything in it.
- The script's endpoints are Claude.ai's internal ones — undocumented and unversioned. If it
  fails it prints every endpoint it tried; relay that rather than guessing.

**If no**, carry on without it, and say plainly that conversations will be matched by guessing
from their titles, so some will be missed and some misfiled.

### Step 3: List projects

```bash
PYTHONIOENCODING=utf-8 python ~/.claude/skills/ClaudeProjectExport/claude_export_extractor.py "<zip_path>" [--mapping "<path>"] --json
```

Returns each project's name, doc count, conversation count, and size. With `--mapping` each entry
also carries `strategy`:

| `strategy` | meaning |
|---|---|
| `exact` | conversations joined by UUID — nothing guessed |
| `fuzzy` | guessed from the project name; only appears if you passed `--fuzzy` |
| `none` | the mapping doesn't cover this project, so nothing was attached |

A `none` usually means the project was deleted from claude.ai after the export was taken. Say so
rather than presenting it as an empty project.

With `--mapping` the run also prints a reconciliation line — *N filed by UUID, N guessed, N
unfiled* — which always sums to the total. Worth relaying: it is the user's evidence that
nothing vanished.

### Step 4: Present projects to the user

Show the projects in a clean table. Include the match strategy when a mapping is in play. Ask
which to extract.

If the reconciliation line shows a meaningful number of unfiled conversations, offer
`--unfiled <dir>`: *"N conversations belong to no project — save those too?"* They are either
standalone chats or from deleted projects, and nothing in the export can tell those apart.

### Step 5: Get output directories

For each selected project, ask where to save the output. Suggest a sensible default based on the
project name and current working directory.

### Step 6: Extract

```bash
PYTHONIOENCODING=utf-8 python ~/.claude/skills/ClaudeProjectExport/claude_export_extractor.py "<zip_path>" [--mapping "<path>"] --extract <nums> --output "<dir1>,<dir2>" [--unfiled <dir>]
```

Two more flags worth offering rather than assuming:

- `--thinking` — also writes Claude's reasoning to `thinking/`. It was 3.3 MB against 4.2 MB of
  reply text on a measured export, so ask rather than deciding for them.
- `--faithful` — for archiving rather than reading. Implies `--thinking`, adds tool calls and
  their results, citations, the conversation's summary and file names to the transcripts, and
  writes every source record verbatim to `raw/`, so nothing in the export is lost.

### Step 7: Report results

Confirm what was saved: doc count, conversation count, output paths, and — when a mapping was
used — how many conversations were filed exactly versus left unfiled.

If the run reported files, mention `files/`: those are the documents Claude wrote during the
conversations, rebuilt from the tool calls that produced them. Check each `_manifest.json` for
any entry with `"complete": false` and say so — that file was also changed outside the recorded
tool calls, so it is the last state the transcript can account for rather than the final one.

## Notes

- Re-running an extraction into the same directory is safe: it refreshes files in place rather
  than duplicating or versioning them
- `_project_metadata.json` records `conversation_match` (`exact` / `fuzzy` / `none`) when a
  mapping was used, so the output folder documents how it was built
- Identical duplicate docs are dropped; two different docs that happen to share a filename are
  both kept, disambiguated as `notes.txt` and `notes_1.txt`. Nothing is silently overwritten
- Without `--thinking` or `--faithful`, Claude's reasoning is not extracted — about 44% of the
  message text on a measured export. Set that expectation if the user goes looking for it
- Documents Claude wrote are extracted by default into `files/`, with a marker in the transcript
  pointing at each. They are rebuilt by replaying the tool calls that wrote them, so a file the
  conversation also edited through the shell is marked incomplete rather than passed off as
  final — relay that distinction rather than smoothing it over
- Errors are one-line messages on stderr with exit status 1. Relay them verbatim; they name the
  problem
- `PYTHONIOENCODING=utf-8` is required on Windows to avoid emoji encoding errors
- Pass `all` numbers to `--extract` for a full dump
