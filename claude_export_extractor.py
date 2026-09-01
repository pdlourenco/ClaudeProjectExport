#!/usr/bin/env python3
# ©2026 Brad Scheller
"""
Claude.ai Export Extractor
==========================
Interactive tool for extracting specific projects from a Claude.ai data export ZIP.

Usage:
    python claude_export_extractor.py <path_to_zip>
    python claude_export_extractor.py <path_to_zip> --json       # machine-readable project list
    python claude_export_extractor.py <path_to_zip> --extract <project_nums> --output <dirs>
    python claude_export_extractor.py <path_to_zip> --mapping mapping.json

Examples:
    # Interactive mode — pick projects, set output dirs
    python claude_export_extractor.py ~/Downloads/claude_export.zip

    # Machine-readable — for Claude Code skill automation
    python claude_export_extractor.py export.zip --json

    # Non-interactive — extract projects 1,3 to specific dirs
    python claude_export_extractor.py export.zip --extract 1,3 --output "/path/one,/path/two"

    # Exact conversation-to-project join, using a mapping produced by fetch_mapping.js
    python claude_export_extractor.py export.zip --mapping mapping.json

    # Exact join, falling back to keyword matching for projects the mapping misses
    python claude_export_extractor.py export.zip --mapping mapping.json --fuzzy

    # Also save the conversations that belong to no project at all
    python claude_export_extractor.py export.zip --mapping mapping.json --unfiled ./_unfiled
"""

import zipfile
import json
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_name(name: str, max_len: int = 80) -> str:
    """Sanitize a string for use as a filename."""
    # The surrogate range is here for the same reason as the control characters: JSON
    # permits lone surrogates, and a filename holding one cannot be encoded to disk.
    name = re.sub(r'[\\/*?:"<>|\x00-\x1f\ud800-\udfff]', "_", name)
    name = re.sub(r"_+", "_", name)
    name = re.sub(r"\s+", " ", name).strip().strip("_. ")
    return name[:max_len] or "untitled"


def ts(iso: str) -> str:
    """Format ISO timestamp to readable string."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso or ""


def ts_short(iso: str) -> str:
    """Format ISO timestamp to short date."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def detect_extension(content: str, filename: str = "") -> str:
    """Guess file extension if filename doesn't already have one."""
    if "." in Path(filename).name:
        return ""
    stripped = content.strip()
    if stripped.startswith("<!DOCTYPE") or stripped.startswith("<html"):
        return ".html"
    if stripped.startswith("<?xml") or stripped.startswith("<svg"):
        return ".xml"
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return ".json"
        except Exception:
            pass
    if stripped.startswith("---\n") or re.match(r"^#{1,3}\s", stripped):
        return ".md"
    if stripped.startswith("def ") or stripped.startswith("import ") or stripped.startswith("class "):
        return ".py"
    return ".txt"


def safe_filename(name: str, max_len: int = 80) -> str:
    """Sanitize a filename, truncating the stem so the extension survives.

    safe_name truncates blind, which for a long name cuts the extension off the end and
    leaves a file nothing will open — the same failure that used to lose knowledge docs.
    A file Claude wrote is named by its own extension, so that has to be kept.
    """
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > 10 or not stem:
        return safe_name(name, max_len)
    ext = "." + re.sub(r'[^A-Za-z0-9]', "", ext)
    return (safe_name(stem, max(1, max_len - len(ext))) + ext) if len(ext) > 1 else safe_name(name, max_len)


class NameAllocator:
    """Hands out write paths within one directory, disambiguating collisions as name_1.ext.

    Sanitizing and truncating filenames makes distinct source names collide (a/b.txt and
    a\\b.txt both sanitize to a_b.txt; two names differing past character 80 both truncate
    to the same thing), so writing blind loses documents silently.

    A name handed out earlier in this run is never handed out again. A file left over from
    an *earlier* run is overwritten, so re-extracting into the same directory refreshes it
    in place rather than accumulating a copy of every document per run. Names the caller
    reserves up front — the metadata and prompt files — are treated as already taken.

    Each name resumes from its own counter, so a directory full of identically-named files
    costs one step apiece instead of rescanning from _1 every time.
    """

    def __init__(self, directory: Path, reserved=()):
        self.directory = directory
        self.counters = {}
        self.allocated = set(reserved)

    def allocate(self, filename: str) -> Path:
        stem, dot, ext = filename.rpartition(".")
        if dot:
            ext = "." + ext
        else:
            stem, ext = filename, ""

        counter = self.counters.get(filename, 0)
        while True:
            candidate = filename if counter == 0 else f"{stem}_{counter}{ext}"
            counter += 1
            if candidate not in self.allocated:
                self.counters[filename] = counter
                self.allocated.add(candidate)
                return self.directory / candidate


# ── Data loading ──────────────────────────────────────────────────────────────

