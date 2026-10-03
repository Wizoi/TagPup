/**
 * Just look lets the owner edit the photo files.
 *
 * "For the Just Look -- that should still let me change captions on photos, do smart
 * renames, and add/update tags if it does not go into an index" (2026-10-02), and then
 * rotate, delete and the date changes: none of them needs the database. The server writes
 * the files and nothing of the library's (tests/test_just_look_edits.py) and decides,
 * photo by photo, by whether the library holds the photo's folder; the page does not
 * send a flag. What the page does here is stop blocking exactly those controls, say
 * where the write went, and keep face naming off. Suggest works too (2026-10-03): the server
 * analyses the photos against the library, keeps what it finds in memory and adds nothing to
 * it (tests/test_analyse_only_suggest.py); what it offers is applied to the files only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "\\\\harbour-nas\\photos\\2019\\Lighthouse Trip";
const HELD_FOLDER = "D:/Library/2020";
const NOT_HELD = {
  library: "kr-track", folder: FOLDER, photos: 2, photos_held: 0, photos_not_held: 2,
  folders_not_held: 1, first_not_held: FOLDER, has_roots: true, under_roots: false, ignored: false,
};
const HELD = { ...NOT_HELD, photos_held: 2, photos_not_held: 0, folders_not_held: 0, first_not_held: null };

const A = `${FOLDER}\\IMG_0001.jpg`;
const B = `${FOLDER}\\IMG_0002.jpg`;
const photos = () => [photoRecord({ path: A, filename: "IMG_0001.jpg", tags: ["Beach"] }),
  photoRecord({ path: B, filename: "IMG_0002.jpg" })];

function server(scanned = photos) {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["kr-track", "photo_index"] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/membership", (url) => (url.includes(encodeURIComponent(HELD_FOLDER)) ? HELD : NOT_HELD))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", (url) => ({ success: true, new_path: A, file_only: 1, with_rows: 0 }))
    .on("/api/photos/bulk-tags", { success: true, written: {}, file_only: 2, with_rows: 0 })
    .on("/api/photo/rotate", { success: true, mtime: 1700000000, file_only: 1, with_rows: 0 })
    .on("/api/photo/delete", { success: true, file_only: 1, with_rows: 0,
      message: "Moved to the Recycle Bin. Nothing in kr-track changed: it does not hold this folder." })
    .on("/api/folder/rename-photos", { success: true, updated_paths: {}, updated_photos: photos(),
      index_rows_moved: 0, index_skipped: [], file_only: 2, with_rows: 0 })
    .on("/api/folder/time-shift", { success: true, updated_photos: photos(), updated_count: 2,
      requested_count: 2, file_only: 2, with_rows: 0 })
    .on("/api/folder/scan", (url) => (url.includes(encodeURIComponent(HELD_FOLDER))
      ? [photoRecord({ filename: "held.jpg" })] : scanned()));
}

/** Open FOLDER and choose Just look. */
async function looking(t, s = server()) {
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: s });
  ctx.window.alert = () => {};
  ctx.window.confirm = () => true;
  await openFolder(ctx, FOLDER);
  const $ = (id) => ctx.document.getElementById(id);
  ctx.$ = $;
  ctx.posts = (route) => s.calls.filter((c) => c.method === "POST" && c.url.includes(route));
  click(ctx.window, $("btn-just-look"));
  await flush(ctx.window);
  assert.ok(ctx.document.body.classList.contains("just-looking"));
  ctx.open = async (n = 0) => {
    click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[n]);
    await flush(ctx.window, 6);
  };
  ctx.select = (...ns) => ns.forEach((n) => click(ctx.window, ctx.document.querySelectorAll(
    ".thumbnail-card .thumbnail-checkbox")[n]));
  ctx.key = (target, key, mods = {}) => target.dispatchEvent(new ctx.window.KeyboardEvent("keydown", {
    key, bubbles: true, cancelable: true, ...mods }));
  return ctx;
}

