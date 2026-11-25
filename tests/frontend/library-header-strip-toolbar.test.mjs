/**
 * The owner's first review of the library views after installing phase 9 (docs/findings.md #668-#675; this file holds the
 * header, the strip, the toolbar, the selection details and Delete -- #671-#673, the navigator's, are tested beside it).
 * Fictional names only: the real library is photographs of real people, many of them minors.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord, CLIP_WRITES, testClock } from "./harness.mjs";
import { loadViewPage, pageErrors, speedUp, recordOf } from "./view-page.mjs";
import fs from "node:fs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const el = (ctx, id) => ctx.document.getElementById(id);
const stripSentence = (ctx) => ctx.document.querySelector(".batch-strip-sentence").textContent;
/** A Delete's question and reply as the real routes make them (tests/test_batch_strip_replies.py: three photos of one
 * folder, to the Recycle Bin and, the Bin full, for good); a test changes only what its scenario is about. */
const served = JSON.parse(fs.readFileSync(new URL("./fixtures/batches.json", import.meta.url), "utf8"));
const TOKEN = served.delete_check.token;
const FOR_GOOD_TOKEN = served.delete_check_for_good.token;

describe("#668: the button beside Library is Organize", () => {
  test("it says Organize, and its tooltip says what Organize is for: one folder on disk", async (t) => {
    const ctx = await loadViewPage(t);
    const button = el(ctx, "sidebar-tab-folder");
    assert.equal(button.textContent.trim(), "Organize");
    assert.match(button.title, /one folder on disk/);
    assert.match(button.title, /Smart Rename/);
    const switchText = el(ctx, "sidebar-switch").textContent.replace(/\s+/g, " ").trim();
    assert.equal(switchText, "Library Organize", "no button is called Folder: that is the library's Folders tab");
  });
});

describe("#669: Smart Rename and the time shift are Organize's, not a library view's", () => {
  const FOLDER = "D:\\Library\\2020\\Event 01";
  const scan = () => [1, 2].map((n) => photoRecord({ filename: `IMG_000${n}.jpg`, path: `${FOLDER}\\IMG_000${n}.jpg`, tags: [], people: [] }));

  test("every kind of view: neither button is shown, and neither panel", async (t) => {
    for (const search of ["?view=all", "?view=folder&value=D%3A%5CLibrary&recursive=1", "?view=keyword&value=Trips", "?view=person&value=Wren%20Halloway", "?view=year&value=2020", "?view=month&value=2020-06"]) {
      const ctx = await loadViewPage(t, { search });
      for (const id of ["btn-toggle-rename", "btn-toggle-timeshift"]) assert.ok(el(ctx, id).classList.contains("hidden"), `${id} in ${search}`);
      assert.ok(el(ctx, "rename-panel").classList.contains("hidden"));
      assert.ok(el(ctx, "timeshift-panel").classList.contains("hidden"));
      assert.equal(el(ctx, "timeshift-direction"), null, "the view's own direction field is gone");
    }
  });

  test("a folder in Organize keeps both, with the camera, and they come back when a view closes onto a folder", async (t) => {
    const ctx = await loadViewPage(t, { search: "", scan });
    await openFolder(ctx, FOLDER);
    await ctx.settle(60);
    for (const id of ["btn-toggle-rename", "btn-toggle-timeshift"]) {
      assert.ok(!el(ctx, id).classList.contains("hidden"), id);
      assert.equal(el(ctx, id).disabled, false, id);
    }
    ctx.module("library-view.js").openLibraryView({ kind: "all" });
    await ctx.settle();
    assert.ok(el(ctx, "btn-toggle-timeshift").classList.contains("hidden"));
    ctx.module("library-view.js").closeLibraryView({ folder: FOLDER });
    await ctx.settle(100);
    assert.ok(!el(ctx, "btn-toggle-timeshift").classList.contains("hidden"));
    assert.ok(!el(ctx, "btn-toggle-rename").classList.contains("hidden"));
    click(ctx.window, el(ctx, "btn-toggle-timeshift"));
    assert.ok(!el(ctx, "timeshift-panel").classList.contains("hidden"));
    assert.ok(el(ctx, "timeshift-camera-select").options.length > 0, "the camera is offered in Organize");
  });
});

