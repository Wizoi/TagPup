/**
 * V29: somebody turns on the navigator's Filter mode and clicks rows -- `+` any of, `&` must, `-` not, off -- and the grid is the
 * filter; the strip above it shows a chip for each row marked (docs/plans/advanced-filtering.md, cut 1b: web/tagpup/filter-bar.js,
 * navigator.js, library-view.js leaveSearch). How it fails: rapid clicks on one row, a Clear or a toggle clicked again before Back
 * has arrived, Back and a bookmark, a tag deleted since the bookmark, a filter that finds nothing, a list at the server's cap,
 * unsaved edits in the open photo, a page with no view. Off, the navigator is Browse as it always was (navigator.test.mjs).
 * Every name is fictional; the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, personRow } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const FILTER_MODE_KEY = "tagpup.filterMode";

/** The navigator's keywords and people, as GET /api/library/navigator answers them: a few, so a row is found by its name. */
const NAVIGATOR = () => ({
  keywords: {
    keywords: [
      { tag: "Trips", name: "Trips", parent: null, count: 50 },
      { tag: "Trips/Coast", name: "Coast", parent: "Trips", count: 40 },
      { tag: "Places", name: "Places", parent: null, count: 8 },
      { tag: "Places/Home", name: "Home", parent: "Places", count: 8 },
    ],
  },
  people: { people: [personRow(1, "Rowan Thackeray", 12), personRow(2, "Hazel Brookmire", 9), personRow(3, "Wren Halloway", 4)] },
});

/** A page whose browser remembers Filter mode as on (filter-bar.js wireFilterBar reads it as the page starts). */
const filterOn = (window) => window.localStorage.setItem(FILTER_MODE_KEY, "on");

/** The page with the library pane shown and a tab open. */
async function page(t, { tab = "people", ...options } = {}) {
  const ctx = await loadViewPage(t, { search: "?view=all", navigator: NAVIGATOR(), ...options });
  await ctx.showLibraryPane();
  await ctx.openTab(tab);
  return ctx;
}

const toggle = (ctx) => ctx.document.getElementById("nav-filter-toggle");
const bar = (ctx) => ctx.document.getElementById("filter-bar");
const chips = (ctx) => [...ctx.document.querySelectorAll("#filter-bar-chips .filter-chip")];
const chipTexts = (ctx) => chips(ctx).map((c) => `${c.querySelector(".filter-chip-mark").textContent} ${c.querySelector(".library-search-chip-text").textContent}`);
/** The marks the rows of a tab show: "sign label" for each row marked. */
const rowMarks = (ctx, tab) => ctx.rows(tab)
  .filter((row) => row.querySelector(".nav-mark").textContent)
  .map((row) => `${row.querySelector(".nav-mark").textContent} ${row.querySelector(".nav-label").textContent}`);
const markOf = (ctx, tab, label) => ctx.rowByLabel(tab, label).querySelector(".nav-mark").textContent;
const idsCalls = (ctx) => ctx.server.calls.filter((call) => call.url.includes("/api/library/ids"));
const valueInAddress = (ctx) => JSON.parse(new URLSearchParams(ctx.window.location.search).get("value"));
const words = (ctx) => ctx.document.getElementById("library-search-words");

/** Count the places the page pushes, and keep (without going) each history.go it asks for. */
function watchHistory(ctx) {
  const seen = { pushes: 0, went: [], go: ctx.window.history.go.bind(ctx.window.history) };
  const push = ctx.window.history.pushState.bind(ctx.window.history);
  ctx.window.history.pushState = (...args) => {
    seen.pushes++;
    return push(...args);
  };
  ctx.window.history.go = (delta) => seen.went.push(delta);
  return seen;
}

async function searchWords(ctx, text) {
  words(ctx).value = text;
  words(ctx).dispatchEvent(new ctx.window.Event("input"));
  ctx.key(words(ctx), "Enter");
  await ctx.settle();
}