describe("a caption and tags on one photo", () => {
  test("typed in the details panel and saved with the button", async (t) => {
    const ctx = await looking(t);
    await ctx.open();
    ctx.$("input-photo-title").value = "Lamp room";
    ctx.$("input-add-tag").value = "Trips/Lighthouse";
    ctx.$("input-photo-title").dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    click(ctx.window, ctx.$("btn-save-details"));
    await flush(ctx.window, 10);
    const sent = ctx.posts("/api/photo/save-metadata");
    assert.equal(sent.length, 1, "nothing was sent");
    assert.equal(sent[0].body.title, "Lamp room");
    assert.deepEqual(sent[0].body.tags, ["Beach", "Trips/Lighthouse"]);
    assert.equal(sent[0].body.just_looking, undefined, "the page sends no flag; the server decides");
    assert.match(ctx.$("status-text").textContent, /Written to the files only: kr-track does not hold this folder/);
    assert.match(ctx.$("detail-tags").textContent, /Lighthouse/, "the panel did not take the tag from the answer");
    assert.equal(ctx.posts("/api/taxonomy/create").length, 0, "the library's tag tree was written");
  });

  test("Enter in the tag field and Ctrl+S write; Escape takes the text back", async (t) => {
    const ctx = await looking(t);
    await ctx.open();
    ctx.$("input-add-tag").value = "Trips/Lighthouse";
    ctx.key(ctx.$("input-add-tag"), "Enter");
    await flush(ctx.window, 10);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 1, "Enter wrote nothing");

    ctx.$("input-photo-title").value = "Stairs";
    ctx.$("input-photo-title").dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    ctx.key(ctx.document.body, "s", { ctrlKey: true });
    await flush(ctx.window, 10);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 2, "Ctrl+S wrote nothing");

    ctx.$("input-add-tag").value = "Not wanted";
    ctx.key(ctx.$("input-add-tag"), "Escape");
    assert.equal(ctx.$("input-add-tag").value, "");
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 2);
  });

  test("a new person is filed under People without making a node in the library's tree", async (t) => {
    const ctx = await looking(t);
    await ctx.open();
    ctx.$("input-add-person").value = "Fictional Newcomer";
    click(ctx.window, ctx.$("btn-add-person"));
    await flush(ctx.window, 10);
    const sent = ctx.posts("/api/photo/save-metadata");
    assert.equal(sent.length, 1);
    assert.ok(sent[0].body.tags.includes("People/Fictional Newcomer"), JSON.stringify(sent[0].body.tags));
    assert.equal(ctx.posts("/api/taxonomy/create").length, 0, "a node was made in the tree of a library that does not hold the folder");
  });

  test("a caption typed on a card", async (t) => {
    const ctx = await looking(t);
    click(ctx.window, ctx.document.querySelector(".thumbnail-card .editable-title"));
    const input = ctx.document.querySelector(".thumbnail-filename-input");
    assert.ok(input, "the card would not take a caption");
    input.value = "Beacon";
    ctx.key(input, "Enter");
    await flush(ctx.window, 10);
    assert.equal(ctx.posts("/api/photo/save-metadata")[0].body.title, "Beacon");
  });
});

