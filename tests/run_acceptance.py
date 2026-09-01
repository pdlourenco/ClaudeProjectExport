#!/usr/bin/env python3
"""
Acceptance checks for the --mapping feature, run against the synthetic fixture.

    python tests/run_acceptance.py

No test framework, no dependencies — same constraints as the tool itself. Each check
prints PASS / FAIL / SKIP and the script exits non-zero if anything failed.

The backwards-compatibility check compares against the extractor as it exists on the
`main` branch, read via `git show`. If git or that branch is unavailable the check is
skipped rather than silently passing.
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "claude_export_extractor.py"
# The backwards-compatibility claim is "no --mapping behaves exactly as it did before the
# mapping feature", so the baseline is pinned to the commit immediately before it: main at
# the point the export-loading and data-loss fixes had landed and nothing else. A branch name
# would stop meaning that the moment this work merges — the check would compare the extractor
# against itself and pass for free. Branch names remain as fallbacks for a shallow clone that
# lacks the commit.
BASELINE_REFS = ("2a3d93947ac3593396b5624e427628257319b925",
                 "claude/fix-silent-data-loss-and-crashes", "main")

STDLIB_ALLOWED = {"zipfile", "json", "re", "sys", "argparse", "pathlib", "datetime", "collections"}

results = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    results.append((name, ok))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail):
    results.append((name, None))
    print(f"  [SKIP] {name} — {detail}")


def run(script, *args, expect_rc=0, stdin="q\n"):
    proc = subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, input=stdin)
    if expect_rc is not None and proc.returncode != expect_rc:
        raise AssertionError(f"rc={proc.returncode} (wanted {expect_rc})\n{proc.stdout}\n{proc.stderr}")
    return proc


def index_json(zip_path, *args):
    proc = run(EXTRACTOR, zip_path, "--json", *args)
    return json.loads(proc.stdout)


def normalise(text, tmp):
    text = text.replace(str(tmp), "TMP")
    return re.sub(r'"extracted_at": "[^"]*"', '"extracted_at": "X"', text)


def tree(root):
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*")) if p.is_file()}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cpe-acceptance-"))
    try:
        sys.path.insert(0, str(ROOT / "tests"))
        import make_fixture

        zip_path, mapping_path = make_fixture.build(tmp / "fixtures")
        print()

        # ── Backwards compatibility ──────────────────────────────────────────
        print("Backwards compatibility")
        baseline = tmp / "baseline_extractor.py"
        ref = None
        for candidate in BASELINE_REFS:
            try:
                src = subprocess.run(
                    ["git", "-C", str(ROOT), "show", f"{candidate}:claude_export_extractor.py"],
                    capture_output=True, text=True, check=True).stdout
            except (OSError, subprocess.CalledProcessError):
                continue
            baseline.write_text(src, encoding="utf-8")
            ref = candidate
            break
        if ref is None:
            baseline = None
            skip("output identical to the base branch without --mapping",
                 f"none of {BASELINE_REFS} could be read")
        else:
            print(f"  (baseline: {ref})")

        if baseline:
            base_json = run(baseline, zip_path, "--json")
            new_json = run(EXTRACTOR, zip_path, "--json")
            check("--json output identical to the base branch",
                  (base_json.stdout, base_json.stderr) == (new_json.stdout, new_json.stderr))

            base_picker = run(baseline, zip_path)
            new_picker = run(EXTRACTOR, zip_path)
            check("interactive picker identical to the base branch", base_picker.stdout == new_picker.stdout)

            base_dir, new_dir = tmp / "base_out", tmp / "new_out"
            base_out = run(baseline, zip_path, "--extract", "1,2,3,4",
                           "--output", ",".join(str(base_dir / f"p{i}") for i in (1, 2, 3, 4)))
            new_out = run(EXTRACTOR, zip_path, "--extract", "1,2,3,4",
                          "--output", ",".join(str(new_dir / f"p{i}") for i in (1, 2, 3, 4)))
            check("extraction stdout identical to the base branch",
                  normalise(base_out.stdout, base_dir) == normalise(new_out.stdout, new_dir))
            base_tree = {k: normalise(v, base_dir) for k, v in tree(base_dir).items()}
            new_tree = {k: normalise(v, new_dir) for k, v in tree(new_dir).items()}
            differing = [k for k in base_tree if base_tree.get(k) != new_tree.get(k)]
            check("extracted files identical to the base branch",
                  base_tree.keys() == new_tree.keys() and not differing,
                  f"{len(base_tree)} files compared" if not differing else f"differ: {differing}")

        # ── Mapping strategies ───────────────────────────────────────────────
        print("\nMapping strategies")
        mapped = {e["name"]: e for e in index_json(zip_path, "--mapping", mapping_path)}
        check("covered projects report exact",
              all(mapped[n]["strategy"] == "exact" for n in ("Marketing Course 2700", "Lean Metadata Project")))
        check("exact join finds a conversation keywords miss",
              mapped["Marketing Course 2700"]["conv_count"] == 3,
              f"conv_count={mapped['Marketing Course 2700']['conv_count']} (keyword matching finds 2)")
        check("uncovered project reports none and extracts nothing",
              mapped["Zebra Analysis"]["strategy"] == "none" and mapped["Zebra Analysis"]["conv_count"] == 0)

        fuzzy = {e["name"]: e for e in index_json(zip_path, "--mapping", mapping_path, "--fuzzy")}
        check("uncovered project falls back to fuzzy with --fuzzy",
              fuzzy["Zebra Analysis"]["strategy"] == "fuzzy" and fuzzy["Zebra Analysis"]["conv_count"] == 1)
        check("fuzzy cannot steal a conversation the mapping files elsewhere",
              fuzzy["Capstone Review"]["conv_count"] == 0,
              "'Capstone Review' keyword-matches a conversation mapped to 'Marketing Course 2700'")
        plain_capstone = next(e for e in index_json(zip_path) if e["name"] == "Capstone Review")
        check("...but still matches it when there is no mapping to defer to",
              plain_capstone["conv_count"] == 1)
        check("--fuzzy does not disturb covered projects",
              all(fuzzy[n]["strategy"] == "exact" for n in ("Marketing Course 2700", "Lean Metadata Project")))

        # A mapping whose "projects" omits a project its own conversations reference must not
        # strand them: the project is uncovered, but its conversations are excluded from the
        # keyword pool because the mapping does file them, so they would reach nothing at all.
        omit = json.loads(mapping_path.read_text(encoding="utf-8"))
        omit["projects"] = {k: v for k, v in omit["projects"].items() if k != make_fixture.P1}
        omit_path = tmp / "omits_a_project.json"
        omit_path.write_text(json.dumps(omit, indent=2), encoding="utf-8")
        patched = {e["name"]: e for e in index_json(zip_path, "--mapping", omit_path, "--fuzzy")}
        check("a project missing from the mapping's 'projects' keeps its mapped conversations",
              patched["Marketing Course 2700"]["strategy"] == "exact"
              and patched["Marketing Course 2700"]["conv_count"] == 3,
              f"{patched['Marketing Course 2700']['conv_count']} convs, "
              f"{patched['Marketing Course 2700']['strategy']}")

        # ...while the reason that key exists still works: a project registered there with no
        # conversations reports an honest exact match of zero rather than looking uncovered.
        empty = json.loads(mapping_path.read_text(encoding="utf-8"))
        empty["projects"][make_fixture.P3] = "Zebra Analysis"
        empty_path = tmp / "zero_conversation_project.json"
        empty_path.write_text(json.dumps(empty, indent=2), encoding="utf-8")
        zeroed = {e["name"]: e for e in index_json(zip_path, "--mapping", empty_path, "--fuzzy")}
        check("a mapped project with no conversations reports exact, not fuzzy",
              zeroed["Zebra Analysis"]["strategy"] == "exact"
              and zeroed["Zebra Analysis"]["conv_count"] == 0)

        plain = index_json(zip_path)
        check("strategy key absent without --mapping",
              all("strategy" not in e for e in plain))

        proc = run(EXTRACTOR, zip_path, "--fuzzy", "--json")
        check("--fuzzy without --mapping says it does nothing",
              "NOTE: --fuzzy has no effect without --mapping" in proc.stderr)

        # ── Lean projects ────────────────────────────────────────────────────
        print("\nLean projects")
        check("metadata-only project reports 0 docs without error",
              mapped["Lean Metadata Project"]["doc_count"] == 0)

        # ── Unfiled bucket ───────────────────────────────────────────────────
        print("\nUnfiled bucket")
        total = len(make_fixture.CONVERSATIONS)

        def extract_all(label, *extra):
            root = tmp / label
            proc = run(EXTRACTOR, zip_path, "--mapping", mapping_path, *extra,
                       "--extract", "1,2,3,4",
                       "--output", ",".join(str(root / f"p{i}") for i in (1, 2, 3, 4)),
                       "--unfiled", root / "_unfiled")
            bucket = sorted(f.name for f in (root / "_unfiled").glob("*.md"))
            claimed = {f.name for i in (1, 2, 3, 4)
                       for f in (root / f"p{i}" / "conversations").glob("*.md")}
            return root, proc, bucket, claimed

        root, proc, bucket, claimed = extract_all("uf_exact")
        check("unmapped conversations land in the unfiled bucket",
              bucket == ["Weeknight dinner ideas.md", "Zebra stripe measurements.md"],
              ", ".join(bucket))
        check("counts reconcile without --fuzzy: 4 + 0 + 2 = 6",
              "4 conversations filed by UUID, 0 guessed by keyword, 2 unfiled" in proc.stdout,
              [l for l in proc.stdout.splitlines() if "unfiled" in l][:1])
        check("exact + guessed + unfiled = total (no --fuzzy)", len(claimed) + len(bucket) == total)

        root_f, proc_f, bucket_f, claimed_f = extract_all("uf_fuzzy", "--fuzzy")
        check("a guessed conversation is NOT also written to the unfiled bucket",
              "Zebra stripe measurements.md" in claimed_f and "Zebra stripe measurements.md" not in bucket_f,
              f"unfiled now: {', '.join(bucket_f)}")
        check("counts reconcile with --fuzzy: 4 + 1 + 1 = 6",
              "4 conversations filed by UUID, 1 guessed by keyword, 1 unfiled" in proc_f.stdout,
              [l for l in proc_f.stdout.splitlines() if "unfiled" in l][:1])
        check("exact + guessed + unfiled = total (with --fuzzy)",
              len(claimed_f) + len(bucket_f) == total, f"{len(claimed_f)} + {len(bucket_f)} = {total}")

        run(EXTRACTOR, zip_path, "--unfiled", tmp / "never", expect_rc=1)
        check("--unfiled without --mapping is a clean error", not (tmp / "never").exists())

        # ── Provenance recorded in the extracted output ──────────────────────
        print("\nProvenance in extracted output")
        meta = json.loads((root_f / "p3" / "project_knowledge" / "_project_metadata.json")
                          .read_text(encoding="utf-8"))
        check("guessed project is marked in its saved metadata",
              meta.get("conversation_match") == "fuzzy", f"conversation_match={meta.get('conversation_match')!r}")
        meta_exact = json.loads((root_f / "p1" / "project_knowledge" / "_project_metadata.json")
                                .read_text(encoding="utf-8"))
        check("exactly-joined project is marked too",
              meta_exact.get("conversation_match") == "exact")

        # ── --extract all ────────────────────────────────────────────────────
        # Interactive mode has always accepted "all" at its prompt. The CLI rejecting it
        # made one word mean "everything" in half the tool and an error in the other.
        print("\n--extract all")
        all_out = tmp / "all_out"
        all_proc = run(EXTRACTOR, zip_path, "--extract", "all", "--output",
                       ",".join(str(all_out / f"p{i}") for i in range(1, 5)))
        check("--extract all takes every project",
              sorted(d.name for d in all_out.iterdir()) == ["p1", "p2", "p3", "p4"],
              f"{sorted(d.name for d in all_out.iterdir()) if all_out.exists() else None}")
        numbered = run(EXTRACTOR, zip_path, "--extract", "1,2,3,4", "--output",
                       ",".join(str(tmp / "num_out" / f"p{i}") for i in range(1, 5)))
        check("it means exactly what listing every number means",
              normalise(all_proc.stdout, all_out) == normalise(numbered.stdout, tmp / "num_out"))

        # Without --output the directories are named after the projects, which is the only
        # way "all" is usable on an export whose project count you don't already know.
        derived = tmp / "derived"
        derived.mkdir()
        # Run from inside an empty directory, since without --output the tool derives the
        # names relative to the working directory.
        subprocess.run([sys.executable, str(EXTRACTOR), str(zip_path), "--extract", "all"],
                       capture_output=True, text=True, cwd=derived, check=True)
        check("--extract all without --output names directories after the projects",
              sorted(d.name for d in derived.iterdir()) ==
              sorted(e["name"].strip() or "Untitled" for e in index_json(zip_path)),
              str(sorted(d.name for d in derived.iterdir())))

        for variant in ("ALL", "  all  "):
            v_out = tmp / f"all_{variant.strip().lower()}_{len(variant)}"
            run(EXTRACTOR, zip_path, "--extract", variant, "--output",
                ",".join(str(v_out / f"p{i}") for i in range(1, 5)))
            check(f"--extract {variant!r} is accepted like 'all'",
                  len(list(v_out.iterdir())) == 4)

        mismatch = run(EXTRACTOR, zip_path, "--extract", "all", "--output", "a,b", expect_rc=1)
        check("a wrong --output count says how many the export needs",
              "2 given, 4 needed" in mismatch.stderr and "Omit --output" in mismatch.stderr,
              mismatch.stderr.strip().splitlines()[-1][:90])
        nonsense = run(EXTRACTOR, zip_path, "--extract", "nonsense", expect_rc=1)
        check("the error for a bad value now mentions 'all'",
              "or 'all'" in nonsense.stderr and "Traceback" not in nonsense.stderr,
              nonsense.stderr.strip().splitlines()[-1][:90])

        empty_zip = tmp / "no_projects.zip"
        with zipfile.ZipFile(empty_zip, "w") as zf:
            zf.writestr("projects.json", json.dumps([]))
            zf.writestr("conversations.json", json.dumps([]))
        none_proc = run(EXTRACTOR, empty_zip, "--extract", "all", expect_rc=1)
        check("--extract all on an export with no projects fails cleanly",
              none_proc.stderr.startswith("ERROR: ") and "Traceback" not in none_proc.stderr,
              none_proc.stderr.strip().splitlines()[-1][:90])

        # ── Error handling ───────────────────────────────────────────────────
        print("\nError handling")
        bad = tmp / "bad"
        bad.mkdir()
        cases = {
            "missing file": bad / "nope.json",
            "invalid JSON": bad / "notjson.json",
            "wrong schema": bad / "schema9.json",
            "no conversations key": bad / "noconvs.json",
            "entry without project_uuid": bad / "nouuid.json",
            "top-level array": bad / "array.json",
        }
        cases["invalid JSON"].write_text("{not json", encoding="utf-8")
        cases["wrong schema"].write_text('{"schema": 9, "conversations": {}}', encoding="utf-8")
        cases["no conversations key"].write_text('{"schema": 1}', encoding="utf-8")
        cases["entry without project_uuid"].write_text(
            '{"schema": 1, "conversations": {"abc": {"project_name": "x"}}}', encoding="utf-8")
        cases["top-level array"].write_text("[]", encoding="utf-8")

        for label, path in cases.items():
            proc = run(EXTRACTOR, zip_path, "--mapping", path, "--json", expect_rc=1)
            clean = proc.stderr.startswith("ERROR: ") and "Traceback" not in proc.stderr
            check(f"clean error: {label}", clean, proc.stderr.strip().splitlines()[0][:90])

        # ── Staleness ────────────────────────────────────────────────────────
        print("\nStaleness")
        stale = json.loads(mapping_path.read_text(encoding="utf-8"))
        stale["fetched_at"] = "2020-01-01T00:00:00Z"
        stale_path = tmp / "stale_mapping.json"
        stale_path.write_text(json.dumps(stale, indent=2), encoding="utf-8")
        proc = run(EXTRACTOR, zip_path, "--mapping", stale_path, "--json")
        check("stale mapping warns and continues", "WARNING:" in proc.stderr and proc.returncode == 0)

        # ── Thinking ─────────────────────────────────────────────────────────
        print("\nThinking")
        plain_dir = tmp / "think_off"
        run(EXTRACTOR, zip_path, "--extract", "1", "--output", plain_dir)
        check("no thinking folder without --thinking", not (plain_dir / "thinking").exists())

        think_dir = tmp / "think_on"
        run(EXTRACTOR, zip_path, "--thinking", "--extract", "1", "--output", think_dir)
        transcripts = sorted(f.name for f in (think_dir / "conversations").glob("*.md"))
        reasoning = sorted(f.name for f in (think_dir / "thinking").glob("*.md"))
        check("reasoning filenames are a subset of the transcript filenames",
              set(reasoning) <= set(transcripts), f"{reasoning}")
        written = "\n".join((think_dir / "thinking" / f).read_text(encoding="utf-8") for f in reasoning)
        check("reasoning text is written", "REASONING ONE" in written and "REASONING TWO" in written)
        check("a message's several blocks are all kept",
              written.count("REASONING") >= 2)
        check("transcripts still carry no reasoning",
              "REASONING" not in "\n".join((think_dir / "conversations" / f).read_text(encoding="utf-8")
                                           for f in transcripts))
        check("a conversation whose only block is empty gets no file",
              "Ideas for the final capstone brief.md" not in reasoning,
              f"{len(reasoning)} of {len(transcripts)} transcripts have reasoning")

        # Two conversations sharing a title are disambiguated once, by the allocator; the
        # reasoning file has to inherit that answer rather than deriving a name of its own.
        collide = tmp / "collide.zip"
        think_block = {"type": "thinking", "thinking": "COLLIDING REASONING"}
        with zipfile.ZipFile(collide, "w") as zf:
            zf.writestr("projects.json", json.dumps([{
                "uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "description": "", "prompt_template": "", "docs": []}]))
            zf.writestr("conversations.json", json.dumps([
                # Same title on purpose: they collide on filename, and the title matches the
                # project so keyword matching attaches them without needing a mapping here.
                {"uuid": f"c{i}", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                 "updated_at": "2026-01-01T00:00:00Z",
                 "chat_messages": [{"uuid": f"m{i}", "sender": "assistant",
                                    "created_at": "2026-01-01T00:00:00Z",
                                    "content": [think_block, {"type": "text", "text": "hi"}]}]}
                for i in range(3)]))
        collide_out = tmp / "collide_out"
        run(EXTRACTOR, collide, "--thinking", "--extract", "1", "--output", collide_out)
        ct = sorted(f.name for f in (collide_out / "conversations").glob("*.md"))
        cr = sorted(f.name for f in (collide_out / "thinking").glob("*.md"))
        check("colliding titles: reasoning filenames track the disambiguated transcripts",
              ct == cr and len(ct) == 3, f"{ct} vs {cr}")

        # ── Files Claude produced ────────────────────────────────────────────
        print("\nFiles Claude produced")

        # Its own export rather than the shared fixture, deliberately. The shared fixture
        # stays free of file-producing tools so the byte-compat guarantee above keeps a
        # sharp meaning: a conversation that produces no files still extracts identically
        # to the baseline. This one exercises every shape that writes a file.
        def tool(name, inp, i):
            return {"type": "tool_use", "name": name, "id": f"t{i}", "input": inp}

        def assistant(i, blocks):
            return {"uuid": f"m{i}", "sender": "assistant",
                    "created_at": "2026-01-01T00:00:00Z", "content": blocks}

        files_zip = tmp / "files.zip"
        with zipfile.ZipFile(files_zip, "w") as zf:
            zf.writestr("projects.json", json.dumps([{
                "uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "description": "", "prompt_template": "", "docs": []}]))
            zf.writestr("conversations.json", json.dumps([{
                "uuid": "c1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "chat_messages": [
                    assistant(1, [
                        {"type": "text", "text": "Writing it."},
                        tool("create_file", {"path": "/mnt/user-data/outputs/notes.md",
                                             "file_text": "# Notes\nalpha\nbravo\n"}, 1)]),
                    # A normal edit, then one that omits its path (real exports contain
                    # both), then one whose old_str is nowhere to be found.
                    assistant(2, [tool("str_replace", {"path": "/mnt/user-data/outputs/notes.md",
                                                       "old_str": "bravo",
                                                       "new_str": "BRAVO"}, 2)]),
                    assistant(3, [tool("str_replace", {"old_str": "alpha",
                                                       "new_str": "ALPHA"}, 3)]),
                    assistant(4, [tool("create_file", {"path": "/tmp/report.md",
                                                       "file_text": "R1\n"}, 4)]),
                    assistant(5, [tool("str_replace", {"path": "/tmp/report.md",
                                                       "old_str": "GONE",
                                                       "new_str": "x"}, 5)]),
                    # An artifact carries its whole body on every revision, so the last
                    # one wins outright rather than being replayed.
                    assistant(6, [tool("artifacts", {"command": "create", "id": "a1",
                                                     "type": "text/markdown",
                                                     "title": "Research Report",
                                                     "content": "v1\n"}, 6)]),
                    assistant(7, [tool("artifacts", {"command": "rewrite", "id": "a1",
                                                     "type": "text/markdown",
                                                     "title": "Research Report",
                                                     "content": "FINAL\n"}, 7)]),
                    assistant(8, [tool("artifacts", {"command": "create", "id": "a2",
                                                     "type": "application/vnd.ant.code",
                                                     "language": "python",
                                                     "title": "helper",
                                                     "content": "print(1)\n"}, 8)]),
                ]}]))

        fout = tmp / "files_out"
        proc = run(EXTRACTOR, files_zip, "--extract", "1", "--output", fout)
        fdir = fout / "files" / "Alpha"
        produced = sorted(f.name for f in fdir.glob("*")) if fdir.exists() else []
        check("files are written without any flag",
              produced == ["Research Report.md", "_manifest.json", "helper.py",
                           "notes.md", "report.md"], f"{produced}")
        check("the extraction says how many files it found",
              "4 files Claude produced" in proc.stdout,
              "4 documents plus the manifest")

        notes = (fdir / "notes.md").read_text(encoding="utf-8")
        check("edits are replayed onto the file they name",
              "BRAVO" in notes and "bravo" not in notes)
        check("an edit that omits its path applies to the file last written",
              "ALPHA" in notes and "alpha" not in notes, notes.replace("\n", " "))
        check("an artifact's last revision wins",
              (fdir / "Research Report.md").read_text(encoding="utf-8").strip() == "FINAL")
        check("a code artifact is named from its language",
              (fdir / "helper.py").read_text(encoding="utf-8").strip() == "print(1)")

        manifest_doc = json.loads((fdir / "_manifest.json").read_text(encoding="utf-8"))
        manifest = {m["file"]: m for m in manifest_doc["files"]}
        check("the manifest keeps the source path the base name loses",
              manifest["notes.md"]["source"] == "/mnt/user-data/outputs/notes.md")
        check("a file whose edits all applied is marked complete",
              manifest["notes.md"]["complete"] and manifest["notes.md"]["edits_applied"] == 2)
        check("an edit that could not be applied marks the file incomplete",
              manifest["report.md"]["complete"] is False
              and manifest["report.md"]["edits_unmatched"] == 1)

        transcript = (fout / "conversations" / "Alpha.md").read_text(encoding="utf-8")
        check("the transcript points at each file it produced",
              transcript.count("[File written:") == 4,
              f"{transcript.count('[File written:')} markers")
        check("the marker gives a path that resolves from the output root",
              (fout / "files" / "Alpha" / "notes.md").exists()
              and "files/Alpha/notes.md" in transcript)
        check("an incomplete reconstruction says so in the transcript",
              "Reconstruction incomplete" in transcript
              and transcript.count("Reconstruction incomplete") == 1)

        # The whole point of the change: the baseline wrote none of this.
        if baseline:
            base_fout = tmp / "files_base"
            run(baseline, files_zip, "--extract", "1", "--output", base_fout)
            base_trans = (base_fout / "conversations" / "Alpha.md").read_text(encoding="utf-8")
            # The artifact body did reach the baseline transcript; a written file's never
            # did. That asymmetry — same document, two tools — is the whole bug.
            check("the baseline wrote no files at all",
                  not (base_fout / "files").exists())
            check("the baseline dropped every written file, while keeping artifacts",
                  "# Notes" not in base_trans and "FINAL" in base_trans)

        # A written body is a document, not tool noise: --faithful must point at it rather
        # than inline a whole file as escaped JSON and cut it off mid-way.
        ffaith = tmp / "files_faithful"
        run(EXTRACTOR, files_zip, "--faithful", "--extract", "1", "--output", ffaith)
        ftrans = (ffaith / "conversations" / "Alpha.md").read_text(encoding="utf-8")
        check("--faithful points at the written file instead of inlining its body",
              "characters, written to files/>" in ftrans
              and "# Notes\\nalpha" not in ftrans,
              "the body is named and located, not pasted in as escaped JSON")
        check("--faithful still writes the files and the raw records",
              (ffaith / "files" / "Alpha" / "notes.md").exists()
              and (ffaith / "raw" / "conversations" / "Alpha.json").exists())

        # An edit naming a file the conversation never shows being created — the shell
        # wrote it, or it is the same document under a second path. Nothing can be
        # reconstructed, and resolving by base name would be a guess that is wrong about
        # half the time on real data, so it is counted instead of dropped.
        orphan = tmp / "orphan_edits.zip"
        with zipfile.ZipFile(orphan, "w") as zf:
            zf.writestr("projects.json", json.dumps([{
                "uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "description": "", "prompt_template": "", "docs": []}]))
            zf.writestr("conversations.json", json.dumps([{
                "uuid": "c1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "chat_messages": [
                    assistant(1, [tool("str_replace", {"path": "/made/by/bash.md",
                                                       "old_str": "a", "new_str": "b"}, 1),
                                  tool("str_replace", {"path": "/made/by/bash.md",
                                                       "old_str": "c", "new_str": "d"}, 2)]),
                    # Same document, second path form: created as /repo/a.md, edited as a.md.
                    assistant(2, [tool("create_file", {"path": "/repo/a.md",
                                                       "file_text": "hello\n"}, 3),
                                  tool("str_replace", {"path": "a.md", "old_str": "hello",
                                                       "new_str": "HELLO"}, 4)]),
                ]}]))
        oout = tmp / "orphan_out"
        run(EXTRACTOR, orphan, "--extract", "1", "--output", oout)
        odoc = json.loads((oout / "files" / "Alpha" / "_manifest.json").read_text(encoding="utf-8"))
        counts = {e["path"]: e["edits"] for e in odoc["orphaned_edits"]}
        check("an edit to a file never created here is counted, not dropped",
              counts.get("/made/by/bash.md") == 2, f"{counts}")
        check("the same document under a second path is counted too",
              counts.get("a.md") == 1, f"{counts}")
        otrans = (oout / "conversations" / "Alpha.md").read_text(encoding="utf-8")
        check("the transcript says so, since an orphan edit has no file to mark",
              "3 edits in this conversation change 2 files it never shows being created" in otrans)
        check("orphan edits are never applied by guessing at a matching base name",
              (oout / "files" / "Alpha" / "a.md").read_text(encoding="utf-8") == "hello\n",
              "resolving by base name would be wrong 12 times in 25 on real data")

        # A conversation with only orphan edits still has to say so somewhere.
        only = tmp / "only_orphans.zip"
        with zipfile.ZipFile(only, "w") as zf:
            zf.writestr("projects.json", json.dumps([{
                "uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "description": "", "prompt_template": "", "docs": []}]))
            zf.writestr("conversations.json", json.dumps([{
                "uuid": "c1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "chat_messages": [
                    assistant(1, [tool("str_replace", {"path": "/only/bash.md",
                                                       "old_str": "a", "new_str": "b"}, 1)]),
                ]}]))
        oo = tmp / "only_out"
        run(EXTRACTOR, only, "--extract", "1", "--output", oo)
        odoc2 = json.loads((oo / "files" / "Alpha" / "_manifest.json").read_text(encoding="utf-8"))
        check("a conversation that produced no files still records its orphan edits",
              odoc2["files"] == [] and odoc2["orphaned_edits"][0]["path"] == "/only/bash.md")

        # And a conversation that touched no files at all gets no folder — the record is
        # complete without being noisy.
        quiet = tmp / "quiet_out"
        run(EXTRACTOR, zip_path, "--extract", "1", "--output", quiet)
        check("a conversation with no file activity gets no files folder",
              not (quiet / "files").exists())

        # An orphan sharing a base name with a file we did write is probably an edit to that
        # file under another path. Still not applied — but the file says so, rather than
        # leaving a reader to match names across two lists.
        suspect = tmp / "suspect_orphans.zip"
        with zipfile.ZipFile(suspect, "w") as zf:
            zf.writestr("projects.json", json.dumps([{
                "uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "description": "", "prompt_template": "", "docs": []}]))
            zf.writestr("conversations.json", json.dumps([{
                "uuid": "c1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "chat_messages": [
                    assistant(1, [tool("create_file", {"path": "/repo/a.md",
                                                       "file_text": "hello\n"}, 1),
                                  tool("str_replace", {"path": "a.md", "old_str": "hello",
                                                       "new_str": "HELLO"}, 2),
                                  tool("str_replace", {"path": "/other/a.md", "old_str": "x",
                                                       "new_str": "y"}, 3)]),
                    assistant(2, [tool("create_file", {"path": "/repo/clean.md",
                                                       "file_text": "untouched\n"}, 4)]),
                    assistant(3, [tool("str_replace", {"path": "/never/seen.md",
                                                       "old_str": "p", "new_str": "q"}, 5)]),
                ]}]))
        sout = tmp / "suspect_out"
        run(EXTRACTOR, suspect, "--extract", "1", "--output", sout)
        sdoc = json.loads((sout / "files" / "Alpha" / "_manifest.json").read_text(encoding="utf-8"))
        entries = {e["file"]: e for e in sdoc["files"]}
        check("a file is told when orphan edits name it at another path",
              entries["a.md"].get("orphan_edits_may_target_this") == 2,
              "both a.md and /other/a.md share its base name")
        check("a file no orphan resembles is left alone",
              "orphan_edits_may_target_this" not in entries["clean.md"])
        check("the suspicion does not change what was applied, or the complete flag",
              (sout / "files" / "Alpha" / "a.md").read_text(encoding="utf-8") == "hello\n"
              and entries["a.md"]["complete"] is True
              and entries["a.md"]["edits_applied"] == 0,
              "narrow by design: complete means every edit keyed to this path applied")
        check("an orphan resembling nothing written is attributed to no file",
              all("orphan_edits_may_target_this" not in e for e in sdoc["files"]
                  if e["file"] == "clean.md")
              and any(o["path"] == "/never/seen.md" for o in sdoc["orphaned_edits"]))
        strans = (sout / "conversations" / "Alpha.md").read_text(encoding="utf-8")
        check("the transcript says it where the file is written, not only in the manifest",
              "2 further edits in this conversation name a file called a.md" in strans)

        # Hostile shapes, each of which reached the disk in an earlier draft of this work.
        hostile = tmp / "hostile_files.zip"
        long_name = "L" * 300
        with zipfile.ZipFile(hostile, "w") as zf:
            zf.writestr("projects.json", json.dumps([{
                "uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "description": "", "prompt_template": "", "docs": []}]))
            zf.writestr("conversations.json", json.dumps([{
                "uuid": "c1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "chat_messages": [
                    assistant(1, [tool("create_file", {"path": "../../../../ESCAPED.md",
                                                       "file_text": "traversal\n"}, 1)]),
                    assistant(2, [tool("create_file", {"path": f"/q/{long_name}.md",
                                                       "file_text": "long\n"}, 2)]),
                    assistant(3, [tool("create_file", {"path": "/a/dup.md",
                                                       "file_text": "first\n"}, 3)]),
                    assistant(4, [tool("create_file", {"path": "/b/dup.md",
                                                       "file_text": "second\n"}, 4)]),
                    # Three matches, so the edit names no single place to apply itself.
                    assistant(5, [tool("create_file", {"path": "/c/amb.md",
                                                       "file_text": "aaa"}, 5),
                                  tool("str_replace", {"path": "/c/amb.md",
                                                       "old_str": "a", "new_str": "Z"}, 6)]),
                    # Shapes that carry no file at all, and must not be mistaken for one.
                    assistant(6, [tool("create_file", {"path": "/d/x.md", "file_text": 123}, 7),
                                  tool("create_file", {"path": None, "file_text": "no path"}, 8),
                                  tool("str_replace", {"path": "/never/seen.md",
                                                       "old_str": "a", "new_str": "b"}, 9)]),
                ]}]))
        hout = tmp / "hostile_out"
        hproc = run(EXTRACTOR, hostile, "--extract", "1", "--output", hout)
        hdir = hout / "files" / "Alpha"
        written = sorted(f.name for f in hdir.glob("*"))
        check("a path that climbs out of the output directory is reduced to its name",
              "ESCAPED.md" in written and not (hout.parent / "ESCAPED.md").exists()
              and not (ROOT / "ESCAPED.md").exists())
        long_written = [f for f in written if f.startswith("LL")]
        check("truncating a long filename keeps its extension",
              len(long_written) == 1 and long_written[0].endswith(".md")
              and len(long_written[0]) <= 80,
              f"{long_written}")
        check("two paths sharing a base name both survive",
              "dup.md" in written and "dup_1.md" in written)
        check("an edit matching several places is not guessed at",
              (hdir / "amb.md").read_text(encoding="utf-8") == "aaa")
        check("shapes carrying no file are skipped rather than crashing",
              hproc.returncode == 0 and "x.md" not in written and "seen.md" not in written,
              f"{written}")

        # ── Faithful export ──────────────────────────────────────────────────
        print("\nFaithful export")
        faith = tmp / "faithful"
        proc = run(EXTRACTOR, zip_path, "--faithful", "--extract", "1", "--output", faith)

        # The claim is losslessness, so the check is a round trip, not a spot check.
        source = {c["uuid"]: c for c in json.loads(
            zipfile.ZipFile(zip_path).read("conversations.json"))}
        raws = sorted((faith / "raw" / "conversations").glob("*.json"))
        differing = [f.name for f in raws
                     if json.loads(f.read_text(encoding="utf-8")) != source.get(
                         json.loads(f.read_text(encoding="utf-8")).get("uuid"))]
        check("every raw record round-trips identical to the source object",
              raws and not differing, f"{len(raws)} records, differing: {differing or 'none'}")

        src_projects = json.loads(zipfile.ZipFile(zip_path).read("projects.json"))
        proj_raw = json.loads((faith / "raw" / "project.json").read_text(encoding="utf-8"))
        check("the project record round-trips identical to the source object",
              proj_raw == next(p for p in src_projects if p["uuid"] == proj_raw["uuid"]))

        check("account-level files are carried across",
              sorted(f.name for f in (faith / "raw" / "account").glob("*.json")) == ["users.json"],
              "the fixture has only users.json alongside projects and conversations")

        transcripts = "\n".join(f.read_text(encoding="utf-8")
                                for f in (faith / "conversations").glob("*.md"))
        check("tool calls reach the transcript", "Tool call — search_docs" in transcripts)
        check("tool results reach the transcript", "Tool result — search_docs" in transcripts)
        check("a failed tool call is marked", "**error**" in transcripts)
        check("citations reach the transcript", "Example Source" in transcripts)
        check("the conversation's own summary reaches the transcript",
              "SUMMARY OF THIS CHAT" in transcripts)
        check("non-text file names reach the transcript", "[File: diagram.png]" in transcripts)

        reasoning = "\n".join(f.read_text(encoding="utf-8")
                              for f in (faith / "thinking").glob("*.md"))
        check("thinking summaries reach the reasoning files", "CONDENSED REASONING" in reasoning)
        check("--faithful implies --thinking", (faith / "thinking").exists())

        # Account files have to reach a directory the run actually writes to, including a
        # default one derived from the project name — the two commonest invocations.
        defaults = tmp / "defaults"
        defaults.mkdir()
        proc = subprocess.run([sys.executable, str(EXTRACTOR), str(zip_path),
                               "--faithful", "--extract", "1"],
                              capture_output=True, text=True, input="q\n", cwd=defaults)
        landed = list(defaults.rglob("raw/account/*.json"))
        check("account files land when output directories are left to default",
              bool(landed), f"{[f.name for f in landed] or 'nothing copied'}")

        interactive = tmp / "interactive"
        interactive.mkdir()
        subprocess.run([sys.executable, str(EXTRACTOR), str(zip_path), "--faithful"],
                       capture_output=True, text=True, input="1\n\ny\n", cwd=interactive)
        check("account files land from an interactive run",
              bool(list(interactive.rglob("raw/account/*.json"))))

        # A listing is a read. It must not put anything on disk.
        listing = tmp / "listing"
        listing.mkdir()
        run(EXTRACTOR, zip_path, "--faithful", "--json", "--mapping", mapping_path,
            "--unfiled", listing / "dump")
        check("a --json listing writes nothing, even with --faithful",
              not list(listing.rglob("*")), f"{[str(f) for f in listing.rglob('*')][:3]}")

        plain2 = tmp / "not_faithful"
        run(EXTRACTOR, zip_path, "--extract", "1", "--output", plain2)
        check("no raw folder without --faithful", not (plain2 / "raw").exists())

        # ── The browser script's paging ──────────────────────────────────────
        # fetch_mapping.js is the one piece of this repo that is not Python, and its paging is
        # the most intricate logic in it — a real run silently returned a third of the data
        # before it was fixed. Covered by node when node is here, skipped when it is not; the
        # tool itself stays stdlib-Python either way.
        print("\nBrowser script paging")
        if shutil.which("node") is None:
            skip("fetch_mapping.js paging scenarios", "node is not installed")
        else:
            proc = subprocess.run(["node", str(ROOT / "tests" / "paging_check.mjs")],
                                  capture_output=True, text=True)
            summary = (proc.stdout.strip().splitlines() or ["no output"])[-1]
            check("fetch_mapping.js paging scenarios", proc.returncode == 0, summary)
            if proc.returncode != 0:
                for line in proc.stdout.splitlines():
                    if "FAIL" in line:
                        print(f"      {line.strip()}")

        # ── Source constraints ───────────────────────────────────────────────
        print("\nSource constraints")
        tree_ast = ast.parse(EXTRACTOR.read_text(encoding="utf-8"))
        check("extractor parses", True)
        imported = set()
        for node in ast.walk(tree_ast):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        extra = imported - STDLIB_ALLOWED
        check("no imports outside the stdlib set", not extra, f"imports: {sorted(imported)}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok in results if ok is False]
    skipped = [n for n, ok in results if ok is None]
    print(f"\n{len(results) - len(failed) - len(skipped)} passed, {len(failed)} failed, {len(skipped)} skipped")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