def load_export(zip_path: Path):
    """Load projects and conversations from the export ZIP.

    Exports come in two layouts. Older ones ship a single projects.json holding every
    project. Current ones ship one file per project, under projects/<uuid>.json. Reading
    only the first file whose name matches drops every project but one — silently, since
    a one-project export is perfectly plausible — so every match is read and merged.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

        proj_files = sorted(n for n in names if "project" in n.lower() and n.endswith(".json"))
        conv_file = next((n for n in names if "conversation" in n.lower() and n.endswith(".json")), None)

        projects = []
        for proj_file in proj_files:
            projects.extend(_as_projects(json.loads(zf.read(proj_file))))

        conversations = json.loads(zf.read(conv_file)) if conv_file else []

    if isinstance(conversations, dict):
        conversations = conversations.get("conversations", conversations.get("data", []))

    # An archive could carry both layouts; keep the first record for each UUID.
    seen = set()
    unique_projects = []
    for proj in projects:
        uuid = proj.get("uuid")
        if uuid:
            if uuid in seen:
                continue
            seen.add(uuid)
        unique_projects.append(proj)

    return unique_projects, conversations


def load_account_files(zip_path: Path) -> dict:
    """Return the archive's JSON files that this tool never reads, name -> bytes.

    users.json, memories.json and login_history.json are account-level rather than project
    data, so nothing here parses them — which also means nothing here would notice if a
    future export added another. Matching by exclusion rather than by name keeps that from
    mattering: whatever is not a project or conversation file is carried across untouched.
    """
    out = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            lowered = name.lower()
            if "project" in lowered or "conversation" in lowered:
                continue
            out[Path(name).name] = zf.read(name)
    return out


def _as_projects(blob):
    """Normalise one project file's contents to a list of project records.

    Handles all three shapes seen in the wild: a bare list of projects, a wrapper object
    with a "projects" list, and a single project object in its own file.
    """
    if isinstance(blob, list):
        return [p for p in blob if isinstance(p, dict)]
    if isinstance(blob, dict):
        nested = blob.get("projects")
        if isinstance(nested, list):
            return [p for p in nested if isinstance(p, dict)]
        return [blob]
    return []


# ── Conversation mapping ──────────────────────────────────────────────────────

MAPPING_SCHEMA = 1


class MappingError(Exception):
    """Raised when a mapping file is missing, malformed, or of an unknown schema."""


def load_mapping(path: Path) -> dict:
    """Load and validate an external conversation-to-project mapping file.

    Claude.ai's export does not record which project a conversation belongs to, so the
    mapping is produced separately from a logged-in browser session (see fetch_mapping.js)
    and passed in with --mapping. Schema v1:

        {
          "schema": 1,
          "fetched_at": "2026-08-20T10:00:00Z",
          "org_uuid": "...",
          "projects": {"<project_uuid>": "<project name>"},          # optional
          "conversations": {
            "<conversation_uuid>": {"project_uuid": "...", "project_name": "..."}
          }
        }

    The optional "projects" key lists every project the fetch saw. It lets a project with
    no conversations report an honest "exact" match of zero, rather than looking uncovered.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MappingError(f"Mapping file not found: {path}")
    except OSError as exc:
        raise MappingError(f"Could not read mapping file {path}: {exc}")
    except json.JSONDecodeError as exc:
        raise MappingError(f"Mapping file is not valid JSON ({path}): {exc}")

    if not isinstance(raw, dict):
        raise MappingError(
            f"Mapping file must contain a JSON object, found {type(raw).__name__}: {path}"
        )

    schema = raw.get("schema")
    if schema != MAPPING_SCHEMA:
        raise MappingError(
            f"Unsupported mapping schema {schema!r} in {path} — this tool understands schema "
            f"{MAPPING_SCHEMA}. Re-run fetch_mapping.js to produce a current mapping file."
        )

    convs = raw.get("conversations")
    if not isinstance(convs, dict):
        raise MappingError(f"Mapping file has no 'conversations' object: {path}")

    cleaned = {}
    for conv_uuid, entry in convs.items():
        if not isinstance(entry, dict):
            raise MappingError(
                f"Mapping entry for conversation {conv_uuid} must be an object: {path}"
            )
        project_uuid = entry.get("project_uuid")
        if not project_uuid:
            raise MappingError(
                f"Mapping entry for conversation {conv_uuid} has no 'project_uuid': {path}"
            )
        cleaned[conv_uuid] = {
            "project_uuid": project_uuid,
            "project_name": entry.get("project_name", ""),
        }

    projects = raw.get("projects")
    if projects is not None and not isinstance(projects, dict):
        raise MappingError(f"Mapping file's optional 'projects' key must be an object: {path}")

    return {
        "schema": schema,
        "fetched_at": raw.get("fetched_at", ""),
        "org_uuid": raw.get("org_uuid", ""),
        "projects": projects or {},
        "conversations": cleaned,
    }


def _parse_iso(value: str):
    """Parse an ISO-8601 timestamp into an aware datetime, or None if unparseable."""
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def mapping_staleness(mapping, conversations):
    """Return (fetched_at, newest_update) when the mapping predates the export, else None.

    A mapping fetched before the export's most recent conversation activity cannot know
    about anything created since. Those conversations are simply unmapped, so the result
    is incomplete rather than wrong — worth a warning, not a refusal.
    """
    fetched = _parse_iso(mapping.get("fetched_at", ""))
    if not fetched:
        return None

    newest = None
    for conv in conversations:
        updated = _parse_iso(conv.get("updated_at", ""))
        if updated and (newest is None or updated > newest):
            newest = updated

    if newest and fetched < newest:
        return fetched, newest
    return None


def build_project_index(projects, conversations, mapping=None, allow_fuzzy=True):
    """Build an enriched index of projects with doc counts and matched conversations.

    Conversations reach a project by one of three strategies, recorded per entry:
      "exact" — joined by UUID through a mapping file (see load_mapping)
      "fuzzy" — keyword similarity between the project name and conversation titles
      "none"  — the mapping does not cover this project and fuzzy matching is off

    "exact" means none of the project's conversations were guessed at. It does not mean the
    project is complete: a conversation created after the mapping was fetched, or missed by a
    truncated fetch, is in the export but not in the mapping. Keyword matching is not used to
    fill those gaps — a covered project never falls back — so no project mixes joined and
    guessed conversations. Such conversations end up unfiled, where they are visible.
    """
    # Build keyword-to-conversation mapping
    conv_name_index = []
    for conv in conversations:
        name = (conv.get("name") or "").lower()
        msg_count = len(conv.get("chat_messages") or conv.get("messages") or [])
        conv_name_index.append((name, msg_count, conv))

    # Exact join: group conversations under the project UUID the mapping assigns them.
    by_project = defaultdict(list)
    covered = set()
    if mapping:
        for conv in conversations:
            entry = mapping["conversations"].get(conv.get("uuid"))
            if entry:
                by_project[entry["project_uuid"]].append(conv)
        # Union, not a fallback: "projects" exists so a project with no conversations can
        # still report an honest exact match of zero, but a hand-edited mapping whose
        # "projects" omits a project its conversations reference must not strand them.
        covered = set(mapping["projects"]) | set(by_project)

    # Keyword matching draws only from conversations the mapping leaves unfiled. The mapping
    # is authoritative: an uncovered project has no business guessing at a conversation that
    # is known to belong somewhere else. This also keeps the exact and guessed sets disjoint,
    # so every conversation is filed, guessed, or unfiled — never two of the three.
    fuzzy_pool = conv_name_index
    if mapping:
        fuzzy_pool = [row for row in conv_name_index
                      if row[2].get("uuid") not in mapping["conversations"]]

    index = []
    for proj in projects:
        name = proj.get("name") or proj.get("title") or "Untitled"
        uuid = proj.get("uuid", "")
        created = ts_short(proj.get("created_at", ""))
        description = proj.get("description", "")
        prompt = proj.get("prompt_template", "")
        docs = proj.get("docs") or []

        # Deduplicate docs by filename *and* content. Two docs sharing a name but holding
        # different text are different documents; keying on the name alone discards one.
        seen = set()
        unique_docs = []
        for d in docs:
            content = d.get("content")
            if not content:
                continue
            key = (d.get("filename", "untitled"), content)
            if key not in seen:
                seen.add(key)
                unique_docs.append(d)

        # Count total content size
        total_kb = sum(len(d.get("content", "")) for d in unique_docs) / 1024

        # Attach conversations: exact where the mapping covers this project, else keywords
        if mapping and uuid in covered:
            matched_convos, strategy = by_project.get(uuid, []), "exact"
        elif allow_fuzzy:
            matched_convos, strategy = _keyword_match(name, fuzzy_pool), "fuzzy"
        else:
            matched_convos, strategy = [], "none"

        index.append({
            "name": name,
            "uuid": uuid,
            "created": created,
            "description": description,
            "prompt_template": prompt,
            "docs": unique_docs,
            "doc_count": len(unique_docs),
            "total_kb": total_kb,
            "matched_conversations": matched_convos,
            "conv_count": len(matched_convos),
            "strategy": strategy,
            "source": proj,      # verbatim, for --faithful; nothing else reads it
        })

    return index


