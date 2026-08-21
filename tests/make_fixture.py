#!/usr/bin/env python3
"""
Synthetic Claude.ai export fixture generator.

Builds a small, fake export ZIP (plus a matching mapping file) so the extractor can
be exercised offline, without touching anyone's real conversation history. Nothing
here is real data; every UUID is a fixed literal so runs are reproducible.

Usage:
    python tests/make_fixture.py [output_dir]     # default: ./fixtures

Produces:
    <output_dir>/fixture_export.zip     conversations.json + projects.json + users.json
    <output_dir>/fixture_mapping.json   schema-v1 mapping covering two of three projects

The fixture deliberately contains the awkward cases:
  * a project whose docs carry no `content` (metadata-only — must report 0 docs)
  * a duplicate doc filename (must be deduplicated)
  * a conversation that shares no vocabulary with its project name (keyword matching
    misses it; the mapping finds it)
  * a project absent from the mapping (exercises the fuzzy / none fallback)
  * a second uncovered project whose name keyword-matches a conversation the mapping
    already files under a *different* project — the mapping must win
  * a standalone conversation in no project at all (exercises _unfiled)
  * message content in both string and content-block form, plus an attachment
"""

import json
import sys
import zipfile
from pathlib import Path

P1 = "11111111-1111-4111-8111-111111111111"   # Marketing Course 2700  (mapped)
P2 = "22222222-2222-4222-8222-222222222222"   # Lean Metadata Project  (mapped, no docs)
P3 = "33333333-3333-4333-8333-333333333333"   # Zebra Analysis         (NOT mapped)
P4 = "44444444-4444-4444-8444-444444444444"   # Capstone Review        (NOT mapped, would
                                              #   keyword-steal C3 from P1 if allowed to)

C1 = "aaaaaaaa-0001-4000-8000-000000000001"   # -> P1, title matches keywords
C2 = "aaaaaaaa-0002-4000-8000-000000000002"   # -> P1, title matches keywords
C3 = "aaaaaaaa-0003-4000-8000-000000000003"   # -> P1, title shares NO vocabulary
C4 = "aaaaaaaa-0004-4000-8000-000000000004"   # -> P2
C5 = "aaaaaaaa-0005-4000-8000-000000000005"   # -> unmapped, matches P3 keywords
C6 = "aaaaaaaa-0006-4000-8000-000000000006"   # -> unmapped, matches nothing

ORG = "99999999-9999-4999-8999-999999999999"

PROJECTS = [
    {
        "uuid": P1,
        "name": "Marketing Course 2700",
        "description": "Course design for the 2700 marketing seminar.",
        "created_at": "2025-11-03T09:15:00Z",
        "updated_at": "2026-02-01T12:00:00Z",
        "prompt_template": "You are a teaching assistant for MKTG 2700. Be concise.\n",
        "docs": [
            {
                "uuid": "d0000000-0000-4000-8000-00000000000a",
                "filename": "syllabus.md",
                "content": "# MKTG 2700 Syllabus\n\nWeek 1: positioning.\nWeek 2: segmentation.\n",
                "created_at": "2025-11-03T09:20:00Z",
            },
            {
                "uuid": "d0000000-0000-4000-8000-00000000000b",
                "filename": "syllabus.md",
                "content": "# MKTG 2700 Syllabus (duplicate upload)\n\nShould be deduplicated.\n",
                "created_at": "2025-11-04T09:20:00Z",
            },
            {
                "uuid": "d0000000-0000-4000-8000-00000000000c",
                "filename": "rubric",
                "content": "{\n  \"criteria\": [\"clarity\", \"evidence\"],\n  \"points\": 100\n}\n",
                "created_at": "2025-11-05T09:20:00Z",
            },
        ],
    },
    {
        # Metadata-only project: docs exist as records but carry no extracted content.
        # A doc count of 0 is the correct answer here, not a bug to fix.
        "uuid": P2,
        "name": "Lean Metadata Project",
        "description": "",
        "created_at": "2026-01-15T08:00:00Z",
        "updated_at": "2026-01-16T08:00:00Z",
        "prompt_template": "",
        "docs": [
            {
                "uuid": "d0000000-0000-4000-8000-00000000000d",
                "filename": "reference.pdf",
                "created_at": "2026-01-15T08:05:00Z",
            },
            {
                "uuid": "d0000000-0000-4000-8000-00000000000e",
                "filename": "diagram.png",
                "content": "",
                "created_at": "2026-01-15T08:06:00Z",
            },
        ],
    },
    {
        "uuid": P3,
        "name": "Zebra Analysis",
        "description": "Left out of the mapping on purpose.",
        "created_at": "2026-03-20T14:30:00Z",
        "updated_at": "2026-03-21T14:30:00Z",
        "prompt_template": "",
        "docs": [
            {
                "uuid": "d0000000-0000-4000-8000-00000000000f",
                "filename": "notes.txt",
                "content": "Stripe pattern observations.\n",
                "created_at": "2026-03-20T14:35:00Z",
            },
        ],
    },
    {
        "uuid": P4,
        "name": "Capstone Review",
        "description": "Uncovered, and its keywords collide with a conversation owned by P1.",
        "created_at": "2026-02-10T10:00:00Z",
        "updated_at": "2026-02-11T10:00:00Z",
        "prompt_template": "",
        "docs": [],
    },
]


