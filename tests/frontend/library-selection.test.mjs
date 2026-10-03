/**
 * A library view's selection is photo ids, in one of two shapes (web/tagpup/selected.js; docs/ARCHITECTURE.md, phase 9d-2): the ids
 * picked, or the view's source but the ids excluded. Select all is the second with nothing excluded, so 68,000 photos are one
 * assignment; a range is a loop over the view's order; a request names the source and a few ids, never 68,000 ids or paths; and
 * what a request cannot carry (more than 20,000 ids, or excluded ones) is said in a sentence before any request is made.
 * Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

/** An object made in the page's window, as the test's own (deepStrictEqual compares prototypes, and the window's differ). */
const plain = (value) => JSON.parse(JSON.stringify(value));
const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const note = (ctx) => ctx.document.getElementById("selection-note");
const countText = (ctx) => ctx.document.getElementById("selection-summary-count").textContent;

async function bigView(t, n = 68000, search = "?view=all") {
  const ctx = await loadViewPage(t, { search, ids: range(n) });
  ctx.selected = ctx.module("selected.js");
  return ctx;
}

describe("Select all, and what is left out of it", () => {
  test("68,000 photos selected, 3 excluded by a click each: the source and 3 ids are what a request carries", async (t) => {
    const ctx = await bigView(t);
    const requests = ctx.server.calls.length;
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    assert.equal(ctx.server.calls.length, requests, "Select all asks nothing");
    for (const id of [3, 5, 7]) click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
    assert.equal(countText(ctx), "Selected: 67,997");
    assert.deepEqual([...ctx.state.library.sel.excluded].sort((a, b) => a - b), [3, 5, 7]);
    const asked = ctx.selected.selectionRequest();
    assert.equal(asked.ok, true);
    assert.deepEqual(plain(asked.body), { source: { kind: "all", value: null, recursive: false }, excluded: [3, 5, 7] });
    assert.equal(ctx.cardById(3).classList.contains("selected"), false);
    assert.equal(ctx.cardById(4).classList.contains("selected"), true);
  });

  test("a view of a folder names its source as the ids route does", async (t) => {
    const ctx = await bigView(t, 300, "?view=folder&value=D%3A%5CLibrary%5C2020&recursive=1");
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    assert.deepEqual(plain(ctx.selected.selectionRequest().body.source), { kind: "folder", value: "D:\\Library\\2020", recursive: true });
  });

  test("ids picked one by one are sent as ids, in the view's order, not the order they were picked in", async (t) => {
    const ctx = await bigView(t, 400);
    for (const id of [9, 2, 5]) click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
    assert.deepEqual(plain(ctx.selected.selectionRequest().body), { ids: [2, 5, 9] });
  });

  test("a selection of nothing is no request", async (t) => {
    const ctx = await bigView(t, 400);
    assert.deepEqual(plain(ctx.selected.selectionRequest()), { ok: false, sentence: "Nothing is selected." });
  });
});

describe("a range", () => {
  test("a Shift-click from the first card to one 20,000 photos on selects them at once and asks for no card", async (t) => {
    const ctx = await bigView(t, 30000);
    click(ctx.window, ctx.cardById(1).querySelector(".thumbnail-checkbox"));
    const before = ctx.cardsAsked.length;
    ctx.state.library.cards.set(20000, { id: 20000, name: "IMG_20000.jpg", filename: "IMG_20000.jpg", path: "D:\\x\\IMG_20000.jpg", thumb: "/t", taken: null });
    const began = Date.now();
    ctx.module("grid.js").handleCardSelectionClick("D:\\x\\IMG_20000.jpg", true, null, true);
    assert.ok(Date.now() - began < 500, "a loop over ids");
    assert.equal(ctx.selectedIds().length, 20000);
    assert.equal(ctx.cardsAsked.length, before, "no card request");
    assert.equal(ctx.selected.selectionRequest().ok, true, "exactly the limit");
  });

  test("a range of an unselecting Shift-click takes the photos off", async (t) => {
    const ctx = await bigView(t, 400);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    click(ctx.window, ctx.cardById(8).querySelector(".thumbnail-checkbox"), { shiftKey: true });
    assert.deepEqual([...ctx.state.library.sel.excluded].sort((a, b) => a - b), [2, 3, 4, 5, 6, 7, 8]);
    assert.equal(ctx.selectedIds().length, 393);
  });
});

describe("Invert", () => {
  test("a few picked photos become everything but them, and back", async (t) => {
    const ctx = await bigView(t, 68000);
    click(ctx.window, ctx.cardById(4).querySelector(".thumbnail-checkbox"));
    ctx.document.querySelector('#grid-context-menu [data-action="invert"]').click();
    assert.equal(ctx.state.library.sel.mode, "source");
    assert.deepEqual([...ctx.state.library.sel.excluded], [4]);
    assert.equal(countText(ctx), "Selected: 67,999");
    assert.equal(ctx.cardById(4).classList.contains("selected"), false);
    ctx.document.querySelector('#grid-context-menu [data-action="invert"]').click();
    assert.equal(ctx.state.library.sel.mode, "ids");
    assert.deepEqual(ctx.selectedIds(), [4]);
  });

  test("exactly 20,000 either way is inverted; 20,001 says 'Too many to invert' and changes nothing", async (t) => {
    const ctx = await bigView(t, 68000);
    ctx.module("selected.js").setIdRange(0, 19999, true);
    ctx.document.querySelector('#grid-context-menu [data-action="invert"]').click();
    assert.equal(ctx.state.library.sel.mode, "source", "20,000 is within the limit");
    ctx.document.querySelector('#grid-context-menu [data-action="invert"]').click();
    ctx.module("selected.js").setIdSelected(30000, true);
    const version = ctx.state.library.sel.version;
    ctx.document.querySelector('#grid-context-menu [data-action="invert"]').click();
    assert.equal(ctx.state.library.sel.mode, "ids", "20,001 ids: not inverted");
    assert.equal(ctx.state.library.sel.version, version);
    assert.match(ctx.stripText(), /Too many to invert: use Select all and deselect\./);
    assert.equal(ctx.selectedIds().length, 20001);
  });
});

