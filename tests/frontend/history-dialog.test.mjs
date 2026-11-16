/**
 * The library's history, opened from either page's gear (web/common/history-dialog.js;
 * docs/findings.md, #266).
 *
 * Undo existed only in the CLI and the MCP server. The gear's History... lists the
 * library's last changes as /api/history answers them, with Undo beside each that can be
 * undone: the first press asks the server to rehearse (apply false) and says what would
 * be put back, the button becoming Confirm undo; the second makes it, reads the list
 * again and tells the page, which shows what its photos hold now. A refusal is said, and
 * nothing more is asked. The writes of one action sent in chunks are one line, whose Undo
 * posts each of them newest first through the same route (tagpup.services.journal.lines).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";
import { OPEN_DIALOG } from "../../web/common/dialog.js";

afterEach(() => closeAllApps());

const LIBRARY = "kr-track";

function change(id, operation, extra = {}) {
  return { id, operation, status: "applied", created: "2026-09-25 10:00:00", applied: "2026-09-25 10:00:01",
           undone: null, summary: {}, rows: {}, files: {}, undoable: true, ...extra };
}

function historyAnswer(changes) {
  return { library: LIBRARY, changes };
}

const REHEARSED = { success: true, dry_run: true, change: 12, attempted: 2, changed: 0, refused: null,
                    errors: [], differences: [], would_put_back: 2 };
const UNDONE = { success: true, dry_run: false, change: 12, attempted: 2, changed: 2, refused: null,
                 errors: [], differences: [] };

async function openFrom(appName, t, { changes, undo, routes = [] }) {
  let listed = changes;
  const server = new FakeServer();
  // A request's body to each of `routes`, [match, (body) => answer].
  for (const [match, answer] of routes) server.on(match, () => answer(server.calls.at(-1).body || {}));
  server
    .on("/api/apps", { this: appName === "tagtuner" ? "tuner" : "tagpup", apps: {} })
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", [])
    // Before /api/history, which the undo's URL holds too: the first route that matches answers.
    .on("/api/history/12/undo", () => {
      const body = server.calls.at(-1).body || {};
      const answer = undo(body.apply);
      if (body.apply && answer.changed) listed = listed.map((c) => (c.id === 12 ? { ...c, status: "undone", undoable: false } : c));
      return answer;
    })
    .on("/api/history", () => historyAnswer(listed));
  const ctx = await loadApp(appName, { t, url: `http://localhost:8080/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  const { document, window } = ctx;
  click(window, document.getElementById("btn-gear"));
  await flush(window);
  click(window, document.querySelector('[data-action="library-history"]'));
  await flush(window, 6);
  ctx.server = server;
  ctx.dialog = document.getElementById("history-modal");
  ctx.undoButton = (id) => ctx.dialog.querySelector(`.history-undo[data-change="${id}"]`);
  ctx.note = (id) => ctx.dialog.querySelector(`.history-change[data-change="${id}"] .history-note`);
  ctx.undoCalls = () => server.calls.filter((c) => c.url.includes("/api/history/12/undo"))
    .map((c) => (c.body || {}).apply);
  return ctx;
}

const CHANGES = [
  change(12, "add to all selected", { files: { done: 2 } }),
  change(11, "change settings", { rows: { settings: { update: 1 } } }),
  change(10, "stamp settings with the defaults", { undoable: false, rows: { settings: { insert: 9 } },
                                                   why_not: "A library's first settings cannot be undone; change them instead." }),
];

for (const appName of ["tagpup", "tagtuner"]) {
  describe(`${appName}'s History...`, () => {
    test("lists the library's changes, newest first, with Undo beside each that can be undone", async (t) => {
      const { dialog, document, undoButton } = await openFrom(appName, t, { changes: CHANGES, undo: () => REHEARSED });
      assert.ok(!dialog.classList.contains("hidden"));
      assert.ok(document.querySelector(OPEN_DIALOG), "the pages' shortcuts would not know it is open");
      assert.equal(document.getElementById("history-title").textContent, `History: ${LIBRARY}`);
      const rows = [...dialog.querySelectorAll(".history-change")];
      assert.deepEqual(rows.map((r) => r.querySelector(".history-operation").textContent),
                       ["add to all selected", "change settings", "stamp settings with the defaults"]);
      assert.equal(rows[0].querySelector(".history-counts").textContent, "2 photo file(s)");
      assert.equal(rows[1].querySelector(".history-counts").textContent, "1 row(s)");
      assert.ok(undoButton(12) && undoButton(11));
      assert.equal(undoButton(10), null, "the settings stamp is offered to be undone");
      assert.equal(rows[2].querySelector(".history-why").textContent,
                   "A library's first settings cannot be undone; change them instead.");
    });

    test("Undo rehearses first, then Confirm undo makes it and the list is read again", async (t) => {
      const ctx = await openFrom(appName, t, { changes: CHANGES, undo: (apply) => (apply ? UNDONE : REHEARSED) });
      const { window, undoButton, note, undoCalls, dialog } = ctx;
      click(window, undoButton(12));
      await flush(window, 6);
      assert.deepEqual(undoCalls(), [false], "the first press did not only rehearse");
      assert.equal(undoButton(12).textContent, "Confirm undo");
      assert.match(note(12).textContent, /would put back 2/);
      click(window, undoButton(12));
      await flush(window, 8);
      assert.deepEqual(undoCalls(), [false, true]);
      const first = dialog.querySelector('.history-change[data-change="12"]');
      assert.equal(first.querySelector(".history-status").textContent, "undone");
      assert.equal(undoButton(12), null, "an undone change is offered to be undone again");
      assert.match(dialog.querySelector(".history-status-line").textContent, /Undone: 2 put back/);
    });

    test("a refusal is said, and nothing is made", async (t) => {
      const refused = { success: false, dry_run: true, change: 12, attempted: 0, changed: 0,
                        refused: "change 13 (rename tag), applied after it, changed the same rows: undo it first",
                        errors: [], differences: [], error: "undo it first" };
      const { window, undoButton, note, undoCalls } = await openFrom(appName, t, { changes: CHANGES, undo: () => refused });
      click(window, undoButton(12));
      await flush(window, 6);
      assert.deepEqual(undoCalls(), [false]);
      assert.equal(undoButton(12).textContent, "Undo");
      assert.match(note(12).textContent, /^Cannot be undone: change 13/);
    });

    test("the writes of one action are one line, and its Undo posts each, newest first, rehearsed then made", async (t) => {
      // As /api/history sends a group's line (tagpup.web.history_routes._listed): its newest change's id, what is left of it.
      const group = change(15, "add to all selected", { summary: { group: "drain-1", photos: 50 }, files: { done: 50 },
        group: "drain-1", writes: 3, members: [{ id: 15, why_not: null }, { id: 14, why_not: null },
                                               { id: 13, why_not: "change 15 (add to all selected), applied after it, wrote the same photo files: undo it first" }] });
      const posted = [];
      const undoOf = (id) => (apply) => {
        posted.push([id, apply]);
        return apply ? { ...UNDONE, change: id, changed: 20 } : { ...REHEARSED, change: id, would_put_back: 20 };
      };
      const routes = [15, 14, 13].map((id) => [`/api/history/${id}/undo`, (body) => undoOf(id)(body.apply)]);
      const ctx = await openFrom(appName, t, { changes: [group, ...CHANGES], undo: () => REHEARSED, routes });
      const { window, dialog, undoButton, note } = ctx;
      const line = dialog.querySelector('.history-change[data-change="15"]');
      assert.equal(line.querySelector(".history-operation").textContent, "Tags on 50 photos (3 writes)");
      assert.equal(line.querySelector(".history-counts").textContent, "50 photo file(s)");
      click(window, undoButton(15));
      await flush(window, 12);
      // The rehearsal asks of those the server offers now; one waiting on a newer write of the group is made after it.
      assert.deepEqual(posted, [[15, false], [14, false]]);
      assert.equal(undoButton(15).textContent, "Confirm undo");
      assert.match(note(15).textContent, /Undoing its 2 write\(s\) would put back 40 item\(s\)/);
      click(window, undoButton(15));
      await flush(window, 16);
      assert.deepEqual(posted.slice(2), [[15, true], [14, true], [13, true]], "not every write, or not newest first");
      assert.match(dialog.querySelector(".history-status-line").textContent, /^Undone: 60 put back\.$/);
    });
  });
}
