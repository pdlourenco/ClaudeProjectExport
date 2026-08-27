/*
 * Paging check for fetch_mapping.js — the one piece of this repo that is not Python.
 *
 *     node tests/paging_check.mjs
 *
 * Run by tests/run_acceptance.py when node is available, skipped when it is not; the tool
 * itself stays stdlib-Python with no dependencies, and this only covers the browser script.
 *
 * It loads fetch_mapping.js verbatim and runs it against mocked listings, so it cannot drift
 * from the shipped code. Each scenario is a way a listing endpoint has behaved or plausibly
 * could: paging that works, paging that lies, and paging that stops halfway. What is asserted
 * is both the number of conversations recovered AND whether the run warned — a truncated
 * mapping that warns is survivable, one that stays quiet makes every affected project report
 * "exact" with total confidence.
 */

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = readFileSync(join(ROOT, "fetch_mapping.js"), "utf8");

const ORG = "org-1";
const PROJECTS = [{ uuid: "p-1", name: "Alpha" }, { uuid: "p-2", name: "Beta" }];
const TOTAL = 250;
const ALL = Array.from({ length: TOTAL }, (_, i) => ({
  uuid: `c${i}`,
  name: `chat ${i}`,
  ...(i % 3 ? { project_uuid: i % 2 ? "p-1" : "p-2" } : {}),   // two thirds are in a project
}));
const FILED = ALL.filter((c) => c.project_uuid).length;

const SCENARIOS = [
  { name: "offset honoured",                 mapped: FILED, warns: false },
  { name: "limit capped, offset honoured",   mapped: FILED, warns: false, cap: 30 },
  { name: "cursor in a pagination envelope", mapped: FILED, warns: false, envelope: true },
  { name: "offset ignored",                  mapped: 66,    warns: true,  ignoreOffset: true },
  { name: "capped below limit, offset ignored", mapped: 16, warns: true,  cap: 25, ignoreOffset: true },  // 25 fetched, 16 of them filed
  { name: "HTTP 429 partway through",        mapped: 133,   warns: true,  failOn: 3 },
];

function mockFetch(s) {
  let listingCalls = 0;
  return async (path) => {
    const json = (b) => ({ ok: true, status: 200, json: async () => b });
    if (path === "/api/organizations") return json([{ uuid: ORG }]);
    if (path.endsWith("/projects")) return json(PROJECTS);
    if (!path.includes("chat_conversations")) return { ok: false, status: 404, json: async () => ({}) };

    listingCalls += 1;
    if (s.failOn && listingCalls >= s.failOn) return { ok: false, status: 429, json: async () => ({}) };

    const url = new URL("https://x" + path);
    const cap = s.cap || Number(url.searchParams.get("limit"));
    if (s.envelope) {
      const from = Number(url.searchParams.get("starting_after") || 0);
      const slice = ALL.slice(from, from + cap);
      return json({ data: slice,
                    pagination: from + cap < TOTAL ? { next_cursor: String(from + cap) }
                                                   : { has_more: false } });
    }
    const from = s.ignoreOffset ? 0 : Number(url.searchParams.get("offset") || 0);
    return json(ALL.slice(from, from + cap));
  };
}

async function run(scenario) {
  const warnings = [];
  let saved = null;
  const ctx = {
    fetch: mockFetch(scenario),
    console: { log: () => {}, warn: (...a) => warnings.push(a.join(" ")), error: () => {} },
    Blob: class { constructor(parts) { saved = JSON.parse(parts[0]); } },
    URL: Object.assign(function (...a) { return new URL(...a); }, URL,
                       { createObjectURL: () => "blob:x", revokeObjectURL: () => {} }),
    document: { createElement: () => ({ click() {}, remove() {}, set href(_) {}, set download(_) {} }),
                body: { appendChild() {} } },
    setTimeout,
  };
  new Function(...Object.keys(ctx), SRC)(...Object.values(ctx));
  await new Promise((r) => setTimeout(r, 8000));
  return { mapped: saved ? Object.keys(saved.conversations).length : 0, warnings };
}

let failed = 0;
console.log("Paging");
for (const s of SCENARIOS) {
  const { mapped, warnings } = await run(s);
  const warned = warnings.length > 0;
  const ok = mapped === s.mapped && warned === s.warns;
  if (!ok) failed += 1;
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${s.name} — recovered ${mapped}/${s.mapped}, ` +
              `warned ${warned}${warned === s.warns ? "" : ` (wanted ${s.warns})`}`);
  if (!ok && warnings.length) console.log(`         ${warnings[0].trim().slice(0, 110)}`);
}
console.log(`\n${SCENARIOS.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