describe("what a request cannot carry is said before any request", () => {
  test("30,000 picked in a view of 68,000: neither 30,000 ids nor all but 38,000; the note says so", async (t) => {
    const ctx = await bigView(t, 68000);
    ctx.module("selected.js").setIdRange(0, 29999, true);
    ctx.module("selection.js").updateSelectedThumbnailsCount();
    const asked = ctx.selected.selectionRequest();
    assert.equal(asked.ok, false);
    assert.match(asked.sentence, /30,000 photos are selected and 38,000 are not/);
    assert.match(asked.sentence, /20,000/);
    assert.ok(!note(ctx).classList.contains("hidden"));
    assert.equal(note(ctx).textContent, asked.sentence);
  });

  test("more than 20,000 excluded from a Select all of 68,000 is the same", async (t) => {
    const ctx = await bigView(t, 68000);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    ctx.module("selected.js").setIdRange(0, 20000, false);
    const asked = ctx.selected.selectionRequest();
    assert.equal(asked.ok, false);
    assert.match(asked.sentence, /47,999 photos are selected and 20,001 are not/);
  });

  test("a range of 60,000 in 68,000 is sent the other way round: the source and the 8,000 left out", async (t) => {
    const ctx = await bigView(t, 68000);
    ctx.module("selected.js").setIdRange(0, 59999, true);
    const asked = ctx.selected.selectionRequest();
    assert.equal(asked.ok, true);
    assert.equal(asked.body.source.kind, "all");
    assert.equal(asked.body.excluded.length, 8000);
    assert.equal(asked.body.excluded[0], 60001);
  });

  test("a Select all minus 50,000 of 68,000 is sent as the 18,000 ids that are left", async (t) => {
    const ctx = await bigView(t, 68000);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    ctx.module("selected.js").setIdRange(0, 49999, false);
    const asked = ctx.selected.selectionRequest();
    assert.equal(asked.ok, true);
    assert.equal(asked.body.ids.length, 18000);
    assert.equal(asked.body.ids[0], 50001);
  });

  test("more than 200,000 selected is refused with the limit", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(300), onIds: () => ({ source: {}, total: 250000, ids: range(300), complete: false }) });
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    const asked = ctx.module("selected.js").selectionRequest();
    assert.equal(asked.ok, false);
    assert.match(asked.sentence, /250,000 photos are selected; a bulk edit takes at most 200,000/);
  });
});

describe("the selection and the view", () => {
  test("it is cleared when another view opens, and kept by Refresh view", async (t) => {
    const ctx = await bigView(t, 400);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    click(ctx.window, ctx.document.getElementById("btn-library-refresh"));
    await ctx.settle();
    assert.equal(ctx.selectedIds().length, 400);
    await ctx.popTo("?view=year&value=2020");
    assert.equal(ctx.selectedIds().length, 0);
    assert.equal(ctx.state.library.sel.mode, "ids");
  });

  test("a photo the refreshed view no longer holds is let go of the selection", async (t) => {
    let ids = range(400);
    const ctx = await loadViewPage(t, { search: "?view=all", onIds: () => ({ source: {}, total: ids.length, ids, complete: true }) });
    for (const id of [3, 4, 5]) click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
    ids = ids.filter((id) => id !== 4);
    click(ctx.window, ctx.document.getElementById("btn-library-refresh"));
    await ctx.settle();
    assert.deepEqual([...ctx.state.library.sel.ids].sort((a, b) => a - b), [3, 5]);
  });

  test("memory: the order is held once, and the selection is sets of ids, never a list of paths", async (t) => {
    const ctx = await bigView(t, 68000);
    const order = ctx.state.library.ids;
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    ctx.module("selected.js").setIdRange(0, 20000, false);
    assert.equal(ctx.state.library.ids, order, "no copy of the order");
    assert.equal(ctx.state.selectedThumbnails.length, 0);
    assert.equal(ctx.state.selectedKeys.size, 0);
    assert.equal(ctx.state.library.sel.excluded.size, 20001);
  });

  test("a card built after a Select all is drawn selected, and one scrolled to later too", async (t) => {
    const ctx = await bigView(t, 68000);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    await ctx.scrollTo(100 + 216 * 5000);
    await ctx.settle();
    assert.ok(ctx.real().length > 0);
    assert.ok(ctx.real().every((card) => card.classList.contains("selected") && card.getAttribute("aria-selected") === "true"));
  });
});
