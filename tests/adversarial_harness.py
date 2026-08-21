#!/usr/bin/env python3
"""Adversarial harness for claude_export_extractor.py.

    python tests/adversarial_harness.py

Each probe builds a hostile-but-plausible export ZIP in a temp dir, runs the
extractor as a subprocess exactly as a user would, and reports:
  CRASH    - unhandled traceback shown to the user
  DATALOSS - output silently missing or overwriting user content
  WEIRD    - questionable behaviour worth a look
  OK       - handled acceptably

Informational, not pass/fail: it documents how the extractor behaves on the
inputs behind the reports in known-issues/. As fixes land, probes flip to OK.
Self-contained: no framework, no fixtures on disk, no dependencies.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "claude_export_extractor.py"
WORK = Path(tempfile.mkdtemp(prefix="cpe-adversarial-"))

findings = []


def report(kind, name, detail=""):
    findings.append((kind, name, detail))
    print(f"[{kind:8}] {name}" + (f"\n           {detail}" if detail else ""))


def fresh(name):
    d = WORK / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def make_zip(d, projects=None, conversations=None, raw_entries=None):
    zp = d / "export.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        if raw_entries:
            for arcname, data in raw_entries.items():
                zf.writestr(arcname, data)
        else:
            zf.writestr("projects.json", json.dumps(projects or []))
            zf.writestr("conversations.json", json.dumps(conversations or []))
    return zp


def run(*args, stdin=None, timeout=120):
    return subprocess.run([sys.executable, str(EXTRACTOR), *map(str, args)],
                          capture_output=True, text=True, input=stdin, timeout=timeout)


def crashed(proc):
    return proc.returncode != 0 and "Traceback" in proc.stderr


def proj(name, docs=None, uuid="p-1", **kw):
    return {"uuid": uuid, "name": name, "created_at": "2026-01-01T00:00:00Z",
            "description": "", "prompt_template": "", "docs": docs or [], **kw}


def doc(filename, content="x" * 60):
    return {"uuid": "d", "filename": filename, "content": content}


def conv(name, uuid="c-1", messages=None):
    return {"uuid": uuid, "name": name, "created_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "chat_messages": messages if messages is not None else
            [{"sender": "human", "text": "hello", "created_at": "2026-01-02T00:00:00Z"}]}


# ── 1. Malformed archive / JSON ───────────────────────────────────────────────

def probe_corrupt_json():
    d = fresh("corrupt_json")
    zp = make_zip(d, raw_entries={"projects.json": "{not json", "conversations.json": "[]"})
    p = run(zp, "--json")
    if crashed(p):
        report("CRASH", "corrupt projects.json -> raw traceback",
               p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "corrupt projects.json handled", p.stderr.strip()[:100])


def probe_not_a_zip():
    d = fresh("not_a_zip")
    f = d / "export.zip"
    f.write_text("this is not a zip")
    p = run(f, "--json")
    if crashed(p):
        report("CRASH", "non-zip file -> raw traceback", p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "non-zip file handled", p.stderr.strip()[:100])


def probe_bom():
    d = fresh("bom")
    body = json.dumps([proj("Alpha")]).encode()
    zp = make_zip(d, raw_entries={"projects.json": b"\xef\xbb\xbf" + body,
                                  "conversations.json": "[]"})
    p = run(zp, "--json")
    if crashed(p):
        report("CRASH", "UTF-8 BOM in projects.json -> raw traceback",
               p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "UTF-8 BOM handled")


def probe_missing_conversations_file():
    d = fresh("no_conv_file")
    zp = make_zip(d, raw_entries={"projects.json": json.dumps([proj("Alpha")])})
    p = run(zp, "--json")
    if crashed(p):
        report("CRASH", "missing conversations.json", p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "missing conversations.json handled")


# ── 2. Filename hazards -> silent overwrite / loss ────────────────────────────

def probe_doc_safe_name_collision():
    d = fresh("doc_collide")
    docs = [doc("a/b.txt", "FIRST " * 20), doc("a\\b.txt", "SECOND " * 20)]
    zp = make_zip(d, [proj("Alpha", docs)], [])
    out = d / "out"
    p = run(zp, "--extract", "1", "--output", out)
    files = sorted(f.name for f in (out / "project_knowledge").glob("*.txt"))
    if len(files) < 2:
        content = (out / "project_knowledge" / files[0]).read_text() if files else ""
        who = "SECOND survived, FIRST silently overwritten" if "SECOND" in content else "?"
        report("DATALOSS", "two docs sanitize to the same filename -> one file",
               f"docs reported: 2, files written: {len(files)} ({who}); stdout says "
               f"{[l for l in p.stdout.splitlines() if 'docs' in l]}")
    else:
        report("OK", "colliding doc names disambiguated")


def probe_doc_truncation_collision():
    d = fresh("doc_trunc")
    long_a = "report_" + "x" * 90 + "_v1.txt"
    long_b = "report_" + "x" * 90 + "_v2.txt"
    docs = [doc(long_a, "VERSION-ONE " * 10), doc(long_b, "VERSION-TWO " * 10)]
    zp = make_zip(d, [proj("Alpha", docs)], [])
    out = d / "out"
    run(zp, "--extract", "1", "--output", out)
    files = [f for f in (out / "project_knowledge").iterdir() if not f.name.startswith("_")]
    if len(files) < 2:
        report("DATALOSS", "80-char truncation collides two distinct docs -> one file",
               f"files written: {[f.name for f in files]}")
    else:
        report("OK", "truncated doc names disambiguated")


def probe_doc_same_name_diff_content():
    d = fresh("doc_dupname")
    docs = [doc("notes.txt", "OLD DRAFT " * 10), doc("notes.txt", "FINAL VERSION " * 10)]
    zp = make_zip(d, [proj("Alpha", docs)], [])
    out = d / "out"
    run(zp, "--extract", "1", "--output", out)
    # What matters is that neither document is lost, not which one keeps the plain name:
    # disambiguating into notes.txt + notes_1.txt is a better answer than overwriting.
    written = "\n".join(f.read_text() for f in (out / "project_knowledge").glob("*.txt"))
    missing = [tag for tag in ("OLD DRAFT", "FINAL VERSION") if tag not in written]
    if missing:
        report("DATALOSS", "duplicate filename with different content -> a doc is dropped",
               f"missing from output: {', '.join(missing)}")
    else:
        report("OK", "duplicate filename with different content -> both docs kept")


def probe_conv_title_collision():
    d = fresh("conv_collide")
    convs = [conv("Chat: one", "c-1"), conv("Chat? one", "c-2")]
    zp = make_zip(d, [proj("Chat one")], convs)
    out = d / "out"
    run(zp, "--extract", "1", "--output", out)
    files = sorted(f.name for f in (out / "conversations").glob("*.md"))
    if len(files) == 2:
        report("OK", "conversation title collision disambiguated", str(files))
    else:
        report("DATALOSS", "conversation title collision", str(files))


def probe_windows_reserved_names():
    # Static check: safe_name does not neutralise Windows reserved device names.
    sys.path.insert(0, str(ROOT))
    import importlib
    m = importlib.import_module("claude_export_extractor")
    bad = [n for n in ("CON", "PRN", "NUL", "COM1", "LPT1", "con.txt")
           if m.safe_name(n).upper().split(".")[0] in
           ("CON", "PRN", "NUL", "COM1", "LPT1")]
    if bad:
        report("WEIRD", "Windows reserved device names pass through safe_name",
               f"{bad} -> unwritable/aliased files on Windows (README targets Windows users)")
    else:
        report("OK", "Windows reserved names neutralised")


# ── 3. Content hazards ────────────────────────────────────────────────────────

def probe_lone_surrogate():
    d = fresh("surrogate")
    docs = [{"uuid": "d", "filename": "notes.txt", "content": "x" * 60}]
    zp_raw = {"projects.json":
              '[{"uuid":"p-1","name":"Alpha","created_at":"2026-01-01T00:00:00Z",'
              '"description":"","prompt_template":"","docs":'
              '[{"uuid":"d","filename":"notes.txt","content":"bad \\ud83d escape %s"}]}]'
              % ("x" * 60),
              "conversations.json": "[]"}
    zp = make_zip(d, raw_entries=zp_raw)
    out = d / "out"
    p = run(zp, "--extract", "1", "--output", out)
    if crashed(p):
        report("CRASH", "lone UTF-16 surrogate in doc content -> traceback mid-extraction",
               p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "lone surrogate handled", p.stdout.strip()[-60:])


def probe_tool_result_string_content():
    d = fresh("tool_result_str")
    msg = {"sender": "assistant", "created_at": "2026-01-02T00:00:00Z",
           "content": [{"type": "tool_result",
                        "content": "IMPORTANT RESULT TEXT that should survive"}]}
    zp = make_zip(d, [proj("Alpha")], [conv("Alpha planning", "c-1", [msg])])
    out = d / "out"
    p = run(zp, "--extract", "1", "--output", out)
    md = next((out / "conversations").glob("*.md")).read_text()
    if "IMPORTANT RESULT TEXT" not in md:
        report("DATALOSS", "tool_result with string content -> text silently dropped",
               "_extract_message_content iterates the string, chars aren't dicts, yields nothing")
    else:
        report("OK", "string tool_result preserved")


def probe_dict_content_block():
    d = fresh("dict_content")
    msg = {"sender": "assistant", "created_at": "2026-01-02T00:00:00Z",
           "content": {"type": "text", "text": "DICT-SHAPED CONTENT"}}
    zp = make_zip(d, [proj("Alpha")], [conv("Alpha planning", "c-1", [msg])])
    out = d / "out"
    run(zp, "--extract", "1", "--output", out)
    md = next((out / "conversations").glob("*.md")).read_text()
    if "DICT-SHAPED CONTENT" not in md:
        report("DATALOSS", "dict-shaped message content -> dropped",
               "content that is a single dict (not str/list) returns ''")
    else:
        report("OK", "dict content preserved")


def probe_attachment_shadows_doc():
    d = fresh("att_shadow")
    docs = [doc("data.txt", "KNOWLEDGE DOC CONTENT " * 5)]
    msg = {"sender": "human", "text": "see attached", "created_at": "2026-01-02T00:00:00Z",
           "attachments": [{"file_name": "data.txt",
                            "extracted_content": "ATTACHMENT CONTENT " * 5}]}
    zp = make_zip(d, [proj("Alpha", docs)], [conv("Alpha kickoff", "c-1", [msg])])
    out = d / "out"
    run(zp, "--extract", "1", "--output", out)
    text = (out / "project_knowledge" / "data.txt").read_text()
    if "ATTACHMENT" in text:
        report("DATALOSS", "attachment overwrote knowledge doc of the same name")
    else:
        report("WEIRD", "attachment sharing a doc's filename is silently not saved",
               "exists-check keeps the doc (good) but the attachment text is lost with no note")


# ── 4. Matching quality ───────────────────────────────────────────────────────

def probe_short_name_false_positives():
    d = fresh("short_name")
    convs = [conv(t, f"c-{i}") for i, t in enumerate(
        ["Emails to send", "Maintaining the garden", "Air fryer recipes",
         "Repairing my bike", "Daily journal"])]
    zp = make_zip(d, [proj("AI")], convs)
    p = run(zp, "--json")
    got = json.loads(p.stdout)[0]["conv_count"]
    if got >= 4:
        report("WEIRD", f"2-letter project name 'AI' claims {got}/5 unrelated conversations",
               "substring matching: 'ai' hits Emails, MAIntaining, AIr, repAIring, dAIly")
    else:
        report("OK", "short project name behaves", f"claimed {got}/5")


def probe_skipword_project_name():
    d = fresh("skipword")
    convs = [conv(t, f"c-{i}") for i, t in enumerate(
        ["The new house", "New year plans", "What's new in python"])]
    zp = make_zip(d, [proj("New")], convs)
    p = run(zp, "--json")
    got = json.loads(p.stdout)[0]["conv_count"]
    report("WEIRD" if got == 3 else "OK",
           f"project named 'New' (a skip-word) claims {got}/3 conversations",
           "the full name is always a keyword, so skip-words only apply to multi-word names")


# ── 5. CLI robustness ─────────────────────────────────────────────────────────

def probe_extract_non_numeric():
    d = fresh("cli_nonnum")
    zp = make_zip(d, [proj("Alpha")], [])
    p = run(zp, "--extract", "abc")
    if crashed(p):
        report("CRASH", "--extract abc -> raw ValueError traceback",
               p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "--extract abc rejected cleanly")


def probe_extract_empty():
    d = fresh("cli_empty")
    zp = make_zip(d, [proj("Alpha")], [])
    p = run(zp, "--extract", "")
    if crashed(p):
        report("CRASH", "--extract '' -> raw traceback", p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "--extract '' rejected cleanly")


def probe_output_is_existing_file():
    d = fresh("cli_outfile")
    zp = make_zip(d, [proj("Alpha")], [])
    blocker = d / "blocker"
    blocker.write_text("i am a file")
    p = run(zp, "--extract", "1", "--output", blocker)
    if crashed(p):
        report("CRASH", "--output pointing at an existing file -> raw traceback",
               p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "--output at existing file handled")


def probe_interactive_eof():
    d = fresh("eof")
    zp = make_zip(d, [proj("Alpha")], [])
    p = run(zp, stdin="")  # interactive mode, stdin closes immediately
    if crashed(p):
        report("CRASH", "interactive mode with closed stdin -> EOFError traceback",
               p.stderr.strip().splitlines()[-1])
    else:
        report("OK", "interactive EOF handled")


def probe_duplicate_selection():
    d = fresh("dup_sel")
    zp = make_zip(d, [proj("Alpha")], [conv("Alpha kickoff")])
    out = d / "out"
    p = run(zp, "--extract", "1,1", "--output", f"{out},{out}")
    files = sorted(f.name for f in (out / "conversations").glob("*.md"))
    if len(files) > 1:
        report("WEIRD", "--extract 1,1 to the same dir duplicates every conversation",
               f"{files} — the exists-counter treats the rerun as new conversations")
    else:
        report("OK", "duplicate selection idempotent")


def probe_same_project_names_default_dirs():
    d = fresh("same_names")
    projects = [proj("Untitled", [doc("a.txt", "FROM PROJECT ONE " * 5)], uuid="p-1"),
                proj("Untitled", [doc("a.txt", "FROM PROJECT TWO " * 5)], uuid="p-2")]
    zp = make_zip(d, projects, [])
    p = subprocess.run([sys.executable, str(EXTRACTOR), str(zp),
                        "--extract", "1,2", "--output", ","],
                       capture_output=True, text=True, cwd=d)
    merged = d / "Untitled"
    meta = json.loads((merged / "project_knowledge" / "_project_metadata.json").read_text()) \
        if (merged / "project_knowledge" / "_project_metadata.json").exists() else {}
    a = (merged / "project_knowledge" / "a.txt")
    detail = f"one dir, metadata uuid={meta.get('uuid')}, a.txt from " + \
             ("TWO" if a.exists() and "TWO" in a.read_text() else "ONE")
    dirs = [x.name for x in d.iterdir() if x.is_dir()]
    if len(dirs) == 1:
        report("DATALOSS", "two projects with the same name merge into one default dir", detail)
    else:
        report("OK", "same-named projects get distinct dirs", str(dirs))


def main():
    WORK.mkdir(exist_ok=True)
    order = [probe_corrupt_json, probe_not_a_zip, probe_bom, probe_missing_conversations_file,
             probe_doc_safe_name_collision, probe_doc_truncation_collision,
             probe_doc_same_name_diff_content, probe_conv_title_collision,
             probe_windows_reserved_names, probe_lone_surrogate,
             probe_tool_result_string_content, probe_dict_content_block,
             probe_attachment_shadows_doc, probe_short_name_false_positives,
             probe_skipword_project_name, probe_extract_non_numeric, probe_extract_empty,
             probe_output_is_existing_file, probe_interactive_eof, probe_duplicate_selection,
             probe_same_project_names_default_dirs]
    for f in order:
        try:
            f()
        except Exception as exc:
            report("HARNESS", f"{f.__name__} itself failed", repr(exc))

    print("\n── Summary ──")
    for kind in ("CRASH", "DATALOSS", "WEIRD", "HARNESS"):
        hits = [n for k, n, _ in findings if k == kind]
        if hits:
            print(f"{kind}: {len(hits)}")
            for h in hits:
                print(f"  - {h}")
    print(f"OK: {sum(1 for k, _, _ in findings if k == 'OK')}")
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