describe("#675: Folders to Organize, and no Date Taken, in a view's selection details", () => {
  const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
  const EVENT = "D:\\Library\\2020\\Event 01";
  const ODD = "D:\\Library\\2021\\<b>Bake & Share</b>";
  const tally = (folders) => ({ total: 3, tags: [], more_tags: 0, people: [], more_people: 0, folders });
  const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
  const links = (ctx) => [...ctx.document.querySelectorAll("#selection-folders-list .selection-folder-link")];
  const listText = (ctx) => el(ctx, "selection-folders-list").textContent.replace(/\s+/g, " ").trim();
  const scans = (ctx) => ctx.server.urls().filter((url) => url.includes("/api/folder/scan"));

  test("the folders of the selection by their names, as text, each with its photos; Date Taken is not shown", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    ctx.bulk.tally = tally({ count: 2, listed: [{ path: EVENT, name: "Event 01", photos: 2 }, { path: ODD, name: "<b>Bake & Share</b>", photos: 1 }] });
    pick(ctx, 2);
    pick(ctx, 3);
    assert.equal(listText(ctx), "counting\u2026");
    await ctx.settle(700);
    assert.deepEqual(links(ctx).map((link) => link.textContent), ["Event 01", "<b>Bake & Share</b>"], "names as text, never markup");
    assert.equal(ctx.document.querySelector("#selection-folders-list b"), null);
    assert.match(listText(ctx), /^Event 01 \(2 photos\)\s*<b>Bake & Share<\/b> \(1 photo\)$/);
    assert.equal(links(ctx)[1].title, `Open ${ODD} in Organize`);
    assert.ok(!el(ctx, "selection-folders-group").classList.contains("hidden"));
    assert.ok(el(ctx, "selection-date-group").classList.contains("hidden"), "no Date Taken in a view (#675)");
  });

  test("clicking one opens it in Organize: scanned first, the view closed, the folder open; rapid clicks scan once", async (t) => {
    const scan = () => [1, 2].map((n) => photoRecord({ filename: `IMG_000${n}.jpg`, path: `${EVENT}\\IMG_000${n}.jpg`, tags: [], people: [] }));
    const ctx = await loadViewPage(t, { search: "?view=keyword&value=Trips", ids: range(40), scan });
    ctx.bulk.tally = tally({ count: 1, listed: [{ path: EVENT, name: "Event 01", photos: 3 }] });
    pick(ctx, 2);
    await ctx.settle(700);
    const [link] = links(ctx);
    link.click();
    link.click();
    link.click();
    await ctx.settle(300);
    // One scan to see that the folder is there, and the folder view's own (the server answers it from the first): never one a click.
    assert.equal(scans(ctx).length, 2);
    assert.match(scans(ctx)[0], /path=D%3A%5CLibrary%5C2020%5CEvent%2001/);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, EVENT);
    assert.equal(ctx.state.nav.shown, "folder", "Organize is shown");
    assert.ok(!el(ctx, "selection-date-group").classList.contains("hidden"), "Organize's panel has Date Taken");
    assert.ok(el(ctx, "selection-folders-group").classList.contains("hidden"), "and no Folders to Organize");
  });

  test("a folder gone, or on a share that is away: the view stays and its strip says so", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    ctx.bulk.tally = tally({ count: 1, listed: [{ path: "\\\\nas\\Pictures\\Away", name: "Away", photos: 1 }] });
    ctx.server.first("/api/folder/scan", { error: "The share did not answer." }, { status: 500 });
    pick(ctx, 2);
    await ctx.settle(700);
    links(ctx)[0].click();
    await ctx.settle(200);
    assert.ok(ctx.state.library);
    assert.match(ctx.stripText(), /Could not open that folder on disk: The share did not answer\./);
    pageErrors();
  });

  test("more than 10: one sentence with how many, and no names", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    ctx.bulk.tally = tally({ count: 11, listed: [] });
    pick(ctx, 2);
    await ctx.settle(700);
    assert.equal(links(ctx).length, 0);
    assert.equal(listText(ctx), "These photos are in 11 folders. Narrow the selection to 10 folders or fewer to open one in Organize.");
  });

  test("Select all of 68,000: the request names the source, never a path; 2,672 folders are counted, not listed", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(68000) });
    ctx.bulk.tally = tally({ count: 2672, listed: [] });
    el(ctx, "btn-select-all-thumbnails").click();
    await ctx.settle(700);
    const asked = ctx.server.calls.filter((call) => call.url.includes("/selection/tally"));
    assert.equal(asked.length, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(asked[0].body)), { selection: { source: { kind: "all", value: null, recursive: false }, excluded: [] } });
    assert.match(listText(ctx), /^These photos are in 2,672 folders\./);
  });

  test("two folders of one name are told apart by the folder above them", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    ctx.bulk.tally = tally({ count: 2, listed: [
      { path: "D:\\Library\\2020\\Event 01", name: "Event 01", photos: 1 }, { path: "D:\\Library\\2021\\Event 01", name: "Event 01", photos: 2 }] });
    pick(ctx, 2);
    await ctx.settle(700);
    assert.deepEqual(links(ctx).map((link) => link.textContent), ["2020\\Event 01", "2021\\Event 01"]);
  });

  test("a count that fails says so in the folders too; none selected shows nothing", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    ctx.server.first("/api/library/selection/tally", { error: "The library is busy." }, { status: 500 });
    pick(ctx, 2);
    await ctx.settle(700);
    assert.match(listText(ctx), /Could not count what these photos carry/);
    pageErrors();
  });
});

