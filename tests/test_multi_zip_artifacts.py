#!/usr/bin/env python3
"""
An export delivered as several zips, scheduled routines, and published artifacts.

    python tests/test_multi_zip_artifacts.py

The November 2026 export arrives as seven zips (one per category) and added two categories:
claude_code_cloud/ (scheduled routines and their runs) and artifacts/ (published Artifacts,
plain or typed). Before this the tool could read one zip only, treated the new categories as
files it had no rendering for, and never saw the non-JSON files inside an artifact. These
checks build a small synthetic export in that shape and confirm it is read as delivered,
rendered, verified, tolerated when incomplete, and kept byte for byte under --faithful.

Self-contained: no framework, no fixtures on disk, no dependencies. Every name and passage
below is invented.
"""

import hashlib
import importlib.util
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
PLAIN_ID = "b0000000-0000-4000-8000-00000000000a"
DECK_ID = "b0000000-0000-4000-8000-00000000000b"
BROKEN_ID = "b0000000-0000-4000-8000-00000000000c"
EMPTY_ID = "b0000000-0000-4000-8000-00000000000d"
ROUTINE = "trig_invented_one"
GONE = "trig_invented_gone"

passes, failures, skipped = [], [], []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    (passes if ok else failures).append(name)


def skip(name, detail):
    print(f"  [SKIP] {name} — {detail}")
    skipped.append(name)


