/*
 * fetch_mapping.js — build a conversation → project mapping from a logged-in claude.ai session
 * ============================================================================================
 *
 * WHY THIS EXISTS
 * ---------------
 * Claude.ai's account data export does not record which project a conversation belongs to.
 * Conversation objects carry no `project_uuid`, and `projects.json` does not list its
 * conversations. The link exists server-side, so this script reads it from the web app's own
 * API using the session you are already signed in with, and saves it as `mapping.json` for
 * `claude_export_extractor.py --mapping`.
 *
 * HOW TO USE
 * ----------
 *   1. Sign in at https://claude.ai in your browser.
 *   2. Open DevTools (F12 / Cmd-Opt-I) → Console.
 *   3. Paste this entire file and press Enter.
 *   4. Wait for it to finish; `mapping.json` downloads automatically.
 *   5. python claude_export_extractor.py export.zip --mapping mapping.json
 *
 * ENDPOINTS THIS DEPENDS ON — READ THIS
 * -------------------------------------
 * These are Claude.ai's *internal* web-app endpoints. They are undocumented, unversioned, and
 * not covered by any compatibility promise: Anthropic may change or remove them at any time,
 * without notice. Nothing here is an official API.
 *
 *   GET /api/organizations
 *       → the signed-in account's organizations; used only to find an org UUID.
 *   GET /api/organizations/{org_uuid}/projects
 *       → the account's projects: uuid + name.
 *   GET /api/organizations/{org_uuid}/projects/{project_uuid}/conversations_v2?limit=N
 *       → the conversations filed under one project. Paginated.
 *
 * The paths above were transcribed from a third-party userscript and have NOT been verified
 * against a live session by the author of this file. If the script fails, confirm the real
 * paths yourself: DevTools → Network → filter "api" → click into a project in the web app and
 * read the requests the page makes. Then edit ENDPOINTS below to match. Please open an issue
 * with what you observed so this comment can be corrected.
 *
 *   Observed on <date> by <who>: <paste the real paths here once verified>
 *
 * WHAT IT DOES NOT DO
 * -------------------
 * No credentials are read, prompted for, or stored — it relies entirely on the session cookie
 * your browser already holds. Every request is same-origin; nothing is sent anywhere except
 * claude.ai, and the only output is a local file download. Read it before you run it.
 */