describe("Filter mode off is Browse, and the toggle is remembered", () => {
  test("off, a click opens the row's view, as Browse always did: no marks, no chips", async (t) => {
    const ctx = await page(t);
    assert.equal(toggle(ctx).getAttribute("aria-pressed"), "false");
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "person");
    assert.deepEqual(rowMarks(ctx, "people"), []);
    assert.ok(bar(ctx).classList.contains("hidden"));
  });

  test("turned on, this browser remembers it; a page started with it on marks a row instead of opening it", async (t) => {
    const ctx = await page(t);
    click(ctx.window, toggle(ctx));
    assert.equal(ctx.window.localStorage.getItem(FILTER_MODE_KEY), "on");
    assert.equal(toggle(ctx).getAttribute("aria-pressed"), "true");
    assert.ok(!ctx.document.getElementById("nav-filter-help").classList.contains("hidden"));

    const next = await page(t, { before: filterOn });
    assert.equal(toggle(next).getAttribute("aria-pressed"), "true", "remembered");
    click(next.window, next.rowByLabel("people", "Rowan Thackeray"));
    await next.settle();
    assert.equal(next.state.library.kind, "search");
    assert.deepEqual(rowMarks(next, "people"), ["+ Rowan Thackeray"]);
    assert.deepEqual(chipTexts(next), ["+ Rowan Thackeray"]);
  });
});

describe("clicks on rows", () => {
  test("rapid clicks on one row: +, &, -, off -- one place pushed, then replaced; off leaves through Back once", async (t) => {
    const ctx = await page(t, { before: filterOn });
    const history = watchHistory(ctx);
    const rowan = () => ctx.rowByLabel("people", "Rowan Thackeray");
    click(ctx.window, rowan());
    assert.equal(markOf(ctx, "people", "Rowan Thackeray"), "+");
    click(ctx.window, rowan());
    assert.equal(markOf(ctx, "people", "Rowan Thackeray"), "&");
    assert.deepEqual(valueInAddress(ctx).all_of.map((m) => m.kind), ["person"], "& is a member of its own");
    click(ctx.window, rowan());
    assert.equal(markOf(ctx, "people", "Rowan Thackeray"), "-");
    assert.deepEqual(valueInAddress(ctx).none_of.map((m) => m.kind), ["person"]);
    assert.equal(ctx.window.history.state.searchBack, 1, "one place from the view before the filter");
    click(ctx.window, rowan());   // off: the filter is emptied and left
    click(ctx.window, rowan());   // and again, before Back has arrived
    await ctx.settle();
    assert.equal(history.pushes, 1, "the first mark is a new place; the others replace it");
    assert.deepEqual(history.went, [-1], "left once, back to the view before the filter");
    history.go(-1);
    assert.ok(await ctx.until(() => ctx.state.library && ctx.state.library.kind === "all"), "Back arrived at the whole library");
    assert.deepEqual(rowMarks(ctx, "people"), []);
    assert.ok(bar(ctx).classList.contains("hidden"));
    click(ctx.window, rowan());   // Back has arrived: a click starts a new filter
    assert.equal(markOf(ctx, "people", "Rowan Thackeray"), "+");
    await ctx.settle();
  });

  test("Ctrl-click sets + on any row; two people with + are one any_of", async (t) => {
    const ctx = await page(t, { before: filterOn });
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));   // &
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"), { ctrlKey: true });
    click(ctx.window, ctx.rowByLabel("people", "Hazel Brookmire"), { ctrlKey: true });
    await ctx.settle();
    assert.deepEqual(rowMarks(ctx, "people"), ["+ Hazel Brookmire", "+ Rowan Thackeray"], "the rows in their own order");
    const sent = idsCalls(ctx).at(-1).body.value;
    assert.equal(sent.all_of.length, 1, "one union for the tab");
    assert.equal(sent.all_of[0].kind, "any_of");
    assert.deepEqual(sent.all_of[0].value.map((m) => m.kind), ["person", "person"]);
    assert.equal(sent.none_of, undefined);
  });

  test("a filter that finds nothing keeps its chips, and the strip says why the grid is empty", async (t) => {
    const ctx = await page(t, { before: filterOn, ids: [] });
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    click(ctx.window, ctx.rowByLabel("people", "Wren Halloway"));
    await ctx.settle();
    assert.equal(ctx.state.library.status, "empty");
    assert.deepEqual(chipTexts(ctx), ["+ Rowan Thackeray", "+ Wren Halloway"]);
    assert.ok(!bar(ctx).classList.contains("hidden"));
    assert.ok(ctx.document.getElementById("library-strip-status").textContent.trim(), "a sentence for the empty grid");
  });

  test("a list at the server's cap: the click that passes it is refused with a sentence and sends nothing; another list still takes a row", async (t) => {
    // 1,000 months not in the filter (the server's most for one list, services.library_view.search_of), and 2019 a must.
    const months = [];
    for (let year = 1900; months.length < 1000; year++) {
      for (let month = 1; month <= 12 && months.length < 1000; month++) months.push({ kind: "month", value: `${year}-${String(month).padStart(2, "0")}` });
    }
    const value = { all_of: [{ kind: "year", value: "2019" }], none_of: months };
    const ctx = await page(t, { tab: "dates", before: filterOn, search: `?view=search&value=${encodeURIComponent(JSON.stringify(value))}` });
    assert.equal(ctx.state.library.invalid, undefined, ctx.state.library.message);
    assert.equal(markOf(ctx, "dates", "2019"), "&");
    assert.equal(chips(ctx).length, 1001);
    const asked = idsCalls(ctx).length;
    const address = ctx.window.location.search;
    click(ctx.window, ctx.rowByLabel("dates", "2019"));   // & -> -: a 1,001st month-or-year not in the filter
    await ctx.settle();
    assert.equal(idsCalls(ctx).length, asked, "no request");
    assert.equal(ctx.window.location.search, address, "the address is the filter's still");
    assert.equal(markOf(ctx, "dates", "2019"), "&", "the mark is the open filter's");
    assert.match(ctx.status("dates"), /at most 1,000/);
    // The cap is the server's, one list at a time: a row added to the filter's own any-of list is taken.
    click(ctx.window, ctx.rowByLabel("dates", "2020"));
    await ctx.settle();
    assert.equal(idsCalls(ctx).length, asked + 1);
    assert.equal(markOf(ctx, "dates", "2020"), "+");
    assert.doesNotMatch(ctx.status("dates"), /at most/);
  });
});