describe("tags on many photos", () => {
  test("added with the bulk field, and the answer says where", async (t) => {
    const s = server().first("/api/photos/bulk-tags", { success: true, file_only: 2, with_rows: 0,
      written: { [A]: ["Beach", "Trips/Lighthouse"], [B]: ["Trips/Lighthouse"] } });
    const ctx = await looking(t, s);
    ctx.select(0, 1);
    ctx.$("bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, ctx.$("btn-bulk-add-tags"));
    await flush(ctx.window, 10);
    const sent = ctx.posts("/api/photos/bulk-tags");
    assert.equal(sent.length, 1, "nothing was sent");
    assert.deepEqual(sent[0].body.paths.sort(), [A, B]);
    assert.deepEqual(sent[0].body.add_tags, ["Trips/Lighthouse"]);
    assert.match(ctx.$("status-text").textContent, /Written to the files only: kr-track does not hold this folder/);
    assert.equal(ctx.posts("/api/taxonomy/create").length, 0);
    assert.equal(ctx.document.querySelectorAll(".photo-item-file").length, 2, "a photo left the list");
  });

  test("a partly held folder says how many went where", async (t) => {
    const s = server().first("/api/photos/bulk-tags", { success: true, file_only: 1, with_rows: 1,
      written: { [A]: ["Beach", "Trips/Lighthouse"], [B]: ["Trips/Lighthouse"] } });
    const ctx = await looking(t, s);
    ctx.select(0, 1);
    ctx.$("bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, ctx.$("btn-bulk-add-tags"));
    await flush(ctx.window, 10);
    assert.match(ctx.$("status-text").textContent,
      /1 to the files only \(kr-track does not hold their folder\), 1 with their rows/);
  });

  test("a folder opened while the write is out is not touched by its answer", async (t) => {
    let release;
    const answer = new Promise((resolve) => { release = resolve; });
    const s = server().first("/api/photos/bulk-tags", () => answer);
    const ctx = await looking(t, s);
    ctx.select(0, 1);
    ctx.$("bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, ctx.$("btn-bulk-add-tags"));
    await flush(ctx.window, 4);
    await openFolder(ctx, HELD_FOLDER);
    assert.ok(!ctx.document.body.classList.contains("just-looking"), "the new folder is held");
    release({ success: true, file_only: 2, with_rows: 0, written: { [A]: ["Beach", "Trips/Lighthouse"],
      [B]: ["Trips/Lighthouse"] } });
    await flush(ctx.window, 10);
    const names = [...ctx.document.querySelectorAll(".photo-item-file")].map((e) => e.getAttribute("data-path"));
    assert.deepEqual(names, ["D:\\Library\\2020\\held.jpg"], "the list is not the new folder's");
    assert.ok(ctx.consoleErrors.every((e) => !/TypeError|ReferenceError/.test(String(e && e.message ? e.message : e))),
      "the late answer broke the page");
  });
});

describe("the other file edits", () => {
  test("rotate is sent and the answer says where", async (t) => {
    const ctx = await looking(t);
    await ctx.open();
    click(ctx.window, ctx.$("btn-rotate-left"));
    await flush(ctx.window, 6);
    assert.equal(ctx.posts("/api/photo/rotate").length, 1, "Rotate was held back");
    assert.match(ctx.$("status-text").textContent, /Rotated\. Written to the files only/);
  });

  test("delete asks as always and says that only the file moves", async (t) => {
    const ctx = await looking(t);
    await ctx.open();
    let asked = "";
    ctx.window.confirm = (text) => { asked = text; return true; };
    click(ctx.window, ctx.$("btn-delete-photo"));
    await flush(ctx.window, 8);
    // FOLDER is a network share: no Recycle Bin, said BEFORE the delete (#520).
    assert.match(asked, /This file is on a network share: it will be deleted permanently, not moved to the Recycle Bin/);
    assert.doesNotMatch(asked, /and move it to the Windows Recycle Bin/);
    assert.match(asked, /kr-track does not hold this folder: only the file is deleted, and nothing in kr-track changes/);
    assert.equal(ctx.posts("/api/photo/delete").length, 1, "Delete was held back");
    assert.match(ctx.$("status-text").textContent, /Nothing in kr-track changed/);
    assert.equal(ctx.document.querySelectorAll(".photo-item-file").length, 1, "the photo stayed in the list");
  });

  test("a local photo's delete says the Recycle Bin; a mapped drive the server names is permanent too", async (t) => {
    const s = server().first("/api/folder/membership", () => ({ ...HELD, permanent_delete: false }));
    const local = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: s });
    local.window.alert = () => {};
    let asked = "";
    local.window.confirm = (text) => { asked = text; return false; };
    await openFolder(local, HELD_FOLDER);
    click(local.window, local.document.querySelectorAll(".photo-item-file")[0]);
    await flush(local.window, 6);
    click(local.window, local.document.getElementById("btn-delete-photo"));
    assert.match(asked, /move it to the Windows Recycle Bin\?/);
    assert.doesNotMatch(asked, /permanently/);
    closeAllApps();

    const mapped = server().first("/api/folder/membership", () => ({ ...HELD, permanent_delete: true,
      permanent_reason: "on a removable drive" }));
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: mapped });
    ctx.window.alert = () => {};
    ctx.window.confirm = (text) => { asked = text; return false; };
    await openFolder(ctx, HELD_FOLDER);
    click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[0]);
    await flush(ctx.window, 6);
    click(ctx.window, ctx.document.getElementById("btn-delete-photo"));
    assert.match(asked, /This file is on a removable drive: it will be deleted permanently, not moved to the Recycle Bin/);
    assert.doesNotMatch(asked, /network share/, "the reason is the server's, not always a share");
  });

  test("a Smart Rename that stopped part-way shows the names that did change, and says how to recover", async (t) => {
    const renamed = { ...photos()[0], path: `${FOLDER}\\Lighthouse - 1.jpg`, filename: "Lighthouse - 1.jpg" };
    const s = server().first("/api/folder/rename-photos", { success: false, updated_paths: { [A]: renamed.path },
      updated_photos: [renamed, photos()[1]],
      error: "Could not rename: gone. If it stops part-way, run Smart Rename on the folder again: it regenerates the same names." },
    { status: 500 });
    const ctx = await looking(t, s);
    ctx.select(0, 1);
    ctx.$("rename-grouping-input").value = "Lighthouse";
    const apply = ctx.$("btn-apply-rename");
    apply.disabled = false;
    let alerted = "";
    ctx.window.alert = (text) => { alerted = text; };
    click(ctx.window, apply);
    await flush(ctx.window, 10);
    const shown = [...ctx.document.querySelectorAll(".photo-item-file")].map((e) => e.getAttribute("data-path"));
    assert.ok(shown.includes(renamed.path), "the page still shows the old names");
    assert.match(alerted, /run Smart Rename on the folder again/);
  });

  test("a delete the owner declines sends nothing", async (t) => {
    const ctx = await looking(t);
    await ctx.open();
    ctx.window.confirm = () => false;
    click(ctx.window, ctx.$("btn-delete-photo"));
    await flush(ctx.window, 6);
    assert.equal(ctx.posts("/api/photo/delete").length, 0);
  });

  test("the date taken is saved", async (t) => {
    const ctx = await looking(t);
    await ctx.open();
    click(ctx.window, ctx.$("btn-edit-date-taken"));
    ctx.$("input-date-taken").value = "2018-03-04T09:08:07";
    click(ctx.window, ctx.$("btn-save-date-modal"));
    await flush(ctx.window, 10);
    const sent = ctx.posts("/api/photo/save-metadata");
    assert.equal(sent.length, 1, "the date was held back");
    assert.match(sent[0].body.date_taken, /^2018-03-04T09:08:07/);
  });

  test("Smart Rename is sent for the selection", async (t) => {
    const ctx = await looking(t);
    ctx.select(0, 1);
    ctx.$("rename-grouping-input").value = "Lighthouse";
    const apply = ctx.$("btn-apply-rename");
    apply.disabled = false;
    click(ctx.window, apply);
    await flush(ctx.window, 10);
    const sent = ctx.posts("/api/folder/rename-photos");
    assert.equal(sent.length, 1, "Smart Rename was held back");
    assert.equal(sent[0].body.grouping, "Lighthouse");
    assert.match(ctx.$("status-text").textContent, /Written to the files only/);
  });

  test("a time shift is sent", async (t) => {
    const ctx = await looking(t);
    click(ctx.window, ctx.$("btn-toggle-timeshift"));
    ctx.$("timeshift-minutes-input").value = "30";
    const apply = ctx.$("btn-apply-timeshift");
    apply.disabled = false;
    click(ctx.window, apply);
    await flush(ctx.window, 10);
    assert.equal(ctx.posts("/api/folder/time-shift").length, 1, "the time shift was held back");
    assert.match(ctx.$("status-text").textContent, /Time shift applied to 2 photo\(s\)\. Written to the files only/);
  });
});