describe("#674: Delete of a view's selection", () => {
  const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
  const plain = (value) => JSON.parse(JSON.stringify(value));
  const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
  const calls = (ctx, part) => ctx.server.calls.filter((call) => call.url.includes(part));
  const BIN = served.delete_check;

  async function view(t, n = 40, { where = BIN, ...options } = {}) {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(n), ...options });
    speedUp(ctx);
    ctx.where = where;
    ctx.server.on("/api/library/selection/delete-check", () => (typeof ctx.where === "function" ? ctx.where() : ctx.where));
    ctx.questions = [];
    ctx.alerts = [];
    ctx.answer = true;
    ctx.window.confirm = (text) => { ctx.questions.push(text); return typeof ctx.answer === "function" ? ctx.answer(text) : ctx.answer; };
    ctx.window.alert = (text) => ctx.alerts.push(text);
    ctx.bulk.edit = { ...served.deleted };
    return ctx;
  }
  const remove = async (ctx) => {
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
  };

  test("the button: shown in every view; in Organize, shown when something is selected", async (t) => {
    const ctx = await view(t);
    assert.ok(!el(ctx, "btn-delete-selection").classList.contains("hidden"), "a view, nothing selected");
    const organize = await loadViewPage(t, { search: "" });
    const button = organize.document.getElementById("btn-delete-selection");
    assert.ok(button.classList.contains("hidden"), "no folder open");
    await openFolder(organize, "D:\\Library\\2020");
    await organize.settle(60);
    assert.ok(button.classList.contains("hidden"), "a folder, nothing selected");
    click(organize.window, organize.document.querySelector("#thumbnails-grid .thumbnail-checkbox"));
    assert.ok(!button.classList.contains("hidden"), "a folder, a photo selected");
  });

  test("3 photos across folders: asked where they go, then the question names them and the Recycle Bin, then one request by id", async (t) => {
    const ctx = await view(t);
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.deepEqual(plain(calls(ctx, "/selection/delete-check")[0].body), { selection: { ids: [2, 5, 9] } });
    assert.deepEqual(ctx.questions, ["Delete 3 photos? The files go to the Recycle Bin, where they can be restored. Their rows leave the library, with their faces. It takes less than a minute; TagPup is busy until it is done, and saving a photo elsewhere waits for the photos being deleted at that moment. It cannot be undone in TagPup."]);
    const [start] = calls(ctx, "/api/library/bulk-edit");
    assert.deepEqual(plain(start.body), { op: "delete", selection: { ids: [2, 5, 9] }, params: { token: TOKEN } },
      "the token of the photos the question named");
    assert.ok(!calls(ctx, "/api/photo/delete").length, "never the one-photo route, once a photo");
    assert.match(stripSentence(ctx), /^Deleted 3 photos to the Recycle Bin;/);
  });

  test("some on a share with no Recycle Bin: the question says they go through this PC, how much, and where they restore to (#694)", async (t) => {
    const ctx = await view(t, 40, { where: { ...BIN, folders: 2, through_this_pc: 1, reasons: [{ reason: "on a network share", photos: 1 }], copy_bytes: 3 * 1024 * 1024 + 1 } });
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.match(ctx.questions[0], /^Delete 3 photos\? 1 of them is where there is no Recycle Bin \(1 on a network share\): it is copied to this PC \(4 MB\), the copy goes to this PC's Recycle Bin, and then the original is deleted\. Restored from the Recycle Bin, a copy goes to C:\\Users\\wren\\Downloads\\TagPup deleted from shares, not back to where it was\. The other 2 go to the Recycle Bin\./);
    assert.doesNotMatch(ctx.questions[0], /PERMANENTLY|permanently/);
    assert.match(ctx.questions[0], /It takes at least about 1 minute \(copies from a share/, "a floor, not a promise, for copies from a share (#705)");
    assert.deepEqual(plain(calls(ctx, "/api/library/bulk-edit")[0].body.params), { token: TOKEN });
    const all = await view(t, 40, { where: { ...BIN, total: 1, through_this_pc: 1, reasons: [{ reason: "on a removable drive", photos: 1 }], copy_bytes: 1000 } });
    pick(all, 4);
    await remove(all);
    assert.match(all.questions[0], /^Delete 1 photo\? It is where there is no Recycle Bin \(1 on a removable drive\): it is copied to this PC \(1 MB\)/);
  });

  test("clips in the selection are said as videos: the question and what was done (#2645)", async (t) => {
    const clips = { onIds: () => ({ source: {}, total: 40, ids: range(40), videos: [5, 9], complete: true }),
      cardExtra: (id) => ({ media: [5, 9].includes(id) ? "video" : "photo" }) };
    const ctx = await view(t, 40, { where: { ...BIN, total: 2 }, ...clips });
    ctx.bulk.edit = { ...served.deleted, total: 2, requested: 2, changed: 2 };
    for (const id of [5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.match(ctx.questions[0], /^Delete 2 videos\? The files go to the Recycle Bin/);
    assert.match(stripSentence(ctx), /^Deleted 2 videos to the Recycle Bin;/);

    const mixed = await view(t, 40, clips);
    mixed.bulk.edit = { ...served.deleted, changed: 2, error_count: 1, errors: [{ id: 2, name: "IMG_2.jpg", why: "the file is in use" }] };
    for (const id of [2, 5, 9]) pick(mixed, id);
    await remove(mixed);
    assert.match(mixed.questions[0], /^Delete 1 photo, 2 videos\?/);
    assert.match(stripSentence(mixed), /^Deleted 2 photos and videos to the Recycle Bin;/, "a count of a mixed selection that is not its own");
  });

  test("no room on this PC for the copies: said, nothing asked, nothing started (#694)", async (t) => {
    const ctx = await view(t, 40, { where: { ...BIN, through_this_pc: 3, copy_bytes: 9e12, no_room: "There is not room on this PC for the copies: 8,583,068 MB needed. Nothing was deleted." } });
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.equal(ctx.questions.length, 0);
    assert.match(ctx.alerts[0], /not room on this PC/);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 0);
  });

  test("the question refused deletes nothing; rapid clicks ask once and send one request", async (t) => {
    const ctx = await view(t);
    pick(ctx, 2);
    ctx.answer = false;
    await remove(ctx);
    assert.equal(ctx.questions.length, 1);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 0);
    ctx.answer = true;
    for (let n = 0; n < 4; n++) click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
    assert.equal(calls(ctx, "/selection/delete-check").length, 2, "one more check for four clicks");
    assert.equal(ctx.questions.length, 2);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 1);
  });

  test("Select all of 5,002 less 2: the source and the 2, one question, never 5,000 ids", async (t) => {
    const ctx = await view(t, 5002, { where: { ...BIN, total: 5000, token: "t", folders: 2672 } });
    el(ctx, "btn-select-all-thumbnails").click();
    pick(ctx, 3);
    pick(ctx, 4);
    await remove(ctx);
    assert.equal(ctx.questions.length, 1);
    assert.match(ctx.questions[0], /^Delete 5,000 photos\?.* It takes about 6 minutes; TagPup is busy until it is done/, "the measured 15 a second (#693)");
    assert.deepEqual(plain(calls(ctx, "/api/library/bulk-edit")[0].body.selection), { source: { kind: "all", value: null, recursive: false }, excluded: [3, 4] });
  });

  test("more than 5,000 is said and nothing is asked of the server", async (t) => {
    const ctx = await view(t, 300, { onIds: () => ({ source: {}, total: 5001, ids: range(300), videos: [], complete: false }) });
    el(ctx, "btn-select-all-thumbnails").click();
    await remove(ctx);
    assert.equal(ctx.alerts[0], "Select up to 5,000 photos at a time (5,001 are selected).");
    assert.equal(calls(ctx, "/selection/delete-check").length, 0);
    assert.equal(ctx.questions.length, 0);
  });

  test("where the files are cannot be told (the library busy, a share away): said, nothing asked, nothing deleted", async (t) => {
    const ctx = await view(t);
    ctx.server.first("/api/library/selection/delete-check", { error: "The library is busy." }, { status: 500 });
    pick(ctx, 2);
    await remove(ctx);
    assert.equal(ctx.questions.length, 0);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 0);
    assert.match(el(ctx, "status-text").textContent, /nothing was deleted: The library is busy\./);
  });

  test("while a bulk edit is written, Delete is off with the reason, and a click sends nothing; the page says how many and how long", async (t) => {
    const ctx = await view(t);
    ctx.hold.bulkEdit = true;
    pick(ctx, 2);
    el(ctx, "bulk-add-tags-input").value = "Trips/Coast";
    click(ctx.window, el(ctx, "btn-bulk-add-tags"));
    await ctx.settle(40);
    assert.equal(el(ctx, "btn-delete-selection").disabled, true);
    assert.match(el(ctx, "btn-delete-selection").title, /A bulk edit is being written/);
    assert.match(el(ctx, "status-text").textContent, /^Add Trips\/Coast to 1 photo\.\.\./);
    await ctx.module("bulk-edit.js").deleteSelection();
    assert.equal(calls(ctx, "/selection/delete-check").length, 0);
    ctx.held.splice(0).forEach((each) => each.release());
    await ctx.settle(40);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 1, "only the tags edit");
    assert.equal(el(ctx, "btn-delete-selection").disabled, false);
  });

  test("when it ends the view's order is read again: the deleted leave the view, the count and the selection; the strip says so", async (t) => {
    let ids = range(40);
    const ctx = await view(t, 40, { onIds: () => ({ source: {}, total: ids.length, ids, videos: [], complete: true }) });
    for (const id of [2, 5, 9]) pick(ctx, id);
    const asked = ctx.idsAsked.length;
    ids = ids.filter((id) => ![2, 5, 9].includes(id));   // the server has deleted them by the time it answers
    await remove(ctx);
    await ctx.until(() => ctx.idsAsked.length > asked);
    await ctx.settle(100);
    assert.equal(ctx.state.library.total, 37);
    assert.deepEqual(ctx.selectedIds(), [], "the deleted are no longer selected");
    assert.equal(stripSentence(ctx), "Deleted 3 photos to the Recycle Bin; 0 errors.");
  });

  test("the photo open in the details panel was deleted: the panel closes onto the grid when the edit is done", async (t) => {
    let ids = range(40);
    const ctx = await view(t, 40, { onIds: () => ({ source: {}, total: ids.length, ids, videos: [], complete: true }), onPhoto: (id) => ({ photo: recordOf(id) }) });
    ctx.bulk.edit = { ...served.deleted, total: 1, requested: 1, changed: 1 };
    pick(ctx, 5);
    ctx.module("photo.js").openLibraryPhoto(5);
    await ctx.settle(100);
    assert.ok(ctx.state.activePhotoPath, "the photo is open");
    ids = ids.filter((id) => id !== 5);
    await remove(ctx);
    await ctx.until(() => ctx.state.activePhotoPath === null);
    assert.equal(ctx.state.activePhotoPath, null);
    assert.ok(el(ctx, "panel-content").classList.contains("hidden"));
    assert.ok(!el(ctx, "folder-view-content").classList.contains("hidden"));
    assert.equal(ctx.photosAsked.filter((id) => id === 5).length, 1, "the deleted photo is not asked for again");
  });

  // Files on a drive whose Recycle Bin would not keep them (DECISIONS 2026-11-08, 'FOR GOOD'): the delete-check counts them and
  // names the first five (selection.where_deleted); the page warns, naming them, and starts with for_good only on OK.
  const FOR_GOOD = (count, names) => ({ ...served.delete_check_for_good, bin_full: 0, for_good: { count, names } });

  test("some its drive's Bin would not keep: a second question names them, and OK alone starts the delete for good", async (t) => {
    const ctx = await view(t, 40, { where: FOR_GOOD(1, ["Harbour_Swim_Long.mp4"]) });
    ctx.bulk.edit = { ...served.deleted_for_good, for_good: 1 };
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.equal(ctx.questions.length, 2, "the delete was asked, then the warning");
    assert.match(ctx.questions[0], /^Delete 3 photos\?/);
    assert.match(ctx.questions[1], /^This file would be deleted for good, not moved to the Recycle Bin/);
    assert.ok(ctx.questions[1].includes("  Harbour_Swim_Long.mp4"), "the warning names the file");
    assert.match(ctx.questions[1], /or a video too large for TagPup to copy to one\)/,
      "a video over 2 GiB is told as a reason: no copy of it is made through this PC (cut C)");
    assert.match(ctx.questions[1], /Cancel deletes nothing\.$/);
    const [start] = calls(ctx, "/api/library/bulk-edit");
    assert.deepEqual(plain(start.body.params), { token: FOR_GOOD_TOKEN, for_good: true },
      "the token of the question that warned, and the owner's OK");
    assert.match(stripSentence(ctx), /^Deleted 3 photos to the Recycle Bin, 1 of them for good;/);
  });

  test("this PC's Recycle Bin is full: both questions say so, and OK starts the delete for good with the question's token", async (t) => {
    // Owner, 2026-11-12: the share's files whose copies the full Bin would not keep are asked of, not refused (`bin_full`).
    const ctx = await view(t, 40, { where: { ...FOR_GOOD(3, ["Pier_A.jpg", "Pier_B.jpg", "Pier_C.jpg"]), bin_full: 2 } });
    ctx.bulk.edit = { ...served.deleted_for_good };
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.equal(ctx.questions.length, 2);
    assert.match(ctx.questions[0], /a Recycle Bin that would keep it is full/);
    assert.match(ctx.questions[1], /^The Recycle Bin is full: delete permanently instead\?/);
    assert.match(ctx.questions[1], /2 of these 3 files are headed for a Recycle Bin that is full/);
    assert.match(ctx.questions[1], /The other 1 would not be kept by any Recycle Bin/);
    assert.ok(ctx.questions[1].includes("  Pier_B.jpg"));
    assert.match(ctx.questions[1], /OK deletes them permanently; Cancel deletes nothing\.$/);
    assert.deepEqual(plain(calls(ctx, "/api/library/bulk-edit")[0].body.params), { token: FOR_GOOD_TOKEN, for_good: true });
  });

  test("a Bin with room for some: the first question says how many are deleted for good and that the rest go to the Bin", async (t) => {
    // The server weighs the files one after another, as the start deletes them (selection.where_deleted): 3 fit, 2 do not.
    const ctx = await view(t, 40, { where: { ...FOR_GOOD(2, ["Shore_D.jpg", "Shore_E.jpg"]), total: 5, bin_full: 2 } });
    ctx.bulk.edit = { ...served.deleted_for_good, total: 5, requested: 5, changed: 5, for_good: 2 };
    for (const id of [2, 5, 9, 11, 14]) pick(ctx, id);
    await remove(ctx);
    assert.match(ctx.questions[0], /^Delete 5 photos\?/);
    assert.match(ctx.questions[0], /2 of them are deleted for good, not moved to a Recycle Bin/);
    assert.match(ctx.questions[0], /full by the time it comes to it/);
    assert.match(ctx.questions[0], /The other 3 go to the Recycle Bin, where they can be restored\./);
  });

  test("a clip over 2 GiB: the first question already says it is deleted for good, not that it goes to a Recycle Bin or is copied", async (t) => {
    const ctx = await view(t, 40, { where: { ...FOR_GOOD(1, ["Harbour_Swim_Long.mp4"]), through_this_pc: 1, copy_bytes: 4 * 1024 * 1024 } });
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.match(ctx.questions[0], /1 of them is deleted for good, not moved to a Recycle Bin and not copied to this PC/);
    assert.match(ctx.questions[0], /a video over 2 GiB/);
    assert.match(ctx.questions[0], /you are asked next, by name/);
    assert.doesNotMatch(ctx.questions[0], /The other 2 go to the Recycle Bin/, "the share's one is copied, the clip is neither");
    const alone = await view(t, 40, { where: FOR_GOOD(1, ["Harbour_Swim_Long.mp4"]) });
    alone.bulk.edit = { ...served.deleted_for_good, total: 1, requested: 1, changed: 1, for_good: 1 };
    pick(alone, 2);
    await remove(alone);
    assert.match(alone.questions[0], /^Delete 3 photos\? 1 of them is deleted for good.* The other 2 go to the Recycle Bin, where they can be restored\./);
  });

  test("the file the warning named was not removed (its delete failed): the strip says none went for good, from the reply", async (t) => {
    const ctx = await view(t, 40, { where: FOR_GOOD(1, ["Harbour_Swim_Long.mp4"]) });
    ctx.bulk.edit = { ...served.deleted_for_good, changed: 2, for_good: 0, error_count: 1,
      errors: [{ id: 5, name: "Harbour_Swim_Long.mp4", why: "The file is in use" }] };
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.deepEqual(plain(calls(ctx, "/api/library/bulk-edit")[0].body.params), { token: FOR_GOOD_TOKEN, for_good: true });
    assert.match(stripSentence(ctx), /^Deleted 2 photos to the Recycle Bin;/, "a write says what it changed, not what it asked");
    assert.doesNotMatch(stripSentence(ctx), /for good/);
  });

  test("Cancel at the warning starts nothing; seven named five and counted the rest", async (t) => {
    const names = ["Clip_A.mp4", "Clip_B.mp4", "Clip_C.mp4", "Clip_D.mp4", "Clip_E.mp4"];
    const ctx = await view(t, 40, { where: FOR_GOOD(7, names) });
    for (const id of [2, 5, 9]) pick(ctx, id);
    ctx.answer = (text) => !/^Th(is file|ese files) would be deleted for good/.test(text);
    await remove(ctx);
    assert.equal(ctx.questions.length, 2);
    assert.match(ctx.questions[1], /^These files would be deleted for good/);
    for (const name of names) assert.ok(ctx.questions[1].includes(`  ${name}`), `${name} is named`);
    assert.match(ctx.questions[1], /\n  and 2 more\n/);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 0, "nothing started: nothing deleted, to the Bin or for good");
  });

  test("the warning names every file the server named, and counts the rest: how many to name is the server's alone", async (t) => {
    const names = ["Clip_A.mp4", "Clip_B.mp4", "Clip_C.mp4", "Clip_D.mp4", "Clip_E.mp4", "Clip_F.mp4"];
    const ctx = await view(t, 40, { where: FOR_GOOD(8, names) });
    for (const id of [2, 5, 9]) pick(ctx, id);
    ctx.answer = (text) => !/^Th(is file|ese files) would be deleted for good/.test(text);
    await remove(ctx);
    for (const name of names) assert.ok(ctx.questions[1].includes(`  ${name}`), `${name} is named`);
    assert.match(ctx.questions[1], /\n  and 2 more\n/);
  });

  test("none for good: one question, and the start does not say for good", async (t) => {
    const ctx = await view(t, 40, { where: FOR_GOOD(0, []) });
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.equal(ctx.questions.length, 1);
    assert.deepEqual(plain(calls(ctx, "/api/library/bulk-edit")[0].body.params), { token: FOR_GOOD_TOKEN });
  });

  // A clip open in the panel among the photos deleted: the server reads it a range at a time while it plays, and a range read as its
  // file is deleted leaves the file there ('the file is there twice'); deleted, a player kept on would play from what it held, then fail.
  const CLIP = 5;
  async function viewWithClipOpen(t, ids) {
    const ctx = await view(t, 40, {
      onIds: () => ({ source: {}, total: ids().length, ids: ids(), videos: [CLIP], complete: true }),
      cardExtra: (id) => ({ media: id === CLIP ? "video" : "photo" }),
      onPhoto: (id) => ({ photo: recordOf(id, id === CLIP ? { media: "video", mtime: 1600000123.5, writes: CLIP_WRITES } : {}) }),
      before: (window) => {
        window.HTMLMediaElement.prototype.pause = () => {};
        window.HTMLMediaElement.prototype.load = () => {};
      },
    });
    ctx.bulk.edit = { ...served.deleted, total: 2, requested: 2, changed: 2 };
    for (const id of [2, CLIP]) pick(ctx, id);
    ctx.module("photo.js").openLibraryPhoto(CLIP);
    await ctx.settle(100);
    assert.match(src(ctx), /\/api\/video-file\?id=5&/, "the clip plays in the panel");
    return ctx;
  }
  const src = (ctx) => el(ctx, "main-video").getAttribute("src") || "";

  test("a clip playing in the panel is stopped before the delete starts, and stays stopped when it was deleted", async (t) => {
    let ids = range(40);
    const ctx = await viewWithClipOpen(t, () => ids);
    ctx.hold.bulkEdit = true;
    await remove(ctx);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 1, "the delete is out");
    assert.equal(src(ctx), "", "and the clip let go of before it: the server holds no range of its file");
    ids = ids.filter((id) => ![2, CLIP].includes(id));
    await ctx.release("bulkEdit");
    await ctx.until(() => ctx.state.activePhotoPath === null);
    await ctx.settle(80);
    assert.equal(src(ctx), "", "deleted: nothing plays it again");
    assert.ok(el(ctx, "panel-content").classList.contains("hidden"), "the panel closed onto the grid");
  });

  // Which files a delete is working on is the server's to know: a Select all names a source, which holds every id outside it too.
  test("while a Select all's delete is out, a clip opened again is asked of the server; refused, its sentence is said", async (t) => {
    let ids = range(40);
    const ctx = await viewWithClipOpen(t, () => ids);
    el(ctx, "btn-select-all-thumbnails").click();
    ctx.hold.bulkEdit = true;
    await remove(ctx);
    assert.ok(plain(calls(ctx, "/api/library/bulk-edit")[0].body.selection).source, "the delete of a Select all is out");
    assert.equal(src(ctx), "", "the clip was stopped before it");
    ctx.module("photo.js").openLibraryPhoto(7);      // another photo, then the clip again, as a click on its card does
    await ctx.settle(80);
    ctx.module("photo.js").openLibraryPhoto(CLIP);
    await ctx.settle(80);
    assert.equal(ctx.state.library.activeId, CLIP, "the clip is the one shown");
    assert.match(src(ctx), /\/api\/video-file\?id=5&/, "and asked of the server: the page does not guess what is being deleted");
    ctx.server.first("/api/video-file", { success: false, error: "This video is being deleted." }, { status: 409 });
    Object.defineProperty(el(ctx, "main-video"), "error", { value: { code: 4 }, configurable: true });
    el(ctx, "main-video").dispatchEvent(new ctx.window.Event("error"));
    await ctx.settle(40);
    assert.equal(el(ctx, "status-text").textContent, "This video is being deleted.", "the server's sentence, not 'not there'");
    ids = ids.filter((id) => ![2, CLIP].includes(id));
    await ctx.release("bulkEdit");
    await ctx.until(() => ctx.state.activePhotoPath === null);
    await ctx.settle(80);
    assert.equal(src(ctx), "", "deleted: nothing plays it");
  });

  test("the delete refused (the selection changed), or the clip left by an error of its own: it plays again", async (t) => {
    const refused = await viewWithClipOpen(t, () => range(40));
    refused.server.first("/api/library/bulk-edit", { success: false, error: "The selection changed since you were asked: ask again." }, { status: 409 });
    await remove(refused);
    assert.equal(calls(refused, "/api/library/bulk-edit").length, 1);
    assert.match(src(refused), /\/api\/video-file\?id=5&/, "refused: nothing deleted, the clip plays again");

    let ids = range(40);
    const kept = await viewWithClipOpen(t, () => ids);
    kept.bulk.edit = { ...served.deleted, total: 2, requested: 2, changed: 1, error_count: 1, errors: [{ id: CLIP, name: "Pier_Jump.mp4", why: "The file is in use" }] };
    ids = ids.filter((id) => id !== 2);     // the photo went; the clip, its error said, stayed
    await remove(kept);
    await kept.until(() => src(kept) !== "");
    assert.equal(kept.state.library.activeId, CLIP, "the clip stayed: still the one shown");
    assert.match(src(kept), /\/api\/video-file\?id=5&/, "and plays again once the view's order is read");
  });
});