def _keyword_match(project_name: str, conv_name_index) -> list:
    """Return conversations whose title contains any keyword from the project name."""
    keywords = _project_keywords(project_name)
    matched_convos = []
    for cname, mcnt, conv in conv_name_index:
        if any(k in cname for k in keywords):
            matched_convos.append(conv)
    return matched_convos


def _project_keywords(project_name: str) -> list:
    """Generate search keywords from a project name for conversation matching."""
    name_lower = project_name.lower()
    keywords = [name_lower]

    # Split into significant words (3+ chars, skip common words)
    skip = {"the", "for", "and", "with", "from", "into", "this", "that", "create", "course", "project", "new"}
    words = [w for w in re.split(r'\W+', name_lower) if len(w) >= 3 and w not in skip]
    keywords.extend(words)

    return keywords


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_project(entry, output_dir: Path, record_strategy: bool = False,
                    include_thinking: bool = False, faithful: bool = False):
    """Extract a single project's docs and conversations to the output directory.

    record_strategy notes in the saved metadata how the conversations were matched. It is
    only meaningful when a mapping was supplied; without one every project is matched the
    same way and the field would say nothing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    docs_dir = output_dir / "project_knowledge"
    conv_dir = output_dir / "conversations"
    docs_dir.mkdir(exist_ok=True)

    stats = {"docs": 0, "conversations": 0, "docs_kb": 0, "convs_msgs": 0, "files": 0}

    # ── Save project metadata ────────────────────────────────────────────
    meta = {
        "name": entry["name"],
        "uuid": entry["uuid"],
        "description": entry["description"],
        "created": entry["created"],
        "extracted_at": datetime.now().isoformat(),
        "doc_count": entry["doc_count"],
        "conversation_count": entry["conv_count"],
    }
    if record_strategy:
        # "exact" — joined by UUID; "fuzzy" — guessed from the project name; "none" — not matched
        meta["conversation_match"] = entry["strategy"]
    (docs_dir / "_project_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    # ── Save prompt template ─────────────────────────────────────────────
    if entry["prompt_template"]:
        (docs_dir / "_prompt_template.md").write_text(
            entry["prompt_template"], encoding="utf-8", errors="backslashreplace"
        )

    # ── Extract knowledge docs ───────────────────────────────────────────
    # The metadata and prompt files were written above, and the allocator overwrites files
    # it did not hand out. safe_name strips leading underscores, so nothing can currently
    # sanitize onto those names — reserving them keeps that from silently ceasing to be
    # true if safe_name changes.
    docs = NameAllocator(docs_dir, reserved=("_project_metadata.json", "_prompt_template.md"))
    for doc in entry["docs"]:
        filename = doc.get("filename", "untitled")
        content = doc.get("content", "")
        if not content:
            continue

        ext = detect_extension(content, filename)
        out_path = docs.allocate(safe_name(filename) + ext)
        out_path.write_text(content, encoding="utf-8", errors="backslashreplace")
        stats["docs"] += 1
        stats["docs_kb"] += len(content) / 1024

    # ── Extract conversations ────────────────────────────────────────────
    if entry["matched_conversations"]:
        conv_dir.mkdir(exist_ok=True)
        conv_names = NameAllocator(conv_dir)

        for conv in entry["matched_conversations"]:
            stats["convs_msgs"] += write_conversation(
                conv, conv_names, docs_dir,
                thinking_dir=(output_dir / "thinking") if include_thinking else None,
                raw_dir=(output_dir / "raw" / "conversations") if faithful else None,
                faithful=faithful, files_root=output_dir / "files", counters=stats)
            stats["conversations"] += 1

    if faithful and entry.get("source") is not None:
        # The project record as the export holds it, including the fields the metadata
        # summary above does not carry and the full doc entries with their own ids.
        raw_dir = output_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "project.json").write_text(
            json.dumps(entry["source"], indent=2), encoding="utf-8")

    return stats


def write_conversation(conv, names: NameAllocator, attach_dir: Path, thinking_dir=None,
                       raw_dir=None, faithful: bool = False, files_root=None,
                       counters=None) -> int:
    """Write one conversation as markdown, via `names`; return its message count.

    Text content from attachments is written alongside, into attach_dir. The allocator is
    passed in rather than built here so that a whole directory's worth of conversations
    shares one — conversations sharing a title need to see each other's names, and a
    directory left over from an earlier run needs to be refreshed rather than duplicated.

    When thinking_dir is given, any reasoning the conversation carries is written there
    under the *same* filename the transcript received. Deliberately not a name of its own:
    two conversations called "Untitled" are disambiguated once, by the allocator, and both
    files inherit that answer — so a transcript and its reasoning always share a name.
    raw_dir receives the source record verbatim, under that same name again.

    `faithful` adds the parts of a message the transcript drops — which tool ran, what came
    back, the sources cited — rather than only the prose.

    files_root receives the documents the conversation produced, in a folder named for the
    transcript. They are part of what was said, not an extra, so this is not behind a flag.
    """
    title = conv.get("name") or "Untitled"
    conv_id = conv.get("uuid", "unknown")
    created = ts(conv.get("created_at", ""))
    updated = ts(conv.get("updated_at", ""))
    messages = conv.get("chat_messages") or conv.get("messages") or []

    lines = [
        f"# {title}\n",
        f"- **ID:** {conv_id}",
        f"- **Created:** {created}",
        f"- **Updated:** {updated}",
        f"- **Messages:** {len(messages)}\n",
    ]
    summary = (conv.get("summary") or "").strip() if faithful else ""
    if summary:
        lines.append(f"> {summary}\n")
    lines.append("---\n")

    # The transcript's name is claimed before its body is built, because the files this
    # conversation produced are filed under that name and the markers pointing at them are
    # written inline. Allocation order across conversations is unchanged — still one claim
    # per conversation, in the same sequence.
    out_path = names.allocate(safe_name(title) + ".md")

    # Replayed up front: a marker sits at the message that wrote the file, but what it
    # has to say — whether the replay stayed consistent to the end — is only known once
    # every later edit has been applied.
    produced = collect_files(conv) if files_root is not None else {}
    files_folder = out_path.stem
    if produced:
        written = write_conversation_files(produced, Path(files_root) / files_folder)
        if counters is not None:
            counters["files"] = counters.get("files", 0) + written
    by_message = {}
    for record in produced.values():
        if record.get("message") is not None:
            by_message.setdefault(id(record["message"]), []).append(record)

    thinking_lines = []
    for msg in messages:
        role = (msg.get("sender") or msg.get("role") or "unknown").upper()
        msg_ts = ts(msg.get("created_at", ""))

        content = _extract_message_content(msg)

        if thinking_dir is not None:
            reasoning = _extract_thinking(msg)
            summaries = []
            if faithful:
                for block in (msg.get("content") if isinstance(msg.get("content"), list) else []):
                    if isinstance(block, dict) and block.get("type") == "thinking":
                        summaries.extend(_thinking_summaries(block))
            if reasoning or summaries:
                thinking_lines.append(f"### {role}  _{msg_ts}_\n")
                for line in summaries:
                    thinking_lines.append(f"> {line}")
                if summaries:
                    thinking_lines.append("")
                if reasoning:
                    thinking_lines.append(reasoning)
                    thinking_lines.append("")
                thinking_lines.append("---\n")

        attachments = msg.get("attachments") or msg.get("files") or []
        attach_notes = []
        for att in attachments:
            fname = att.get("file_name") or att.get("name") or "attachment"
            ftype = att.get("file_type") or att.get("type") or ""
            attach_notes.append(f"[Attachment: {fname} ({ftype})]")
            # Save text-based attachment content
            att_content = att.get("extracted_content") or att.get("content") or ""
            if att_content and len(att_content) > 50:
                att_ext = detect_extension(att_content, fname)
                att_safe = safe_name(fname) + att_ext
                attach_dir.mkdir(parents=True, exist_ok=True)
                att_path = attach_dir / att_safe
                if not att_path.exists():
                    att_path.write_text(att_content, encoding="utf-8",
                                        errors="backslashreplace")

        lines.append(f"### {role}  _{msg_ts}_\n")
        if content:
            lines.append(content.strip())
            lines.append("")
        for note in attach_notes:
            lines.append(f"> {note}")
        if attach_notes:
            lines.append("")

        written_here = by_message.get(id(msg), [])
        if written_here:
            for record in written_here:
                lines.append(_file_marker(record, f"files/{files_folder}"))
            lines.append("")

        if faithful:
            extra = _render_tool_calls(msg) + _render_citations(msg)
            for f in (msg.get("files") or []):
                if isinstance(f, dict) and f.get("file_name"):
                    extra.append(f"> [File: {f['file_name']}]")
            if extra:
                lines.extend(extra + [""])

        lines.append("---\n")

    out_path.write_text("\n".join(lines), encoding="utf-8", errors="backslashreplace")

    if raw_dir is not None:
        # Verbatim, so that anything this file does not understand — today's unrendered
        # fields and tomorrow's new ones — survives extraction without a code change.
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / (out_path.stem + ".json")).write_text(
            json.dumps(conv, indent=2), encoding="utf-8")

    if thinking_lines:
        header = [
            f"# {title} — reasoning\n",
            f"- **ID:** {conv_id}",
            f"- **Created:** {created}",
            f"- **Transcript:** {out_path.name}\n",
            "Claude's internal reasoning for this conversation. The sections below line up",
            "with the assistant messages in the transcript; messages that carried no",
            "reasoning, or whose reasoning was withheld, are absent rather than empty.\n",
            "---\n",
        ]
        thinking_dir.mkdir(parents=True, exist_ok=True)
        (thinking_dir / out_path.name).write_text(
            "\n".join(header + thinking_lines), encoding="utf-8", errors="backslashreplace")

    return len(messages)


# ── Files Claude produced ─────────────────────────────────────────────────────

# An artifact's media type names the file it stands for. `language` refines the code
# case, which is one media type covering every language.
ARTIFACT_TYPES = {
    "text/markdown": ".md",
    "text/html": ".html",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "image/svg+xml": ".svg",
    "application/vnd.ant.mermaid": ".mmd",
    "application/vnd.ant.react": ".jsx",
}

LANGUAGE_EXTENSIONS = {
    "python": ".py", "javascript": ".js", "typescript": ".ts", "bash": ".sh",
    "sh": ".sh", "json": ".json", "yaml": ".yaml", "html": ".html", "css": ".css",
    "sql": ".sql", "matlab": ".m", "r": ".R", "java": ".java", "c": ".c",
    "cpp": ".cpp", "go": ".go", "rust": ".rs", "ruby": ".rb", "php": ".php",
}


def _artifact_filename(inp: dict, content: str) -> str:
    """Name an artifact, which has a title and a media type but no path of its own."""
    title = str(inp.get("title") or "artifact")
    if inp.get("type") == "application/vnd.ant.code":
        ext = LANGUAGE_EXTENSIONS.get(str(inp.get("language") or "").lower(), ".txt")
    else:
        ext = ARTIFACT_TYPES.get(inp.get("type"), "")
    if not ext:
        ext = detect_extension(content, title)
    base = safe_name(title, max(1, 80 - len(ext)))
    return base + ("" if base.lower().endswith(ext.lower()) else ext)


def collect_files(conv) -> dict:
    """Rebuild the files a conversation produced, by replaying the tool calls that wrote them.

    Claude writes a document one of two ways, and the export records both but neither as a
    file: an artifact carries its whole body on every revision, while `create_file` writes
    a body once and `str_replace` edits it afterwards. Replaying those in order recovers
    what the file finally said.

    Replay is only ever as complete as the record. A file the conversation also changed
    through the shell — a heredoc, sed, a script it ran — moves without leaving a tool
    call to replay, so a later edit no longer matches what we hold. That is counted rather
    than guessed at: `complete` is False for such a file and the caller says so, because a
    file that silently claims to be final is worse than one that admits it isn't.

    Returns {key: record}, in the order the files were first written.
    """
    files = {}
    last_path = None

    for msg in conv.get("chat_messages") or conv.get("messages") or []:
        blocks = msg.get("content")
        if isinstance(blocks, dict):
            blocks = [blocks]
        if not isinstance(blocks, list):
            continue

        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name") or ""
            inp = block.get("input")
            if not isinstance(inp, dict):
                continue

            if name == "artifacts":
                _replay_artifact(files, inp, msg)
                continue

            path = inp.get("path")
            text = inp.get("file_text")

            # Keyed on the shape rather than the tool name: a differently-named tool
            # writing a path and a body is the same event, and the export has renamed
            # its tools before.
            if isinstance(path, str) and isinstance(text, str):
                record = files.get(path)
                if record is None:
                    files[path] = {
                        "name": Path(path).name or safe_name(path),
                        "source": path, "origin": "create_file", "content": text,
                        "applied": 0, "unmatched": 0, "message": msg,
                    }
                else:
                    # A second write to the same path replaces it, as the tool would.
                    record["content"] = text
                last_path = path
                continue

            if isinstance(inp.get("old_str"), str) and isinstance(inp.get("new_str"), str):
                # str_replace usually names its path; a handful of calls in real exports
                # omit it, meaning the file the conversation was last working on.
                target = path if isinstance(path, str) else last_path
                record = files.get(target)
                if record is None:
                    continue
                _apply_edit(record, inp["old_str"], inp["new_str"])
                last_path = target

    for record in files.values():
        record["complete"] = record["unmatched"] == 0
    return files


def _replay_artifact(files, inp: dict, msg=None):
    """Apply one artifacts call. Every revision carries the whole body, bar `update`."""
    command = inp.get("command")
    key = inp.get("id") or inp.get("title") or "artifact"
    key = f"artifact:{key}"
    content = inp.get("content")
    record = files.get(key)

    if isinstance(content, str) and command in (None, "create", "rewrite", "update"):
        if record is None:
            files[key] = {
                "name": _artifact_filename(inp, content), "source": inp.get("title") or key,
                "origin": "artifact", "content": content,
                "applied": 0, "unmatched": 0, "message": msg,
            }
        else:
            record["content"] = content
        return

    if record is not None and command == "update":
        old, new = inp.get("old_str"), inp.get("new_str")
        if isinstance(old, str) and isinstance(new, str):
            _apply_edit(record, old, new)


def _apply_edit(record, old: str, new: str):
    """Replace one occurrence, or record that the file had moved out from under the edit."""
    if old and record["content"].count(old) == 1:
        record["content"] = record["content"].replace(old, new, 1)
        record["applied"] += 1
    else:
        record["unmatched"] += 1


def write_conversation_files(records, directory: Path) -> int:
    """Write the reconstructed files, plus a manifest naming where each came from.

    Files are written under their base name, so the manifest carries the full source path
    — two directories in one conversation can hold the same base name, and the allocator
    disambiguates that on disk without recording which was which.
    """
    if not records:
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    allocator = NameAllocator(directory, reserved=("_manifest.json",))
    manifest = []
    for record in records.values():
        out_path = allocator.allocate(safe_filename(record["name"]) or "file")
        out_path.write_text(record["content"], encoding="utf-8", errors="backslashreplace")
        record["written"] = out_path.name
        manifest.append({
            "file": out_path.name,
            "source": record["source"],
            "origin": record["origin"],
            "characters": len(record["content"]),
            "edits_applied": record["applied"],
            "edits_unmatched": record["unmatched"],
            "complete": record["complete"],
        })
    (directory / "_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return len(manifest)


def _file_marker(record, folder: str) -> str:
    """One line in the transcript pointing at the file this message produced."""
    marker = f"> [File written: {record['written']} → {folder}/{record['written']}]"
    if record["complete"]:
        return marker
    return (marker + f"\n> [Reconstruction incomplete: {record['unmatched']} of "
            f"{record['applied'] + record['unmatched']} edits could not be applied — this "
            f"file was also changed outside the recorded tool calls, so what is written "
            f"here is the last state the transcript can account for.]")


def _conv_key(conv):
    """Identity for a conversation. Falls back to object identity if the export omits a UUID."""
    return conv.get("uuid") or id(conv)


def unfiled_conversations(index, conversations):
    """Return conversations that no project claimed, by any strategy.

    A conversation a project guessed at is not unfiled — it already has a home, however
    tentative, and writing it to both places would double-count it. What is left over is
    genuinely unaccounted for, which can mean any of:

      * a standalone chat that never belonged to a project
      * a chat from a project deleted since, so the mapping points at a project the export
        does not contain
      * a chat created after the mapping was fetched

    The export does not distinguish these, so neither does this function.

    Requires a mapping — keyword matching alone lets one conversation match several projects
    and leaves most of them matched by nothing, so "unfiled" carries no information without
    an exact join to measure against.
    """
    claimed = {_conv_key(conv) for entry in index for conv in entry["matched_conversations"]}
    return [conv for conv in conversations if _conv_key(conv) not in claimed]


def strategy_counts(index):
    """Count distinct conversations claimed by each strategy.

    Distinct, because two uncovered projects can guess at the same conversation. The exact
    and fuzzy sets never overlap — see the fuzzy_pool note in build_project_index — so these
    counts plus the unfiled count add up to the export's conversation total.
    """
    return {
        name: len({_conv_key(conv) for entry in index if entry["strategy"] == name
                   for conv in entry["matched_conversations"]})
        for name in ("exact", "fuzzy")
    }


def extract_unfiled(conversations, output_dir: Path, include_thinking: bool = False,
                    faithful: bool = False):
    """Write every unfiled conversation into a single bucket directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {"conversations": 0, "convs_msgs": 0, "files": 0}
    names = NameAllocator(output_dir)
    for conv in conversations:
        stats["convs_msgs"] += write_conversation(
            conv, names, output_dir / "attachments",
            thinking_dir=(output_dir / "thinking") if include_thinking else None,
            raw_dir=(output_dir / "raw" / "conversations") if faithful else None,
            faithful=faithful, files_root=output_dir / "files", counters=stats)
        stats["conversations"] += 1

    return stats


