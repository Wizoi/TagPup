/**
 * V32: somebody adds a folder and works on it in Organize at once (plan docs/plans/working-on-a-new-folder.md, cut A;
 * findings #3407, #3408). They add it from the folder (the Add question), run Suggest before its index ends, open a view
 * of the library and come back, and the index ends. The page follows the library's queue (GET /api/folder/index-active)
 * whoever queued the folder, says "Indexing N of M folders", keeps following in a view and after it, does not show the
 * list it kept from before the index, and when the index ends reads again what it wrote -- the Library pane's folders, the
 * open folder, the open view -- and keeps the index's closing sentence on the status line. Around it: rapid folder
 * switches, a reply for the library left, two folders queued with the open one second, a queue that does not answer, an
 * index cancelled, and no asking once nothing is queued.
 *
 * The queue is the server's as tagpup/jobs/indexing.py IndexQueue gives it: `active` and `queued` from active(), each
 * folder's status from status() (READY for one nobody asked about). Folder names are invented.
 */
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { advance, click, flush, FakeServer, loadApp, openFolder, pageExports, photoRecord, testClock } from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const ROOT = "D:\\Pictures\\Meet 2026";
const HEAT_1 = `${ROOT}\\Heat 1`;
const HEAT_2 = `${ROOT}\\Heat 2`;
const REGATTA = "D:\\Pictures\\Regatta";
const HARBOUR = "D:\\Pictures\\Harbour";
const READY = { status: "completed", percent: 100, message: "Ready" };
const CLOSING = "Folder indexed. 12 photos read, 12 new. Baked 12 photos. 3 new faces: Re-examine to name them.";

const nameOf = (folder) => folder.split("\\").pop();
const pathIn = (url) => new URL(url, "http://localhost").searchParams.get("path");

/** The library's index queue, which a test moves on: one folder indexing, the rest waiting, each one's status. */
function queueOf(held = [REGATTA, HARBOUR]) {
  const q = { running: null, waiting: [], statuses: {}, held: new Set(held) };
  const status = (folder, values) => { q.statuses[folder] = { percent: 0, folder, ...values }; };
  q.add = (folder) => {
    q.held.add(folder);
    if (!q.running) {
      q.running = { folder, percent: 0, message: "Starting indexing..." };
      status(folder, { status: "running", message: q.running.message });
    } else {
      q.waiting.push(folder);
      status(folder, { status: "queued", message: "Waiting to be indexed..." });
    }
  };
  q.progress = (percent, message) => {
    Object.assign(q.running, { percent, message });
    status(q.running.folder, { status: "running", percent, message });
  };
  q.finish = (how = "completed", message = CLOSING) => {
    status(q.running.folder, { status: how, percent: 100, message });
    q.running = null;
    const next = q.waiting.shift();
    if (next) {
      q.running = { folder: next, percent: 0, message: "Starting indexing..." };
      status(next, { status: "running", message: q.running.message });
    }
  };
  q.cancel = (folder) => {
    q.waiting = q.waiting.filter((each) => each !== folder);
    status(folder, { status: "cancelled", message: "Cancelled." });
  };
  q.active = () => ({
    active: q.running ? [{ folder: q.running.folder, name: nameOf(q.running.folder), percent: q.running.percent,
      message: q.running.message }] : [],
    queued: q.waiting.map((folder) => ({ folder, name: nameOf(folder) })),
    busy: Boolean(q.running || q.waiting.length), remaining: (q.running ? 1 : 0) + q.waiting.length,
  });
  return q;
}

const NOT_HELD = (folder) => ({ library: "photo_index", folder, photos: 2, photos_held: 0, photos_not_held: 2,
  folders_not_held: 1, first_not_held: folder, has_roots: true, under_roots: true, ignored: false });
const HELD = (folder) => ({ ...NOT_HELD(folder), photos_held: 2, photos_not_held: 0, folders_not_held: 0, first_not_held: null });

