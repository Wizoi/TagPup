/**
 * The owner's first review of the library views after installing phase 9 (docs/findings.md #668-#675; this file holds the
 * header, the strip, the toolbar, the selection details and Delete -- #671-#673, the navigator's, are tested beside it).
 * Fictional names only: the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

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