def _render_tool_calls(msg) -> list:
    """Render tool activity that the transcript otherwise reduces to artifact bodies.

    A tool_use block names a tool and carries its input; a tool_result carries what came
    back, whether it failed, and often a display_content the UI showed instead of the raw
    result. None of that reaches the transcript, so a conversation driven by tool calls
    reads as though it happened by magic.
    """
    raw = msg.get("content")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    lines = []
    for block in raw:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name") or "(unnamed tool)"
            where = block.get("integration_name")
            lines.append(f"> **Tool call — {name}**" + (f" _via {where}_" if where else ""))
            note = block.get("message")
            if note:
                lines.append(f"> {note}")
            inp = block.get("input")
            if isinstance(inp, dict):
                # Bodies are omitted rather than repeated: the artifact body is already in
                # the transcript, and a written file's body is in files/ under its own
                # name. Rendering file_text here would inline a whole document as escaped
                # JSON and then cut it at the limit below — which is how 1.3M characters
                # of written files used to read as a truncated blob.
                shown = {k: v for k, v in inp.items()
                         if k not in ("content", "file_text")}
                if "file_text" in inp:
                    # Plain ASCII on purpose: this value goes through json.dumps, which
                    # escapes anything else to \uXXXX and would render as line noise.
                    shown["file_text"] = f"<{len(str(inp['file_text']))} characters, written to files/>"
                if shown:
                    lines.append("> ```json")
                    dumped = json.dumps(shown, indent=2)
                    for row in dumped[:2000].splitlines():
                        lines.append(f"> {row}")
                    if len(dumped) > 2000:
                        lines.append(f"> … truncated, {len(dumped) - 2000} more characters")
                    lines.append("> ```")
            lines.append("")
        elif btype == "tool_result":
            name = block.get("name") or "(unnamed tool)"
            failed = " — **error**" if block.get("is_error") else ""
            lines.append(f"> **Tool result — {name}**{failed}")
            for key in ("message", "display_content"):
                value = block.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    lines.append(f"> {text[:2000]}")
                    if len(text) > 2000:
                        lines.append(f"> … truncated, {len(text) - 2000} more characters")
            lines.append("")
    return lines


