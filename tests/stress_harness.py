#!/usr/bin/env python3
"""Stress harness: scale + pathological shapes for claude_export_extractor.py.

    python tests/stress_harness.py

Informational, not pass/fail: prints wall time, peak RSS, and output counts for
large and pathological exports (see known-issues/06 for the quadratic collision
counter this measures). Builds everything in a temp dir; needs ~1 GB free disk
for the 200 MB single-doc case. `resource` limits this to Unix.
"""
import json
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR = ROOT / "claude_export_extractor.py"
WORK = Path(tempfile.mkdtemp(prefix="cpe-stress-"))


def fresh(name):
    d = WORK / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def timed(*args, stdin=None):
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, str(EXTRACTOR), *map(str, args)],
                       capture_output=True, text=True, input=stdin, timeout=600)
    dt = time.monotonic() - t0
    mem_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return p, dt, mem_kb


def big_export(d, n_projects=100, n_convs=5000, msgs_per_conv=40):
    """A realistic-shaped large account."""
    words = ["marketing", "research", "python", "garden", "finance", "travel",
             "music", "cooking", "fitness", "startup", "novel", "genealogy"]
    projects = []
    for i in range(n_projects):
        projects.append({
            "uuid": f"p-{i}", "name": f"{words[i % len(words)].title()} Project {i}",
            "created_at": "2026-01-01T00:00:00Z", "description": "d" * 200,
            "prompt_template": "be brief", "docs": [
                {"uuid": f"d-{i}-{j}", "filename": f"doc_{i}_{j}.md",
                 "content": ("# Doc\n" + "lorem ipsum " * 200)} for j in range(3)],
        })
    convs = []
    for i in range(n_convs):
        convs.append({
            "uuid": f"c-{i}",
            "name": f"Notes about {words[i % len(words)]} session {i}",
            "created_at": "2026-01-02T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
            "chat_messages": [
                {"sender": "human" if k % 2 == 0 else "assistant",
                 "created_at": "2026-01-02T00:00:00Z",
                 "text": f"message {k} " + "words and more words " * 30,
                 "attachments": []} for k in range(msgs_per_conv)],
        })
    zp = d / "big.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("projects.json", json.dumps(projects))
        zf.writestr("conversations.json", json.dumps(convs))
    return zp


def scale_test():
    d = fresh("scale")
    zp = big_export(d)
    raw_mb = sum(i.file_size for i in zipfile.ZipFile(zp).infolist()) / 1e6
    print(f"scale: export with 100 projects, 5000 convs x 40 msgs "
          f"({zp.stat().st_size/1e6:.0f} MB zip, {raw_mb:.0f} MB json)")
    p, dt, mem = timed(zp, "--json")
    n = len(json.loads(p.stdout))
    print(f"  --json: {dt:.1f}s, peak RSS {mem/1e6:.2f} GB, {n} projects listed, rc={p.returncode}")

    out = d / "out"
    p, dt, mem = timed(zp, "--extract", "1", "--output", out)
    convs_written = len(list((out / "conversations").glob("*.md"))) if (out / "conversations").exists() else 0
    print(f"  --extract 1: {dt:.1f}s, peak RSS {mem/1e6:.2f} GB, {convs_written} conversations written, rc={p.returncode}")


def collision_quadratic():
    """Many conversations with the identical title all matched to one project."""
    d = fresh("collide")
    for n in (200, 800):
        convs = [{"uuid": f"c-{i}", "name": "Alpha weekly sync",
                  "created_at": "2026-01-02T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
                  "chat_messages": [{"sender": "human", "text": "hi",
                                     "created_at": "2026-01-02T00:00:00Z"}]}
                 for i in range(n)]
        proj = [{"uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
                 "description": "", "prompt_template": "", "docs": []}]
        zp = d / f"n{n}.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("projects.json", json.dumps(proj))
            zf.writestr("conversations.json", json.dumps(convs))
        out = d / f"out{n}"
        p, dt, _ = timed(zp, "--extract", "1", "--output", out)
        written = len(list((out / "conversations").glob("*.md")))
        print(f"  identical-title collisions n={n}: {dt:.1f}s, {written} files, rc={p.returncode}")


def huge_single_doc():
    d = fresh("hugedoc")
    proj = [{"uuid": "p-1", "name": "Alpha", "created_at": "2026-01-01T00:00:00Z",
             "description": "", "prompt_template": "",
             "docs": [{"uuid": "d", "filename": "big.txt", "content": "A" * (200 * 1024 * 1024)}]}]
    zp = d / "huge.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("projects.json", json.dumps(proj))
        zf.writestr("conversations.json", "[]")
    p, dt, mem = timed(zp, "--extract", "1", "--output", d / "out")
    size = (d / "out" / "project_knowledge" / "big.txt").stat().st_size
    print(f"  200MB single doc: {dt:.1f}s, peak RSS {mem/1e6:.2f} GB, wrote {size/1e6:.0f} MB, rc={p.returncode}")
    shutil.rmtree(d)


def deep_nesting():
    """Deeply nested JSON in a message content block — recursion safety of json module."""
    d = fresh("deep")
    deep = "[" * 200000 + "]" * 200000
    with zipfile.ZipFile(d / "deep.zip", "w") as zf:
        zf.writestr("projects.json", "[]")
        zf.writestr("conversations.json", deep)
    p, dt, _ = timed(d / "deep.zip", "--json")
    tail = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ""
    print(f"  200k-deep nested JSON: rc={p.returncode} in {dt:.1f}s — {tail[:90]}")


def many_projects_many_convs_matching_cost():
    """Worst-case keyword matching: every project name word hits every title."""
    d = fresh("matchcost")
    projects = [{"uuid": f"p-{i}", "name": f"common shared words everywhere {i}",
                 "created_at": "2026-01-01T00:00:00Z", "description": "",
                 "prompt_template": "", "docs": []} for i in range(300)]
    convs = [{"uuid": f"c-{i}", "name": "common shared words everywhere too " + "pad " * 20,
              "created_at": "2026-01-02T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
              "chat_messages": []} for i in range(10000)]
    zp = d / "m.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("projects.json", json.dumps(projects))
        zf.writestr("conversations.json", json.dumps(convs))
    p, dt, mem = timed(zp, "--json")
    counts = {e["conv_count"] for e in json.loads(p.stdout)}
    print(f"  300 projects x 10k convs, all-match keywords: {dt:.1f}s, "
          f"peak RSS {mem/1e6:.2f} GB, conv_count per project={counts}")


if __name__ == "__main__":
    WORK.mkdir(exist_ok=True)
    print("── Stress tests ──")
    scale_test()
    collision_quadratic()
    huge_single_doc()
    deep_nesting()
    many_projects_many_convs_matching_cost()
    shutil.rmtree(WORK, ignore_errors=True)
