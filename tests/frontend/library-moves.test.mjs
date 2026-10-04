/**
 * The move between a folder on disk and its view of the library (web/tagpup/library-moves.js, library-banner.js,
 * sync-state.js; phase 9c): Show in library, a folder opened in Organize from a view (openInOrganize: the strip's Show on
 * disk and This folder only went with #670), the banner for photos the disk holds and the library does not (asked after the
 * view paints, with a deadline, and shown only as an offer), and when the library was last in step -- said only when something
 * is wrong (#670). Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord, flush } from "./harness.mjs";
import { loadViewPage, pageErrors, GRID_TOP, STRIDE, cardOf } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const FOLDER = "D:\\Library\\2020\\Event 01";
const photo = (n) => photoRecord({ filename: `IMG_${String(n).padStart(4, "0")}.jpg`, path: `${FOLDER}\\IMG_${String(n).padStart(4, "0")}.jpg`, raw_metadata: { "EXIF:DateTimeOriginal": `2020:01:01 10:${String(n % 60).padStart(2, "0")}:00` } });
const scan = (count) => () => Array.from({ length: count }, (_, i) => photo(i + 1));
const INSET = 12;   // the strip is sticky above the grid: a card scrolled to is put below it (jsdom: no height, the 12 px margin)

/** The folder open on disk, as the folder box opens it. */
async function folderPage(t, options = {}) {
  const ctx = await loadViewPage(t, { scan: scan(options.photos ?? 60), ...options });
  await openFolder(ctx, FOLDER);
  await ctx.settle(60);
  return ctx;
}

describe("Show in library", () => {
  test("the button is there while a folder is open on disk, and not in a view or with no folder", async (t) => {
    const none = await loadViewPage(t);
    assert.ok(none.document.getElementById("btn-show-in-library").classList.contains("hidden"));
    const ctx = await folderPage(t);
    assert.ok(!ctx.document.getElementById("btn-show-in-library").classList.contains("hidden"));
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle();
    assert.ok(ctx.document.getElementById("btn-show-in-library").classList.contains("hidden"), "a view of the library is not a folder on disk");
    const view = await loadViewPage(t, { search: "?view=all" });
    assert.ok(view.document.getElementById("btn-show-in-library").classList.contains("hidden"));
  });

  test("it opens the same folder's library view with its subfolders, and the address names it", async (t) => {
    const ctx = await folderPage(t);
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "folder");
    assert.equal(ctx.state.library.value, FOLDER);
    assert.equal(ctx.state.library.recursive, true);
    assert.match(ctx.window.location.search, /view=folder&value=D%3A%5CLibrary%5C2020%5CEvent\+01&recursive=1/);
    assert.match(ctx.idsAsked.at(-1), /kind=folder&folder=D%3A%5CLibrary%5C2020%5CEvent%2001&recursive=1$/);
    assert.equal(ctx.state.nav.shown, "library");
    assert.equal(ctx.state.nav.tab, "folders");
  });

  test("the selection is cleared by the move", async (t) => {
    const ctx = await folderPage(t);
    ctx.real()[0].click();
    ctx.real()[1].click();
    assert.equal(ctx.state.selectedThumbnails.length, 2);
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle();
    assert.equal(ctx.state.selectedThumbnails.length, 0);
    assert.equal(ctx.state.selectedKeys.size, 0);
  });

  test("it lands on the photo that was at the top, when the library holds it: its id is asked of the library", async (t) => {
    const ids = Array.from({ length: 400 }, (_, i) => 1000 + i);
    const ctx = await folderPage(t, { photos: 400, ids, find: () => ({ id: 1000 + 200 }) });
    // The folder view scrolled to the 41st row: its top photo is the 160th.
    await ctx.scrollTo(GRID_TOP + 40 * STRIDE);
    const top = ctx.state.shownPhotos[ctx.state.grid.extent().viewFrom].path;
    assert.equal(top, photo(161).path);
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle(200);
    assert.match(ctx.server.urls().find((url) => url.includes("/api/library/find")), new RegExp(`path=${encodeURIComponent(top).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`));
    // The photo (the 201st of the view) is at the top of the grid, below the strip.
    assert.equal(ctx.here.scrollTop, GRID_TOP + 50 * STRIDE - INSET);
  });

  test("a photo the library does not hold leaves the view at the top, and nothing is said", async (t) => {
    const ids = Array.from({ length: 400 }, (_, i) => 1000 + i);
    const ctx = await folderPage(t, { photos: 400, ids });
    ctx.server.first("/api/library/find", { id: null });
    await ctx.scrollTo(GRID_TOP + 40 * STRIDE);
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle(200);
    assert.equal(ctx.here.scrollTop, 0);
    assert.equal(ctx.state.library.status, "ready");
  });

  test("a folder with no rows in the library: the view says so, and the banner offers the photos", async (t) => {
    const ctx = await folderPage(t, { ids: [], membership: { folder: FOLDER, photos: 60, photos_held: 0, photos_not_held: 60, folders_not_held: 1 } });
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle(100);
    assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /The library holds no photo in D:\\Library\\2020\\Event 01 or below it/);
    assert.ok(!ctx.document.getElementById("moves-banner").classList.contains("hidden"));
    assert.match(ctx.document.getElementById("moves-banner-text").textContent, /^60 photos in this folder are not in photo_index\.$/);
  });
});