def _render_citations(msg) -> list:
    """Render the sources attached to text blocks, which the transcript drops entirely."""
    raw = msg.get("content")
    if not isinstance(raw, list):
        return []
    seen, lines = set(), []
    for block in raw:
        if not isinstance(block, dict):
            continue
        for cite in (block.get("citations") or []):
            if not isinstance(cite, dict):
                continue
            url = cite.get("url") or cite.get("uri") or ""
            title = cite.get("title") or cite.get("source") or url or "source"
            key = (title, url)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"> [{title}]({url})" if url else f"> {title}")
    return (["> **Sources**"] + lines + [""]) if lines else []


def _thinking_summaries(block) -> list:
    """The condensed reasoning a thinking block carries alongside its full text."""
    out = []
    for item in (block.get("summaries") or []):
        if isinstance(item, dict):
            text = (item.get("summary") or "").strip()
        else:
            text = str(item).strip()
        if text:
            out.append(text)
    return out


def _extract_thinking(msg) -> str:
    """Return the reasoning text a message carries, or "" if it carries none.

    Recent exports interleave `thinking` blocks with the reply text. The transcript writer
    ignores them, so on one real export 3.3M characters of reasoning — against 4.2M of
    reply — never reached the output at all. A block can be present but empty, or marked
    hidden with its text withheld; neither is worth a section of its own, so both are
    dropped here rather than producing an empty heading.
    """
    raw = msg.get("content")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return ""

    parts = []
    for block in raw:
        if not isinstance(block, dict) or block.get("type") != "thinking":
            continue
        text = (block.get("thinking") or "").strip()
        if text:
            parts.append(text)
    return "\n\n---\n\n".join(parts)


