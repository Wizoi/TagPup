/**
 * V32: somebody adds a folder to the library and looks at the Library pane's Folders tab while it is indexed (plan
 * docs/plans/working-on-a-new-folder.md, cut F; DECISIONS 2026-11-26). The tab names the folder at once, under the folder
 * above it, marked queued with a count of 0; then indexing, its count filling; then its count alone, on the same row, and
 * nothing is asked any more. A cancel takes it off. The page follows the library's index queue in one place
 * (web/tagpup/suggestions.js heardIndexes, the 1 s ask of GET /api/folder/index-active): a change of what the queue holds
 * reads the tab again, and while a folder is being indexed so does every NAV_QUEUED_MS; the tab keeps no clock of its own
 * (tests/frontend/the-index-queue-has-one-follower.test.mjs). Around it: two folders added and one cancelled, a folder
 * queued elsewhere while the page was not asking, the page hidden for a minute, a library switch. A click on a marked
 * folder that holds no indexed photo yet opens it in Organize, read from the disk (owner, 2026-11-26); one that holds some
 * opens its view as any folder's. The tab's replies are
 * the server's own, tests/fixtures/queued_folders.json (tests/test_queued_folders_in_the_folders_tab.py holds the server to
 * them); every name is fictional.
 */
import { readFileSync } from "node:fs";
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, testClock } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const REPLIES = JSON.parse(readFileSync(new URL("../fixtures/queued_folders.json", import.meta.url), "utf-8"));
const PICTURES = "D:\\Pictures";
const RELAY_DAY = `${PICTURES}\\Season\\Relay Day`;
const HEATS = `${PICTURES}\\Season\\Meet 1\\Heats`;
const spelled = (path) => (path === null ? null : path.replace("{pictures}", PICTURES));
const reply = (name) => ({ folders: REPLIES[name].map((each) => ({ ...each, path: spelled(each.path), parent: spelled(each.parent) })) });
/** The queued folder cancelled: the tree as it was before it was added. */
const CANCELLED = () => ({ folders: reply("queued").folders.filter((each) => !each.queue).map((each) => ({ ...each })) });
/** Heats, under Meet 1, added beside Relay Day and waiting behind it. */
const TWO_QUEUED = () => ({ folders: [...reply("queued").folders, { path: HEATS, name: "Heats", parent: `${PICTURES}\\Season\\Meet 1`,
  direct: 0, recursive: 0, queue: "queued" }] });

/** What the library's index queue answers (tagpup/jobs/indexing.py IndexQueue.active), and each folder's status. */
const job = (folder) => ({ folder, name: folder.split("\\").pop() });
const IDLE = { active: [], queued: [], busy: false, remaining: 0 };
const waits = (...folders) => ({ active: [], queued: folders.map(job), busy: true, remaining: folders.length });
const INDEXING = { active: [{ ...job(RELAY_DAY), percent: 40, message: "Indexing r1.jpg" }], queued: [], busy: true, remaining: 1 };
const READY = { status: "completed", percent: 100, message: "Ready" };
const status = (how, message) => ({ status: how, percent: how === "completed" ? 100 : 0, message });

/** Each moment of the folder's way: the tab's answer, the queue's, and the folders' statuses. */
const MOMENTS = {
  before: { tab: CANCELLED, queue: () => IDLE, statuses: {} },
  queued: { tab: () => reply("queued"), queue: () => waits(RELAY_DAY), statuses: { [RELAY_DAY]: status("queued", "Waiting to be indexed...") } },
  indexing: { tab: () => reply("indexing"), queue: () => INDEXING, statuses: { [RELAY_DAY]: status("running", "Indexing r1.jpg") } },
  indexed: { tab: () => reply("indexed"), queue: () => IDLE, statuses: { [RELAY_DAY]: status("completed", "Folder indexed. 3 photos read, 3 new.") } },
  cancelled: { tab: CANCELLED, queue: () => IDLE, statuses: { [RELAY_DAY]: status("cancelled", "Cancelled.") } },
  two: { tab: TWO_QUEUED, queue: () => waits(RELAY_DAY, HEATS),
    statuses: { [RELAY_DAY]: status("queued", "Waiting..."), [HEATS]: status("queued", "Waiting...") } },
  heatsCancelled: { tab: () => reply("queued"), queue: () => waits(RELAY_DAY),
    statuses: { [RELAY_DAY]: status("queued", "Waiting..."), [HEATS]: status("cancelled", "Cancelled.") } },
};

