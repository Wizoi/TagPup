/**
 * Back from a photo opened over a library view (web/tagpup/view-left.js, #780): the Back button of the photo panel, Escape and the
 * browser's Back each return to the view exactly as it was left -- the address, the grid's place, the selection (a Select all
 * with photos left out too), the order -- and the place the photo made in the history is one place however many photos are stepped
 * through. How it fails: unsaved edits in the photo, the open photo deleted, another view opened over it, a photo's title saved,
 * a reload (the place is kept best-effort). Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, STRIDE } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const IDS = range(400);
const el = (ctx, id) => ctx.document.getElementById(id);
const gridShown = (ctx) => !el(ctx, "folder-view-content").classList.contains("hidden");
const photoShown = (ctx) => !el(ctx, "panel-content").classList.contains("hidden");
const backButton = (ctx) => el(ctx, "btn-photo-back");
const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
const open = async (ctx, id) => {
  ctx.cardById(id).querySelector(".btn-thumbnail-detail").click();
  await ctx.settle(80);
};
const press = (ctx, key) => ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
const topOf = (ctx) => ctx.here.scrollTop;
const answer = async (ctx, choice) => {
  ctx.document.querySelector(`.unsaved-edits-modal.active [data-choice='${choice}']`).click();
  await ctx.settle(120);
};

/** A view of 400 photos, scrolled to the 11th row, with everything selected but two, as a person leaves it before looking at a photo. */
async function leftView(t, extra = {}) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: IDS, ...extra });
  await ctx.scrollTo(10 * STRIDE);
  ctx.document.getElementById("btn-select-all-thumbnails").click();
  pick(ctx, ctx.real().map((card) => Number(card.dataset.id))[1]);
  pick(ctx, ctx.real().map((card) => Number(card.dataset.id))[2]);
  await ctx.settle(60);
  const first = ctx.real().map((card) => Number(card.dataset.id));
  ctx.left = {
    scroll: topOf(ctx), selected: ctx.selectedIds(), address: ctx.window.location.search, count: el(ctx, "selected-thumbnails-count").textContent,
    photo: first[4], order: ctx.state.library.order,
  };
  return ctx;
}

/** Is the view as it was left? */
function assertAsLeft(ctx, what) {
  assert.ok(gridShown(ctx) && !photoShown(ctx), `${what}: the grid is shown, not the photo`);
  assert.equal(topOf(ctx), ctx.left.scroll, `${what}: the grid is where it was scrolled to`);
  assert.deepEqual(ctx.selectedIds(), ctx.left.selected, `${what}: the same photos are selected`);
  assert.equal(el(ctx, "selected-thumbnails-count").textContent, ctx.left.count, `${what}: the count says so`);
  assert.equal(ctx.window.location.search, ctx.left.address, `${what}: the same view in the address`);
  assert.equal(ctx.state.library.order, ctx.left.order, `${what}: the same order`);
  assert.ok(ctx.cardById(ctx.left.photo), `${what}: the photo that was in view has its card`);
  assert.equal(ctx.state.viewLeft, null, `${what}: nothing is left to restore`);
}