def load_module():
    spec = importlib.util.spec_from_file_location("extractor", EXTRACTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Building an export ────────────────────────────────────────────────────────

def conversation(text):
    return {"uuid": CONV, "name": "Chat about a deck", "summary": "",
            "created_at": "2026-10-01T09:00:00Z", "updated_at": "2026-10-01T09:30:00Z",
            "account": {"uuid": ACCOUNT},
            "chat_messages": [{"uuid": "m1", "sender": "human", "text": text,
                               "content": [{"type": "text", "text": text}],
                               "created_at": "2026-10-01T09:00:00Z", "updated_at": "2026-10-01T09:00:00Z",
                               "attachments": [], "files": []}]}


def project_record():
    return {"uuid": PROJECT, "name": "Garden", "description": "", "created_at": "2026-02-22T10:00:00Z",
            "updated_at": "2026-02-23T10:00:00Z", "prompt_template": "", "is_private": True,
            "is_starter_project": False, "docs": []}


def artifact_meta(artifact_id, title, versions, active, **extra):
    return {"id": artifact_id, "kind": "artifact", "visibility": "private", "owner_account": ACCOUNT,
            "updated_at": "2026-10-05T12:00:00+00:00", "active_version": active,
            "versions": [{"id": v, "title": title, "description": f"about {v}",
                          "created_at": f"2026-10-0{n + 1}T10:00:00+00:00"}
                         for n, v in enumerate(versions)], **extra}


def add_plain(zf, artifact_id, title, bodies, active):
    """A plain artifact: one html file per version. `bodies` is {version id: html}."""
    zf.writestr(f"artifacts/{artifact_id}/artifact.json",
                json.dumps(artifact_meta(artifact_id, title, list(bodies), active)))
    for version, body in bodies.items():
        zf.writestr(f"artifacts/{artifact_id}/versions/{version}.html", body)


def typed_files(version, no_project=False, extra=None):
    files = {"SKILL.md": b"# instructions for the type\n",
             "index.html": b"<!doctype html><title>shell</title>",
             "artifact-type/app.js": b"runtime;" * 3000,
             "artifact-type/fonts/f.woff2": b"\x00\x01font"}
    if not no_project:
        files["project/deck.json"] = json.dumps({"version": version}).encode()
        files["project/slides/a.html"] = f"<h1>slide in {version}</h1>".encode()
    files.update(extra or {})
    return files


def add_typed(zf, artifact_id, title, versions, active, tamper=(), skip_files=(), no_project=False,
              extra=None):
    """A typed artifact: a folder per version, and a manifest of hashes beside it."""
    zf.writestr(f"artifacts/{artifact_id}/artifact.json",
                json.dumps(artifact_meta(artifact_id, title, versions, active)))
    for version in versions:
        files = typed_files(version, no_project, extra)
        manifest = [{"original_path": p, "exported_as": p, "sha256": sha(b), "size_bytes": len(b),
                     "mime_type": "application/octet-stream"} for p, b in files.items()]
        zf.writestr(f"artifacts/{artifact_id}/versions/{version}.files.json", json.dumps({"files": manifest}))
        for path, data in files.items():
            if path in skip_files:
                continue
            zf.writestr(f"artifacts/{artifact_id}/versions/{version}/{path}", data + (b"!" if path in tamper else b""))


ROUTINES = {"claude_code_cloud": [
    {"routine_id": ROUTINE, "agent_id": "agent_1", "name": "Weekly check", "enabled": True,
     "prompt": "Look at the `build` and say ```so``` if it is red.", "prompt_withheld": False,
     "schedule": {"cron": "0 9 * * 1", "run_once_at": None},
     "created_at": "2026-10-01T08:00:00Z", "updated_at": "2026-10-01T08:00:00Z", "ended_at": None},
    {"routine_id": "trig_invented_once", "agent_id": "agent_1", "name": "One-off nudge", "enabled": False,
     "prompt": "", "prompt_withheld": True, "schedule": {"cron": None, "run_once_at": "2026-10-04T21:44:00Z"},
     "created_at": "2026-10-04T20:00:00Z", "updated_at": "2026-10-04T21:44:00Z", "ended_at": "2026-10-04T21:44:00Z"},
]}
RUNS = {"claude_code_cloud": [
    {"routine_id": ROUTINE, "scheduled_for": "2026-10-05T09:00:00Z", "started_at": "2026-10-05T09:00:02Z",
     "finished_at": "2026-10-05T09:03:00Z", "status": "succeeded", "session_id": "cse_one"},
    {"routine_id": ROUTINE, "scheduled_for": "2026-10-12T09:00:00Z", "started_at": "2026-10-12T09:00:01Z",
     "finished_at": None, "status": "failed", "session_id": "cse_two"},
    {"routine_id": GONE, "scheduled_for": "2026-10-06T09:00:00Z", "started_at": None, "finished_at": None,
     "status": "skipped", "session_id": None},
]}


def build_export(directory: Path, tamper=(), skip_files=(), extra_zips=None) -> Path:
    """The export as delivered: a zip per category, a manifest, and a note that is no archive."""
    directory.mkdir(parents=True, exist_ok=True)

    def make(name, entries):
        with zipfile.ZipFile(directory / name, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, data in entries:
                zf.writestr(path, data)

    make("projects-000.zip", [(f"projects/{PROJECT}.json", json.dumps(project_record()))])
    make("conversations-000.zip", [("conversations.json", json.dumps(
        [conversation(f"See https://claude.ai/artifact/{DECK_ID} for the deck.")]))])
    make("light_metadata-000.zip", [("users.json", json.dumps([{"uuid": ACCOUNT}])),
                                    ("login_history.json", json.dumps({"logins": []}))])
    make("claude_code_cloud-000.zip", [("claude_code_cloud/routines.json", json.dumps(ROUTINES)),
                                       ("claude_code_cloud/routine_runs.json", json.dumps(RUNS))])
    with zipfile.ZipFile(directory / "frames-000.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        add_plain(zf, PLAIN_ID, "Music Room", {"v1": "<p>old</p>", "v2": "<p>current</p>"}, "v2")
        add_typed(zf, DECK_ID, "Sun Deck", ["d1", "d2"], "d2",
                  extra={"project/conversations.json": json.dumps([{"uuid": "zzz", "name": "Not a chat"}]).encode()})
        add_typed(zf, BROKEN_ID, "Broken Deck", ["x1"], "x1", tamper=tamper, skip_files=skip_files)
    for name, entries in (extra_zips or {}).items():
        make(name, entries)

    zips = sorted(p.name for p in directory.glob("*.zip"))
    (directory / "manifest-invented-2026-11-03.json").write_text(json.dumps({
        "instructions": "Download each file.", "created_at": "2026-11-03T07:28:03Z", "total_files": len(zips),
        "data_files": [{"batch_index": 0, "export_url": "https://example.invalid/x", "category": n.split("-")[0],
                        "part": 0, "filename": n} for n in zips], "version": "1.0"}), encoding="utf-8")
    (directory / "notes.txt").write_text("not an archive", encoding="utf-8")
    return directory


def write_mapping(path: Path):
    path.write_text(json.dumps({"schema": 1, "fetched_at": "2026-11-03T08:00:00Z", "org_uuid": "o",
                                "projects": {PROJECT: "Garden"}, "conversations": {}}), encoding="utf-8")
    return path


def cli(*args, mapping=None):
    command = [sys.executable, "-I", str(EXTRACTOR), *map(str, args)]
    if mapping:
        command += ["--mapping", str(mapping)]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def extract(source, out: Path, mapping: Path, *flags):
    sources = source if isinstance(source, (list, tuple)) else [source]
    return cli(*sources, "--extract", "1", "--output", out / "proj", "--unfiled", out / "unfiled",
               *flags, mapping=mapping)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def snapshot(directory: Path) -> dict:
    """Every file under `directory`, by relative path. The one file that records when the run
    happened is left out, since two runs of the same input never agree on it."""
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*")
            if p.is_file() and p.name != "_project_metadata.json"}


# ── The checks ────────────────────────────────────────────────────────────────

def main():
    module = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mapping = write_mapping(tmp / "mapping.json")
        export = build_export(tmp / "export")

        print("\nReading the export as delivered")
        folder = extract(export, tmp / "by_folder", mapping)
        check("a folder of zips is read", folder.returncode == 0, folder.stderr.strip()[:200])
        check("every archive in the folder is read, and nothing else", "Loading: 5 archives" in folder.stdout
              and "notes.txt" not in folder.stdout and "manifest-" not in folder.stdout, folder.stdout.splitlines()[0])
        check("the project and the conversation come from different zips",
              "Found 1 projects, 1 conversations" in folder.stdout)
        zips = sorted(export.glob("*.zip"))
        listed = extract(zips, tmp / "by_list", mapping)
        check("zips named one by one give the same output",
              listed.returncode == 0 and snapshot(tmp / "by_folder") == snapshot(tmp / "by_list"))
        reversed_ = extract(list(reversed(zips)), tmp / "by_reversed", mapping)
        check("and the order they are named in does not matter",
              reversed_.returncode == 0 and snapshot(tmp / "by_folder") == snapshot(tmp / "by_reversed"))

        one = tmp / "one.zip"
        with zipfile.ZipFile(one, "w", zipfile.ZIP_DEFLATED) as out:
            for z in zips:
                with zipfile.ZipFile(z) as src:
                    for info in src.infolist():
                        out.writestr(info.filename, src.read(info))
        single = extract(one, tmp / "single", mapping)
        check("one zip holding everything still works, with the same output",
              single.returncode == 0 and snapshot(tmp / "by_folder") == snapshot(tmp / "single"))

        missing = cli(tmp / "no-such-folder")
        check("a path that does not exist is an error", missing.returncode == 1 and "File not found" in missing.stderr)
        (tmp / "empty").mkdir()
        (tmp / "empty" / "notes.txt").write_text("x", encoding="utf-8")
        nothing = cli(tmp / "empty")
        check("a folder with no zip in it is an error", nothing.returncode == 1 and "No .zip files" in nothing.stderr,
              nothing.stderr.strip()[:120])
        (tmp / "bad").mkdir()
        (tmp / "bad" / "broken-000.zip").write_bytes(b"not a zip at all")
        corrupt = cli(tmp / "bad")
        check("a corrupt zip is an error that names it", corrupt.returncode == 1 and "broken-000.zip" in corrupt.stderr,
              corrupt.stderr.strip()[:160])

        print("\nThe same entry in two zips")
        same = build_export(tmp / "dup_same", extra_zips={"projects-001.zip": [
            (f"projects/{PROJECT}.json", json.dumps(project_record()))]})
        run_same = extract(same, tmp / "dup_same_out", mapping)
        check("identical content is kept once", run_same.returncode == 0 and "Found 1 projects" in run_same.stdout,
              run_same.stderr.strip()[:160])
        clash = build_export(tmp / "dup_clash", extra_zips={"projects-001.zip": [
            (f"projects/{PROJECT}.json", json.dumps({**project_record(), "name": "Renamed"}))]})
        run_clash = extract(clash, tmp / "dup_clash_out", mapping)
        check("different content is an error that names both zips",
              run_clash.returncode == 1 and "projects-000.zip" in run_clash.stderr
              and "projects-001.zip" in run_clash.stderr, run_clash.stderr.strip()[:200])

        print("\nThe download manifest")
        short = tmp / "short"
        build_export(short)
        (short / "claude_code_cloud-000.zip").unlink()
        warned = extract(short, tmp / "short_out", mapping)
        check("a zip the manifest lists but the folder lacks is a warning that names it",
              warned.returncode == 0 and "WARNING" in warned.stderr and "claude_code_cloud-000.zip" in warned.stderr,
              warned.stderr.strip()[:200])
        check("the manifest says nothing when everything is there", "lists" not in folder.stderr)
        explicit = extract([short / z.name for z in sorted(short.glob("*.zip"))], tmp / "short_named", mapping)
        check("zips named one by one are not checked against it", "lists" not in explicit.stderr)

        print("\nClassification")
        classify = module.classify_entry
        check("an artifact's own files are claimed first, whatever they are called",
              classify("artifacts/x/versions/v/project/conversations.json") == "artifact"
              and classify("artifacts/x/versions/v/project/project.json") == "artifact"
              and classify("artifacts/x/artifact.json") == "artifact"
              and classify("artifacts/x/versions/v.html") == "artifact")
        check("including under a folder the archive nests everything in",
              classify("export/artifacts/x/artifact.json") == "artifact")
        check("a folder that is merely called artifacts is not one",
              classify("artifacts/notes.json") == "account" and classify("artifacts/notes.txt") == "")
        check("other entries are classified as before",
              classify("projects/p.json") == "project" and classify("conversations.json") == "conversation"
              and classify("users.json") == "account" and classify("diagram.png") == "")
        check("a file inside an artifact is not read as a conversation", "Found 1 projects, 1 conversations" in folder.stdout
              and not (tmp / "by_folder" / "unfiled" / "Not a chat.md").exists())

        print("\nScheduled routines")
        routines = tmp / "by_folder" / "unfiled" / "account" / "routines"
        weekly = read(routines / "Weekly check.md")
        check("a routine is rendered", bool(weekly))
        check("with its schedule and state", "cron `0 9 * * 1`" in weekly and "enabled" in weekly)
        check("its prompt is fenced so backticks inside cannot close the fence",
              "````\nLook at the `build` and say ```so``` if it is red.\n````" in weekly, weekly[200:420])
        check("its runs are joined on routine_id", "## Runs (2)" in weekly and "cse_one" in weekly and "failed" in weekly)
        once = read(routines / "One-off nudge.md")
        check("a one-off routine shows when it ran, and that it ended", "once, at 2026-10-04 21:44 UTC" in once
              and "ended 2026-10-04 21:44 UTC" in once)
        check("a withheld prompt is said to be withheld", "withheld" in once)
        check("a routine with no runs says so", "## Runs (0)" in once and "None in this export" in once)
        orphans = read(routines / "_runs_without_routine.md")
        check("a run whose routine is not in the export is kept", GONE in orphans and "skipped" in orphans)
        index = read(routines / "_index.md")
        check("an index lists the routines", "Weekly check" in index and "One-off nudge" in index)
        check("and the summary counts them", "2 routines, 3 routine runs" in folder.stdout, folder.stdout[-400:])

        odd = tmp / "odd"
        build_export(odd, extra_zips={"claude_code_cloud-001.zip": [
            ("claude_code_cloud/extra.json", json.dumps({"claude_code_cloud": [{"mystery": 1}, "text"]}))]})
        odd_run = extract(odd, tmp / "odd_out", mapping)
        check("a record that is neither a routine nor a run is shown, not dropped",
              odd_run.returncode == 0 and '"mystery": 1' in read(tmp / "odd_out" / "unfiled" / "account" / "routines" / "_unrecognised.md"))

        print("\nPublished artifacts")
        artifacts = tmp / "by_folder" / "unfiled" / "account" / "artifacts"
        music = artifacts / "Music Room"
        check("a plain artifact's current version is written under its title", read(music / "Music Room.html") == "<p>current</p>")
        check("and its older versions are not", "<p>old</p>" not in "".join(read(p) for p in music.iterdir() if p.is_file()))
        deck = artifacts / "Sun Deck"
        check("a typed artifact's own files are written", read(deck / "slides" / "a.html") == "<h1>slide in d2</h1>"
              and json.loads(read(deck / "deck.json")) == {"version": "d2"})
        names = {p.name for p in deck.rglob("*") if p.is_file()}
        check("without the type's runtime, instructions or shell page",
              not names & {"app.js", "f.woff2", "SKILL.md", "index.html"}, str(sorted(names)))
        versions_md = read(deck / "_versions.md")
        check("a versions index lists every version and marks the current one",
              "d1" in versions_md and "d2 **(current)**" in versions_md and "3 project files + the type's runtime" in versions_md
              and "about d1" in versions_md, versions_md[:600])
        check("it records how many files were checked against their hashes", "| 7 files |" in versions_md)
        check("it names the conversations that mention the artifact", "Chat about a deck" in versions_md
              and CONV in versions_md)
        check("an artifact no conversation mentions has no such section", "Mentioned in" not in read(music / "_versions.md"))
        check("the summary counts what was written and checked",
              "3 artifacts, 5 versions" in folder.stdout and "files verified" in folder.stdout, folder.stdout[-300:])
        check("a clean export raises no warning", "WARNING" not in folder.stderr, folder.stderr.strip()[:200])

        print("\nFiles that do not match their hashes")
        for label, kwargs, expected in (
                ("a file that differs from its recorded sha256", {"tamper": ("project/deck.json",)}, "does not match"),
                ("a file the manifest lists but the archive lacks", {"skip_files": ("project/slides/a.html",)},
                 "not in the archive")):
            broken = build_export(tmp / "broken", **kwargs)
            run = extract(broken, tmp / "broken_out", mapping)
            check(f"{label} is a warning, not a failure", run.returncode == 0 and "WARNING" in run.stderr
                  and "Broken Deck" in run.stderr and expected in run.stderr, run.stderr.strip()[:240])
            md = read(tmp / "broken_out" / "unfiled" / "account" / "artifacts" / "Broken Deck" / "_versions.md")
            check("and is listed in the artifact's index", "Files that failed verification" in md and expected in md)
            check("and counted in the summary", "1 failed verification" in run.stdout, run.stdout[-200:])
            shutil.rmtree(tmp / "broken")
            shutil.rmtree(tmp / "broken_out")

        print("\nIncomplete artifacts")
        weird = tmp / "weird"
        build_export(weird, extra_zips={"frames-001.zip": [
            (f"artifacts/{EMPTY_ID}/artifact.json", json.dumps(artifact_meta(EMPTY_ID, "No Versions", [], None))),
            ("artifacts/e0/artifact.json", "{ not json"),
            ("artifacts/e0/versions/v1.html", "<p>survivor</p>"),
            ("artifacts/e1/artifact.json", json.dumps(artifact_meta("e1", "Gone Active", ["g1", "g2"], "g9"))),
            ("artifacts/e1/versions/g1.html", "<p>g1</p>"), ("artifacts/e1/versions/g2.html", "<p>g2</p>"),
            ("artifacts/e2/versions/v1.html", "<p>no record</p>"),
            ("artifacts/e3/artifact.json", json.dumps(artifact_meta("e3", "Music Room", ["m1"], "m1"))),
            ("artifacts/e3/versions/m1.html", "<p>same title</p>"),
            # Fields of the wrong type: nothing here may stop the run.
            ("artifacts/e4/artifact.json", json.dumps({"active_version": ["x"], "updated_at": {"a": 1},
                                                       "versions": [{"id": {"a": 1}}, "text", {"id": "w1", "title": 7}]})),
            ("artifacts/e4/versions/w1.html", "<p>wrong types</p>"),
        ]})
        run = extract(weird, tmp / "weird_out", mapping)
        out = tmp / "weird_out" / "unfiled" / "account" / "artifacts"
        check("odd artifacts do not stop the run", run.returncode == 0, run.stderr.strip()[:300])
        check("an artifact with no versions still gets an index, named by its id as nothing else names it",
              "(none in the archive)" in read(out / EMPTY_ID / "_versions.md"), str(sorted(p.name for p in out.iterdir())))
        check("an unreadable artifact.json is noted and the file still written",
              "not valid JSON" in read(out / "e0" / "_versions.md") and "<p>survivor</p>" in read(out / "e0" / "e0.html"),
              str(sorted(p.name for p in out.iterdir())))
        gone = read(out / "Gone Active" / "_versions.md")
        check("a current version that is not in the archive falls back to the newest, and says so",
              "g9" in gone and "not in the archive" in gone and read(out / "Gone Active" / "Gone Active.html") == "<p>g2</p>")
        check("an artifact with no artifact.json is still written", read(out / "e2" / "e2.html") == "<p>no record</p>")
        check("fields of the wrong type are tolerated and the page still written",
              read(out / "e4" / "e4.html") == "<p>wrong types</p>" and "_versions.md" in {p.name for p in (out / "e4").iterdir()},
              str(sorted(p.name for p in out.iterdir())))
        check("two artifacts with one title both survive",
              read(out / "Music Room" / "Music Room.html") == "<p>current</p>"
              and read(out / "Music Room_1" / "Music Room.html") == "<p>same title</p>", str(sorted(p.name for p in out.iterdir())))

        nopj = tmp / "nopj"
        nopj.mkdir()
        with zipfile.ZipFile(nopj / "frames-000.zip", "w") as zf:
            add_typed(zf, "n1", "Bare Deck", ["n1v"], "n1v", no_project=True)
        with zipfile.ZipFile(nopj / "projects-000.zip", "w") as zf:
            zf.writestr(f"projects/{PROJECT}.json", json.dumps(project_record()))
        run = extract(nopj, tmp / "nopj_out", mapping)
        bare = tmp / "nopj_out" / "unfiled" / "account" / "artifacts" / "Bare Deck"
        check("a typed version without project/ falls back to its page and says so",
              run.returncode == 0 and read(bare / "index.html").startswith("<!doctype html>")
              and "no project/ folder" in read(bare / "_versions.md"))

        print("\nWhat --faithful keeps")
        faithful = extract(export, tmp / "faithful", mapping, "--faithful")
        check("the run succeeds", faithful.returncode == 0, faithful.stderr.strip()[:200])
        raw = tmp / "faithful" / "unfiled" / "raw" / "artifacts"
        expected = {}
        with zipfile.ZipFile(export / "frames-000.zip") as zf:
            for info in zf.infolist():
                expected[info.filename[len("artifacts/"):]] = zf.read(info)
        found = snapshot(raw)
        check("every file of every version is there, byte for byte", found == expected,
              f"{len(found)} of {len(expected)} files; differing: {[k for k in expected if found.get(k) != expected[k]][:3]}")
        check("including the runtime the readable output leaves out",
              any(k.endswith("artifact-type/app.js") for k in found))
        account_raw = sorted(snapshot(tmp / "faithful" / "unfiled" / "raw" / "account"))
        check("the routine files are kept raw beside the other account files",
              "routines.json" in "".join(account_raw) and "routine_runs.json" in "".join(account_raw), str(account_raw))
        check("and the artifacts are not duplicated into the account files", not any("artifact" in k for k in account_raw))

        print("\nFiles nothing reads")
        strange = tmp / "strange"
        build_export(strange, extra_zips={"misc-000.zip": [("pictures/diagram.png", b"\x89PNG-ish"), ("notes.md", "# loose")]})
        plain = extract(strange, tmp / "strange_plain", mapping)
        check("are named in a note", "2 file(s) in the export are not read" in plain.stderr and "diagram.png" in plain.stderr,
              plain.stderr.strip()[:240])
        check("and not copied without --faithful", not (tmp / "strange_plain" / "unfiled" / "raw").exists())
        kept = extract(strange, tmp / "strange_faithful", mapping, "--faithful")
        other = tmp / "strange_faithful" / "unfiled" / "raw" / "other"
        check("under --faithful they are copied verbatim", (other / "pictures" / "diagram.png").read_bytes() == b"\x89PNG-ish"
              and read(other / "notes.md") == "# loose", kept.stderr.strip()[:200])

        print("\nWithout --unfiled")
        inproj = cli(export, "--extract", "1", "--output", tmp / "inproj", mapping=mapping)
        check("account-wide documents are held back, and the note says artifacts are among them",
              inproj.returncode == 0 and "artifacts" in inproj.stderr and "were not written" in inproj.stderr,
              inproj.stderr.strip()[:240])
        check("and none is written into the project folder", not list((tmp / "inproj").rglob("_versions.md")))

        print("\nLong paths")
        if sys.platform != "win32":
            skip("artifact files past 260 characters", "only Windows has that limit")
        else:
            deep = tmp / "deep"
            while len(str(deep)) < 235:
                deep = deep / ("nested-directory-" + "x" * 20)
            try:
                run = extract(export, deep, mapping, "--faithful")
                target = deep / "unfiled" / "raw" / "artifacts" / DECK_ID / "versions" / "d2" / "artifact-type" / "fonts" / "f.woff2"
                check("the run completes", run.returncode == 0, run.stderr.strip()[:200])
                check("and the deepest file is there", len(str(target)) > 260 and Path("\\\\?\\" + str(target)).exists())
            finally:
                shutil.rmtree("\\\\?\\" + str(tmp / "deep"), ignore_errors=True)

    print(f"\n{len(passes)} passed, {len(failures)} failed" + (f", {len(skipped)} skipped" if skipped else ""))
    if failures:
        print("FAILED: " + "; ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