describe("Suggest while just looking", () => {
  const offered = {
    status: "completed", completed: 2, total: 2, in_memory: true,
    notes: ["The library has no named faces yet, so no people are suggested."],
    suggestions: { [B]: { tags: [{ tag: "Trips/Lighthouse", score: 0.9 }], people: [], title: "Lighthouse" } },
  };

  test("runs, says nothing was added to the library, and what it offers is applied to the file", async (t) => {
    const s = server()
      .first("/api/folder/suggest-start", { success: true, status: "running", in_memory: true })
      .first("/api/folder/suggest-status", offered);
    const ctx = await looking(t, s);
    assert.ok(!ctx.$("btn-suggest-tags").disabled, "Suggest is off while just looking");
    click(ctx.window, ctx.$("btn-suggest-tags"));
    await flush(ctx.window, 12);
    assert.equal(ctx.posts("/api/folder/suggest-start").length, 1, "Suggest was not sent");
    assert.match(ctx.$("status-text").textContent,
      /Analysed against kr-track; nothing was added to kr-track\. The library has no named faces yet/);
    await ctx.open(1);
    const chip = ctx.document.querySelector("#suggested-tags-container .suggestion-chip");
    assert.ok(chip, "the suggestion is not offered");
    click(ctx.window, chip);
    await flush(ctx.window, 12);
    const sent = ctx.posts("/api/photo/save-metadata");
    assert.equal(sent.length, 1, "taking the suggestion wrote nothing");
    assert.ok(sent[0].body.tags.includes("Trips/Lighthouse"), JSON.stringify(sent[0].body.tags));
    assert.equal(sent[0].body.just_looking, undefined, "the page sends no flag; the server decides");
    assert.equal(ctx.posts("/api/taxonomy/create").length, 0, "a node was made in the library's tree");
  });

  test("Apply All on the selection goes to the files, with no taxonomy write", async (t) => {
    const s = server()
      .first("/api/folder/auto-apply", { success: true, written: {}, skipped_damaged: 0, skipped: [], file_only: 1, with_rows: 0 })
      .first("/api/folder/suggest-status", offered);
    const ctx = await looking(t, s);
    click(ctx.window, ctx.$("btn-suggest-tags"));
    await flush(ctx.window, 12);
    ctx.select(1);
    ctx.$("btn-folder-auto-apply").disabled = false;
    click(ctx.window, ctx.$("btn-folder-auto-apply"));
    await flush(ctx.window, 12);
    assert.equal(ctx.posts("/api/folder/auto-apply").length, 1, "Apply All was held back while just looking");
    assert.equal(ctx.posts("/api/taxonomy/create").length, 0);
  });
});