describe("#691-#693: a Delete deletes exactly what its question named", () => {
  const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
  const plain = (value) => JSON.parse(JSON.stringify(value));
  const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
  const calls = (ctx, part) => ctx.server.calls.filter((call) => call.url.includes(part));

  async function view(t, where) {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    speedUp(ctx);
    ctx.where = where;
    ctx.server.on("/api/library/selection/delete-check", () => (typeof ctx.where === "function" ? ctx.where() : ctx.where));
    ctx.questions = [];
    ctx.window.confirm = (text) => { ctx.questions.push(text); return true; };
    ctx.bulk.edit = { ...served.deleted };
    return ctx;
  }

  test("the question names the server's count, which its token is of, not the page's", async (t) => {
    const ctx = await view(t, { ...served.delete_check, total: 4, token: "four" });
    el(ctx, "btn-select-all-thumbnails").click();
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
    assert.match(ctx.questions[0], /^Delete 4 photos\?/);
    assert.equal(plain(calls(ctx, "/api/library/bulk-edit")[0].body.params).token, "four");
  });

  test("the server refuses a delete whose selection changed: its sentence is shown and nothing was deleted", async (t) => {
    const ctx = await view(t, served.delete_check);
    ctx.server.first("/api/library/bulk-edit", { error: "The selection changed since you were asked (photos came into it or left it): nothing was deleted. Ask again: click Delete once more." }, { status: 409 });
    for (const id of [2, 5, 9]) pick(ctx, id);
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
    assert.match(stripSentence(ctx), /The selection changed since you were asked/);
    assert.equal(el(ctx, "btn-delete-selection").disabled, false, "the controls are back");
  });

  test("asked again, the question is asked afresh: where the files are, the question, a new token", async (t) => {
    let n = 0;
    const ctx = await view(t, () => ({ ...served.delete_check, token: `token-${++n}` }));
    for (const id of [2, 5, 9]) pick(ctx, id);
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(60);
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
    assert.equal(calls(ctx, "/selection/delete-check").length, 2, "asked where the files are again");
    assert.equal(ctx.questions.length, 2, "and the question asked again");
    const [first, second] = calls(ctx, "/api/library/bulk-edit");
    assert.equal(plain(first.body.params).token, "token-1");
    assert.equal(plain(second.body.params).token, "token-2", "the new answer's token, not the first");
  });
});

