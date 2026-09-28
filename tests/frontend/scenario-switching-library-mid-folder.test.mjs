/**
 * Owner scenario: TagPup's library picker is changed while a folder is open and a
 * bulk write (a suggestion chip, Apply All) is still queued or out.
 *
 * edits.js's queuePhotoWrite says leaving "waits for none" of a bulk write -- only
 * for the open photo's own save (openPhotoWrite, covered alone by
 * unsaved-edits.test.mjs and photo-write-queue.test.mjs). So the picker's change goes
 * at once; what it must not do is let a write queued under the folder's library land
 * on the library just chosen. api.js resolves the library from the page's URL at each
 * call (web/common/api.js), and this checks that a write queued before the switch
 * still carries the library it was opened in once it is finally sent.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

const FOLDER = "D:/Library/2020";

const PHOTOS = [
  photoRecord({ filename: "a.jpg", tags: [], people: [] }),
  photoRecord({ filename: "b.jpg", tags: [], people: [] }),
];

const OFFERED = ["Cross Country"];

function build() {
  const held = [];
  const hold = () => new Promise((resolve) => held.push(resolve));
  const server = new FakeServer()
    .on("/api/tags", OFFERED)
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p, tags: [...p.tags], people: [...p.people] })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photos/bulk-tags", hold)
    .on("/api/folder/suggest-status", {
      status: "completed",
      suggestions: Object.fromEntries(PHOTOS.map((p) => [p.path, {
        people: [], tags: OFFERED.map((tag) => ({ tag, score: 0.9 })),
      }])),
    });
  return { server, held };
}

async function loadSelected(t) {
  const { server, held } = build();
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/alpha/", server });
  await openFolder(ctx, FOLDER);
  for (const box of ctx.document.querySelectorAll(".thumbnail-checkbox")) click(ctx.window, box);
  await flush(ctx.window, 6);
  for (let i = 0; i < 50 && chips(ctx).length < OFFERED.length; i++) {
    await new Promise((resolve) => ctx.window.setTimeout(resolve, 20));
  }
  ctx.window.alert = () => {};
  return { ...ctx, held };
}

const chips = (ctx) => [...ctx.document.querySelectorAll("#selection-suggested-tags-list .suggestion-chip")];
const chip = (ctx, name) => chips(ctx).find((c) => c.textContent.includes(name));
const said = (ctx) => ctx.document.getElementById("write-queue").querySelector(".write-queue-status").textContent;

afterEach(() => closeAllApps());

describe("choosing another library while a bulk write is queued", () => {
  test("leaves at once, and the queued write still goes to the library it was opened in", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Cross Country"));
    await flush(ctx.window, 8);
    assert.equal(ctx.held.length, 1, "the bulk write was not sent");
    assert.equal(ctx.server.urls().at(-1), "/alpha/api/photos/bulk-tags");
    assert.equal(said(ctx), "Saving 1 of 1...");

    // Nothing asks to keep the write: only an open photo's own save blocks leaving
    // (edits.js queueWriteOf), never a bulk write.
    let confirmed = false;
    ctx.window.confirm = () => { confirmed = true; return true; };
    const select = ctx.document.getElementById("db-select");
    select.value = "beta";
    select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.equal(confirmed, false, "the picker asked before leaving, for a bulk write");

    // The write, still out, was never re-sent under the library just chosen.
    assert.equal(ctx.held.length, 1, "a second write went out on switching");
    assert.equal(
      ctx.server.urlsStartingWith("/beta/").length, 0,
      "a request reached the library just chosen before its page loaded"
    );
    assert.ok(
      ctx.server.urlsStartingWith("/alpha/").includes("/alpha/api/photos/bulk-tags"),
      "the queued write's URL no longer names the library it was opened in"
    );

    // Answered late, it still lands on the page it was queued from -- the only
    // library it was ever addressed to.
    ctx.held.shift()({ success: true, written: {} });
    await flush(ctx.window, 8);
    assert.equal(said(ctx), "All changes saved");
    assert.equal(ctx.server.urlsStartingWith("/beta/").length, 0, "the write ended up reaching beta");
  });
});
