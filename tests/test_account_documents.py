#!/usr/bin/env python3
"""
Reflections, memory, and the transcript parts the October 2026 export added.

    python tests/test_account_documents.py

The October 2026 export introduced a reflections/ folder (its manifest calls the category
"feedback"), memory documents returned inside tool results, platform-injected prompt blocks,
and image galleries. None of that reached the readable output: it survived only as raw JSON,
and only under --faithful. These checks build a small synthetic export containing each shape
and confirm it is rendered, tolerated when incomplete, and never silently dropped.

Self-contained: no framework, no fixtures on disk, no dependencies. Every name and passage
below is invented.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "claude_export_extractor.py"

PROJECT = "a0000000-0000-4000-8000-000000000001"
CONV = "c0000000-0000-4000-8000-000000000001"
ACCOUNT = "u0000000-0000-4000-8000-000000000001"
MEMORY_PATH = f"/projects/{PROJECT}/working-notes.md"
MEMORY_BODY = "## Habits\n\n- Prefers short answers\n\n```python\nprint('fenced')\n```\n"

passes, failures, skipped = [], [], []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    (passes if ok else failures).append(name)


def skip(name, detail):
    print(f"  [SKIP] {name} — {detail}")
    skipped.append(name)


def message(uuid, sender, text, blocks):
    return {"uuid": uuid, "sender": sender, "text": text, "content": blocks,
            "created_at": "2026-09-30T09:00:00Z", "updated_at": "2026-09-30T09:00:00Z",
            "attachments": [], "files": []}


CONVERSATION = {
    "uuid": CONV, "name": "Invented chat", "summary": "",
    "created_at": "2026-09-30T09:00:00Z", "updated_at": "2026-09-30T09:30:00Z",
    "account": {"uuid": ACCOUNT},
    "chat_messages": [
        message("m1", "human", "Remind me how I like answers.", [
            {"type": "injected_prompt_block", "injection_source": "date_note",
             "prompt": "\n\nThe current date is Wednesday, September 30, 2026."},
            {"type": "text", "text": "Remind me how I like answers."},
        ]),
        message("m2", "assistant", "Checking.", [
            {"type": "tool_use", "id": "t1", "name": "memory_read",
             "input": {"path": [MEMORY_PATH, "/areas/other.md"]}},
            {"type": "tool_result", "tool_use_id": "t1", "name": "memory_read",
             "message": "Recalled 1 memory", "is_error": False,
             "content": [{"type": "text", "text": "=== note ==="}],
             "structured_content": {"documents": [{
                 "path": MEMORY_PATH, "version": "abc123def456",
                 "updated_at": "2026-09-11T08:28:30Z",
                 "parsed": {"name": "working-notes", "description": "How the user works",
                            "body": MEMORY_BODY}}]}},
            {"type": "tool_use", "id": "t2", "name": "web_search_fast",
             "input": {"query": "invented query"}},
            {"type": "tool_result", "tool_use_id": "t2", "name": "image_search",
             "message": "Searched images", "is_error": False,
             "content": [{"type": "image_gallery",
                          "images": [{"title": "A [bracketed] title", "url": "https://img.example/1.png",
                                      "page_url": "https://page.example/1", "source": "page.example"}],
                          "spare_images": [{"url": "https://img.example/2.png"}]}]},
            {"type": "future_block", "payload": {"anything": 1}},
            {"type": "text", "text": "Short answers, as noted."},
        ]),
    ],
}

REFLECTION = {
    "account_uuid": ACCOUNT,
    "reflections": [{
        "period": "2026-08", "created_at": "2026-09-02T21:45:00Z", "updated_at": "2026-09-02T21:45:00Z",
        "content": {
            "period": "August 2026", "hero_title": "A month of invented things",
            "hero_body": "Most conversations circled one theme.",
            "stats": [{"label": "Conversations", "n": "14", "sublabel": "on 11 days"}],
            "topics": [{"title": "Gardening", "percent": 60, "description": "Beds and seeds"}],
            "about_your_time": [{"title": "Planting came first", "body": "Four chats about beds."}],
            "expanding_your_skills": [{"title": "Delegating research", "skill": "delegation",
                                       "body": "Lookups went to Claude."}],
            "a_future_section": [{"title": "Unexpected", "body": "Added by a later export."}],
        }}],
    "feedback": [],
}

# The same reflection with most of its content gone, which a thin month could produce.
REFLECTION_THIN = {"reflections": [{"period": "2026-09", "content": {"hero_title": "Quiet month"}}],
                   "feedback": []}

MEMORIES = {
    "conversations_memory": "The user keeps a vegetable garden.",
    "project_memories": {PROJECT: "**Purpose**\n\nTrack the garden.", "p-empty": "   "},
    "memory_files": [
        {"path": MEMORY_PATH, "content": MEMORY_BODY, "updated_at": "2026-09-11T08:28:30Z"},
        {"path": "/areas/../../escape.md", "content": "stays inside", "updated_at": "2026-09-01T00:00:00Z"},
        {"path": "/areas/dup.md", "content": "first", "updated_at": "2026-09-01T00:00:00Z"},
        {"path": "/areas/dup.md", "content": "second", "updated_at": "2026-09-02T00:00:00Z"},
    ],
    "account_uuid": ACCOUNT,
}


def project_record():
    return {"uuid": PROJECT, "name": "Garden", "description": "", "created_at": "2026-02-22T10:00:00Z",
            "updated_at": "2026-02-23T10:00:00Z", "prompt_template": "", "is_private": True,
            "is_starter_project": False, "docs": []}


def build_zip(path: Path, reflection=None, extra=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"projects/{PROJECT}.json", json.dumps(project_record()))
        zf.writestr("conversations.json", json.dumps([CONVERSATION]))
        zf.writestr(f"memories/{ACCOUNT}.json", json.dumps(MEMORIES))
        zf.writestr(f"reflections/{ACCOUNT}.json", json.dumps(REFLECTION if reflection is None else reflection))
        zf.writestr("users.json", json.dumps([{"uuid": ACCOUNT}]))
        zf.writestr("login_history.json", json.dumps({"logins": []}))
        for name, body in extra:
            zf.writestr(name, body)
    return path


def write_mapping(path: Path):
    path.write_text(json.dumps({"schema": 1, "fetched_at": "2026-10-07T00:00:00Z",
                                "org_uuid": "o", "projects": {PROJECT: "Garden"},
                                "conversations": {}}), encoding="utf-8")
    return path


def run(zip_path, out: Path, mapping: Path, *flags):
    proc = subprocess.run(
        [sys.executable, str(EXTRACTOR), str(zip_path), "--mapping", str(mapping),
         "--extract", "1", "--output", str(out / "proj"), "--unfiled", str(out / "unfiled"), *flags],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return proc


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mapping = write_mapping(tmp / "mapping.json")

        print("\nReflections")
        plain = tmp / "plain"
        proc = run(build_zip(tmp / "a.zip"), plain, mapping)
        check("plain run succeeds", proc.returncode == 0, proc.stderr.strip()[:200])
        account = plain / "unfiled" / "account"
        reflection = read(account / "reflections" / "2026-08.md")
        check("rendered without --faithful", bool(reflection))
        check("title, body and period", all(s in reflection for s in (
            "A month of invented things", "Most conversations circled one theme.", "2026-08")))
        check("stats and topics", "**14** Conversations" in reflection and "**Gardening** (60%)" in reflection)
        check("prose sections, with the skill noted",
              "Planting came first" in reflection and "skill: delegation" in reflection)
        check("a section this tool has never heard of is rendered, not dropped",
              "A future section" in reflection and "Added by a later export." in reflection)
        check("an empty feedback list writes no feedback file", not (account / "feedback.md").exists())

        thin = tmp / "thin"
        run(build_zip(tmp / "b.zip", reflection=REFLECTION_THIN), thin, mapping)
        text = read(thin / "unfiled" / "account" / "reflections" / "2026-09.md")
        check("a reflection missing every section still renders", text.startswith("# Quiet month"))
        check("and invents no empty headings", "## " not in text)

        shapes = tmp / "shapes"
        odd_shapes = {"reflections": [{"period": "2026-07", "content": {
            "hero_title": "Shapes", "stats": {"conversations": 9},
            "topics": ["Gardening", "Bees"], "about_your_time": "Prose where a list was.",
            "worth_thinking_about": {"title": "A lone record"}}}], "feedback": []}
        run(build_zip(tmp / "s.zip", reflection=odd_shapes), shapes, mapping)
        text = read(shapes / "unfiled" / "account" / "reflections" / "2026-07.md")
        check("stats in an unexpected shape are shown", '"conversations": 9' in text)
        check("topics given as plain strings are shown", "- Gardening" in text and "- Bees" in text)
        check("a prose section given as a string is shown",
              "## About your time" in text and "Prose where a list was." in text)
        check("a prose section given as one record is shown", "A lone record" in text)

        filled = tmp / "filled"
        item = {"rating": "up", "note": "invented"}
        run(build_zip(tmp / "c.zip", reflection={**REFLECTION, "feedback": [item]}), filled, mapping)
        text = read(filled / "unfiled" / "account" / "feedback.md")
        check("non-empty feedback is written as what it is", '"rating": "up"' in text)

        print("\nMemory")
        memory = account / "memory"
        check("conversations memory", "vegetable garden" in read(memory / "conversations_memory.md"))
        check("project memory is named for the project, and says which",
              "Track the garden." in read(memory / "project_memories" / "Garden.md")
              and PROJECT in read(memory / "project_memories" / "Garden.md"))
        check("a blank project memory is not written",
              sorted(p.name for p in (memory / "project_memories").iterdir()) == ["Garden.md"])
        doc = memory / "documents" / "projects" / PROJECT / "working-notes.md"
        check("memory files keep their paths", read(doc) == MEMORY_BODY)
        check("a path that climbs out stays inside",
              (memory / "documents" / "areas" / "escape.md").exists()
              and not (tmp / "escape.md").exists() and not (plain / "escape.md").exists())
        check("two files at one path both survive",
              {read(memory / "documents" / "areas" / n) for n in ("dup.md", "dup_1.md")} == {"first", "second"})
        check("an index lists them", "/areas/dup.md" in read(memory / "_index.md"))

        print("\nSeveral account files")
        second_reflection = {"reflections": [{"period": "2026-08", "content": {"hero_title": "Same month, other file"}}],
                             "feedback": [{"rating": "down"}]}
        second_memory = {"conversations_memory": "The user also keeps bees.",
                         "project_memories": {PROJECT: "A second note on the garden."},
                         "memory_files": [{"path": "/areas/dup.md", "content": "third",
                                           "updated_at": "2026-09-03T00:00:00Z"}]}
        several = tmp / "several"
        proc = run(build_zip(tmp / "h.zip", reflection={**REFLECTION, "feedback": [{"rating": "up"}]},
                             extra=[("reflections/other.json", json.dumps(second_reflection)),
                                    ("memories/other.json", json.dumps(second_memory))]),
                   several, mapping)
        acc = several / "unfiled" / "account"
        check("feedback from both files is kept",
              all(s in read(acc / "feedback.md") for s in ('"up"', '"down"')), proc.stderr.strip()[:200])
        check("two reflections for one month both survive",
              {read(acc / "reflections" / n).partition("\n")[0] for n in ("2026-08.md", "2026-08_1.md")}
              == {"# A month of invented things", "# Same month, other file"})
        summary = read(acc / "memory" / "conversations_memory.md")
        check("both conversations summaries are kept", "vegetable garden" in summary and "bees" in summary)
        check("both notes on one project are kept",
              {"Track the garden." in read(acc / "memory" / "project_memories" / n) or
               "A second note" in read(acc / "memory" / "project_memories" / n)
               for n in ("Garden.md", "Garden_1.md")} == {True})
        check("memory files from both files share one folder without overwriting",
              {read(acc / "memory" / "documents" / "areas" / n) for n in ("dup.md", "dup_1.md", "dup_2.md")}
              == {"first", "second", "third"})
        check("the index lists documents from both files",
              read(acc / "memory" / "_index.md").count("/areas/dup.md") == 3)

        print("\nA memory path that is another's folder")
        nested = {"memory_files": [
            {"path": "/notes", "content": "file first"},
            {"path": "/notes/inner.md", "content": "folder second"},
            {"path": "/later/inner.md", "content": "folder first"},
            {"path": "/later", "content": "file second"},
        ]}
        clash = tmp / "clash"
        proc = run(build_zip(tmp / "k.zip", extra=[("memories/nested.json", json.dumps(nested))]), clash, mapping)
        check("the run completes", proc.returncode == 0, proc.stderr.strip()[-200:])
        docs = clash / "unfiled" / "account" / "memory" / "documents"
        check("a file named like a folder is written beside it",
              read(docs / "notes" / "inner.md") == "folder second" and read(docs / "notes_1") == "file first")
        check("whichever order they arrive in",
              read(docs / "later" / "inner.md") == "folder first" and read(docs / "later_1") == "file second")

        blocked = tmp / "blocked"
        (blocked / "unfiled").mkdir(parents=True)
        (blocked / "unfiled" / "account").write_text("in the way", encoding="utf-8")
        proc = run(build_zip(tmp / "l.zip"), blocked, mapping)
        check("an account directory that cannot be written is a warning, not the end of the run",
              proc.returncode == 0 and "WARNING: Could not write the account files" in proc.stderr
              and (blocked / "proj" / "project_knowledge" / "_project_metadata.json").exists(), proc.stderr.strip()[-200:])

        print("\nWithout --unfiled")
        def run_into_project(out, *flags):
            return subprocess.run(
                [sys.executable, str(EXTRACTOR), str(tmp / "a.zip"), "--extract", "1",
                 "--output", str(out), *flags],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        alone = tmp / "alone"
        proc = run_into_project(alone)
        check("a project folder gets none of the account's memory by default",
              proc.returncode == 0 and not (alone / "account").exists(), proc.stderr.strip()[-160:])
        check("and the note says how to get it", "--unfiled DIR" in proc.stderr)
        everything = tmp / "everything"
        run_into_project(everything, "--faithful")
        check("--faithful still writes it beside the first project",
              "vegetable garden" in read(everything / "account" / "memory" / "conversations_memory.md"))

        print("\nMemory records in odd shapes")
        odd_records = {"memory_files": [
            {"path": ["a", "list"], "content": "list path", "updated_at": 5},
            {"path": 42, "content": "number path"},
            {"path": "/two\nlines|piped.md", "content": "newline path", "updated_at": "bad\ndate"},
        ]}
        shaped = tmp / "shaped"
        proc = run(build_zip(tmp / "m.zip", extra=[("memories/odd.json", json.dumps(odd_records))]), shaped, mapping)
        check("a path that is not a string does not end the run", proc.returncode == 0, proc.stderr.strip()[-200:])
        rows = [r for r in read(shaped / "unfiled" / "account" / "memory" / "_index.md").splitlines()
                if r.startswith("| ") and "Path" not in r]
        check("every document gets exactly one index row, newlines and pipes notwithstanding",
              len(rows) == 4 + 3 and all(r.count(" | ") == 2 for r in rows), "\n".join(rows))

        print("\nThe older memories.json layout")
        legacy = tmp / "legacy"
        with zipfile.ZipFile(tmp / "i.zip", "w") as zf:
            zf.writestr(f"projects/{PROJECT}.json", json.dumps(project_record()))
            zf.writestr("conversations.json", json.dumps([CONVERSATION]))
            zf.writestr("memories.json", json.dumps([{"conversations_memory": "An older summary.",
                                                       "project_memories": {PROJECT: "Older note."}}]))
        proc = run(tmp / "i.zip", legacy, mapping)
        memory_dir = legacy / "unfiled" / "account" / "memory"
        check("a list of memory records is rendered",
              "An older summary." in read(memory_dir / "conversations_memory.md")
              and "Older note." in read(memory_dir / "project_memories" / "Garden.md"))
        check("and is not reported as unreadable", "memories.json" not in proc.stderr, proc.stderr.strip()[:160])
        with zipfile.ZipFile(tmp / "j.zip", "w") as zf:
            zf.writestr(f"projects/{PROJECT}.json", json.dumps(project_record()))
            zf.writestr("conversations.json", json.dumps([CONVERSATION]))
            zf.writestr("memories.json", json.dumps([]))
        proc = run(tmp / "j.zip", tmp / "empty", mapping)
        check("an empty memories.json is not reported either", "memories.json" not in proc.stderr,
              proc.stderr.strip()[:160])

        print("\nAccount files with nothing to read, and ones with no renderer")
        check("users.json and login_history.json are not rendered",
              not any("users" in p.name or "login" in p.name for p in account.rglob("*")))
        odd = tmp / "odd"
        proc = run(build_zip(tmp / "d.zip", extra=[("billing/invoices.json", json.dumps({"rows": [1]}))]),
                   odd, mapping)
        check("an unrecognised account file is named in a note", "invoices.json" in proc.stderr, proc.stderr.strip()[:160])
        check("and is not rendered", not list((odd / "unfiled" / "account").rglob("invoices*")))
        kept = tmp / "kept"
        proc = run(build_zip(tmp / "e.zip", extra=[("billing/invoices.json", json.dumps({"rows": [1]}))]),
                   kept, mapping, "--faithful")
        check("under --faithful it is kept verbatim",
              list((kept / "unfiled" / "raw" / "account").rglob("invoices.json")) != [])
        check("and the note says so rather than asking for --faithful",
              "invoices.json" in proc.stderr and "Pass --faithful" not in proc.stderr
              and "kept verbatim under raw/account/" in proc.stderr, proc.stderr.strip()[-160:])

        print("\nTranscripts")
        faithful = tmp / "faithful"
        run(build_zip(tmp / "f.zip"), faithful, mapping, "--faithful")
        transcript = read(faithful / "unfiled" / "Invented chat.md")
        check("a memory read shows its document, with path and version",
              f"**Document — {MEMORY_PATH}**" in transcript and "abc123def456" in transcript)
        check("and the document's body", "Prefers short answers" in transcript)
        check("a fence in the body cannot close the quote early",
              "````markdown" in transcript and "print('fenced')" in transcript)
        check("a list-valued input.path renders", f'"{MEMORY_PATH}"' in transcript and "/areas/other.md" in transcript)
        check("a tool nothing special-cases renders generically", "Tool call — web_search_fast" in transcript)
        check("an injected prompt is labelled by its source",
              "**Injected prompt — date_note**" in transcript and "September 30, 2026" in transcript)
        check("image galleries list title, page and source",
              "[A \\[bracketed\\] title](https://page.example/1) — page.example" in transcript)
        check("spare images are listed too", "**Also returned**" in transcript and "https://img.example/2.png" in transcript)
        check("a block type with no renderer is shown, not dropped",
              "**Block — future_block**" in transcript and '"anything": 1' in transcript)
        quiet = read(plain / "unfiled" / "Invented chat.md")
        check("a plain transcript carries none of it",
              not any(s in quiet for s in ("Injected prompt", "Document —", "Block —", "Tool result")))

        print("\nLong paths")
        if sys.platform != "win32":
            skip("an output directory past 260 characters", "only Windows has that limit")
        else:
            deep = tmp / "deep"
            while len(str(deep)) < 250:
                deep = deep / ("nested-directory-" + "x" * 20)
            try:
                proc = run(build_zip(tmp / "g.zip"), deep, mapping)
                check("extraction completes instead of aborting", proc.returncode == 0,
                      proc.stderr.strip()[:200])
                target = deep / "unfiled" / "account" / "reflections" / "2026-08.md"
                check("and the file is there",
                      len(str(target)) > 260 and Path("\\\\?\\" + str(target)).exists())
            finally:
                # Neither rmtree nor the temporary directory's own cleanup can delete a path
                # past the limit unless it is handed the extended form.
                shutil.rmtree("\\\\?\\" + str(tmp / "deep"), ignore_errors=True)

    print(f"\n{len(passes)} passed, {len(failures)} failed" + (f", {len(skipped)} skipped" if skipped else ""))
    if failures:
        print("FAILED: " + "; ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