describe("#704, #705: the question's paths and pace for photos that go through this PC", () => {
  const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
  async function ask(t, where, n = 3) {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    speedUp(ctx);
    ctx.server.on("/api/library/selection/delete-check", () => where);
    ctx.questions = [];
    ctx.window.confirm = (text) => { ctx.questions.push(text); return false; };
    for (const id of range(n, 2)) click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
    return ctx.questions[0];
  }
  const WHERE = { ...served.delete_check, reasons: [{ reason: "on a network share", photos: 3 }] };

  test("photos with paths too long to copy are counted in the question as left", async (t) => {
    const asked = await ask(t, { ...WHERE, total: 3, through_this_pc: 3, copy_bytes: 3000, too_long: 2 });
    assert.match(asked, /2 of them have a path too long to copy there \(260 characters or more\) and are left where they are, each an error\./);
  });

  test("the pace of copies through this PC is its own, and a floor: 'at least'", async (t) => {
    const local = await ask(t, { ...WHERE, total: 3000, through_this_pc: 0, reasons: [], copy_bytes: 0 });
    assert.match(local, /It takes about 3 minutes;/);
    const share = await ask(t, { ...WHERE, total: 3000, through_this_pc: 3000, reasons: [{ reason: "on a network share", photos: 3000 }], copy_bytes: 3000 * 3.4 * 1024 * 1024 });
    assert.match(share, /It takes at least about 4 minutes \(copies from a share take as long as the share does\)/, "3,000 at 15 a second, and 10 GB at 200 MB a second");
  });
});
