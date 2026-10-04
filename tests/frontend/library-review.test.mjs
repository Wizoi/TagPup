/**
 * The owner's first review of the library views after installing phase 9 (docs/findings.md #668-#675; this file holds the
 * header, the strip, the toolbar, the selection details and Delete -- #671-#673, the navigator's, are tested beside it).
 * Fictional names only: the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord } from "./harness.mjs";
import { loadViewPage, pageErrors, jobStatus, speedUp, recordOf } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const el = (ctx, id) => ctx.document.getElementById(id);

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
  const BIN = { total: 3, folders: 1, permanent: 0, reasons: [] };

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
    ctx.bulk.start = { op: "delete", total: 3 };
    ctx.bulk.status = jobStatus({ op: "delete", total: 3 });
    return ctx;
  }
  const remove = async (ctx) => {
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
  };

  test("the button is a view's: shown in every view, not in Organize", async (t) => {
    const ctx = await view(t);
    assert.ok(!el(ctx, "btn-delete-selection").classList.contains("hidden"));
    const organize = await loadViewPage(t, { search: "" });
    assert.ok(organize.document.getElementById("btn-delete-selection").classList.contains("hidden"));
  });

  test("3 photos across folders: asked where they go, then the question names them and the Recycle Bin, then a job by id", async (t) => {
    const ctx = await view(t);
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.deepEqual(plain(calls(ctx, "/selection/delete-check")[0].body), { selection: { ids: [2, 5, 9] } });
    assert.deepEqual(ctx.questions, ["Delete 3 photos? The files go to the Recycle Bin, where they can be restored. Their rows leave the library, with their faces. It runs as a job you can watch and cancel; it cannot be undone in TagPup."]);
    const [start] = calls(ctx, "/bulk/start");
    assert.deepEqual(plain(start.body), { op: "delete", selection: { ids: [2, 5, 9] }, params: { permanent: false } });
    assert.ok(!calls(ctx, "/api/photo/delete").length, "never the one-photo route, once a photo");
    assert.equal(el(ctx, "bulk-strip-title").textContent, "Delete 3 photos to the Recycle Bin");
  });

  test("some on a share with no Recycle Bin: the question says how many are deleted for good, and only then is that sent", async (t) => {
    const ctx = await view(t, 40, { where: { total: 3, folders: 2, permanent: 1, reasons: [{ reason: "on a network share", photos: 1 }] } });
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    assert.match(ctx.questions[0], /^Delete 3 photos\? 1 of them is deleted PERMANENTLY, not moved to the Recycle Bin: there is none where it is \(1 on a network share\)\. The other 2 go to the Recycle Bin\./);
    assert.deepEqual(plain(calls(ctx, "/bulk/start")[0].body.params), { permanent: true });
    const all = await view(t, 40, { where: { total: 1, folders: 1, permanent: 1, reasons: [{ reason: "on a removable drive", photos: 1 }] } });
    pick(all, 4);
    await remove(all);
    assert.match(all.questions[0], /^Delete 1 photo\? It is deleted PERMANENTLY, not moved to the Recycle Bin: there is none where it is \(1 on a removable drive\), so the file cannot be restored\./);
  });

  test("the question refused deletes nothing; rapid clicks ask once and start one job", async (t) => {
    const ctx = await view(t);
    pick(ctx, 2);
    ctx.answer = false;
    await remove(ctx);
    assert.equal(ctx.questions.length, 1);
    assert.equal(calls(ctx, "/bulk/start").length, 0);
    ctx.answer = true;
    for (let n = 0; n < 4; n++) click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
    assert.equal(calls(ctx, "/selection/delete-check").length, 2, "one more check for four clicks");
    assert.equal(ctx.questions.length, 2);
    assert.equal(calls(ctx, "/bulk/start").length, 1);
  });

  test("Select all of 68,000 less 2: the source and the 2, a second question, never 68,000 ids", async (t) => {
    const ctx = await view(t, 68000, { where: { total: 67998, folders: 2672, permanent: 0, reasons: [] } });
    el(ctx, "btn-select-all-thumbnails").click();
    pick(ctx, 3);
    pick(ctx, 4);
    await remove(ctx);
    assert.equal(ctx.questions.length, 2);
    assert.match(ctx.questions[0], /^Delete 67,998 photos\?/);
    assert.equal(ctx.questions[1], "This is 67,998 photos. Continue?");
    assert.deepEqual(plain(calls(ctx, "/bulk/start")[0].body.selection), { source: { kind: "all", value: null, recursive: false }, excluded: [3, 4] });
  });

  test("more than a bulk edit takes is said and nothing is asked of the server", async (t) => {
    const ctx = await view(t, 300, { onIds: () => ({ source: {}, total: 250000, ids: range(300), complete: false }) });
    el(ctx, "btn-select-all-thumbnails").click();
    await remove(ctx);
    assert.match(ctx.alerts[0], /250,000 photos are selected; a bulk edit takes at most 200,000/);
    assert.equal(calls(ctx, "/selection/delete-check").length, 0);
    assert.equal(ctx.questions.length, 0);
  });

  test("where the files are cannot be told (the library busy, a share away): said, nothing asked, nothing deleted", async (t) => {
    const ctx = await view(t);
    ctx.server.first("/api/library/selection/delete-check", { error: "The library is busy." }, { status: 500 });
    pick(ctx, 2);
    await remove(ctx);
    assert.equal(ctx.questions.length, 0);
    assert.equal(calls(ctx, "/bulk/start").length, 0);
    assert.match(el(ctx, "status-text").textContent, /nothing was deleted: The library is busy\./);
  });

  test("while a bulk edit runs, Delete is off with the reason, and a click starts nothing", async (t) => {
    const ctx = await view(t);
    ctx.bulk.start = { op: "tags", total: 1 };
    ctx.bulk.status = jobStatus({ total: 1 });
    pick(ctx, 2);
    el(ctx, "bulk-add-tags-input").value = "Trips/Coast";
    click(ctx.window, el(ctx, "btn-bulk-add-tags"));
    await ctx.settle(40);
    assert.equal(el(ctx, "btn-delete-selection").disabled, true);
    assert.match(el(ctx, "btn-delete-selection").title, /A bulk edit is running/);
    await ctx.module("bulk-edit.js").deleteSelection();
    assert.equal(calls(ctx, "/selection/delete-check").length, 0);
    assert.equal(calls(ctx, "/bulk/start").length, 1, "only the tags job");
  });

  test("when it ends the view's order is read again: the deleted leave the view, the count and the selection; the strip says so", async (t) => {
    let ids = range(40);
    const ctx = await view(t, 40, { onIds: () => ({ source: {}, total: ids.length, ids, complete: true }) });
    for (const id of [2, 5, 9]) pick(ctx, id);
    await remove(ctx);
    const asked = ctx.idsAsked.length;
    ids = ids.filter((id) => ![2, 5, 9].includes(id));
    ctx.bulk.status = jobStatus({ op: "delete", total: 3, done: 3, changed: 3, state: "done", finished: 1760000100 });
    await ctx.until(() => ctx.idsAsked.length > asked);
    await ctx.settle(100);
    assert.equal(ctx.state.library.total, 37);
    assert.deepEqual(ctx.selectedIds(), [], "the deleted are no longer selected");
    assert.equal(el(ctx, "bulk-strip-message").textContent, "Deleted 3 photos to the Recycle Bin; 0 errors.");
    assert.match(el(ctx, "bulk-strip-counts").textContent, /^deleted 3 /);
  });

  test("the photo open in the details panel was deleted: the panel closes onto the grid when the job ends", async (t) => {
    let ids = range(40);
    const ctx = await view(t, 40, { onIds: () => ({ source: {}, total: ids.length, ids, complete: true }), onPhoto: (id) => ({ photo: recordOf(id) }) });
    pick(ctx, 5);
    await remove(ctx);
    ctx.module("photo.js").openLibraryPhoto(5);
    await ctx.settle(100);
    assert.ok(ctx.state.activePhotoPath, "the photo is open");
    ids = ids.filter((id) => id !== 5);
    ctx.bulk.status = jobStatus({ op: "delete", total: 1, done: 1, changed: 1, state: "done", finished: 1760000100 });
    await ctx.until(() => ctx.state.activePhotoPath === null);
    assert.equal(ctx.state.activePhotoPath, null);
    assert.ok(el(ctx, "panel-content").classList.contains("hidden"));
    assert.ok(!el(ctx, "folder-view-content").classList.contains("hidden"));
    assert.equal(ctx.photosAsked.filter((id) => id === 5).length, 1, "the deleted photo is not asked for again");
  });

  test("a delete found stopped after a reload says how to finish it", async (t) => {
    const ctx = await view(t);
    ctx.bulk.current = jobStatus({ op: "delete", total: 100, done: 40, changed: 40, state: "abandoned", message: "TagPup was closed before this finished: 40 of 100 photos were done." });
    await ctx.module("bulk-job.js").attachBulk({ force: true });
    await ctx.settle(40);
    assert.equal(el(ctx, "bulk-strip-title").textContent, "Delete 100 photos");
    assert.match(el(ctx, "bulk-strip-message").textContent, /Deleted 40 photos; 0 errors\. Select the photos that are left and delete them again to finish it\.$/);
  });
});
