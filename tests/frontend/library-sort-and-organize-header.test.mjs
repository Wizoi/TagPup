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

describe("#714: Sort by, in the header: the field and the direction", () => {
  const button = (ctx) => el(ctx, "btn-sort-by");
  const menu = (ctx) => el(ctx, "sort-menu");
  const items = (ctx) => [...menu(ctx).querySelectorAll("[role='menuitemradio']")];
  const item = (ctx, label) => items(ctx).find((each) => each.textContent === label);
  const checked = (ctx) => items(ctx).filter((each) => each.getAttribute("aria-checked") === "true").map((each) => each.textContent);
  const isOpen = (ctx) => !menu(ctx).classList.contains("hidden");
  const lastOrder = (ctx) => new URL(ctx.idsAsked.at(-1), "http://localhost").searchParams.get("order");
  const press = (ctx, key, target = ctx.document.activeElement) => ctx.key(target, key);

  test("two sections, each a radio group showing the current choice; the button says the order", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=keyword&value=Trips&order=name-desc" });
    assert.ok(el(ctx, "folder-view-top").contains(button(ctx)), "in the floating header");
    assert.ok(!el(ctx, "sidebar-pane-library").contains(button(ctx)), "not in the sidebar");
    assert.equal(button(ctx).getAttribute("aria-haspopup"), "menu");
    assert.equal(button(ctx).getAttribute("aria-label"), "Sort by: File name, descending");
    assert.match(button(ctx).textContent, /^Sort by: File name/);
    assert.ok(!isOpen(ctx));
    click(ctx.window, button(ctx));
    assert.ok(isOpen(ctx));
    assert.equal(button(ctx).getAttribute("aria-expanded"), "true");
    const groups = [...menu(ctx).querySelectorAll("[role='group']")];
    assert.deepEqual(groups.map((g) => [...g.querySelectorAll("[role='menuitemradio']")].map((i) => i.textContent)),
      [["Date Taken", "Caption", "File name"], ["Ascending", "Descending"]]);
    assert.deepEqual(groups.map((g) => ctx.document.getElementById(g.getAttribute("aria-labelledby")).textContent), ["Sort by", "Order"]);
    assert.deepEqual(checked(ctx), ["File name", "Descending"]);
    assert.equal(ctx.document.activeElement, item(ctx, "File name"), "the focus on the field chosen");
  });

  test("Caption keeps the direction; Ascending keeps the field; each reads the view again, the address keeps it, Back returns", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all&order=taken-desc" });
    click(ctx.window, button(ctx));
    click(ctx.window, item(ctx, "Caption"));
    await ctx.settle();
    assert.ok(!isOpen(ctx));
    assert.equal(ctx.document.activeElement, button(ctx), "the focus back on the button");
    assert.equal(lastOrder(ctx), "caption-desc");
    assert.equal(new URLSearchParams(ctx.window.location.search).get("order"), "caption-desc");
    assert.equal(ctx.state.nav.order, "caption-desc", "and the next view is opened in it");
    click(ctx.window, button(ctx));
    click(ctx.window, item(ctx, "Ascending"));
    await ctx.settle();
    assert.equal(lastOrder(ctx), "caption");
    assert.equal(ctx.window.location.search, "?view=all&order=caption");
    assert.equal(button(ctx).getAttribute("aria-label"), "Sort by: Caption, ascending");
    await ctx.popTo("?view=all&order=caption-desc");
    assert.equal(lastOrder(ctx), "caption-desc");
    assert.equal(button(ctx).getAttribute("aria-label"), "Sort by: Caption, descending");
  });

  test("the keys: ArrowDown opens on the field, arrows move through both sections, Enter chooses, Escape closes onto the button", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const asked = ctx.idsAsked.length;
    button(ctx).focus();
    press(ctx, "ArrowDown");
    assert.ok(isOpen(ctx));
    assert.equal(ctx.document.activeElement, item(ctx, "Date Taken"));
    press(ctx, "ArrowUp");
    assert.equal(ctx.document.activeElement, item(ctx, "Descending"), "up from the first wraps to the last");
    press(ctx, "Home");
    press(ctx, "ArrowDown");
    press(ctx, "ArrowDown");
    assert.equal(ctx.document.activeElement, item(ctx, "File name"));
    press(ctx, "Escape");
    assert.ok(!isOpen(ctx));
    assert.equal(ctx.document.activeElement, button(ctx));
    assert.equal(ctx.idsAsked.length, asked, "Escape chooses nothing");
    press(ctx, "ArrowUp");
    assert.equal(ctx.document.activeElement, item(ctx, "Ascending"), "ArrowUp opens on the direction chosen");
    press(ctx, "ArrowDown");
    const enter = press(ctx, "Enter");
    assert.ok(enter.defaultPrevented);
    await ctx.settle();
    assert.equal(lastOrder(ctx), "taken-desc");
    assert.ok(!isOpen(ctx));
    assert.equal(ctx.document.activeElement, button(ctx));
  });

  test("the arrow keys in the menu do not step the open photo; Tab and a click outside close it", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const before = ctx.photosAsked.length;
    click(ctx.window, button(ctx));
    press(ctx, "ArrowDown");
    press(ctx, "ArrowRight");
    press(ctx, "ArrowLeft");
    await ctx.settle(20);
    assert.equal(ctx.photosAsked.length, before);
    press(ctx, "Tab");
    assert.ok(!isOpen(ctx));
    click(ctx.window, button(ctx));
    assert.ok(isOpen(ctx));
    el(ctx, "thumbnails-grid").dispatchEvent(new ctx.window.MouseEvent("mousedown", { bubbles: true }));
    assert.ok(!isOpen(ctx));
    assert.equal(button(ctx).getAttribute("aria-expanded"), "false");
  });

  test("rapid clicks: the button toggles, the same choice asks for nothing, two choices read the last", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const asked = ctx.idsAsked.length;
    for (let i = 0; i < 5; i++) click(ctx.window, button(ctx));
    assert.ok(isOpen(ctx), "five clicks: open");
    click(ctx.window, item(ctx, "Date Taken"));
    click(ctx.window, button(ctx));
    click(ctx.window, item(ctx, "Ascending"));
    await ctx.settle();
    assert.equal(ctx.idsAsked.length, asked, "what is chosen already is not asked again");
    ctx.hold.ids = true;
    click(ctx.window, button(ctx));
    click(ctx.window, item(ctx, "File name"));
    click(ctx.window, button(ctx));
    click(ctx.window, item(ctx, "Caption"));
    await ctx.settle(20);
    await ctx.release("ids");
    await ctx.settle();
    assert.equal(ctx.state.library.order, "caption");
    assert.equal(ctx.state.library.status, "ready", "the view is the last one chosen, and it is read");
    assert.equal(ctx.window.location.search, "?view=all&order=caption");
  });

  test("the menu open while the view changes under it (Back, a navigator row) closes, its focus back on the button", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    ctx.module("library-view.js").openLibraryView({ kind: "year", value: "2020" });
    await ctx.settle();
    click(ctx.window, button(ctx));
    assert.ok(isOpen(ctx));
    await ctx.popTo("?view=all");
    assert.ok(!isOpen(ctx), "drawn for the view that has gone");
    assert.equal(ctx.document.activeElement, button(ctx));
    click(ctx.window, button(ctx));
    ctx.module("library-view.js").closeLibraryView();
    await ctx.settle();
    assert.ok(!isOpen(ctx), "no view: no menu");
  });

  test("a sort chosen with unsaved edits in the open photo asks first; Cancel keeps the view, its order and the next view's", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(40) });
    ctx.cardById(3).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    const title = el(ctx, "input-photo-title");
    title.value = "Unsaved";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    const asked = ctx.idsAsked.length;
    click(ctx.window, button(ctx));
    click(ctx.window, item(ctx, "Caption"));
    await ctx.settle(60);
    const modal = ctx.document.querySelector(".unsaved-edits-modal");
    assert.ok(modal, "Save, Discard or Cancel");
    modal.querySelector("[data-choice='cancel']").click();
    await ctx.settle(60);
    assert.equal(ctx.idsAsked.length, asked);
    assert.equal(ctx.state.library.order, "taken");
    assert.equal(ctx.state.nav.order, "taken", "the next view is not read in an order that was never shown");
    assert.equal(ctx.window.location.search, "?view=all");
    assert.equal(button(ctx).getAttribute("aria-label"), "Sort by: Date Taken, ascending");
  });

  test("a bookmark of the first review's orders opens in them; caption orders are the address's own", async (t) => {
    for (const [order, label] of [["taken", "Date Taken, ascending"], ["taken-desc", "Date Taken, descending"],
      ["name", "File name, ascending"], ["name-desc", "File name, descending"], ["caption", "Caption, ascending"],
      ["caption-desc", "Caption, descending"]]) {
      const ctx = await loadViewPage(t, { search: `?view=all&order=${order}` });
      assert.equal(ctx.state.library.invalid, undefined, order);
      assert.equal(lastOrder(ctx), order === "taken" ? null : order);
      assert.equal(button(ctx).getAttribute("aria-label"), `Sort by: ${label}`);
    }
  });

  test("a view whose address is no view: Sort by is off and opens nothing", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all&order=sideways" });
    assert.equal(button(ctx).disabled, true);
    click(ctx.window, button(ctx));
    assert.ok(!isOpen(ctx));
  });

  test("a view in caption order: Select all and the tally name the source, as in any order", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=keyword&value=Trips&order=caption", ids: range(40) });
    el(ctx, "btn-select-all-thumbnails").click();
    const request = ctx.module("selected.js").selectionRequest();
    assert.deepEqual(JSON.parse(JSON.stringify(request.body)), { source: { kind: "keyword", value: "Trips", recursive: false }, excluded: [] },
      "the selection names the source, never its order");
    await ctx.settle(700);
    const tallied = ctx.server.calls.filter((call) => call.url.includes("/selection/tally"));
    assert.equal(tallied.length, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(tallied[0].body)).selection, JSON.parse(JSON.stringify(request.body)));
  });
});
