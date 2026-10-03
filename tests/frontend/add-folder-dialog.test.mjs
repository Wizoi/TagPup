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
 * roots. It names only the open library, never another (2026-10-02). Add adds it (POST
 * /api/folder/add); Just look shows it with Suggest and what needs the library held back,
 * and edits to the photo files allowed (tests/frontend/just-look-edits.test.mjs).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "\\\\harbour-nas\\photos\\2019\\Lighthouse Trip";

const NOT_HELD = {
  library: "kr-track", folder: FOLDER, photos: 25, photos_held: 0, photos_not_held: 25,
  folders_not_held: 1, first_not_held: FOLDER, has_roots: true, under_roots: false, ignored: false,
};

const HELD = { ...NOT_HELD, photos_held: 25, photos_not_held: 0, folders_not_held: 0, first_not_held: null,
  under_roots: true };

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
    .on("/api/folder/add", { success: true, status: "running", library: "kr-track", folder: FOLDER, added: 1,
      queued: [FOLDER], already_queued: [], invalid: [], pending: 1 })
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
  test("asks, naming only the library, the folder, its photos and the roots", async (t) => {
    const ctx = await open(t);
    assert.ok(ctx.dialogOpen(), "no question was asked");
    assert.equal(ctx.$("add-folder-title").textContent, "Add this folder to kr-track?");
    assert.equal(ctx.$("add-folder-library").textContent, "kr-track");
    assert.equal(ctx.$("add-folder-path").textContent, FOLDER);
    const facts = ctx.$("add-folder-facts").textContent;
    assert.match(facts, /25 photos, none of them in kr-track/);
    assert.match(facts, /Outside kr-track's root folders/);
    assert.doesNotMatch(ctx.$("add-folder-modal").textContent, /photo_index/, "another library was named");
    assert.equal(ctx.$("btn-open-in-other-library"), null, "a button to open another library is back");
    assert.equal(ctx.$("btn-add-folder").textContent, "Add to kr-track");
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
    assert.match(ctx.$("status-text").textContent, /Added to kr-track; indexing it now/);
  });

  test("Just look shows it, with Suggest and what needs the library held back", async (t) => {
    const ctx = await open(t);
    click(ctx.window, ctx.$("btn-just-look"));
    await flush(ctx.window);
    assert.ok(!ctx.dialogOpen());
    assert.ok(ctx.document.body.classList.contains("just-looking"));
    assert.ok(!ctx.$("just-looking-note").classList.contains("hidden"));
    assert.equal(ctx.$("just-looking-text").textContent,
      "Just looking: kr-track does not hold this folder. Tags, captions, renames, rotating, deleting and date "
      + "changes are made to the photo files only, not to kr-track. Suggest and face naming are off until you add it.");
    assert.ok(ctx.$("btn-suggest-tags").disabled, "Suggest can still be started");

    // Suggest, Apply All and carrying tags forward need the library: none is sent.
    ctx.$("btn-suggest-tags").disabled = false;   // whatever enabled it, the click is held
    click(ctx.window, ctx.$("btn-suggest-tags"));
    click(ctx.window, ctx.$("btn-folder-auto-apply"));
    click(ctx.window, ctx.$("btn-carry-forward"));
    click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[0]);
    await flush(ctx.window, 6);
    for (const route of ["/api/folder/suggest-start", "/api/folder/auto-apply"]) {
      assert.equal(ctx.posts(route).length, 0, `${route} was sent while just looking`);
    }
    // The fields are the owner's to type in: a caption and tags are written to the files.
    assert.ok(!ctx.$("input-photo-title").readOnly, "the title cannot be typed");
    assert.ok(!ctx.$("bulk-add-tags-input").readOnly);
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
    const ctx = await open(t, { ...NOT_HELD, photos: 30, photos_held: 25, photos_not_held: 5 });
    assert.ok(ctx.dialogOpen());
    assert.equal(ctx.$("add-folder-title").textContent, "Add the rest of this folder to kr-track?");
    assert.match(ctx.$("add-folder-facts").textContent, /kr-track holds 25 of its 30 photos/);
  });
});

describe("the write queue while just looking", () => {
  const HELD_FOLDER = "D:/Library/2020";

  test("takes the writes that need the library no more; an undo of an earlier write goes through", async (t) => {
    // Carry-forward writes a suggestion-like copy and needs the library; the queue itself
    // holds it back, and it is not shown as saving. Undo re-writes earlier tags, which a
    // file-only edit needs too: the server says per photo where it writes.
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
    press("d", { ctrlKey: true });
    click(ctx.window, $("btn-carry-forward"));
    await flush(ctx.window, 8);
    assert.equal(writes(), before, "a carry-forward was sent while just looking");
    assert.equal(entries(), queued, "a write that needs the library was put on the queue");

    click(ctx.window, $("btn-undo"));
    await flush(ctx.window, 8);
    assert.equal(writes(), before + 1, "the undo was held back");
    assert.doesNotMatch($("write-queue").textContent, /failed/);
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