def _extract_message_content(msg) -> str:
    """Extract text content from a message, handling multiple schema shapes."""
    raw = msg.get("content") or msg.get("text") or ""

    if isinstance(raw, str):
        return raw

    # A single content block, unwrapped. Treat it as a one-element list rather than
    # falling through every branch and returning nothing.
    if isinstance(raw, dict):
        raw = [raw]

    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_result":
                    inner_content = block.get("content", [])
                    # Documented as a list of blocks, but a bare string is the shape a
                    # simple tool returns; iterating that yields characters, not text.
                    if isinstance(inner_content, str):
                        parts.append(inner_content)
                        inner_content = []
                    elif isinstance(inner_content, dict):
                        inner_content = [inner_content]
                    for inner in inner_content:
                        if isinstance(inner, dict) and inner.get("type") == "text":
                            parts.append(inner.get("text", ""))
                elif btype == "tool_use":
                    inp = block.get("input", {})
                    if isinstance(inp, dict) and "content" in inp:
                        title = inp.get("title", "untitled")
                        parts.append(f"\n[Artifact: {title}]\n{inp['content']}")
        return "\n".join(parts)

    return ""


# ── Display ───────────────────────────────────────────────────────────────────

def print_project_list(index, show_strategy: bool = False):
    """Print a numbered list of projects.

    The match-strategy column only appears when a mapping is in play; without one every
    project is matched the same way and the column carries no information.
    """
    header = [f"{'#':>3}", f"{'Project Name':<50}", f"{'Docs':>5}", f"{'Convos':>6}"]
    if show_strategy:
        header.append(f"{'Match':>6}")
    header += [f"{'Size':>8}", f"{'Created':>10}"]
    print("\n" + "  ".join(header))
    print("─" * (103 if show_strategy else 95))
    for i, entry in enumerate(index, 1):
        name = entry["name"][:48]
        size = f"{entry['total_kb']:.0f} KB" if entry["total_kb"] < 1024 else f"{entry['total_kb']/1024:.1f} MB"
        row = [f"{i:>3}", f"{name:<50}", f"{entry['doc_count']:>5}", f"{entry['conv_count']:>6}"]
        if show_strategy:
            row.append(f"{entry['strategy']:>6}")
        row += [f"{size:>8}", f"{entry['created']:>10}"]
        print("  ".join(row))
    print(f"\nTotal: {len(index)} projects")


