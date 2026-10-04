/**
 * The owner's second review of the library views (docs/findings.md #712-#714): a folder of "Folders to Organize" switches the
 * sidebar to Organize; one floating header for a view; Sort by in it, a menu of the field and the direction.
 * Fictional names only: the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, closeAllApps, click, openFolder, photoRecord } from "./harness.mjs";
import { GRID_TOP, STRIDE, loadViewPage, pageErrors } from "./view-page.mjs";

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

describe("#713: one floating header for a library view", () => {
  const css = fs.readFileSync(path.join(REPO_ROOT, "web", "tagpup", "style.css"), "utf8");
  const rule = (selector) => {
    const at = css.indexOf(`${selector} {`);
    assert.ok(at >= 0, `style.css has ${selector}`);
    return css.slice(at, css.indexOf("}", at));
  };
  const top = (ctx) => el(ctx, "folder-view-top");

  test("the header holds the title, the total, Refresh view as an icon, and the view's actions; the card's own title is hidden", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=person&value=Wren%20Halloway", ids: range(40) });
    assert.ok(top(ctx).classList.contains("in-library-view"));
    assert.match(rule(".folder-view-top.in-library-view"), /position: sticky;/);
    assert.match(rule(".folder-view-top.in-library-view .folder-view-heading"), /display: none;/, "no second \"Photos of\" that scrolls away");
    assert.equal(el(ctx, "library-strip-source").textContent, "Photos of Wren Halloway");
    assert.ok(top(ctx).querySelector(".folder-view-heading").contains(el(ctx, "folder-view-title")), "the hidden one is the card's");
    for (const id of ["library-strip", "btn-library-refresh", "btn-select-all-thumbnails", "btn-select-none-thumbnails",
      "btn-delete-selection", "selected-thumbnails-count", "btn-size-small"]) {
      assert.ok(top(ctx).contains(el(ctx, id)), `${id} is in the one header`);
    }
    const refresh = el(ctx, "btn-library-refresh");
    assert.equal(refresh.getAttribute("aria-label"), "Refresh view");
    assert.match(refresh.title, /^Refresh view: /);
    assert.equal(refresh.querySelector("[aria-hidden='true']").textContent.length, 1, "an icon, hidden from a screen reader");
    click(ctx.window, refresh);
    await ctx.settle();
    assert.equal(ctx.idsAsked.length, 2, "it asks the library again");
  });

  test("Organize's header is as it was: nothing floats, its card keeps its title", async (t) => {
    const ctx = await loadViewPage(t, { search: "" });
    await openFolder(ctx, "D:\\Library\\2020\\Event 01");
    await ctx.settle(60);
    assert.ok(!top(ctx).classList.contains("in-library-view"));
    assert.ok(el(ctx, "library-strip").classList.contains("hidden"));
    assert.equal(el(ctx, "folder-view-title").closest(".folder-view-heading") !== null, true);
    ctx.module("library-view.js").openLibraryView({ kind: "all" });
    await ctx.settle();
    assert.ok(top(ctx).classList.contains("in-library-view"));
    ctx.module("library-view.js").closeLibraryView({ folder: "D:\\Library\\2020\\Event 01" });
    await ctx.settle(100);
    assert.ok(!top(ctx).classList.contains("in-library-view"), "a view closed onto a folder floats nothing");
  });

  test("a card scrolled to by the keys is below the header and its gutter, however tall the header is", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(400) });
    Object.defineProperty(top(ctx), "offsetHeight", { get: () => 92, configurable: true });
    ctx.state.grid.scrollToIndex(40, "start");
    assert.equal(ctx.here.scrollTop, GRID_TOP + 10 * STRIDE - (92 + 16));
    // Up from below: the card's row lands below the header, not under it.
    await ctx.scrollTo(GRID_TOP + 30 * STRIDE);
    ctx.state.grid.scrollToIndex(80, "nearest");
    assert.equal(ctx.here.scrollTop, GRID_TOP + 20 * STRIDE - (92 + 16));
  });
});
