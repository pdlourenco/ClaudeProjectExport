# Claude Project Export — Extract Projects from Claude.ai Data Exports

> **The missing tool for Claude.ai power users.** Selectively extract projects, knowledge docs, prompt templates, and conversation history from your Claude.ai data export ZIP file.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![No Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#installation)

## The Problem

When you export your data from [Claude.ai](https://claude.ai), you get **one giant ZIP** with every conversation you've ever had. If you have 88 projects and 950 conversations, there's no way to extract just the projects you need.

**Claude Project Export** solves this. Point it at your export ZIP, pick the projects you want, and get clean local folders with all the knowledge docs, prompt templates, and related conversations — organized and ready to use.

## Key Features

- **Interactive project picker** — see all your projects listed with doc counts, conversation counts, and sizes, then pick by number
- **Selective extraction** — extract one project, multiple projects, or everything
- **Exact conversation matching** — join conversations to projects by UUID using a mapping you fetch from your browser, instead of guessing from names (Claude.ai exports don't link them)
- **Three modes** — interactive (human), JSON (automation), CLI (scripted)
- **Zero dependencies** — pure Python 3.10+ stdlib, nothing to install
- **Claude Code skill** — install as a `/ClaudeProjectExport` slash command

## Quick Start

```bash
git clone https://github.com/Brads777/ClaudeProjectExport.git
cd ClaudeProjectExport
python claude_export_extractor.py ~/Downloads/your-claude-export.zip
```

That's it. No `pip install`, no virtual environment, no config file.

## How to Export Your Data from Claude.ai

1. Go to [claude.ai](https://claude.ai) and sign in
2. Click your **profile icon** (bottom-left corner)
3. Click **Settings**
4. Under the **Account** section, click **Export Data**
5. Claude sends you an email with a download link (usually arrives within a few minutes)
6. Download the ZIP file. What's inside depends on when the export was produced:

| File | Contents |
|------|----------|
| `conversations.json` | All your chat history (every conversation you've had) |
| `projects/<uuid>.json` | One file per project — knowledge docs and prompt template |
| `projects.json` | Older exports instead ship a single file holding every project |
| `memories.json` | Saved memories |
| `users.json` | Account info |
| `login_history.json` | Sign-in history (not used by this tool) |

Both project layouts are read, so it doesn't matter which one your export uses.

## Usage

### Interactive Mode (Recommended)

```bash
python claude_export_extractor.py path/to/claude-export.zip
```

You'll see a table of all your projects:

```
  #  Project Name                                        Docs  Convos      Size     Created
───────────────────────────────────────────────────────────────────────────────────────────────
  1  My Research Project                                   12      28   340 KB  2025-11-03
  2  Course Materials                                       7       5    78 KB  2026-01-15
  3  API Documentation                                      3       2    12 KB  2026-03-20

Enter project numbers to extract (comma-separated, e.g. '1,3,5')
Or 'all' to extract everything, or 'q' to quit:
> 1,2
```

Then choose output directories for each, confirm, and extract.

### Non-Interactive Mode

```bash
# Extract projects 1 and 3 to specific directories
python claude_export_extractor.py export.zip --extract 1,3 --output "./research,./course"
```

### Exact Conversation Matching (Recommended)

By default the tool guesses which conversations belong to a project by comparing the project
name to conversation titles. If you can open claude.ai in a browser, you can do better — see
[How Conversation Matching Works](#how-conversation-matching-works) for the two-step workflow:

```bash
# 1. In your browser console on claude.ai, run fetch_mapping.js -> downloads mapping.json
# 2. Point the extractor at it
python claude_export_extractor.py export.zip --mapping mapping.json
```

### JSON Mode (for Automation)

```bash
# Get machine-readable project list
python claude_export_extractor.py export.zip --json
```

Returns a JSON array you can pipe into other tools:

```json
[
  {
    "number": 1,
    "name": "My Research Project",
    "doc_count": 12,
    "conv_count": 28,
    "total_kb": 340.2,
    "has_prompt": true
  }
]
```

## Output Structure

Each extracted project creates this organized layout:

```
<output_dir>/
├── project_knowledge/
│   ├── _project_metadata.json    # Project name, UUID, dates, doc/conversation counts
│   │                             #   (plus how conversations were matched, with --mapping)
│   ├── _prompt_template.md       # Project custom instructions (if the project had one)
│   ├── research-paper.pdf        # Knowledge docs you uploaded to the project
│   ├── api-spec.yaml             #   (identical copies deduplicated; same name but
│   │                             #    different content is kept as api-spec_1.yaml)
│   └── notes.md                  #   (original filenames preserved)
├── conversations/
│   ├── Building the API client.md         # Related conversations as readable markdown
│   ├── Debugging auth flow.md             #   (matched by project name keywords)
│   └── Architecture review.md
└── thinking/                              # only with --thinking; same filenames as above
    └── Building the API client.md
```

With `--mapping`, you can also ask for the conversations that belong to no project at all —
they're written once per run, to a directory of your choosing:

```bash
python claude_export_extractor.py export.zip --mapping mapping.json --unfiled ./_unfiled
```

```
_unfiled/
├── Weeknight dinner ideas.md
├── Quick API question.md
└── attachments/                # text extracted from attachments, if any
```

## How Conversation Matching Works

Claude.ai's export format **does not link conversations to projects** by ID — there's no
`project_uuid` field on conversations, and `projects.json` doesn't list its conversations.
The tool offers two ways to bridge that gap.

### Exact matching (recommended)

The link does exist server-side, and a logged-in browser session can read it. `fetch_mapping.js`
does exactly that and saves the result as `mapping.json`; the extractor then joins by UUID.

**Step 1 — fetch the mapping**

1. **Request your data export first** (Settings → Account → Export Data), then do the rest
   while you wait for the email. Order matters: the export is a snapshot taken when you
   request it, so fetching the mapping *afterwards* guarantees it covers everything the
   export contains. Fetch first and anything you chat about in between looks unmapped.
2. Sign in at [claude.ai](https://claude.ai)
3. Open DevTools (`F12`, or `Cmd-Opt-I` on macOS) and go to the **Console** tab
4. Paste the entire contents of [`fetch_mapping.js`](fetch_mapping.js) and press Enter
5. Wait for it to finish — `mapping.json` downloads automatically

The script prints how many conversations it found per project. Spot-check a couple against
the web app before relying on it: a project short a page still reports `exact` downstream,
with full confidence. If something goes wrong it reports every endpoint it tried and what
came back; setting `PROBE_ONLY = true` at the top runs just that diagnostic without
fetching anything.

**Step 2 — extract using it**

```bash
python claude_export_extractor.py export.zip --mapping mapping.json
```

Each project reports how its conversations were matched — and the same answer is written into
that project's `_project_metadata.json` as `"conversation_match"`, so the extracted folder still
says how it was built once you've forgotten which flags you ran:

```
  #  Project Name                                         Docs  Convos   Match      Size     Created
───────────────────────────────────────────────────────────────────────────────────────────────────────
  1  NEU Marketing 2700                                     12      28   exact    340 KB  2025-11-03
  2  Course Materials                                        7       5   exact     78 KB  2026-01-15
  3  Archived Ideas                                          3       0    none     12 KB  2026-03-20
```

> **⚠️ The endpoints `fetch_mapping.js` uses are undocumented and unversioned.** They are
> Claude.ai's internal web-app APIs, not a published interface — Anthropic can change or remove
> them at any time, and this script will break when they do. It reads no credentials and sends
> nothing anywhere except claude.ai (it relies on the session cookie your browser already has),
> but read it before you run it, and see the header comment for how to correct the paths if
> they've moved.

`mapping.json` contains your project and conversation names, so it's personal data —
it's in `.gitignore` and shouldn't be committed anywhere. It's plain, pretty-printed JSON,
so you can hand-edit it if a project was renamed or deleted.

#### What `exact` does and doesn't promise

`exact` means every conversation in that project came from the mapping rather than a guess. It
does **not** promise the project is complete — the mapping records only what claude.ai returned
at the moment you fetched it. Two things can leave a covered project short:

- **A conversation created or filed after the fetch.** Re-fetch; the staleness warning exists to
  tell you when this is possible.
- **A truncated fetch.** If `fetch_mapping.js` failed to follow pagination for a project, that
  project is quietly missing conversations while still reporting `exact`. The script warns on the
  console when it sees a full page it can't get a cursor for — don't ignore that warning.

A conversation you *removed* from a project is a different matter: it genuinely doesn't belong to
that project any more, so leaving it unfiled is the right answer, not a gap. Likewise a deleted
project — it isn't in your export either, so there is nothing to file into.

None of these are guessed back into a covered project: keyword matching never runs for a project
the mapping covers, so `exact` stays exact and no project ever mixes joined and guessed
conversations. Anything missing surfaces in the unfiled count instead, where you can see it. If
one of those really does belong to a project, add it to `mapping.json` by hand and re-run —
that's what the file is pretty-printed for.

### Keyword matching (fallback)

Without `--mapping`, the tool behaves exactly as it always has: it matches conversations using
keyword similarity on the project name. A project named "NEU Marketing 2700" matches
conversations containing "2700", "marketing", or "NEU" in their titles. This catches most
relevant conversations but produces both false positives (generic project names match too much)
and false negatives (a conversation titled "Ideas for the final capstone" belongs to that
project but shares no vocabulary with it).

When you supply `--mapping`, keyword matching is **off** by default: a project the mapping
doesn't cover reports `none` and extracts no conversations, rather than quietly falling back to
guesswork. Add `--fuzzy` to re-enable the fallback for uncovered projects:

```bash
python claude_export_extractor.py export.zip --mapping mapping.json --fuzzy
```

If the mapping was fetched before your export's most recent conversation activity, the tool
warns that it may be stale and carries on — anything filed since the fetch simply looks unmapped.

### Conversations in no project

With a mapping in hand, the tool can also account for everything it *didn't* file. Each run
reports the reconciliation:

```
Found 88 projects, 950 conversations
Mapping: 731 conversations filed by UUID, 12 guessed by keyword, 207 unfiled
```

Those three numbers always add up to the total, because every conversation is filed, guessed,
or unfiled — never two of the three:

- A conversation the mapping files under a project in your export is **filed**.
- A conversation the mapping knows nothing about, that an uncovered project keyword-matched
  under `--fuzzy`, is **guessed**. It is not also written to the unfiled bucket — it has a
  home, however tentative.
- Everything else is **unfiled**, and `--unfiled DIR` writes it out.

Keyword matching only ever draws from conversations the mapping leaves unfiled. If the mapping
says a conversation belongs to project A, then project B cannot guess its way to it, even when
B's name matches the title. The mapping wins; guesses only fill silence.

Be aware of what "unfiled" can mean. A conversation nothing claimed is **any** of: a standalone
chat that never belonged to a project; a chat from a project you have since deleted, so the
mapping points at a project your export doesn't contain; or a chat you started after fetching
the mapping. Nothing in the export distinguishes these, so the tool doesn't pretend to — they
all land in the same bucket.

`--unfiled` requires `--mapping`: keyword matching alone attaches one conversation to several
projects and leaves most matched by nothing, so "unfiled" carries no information without an
exact join to measure against.

## Use Cases

- **Migrate Claude.ai projects to Claude Code** — extract your project knowledge and use it as local context
- **Back up specific projects** — don't lose important research when your Claude.ai subscription changes
- **Audit project history** — review all knowledge docs and conversations organized by project
- **Course material extraction** — pull out teaching materials, rubrics, and student interaction history
- **Share project context** — extract a project's knowledge base to share with collaborators

## Claude Code Integration

This tool also works as a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill. Install it as a `/ClaudeProjectExport` slash command:

```bash
mkdir -p ~/.claude/skills/ClaudeProjectExport
cp claude_export_extractor.py SKILL.md ~/.claude/skills/ClaudeProjectExport/
```

Then in Claude Code, type `/ClaudeProjectExport` and follow the prompts.

## Requirements

- **Python 3.10+** (uses `match` statements and modern type hints)
- No external packages — stdlib only (`zipfile`, `json`, `argparse`, `pathlib`)
- Works on **Windows**, **macOS**, and **Linux**

## FAQ

### Does this extract images or file attachments from conversations?

It extracts text content from attachments (the `extracted_content` field in the export), but binary files (images, PDFs) are not included in Claude.ai's export format — only their text extractions are.

### Why aren't my conversations linked to the right project?

Claude.ai's export format doesn't include a project-to-conversation link. Without `--mapping`
the tool falls back to keyword matching on conversation titles, which works well for most cases
but isn't perfect — projects with very generic names (like "Test") match too many conversations,
and conversations whose titles don't echo the project name are missed entirely. Use
[exact matching](#exact-matching-recommended) to get the real link.

### Can I extract conversations that aren't in any project?

Currently the tool focuses on project-based extraction. For a full dump of all conversations, use the export ZIP directly — `conversations.json` contains everything.

### Does the output include Claude's thinking?

Not by default — pass `--thinking` and it will:

```bash
python claude_export_extractor.py export.zip --thinking
```

Conversations in recent exports carry `thinking` blocks alongside the reply text, and the
transcript holds only the reply. On one real export that was 3.3 million characters of
reasoning against 4.2 million of reply — worth having if you are archiving a project for how
something was worked out, and worth leaving out if you are not, which is why it is a flag.

`--thinking` writes a `thinking/` folder beside `conversations/`, one file per conversation,
**under the same filename as the transcript**:

```
<output_dir>/
├── conversations/
│   ├── Weekly sync.md
│   └── Weekly sync_1.md
└── thinking/
    ├── Weekly sync.md          # reasoning for the transcript of the same name
    └── Weekly sync_1.md
```

Conversations that carry no reasoning simply have no file — on that same export, 40 of 78
unfiled conversations had any. Blocks that are empty, or whose text the export withheld, are
skipped rather than written as empty sections.

### Does this work with Claude.ai Team/Enterprise exports?

It should work with any Claude.ai data export that follows the standard format (`conversations.json`, `projects.json`). The schema is auto-detected.

## Contributing

Issues and PRs welcome. The codebase is a single Python file — easy to read and modify.

## License

[MIT License](LICENSE) — use it however you want.

## Author

**Brad Scheller** — building [ToolsIQ](https://toolsiq.ai), an AI-powered toolkit for education and business.

---

*Built with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Anthropic's agentic coding tool.*