describe("what stays off", () => {
  test("only face naming needs the library: a click on the faces strip is held", async (t) => {
    const ctx = await looking(t);
    await ctx.open(1);
    const before = ctx.posts("/api/faces/name").length + ctx.posts("/api/photo-faces/name").length;
    click(ctx.window, ctx.$("faces-strip"));
    await flush(ctx.window, 8);
    assert.equal(ctx.posts("/api/faces/name").length + ctx.posts("/api/photo-faces/name").length, before);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 0);
  });

  test("the right-click menu offers what it always did, and writes nothing", async (t) => {
    const ctx = await looking(t);
    const card = ctx.document.querySelector(".thumbnail-card");
    card.dispatchEvent(new ctx.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    const menu = ctx.$("grid-context-menu");
    assert.ok(!menu.classList.contains("hidden"), "the menu did not open");
    assert.ok(![...menu.querySelectorAll("[data-action]")].some((item) => item.classList.contains("disabled")
      && !item.hasAttribute("data-requires-selection")), "an entry was dimmed by just looking");
    click(ctx.window, menu.querySelector('[data-action="select-all"]'));
    await flush(ctx.window, 4);
    assert.ok(ctx.server.calls.every((c) => c.method !== "POST" || !/save-metadata|bulk-tags|rotate|delete/.test(c.url)));
  });

  test("a library that holds the folder is written with its rows, with no note", async (t) => {
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: server() });
    await openFolder(ctx, HELD_FOLDER);
    assert.ok(ctx.document.getElementById("just-looking-note").classList.contains("hidden"));
  });
});