describe("a folder opened in Organize from a view (openInOrganize)", () => {
  const FOLDER_VIEW = `?view=folder&value=${encodeURIComponent(FOLDER)}&recursive=1`;
  const organize = (ctx, folder = FOLDER) => ctx.module("library-moves.js").openInOrganize(folder);

  test("the strip has neither Show on disk nor This folder only, nor Back to folder view; Refresh view stays (#670)", async (t) => {
    const folder = await loadViewPage(t, { search: FOLDER_VIEW });
    for (const id of ["btn-show-on-disk", "btn-library-scope", "library-strip-back"]) assert.equal(folder.document.getElementById(id), null, id);
    assert.ok(folder.document.getElementById("btn-library-refresh"));
    assert.doesNotMatch(folder.stripText(), /Show on disk|This folder only|Back to folder view/);
  });

  test("it opens the folder in Organize, scanned, with the view gone and the selection cleared; Back returns to the view", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=year&value=2020", scan: scan(60) });
    await ctx.settle(100);
    ctx.real()[0].click();
    assert.equal(ctx.selectedIds().length, 1);
    assert.equal(await organize(ctx), true);
    await ctx.settle(200);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, FOLDER);
    assert.equal(ctx.document.getElementById("folder-path-input").value, FOLDER);
    assert.ok(ctx.server.urls().some((url) => url.includes("/api/folder/scan?path=D%3A%5CLibrary%5C2020%5CEvent%2001")));
    assert.equal(ctx.state.selectedThumbnails.length, 0);
    assert.doesNotMatch(ctx.window.location.search, /view=/);
    assert.equal(ctx.state.nav.shown, "folder");
    ctx.window.history.back();
    await ctx.settle(300);
    assert.equal(ctx.state.library && ctx.state.library.kind, "year", "Back is the view it was opened from");
  });

  test("it lands on the photo that was at the top of the view, when the folder holds it", async (t) => {
    const ids = Array.from({ length: 400 }, (_, i) => 1000 + i);
    const cards = new Map(ids.map((id, i) => [id, cardOf(id, { path: photo(i + 1).path, name: photo(i + 1).filename })]));
    const ctx = await loadViewPage(t, {
      search: FOLDER_VIEW, ids, scan: scan(400),
      onCards: (asked) => ({ cards: asked.map((id) => cards.get(id)) }),
    });
    await ctx.scrollTo(GRID_TOP + 40 * STRIDE);
    await ctx.settle(300);
    await organize(ctx);
    await ctx.settle(300);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.here.scrollTop, GRID_TOP + 40 * STRIDE, "the 161st photo is at the top again (no strip above a folder view)");
  });

  test("a folder that is not on disk keeps the view and says so; the page is never left with neither a view nor a folder (#569)", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW });
    const said = [];
    ctx.window.alert = (text) => said.push(text);
    ctx.server.first("/api/folder/scan", { error: "Path is not a valid directory: D:\\Library\\2020\\Event 01" }, { status: 400 });
    assert.equal(await organize(ctx), false);
    await ctx.settle(200);
    assert.ok(ctx.state.library, "the view is still open");
    assert.equal(ctx.state.library.status, "ready");
    assert.ok(ctx.real().length > 0);
    assert.match(ctx.stripText(), /That folder is not on disk any more\./);
    assert.deepEqual(said, [], "no alert");
    assert.match(ctx.window.location.search, /view=folder/);
    assert.equal(ctx.document.getElementById("folder-path-input").value, "");
    assert.equal(ctx.state.moves.leaving, false, "and it can be tried again");
  });

  test("a scan that cannot be had for another reason (a share away) keeps the view too, with the server's sentence", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW });
    ctx.server.first("/api/folder/scan", { error: "The share did not answer." }, { status: 500 });
    await organize(ctx);
    await ctx.settle(200);
    assert.ok(ctx.state.library);
    assert.match(ctx.stripText(), /Could not open that folder on disk: The share did not answer\./);
    pageErrors();
  });

  test("the view is closed only after the scan has answered, and a second click while it is out sends no second scan", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, scan: scan(60) });
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    ctx.server.first("/api/folder/scan", () => gate.then(() => scan(60)()));
    organize(ctx);
    organize(ctx);
    await ctx.settle(60);
    assert.ok(ctx.state.library, "still the view while the scan is out");
    assert.equal(ctx.server.urls().filter((url) => url.includes("/api/folder/scan")).length, 1);
    release();
    await ctx.settle(200);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, FOLDER);
  });
});