def print_json_index(index, show_strategy: bool = False):
    """Print machine-readable JSON index for Claude Code skill automation."""
    output = []
    for i, entry in enumerate(index, 1):
        record = {
            "number": i,
            "name": entry["name"],
            "uuid": entry["uuid"],
            "created": entry["created"],
            "description": entry["description"],
            "doc_count": entry["doc_count"],
            "conv_count": entry["conv_count"],
            "total_kb": round(entry["total_kb"], 1),
            "has_prompt": bool(entry["prompt_template"]),
        }
        if show_strategy:
            record["strategy"] = entry["strategy"]
        output.append(record)
    print(json.dumps(output, indent=2))


# ── Interactive mode ──────────────────────────────────────────────────────────

def prompt(message: str) -> str:
    """input() that treats end-of-input or Ctrl-C as a cancellation, not a traceback.

    Interactive mode is reachable by accident — a piped invocation, a CI job, an empty
    --extract — so reading from a closed stdin has to end the run politely.
    """
    try:
        return input(message)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(0)


def default_output_dir(entry, counters: dict) -> Path:
    """Default directory for a project, disambiguated when two projects share a name.

    Project names are not unique — "Untitled", a re-created project, a renamed duplicate —
    and deriving the directory from the name alone silently merges them into one folder.
    """
    base = safe_name(entry["name"])
    counter = counters.get(base, 0)
    counters[base] = counter + 1
    return Path.cwd() / (base if counter == 0 else f"{base}_{counter + 1}")


def assert_distinct_dirs(plan):
    """Refuse to extract two different projects into the same directory."""
    by_dir = {}
    for entry, out_dir in plan:
        resolved = Path(out_dir).expanduser().resolve()
        clash = by_dir.get(resolved)
        if clash is not None and clash["uuid"] != entry["uuid"]:
            print(f"ERROR: '{clash['name']}' and '{entry['name']}' would both extract to "
                  f"{resolved}. Give them separate output directories.", file=sys.stderr)
            sys.exit(1)
        by_dir[resolved] = entry


def extract_or_exit(entry, out_dir: Path, record_strategy: bool = False,
                    include_thinking: bool = False, faithful: bool = False):
    """Extract one project, turning filesystem refusals into a one-line error."""
    try:
        return extract_project(entry, out_dir, record_strategy, include_thinking, faithful)
    except OSError as exc:
        print(f"ERROR: Cannot write to {out_dir}: {exc}", file=sys.stderr)
        sys.exit(1)


