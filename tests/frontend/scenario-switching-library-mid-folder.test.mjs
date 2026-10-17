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
 *
 * The folder goes with the switch (?path, web/common/library.js goToLibrary), and the
 * scan this browser kept of it was the first library's: the other library must scan
 * the folder itself and show none of the first one's records or suggestions as
 * "(cached)" (docs/findings.md, #1359).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports } from "./harness.mjs";

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

describe("choosing another library with a folder open", () => {
  // beta holds the same folder, with photos of its own and no suggestions yet.
  const BETA_PHOTOS = [photoRecord({ filename: "c.jpg", tags: [], people: [] })];

  function betaServer() {
    return new FakeServer()
      .on("/api/tags", [])
      .on("/api/people", [])
      .on("/api/taxonomy/tree", [])
      .on("/api/databases", { databases: ["alpha", "beta"] })
      .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
      .on("/api/folder/scan", () => BETA_PHOTOS.map((p) => ({ ...p, tags: [], people: [] })))
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/folder/suggest-status", { status: "idle" });
  }

  // What this browser keeps for the site, carried to the page the picker loads (one origin, one local storage).
  const kept = (window) => Object.fromEntries(Object.keys(window.localStorage).map((k) => [k, window.localStorage.getItem(k)]));
  // Copied into this realm: a kept scan is parsed in the page's, and its arrays are not this one's.
  const names = (ctx) => [...pageExports(ctx.window, "web/tagpup/state.js").state.folderPhotos].map((p) => p.filename).sort();

  test("the other library scans the folder itself, and the first one's scan is still its own", async (t) => {
    const alpha = await loadSelected(t);
    assert.deepEqual(names(alpha), ["a.jpg", "b.jpg"]);
    assert.ok(chip(alpha, "Cross Country"), "alpha's suggestion was not shown");
    const select = alpha.document.getElementById("db-select");
    select.value = "beta";
    select.dispatchEvent(new alpha.window.Event("change", { bubbles: true }));
    await flush(alpha.window, 4);
    const storage = kept(alpha.window);

    // The page the picker goes to: beta, with the folder in ?path.
    const beta = await loadApp("tagpup", {
      t, server: betaServer(),
      url: "http://localhost:8090/beta/?path=" + encodeURIComponent(FOLDER),
      before: (window) => Object.entries(storage).forEach(([k, v]) => window.localStorage.setItem(k, v)),
    });
    await flush(beta.window, 10);
    assert.ok(beta.server.urls().some((u) => u.startsWith("/beta/api/folder/scan")), "beta showed a scan it never made");
    assert.deepEqual(names(beta), ["c.jpg"], "beta shows alpha's records");
    assert.doesNotMatch(beta.document.getElementById("list-stats").textContent, /cached/);
    assert.deepEqual(Object.keys(pageExports(beta.window, "web/tagpup/state.js").state.folderSuggestions), [],
      "beta holds alpha's suggestions");

    // Back on alpha, the folder is alpha's as it was kept: beta's scan did not take its place.
    const storageAfter = kept(beta.window);
    const back = await loadApp("tagpup", {
      t, server: build().server,
      url: "http://localhost:8090/alpha/?path=" + encodeURIComponent(FOLDER),
      before: (window) => Object.entries(storageAfter).forEach(([k, v]) => window.localStorage.setItem(k, v)),
    });
    await flush(back.window, 10);
    assert.deepEqual(names(back), ["a.jpg", "b.jpg"], "alpha shows beta's records");
    assert.equal(back.server.urls().filter((u) => u.includes("/api/folder/scan")).length, 0, "alpha's own kept scan was not used");
  });
});