describe("Back, Forward and a bookmark", () => {
  test("Back to a filter's address gives its marks and chips; to a Browse address, none", async (t) => {
    const ctx = await page(t, { before: filterOn });
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));   // &
    click(ctx.window, ctx.rowByLabel("people", "Hazel Brookmire"));   // +
    await ctx.settle();
    const filter = ctx.window.location.search;
    await ctx.popTo("?view=all");
    assert.deepEqual(rowMarks(ctx, "people"), []);
    assert.deepEqual(chips(ctx), []);
    assert.ok(bar(ctx).classList.contains("hidden"));
    await ctx.popTo(filter);
    assert.deepEqual(rowMarks(ctx, "people"), ["+ Hazel Brookmire", "& Rowan Thackeray"]);
    assert.deepEqual(chipTexts(ctx).sort(), ["& Rowan Thackeray", "+ Hazel Brookmire"]);
    // A chip clicked takes its row off, in the filter's own place.
    const history = watchHistory(ctx);
    chips(ctx).find((c) => c.textContent.includes("Hazel")).click();
    await ctx.settle();
    assert.deepEqual(rowMarks(ctx, "people"), ["& Rowan Thackeray"]);
    assert.equal(history.pushes, 0);
  });

  test("a bookmark naming a tag deleted since: its chip is struck through, and taking it off works", async (t) => {
    const value = { all_of: [{ kind: "keyword", value: "Trips/Lighthouse" }, { kind: "keyword", value: "Trips/Coast" }] };
    const ctx = await page(t, { tab: "keywords", before: filterOn, search: `?view=search&value=${encodeURIComponent(JSON.stringify(value))}` });
    assert.ok(await ctx.until(() => chips(ctx).some((c) => c.classList.contains("library-search-chip-unknown"))), "the gone tag's chip says so");
    const unknown = chips(ctx).filter((c) => c.classList.contains("library-search-chip-unknown"));
    assert.equal(unknown.length, 1);
    assert.match(unknown[0].title, /no such tag now/);
    unknown[0].click();
    await ctx.settle();
    assert.deepEqual(valueInAddress(ctx).all_of, [{ kind: "keyword", value: "Trips/Coast" }]);
  });

  test("a page with no view open -- another library chosen, a folder shown -- shows no chips with Filter mode on", async (t) => {
    const ctx = await loadViewPage(t, { library: "other", search: "", navigator: NAVIGATOR(), before: filterOn });
    assert.equal(ctx.state.library, null);
    assert.equal(toggle(ctx).getAttribute("aria-pressed"), "true");
    assert.ok(bar(ctx).classList.contains("hidden"));
    assert.deepEqual(chips(ctx), []);
  });
});