const SEASON = "f:d:\\pictures\\season";
const RELAY = "f:d:\\pictures\\season\\relay day";
/** What a row says beside its name: its hint (the mark, in a filter its place too) and its count. */
const said = (row) => [row.querySelector(".nav-hint").textContent, row.querySelector(".nav-count").textContent];

/** The page over the moment `now()` names, at `search`. */
async function pageAt(t, now, search = "?view=all") {
  const at = () => MOMENTS[now()];
  const ctx = await loadViewPage(t, {
    search, navigator: { folders: () => at().tab() }, queue: () => at().queue(),
    indexStatus: (url) => at().statuses[new URL(url, "http://localhost").searchParams.get("path")] || READY,
  });
  const { NAV_QUEUED_MS } = ctx.module("suggestions.js");
  const { NAV_COUNTS_MS } = ctx.module("navigator.js");
  ctx.foldersAsked = () => ctx.navigatorAsked.filter((name) => name === "folders").length;
  ctx.queueAsked = () => ctx.server.urls().filter((url) => url.includes("/api/folder/index-active")).length;
  // The queue's next answer, then the tab's read it marks (the counts' pause, NAV_COUNTS_MS): within NAV_QUEUED_MS.
  ctx.nextRead = () => ctx.settle(NAV_QUEUED_MS);
  ctx.counted = () => ctx.settle(NAV_COUNTS_MS + 100);
  ctx.relay = () => ctx.row("folders", RELAY);
  ctx.said = said;
  ctx.shown = (shown) => {
    Object.defineProperty(ctx.document, "visibilityState", { configurable: true, get: () => (shown ? "visible" : "hidden") });
    ctx.document.dispatchEvent(new ctx.window.Event("visibilitychange"));
  };
  return ctx;
}

/** The page on a view of the library, its Folders tab answering the moment `now()` names, Season opened. */
async function openTab(t, now) {
  const ctx = await pageAt(t, now);
  click(ctx.window, ctx.row("folders", SEASON).querySelector(".nav-twisty"));
  await ctx.settle(20);
  return ctx;
}