(async () => {
  "use strict";

  // ── Configuration — edit these if the endpoints have moved ──────────────────
  const ENDPOINTS = {
    organizations: () => `/api/organizations`,
    projects: (org) => `/api/organizations/${org}/projects`,
    projectConversations: (org, project, limit, cursor) => {
      let url = `/api/organizations/${org}/projects/${project}/conversations_v2?limit=${limit}`;
      if (cursor) url += `&starting_after=${encodeURIComponent(cursor)}`;
      return url;
    },
  };

  const ORG_UUID = null;      // set a string here to skip org auto-detection
  const PAGE_SIZE = 50;       // conversations requested per page
  const MAX_PAGES = 200;      // runaway guard; a warning is printed if it is ever hit
  const DELAY_MS = 300;       // pause between project requests, to stay polite

  // ── Helpers ────────────────────────────────────────────────────────────────
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function getJSON(path) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { accept: "application/json" },
    });
    if (res.status === 401 || res.status === 403) {
      throw new Error(`Not signed in (HTTP ${res.status}) for ${path} — sign in to claude.ai and retry.`);
    }
    if (res.status === 404) {
      throw new Error(`No such endpoint: ${path} (HTTP 404) — the API has probably moved. ` +
                      `Check DevTools → Network and update ENDPOINTS at the top of this script.`);
    }
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
    return res.json();
  }

  // Responses come back either as a bare array or wrapped in {data: [...]}.
  const asList = (body) =>
    Array.isArray(body) ? body
      : Array.isArray(body?.data) ? body.data
      : Array.isArray(body?.conversations) ? body.conversations
      : [];

  // Pagination cursor, under whichever key this endpoint version uses.
  function nextCursor(body, page) {
    // An explicit has_more:false is authoritative — no cursor, and nothing left behind.
    if (!Array.isArray(body) && body?.has_more === false) return null;

    const cursor = Array.isArray(body)
      ? null
      : body?.last_id ?? body?.next_cursor ?? body?.cursor ?? null;
    if (cursor) return cursor;

    // A full page and no way to ask for the next one: we cannot tell whether more exist.
    // This has to fire for bare-array responses too — an endpoint that returns a plain
    // array is exactly the case where pagination would stop after page one in silence,
    // and the conversations left behind would just look unfiled to the extractor.
    if (page.length >= PAGE_SIZE) {
      const shape = Array.isArray(body)
        ? "the response is a bare array, with no pagination envelope"
        : `response keys: [${Object.keys(body || {}).join(", ")}]`;
      console.warn(
        `  ! Got a full page of ${page.length} with no pagination cursor — ${shape}. ` +
        `Conversations may be missing. Check how the real endpoint paginates and update ` +
        `nextCursor() (and ENDPOINTS.projectConversations) in this script to match.`
      );
    }
    return null;
  }

  // ── Fetch ──────────────────────────────────────────────────────────────────
  console.log("Fetching Claude.ai project ↔ conversation mapping…");

  let org = ORG_UUID;
  if (!org) {
    const orgs = asList(await getJSON(ENDPOINTS.organizations()));
    if (!orgs.length) throw new Error("No organizations returned — are you signed in?");
    org = orgs[0].uuid || orgs[0].id;
    if (orgs.length > 1) {
      console.warn(`Found ${orgs.length} organizations; using the first (${org}). ` +
                   `Set ORG_UUID at the top of this script to choose a different one.`);
    }
  }
  console.log(`Organization: ${org}`);

  const projects = asList(await getJSON(ENDPOINTS.projects(org)));
  console.log(`Projects: ${projects.length}`);

  const mapping = {
    schema: 1,
    fetched_at: new Date().toISOString(),
    org_uuid: org,
    projects: {},
    conversations: {},
  };

  let totalConvs = 0;
  for (const [i, project] of projects.entries()) {
    const projectUuid = project.uuid || project.id;
    const projectName = project.name || project.title || "Untitled";
    mapping.projects[projectUuid] = projectName;

    let cursor = null;
    let pages = 0;
    let count = 0;
    do {
      const body = await getJSON(ENDPOINTS.projectConversations(org, projectUuid, PAGE_SIZE, cursor));
      const page = asList(body);
      for (const conv of page) {
        const convUuid = conv.uuid || conv.id;
        if (!convUuid) continue;
        mapping.conversations[convUuid] = {
          project_uuid: projectUuid,
          project_name: projectName,
        };
        count += 1;
      }
      cursor = page.length ? nextCursor(body, page) : null;
      pages += 1;
      if (pages >= MAX_PAGES && cursor) {
        console.warn(`  ! Stopped at ${MAX_PAGES} pages for "${projectName}" — results are truncated. ` +
                     `Raise MAX_PAGES and re-run.`);
        break;
      }
    } while (cursor);

    totalConvs += count;
    console.log(`  [${i + 1}/${projects.length}] ${projectName} — ${count} conversations`);
    if (i < projects.length - 1) await sleep(DELAY_MS);
  }

  // ── Save ───────────────────────────────────────────────────────────────────
  const blob = new Blob([JSON.stringify(mapping, null, 2) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "mapping.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);

  console.log(`Done. ${projects.length} projects, ${totalConvs} conversations → mapping.json`);
  console.log("Next: python claude_export_extractor.py export.zip --mapping mapping.json");
})().catch((err) => {
  console.error("fetch_mapping.js failed:", err.message || err);
  console.error("Nothing was saved. See the endpoint notes at the top of this script.");
});