/** The page on photo_index over `q`; `activeReply` answers index-active instead of the queue when given. */
async function page(t, q, { activeReply, suggest = () => ({ status: "idle" }), kept = {} } = {}) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index", "other"], selected: "photo_index" })
    .on("/api/folder/index-active", activeReply || (() => q.active()))
    .on("/api/folder/index-status", (url) => q.statuses[pathIn(url)] || READY)
    .on("/api/folder/membership", (url) => (q.held.has(pathIn(url)) ? HELD(pathIn(url)) : NOT_HELD(pathIn(url))))
    .on("/api/folder/add", (url, body) => {
      q.add(body.folder_path);
      return { success: true, status: "running", library: "photo_index", folder: body.folder_path, added: 2,
        queued: [body.folder_path], already_queued: [], invalid: [], pending: q.waiting.length + 1 };
    })
    .on("/api/folder/suggest-start", { success: true, status: "running" })
    .on("/api/folder/suggest-status", (url) => suggest(pathIn(url)))
    .on("/api/folder/scan", (url) => {
      const folder = pathIn(url);
      return ["IMG_0001.jpg", "IMG_0002.jpg"].map((filename) => photoRecord({ path: `${folder}\\${filename}`, filename }));
    })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/library/navigator?section=folders", { folders: [] })
    .on("/api/library/navigator", {})
    .on("/api/library/ids", { source: {}, total: 2, ids: [101, 102], videos: [], complete: true })
    .on("/api/library/cards", (url) => ({
      cards: decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").filter(Boolean).map(Number)
        .map((id) => ({ id, name: `IMG_${id}.jpg`, path: `${HARBOUR}\\IMG_${id}.jpg`, taken: "2026:05:01 10:00:00" })),
    }));
  const ctx = await loadApp("tagpup", {
    t, url: "http://localhost:8090/photo_index/", server,
    before: (window) => { for (const [key, value] of Object.entries(kept)) window.localStorage.setItem(key, value); },
  });
  ctx.alerts = [];
  ctx.window.alert = (message) => ctx.alerts.push(message);
  ctx.window.confirm = () => true;
  const $ = (id) => ctx.document.getElementById(id);
  Object.assign(ctx, {
    $,
    q,
    state: pageExports(ctx.window, "web/tagpup/state.js").state,
    bar: () => !$("index-progress-container").classList.contains("hidden"),
    barText: () => $("index-progress-text").textContent,
    status: () => $("status-text").textContent,
    asks: () => server.urls().filter((u) => u.includes("/api/folder/index-active")),
    statusAsks: () => server.urls().filter((u) => u.includes("/api/folder/index-status")).map(pathIn),
    scans: (folder, forced) => server.urls().filter((u) => u.includes("/api/folder/scan") && pathIn(u) === folder
      && (forced === undefined || u.includes(`force=${forced}`))).length,
    folderReads: () => server.urls().filter((u) => u.includes("/api/library/navigator?section=folders")).length,
    tick: async (ms = 1000) => {
      await advance(ctx.window, ms);
      await flush(ctx.window, 6);
    },
  });
  return ctx;
}

/** Open `folder` and answer the Add question with Add, as somebody adding a folder from Organize does. */
async function addFromOrganize(ctx, folder) {
  await openFolder(ctx, folder);
  assert.ok(ctx.$("add-folder-modal").classList.contains("active"), "fixture: the Add question was not asked");
  click(ctx.window, ctx.$("btn-add-folder"));
  await flush(ctx.window, 8);
}