describe("the three ways back", () => {
  test("a photo opened over a view has a Back button, and it returns to the view as it was left", async (t) => {
    const ctx = await leftView(t);
    assert.equal(backButton(ctx).classList.contains("hidden"), false, "the button is offered in a library view");
    await open(ctx, ctx.left.photo);
    assert.ok(photoShown(ctx) && !gridShown(ctx));
    assert.equal(backButton(ctx).tagName, "BUTTON");
    click(ctx.window, backButton(ctx));
    await ctx.settle(200);
    assertAsLeft(ctx, "Back button");
  });

  test("Escape returns, too", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    press(ctx, "Escape");
    await ctx.settle(200);
    assertAsLeft(ctx, "Escape");
  });

  test("so does the browser's Back, which stays on the page, and Forward opens the photo again", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    ctx.window.history.back();
    await ctx.settle(200);
    assertAsLeft(ctx, "browser Back");
    ctx.window.history.forward();
    await ctx.settle(200);
    assert.ok(photoShown(ctx) && !gridShown(ctx), "Forward is the photo again");
    assert.equal(ctx.state.library.activeId, ctx.left.photo);
    ctx.window.history.back();
    await ctx.settle(200);
    assertAsLeft(ctx, "Back again");
  });

  test("Escape with the photo's tag field holding text, or a face panel open, is the field's or the panel's, not Back", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    const field = el(ctx, "input-add-tag");
    field.focus();
    field.value = "Trips/Co";
    field.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await ctx.settle(60);
    assert.ok(photoShown(ctx), "the photo stays; the field was emptied");
    assert.equal(field.value, "");
    ctx.state.faceBoxes.open = 7;
    press(ctx, "Escape");
    await ctx.settle(60);
    assert.ok(photoShown(ctx), "a face's panel is open: it has the key");
    ctx.state.faceBoxes.open = null;
  });

  test("a search comes back as it was: its words and chips in the box, the navigator's open rows, the sidebar's pane", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=year&value=2020", ids: IDS });
    const words = el(ctx, "library-search-words");
    words.value = "beach";
    words.dispatchEvent(new ctx.window.Event("input"));
    ctx.key(words, "Enter");
    await ctx.settle();
    await ctx.openTab("keywords");
    ctx.state.nav.sections.keywords.expanded.add("Trips");
    const shown = { words: words.value, address: ctx.window.location.search, tab: ctx.state.nav.tab, pane: ctx.state.nav.shown, order: ctx.state.library.order };
    await open(ctx, IDS[4]);
    click(ctx.window, backButton(ctx));
    await ctx.settle(200);
    assert.ok(gridShown(ctx) && !photoShown(ctx));
    assert.equal(ctx.state.library.kind, "search");
    assert.deepEqual({ words: words.value, address: ctx.window.location.search, tab: ctx.state.nav.tab, pane: ctx.state.nav.shown, order: ctx.state.library.order }, shown);
    assert.ok(ctx.state.nav.sections.keywords.expanded.has("Trips"));
  });

  test("a folder's photo has no Back button and Escape does nothing there", async (t) => {
    const ctx = await loadViewPage(t, { search: "" });
    assert.equal(backButton(ctx).classList.contains("hidden"), true);
  });
});

describe("one place in the history, however many photos", () => {
  test("stepping from photo to photo makes no new place: one Back is the view", async (t) => {
    const ctx = await leftView(t);
    const before = ctx.window.history.length;
    await open(ctx, ctx.left.photo);
    assert.equal(ctx.window.history.length, before + 1, "the photo is a place");
    press(ctx, "ArrowRight");
    await ctx.settle(80);
    press(ctx, "ArrowRight");
    await ctx.settle(80);
    assert.equal(ctx.window.history.length, before + 1, "stepping adds none");
    assert.equal(ctx.state.library.activeId, ctx.left.photo + 2);
    ctx.window.history.back();
    await ctx.settle(200);
    assertAsLeft(ctx, "one Back after two steps");
    ctx.window.history.forward();
    await ctx.settle(200);
    assert.equal(ctx.state.library.activeId, ctx.left.photo + 2, "Forward opens the photo it was on");
  });

  test("opening a photo again after Back makes a place again, and the places do not pile up", async (t) => {
    const ctx = await leftView(t);
    const before = ctx.window.history.length;
    for (let i = 0; i < 3; i++) {
      await open(ctx, ctx.left.photo);
      click(ctx.window, backButton(ctx));
      await ctx.settle(200);
    }
    assert.equal(ctx.window.history.length, before + 1, "each opening drops the place the last Back left ahead of it");
    assertAsLeft(ctx, "after three round trips");
  });
});