describe("the banner: the disk holds photos the library does not", () => {
  const FOLDER_VIEW = `?view=folder&value=${encodeURIComponent(FOLDER)}&recursive=1`;
  const MEMBERSHIP = { folder: FOLDER, photos: 12, photos_held: 7, photos_not_held: 5, folders_not_held: 1, has_roots: false, under_roots: false };
  const banner = (ctx) => ctx.document.getElementById("moves-banner");
  const bannerText = (ctx) => ctx.document.getElementById("moves-banner-text").textContent;

  test("asked after the view has painted, so the view is never waiting for it; five photos are said, with Add them and Dismiss", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: MEMBERSHIP });
    await ctx.popTo("?view=all");
    ctx.hold.membership = true;
    ctx.state.moves.cache.clear();
    await ctx.popTo(FOLDER_VIEW);
    assert.equal(ctx.state.library.status, "ready");
    assert.ok(ctx.real().length > 0, "the cards are drawn while the question is out");
    assert.ok(banner(ctx).classList.contains("hidden"));
    const order = ctx.server.urls().filter((url) => /library\/ids|folder\/membership/.test(url));
    assert.ok(order.at(-1).includes("/api/folder/membership"), "the question follows the order of the view");
    assert.ok(order.at(-2).includes("/api/library/ids"));
    await ctx.release("membership");
    await ctx.settle(40);
    assert.ok(!banner(ctx).classList.contains("hidden"), "and when it answers, it is shown");
  });

  test("the answer is shown: 5 photos in this folder are not in photo_index", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: MEMBERSHIP });
    assert.ok(!banner(ctx).classList.contains("hidden"));
    assert.equal(bannerText(ctx), "5 photos in this folder are not in photo_index.");
    assert.match(ctx.membershipAsked.at(-1), /\/photo_index\/api\/folder\/membership\?path=D%3A%5CLibrary%5C2020%5CEvent%2001$/);
    const one = await loadViewPage(t, { search: FOLDER_VIEW, membership: { ...MEMBERSHIP, photos_not_held: 1 } });
    assert.equal(bannerText(one), "1 photo in this folder is not in photo_index.");
  });

  test("only a folder's view asks: a keyword's, a year's and the whole library's do not", async (t) => {
    for (const search of ["?view=all", "?view=keyword&value=Trips", "?view=year&value=2020", "?view=person&value=Wren%20Halloway"]) {
      const ctx = await loadViewPage(t, { search, membership: MEMBERSHIP });
      assert.equal(ctx.membershipAsked.length, 0, search);
      assert.ok(banner(ctx).classList.contains("hidden"), search);
    }
  });

  test("nothing missing from the library: no banner", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: { ...MEMBERSHIP, photos_not_held: 0 } });
    assert.ok(banner(ctx).classList.contains("hidden"));
  });

  test("a share away, a folder gone, a library that cannot say: no banner, no error, the view as it was", async (t) => {
    for (const make of [
      () => ({ error: "Path is not a valid directory: D:\\Gone" }),
      () => ({ error: "boom" }),
    ]) {
      const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: make });
      ctx.server.first("/api/folder/membership", make(), { status: 400 });
      await ctx.popTo("?view=all");
      await ctx.popTo(FOLDER_VIEW);
      assert.ok(banner(ctx).classList.contains("hidden"));
      assert.equal(ctx.state.library.status, "ready");
    }
    const down = await loadViewPage(t, { search: FOLDER_VIEW });
    down.server.first("/api/folder/membership", () => Promise.reject(new TypeError("Failed to fetch")));
    await down.popTo("?view=all");
    await down.popTo(FOLDER_VIEW);
    assert.ok(banner(down).classList.contains("hidden"));
    assert.equal(down.state.library.status, "ready");
    assert.deepEqual(pageErrors(), [], "a failed offer is not a fault");
  });

  test("a question that never answers is given up at its deadline, and the view goes on being usable", async (t) => {
    const ctx = await loadViewPage(t, {
      search: FOLDER_VIEW,
      before: (window) => {
        const set = window.setTimeout.bind(window);
        window.setTimeout = (fn, ms, ...rest) => set(fn, ms === 15000 ? 30 : ms, ...rest);
      },
    });
    ctx.hold.membership = true;
    ctx.state.moves.cache.clear();
    ctx.state.moves.dismissed.clear();
    await ctx.popTo("?view=all");
    await ctx.popTo(FOLDER_VIEW);
    await ctx.settle(200);
    assert.ok(banner(ctx).classList.contains("hidden"));
    assert.equal(ctx.state.moves.controller.signal.aborted, true, "the request was abandoned");
    assert.equal(ctx.state.library.status, "ready");
    assert.ok(ctx.real().length > 0);
  });

  test("leaving the view while the question is out: its answer is not shown in the next view", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW });
    ctx.hold.membership = true;
    await ctx.popTo("?view=all");
    await ctx.popTo(FOLDER_VIEW);
    await ctx.popTo("?view=keyword&value=Trips");
    ctx.server.first("/api/folder/membership", MEMBERSHIP);
    await ctx.release("membership");
    await ctx.settle(60);
    assert.ok(banner(ctx).classList.contains("hidden"));
  });

  test("Add them opens the add-folder question for the view's folder and indexes nothing until it is answered", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: MEMBERSHIP });
    ctx.document.getElementById("btn-moves-add").click();
    const modal = ctx.document.getElementById("add-folder-modal");
    assert.ok(modal.classList.contains("active"));
    assert.equal(ctx.document.getElementById("add-folder-title").textContent, "Add the rest of this folder to photo_index?");
    assert.equal(ctx.document.getElementById("add-folder-path").textContent, FOLDER);
    assert.equal(ctx.document.getElementById("btn-just-look").textContent, "Not now");
    assert.deepEqual(ctx.server.calls.filter((call) => call.url.includes("/api/folder/add")), [], "nothing is added by asking");
    // Not now: closes, adds nothing, and the offer is still on the page.
    ctx.document.getElementById("btn-just-look").click();
    assert.ok(!modal.classList.contains("active"));
    assert.deepEqual(ctx.server.calls.filter((call) => call.url.includes("/api/folder/add")), []);
    assert.ok(!banner(ctx).classList.contains("hidden"));
  });

  test("answering Add adds the view's folder, and only then: the banner goes, the strip says it is being indexed", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: MEMBERSHIP });
    ctx.server.on("/api/folder/add", { success: true });
    ctx.document.getElementById("btn-moves-add").click();
    ctx.document.getElementById("btn-add-folder").click();
    await ctx.settle(60);
    const adds = ctx.server.calls.filter((call) => call.url.includes("/api/folder/add"));
    assert.equal(adds.length, 1);
    assert.deepEqual(adds[0].body, { folder_path: FOLDER });
    assert.ok(banner(ctx).classList.contains("hidden"));
    assert.match(ctx.stripText(), /is added to photo_index and is being indexed\. Refresh view/);
    assert.equal(ctx.state.moves.addFor, null);
    assert.equal(ctx.state.scannedFolder, null, "no folder view was made of it");
  });

  test("the add button of a folder view's just-looking note is not the banner's: it adds the open folder", async (t) => {
    const ctx = await folderPage(t, { membership: MEMBERSHIP });
    ctx.server.on("/api/folder/add", { success: true });
    ctx.document.getElementById("btn-add-folder").click();
    await ctx.settle(60);
    const adds = ctx.server.calls.filter((call) => call.url.includes("/api/folder/add"));
    assert.deepEqual(adds.map((call) => call.body), [{ folder_path: FOLDER }]);
  });

  test("Dismiss hides it, and the same folder's view does not ask again until the page is left", async (t) => {
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: MEMBERSHIP });
    ctx.document.getElementById("btn-moves-dismiss").click();
    assert.ok(banner(ctx).classList.contains("hidden"));
    const asked = ctx.membershipAsked.length;
    await ctx.popTo("?view=all");
    await ctx.popTo(FOLDER_VIEW.replace("&recursive=1", ""));
    await ctx.popTo(FOLDER_VIEW);
    assert.equal(ctx.membershipAsked.length, asked, "not asked again");
    assert.ok(banner(ctx).classList.contains("hidden"));
  });

  test("Refresh view asks again: photos copied in since are said", async (t) => {
    let held = 0;
    const ctx = await loadViewPage(t, { search: FOLDER_VIEW, membership: () => ({ ...MEMBERSHIP, photos_not_held: held }) });
    assert.ok(banner(ctx).classList.contains("hidden"));
    held = 3;
    ctx.document.getElementById("btn-library-refresh").click();
    await ctx.settle(100);
    assert.equal(bannerText(ctx), "3 photos in this folder are not in photo_index.");
  });
});

