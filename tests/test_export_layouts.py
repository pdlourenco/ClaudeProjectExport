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
        "uuid": "019c85ae-f2b7-7777-8f5a-2b416ed16ce8",
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
        "uuid": "019c874c-fc40-729b-abdb-342cf6268398",
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
        "uuid": "019c8967-f21d-723c-8623-56bec3207569",
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