def _msg(uuid, sender, text, created_at, attachments=None, blocks=False):
    msg = {
        "uuid": uuid,
        "sender": sender,
        "created_at": created_at,
        "updated_at": created_at,
        "attachments": attachments or [],
        "files": [],
    }
    if blocks:
        msg["content"] = [{"type": "text", "text": text}]
        msg["text"] = ""
    else:
        msg["content"] = text
    return msg


CONVERSATIONS = [
    {
        "uuid": C1,
        "name": "Marketing 2700 week one plan",
        "created_at": "2025-11-06T10:00:00Z",
        "updated_at": "2025-11-06T10:30:00Z",
        "chat_messages": [
            _msg("m1-1", "human", "Draft the week one lesson plan.", "2025-11-06T10:00:00Z"),
            _msg("m1-2", "assistant", "Here is a plan:\n\n1. Positioning\n2. Segmentation\n",
                 "2025-11-06T10:01:00Z", blocks=True),
        ],
    },
    {
        "uuid": C2,
        "name": "2700 grading rubric revisions",
        "created_at": "2025-11-09T11:00:00Z",
        "updated_at": "2025-11-09T11:45:00Z",
        "chat_messages": [
            _msg("m2-1", "human", "Tighten the rubric wording.", "2025-11-09T11:00:00Z",
                 attachments=[{
                     "file_name": "old_rubric.txt",
                     "file_type": "text/plain",
                     "extracted_content": "Clarity: 50 points. Evidence: 50 points. "
                                          "This attachment is long enough to be saved to disk.\n",
                 }]),
            _msg("m2-2", "assistant", "Revised rubric attached below.", "2025-11-09T11:02:00Z"),
        ],
    },
    {
        # Belongs to P1 but shares no vocabulary with "Marketing Course 2700".
        # Keyword matching cannot find this; the mapping can.
        "uuid": C3,
        "name": "Ideas for the final capstone brief",
        "created_at": "2025-12-01T09:00:00Z",
        "updated_at": "2025-12-01T09:20:00Z",
        "chat_messages": [
            _msg("m3-1", "human", "Suggest three capstone briefs.", "2025-12-01T09:00:00Z"),
            _msg("m3-2", "assistant", "1. Local retailer repositioning\n2. Category entry\n3. Pricing test\n",
                 "2025-12-01T09:01:00Z"),
        ],
    },
    {
        "uuid": C4,
        "name": "Lean Metadata questions",
        "created_at": "2026-01-16T13:00:00Z",
        "updated_at": "2026-01-16T13:10:00Z",
        "chat_messages": [
            _msg("m4-1", "human", "What is in this project?", "2026-01-16T13:00:00Z"),
            _msg("m4-2", "assistant", "Metadata only — no knowledge documents were uploaded.",
                 "2026-01-16T13:01:00Z"),
        ],
    },
    {
        "uuid": C5,
        "name": "Zebra stripe measurements",
        "created_at": "2026-03-22T15:00:00Z",
        "updated_at": "2026-03-22T15:30:00Z",
        "chat_messages": [
            _msg("m5-1", "human", "Summarise the stripe width data.", "2026-03-22T15:00:00Z"),
            _msg("m5-2", "assistant", "Mean width 4.2cm, sd 0.6cm.", "2026-03-22T15:01:00Z"),
        ],
    },
    {
        # In no project at all — the honest _unfiled case.
        "uuid": C6,
        "name": "Weeknight dinner ideas",
        "created_at": "2026-04-02T18:00:00Z",
        "updated_at": "2026-04-02T18:15:00Z",
        "chat_messages": [
            _msg("m6-1", "human", "Something quick with lentils?", "2026-04-02T18:00:00Z"),
            _msg("m6-2", "assistant", "Lentil ragu, 25 minutes.", "2026-04-02T18:01:00Z"),
        ],
    },
]

MAPPING = {
    "schema": 1,
    "fetched_at": "2026-08-20T10:00:00Z",
    "org_uuid": ORG,
    "projects": {
        P1: "Marketing Course 2700",
        P2: "Lean Metadata Project",
    },
    "conversations": {
        C1: {"project_uuid": P1, "project_name": "Marketing Course 2700"},
        C2: {"project_uuid": P1, "project_name": "Marketing Course 2700"},
        C3: {"project_uuid": P1, "project_name": "Marketing Course 2700"},
        C4: {"project_uuid": P2, "project_name": "Lean Metadata Project"},
    },
}

USERS = [{"uuid": "u0000000-0000-4000-8000-000000000001",
          "full_name": "Fixture User",
          "email_address": "fixture@example.invalid"}]


def build(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "fixture_export.zip"
    mapping_path = out_dir / "fixture_mapping.json"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("projects.json", json.dumps(PROJECTS, indent=2))
        zf.writestr("conversations.json", json.dumps(CONVERSATIONS, indent=2))
        zf.writestr("users.json", json.dumps(USERS, indent=2))

    mapping_path.write_text(json.dumps(MAPPING, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {zip_path}")
    print(f"Wrote {mapping_path}")
    return zip_path, mapping_path


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures"))
