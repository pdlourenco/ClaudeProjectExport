#!/usr/bin/env python3
"""
Both export layouts must load every project.

    python tests/test_export_layouts.py

Claude.ai has shipped two export layouts. The older one puts every project in a single
projects.json; the current one writes one file per project under projects/<uuid>.json.
This builds the same three projects in both layouts and checks that they load identically
and completely — the per-file layout used to yield exactly one project, whichever the
archive happened to list first.

Self-contained: no framework, no fixtures on disk, no dependencies.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "claude_export_extractor.py"

PROJECTS = [
    {
        "uuid": "a0000000-0000-4000-8000-000000000001",
        "name": "First Project",
        "description": "Has a knowledge doc.",
        "created_at": "2026-02-22T10:00:00Z",
        "updated_at": "2026-02-23T10:00:00Z",
        "prompt_template": "",
        "is_private": True,
        "is_starter_project": False,
        "docs": [{"uuid": "d1", "filename": "notes.md",
                  "content": "# Notes\n", "created_at": "2026-02-22T10:05:00Z"}],
    },
    {
        "uuid": "a0000000-0000-4000-8000-000000000002",
        "name": "Second Project",
        "description": "",
        "created_at": "2026-03-01T10:00:00Z",
        "updated_at": "2026-03-02T10:00:00Z",
        "prompt_template": "",
        "is_private": True,
        "is_starter_project": False,
        "docs": [],
    },
    {
        "uuid": "a0000000-0000-4000-8000-000000000003",
        "name": "Third Project",
        "description": "",
        "created_at": "2026-03-10T10:00:00Z",
        "updated_at": "2026-03-11T10:00:00Z",
        "prompt_template": "Be brief.\n",
        "is_private": True,
        "is_starter_project": False,
        "docs": [],
    },
]

CONVERSATIONS = [
    {
        "uuid": "c0000000-0000-4000-8000-000000000001",
        "name": "First Project kickoff",
        "created_at": "2026-02-24T09:00:00Z",
        "updated_at": "2026-02-24T09:30:00Z",
        "account": {"uuid": "u1"},
        "summary": "",
        "chat_messages": [
            {"uuid": "m1", "sender": "human", "text": "Hello", "content": [],
             "created_at": "2026-02-24T09:00:00Z", "updated_at": "2026-02-24T09:00:00Z",
             "attachments": [], "files": []},
        ],
    },
]

passes = []
failures = []
skipped = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    (passes if ok else failures).append(name)


def skip(name, detail):
    print(f"  [SKIP] {name} — {detail}")
    skipped.append(name)


def write_legacy(path: Path):
    """The older layout: one projects.json holding every project."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("projects.json", json.dumps(PROJECTS, indent=2))
        zf.writestr("conversations.json", json.dumps(CONVERSATIONS, indent=2))
        zf.writestr("users.json", json.dumps([{"uuid": "u1"}]))
    return path


def write_split(path: Path):
    """The current layout: projects/<uuid>.json, one project per file."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("users.json", json.dumps([{"uuid": "u1"}]))
        for proj in PROJECTS:
            zf.writestr(f"projects/{proj['uuid']}.json", json.dumps(proj, indent=2))
        zf.writestr("conversations.json", json.dumps(CONVERSATIONS, indent=2))
        zf.writestr("memories.json", json.dumps([]))
        zf.writestr("login_history.json", json.dumps([]))
    return path


def write_nested(path: Path, prefix="conversations-with-claude/unprocessed/", reverse=False):
    """The current layout nested under a folder whose own name contains "conversation".

    Real exports now arrive this way, which defeats any classification that substring-matches
    the whole path: every entry, project files included, then contains "conversation".
    `reverse` writes the same bytes in the opposite archive order, which is equally legal and
    used to decide which file was read as the conversation list.
    """
    entries = [("users.json", json.dumps([{"uuid": "u1"}])),
               ("conversations.json", json.dumps(CONVERSATIONS, indent=2)),
               ("login_history.json", json.dumps([])),
               ("memories/acct-1.json", json.dumps([]))]
    entries += [(f"projects/{proj['uuid']}.json", json.dumps(proj, indent=2))
                for proj in PROJECTS]
    if reverse:
        entries.reverse()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries:
            zf.writestr(prefix + name, body)
    return path


def write_per_conversation(path: Path):
    """A conversations/<uuid>.json layout, the shape projects already moved to."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("users.json", json.dumps([{"uuid": "u1"}]))
        for proj in PROJECTS:
            zf.writestr(f"projects/{proj['uuid']}.json", json.dumps(proj, indent=2))
        for conv in CONVERSATIONS:
            zf.writestr(f"conversations/{conv['uuid']}.json", json.dumps(conv, indent=2))
    return path