def interactive_mode(index, show_strategy: bool = False, include_thinking: bool = False,
                     faithful: bool = False):
    """Run interactive project selection and extraction."""
    print_project_list(index, show_strategy)

    print("\nEnter project numbers to extract (comma-separated, e.g. '1,3,5')")
    print("Or 'all' to extract everything, or 'q' to quit:")
    choice = prompt("> ").strip()

    if choice.lower() in ("q", "quit", "exit"):
        print("Cancelled.")
        return False

    if choice.lower() == "all":
        selected = list(range(len(index)))
    else:
        try:
            selected = [int(x.strip()) - 1 for x in choice.split(",")]
            for s in selected:
                if s < 0 or s >= len(index):
                    print(f"Invalid number: {s+1}")
                    return False
        except ValueError:
            print("Invalid input. Enter numbers separated by commas.")
            return False

    # Ask for output directories
    extractions = []
    default_names = {}
    for idx in selected:
        entry = index[idx]
        default_dir = default_output_dir(entry, default_names)
        print(f"\nOutput directory for '{entry['name']}'?")
        print(f"  [Enter] for default: {default_dir}")
        dir_input = prompt("  > ").strip()
        out_dir = Path(dir_input) if dir_input else default_dir
        extractions.append((entry, out_dir))
    assert_distinct_dirs(extractions)

    # Confirm
    print("\n── Extraction Plan ──")
    for entry, out_dir in extractions:
        print(f"  {entry['name']}")
        print(f"    -> {out_dir}")
        matched_by = f" ({entry['strategy']} match)" if show_strategy else ""
        print(f"    {entry['doc_count']} docs, {entry['conv_count']} conversations{matched_by}")
    print()
    confirm = prompt("Proceed? [Y/n] ").strip()
    if confirm.lower() in ("n", "no"):
        print("Cancelled.")
        return False

    # Extract
    for entry, out_dir in extractions:
        print(f"\nExtracting: {entry['name']} -> {out_dir}")
        stats = extract_or_exit(entry, out_dir, show_strategy, include_thinking, faithful)
        print(f"  {stats['docs']} docs ({stats['docs_kb']:.0f} KB)")
        print(f"  {stats['conversations']} conversations ({stats['convs_msgs']} messages)")
        if stats.get("files"):
            print(f"  {stats['files']} files Claude produced")

    # The first directory, so a caller wanting somewhere to put run-level files has one.
    return extractions[0][1]


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract specific projects from a Claude.ai data export ZIP."
    )
    parser.add_argument("zip_path", help="Path to the Claude.ai export ZIP file")
    parser.add_argument("--json", action="store_true",
                        help="Print project list as JSON (for automation)")
    parser.add_argument("--extract", type=str, default=None,
                        help="Comma-separated project numbers to extract (non-interactive)")
    parser.add_argument("--output", type=str, default=None,
                        help="Comma-separated output directories (one per project)")
    parser.add_argument("--mapping", type=str, default=None,
                        help="Path to a conversation-to-project mapping file produced by "
                             "fetch_mapping.js. Joins conversations to projects by UUID "
                             "instead of guessing from names.")
    parser.add_argument("--fuzzy", action="store_true",
                        help="Fall back to keyword matching for projects the mapping does not "
                             "cover. Without --mapping, keyword matching is used regardless.")
    parser.add_argument("--faithful", action="store_true",
                        help="Lose nothing. Implies --thinking, adds the parts of a message the "
                             "transcript drops (which tool ran and what it returned, cited "
                             "sources, the conversation's own summary, attached file names), "
                             "and writes every source record verbatim to raw/ so that fields "
                             "this tool does not render — including ones added in future — "
                             "survive extraction.")
    parser.add_argument("--thinking", action="store_true",
                        help="Also write Claude's reasoning to a thinking/ folder beside the "
                             "conversations, one file per conversation, same filenames. Omitted "
                             "by default: on one real export it was 3.3 MB against 4.2 MB of "
                             "reply text.")
    parser.add_argument("--unfiled", type=str, default=None, metavar="DIR",
                        help="Also write conversations that belong to no project into DIR, "
                             "e.g. ./_unfiled. Requires --mapping.")

    # A lone UTF-16 surrogate in a project or conversation name would otherwise abort the
    # run on the first print, before anything is written. JSON allows them, so survive them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    args = parser.parse_args()
    zip_path = Path(args.zip_path)

    if not zip_path.exists():
        print(f"ERROR: File not found: {zip_path}", file=sys.stderr)
        sys.exit(1)

    if args.unfiled and not args.mapping:
        print("ERROR: --unfiled requires --mapping — without an exact join there is no way to "
              "tell which conversations are unfiled.", file=sys.stderr)
        sys.exit(1)

    if args.fuzzy and not args.mapping:
        print("NOTE: --fuzzy has no effect without --mapping; keyword matching is already the "
              "default.", file=sys.stderr)

    mapping = None
    if args.mapping:
        try:
            mapping = load_mapping(Path(args.mapping))
        except MappingError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"Loading: {zip_path}", file=sys.stderr if args.json else sys.stdout)
    try:
        projects, conversations = load_export(zip_path)
    except zipfile.BadZipFile:
        print(f"ERROR: Not a ZIP file (or the download is corrupt): {zip_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"ERROR: The export contains invalid JSON ({zip_path}): {exc}", file=sys.stderr)
        sys.exit(1)
    except RecursionError:
        print(f"ERROR: The export's JSON is nested too deeply to parse: {zip_path}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR: Could not read {zip_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if mapping:
        stale = mapping_staleness(mapping, conversations)
        if stale:
            fetched, newest = (dt.astimezone(timezone.utc) for dt in stale)
            print(f"WARNING: mapping was fetched {fetched:%Y-%m-%d %H:%M UTC} but the export has "
                  f"conversation activity up to {newest:%Y-%m-%d %H:%M UTC}. Anything filed since "
                  f"the fetch will look unmapped — re-run fetch_mapping.js for a current mapping.",
                  file=sys.stderr)

    index = build_project_index(projects, conversations, mapping=mapping,
                                allow_fuzzy=(mapping is None or args.fuzzy))
    print(f"Found {len(index)} projects, {len(conversations)} conversations",
          file=sys.stderr if args.json else sys.stdout)

    unfiled = []
    if mapping:
        unfiled = unfiled_conversations(index, conversations)
        counts = strategy_counts(index)
        print(f"Mapping: {counts['exact']} conversations filed by UUID, "
              f"{counts['fuzzy']} guessed by keyword, {len(unfiled)} unfiled",
              file=sys.stderr if args.json else sys.stdout)

    # JSON mode — machine-readable output for Claude Code
    if args.json:
        print_json_index(index, show_strategy=mapping is not None)
        return

    # Non-interactive mode — extract specified projects
    # `is not None`, so that --extract "" is an error rather than a silent fall-through
    # into interactive mode, which then dies on a closed stdin.
    if args.extract is not None:
        if not args.extract.strip():
            print("ERROR: --extract needs at least one project number", file=sys.stderr)
            sys.exit(1)
        try:
            nums = [int(x.strip()) - 1 for x in args.extract.split(",")]
        except ValueError:
            print(f"ERROR: --extract takes comma-separated project numbers, got: "
                  f"{args.extract!r}", file=sys.stderr)
            sys.exit(1)

        dirs = args.output.split(",") if args.output else [None] * len(nums)

        if len(dirs) != len(nums):
            print("ERROR: --output must have the same number of paths as --extract", file=sys.stderr)
            sys.exit(1)

        plan = []
        default_names = {}
        for i, num in enumerate(nums):
            if num < 0 or num >= len(index):
                print(f"ERROR: Invalid project number: {num+1}", file=sys.stderr)
                sys.exit(1)
            entry = index[num]
            out_dir = (Path(dirs[i].strip()) if dirs[i]
                       else default_output_dir(entry, default_names))
            plan.append((entry, out_dir))
        assert_distinct_dirs(plan)

        if args.faithful:
            # The unfiled bucket when there is one, otherwise the first project's directory,
            # resolved default included. Account files are not project data, so one copy.
            copy_account_files(zip_path, Path(args.unfiled) if args.unfiled else plan[0][1])

        for entry, out_dir in plan:
            print(f"\nExtracting: {entry['name']} -> {out_dir}")
            stats = extract_or_exit(entry, out_dir, mapping is not None,
                                    args.thinking or args.faithful, args.faithful)
            print(f"  {stats['docs']} docs ({stats['docs_kb']:.0f} KB)")
            print(f"  {stats['conversations']} conversations ({stats['convs_msgs']} messages)")
            if stats.get("files"):
                print(f"  {stats['files']} files Claude produced")

        _extract_unfiled(args.unfiled, unfiled, args.thinking or args.faithful, args.faithful)
        print("\nDone!")
        return

    # Interactive mode
    chosen = interactive_mode(index, show_strategy=mapping is not None,
                              include_thinking=args.thinking or args.faithful,
                              faithful=args.faithful)
    if chosen:
        if args.faithful:
            copy_account_files(zip_path, Path(args.unfiled) if args.unfiled else chosen)
        _extract_unfiled(args.unfiled, unfiled, args.thinking or args.faithful, args.faithful)
        print("\nDone!")


def copy_account_files(zip_path: Path, destination: Path):
    """Carry across the archive's account-level files, once.

    Called only after an extraction plan resolves, so the destination is a directory that
    is actually being written to — including a default one derived from a project name.
    Doing it earlier meant a --json listing wrote files to disk, and a run using default
    directories carried nothing at all, which are the two commonest ways to invoke this.
    """
    account = load_account_files(zip_path)
    if not account:
        return
    target = Path(destination) / "raw" / "account"
    target.mkdir(parents=True, exist_ok=True)
    for name, blob in account.items():
        (target / name).write_bytes(blob)
    print(f"\nAccount files -> {target}")
    print(f"  {', '.join(sorted(account))}")


def _extract_unfiled(unfiled_dir, unfiled, include_thinking: bool = False,
                     faithful: bool = False):
    """Write the unfiled bucket, if one was asked for."""
    if not unfiled_dir:
        return
    out_dir = Path(unfiled_dir)
    print(f"\nExtracting unfiled conversations -> {out_dir}")
    stats = extract_unfiled(unfiled, out_dir, include_thinking, faithful)
    print(f"  {stats['conversations']} conversations ({stats['convs_msgs']} messages)")
    if stats.get("files"):
        print(f"  {stats['files']} files Claude produced")


if __name__ == "__main__":
    main()
