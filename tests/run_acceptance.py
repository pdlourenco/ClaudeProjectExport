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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "claude_export_extractor.py"
BASELINE_REF = "main:claude_export_extractor.py"

STDLIB_ALLOWED = {"zipfile", "json", "re", "sys", "argparse", "pathlib", "datetime", "collections"}

results = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    results.append((name, ok))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail):
    results.append((name, None))
    print(f"  [SKIP] {name} — {detail}")


def run(script, *args, expect_rc=0):
    proc = subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, input="q\n")
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
        try:
            src = subprocess.run(["git", "-C", str(ROOT), "show", BASELINE_REF],
                                 capture_output=True, text=True, check=True).stdout
            baseline.write_text(src, encoding="utf-8")
        except (OSError, subprocess.CalledProcessError) as exc:
            baseline = None
            skip("output identical to main without --mapping", f"cannot read {BASELINE_REF} ({exc})")

        if baseline:
            base_json = run(baseline, zip_path, "--json")
            new_json = run(EXTRACTOR, zip_path, "--json")
            check("--json output identical to main",
                  (base_json.stdout, base_json.stderr) == (new_json.stdout, new_json.stderr))

            base_picker = run(baseline, zip_path)
            new_picker = run(EXTRACTOR, zip_path)
            check("interactive picker identical to main", base_picker.stdout == new_picker.stdout)

            base_dir, new_dir = tmp / "base_out", tmp / "new_out"
            base_out = run(baseline, zip_path, "--extract", "1,2,3,4",
                           "--output", ",".join(str(base_dir / f"p{i}") for i in (1, 2, 3, 4)))
            new_out = run(EXTRACTOR, zip_path, "--extract", "1,2,3,4",
                          "--output", ",".join(str(new_dir / f"p{i}") for i in (1, 2, 3, 4)))
            check("extraction stdout identical to main",
                  normalise(base_out.stdout, base_dir) == normalise(new_out.stdout, new_dir))
            base_tree = {k: normalise(v, base_dir) for k, v in tree(base_dir).items()}
            new_tree = {k: normalise(v, new_dir) for k, v in tree(new_dir).items()}
            differing = [k for k in base_tree if base_tree.get(k) != new_tree.get(k)]
            check("extracted files identical to main",
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