describe("V32: a folder added, worked on in Organize, a view and back, and its index ends", () => {
  test("the page follows the index throughout, reads again what it wrote, and keeps its closing sentence", async (t) => {
    let suggesting = false;
    const q = queueOf();
    const ctx = await page(t, q, {
      suggest: (folder) => (folder === HEAT_1 && suggesting
        ? { status: "preparing", completed: 0, total: 0, message: "Waiting for this folder to be indexed first" }
        : { status: "idle" }),
    });
    await addFromOrganize(ctx, HEAT_1);
    q.progress(30, "Generating embeddings: 30% (3/10)");
    await ctx.tick();
    assert.ok(ctx.bar(), "the index's bar is not shown");
    assert.match(ctx.barText(), /^Indexing 1 of 1 folder \(Heat 1\): Generating embeddings: 30%/);

    // Suggest before the index ends: it waits, its bar carrying the index's progress (one bar).
    suggesting = true;
    click(ctx.window, ctx.$("btn-suggest-tags"));
    await ctx.tick(1600);
    assert.ok(!ctx.$("suggest-progress-container").classList.contains("hidden"));
    assert.equal(ctx.$("suggest-progress-bar").style.width, "30%");
    assert.ok(!ctx.bar(), "two bars showed");
    suggesting = false;

    // A view of the library: the Library pane reads its folders once; the index is followed on, in the view.
    pageExports(ctx.window, "web/tagpup/library-view.js").browseWholeLibrary();
    await ctx.tick(200);
    assert.ok(ctx.state.library, "fixture: no view opened");
    assert.equal(ctx.folderReads(), 1, "fixture: the Library pane did not read its folders");
    q.progress(60, "Generating embeddings: 60% (6/10)");
    const inView = ctx.asks().length;
    await ctx.tick();
    assert.ok(ctx.asks().length > inView, "the index is no longer followed in a library view");
    assert.match(ctx.barText(), /60%/);

    // Back to the folder: the list kept from before the index is not shown, it is read again; the index is followed.
    const scansBefore = ctx.scans(HEAT_1);
    ctx.window.history.back();
    await ctx.tick(300);
    assert.equal(ctx.state.library, null, "fixture: Back did not close the view");
    assert.ok(ctx.scans(HEAT_1) > scansBefore, "the folder's kept list from before its index was shown");
    assert.doesNotMatch(ctx.$("list-stats").textContent, /cached/);
    // Before the end, the Library pane's folders are as read: choosing it asks nothing.
    click(ctx.window, ctx.$("sidebar-tab-library"));
    await ctx.tick(200);
    assert.equal(ctx.folderReads(), 1, "fixture: the folders were read again before the index ended");
    click(ctx.window, ctx.$("sidebar-tab-folder"));
    await ctx.tick(200);

    // The index ends.
    const forced = ctx.scans(HEAT_1, true);
    q.finish();
    await ctx.tick();
    await ctx.tick(200);
    assert.equal(ctx.scans(HEAT_1, true), forced + 1, "the open folder was not read again when its index ended");
    assert.equal(ctx.status(), `Heat 1: ${CLOSING}`, "the index's closing sentence is not on the status line");
    assert.ok(!ctx.bar(), "the bar stayed up");
    assert.deepEqual(ctx.alerts, []);
    // The Library pane's folders are read again when it is shown: the new folder appears there (#3407).
    click(ctx.window, ctx.$("sidebar-tab-library"));
    await ctx.tick(200);
    assert.equal(ctx.folderReads(), 2, "the Library pane's folders were not read again after the index");

    // Nothing is queued: no more asks.
    const settled = ctx.asks().length;
    await ctx.tick(5000);
    assert.equal(ctx.asks().length, settled, "the page asks the queue on with nothing queued");
  });

  test("the index ends in a library view: the view is read again", async (t) => {
    const q = queueOf();
    const ctx = await page(t, q);
    await addFromOrganize(ctx, HEAT_1);
    pageExports(ctx.window, "web/tagpup/library-view.js").browseWholeLibrary();
    await ctx.tick(200);
    const ids = () => ctx.server.urls().filter((u) => u.includes("/api/library/ids")).length;
    const before = ids();
    q.finish();
    await ctx.tick();
    await ctx.tick(200);
    assert.equal(ids(), before + 1, "the view was not read again when the index ended");
    assert.equal(ctx.status(), `Heat 1: ${CLOSING}`);
  });
});

