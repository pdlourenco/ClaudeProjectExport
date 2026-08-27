/*
 * fetch_mapping.js — build a conversation → project mapping from a logged-in claude.ai session
 * ============================================================================================
 *
 * WHY THIS EXISTS
 * ---------------
 * Claude.ai's account data export does not record which project a conversation belongs to.
 * Conversation objects carry no `project_uuid`, and a project does not list its conversations.
 * The link exists server-side, so this script reads it from the web app's own API using the
 * session you are already signed in with, and saves it as `mapping.json` for
 * `claude_export_extractor.py --mapping`.
 *
 * HOW TO USE
 * ----------
 *   1. Request your data export FIRST (Settings → Account → Export Data), then run this
 *      while you wait for the email. The export is a snapshot taken when you request it, so
 *      fetching the mapping afterwards guarantees it covers everything the export contains.
 *   2. Sign in at https://claude.ai and open DevTools (F12 / Cmd-Opt-I) → Console.
 *   3. Paste this entire file and press Enter.
 *   4. Wait for it to finish; `mapping.json` downloads automatically.
 *   5. python claude_export_extractor.py export.zip --mapping mapping.json
 *
 * IF IT FAILS
 * -----------
 * It will tell you what it tried and what came back. Set PROBE_ONLY = true below to run just
 * the diagnostic — it prints each endpoint's status and the shape of what it returns, without
 * fetching anything. Paste that output into an issue, or use it to correct CANDIDATES below.
 *
 * ENDPOINTS THIS DEPENDS ON — READ THIS
 * -------------------------------------
 * These are Claude.ai's *internal* web-app endpoints. They are undocumented, unversioned, and
 * covered by no compatibility promise: Anthropic may change or remove them at any time,
 * without notice. Nothing here is an official API. The paths below were transcribed from a
 * third-party userscript and have NOT been verified against a live session by this file's
 * author — which is why each one is a list of candidates that gets probed in order rather
 * than a single hardcoded guess.
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

  // ── Configuration ──────────────────────────────────────────────────────────
  const PROBE_ONLY = false;   // true = report what the API returns, fetch nothing
  const ORG_UUID = null;      // set a string to skip organization auto-detection
  const PAGE_SIZE = 100;      // items requested per page
  const MAX_PAGES = 500;      // runaway guard; warns if ever reached
  const DELAY_MS = 250;       // pause between requests, to stay polite

  // Each endpoint is a list of candidates, tried in order until one answers. Add yours at
  // the front if you find the real path in DevTools → Network.
  const CANDIDATES = {
    organizations: () => [
      "/api/organizations",
      "/api/bootstrap",
    ],
    projects: (org) => [
      `/api/organizations/${org}/projects`,
    ],
    // Strategy A: every conversation in one listing. If the objects carry a project
    // reference, the whole mapping comes from this one endpoint — no per-project paging.
    allConversations: (org, cursor, offset) => [
      `/api/organizations/${org}/chat_conversations?limit=${PAGE_SIZE}` + page(cursor, offset),
      `/api/organizations/${org}/conversations?limit=${PAGE_SIZE}` + page(cursor, offset),
    ],
    // Strategy B: ask each project for its conversations. Used only if strategy A finds no
    // project reference on a conversation.
    projectConversations: (org, project, cursor, offset) => [
      `/api/organizations/${org}/projects/${project}/conversations_v2?limit=${PAGE_SIZE}` + page(cursor, offset),
      `/api/organizations/${org}/projects/${project}/conversations?limit=${PAGE_SIZE}` + page(cursor, offset),
    ],
  };

  // Cursor if the response gave us one, otherwise offset. A listing that answers with a bare
  // array — no envelope, no cursor — is paged by offset or not at all.
  const page = (cursor, offset) =>
    cursor ? `&starting_after=${encodeURIComponent(cursor)}`
      : offset ? `&offset=${offset}`
        : "";

  // Keys a conversation might carry pointing at its project.
  const PROJECT_KEYS = ["project_uuid", "project_id", "projectUuid"];
  const PROJECT_OBJECT_KEYS = ["project"];

  // ── Plumbing ───────────────────────────────────────────────────────────────
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const tried = [];

  async function attempt(path) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { accept: "application/json" },
    });
    const note = { path, status: res.status };
    tried.push(note);
    if (res.status === 401 || res.status === 403) {
      throw new Error(`Not signed in (HTTP ${res.status}). Sign in to claude.ai and retry.`);
    }
    if (!res.ok) {
      note.ok = false;
      return null;
    }
    try {
      const body = await res.json();
      note.ok = true;
      note.shape = Array.isArray(body) ? `array[${body.length}]` : `object{${Object.keys(body).join(",")}}`;
      return body;
    } catch {
      note.ok = false;
      note.shape = "not JSON";
      return null;
    }
  }

  /** Try each candidate in order; return the first that answers, or null. */
  async function firstWorking(paths) {
    for (const path of paths) {
      const body = await attempt(path);
      if (body !== null) return { body, path };
      await sleep(DELAY_MS);
    }
    return null;
  }

  const asList = (body) =>
    Array.isArray(body) ? body
      : Array.isArray(body?.data) ? body.data
      : Array.isArray(body?.conversations) ? body.conversations
      : Array.isArray(body?.projects) ? body.projects
      : [];

  function nextCursor(body) {
    if (!Array.isArray(body) && body?.has_more === false) return null;
    if (Array.isArray(body)) return null;
    return body?.last_id ?? body?.next_cursor ?? body?.cursor ?? null;
  }

  /** Page through an endpoint until it stops returning anything new.
   *
   * Deliberately not "until a page looks short": an API that caps `limit` lower than we ask
   * would make the very first page look like the last, which is how a listing silently comes
   * back with only its first page. Paging until nothing new arrives costs one extra request
   * and cannot be fooled that way. Deduplicating by uuid is what makes it safe — an endpoint
   * that ignores `offset` returns page one forever, and that is detected as "nothing new"
   * rather than looping.
   */
  async function pageThrough(makePaths, label) {
    const items = [];
    const seen = new Set();
    let cursor = null, chosen = -1, pages = 0, path = null, stalled = false;

    while (true) {
      const paths = makePaths(cursor, items.length);
      let got = null;
      if (chosen >= 0) {
        // Stay on the candidate that answered the first page.
        const body = await attempt(paths[chosen]);
        if (body !== null) got = { body, path: paths[chosen] };
      } else {
        for (let i = 0; i < paths.length; i++) {
          const body = await attempt(paths[i]);
          if (body !== null) { got = { body, path: paths[i] }; chosen = i; break; }
          await sleep(DELAY_MS);
        }
      }
      if (!got) return path ? { items, path } : null;
      path = got.path;

      const batch = asList(got.body);
      let fresh = 0;
      for (const item of batch) {
        const id = item?.uuid || item?.id;
        if (id && seen.has(id)) continue;
        if (id) seen.add(id);
        items.push(item);
        fresh += 1;
      }

      if (!batch.length) break;                 // exhausted
      if (fresh === 0) { stalled = true; break; } // same page again: paging is not working
      cursor = nextCursor(got.body);
      if (++pages >= MAX_PAGES) {
        console.warn(`  ! Stopped at ${MAX_PAGES} pages for ${label}; raise MAX_PAGES.`);
        break;
      }
      await sleep(DELAY_MS);
    }

    if (stalled && items.length >= PAGE_SIZE) {
      console.warn(`  ! ${label}: the next page repeated the previous one, so this listing ` +
                   `is probably capped at ${items.length}. Neither a cursor nor ?offset= ` +
                   `advanced it — check the counts below against the web app.`);
    }
    return { items, path };
  }

  /** The project a conversation points at, whatever key the API uses for it. */
  function projectRef(conv) {
    for (const key of PROJECT_KEYS) {
      if (conv?.[key]) return { uuid: conv[key], name: "" };
    }
    for (const key of PROJECT_OBJECT_KEYS) {
      const obj = conv?.[key];
      if (obj && typeof obj === "object" && (obj.uuid || obj.id)) {
        return { uuid: obj.uuid || obj.id, name: obj.name || obj.title || "" };
      }
    }
    return null;
  }

  function report() {
    console.log("\n── endpoints tried ──");
    for (const t of tried) {
      console.log(`  ${t.ok ? "ok  " : "FAIL"}  HTTP ${t.status}  ${t.path}` +
                  (t.shape ? `\n          -> ${t.shape}` : ""));
    }
    console.log("\nIf every candidate failed, open DevTools → Network, click into a project in " +
                "the web app, and note the request the page makes. Add that path to the front " +
                "of the matching list in CANDIDATES at the top of this script.");
  }

  // ── Run ────────────────────────────────────────────────────────────────────
  try {
    console.log("Fetching Claude.ai project ↔ conversation mapping…");

    let org = ORG_UUID;
    if (!org) {
      const got = await firstWorking(CANDIDATES.organizations());
      if (!got) throw new Error("Could not list organizations.");
      const orgs = asList(got.body).length
        ? asList(got.body)
        : (got.body?.account?.memberships || []).map((m) => m.organization).filter(Boolean);
      if (!orgs.length) throw new Error("No organizations in the response — are you signed in?");
      org = orgs[0].uuid || orgs[0].id;
      if (orgs.length > 1) {
        console.warn(`${orgs.length} organizations found; using the first (${org}). ` +
                     `Set ORG_UUID to choose another.`);
      }
    }
    console.log(`Organization: ${org}`);

    const projGot = await firstWorking(CANDIDATES.projects(org));
    if (!projGot) throw new Error("Could not list projects.");
    const projects = asList(projGot.body);
    console.log(`Projects: ${projects.length}  (via ${projGot.path})`);

    const mapping = {
      schema: 1,
      fetched_at: new Date().toISOString(),
      org_uuid: org,
      projects: {},
      conversations: {},
    };
    for (const p of projects) {
      mapping.projects[p.uuid || p.id] = p.name || p.title || "Untitled";
    }

    if (PROBE_ONLY) {
      const probe = await firstWorking(CANDIDATES.allConversations(org, null, 0));
      if (probe) {
        const sample = asList(probe.body)[0];
        console.log(`\nConversation listing: ${probe.path}`);
        console.log(`  keys on one conversation: [${Object.keys(sample || {}).join(", ")}]`);
        console.log(`  project reference found:  ${JSON.stringify(projectRef(sample))}`);
      }
      report();
      console.log("\nPROBE_ONLY is set — nothing was fetched or saved.");
      return;
    }

    // Strategy A — one listing, if conversations carry their project.
    let counts = {};
    const all = await pageThrough((c, o) => CANDIDATES.allConversations(org, c, o), "all conversations");
    const sample = all?.items?.find((c) => projectRef(c));
    if (sample) {
      console.log(`Listing all conversations via ${all.path} — they carry a project reference.`);
      for (const conv of all.items) {
        const ref = projectRef(conv);
        const uuid = conv.uuid || conv.id;
        if (!ref || !uuid) continue;
        mapping.conversations[uuid] = {
          project_uuid: ref.uuid,
          project_name: ref.name || mapping.projects[ref.uuid] || "",
        };
        counts[ref.uuid] = (counts[ref.uuid] || 0) + 1;
      }
    } else {
      // Strategy B — ask each project for its conversations.
      console.log(all?.items?.length
        ? "Conversations carry no project reference; asking each project instead."
        : "No conversation listing available; asking each project instead.");
      for (const [i, project] of projects.entries()) {
        const uuid = project.uuid || project.id;
        const name = project.name || project.title || "Untitled";
        const got = await pageThrough((c, o) => CANDIDATES.projectConversations(org, uuid, c, o), name);
        if (!got) {
          console.warn(`  ! No conversation endpoint answered for "${name}".`);
          continue;
        }
        for (const conv of got.items) {
          const cid = conv.uuid || conv.id;
          if (cid) mapping.conversations[cid] = { project_uuid: uuid, project_name: name };
        }
        counts[uuid] = got.items.length;
        console.log(`  [${i + 1}/${projects.length}] ${name} — ${got.items.length}`);
        if (i < projects.length - 1) await sleep(DELAY_MS);
      }
    }

    const total = Object.keys(mapping.conversations).length;
    if (!total) {
      console.error("No conversations were mapped — the mapping would be empty, so nothing was saved.");
      report();
      return;
    }

    // Per-project counts, so you can spot-check a few against the web UI. A project short a
    // page still reports "exact" downstream with full confidence, so this is worth a glance.
    console.log("\n── conversations per project ──");
    for (const [uuid, name] of Object.entries(mapping.projects)) {
      console.log(`  ${String(counts[uuid] || 0).padStart(4)}  ${name}`);
    }

    const blob = new Blob([JSON.stringify(mapping, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "mapping.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);

    console.log(`\nDone. ${projects.length} projects, ${total} conversations → mapping.json`);
    console.log("Next: python claude_export_extractor.py export.zip --mapping mapping.json");
  } catch (err) {
    console.error("fetch_mapping.js failed:", err.message || err);
    console.error("Nothing was saved.");
    report();
  }
})();
