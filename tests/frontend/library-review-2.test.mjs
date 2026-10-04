/**
 * The owner's second review of the library views (docs/findings.md #712-#714): a folder of "Folders to Organize" switches the
 * sidebar to Organize; one floating header for a view; Sort by in it, a menu of the field and the direction.
 * Fictional names only: the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, photoRecord } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const el = (ctx, id) => ctx.document.getElementById(id);
const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);

describe("#712: a folder of Folders to Organize switches the sidebar to Organize", () => {
  const EVENT = "D:\\Library\\2020\\Event 01";
  const scan = () => [1, 2].map((n) => photoRecord({ filename: `IMG_000${n}.jpg`, path: `${EVENT}\\IMG_000${n}.jpg`, tags: [], people: [] }));
  const tally = { total: 1, tags: [], more_tags: 0, people: [], more_people: 0, folders: { count: 1, listed: [{ path: EVENT, name: "Event 01", photos: 1 }] } };
  const link = (ctx) => ctx.document.querySelector("#selection-folders-list .selection-folder-link");

  /** The Library pane chosen with no view open -- as the owner does on opening TagPup -- then a view, a photo selected. */
  async function fromTheLibraryPane(t, extra = {}) {
    const ctx = await loadViewPage(t, { search: "", ids: range(40), scan, ...extra });
    await ctx.showLibraryPane();
    assert.equal(ctx.state.nav.choice.folder, "library", "the pane chosen for a folder is Library");
    ctx.module("library-view.js").openLibraryView({ kind: "keyword", value: "Trips" });
    await ctx.settle();
    ctx.bulk.tally = tally;
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await ctx.settle(700);
    return ctx;
  }

  test("the switch is on Organize, selected, its pane shown, as its own click leaves it", async (t) => {
    const ctx = await fromTheLibraryPane(t);
    link(ctx).click();
    await ctx.settle(300);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, EVENT);
    assert.equal(ctx.state.nav.shown, "folder");
    assert.equal(ctx.paneTab("folder").getAttribute("aria-selected"), "true");
    assert.ok(ctx.paneTab("folder").classList.contains("active"));
    assert.equal(ctx.paneTab("library").getAttribute("aria-selected"), "false");
    assert.ok(!ctx.pane("folder").classList.contains("hidden"));
    assert.ok(ctx.pane("library").classList.contains("hidden"));
    assert.equal(ctx.state.nav.choice.folder, "folder", "chosen, as a click on the switch chooses it");
  });

  test("a folder that is not on disk any more leaves the view and the switch as they were", async (t) => {
    const ctx = await fromTheLibraryPane(t);
    ctx.server.first("/api/folder/scan", { error: "No such folder." }, { status: 400 });
    link(ctx).click();
    await ctx.settle(300);
    assert.ok(ctx.state.library);
    assert.equal(ctx.state.nav.shown, "library");
    assert.equal(ctx.paneTab("library").getAttribute("aria-selected"), "true");
    pageErrors();
  });
});
