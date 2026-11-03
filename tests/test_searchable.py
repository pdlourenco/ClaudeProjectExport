#!/usr/bin/env python3
"""
--searchable: conversations written for tools that index and filter them.

    python tests/test_searchable.py

The flag changes three things about a transcript and nothing else: the header becomes YAML
front matter, the conversation's own summary is printed under it, and each message heading
carries a snippet of the message. This checks each of those, that titles which are hostile
to YAML survive, that headings stay one line whatever the message holds, and that without
the flag the output is exactly what it was.

Self-contained: no framework, no fixtures on disk, no dependencies.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "claude_export_extractor.py"

PROJECT = "a0000000-0000-4000-8000-000000000001"
FILED = "c0000000-0000-4000-8000-000000000001"
LOOSE = "c0000000-0000-4000-8000-000000000002"
HOSTILE_TITLE = 'Why: a "quoted" thing & #hash — 好 \\ done'
SUMMARY = "**Conversation overview**\n\nFirst paragraph of the summary.\n\nSecond paragraph, after a blank line."
LONG = ("This opening sentence is deliberately much longer than the snippet allows so that it has "
        "to be cut at a word boundary somewhere before the end.")

passes, failures = [], []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    (passes if ok else failures).append(name)


def load_module():
    spec = importlib.util.spec_from_file_location("extractor", EXTRACTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def message(sender, text, at, content=None):
    return {"uuid": f"m-{at}", "sender": sender, "text": text, "created_at": at,
            "content": content if content is not None else [{"type": "text", "text": text}],
            "attachments": [], "files": []}


def conversations():
    return [
        {"uuid": FILED, "name": HOSTILE_TITLE, "summary": SUMMARY,
         "created_at": "2026-05-01T08:00:00.123456Z", "updated_at": "2026-05-02T09:30:00Z",
         "account": {"uuid": "u"}, "chat_messages": [
             message("human", "## Plants\n- which **herbs** grow in [shade](http://x.invalid)?", "2026-05-01T08:00:01Z"),
             message("assistant", LONG, "2026-05-01T08:00:30Z"),
             message("human", "```python\nprint(1)\n```\nthen explain it", "2026-05-01T08:01:00Z"),
             message("assistant", "", "2026-05-01T08:01:30Z", content=[]),
             message("human", "<div>**bold** `code` <b>tag</b></div>", "2026-05-01T08:02:00Z"),
             message("assistant", "---\n\n***\n", "2026-05-01T08:02:30Z"),
         ]},
        {"uuid": LOOSE, "name": "", "summary": None,
         "created_at": "2026-06-01T10:00:00Z", "updated_at": "2026-06-01T10:00:00Z",
         "account": {"uuid": "u"}, "chat_messages": [
             message("human", "just one line", "2026-06-01T10:00:01Z")]},
    ]


def build(tmp: Path):
    export = tmp / "export"
    export.mkdir()
    project = {"uuid": PROJECT, "name": 'Garden: "plants" & herbs', "description": "",
               "created_at": "2026-02-22T10:00:00Z", "updated_at": "2026-02-23T10:00:00Z",
               "prompt_template": "", "is_private": True, "is_starter_project": False, "docs": []}
    with zipfile.ZipFile(export / "export.zip", "w") as zf:
        zf.writestr("projects.json", json.dumps([project]))
        zf.writestr("conversations.json", json.dumps(conversations()))
    mapping = tmp / "mapping.json"
    mapping.write_text(json.dumps({
        "schema": 1, "fetched_at": "2026-11-03T08:00:00Z", "org_uuid": "o",
        "projects": {PROJECT: project["name"]},
        "conversations": {FILED: {"project_uuid": PROJECT, "project_name": project["name"]}}}),
        encoding="utf-8")
    return export / "export.zip", mapping


def extract(zip_path, mapping, out: Path, *flags):
    command = [sys.executable, "-I", str(EXTRACTOR), str(zip_path), "--extract", "1",
               "--output", str(out / "proj"), "--unfiled", str(out / "unfiled"),
               "--mapping", str(mapping), *flags]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def only_md(directory: Path) -> Path:
    found = sorted(p for p in directory.glob("*.md"))
    assert len(found) == 1, found
    return found[0]


def split_front_matter(text: str):
    lines = text.replace("\r\n", "\n").split("\n")
    assert lines[0] == "---", lines[:3]
    end = lines.index("---", 1)
    meta = {}
    for line in lines[1:end]:
        key, _, value = line.partition(": ")
        meta[key] = value
    return meta, lines[end + 1:]


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    mod = load_module()

    print("Snippets")
    snip = mod._heading_snippet
    check("a short line is returned whole", snip("just one line") == "just one line")
    check("heading, list and quote markers are dropped", snip("## Plants") == "Plants"
          and snip("- an item") == "an item" and snip("> quoted words") == "quoted words"
          and snip("3. third item") == "third item")
    check("a link keeps its text, not its address", snip("see [the docs](http://x.invalid/a) now") == "see the docs now")
    check("emphasis, code marks and tags are removed", snip("<div>**bold** `code` <b>tag</b></div>") == "bold code tag")
    check("a code fence is skipped for the first real line", snip("```py\nprint(1)\n```\nthen explain") == "then explain")
    check("rules and empty lines are skipped", snip("---\n\n***\n\nreal words") == "real words")
    check("nothing but rules leaves no snippet", snip("---\n***\n") == "" and snip("") == "" and snip(None) == "")
    cut = snip(LONG)
    check("a long line is cut at a word, with an ellipsis", cut.endswith("…") and len(cut) <= 71
          and LONG.startswith(cut[:-1]) and LONG[len(cut) - 1] == " ", cut)
    check("a word longer than the limit is cut hard rather than lost", snip("x" * 200).endswith("…")
          and len(snip("x" * 200)) == 71)
    check("the snippet is never more than one line", "\n" not in snip("a\nb") and "|" not in snip("a | b"))

    print("\nFront matter")
    fm = mod._front_matter
    block = fm([("title", HOSTILE_TITLE), ("id", "x"), ("project", None), ("created", "2026-05-01T08:00:00Z"),
                ("messages", 6)])
    check("it opens and closes with rules and leaves out empty values",
          block[0] == "---" and block[-1] == "---\n" and not any(l.startswith("project") for l in block))
    check("a hostile title is a quoted scalar that round-trips",
          json.loads(block[1].split(": ", 1)[1]) == HOSTILE_TITLE, block[1])
    check("dates and counts are written bare", "created: 2026-05-01T08:00:00Z" in block and "messages: 6" in block)
    check("an unparseable date gives nothing rather than junk", mod.ts_iso("garbage") == ""
          and mod.ts_iso("2026-05-01T08:00:00.123456Z") == "2026-05-01T08:00:00Z"
          and mod.ts_iso("2026-05-01T10:00:00+02:00") == "2026-05-01T08:00:00Z")
    check("a block quote carries the marker on every line", mod._quote_block("a\n\nb") == ["> a", ">", "> b", ""])

    with tempfile.TemporaryDirectory() as tmpname:
        tmp = Path(tmpname)
        zip_path, mapping = build(tmp)

        print("\nWithout the flag")
        plain = extract(zip_path, mapping, tmp / "plain")
        check("the run completes", plain.returncode == 0, plain.stderr.strip()[:200])
        plain_text = only_md(tmp / "plain" / "proj" / "conversations").read_text(encoding="utf-8")
        check("the header is still the bullet list", "- **ID:** " + FILED in plain_text
              and not plain_text.startswith("---"))
        check("message headings are unchanged", "### HUMAN  _2026-05-01 08:00 UTC_" in plain_text)
        check("and the summary is still left out", "First paragraph of the summary" not in plain_text)

        print("\nWith --searchable")
        run = extract(zip_path, mapping, tmp / "srch", "--searchable")
        check("the run completes", run.returncode == 0, run.stderr.strip()[:200])
        text = only_md(tmp / "srch" / "proj" / "conversations").read_text(encoding="utf-8")
        meta, rest = split_front_matter(text)
        check("title, id, project, dates and count are in the front matter",
              json.loads(meta["title"]) == HOSTILE_TITLE and json.loads(meta["id"]) == FILED
              and json.loads(meta["project"]) == 'Garden: "plants" & herbs'
              and meta["created"] == "2026-05-01T08:00:00Z" and meta["updated"] == "2026-05-02T09:30:00Z"
              and meta["messages"] == "6", str(meta)[:300])
        check("the old bullet header is gone", "- **ID:**" not in text)
        check("the title is still the first heading", rest[1] == f"# {HOSTILE_TITLE}")
        quoted = [l for l in rest if l.startswith(">")]
        check("the whole summary is quoted, blank lines included",
              quoted[:5] == ["> **Conversation overview**", ">", "> First paragraph of the summary.", ">",
                             "> Second paragraph, after a blank line."], str(quoted[:5]))
        headings = [l for l in rest if re.match(r"^### [A-Z]+ ", l)]
        check("every message has one heading", len(headings) == 6, str(len(headings)))
        check("a list item's heading names the item, not the markup",
              headings[0] == "### HUMAN · 2026-05-01 08:00 UTC — Plants", headings[0])
        check("a long message is cut at a word", headings[1].endswith("…") and len(headings[1]) < 130, headings[1])
        check("a code fence does not become the label", headings[2].endswith("— then explain it"), headings[2])
        check("a message with no text keeps a bare heading", headings[3] == "### ASSISTANT · 2026-05-01 08:01 UTC",
              headings[3])
        check("tags and emphasis are stripped from the label", headings[4].endswith("— bold code tag"), headings[4])
        check("a message of only rules keeps a bare heading", headings[5] == "### ASSISTANT · 2026-05-01 08:02 UTC",
              headings[5])

        def normalised(lines, searchable):
            out, seen = [], False
            pattern = re.compile(r"^### ([A-Z]+) · (\d{4}-\d\d-\d\d \d\d:\d\d UTC)(?: — .*)?$") if searchable \
                else re.compile(r"^### ([A-Z]+)  _(.*)_$")
            for line in lines:
                m = pattern.match(line)
                if m:
                    seen = True
                    line = f"### {m.group(1)} {m.group(2)}"
                if seen:
                    out.append(line)
            return out
        check("below the headings the transcript is exactly what it was",
              normalised(rest, True) == normalised(plain_text.replace("\r\n", "\n").split("\n"), False))

        loose = only_md(tmp / "srch" / "unfiled")
        loose_meta, _ = split_front_matter(loose.read_text(encoding="utf-8"))
        check("an unfiled conversation has no project and falls back to Untitled",
              "project" not in loose_meta and json.loads(loose_meta["title"]) == "Untitled" and loose.name == "Untitled.md",
              str(loose_meta))

        print("\nWith --faithful as well")
        both = extract(zip_path, mapping, tmp / "both", "--searchable", "--faithful")
        check("the run completes", both.returncode == 0, both.stderr.strip()[:200])
        both_text = only_md(tmp / "both" / "proj" / "conversations").read_text(encoding="utf-8")
        check("the summary is printed once, in the quoted form", both_text.count("First paragraph of the summary") == 1
              and "> Second paragraph, after a blank line." in both_text)
        check("the source records are still kept verbatim",
              (tmp / "both" / "proj" / "raw" / "conversations").is_dir())

    print(f"\n{len(passes)} passed, {len(failures)} failed")
    if failures:
        print("FAILED: " + "; ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
