/**
 * Opening a folder the library does not hold asks before it is added.
 *
 * With kr-track selected, the owner opened a folder another library held, on a network
 * share outside kr-track's root folders, and Suggest made rows for its photos in
 * kr-track without asking (2026-09-28): "we need some prompt to be clear the folder is
 * to be added to a given index so I can also use it to verify the index vs it just
 * adding it." The page names its library in the header at all times, and a folder the
 * library holds photos in none of the folders of opens with a question: "Add this
 * folder to kr-track?", the folder, its photos, whether it is outside the library's
 * roots, and which other library holds them. Add adds it (POST /api/folder/add); Open
 * in <other> goes to the library holding it; Just look shows it with Suggest and every
 * change held back until it is added.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "\\\\harbour-nas\\photos\\2019\\Lighthouse Trip";

const NOT_HELD = {
  library: "kr-track", folder: FOLDER, photos: 25, photos_held: 0, photos_not_held: 25,
  folders_not_held: 1, first_not_held: FOLDER, has_roots: true, under_roots: false, ignored: false,
  others: [{ library: "photo_index", photos: 25 }],
};

const HELD = { ...NOT_HELD, photos_held: 25, photos_not_held: 0, folders_not_held: 0, first_not_held: null,
  under_roots: true, others: [] };

function server(membership, indexing = { status: "completed", percent: 100, message: "Ready" },
  scanned = [photoRecord({ filename: "IMG_0001.jpg" }), photoRecord({ filename: "IMG_0002.jpg" })]) {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["kr-track", "photo_index"] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", indexing)
    .on("/api/folder/membership", membership)
    .on("/api/folder/add", { success: true, status: "running", library: "kr-track", folder: FOLDER, added: 25,
      admitted: 25, photos: 25, queued: [FOLDER], already_queued: [], invalid: [], pending: 1 })
    .on("/api/folder/suggest-start", { success: true, status: "running" })
    .on("/api/photos/bulk-tags", { success: true })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/scan", scanned);
}

async function open(t, membership = NOT_HELD, indexing = undefined) {
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: server(membership, indexing) });
  ctx.window.alert = () => {};
  await openFolder(ctx, FOLDER);
  const $ = (id) => ctx.document.getElementById(id);
  ctx.$ = $;
  ctx.dialogOpen = () => $("add-folder-modal").classList.contains("active");
  ctx.posts = (route) => ctx.server.calls.filter((c) => c.method === "POST" && c.url.includes(route));
  return ctx;
}

describe("the library is named", () => {
  test("in the header, all the time", async (t) => {
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: server(HELD) });
    const badge = ctx.document.getElementById("library-name");
    assert.equal(badge.textContent, "kr-track");
    assert.ok(!badge.classList.contains("hidden"));
  });
});

describe("a folder the library does not hold", () => {
  test("asks, naming the library, the folder, its photos, the roots and who holds it", async (t) => {
    const ctx = await open(t);
    assert.ok(ctx.dialogOpen(), "no question was asked");
    assert.equal(ctx.$("add-folder-title").textContent, "Add this folder to kr-track?");
    assert.equal(ctx.$("add-folder-library").textContent, "kr-track");
    assert.equal(ctx.$("add-folder-path").textContent, FOLDER);
    const facts = ctx.$("add-folder-facts").textContent;
    assert.match(facts, /25 photos, none of them in kr-track/);
    assert.match(facts, /Outside kr-track's root folders/);
    assert.match(facts, /photo_index already holds 25 of these photos/);
    assert.equal(ctx.$("btn-add-folder").textContent, "Add to kr-track");
    assert.equal(ctx.$("btn-open-in-other-library").textContent, "Open in photo_index");
    assert.ok(!ctx.$("btn-open-in-other-library").classList.contains("hidden"));
    assert.equal(ctx.posts("/api/folder/add").length, 0, "it was added without asking");
  });

  test("Add adds it, and Suggest may start", async (t) => {
    // The index waits behind another: what the queue says of a folder just added.
    const ctx = await open(t, NOT_HELD, { status: "queued", percent: 0, message: "Waiting", folder: FOLDER });
    click(ctx.window, ctx.$("btn-add-folder"));
    await flush(ctx.window, 6);
    assert.ok(!ctx.dialogOpen());
    const added = ctx.posts("/api/folder/add");
    assert.equal(added.length, 1);
    assert.equal(added[0].url, "/kr-track/api/folder/add");
    assert.deepEqual(added[0].body, { folder_path: FOLDER });
    assert.ok(!ctx.document.body.classList.contains("just-looking"));
    assert.ok(!ctx.$("btn-suggest-tags").disabled, "Suggest stayed off after adding");
    assert.match(ctx.$("status-text").textContent, /Added to kr-track: 25 photos/);
  });

  test("Open in the other library goes there, with the folder", async (t) => {
    const ctx = await open(t);
    click(ctx.window, ctx.$("btn-open-in-other-library"));
    await flush(ctx.window);
    const navigated = ctx.consoleErrors.some((e) => /navigation/i.test(e && e.message ? e.message : String(e)));
    assert.ok(navigated, "the page did not go to photo_index");
    assert.equal(ctx.posts("/api/folder/add").length, 0);
  });

  test("Just look shows it, with Suggest and every change held back", async (t) => {
    const ctx = await open(t);
    click(ctx.window, ctx.$("btn-just-look"));
    await flush(ctx.window);
    assert.ok(!ctx.dialogOpen());
    assert.ok(ctx.document.body.classList.contains("just-looking"));
    assert.ok(!ctx.$("just-looking-note").classList.contains("hidden"));
    assert.match(ctx.$("just-looking-text").textContent, /kr-track does not hold this folder/);
    assert.ok(ctx.$("btn-suggest-tags").disabled, "Suggest can still be started");

    // Suggest, a bulk tag and a title: none of them is sent.
    ctx.$("btn-suggest-tags").disabled = false;   // whatever enabled it, the click is held
    click(ctx.window, ctx.$("btn-suggest-tags"));
    ctx.$("bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, ctx.$("btn-bulk-add-tags"));
    click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[0]);
    await flush(ctx.window, 6);
    assert.ok(ctx.$("input-photo-title").readOnly, "the title can still be typed");
    const save = new ctx.window.KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
    ctx.$("input-photo-title").dispatchEvent(save);
    click(ctx.window, ctx.$("btn-save-details"));
    await flush(ctx.window, 6);
    for (const route of ["/api/folder/suggest-start", "/api/photos/bulk-tags", "/api/photo/save-metadata"]) {
      assert.equal(ctx.posts(route).length, 0, `${route} was sent while just looking`);
    }
  });

  test("the note adds it too, and the changes come back", async (t) => {
    const ctx = await open(t);
    click(ctx.window, ctx.$("btn-just-look"));
    await flush(ctx.window);
    click(ctx.window, ctx.$("btn-add-folder-from-note"));
    await flush(ctx.window, 6);
    assert.equal(ctx.posts("/api/folder/add").length, 1);
    assert.ok(!ctx.document.body.classList.contains("just-looking"));
    assert.ok(ctx.$("just-looking-note").classList.contains("hidden"));
    assert.ok(!ctx.$("input-photo-title").readOnly);
  });

  test("Escape is Just look, never Add", async (t) => {
    const ctx = await open(t);
    ctx.$("add-folder-modal").dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flush(ctx.window);
    assert.ok(!ctx.dialogOpen());
    assert.ok(ctx.document.body.classList.contains("just-looking"));
    assert.equal(ctx.posts("/api/folder/add").length, 0);
  });

  test("a folder only partly held asks to add the rest", async (t) => {
    const ctx = await open(t, { ...NOT_HELD, photos: 30, photos_held: 25, photos_not_held: 5, others: [] });
    assert.ok(ctx.dialogOpen());
    assert.equal(ctx.$("add-folder-title").textContent, "Add the rest of this folder to kr-track?");
    assert.match(ctx.$("add-folder-facts").textContent, /kr-track holds 25 of its 30 photos/);
    assert.ok(ctx.$("btn-open-in-other-library").classList.contains("hidden"));
  });
});

describe("the write queue while just looking", () => {
  const HELD_FOLDER = "D:/Library/2020";

  test("takes no write -- an undo of an earlier one waits -- and counts none", async (t) => {
    // Undo and a failed write's Retry reach the queue without a control the page
    // dims; the queue itself holds them back, and they are not shown as saving.
    const photos = [photoRecord({ filename: "a.jpg", tags: ["Beach"] }), photoRecord({ filename: "b.jpg" })];
    const s = server((url) => (url.includes(encodeURIComponent(HELD_FOLDER)) ? HELD : NOT_HELD), undefined, photos);
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: s });
    ctx.window.alert = () => {};
    const $ = (id) => ctx.document.getElementById(id);
    await openFolder(ctx, HELD_FOLDER);
    assert.ok(!$("add-folder-modal").classList.contains("active"));
    const press = (key, mods = {}) => ctx.document.dispatchEvent(
      new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...mods }));
    press("ArrowDown");
    press("ArrowDown");
    click(ctx.window, $("btn-carry-forward"));
    await flush(ctx.window, 8);
    const writes = () => s.calls.filter((c) => c.method === "POST"
      && (c.url.includes("/api/photos/bulk-tags") || c.url.includes("/api/photo/save-metadata"))).length;
    const before = writes();
    assert.equal(before, 1, "the carried tags were not written");
    assert.ok(!$("btn-undo").disabled);
    const entries = () => $("write-queue").querySelectorAll(".write-queue-entry").length;
    const queued = entries();

    await openFolder(ctx, FOLDER);
    click(ctx.window, $("btn-just-look"));
    await flush(ctx.window);
    click(ctx.window, $("btn-undo"));
    press("z", { ctrlKey: true });
    await flush(ctx.window, 8);
    assert.equal(writes(), before, "a write was sent while just looking");
    assert.equal(entries(), queued, "a write held back was put on the queue");
    assert.doesNotMatch($("write-queue").textContent, /Saving|failed/);
    assert.ok(!$("btn-undo").disabled, "the undo was lost instead of waiting");
    assert.match($("status-text").textContent, /kr-track does not hold this folder/);
  });
});

describe("a folder the library holds", () => {
  test("opens without a question", async (t) => {
    const ctx = await open(t, HELD);
    assert.ok(!ctx.dialogOpen());
    assert.ok(!ctx.document.body.classList.contains("just-looking"));
    assert.ok(!ctx.$("btn-suggest-tags").disabled);
  });
});