describe("a folder just added, in the Folders tab", () => {
  test("queued at once with 0, then indexing with its count filling, then its count alone on the same row; then no more asks", async (t) => {
    let now = "queued";
    const ctx = await openTab(t, () => now);
    const relay = ctx.relay();
    assert.ok(relay, "the folder added is in the tab before it holds a photo");
    assert.deepEqual(ctx.said(relay), ["queued", "0"]);
    assert.equal(relay.getAttribute("aria-level"), "2", "under the folder above it");
    assert.match(relay.getAttribute("aria-label"), /waiting to be indexed/);
    now = "indexing";
    await ctx.nextRead();
    assert.equal(ctx.relay(), relay, "the same element: read again quietly");
    assert.deepEqual(ctx.said(relay), ["indexing", "1"], "its count fills as it is indexed");
    const filling = ctx.foldersAsked();
    await ctx.nextRead();
    await ctx.nextRead();
    assert.ok(ctx.foldersAsked() >= filling + 1, "the tab is not read again while the folder is indexed: its count does not fill");
    assert.ok(ctx.foldersAsked() <= filling + 2, `the tab was read ${ctx.foldersAsked() - filling} times in two NAV_QUEUED_MS`);
    now = "indexed";
    await ctx.nextRead();
    assert.deepEqual(ctx.said(relay), ["", "3"], "the mark goes, the real count shows");
    assert.ok(relay.querySelector(".nav-hint").classList.contains("hidden"));
    assert.equal(ctx.rows("folders").filter((row) => row.querySelector(".nav-label").textContent === "Relay Day").length, 1,
      "one row, no duplicate");
    const asked = ctx.foldersAsked();
    const queueAsked = ctx.queueAsked();
    await ctx.nextRead();
    await ctx.nextRead();
    assert.equal(ctx.foldersAsked(), asked, "nothing marked: the tab is not asked again");
    assert.equal(ctx.queueAsked(), queueAsked, "nothing queued: the queue is not asked again");
  });

  test("a cancelled folder leaves the tab at the next read, and the indexed folders stay", async (t) => {
    let now = "queued";
    const ctx = await openTab(t, () => now);
    assert.ok(ctx.relay());
    now = "cancelled";
    await ctx.nextRead();
    assert.equal(ctx.relay(), null, "the cancelled folder is gone");
    assert.ok(ctx.row("folders", `${SEASON}\\meet 1`), "the indexed folder stays");
    const asked = ctx.foldersAsked();
    await ctx.nextRead();
    assert.equal(ctx.foldersAsked(), asked);
  });

  test("two folders added, then one cancelled: it leaves the tab, the other stays queued and followed", async (t) => {
    let now = "two";
    const ctx = await openTab(t, () => now);
    click(ctx.window, ctx.row("folders", `${SEASON}\\meet 1`).querySelector(".nav-twisty"));
    await ctx.settle(20);
    assert.deepEqual(ctx.said(ctx.row("folders", `${SEASON}\\meet 1\\heats`)), ["queued", "0"]);
    now = "heatsCancelled";
    await ctx.nextRead();
    assert.equal(ctx.row("folders", `${SEASON}\\meet 1\\heats`), null, "the cancelled folder is still in the tab");
    assert.deepEqual(ctx.said(ctx.relay()), ["queued", "0"], "the other folder lost its mark");
    const asked = ctx.queueAsked();
    await ctx.nextRead();
    assert.ok(ctx.queueAsked() > asked, "the folder still queued is no longer followed");
    now = "indexing";
    await ctx.nextRead();
    assert.deepEqual(ctx.said(ctx.relay()), ["indexing", "1"]);
  });

  test("while another tab is shown the Folders tab is not read; opening it again reads it", async (t) => {
    let now = "queued";
    const ctx = await openTab(t, () => now);
    await ctx.openTab("keywords");
    const asked = ctx.foldersAsked();
    now = "indexing";
    await ctx.nextRead();
    await ctx.nextRead();
    assert.equal(ctx.foldersAsked(), asked, "not on screen: not read");
    await ctx.openTab("folders");
    assert.equal(ctx.foldersAsked(), asked + 1, "read as it is shown");
    assert.deepEqual(ctx.said(ctx.relay()), ["indexing", "1"]);
  });

  test("a folder queued elsewhere while the page was not asking: the tab names it, and from there the page follows it to its end", async (t) => {
    let now = "before";
    const ctx = await pageAt(t, () => now, "");
    const idle = ctx.queueAsked();
    now = "queued";   // TagTuner, or the watcher's sync, queues it
    await ctx.nextRead();
    assert.equal(ctx.queueAsked(), idle, "fixture: the page asked the queue with nothing to follow");
    await ctx.showLibraryPane();
    assert.deepEqual(ctx.said(ctx.rowByLabel("folders", "Season")), ["", "2"], "fixture: the tab was not read");
    assert.ok(ctx.queueAsked() > idle, "the folder the tab names as queued is not followed");
    now = "indexing";
    await ctx.nextRead();
    now = "indexed";
    await ctx.nextRead();
    await ctx.counted();
    assert.deepEqual(ctx.said(ctx.rowByLabel("folders", "Season")), ["", "5"], "its end was not read into the tab");
  });

  test("the page hidden for a minute asks nothing; shown again it asks at once and shows what changed meanwhile", async (t) => {
    let now = "indexing";
    const ctx = await openTab(t, () => now);
    assert.deepEqual(ctx.said(ctx.relay()), ["indexing", "1"]);
    ctx.shown(false);
    await ctx.settle(2500);   // an ask out as it was hidden, and a read it marked, land
    const asked = ctx.queueAsked();
    const read = ctx.foldersAsked();
    now = "indexed";   // the index ends while nobody looks
    await ctx.settle(60000);
    assert.equal(ctx.queueAsked(), asked, "the queue was asked while the page was hidden");
    assert.equal(ctx.foldersAsked(), read, "the Folders tab was read while the page was hidden");
    ctx.shown(true);
    await ctx.settle(20);
    assert.equal(ctx.queueAsked(), asked + 1, "shown again, the queue is not asked at once");
    await ctx.counted();
    assert.deepEqual(ctx.said(ctx.relay()), ["", "3"], "the end seen as the page is shown is not read into the tab");
  });

  test("a library switch: the library left's queue reads the tab no more", async (t) => {
    const now = "indexing";
    const ctx = await openTab(t, () => now);
    ctx.window.history.pushState({}, "", "/other/");
    await ctx.counted();   // a read already marked lands
    const read = ctx.foldersAsked();
    const asked = ctx.queueAsked();
    await ctx.settle(15000);
    assert.equal(ctx.foldersAsked(), read, "the Folders tab of the library left is read on");
    assert.ok(ctx.queueAsked() <= asked + 1, "the queue of the library left is followed on");
  });

  test("the filter finds a queued folder, its mark beside its place", async (t) => {
    const ctx = await openTab(t, () => "queued");
    const input = ctx.document.querySelector("#nav-panel-folders .nav-filter");
    input.value = "relay";
    input.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    assert.deepEqual(ctx.rows("folders").map((row) => ctx.said(row)), [[`queued \u00b7 ${PICTURES}\\Season\\Relay Day`, "0"]]);
  });

  test("a click on a queued folder with no photo indexed yet opens it in Organize, read from the disk, not its empty view", async (t) => {
    const ctx = await openTab(t, () => "queued");
    const views = ctx.idsAsked.length;
    click(ctx.window, ctx.relay());
    await ctx.settle();
    assert.equal(ctx.idsAsked.length, views, "its library view, which holds nothing yet, was opened");
    assert.equal(ctx.state.library, null, "the view open was not closed onto the folder");
    assert.equal(ctx.state.scannedFolder, RELAY_DAY, "the folder was not opened in Organize");
    assert.ok(ctx.server.urls().some((url) => url.includes("/api/folder/scan") && url.includes(encodeURIComponent(RELAY_DAY))),
      "the folder was not read from the disk");
  });

  test("from Organize, with no view open, a click on it opens it there too", async (t) => {
    const ctx = await pageAt(t, () => "queued", "");
    await ctx.showLibraryPane();
    click(ctx.window, ctx.rowByLabel("folders", "Season").querySelector(".nav-twisty"));
    await ctx.settle(20);
    click(ctx.window, ctx.relay());
    await ctx.settle();
    assert.equal(ctx.idsAsked.length, 0, "a library view was opened");
    assert.equal(ctx.state.scannedFolder, RELAY_DAY);
  });

  test("a folder being indexed that holds photos already opens its view as any folder's", async (t) => {
    const ctx = await openTab(t, () => "indexing");
    click(ctx.window, ctx.relay());
    await ctx.settle();
    assert.match(ctx.idsAsked.at(-1), /kind=folder&folder=D%3A%5CPictures%5CSeason%5CRelay%20Day&recursive=1$/);
    assert.equal(ctx.relay().getAttribute("aria-selected"), "true");
  });

  test("a top being indexed is its own row, not folded into its one subfolder", async (t) => {
    const top = () => ({ folders: reply("indexing").folders.map((each) => (each.path === PICTURES ? { ...each, queue: "indexing" } : each)) });
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: { folders: top } });
    assert.deepEqual(ctx.rows("folders").map((row) => row.querySelector(".nav-label").textContent), ["Pictures"]);
    assert.deepEqual(said(ctx.rows("folders")[0]), ["indexing", "3"]);
  });
});