describe("leaving a filter, and a search made elsewhere", () => {
  test("Filter turned off with a filter open leaves it once, however fast the toggle is clicked", async (t) => {
    const ctx = await page(t, { before: filterOn });
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    await ctx.settle();
    const history = watchHistory(ctx);
    click(ctx.window, toggle(ctx));   // off: leave
    click(ctx.window, toggle(ctx));   // on
    click(ctx.window, toggle(ctx));   // off again, before Back has arrived
    assert.deepEqual(history.went, [-1]);
    assert.equal(ctx.window.localStorage.getItem(FILTER_MODE_KEY), "off");
  });

  test("Clear all clicked twice leaves the filter once", async (t) => {
    const ctx = await page(t, { before: filterOn });
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    click(ctx.window, ctx.rowByLabel("people", "Wren Halloway"));
    await ctx.settle();
    const history = watchHistory(ctx);
    ctx.document.getElementById("btn-filter-bar-clear").click();
    ctx.document.getElementById("btn-filter-bar-clear").click();
    assert.deepEqual(history.went, [-1]);
  });

  test("the search box's Clear clicked twice before Back has arrived goes back once", async (t) => {
    const ctx = await page(t);
    await searchWords(ctx, "beach");
    assert.equal(ctx.window.history.state.searchBack, 1);
    const history = watchHistory(ctx);
    ctx.document.getElementById("btn-library-search-clear").click();
    ctx.document.getElementById("btn-library-search-clear").click();
    assert.deepEqual(history.went, [-1], "back to the whole library, not past it");
  });

  test("a words search marks nothing; a click then starts a new filter, a new place", async (t) => {
    const ctx = await page(t, { before: filterOn });
    await searchWords(ctx, "beach");
    assert.deepEqual(rowMarks(ctx, "people"), []);
    assert.ok(bar(ctx).classList.contains("hidden"));
    const history = watchHistory(ctx);
    click(ctx.window, ctx.rowByLabel("people", "Hazel Brookmire"));
    await ctx.settle();
    assert.equal(history.pushes, 1);
    const sent = idsCalls(ctx).at(-1).body.value;
    assert.equal(sent.words, undefined, "the words are not the filter's");
    assert.equal(sent.all_of[0].kind, "any_of");
    assert.deepEqual(chipTexts(ctx), ["+ Hazel Brookmire"]);
  });

  test("a marked row shows once, as a chip in the Filters section, not also in its All of / Any of / None of lists", async (t) => {
    const ctx = await page(t, { before: filterOn });
    click(ctx.window, ctx.rowByLabel("people", "Hazel Brookmire"));
    await ctx.settle();
    const section = ctx.document.getElementById("library-search-filters");
    assert.ok(!section.classList.contains("hidden"), "the section opens with the filter");
    assert.deepEqual(chipTexts(ctx), ["+ Hazel Brookmire"]);
    assert.equal(section.querySelectorAll(".library-search-list .library-search-chip").length, 0);
  });

  test("a filter made over a words search: Clear all goes back to the words search, as Back does", async (t) => {
    const ctx = await page(t, { before: filterOn });
    await searchWords(ctx, "beach");
    click(ctx.window, ctx.rowByLabel("people", "Hazel Brookmire"));
    await ctx.settle();
    assert.equal(ctx.window.history.state.searchBack, 1, "one place back is the words search");
    const history = watchHistory(ctx);
    ctx.document.getElementById("btn-filter-bar-clear").click();
    assert.deepEqual(history.went, [-1]);
  });

  test("unsaved edits in the open photo: the click asks first, and the marks stay the open view's until it is answered", async (t) => {
    const ctx = await page(t, { before: filterOn });
    ctx.cardById(12).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    const title = ctx.document.getElementById("input-photo-title");
    title.value = "Unsaved";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    const asked = idsCalls(ctx).length;
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    await ctx.settle(60);
    const modal = ctx.document.querySelector(".unsaved-edits-modal");
    assert.ok(modal, "Save, Discard or Cancel");
    assert.deepEqual(rowMarks(ctx, "people"), [], "no mark while the question is open");
    assert.deepEqual(chips(ctx), []);
    modal.querySelector("[data-choice='cancel']").click();
    await ctx.settle(60);
    assert.equal(idsCalls(ctx).length, asked);
    assert.equal(ctx.state.library.kind, "all");
    assert.deepEqual(rowMarks(ctx, "people"), []);
    // The question answered with Discard: the click is the filter's.
    click(ctx.window, ctx.rowByLabel("people", "Rowan Thackeray"));
    await ctx.settle(60);
    ctx.document.querySelector(".unsaved-edits-modal [data-choice='discard']").click();
    await ctx.settle();
    assert.deepEqual(rowMarks(ctx, "people"), ["+ Rowan Thackeray"]);
    assert.deepEqual(chipTexts(ctx), ["+ Rowan Thackeray"]);
  });
});