def write_colliding_account(path: Path, reverse=False):
    """Two account files sharing a base name in different folders."""
    extra = [("memories/a.json", json.dumps({"which": "memories"})),
             ("settings/a.json", json.dumps({"which": "settings"}))]
    if reverse:
        extra.reverse()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for proj in PROJECTS:
            zf.writestr(f"projects/{proj['uuid']}.json", json.dumps(proj, indent=2))
        zf.writestr("conversations.json", json.dumps(CONVERSATIONS, indent=2))
        for name, body in extra:
            zf.writestr(name, body)
    return path


def account_files(zip_path):
    """The account-level files the extractor would carry across with --faithful."""
    spec = importlib.util.spec_from_file_location("cpe", EXTRACTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return sorted(module.load_account_files(Path(zip_path)))


def index(zip_path, script=EXTRACTOR):
    proc = subprocess.run([sys.executable, str(script), str(zip_path), "--json"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"rc={proc.returncode}\n{proc.stderr}")
    return json.loads(proc.stdout)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cpe-layouts-"))
    legacy = index(write_legacy(tmp / "legacy.zip"))
    split = index(write_split(tmp / "split.zip"))

    print("Export layouts")
    check("legacy projects.json loads every project",
          len(legacy) == len(PROJECTS), f"{len(legacy)} of {len(PROJECTS)}")
    check("per-project projects/<uuid>.json loads every project",
          len(split) == len(PROJECTS), f"{len(split)} of {len(PROJECTS)}")
    check("both layouts produce the same index", legacy == split,
          "" if legacy == split else f"{[p['name'] for p in legacy]} vs {[p['name'] for p in split]}")
    check("docs and prompts survive the per-project layout",
          [(p["doc_count"], p["has_prompt"]) for p in split] == [(1, False), (0, False), (0, True)],
          str([(p["doc_count"], p["has_prompt"]) for p in split]))

    # ── The nested layout, and what its folder name broke ────────────────────
    print("\nNested layout")
    nested = index(write_nested(tmp / "nested.zip"))
    reversed_ = index(write_nested(tmp / "nested_rev.zip", reverse=True))
    check("projects load when everything is nested under a 'conversation...' folder",
          len(nested) == len(PROJECTS), f"{len(nested)} of {len(PROJECTS)}")
    check("conversations load there too",
          sum(p["conv_count"] for p in nested) == sum(p["conv_count"] for p in split),
          f"{sum(p['conv_count'] for p in nested)} conversations matched")
    # The old classifier read whichever matching file the archive happened to list first,
    # so the same export in a different order silently produced no conversations at all.
    check("archive order does not decide what gets read", nested == reversed_,
          "same bytes, entries written in the opposite order")
    check("account files survive the nested layout",
          account_files(tmp / "nested.zip") == ["acct-1.json", "login_history.json", "users.json"],
          str(account_files(tmp / "nested.zip")))
    check("a project file is never mistaken for the conversation list",
          all(p["conv_count"] >= 0 for p in nested) and len(nested) == len(PROJECTS))

    collide_a = write_colliding_account(tmp / "acct.zip")
    collide_b = write_colliding_account(tmp / "acct_rev.zip", reverse=True)
    check("account files sharing a base name in different folders both survive",
          account_files(collide_a) == ["memories/a.json", "settings/a.json"],
          str(account_files(collide_a)))
    check("and which one survives is not decided by archive order",
          account_files(collide_a) == account_files(collide_b))

    per_conv = index(write_per_conversation(tmp / "per_conv.zip"))
    check("a conversations/<uuid>.json layout loads every conversation",
          sum(p["conv_count"] for p in per_conv) == sum(p["conv_count"] for p in split),
          f"{sum(p['conv_count'] for p in per_conv)} vs {sum(p['conv_count'] for p in split)}")

    # The fix must be a no-op on the layout that already worked.
    try:
        baseline = tmp / "baseline.py"
        baseline.write_text(
            subprocess.run(["git", "-C", str(ROOT), "show", "main:claude_export_extractor.py"],
                           capture_output=True, text=True, check=True).stdout,
            encoding="utf-8")
        check("legacy layout output unchanged from main",
              index(tmp / "legacy.zip", baseline) == legacy)
    except (OSError, subprocess.CalledProcessError) as exc:
        skip("legacy layout output unchanged from main", str(exc))

    summary = f"\n{len(passes)} passed, {len(failures)} failed"
    print(summary + (f", {len(skipped)} skipped" if skipped else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