describe("what is asked, and what fails", () => {
  test("unsaved edits in the photo: Back asks; Cancel keeps the photo and its place; Discard returns to the view as left", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    const title = el(ctx, "input-photo-title");
    title.value = "Unsaved caption";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    click(ctx.window, backButton(ctx));
    await ctx.settle(200);
    await answer(ctx, "cancel");
    assert.ok(photoShown(ctx), "Cancel: the photo stays");
    assert.equal(title.value, "Unsaved caption", "and what was typed");
    assert.equal(ctx.window.history.state.photo, ctx.left.photo, "the page is on the photo's place again");
    click(ctx.window, backButton(ctx));
    await ctx.settle(200);
    await answer(ctx, "discard");
    assertAsLeft(ctx, "after Discard");
  });

  test("the photo's title saved: its card follows when the view is back", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    el(ctx, "input-photo-title").value = "Harbour";
    click(ctx.window, el(ctx, "btn-save-title"));
    await ctx.settle(80);
    click(ctx.window, backButton(ctx));
    await ctx.settle(200);
    assertAsLeft(ctx, "after a save");
    assert.equal(ctx.cardById(ctx.left.photo).querySelector(".thumbnail-filename").textContent, "Harbour");
  });

  test("the open photo deleted: the next opens; Back then shows the view without it, the selection less it, the place kept", async (t) => {
    const ctx = await leftView(t);
    ctx.left.selected = ctx.left.selected.filter((id) => id !== ctx.left.photo);
    await open(ctx, ctx.left.photo);
    el(ctx, "btn-delete-photo").click();
    await ctx.settle(120);
    assert.ok(photoShown(ctx), "the photo after it is open");
    assert.equal(ctx.state.library.activeId, ctx.left.photo + 1);
    assert.ok(!ctx.state.library.ids.includes(ctx.left.photo), "the deleted photo left the view");
    click(ctx.window, backButton(ctx));
    await ctx.settle(200);
    assert.ok(gridShown(ctx) && !photoShown(ctx));
    assert.equal(topOf(ctx), ctx.left.scroll, "the grid's place is kept");
    assert.deepEqual(ctx.selectedIds(), ctx.left.selected, "the selection is what it was, less the photo");
    assert.equal(ctx.cardById(ctx.left.photo), null, "and its card is gone");
    assert.equal(ctx.window.location.search, ctx.left.address);
  });

  test("the last photo deleted: the grid, as left, an empty view", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: [11] });
    await open(ctx, 11);
    el(ctx, "btn-delete-photo").click();
    await ctx.settle(200);
    assert.ok(gridShown(ctx) && !photoShown(ctx));
    assert.equal(ctx.state.library.ids.length, 0);
  });

  test("a bulk Delete that took the open photo returns to the view as left, less the photos gone", async (t) => {
    const ctx = await leftView(t, { onIds: () => ({ source: {}, total: IDS.length - 1, ids: IDS.filter((id) => id !== 7), complete: true }) });
    await open(ctx, ctx.left.photo);
    ctx.state.library.activeId = 7;
    await ctx.module("library-view.js").photosDeleted();
    await ctx.settle(200);
    assert.ok(gridShown(ctx) && !photoShown(ctx));
    assert.equal(ctx.window.location.search, ctx.left.address);
    assert.equal(ctx.state.viewLeft, null);
  });

  test("another view opened over the photo forgets the place; Back then goes to the photo's view, opened as it was, not the photo", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    const { upper } = ctx.module("hooks.js");
    upper.openLibraryView({ kind: "year", value: "2020", recursive: false });
    await ctx.settle(200);
    assert.ok(gridShown(ctx) && !photoShown(ctx), "the new view's grid");
    assert.equal(ctx.state.library.kind, "year");
    assert.equal(ctx.state.viewLeft, null, "what was left of the first view is not the second's");
    assert.deepEqual(ctx.selectedIds(), [], "a view has its own selection");
    ctx.window.history.back();
    await ctx.settle(200);
    assert.equal(ctx.state.library.kind, "all", "Back: the first view is open again");
    assert.ok(gridShown(ctx) && !photoShown(ctx));
  });

  test("another view opened over the photo saves the first view's scroll on the photo's place (#863)", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    ctx.here.scrollTop = 0;                      // a hidden grid has no offset: the browser forgot it
    ctx.module("hooks.js").upper.openLibraryView({ kind: "year", value: "2020", recursive: false });
    await ctx.settle(200);
    ctx.window.history.back();
    await ctx.settle(200);
    assert.equal(ctx.state.library.kind, "all");
    assert.equal(topOf(ctx), ctx.left.scroll, "the first view opens where it was left, not at the top");
  });

  test("a second search typed over a photo opened over a search: Clear returns to the view before the first search (#862)", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=year&value=2020", ids: IDS });
    const words = el(ctx, "library-search-words");
    const type = async (text) => {
      words.value = text;
      words.dispatchEvent(new ctx.window.Event("input"));
      ctx.key(words, "Enter");
      await ctx.settle();
    };
    await type("beach");
    await open(ctx, IDS[3]);
    await type("sea");
    assert.equal(ctx.state.library.kind, "search");
    assert.ok(gridShown(ctx) && !photoShown(ctx));
    el(ctx, "btn-library-search-clear").click();
    await ctx.settle(300);
    assert.equal(ctx.state.library.kind, "year", "not the first search");
  });

  test("a search pushed from a photo's place is one place further from the view before it (#862)", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=year&value=2020", ids: IDS });
    await open(ctx, IDS[3]);
    const words = el(ctx, "library-search-words");
    words.value = "sea";
    words.dispatchEvent(new ctx.window.Event("input"));
    ctx.key(words, "Enter");
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "search");
    assert.equal(ctx.window.history.state.searchBack, 2);
    el(ctx, "btn-library-search-clear").click();
    await ctx.settle(300);
    assert.equal(ctx.state.library.kind, "year");
  });

  test("a held Escape or a double click on Back asks the browser to go back once, until it has arrived (#864)", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    const real = ctx.window.history.back.bind(ctx.window.history);
    let asked = 0;
    ctx.window.history.back = () => { asked += 1; };
    press(ctx, "Escape");
    press(ctx, "Escape");
    click(ctx.window, backButton(ctx));
    assert.equal(asked, 1, "one step");
    ctx.window.history.back = real;
    real();
    await ctx.settle(200);                       // the popstate arrives
    assertAsLeft(ctx, "after the one step");
    await open(ctx, ctx.left.photo);
    ctx.window.history.back = () => { asked += 1; };
    press(ctx, "Escape");
    assert.equal(asked, 2, "asked again once it arrived");
  });

  test("the same photo opened again after Back has its place noted, so Forward opens it (#867)", async (t) => {
    const ctx = await leftView(t);
    await open(ctx, ctx.left.photo);
    click(ctx.window, backButton(ctx));
    await ctx.settle(200);
    await open(ctx, ctx.left.photo);
    assert.equal(ctx.window.history.state.photo, ctx.left.photo);
  });

  test("a reload: the view comes from the address, and a Back to the view's place finds where it was scrolled to, best-effort", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: IDS });
    ctx.window.history.replaceState({ scrollTop: 7 * STRIDE }, "");
    ctx.window.dispatchEvent(new ctx.window.PopStateEvent("popstate", { state: { scrollTop: 7 * STRIDE } }));
    await ctx.settle(120);
    assert.equal(topOf(ctx), 7 * STRIDE);
    assert.ok(gridShown(ctx));
  });

  test("Clear on a search with a photo open returns to the view before the search", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=year&value=2020", ids: IDS });
    const words = el(ctx, "library-search-words");
    words.value = "beach";
    words.dispatchEvent(new ctx.window.Event("input"));
    ctx.key(words, "Enter");
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "search");
    await open(ctx, IDS[3]);
    assert.ok(photoShown(ctx));
    el(ctx, "btn-library-search-clear").click();
    await ctx.settle(300);
    assert.equal(ctx.state.library.kind, "year", "the view before the search, though a photo has a place of its own");
    assert.ok(gridShown(ctx) && !photoShown(ctx));
  });
});