describe("V32: around it", () => {
  test("a sync's batch ends all at once: the view is read again once, not once per folder (#3410)", async (t) => {
    // index-active lists each folder of a `together` job as its own running entry; they leave the list in one tick.
    const q = queueOf([REGATTA, ROOT, HEAT_1, HEAT_2]);
    let batch = [HEAT_1, HEAT_2];
    q.active = () => ({
      active: batch.map((folder) => ({ folder, name: nameOf(folder), percent: 50, message: "Reading metadata: 50%" })),
      queued: [], busy: batch.length > 0, remaining: batch.length,
    });
    for (const folder of batch) q.statuses[folder] = { status: "running", percent: 50, folder, message: "Reading metadata" };
    const ctx = await page(t, q);
    await openFolder(ctx, ROOT);
    await ctx.tick(200);
    assert.match(ctx.barText(), /^Indexing 1 of 2 folders/);
    const forced = ctx.scans(ROOT, true);
    const folderReads = ctx.folderReads();
    batch = [];
    for (const folder of [HEAT_1, HEAT_2]) {
      q.statuses[folder] = { status: "completed", percent: 100, folder, message: "Folder indexed. 5 photos read, 5 new." };
    }
    await ctx.tick();
    await ctx.tick(200);
    assert.equal(ctx.scans(ROOT, true), forced + 1, "the open folder was read again once per folder of the batch");
    click(ctx.window, ctx.$("sidebar-tab-library"));
    await ctx.tick(200);
    assert.equal(ctx.folderReads(), folderReads + 1, "the Library pane was read again once per folder of the batch");
    assert.equal(ctx.status(), "Heat 1: Folder indexed. 5 photos read, 5 new. Heat 2: Folder indexed. 5 photos read, 5 new.");
  });

  test("a list kept before an index that ended is not shown after a reload (#3411)", async (t) => {
    const q = queueOf();
    const first = await page(t, q);
    await addFromOrganize(first, HEAT_1);
    q.progress(50, "Reading metadata: 50%");
    await openFolder(first, HARBOUR);   // another folder is open when the index ends: the kept list of Heat 1 is not read again
    await first.tick();
    const browser = () => Object.fromEntries(Object.keys(first.window.localStorage)
      .map((key) => [key, first.window.localStorage.getItem(key)]));
    assert.ok(Object.keys(browser()).some((key) => key.startsWith("tagpup_cache_")), "fixture: no scan was kept");
    q.finish();
    await first.tick();
    await first.tick(200);
    // F5: the page is loaded again over the same browser storage, the queue idle, and the folder opened within 30 minutes.
    const reloaded = await page(t, queueOf(), { kept: browser() });
    await openFolder(reloaded, HEAT_1);
    await reloaded.tick(200);
    assert.equal(reloaded.scans(HEAT_1), 1, "the list kept from before the index was shown");
    assert.doesNotMatch(reloaded.$("list-stats").textContent, /cached/);
  });

  test("rapid folder switches while an index runs: one ask out at a time, and only a folder it holds is read again", async (t) => {
    const q = queueOf([REGATTA, HARBOUR, HEAT_1]);
    q.add(HEAT_1);   // queued elsewhere (TagTuner, a watcher's sync): the page learns of it from the queue
    let out = 0;
    let most = 0;
    const ctx = await page(t, q, {
      activeReply: () => {
        out += 1;
        most = Math.max(most, out);
        return new Promise((resolve) => setTimeout(() => {
          out -= 1;
          resolve(q.active());
        }, 40));
      },
    });
    await ctx.tick(100);
    assert.ok(ctx.bar(), "an index the page did not start is not shown");

    for (const folder of [REGATTA, HARBOUR, HEAT_1, REGATTA, HARBOUR]) {
      const input = ctx.$("folder-path-input");
      input.value = folder;
      input.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
      await flush(ctx.window, 1);
    }
    await ctx.tick(200);
    assert.equal(most, 1, "two asks of the queue were out at once");
    assert.equal(ctx.state.scannedFolder, HARBOUR);

    // It ends with Harbour open: Harbour holds none of its photos, so it is not read again; the sentence is said.
    const forced = ctx.scans(HARBOUR, true);
    q.finish();
    await ctx.tick();
    await ctx.tick(200);
    assert.equal(ctx.scans(HARBOUR, true), forced, "a folder the index did not touch was read again");
    assert.equal(ctx.status(), `Heat 1: ${CLOSING}`);

    // Heat 1 opened again: the list this browser kept from before its index is not shown.
    const scans = ctx.scans(HEAT_1);
    await openFolder(ctx, HEAT_1);
    await ctx.tick(200);
    assert.equal(ctx.scans(HEAT_1), scans + 1, "the kept list from before the index was shown");
  });

  test("a scan out when its folder's index ends reads the folder again as it lands", async (t) => {
    const q = queueOf([REGATTA, HEAT_1]);
    q.add(HEAT_1);
    const ctx = await page(t, q);
    let release = null;
    ctx.server.first("/api/folder/scan", (url) => new Promise((resolve) => {
      const folder = pathIn(url);
      const reply = ["IMG_0001.jpg"].map((filename) => photoRecord({ path: `${folder}\\${filename}`, filename }));
      if (release) resolve(reply);
      else release = () => resolve(reply);
    }));
    await openFolder(ctx, HEAT_1, { settle: 2 });
    q.finish();
    await ctx.tick();
    assert.equal(ctx.scans(HEAT_1), 1, "fixture: the first scan is still out");
    release();
    await ctx.tick(200);
    assert.equal(ctx.scans(HEAT_1), 2, "the scan taken before the end was shown");
    assert.equal(ctx.state.scannedFolder, HEAT_1);
    assert.equal(ctx.status(), `Heat 1: ${CLOSING}`);
  });

  test("switching library mid-index: the answer of the library left is dropped", async (t) => {
    const q = queueOf([REGATTA, HEAT_1]);
    q.add(HEAT_1);
    let release = null;
    const ctx = await page(t, q, {
      activeReply: () => new Promise((resolve) => { release = () => resolve(q.active()); }),
    });
    assert.ok(release, "fixture: the queue was not asked at load");
    ctx.window.history.pushState({}, "", "/other/");
    release();
    await ctx.tick(3000);
    assert.ok(!ctx.bar(), "the queue of the library left was shown");
    assert.deepEqual(ctx.statusAsks(), [], "the library left's folders were followed");
    assert.deepEqual(ctx.asks().filter((u) => u.startsWith("/photo_index/")).length, 1, "the library left was asked again");
  });

  test("two folders queued, the open one second in line: each end is its own, the open one read again at its own", async (t) => {
    const q = queueOf([REGATTA, HEAT_1, HEAT_2]);
    q.add(HEAT_2);
    q.add(HEAT_1);
    const ctx = await page(t, q);
    await openFolder(ctx, HEAT_1);
    q.progress(40, "Reading metadata: 40%");
    await ctx.tick();
    assert.match(ctx.barText(), /^Indexing 1 of 2 folders \(Heat 2\): Reading metadata: 40%/);
    assert.equal(ctx.state.indexPercent, 0, "the open folder waits: its index has not started");

    const forced = ctx.scans(HEAT_1, true);
    q.finish("completed", "Folder indexed. 5 photos read, 5 new.");
    await ctx.tick();
    await ctx.tick(200);
    assert.equal(ctx.scans(HEAT_1, true), forced, "the open folder was read again at the other folder's end");
    assert.equal(ctx.status(), "Heat 2: Folder indexed. 5 photos read, 5 new.");
    assert.match(ctx.barText(), /^Indexing 2 of 2 folders \(Heat 1\)/);

    q.progress(50, "Generating embeddings: 50%");
    await ctx.tick();
    assert.equal(ctx.state.indexPercent, 50);
    q.finish();
    await ctx.tick();
    await ctx.tick(200);
    assert.equal(ctx.scans(HEAT_1, true), forced + 1, "the open folder was not read again at its own end");
    assert.equal(ctx.status(), `Heat 1: ${CLOSING}`);
    assert.ok(!ctx.bar());
    const settled = ctx.asks().length;
    await ctx.tick(5000);
    assert.equal(ctx.asks().length, settled, "the page asks the queue on with nothing queued");
  });

  test("an index cancelled: nothing is read again, it says so, and the other is followed to its end", async (t) => {
    const q = queueOf([REGATTA, HEAT_1, HEAT_2]);
    q.add(HEAT_2);
    q.add(HEAT_1);
    const ctx = await page(t, q);
    await openFolder(ctx, HEAT_1);
    const forced = ctx.scans(HEAT_1, true);
    q.cancel(HEAT_1);
    await ctx.tick();
    await ctx.tick(200);
    assert.equal(ctx.scans(HEAT_1, true), forced, "a cancelled index wrote nothing, and the folder was read again");
    assert.equal(ctx.status(), "Heat 1: Cancelled.");
    assert.match(ctx.barText(), /\(Heat 2\)/);
    q.finish();
    await ctx.tick();
    await ctx.tick(200);
    assert.ok(!ctx.bar());
    const settled = ctx.asks().length;
    await ctx.tick(5000);
    assert.equal(ctx.asks().length, settled);
  });

  test("the queue not answering: the page works on, says nothing wrong, and stops asking", async (t) => {
    const q = queueOf();
    const ctx = await page(t, q);
    ctx.server.first("/api/folder/index-active", { error: "Internal Server Error" }, { status: 500 });
    await addFromOrganize(ctx, HEAT_1);
    assert.match(ctx.status(), /Added to photo_index; indexing it now/);
    await ctx.tick(10000);
    const settled = ctx.asks().length;
    assert.ok(settled > 1 && settled < 10, `the queue was asked ${settled} times`);
    await ctx.tick(10000);
    assert.equal(ctx.asks().length, settled, "the page asks a queue that does not answer for ever");
    assert.ok(!ctx.bar(), "a bar claims an index it cannot see");
    assert.doesNotMatch(ctx.status(), /error|fail|could not/i, "the status line says something went wrong");
    assert.ok(!ctx.$("status-dot").className.includes("error"), "the status line says something went wrong");
    assert.deepEqual(ctx.alerts, []);
    assert.equal(ctx.scans(HEAT_1), 1);
    assert.equal(ctx.state.scannedFolder, HEAT_1, "the folder is still open");
  });
});