describe("when the library was last in step: said only when something is wrong (#670)", () => {
  const stamp = (date) => {
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  };
  const ago = (hours) => stamp(new Date(Date.now() - hours * 3600 * 1000));
  const sync = (ctx) => ctx.document.getElementById("library-strip-sync");
  const inStep = { whole: false, in_step: true };

  test("in step 5 minutes ago: nothing is said, and the strip is one line of the view, its count and Refresh view", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", sync: { library: "photo_index", last_run: inStep, last_in_step: ago(5 / 60), syncing: false } });
    assert.equal(sync(ctx).textContent, "");
    assert.ok(sync(ctx).classList.contains("hidden"));
    assert.equal(ctx.syncAsked.length, 1, "asked once when the view opens");
    assert.match(ctx.syncAsked[0], /^\/photo_index\/api\/sync$/);
    assert.equal(ctx.stripText(), "The whole library 8 photos Refresh view");
  });

  test("never in step, and a sync under way, are said", async (t) => {
    const never = await loadViewPage(t, { search: "?view=all" });
    assert.equal(sync(never).textContent, "Never in step with its folders: no sync of the whole library has finished.");
    assert.ok(sync(never).classList.contains("library-strip-problem"));
    const running = await loadViewPage(t, { search: "?view=all", sync: { library: "photo_index", last_run: inStep, last_in_step: ago(3), syncing: true } });
    assert.equal(sync(running).textContent, "A sync is running now. Last in step with its folders: 3 h ago.");
  });

  test("the newest sync left it out of step: said, with when it was last in step", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", sync: { library: "photo_index", last_run: { whole: false, in_step: false }, last_in_step: ago(1), syncing: false } });
    assert.equal(sync(ctx).textContent, "The last sync found photos not in step with the library yet. Last in step with its folders: 1 h ago.");
    assert.equal(sync(ctx).title, ctx.state.syncInfo.lastInStep);
  });

  test("last in step more than two days ago (the daily catch-up has not left it in step): said", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", sync: { library: "photo_index", last_run: inStep, last_in_step: ago(72), syncing: false } });
    assert.equal(sync(ctx).textContent, "Last in step with its folders: 3 days ago.");
    const fresh = await loadViewPage(t, { search: "?view=all", sync: { library: "photo_index", last_run: inStep, last_in_step: ago(47), syncing: false } });
    assert.equal(sync(fresh).textContent, "");
  });

  test("the disk holds photos the library does not: the banner says how many, the strip when it was last in step", async (t) => {
    const FOLDER_VIEW = `?view=folder&value=${encodeURIComponent(FOLDER)}&recursive=1`;
    const ctx = await loadViewPage(t, {
      search: FOLDER_VIEW, membership: { folder: FOLDER, photos: 12, photos_held: 7, photos_not_held: 5, folders_not_held: 1 },
      sync: { library: "photo_index", last_run: inStep, last_in_step: ago(5 / 60), syncing: false },
    });
    assert.equal(ctx.document.getElementById("moves-banner-text").textContent, "5 photos in this folder are not in photo_index.");
    assert.equal(sync(ctx).textContent, "Last in step with its folders: 5 min ago.");
    ctx.document.getElementById("btn-moves-dismiss").click();
    assert.equal(sync(ctx).textContent, "", "dismissed: nothing wrong is left to say");
  });

  test("the request failing: the strip says it could not be read, and the view is untouched", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    ctx.server.first("/api/sync", { error: "boom" }, { status: 500 });
    ctx.document.getElementById("btn-library-refresh").click();
    await ctx.settle(100);
    assert.equal(sync(ctx).textContent, "Could not read when the library was last in step with its folders.");
    assert.equal(ctx.state.library.status, "ready");
    assert.ok(ctx.real().length > 0);
    pageErrors();
    const down = await loadViewPage(t, { search: "?view=all" });
    down.server.first("/api/sync", () => Promise.reject(new TypeError("Failed to fetch")));
    down.document.getElementById("btn-library-refresh").click();
    await down.settle(100);
    assert.match(sync(down).textContent, /Could not read/);
    pageErrors();
  });

  test("Refresh view asks again, and a slow older answer does not overwrite a newer", async (t) => {
    let calls = 0;
    const ctx = await loadViewPage(t, {
      search: "?view=all",
      sync: () => ({ library: "photo_index", last_run: { whole: false, in_step: false }, last_in_step: ago(++calls), syncing: false }),
    });
    assert.match(sync(ctx).textContent, /Last in step with its folders: 1 h ago\.$/);
    ctx.hold.sync = true;
    ctx.document.getElementById("btn-library-refresh").click();      // the second ask, held
    await ctx.settle(100);
    await ctx.popTo("?view=year&value=2020");                         // a view opens: the third, held
    assert.equal(ctx.syncAsked.length, 3);
    const [second, third] = ctx.held.filter((each) => each.key === "sync");
    ctx.held = [];
    third.release();
    await ctx.settle(40);
    assert.match(sync(ctx).textContent, /Last in step with its folders: 3 h ago\.$/);
    second.release();
    await ctx.settle(40);
    assert.match(sync(ctx).textContent, /Last in step with its folders: 3 h ago\.$/, "the older answer came late and was dropped");
  });

  test("a folder in Organize says nothing of it, and closing the view clears it", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.popTo("");
    assert.equal(sync(ctx).textContent, "");
    assert.ok(ctx.strip().classList.contains("hidden"));
    assert.deepEqual(ctx.syncAsked.length, 1, "a folder view never asks");
  });
});
