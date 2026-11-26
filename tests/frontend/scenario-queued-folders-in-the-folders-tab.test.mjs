/**
 * V32: somebody adds a folder to the library and looks at the Library pane's Folders tab while it is indexed (plan
 * docs/plans/working-on-a-new-folder.md, cut F; DECISIONS 2026-11-26). The tab names the folder at once, under the folder
 * above it, marked queued with a count of 0; then indexing, its count filling as the tab is read again (navigator.js
 * NAV_QUEUED_MS, while a folder is marked and the tab is on screen); then its count alone, on the same row, and the tab is
 * asked no more. A cancel takes it off at the next read. The replies are the server's own, tests/fixtures/queued_folders.json
 * (tests/test_queued_folders_in_the_folders_tab.py holds the server to them); every name is fictional.
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
const spelled = (path) => (path === null ? null : path.replace("{pictures}", PICTURES));
const reply = (name) => ({ folders: REPLIES[name].map((each) => ({ ...each, path: spelled(each.path), parent: spelled(each.parent) })) });
/** The queued folder cancelled: the tree as it was before it was added. */
const CANCELLED = () => ({ folders: reply("queued").folders.filter((each) => !each.queue).map((each) => ({ ...each })) });

const SEASON = "f:d:\\pictures\\season";
const RELAY = "f:d:\\pictures\\season\\relay day";
/** What a row says beside its name: its hint (the mark, in a filter its place too) and its count. */
const said = (row) => [row.querySelector(".nav-hint").textContent, row.querySelector(".nav-count").textContent];

/** The page on a view of the library, its Folders tab answering `now()`, Season opened. */
async function openTab(t, now) {
  const ctx = await loadViewPage(t, { search: "?view=all", navigator: { folders: () => now() } });
  click(ctx.window, ctx.row("folders", SEASON).querySelector(".nav-twisty"));
  await ctx.settle(20);
  const waits = ctx.module("navigator.js").NAV_QUEUED_MS;
  ctx.foldersAsked = () => ctx.navigatorAsked.filter((name) => name === "folders").length;
  ctx.nextRead = () => ctx.settle(waits);
  ctx.relay = () => ctx.row("folders", RELAY);
  ctx.said = said;
  return ctx;
}

describe("a folder just added, in the Folders tab", () => {
  test("queued at once with 0, then indexing with its count filling, then its count alone on the same row; then no more asks", async (t) => {
    let now = "queued";
    const ctx = await openTab(t, () => reply(now));
    const relay = ctx.relay();
    assert.ok(relay, "the folder added is in the tab before it holds a photo");
    assert.deepEqual(ctx.said(relay), ["queued", "0"]);
    assert.equal(relay.getAttribute("aria-level"), "2", "under the folder above it");
    assert.match(relay.getAttribute("aria-label"), /waiting to be indexed/);
    now = "indexing";
    await ctx.nextRead();
    assert.equal(ctx.relay(), relay, "the same element: read again quietly");
    assert.deepEqual(ctx.said(relay), ["indexing", "1"], "its count fills as it is indexed");
    now = "indexed";
    await ctx.nextRead();
    assert.deepEqual(ctx.said(relay), ["", "3"], "the mark goes, the real count shows");
    assert.ok(relay.querySelector(".nav-hint").classList.contains("hidden"));
    assert.equal(ctx.rows("folders").filter((row) => row.querySelector(".nav-label").textContent === "Relay Day").length, 1,
      "one row, no duplicate");
    const asked = ctx.foldersAsked();
    await ctx.nextRead();
    await ctx.nextRead();
    assert.equal(ctx.foldersAsked(), asked, "nothing marked: the tab is not asked again");
  });

  test("a cancelled folder leaves the tab at the next read, and the indexed folders stay", async (t) => {
    let answer = () => reply("queued");
    const ctx = await openTab(t, () => answer());
    assert.ok(ctx.relay());
    answer = CANCELLED;
    await ctx.nextRead();
    assert.equal(ctx.relay(), null, "the cancelled folder is gone");
    assert.ok(ctx.row("folders", `${SEASON}\\meet 1`), "the indexed folder stays");
    const asked = ctx.foldersAsked();
    await ctx.nextRead();
    assert.equal(ctx.foldersAsked(), asked);
  });

  test("while another tab is shown the Folders tab is not read; opening it again reads it", async (t) => {
    let now = "queued";
    const ctx = await openTab(t, () => reply(now));
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

  test("the filter finds a queued folder, its mark beside its place", async (t) => {
    const ctx = await openTab(t, () => reply("queued"));
    const input = ctx.document.querySelector("#nav-panel-folders .nav-filter");
    input.value = "relay";
    input.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    assert.deepEqual(ctx.rows("folders").map((row) => ctx.said(row)), [[`queued \u00b7 ${PICTURES}\\Season\\Relay Day`, "0"]]);
  });

  test("a click on a queued folder opens its view as any folder's", async (t) => {
    const ctx = await openTab(t, () => reply("queued"));
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
