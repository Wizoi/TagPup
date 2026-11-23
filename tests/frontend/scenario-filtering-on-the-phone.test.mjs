/**
 * Owner scenario: on the phone page (web/tagpup/mobile.html, docs/plans/phone-redesign.md cut 3) the owner filters the
 * library from the Filter sheet over the Grid (web/tagpup/mobile-filter.js): People, Keywords, Folders and Dates, each row
 * off or marked `+` any of, `&` must or `-` not, as the desktop's Filter mode marks them (navigator-model.js), the chips
 * above the grid saying what the filter is.
 *
 * - Two people and a keyword marked any of, then a keyword tapped round to not: one search the server reads as said, the
 *   chips in the filter's order, Show counting what it finds.
 * - Rapid taps on one row cycle it from the last mark, in one place of history; a reply for a mark left is dropped.
 * - Back closes the sheet, the filter staying on the Grid; a second Back leaves the Grid; Forward gives the marks back.
 * - A filter finding nothing says so with its chips, Remove the last and Clear all; the count says 0 photos and Show
 *   "No photos", not pressable, while the marks can still be changed.
 * - An address naming a keyword deleted since gives a struck chip.
 * - Another library is another page: no chip of the first, every request to the other.
 * - A tab whose section could not be read says so with Try again, and Try again draws its rows.
 * - A filter at the server's cap of rows (MAX_MEMBERS) refuses one more with the desktop's sentence, and reads nothing.
 * - No cover is asked while the sheet is open.
 * - Six chips at 320 px are one row scrolled sideways, never wrapped (jsdom has no layout: the row and its CSS).
 *
 * Every name is made up; the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadApp, FakeServer, click, closeAllApps, pageExports, answer, intersect, observed } from "./harness.mjs";
import { PHONE_PAGE, el, shown, pageState, settle, phoneCard, phoneLayout, tab } from "./phone-page.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BETA_PAGE = "http://localhost:8090/beta/mobile.html";

const PEOPLE = {
  people: [
    { name: "Wren Alder", count: 4, person_id: 11, person: { tag: "People/Wren Alder" } },
    { name: "Sam Birch", count: 5, person_id: 12, person: { tag: "People/Sam Birch" } },
    { name: "Ada Quill", count: 2, person_id: 13, person: { tag: "People/Ada Quill" } },
  ],
  groups: [],
  unfiled: 0,
};

const KEYWORDS = {
  keywords: [
    { tag: "Events", name: "Events", parent: null, count: 6 },
    { tag: "Events/Track Meet", name: "Track Meet", parent: "Events", count: 3 },
    { tag: "Sports", name: "Sports", parent: null, count: 4 },
    { tag: "Holidays", name: "Holidays", parent: null, count: 2 },
  ],
  untagged: 1,
};

const ROOT = "D:\\Pictures";
const FOLDERS = {
  folders: [
    { path: ROOT, name: "Pictures", parent: null, direct: 0, recursive: 6 },
    { path: `${ROOT}\\2019`, name: "2019", parent: ROOT, direct: 3, recursive: 3 },
    { path: `${ROOT}\\Rowan's Meet`, name: "Rowan's Meet", parent: ROOT, direct: 3, recursive: 3 },
  ],
};

const DATES = { years: [{ year: 2024, count: 6, months: [{ month: "2024-06", count: 6 }], other: 0 }], undated: 0 };

/**
 * What a search finds, by what it says: a source ANDed with another as `&` must finds nothing (no photo of the fixture
 * has two), else one photo fewer for each part, the whole library six.
 */
function foundBy(value) {
  const all = value.all_of || [];
  if (all.filter((member) => member.kind !== "any_of").length >= 2) return [];
  const count = Math.max(1, 6 - all.length - (value.none_of || []).length);
  return Array.from({ length: count }, (_, i) => i + 1);
}

/**
 * A library: its four sections, its ids (a search's by foundBy, the whole library's six) and cards. `hold` keeps each
 * search's reply until `release(order)`; `people` answers the People section (a function for one that fails).
 */
function library({ hold = false, people = PEOPLE } = {}) {
  const held = [];
  const server = new FakeServer()
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/library/navigator?section=people", people)
    .on("/api/library/navigator?section=keywords", KEYWORDS)
    .on("/api/library/navigator?section=folders", FOLDERS)
    .on("/api/library/navigator?section=dates", DATES)
    .on("/api/library/ids", (url, body) => {
      const ids = body && body.kind === "search" ? foundBy(body.value) : [1, 2, 3, 4, 5, 6];
      const reply = { ids, total: ids.length, of: ids.length, videos: [] };
      return hold && body ? new Promise((resolve) => held.push(() => resolve(reply))) : reply;
    })
    .on("/api/library/cards", (url) => {
      const ids = decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").map(Number);
      return { cards: ids.map((id) => phoneCard(id)) };
    });
  return Object.assign(server, { held });
}

async function phone(t, { url = PHONE_PAGE, server = library() } = {}) {
  const ctx = await loadApp("phone", { t, server, url });
  phoneLayout(ctx);
  return ctx;
}

const sheetOpen = (ctx) => !el(ctx, "filter-sheet").classList.contains("hidden");
const rows = (ctx) => [...el(ctx, "filter-rows").querySelectorAll(".filter-row")];
const labels = (ctx) => rows(ctx).map((r) => r.querySelector(".filter-row-label").textContent);
const row = (ctx, label) => rows(ctx).find((r) => r.querySelector(".filter-row-label").textContent === label);
const markOn = (ctx, label) => row(ctx, label).dataset.sign;
/** The chips above the grid, past the Filter chip: "<mark> <name>". */
const chips = (ctx) => [...el(ctx, "grid-chips").querySelectorAll(".grid-chip:not(.grid-chip-filter)")]
  .map((c) => `${c.querySelector(".grid-chip-mark").textContent} ${c.querySelector(".grid-chip-text").textContent}`);
const filterChip = (ctx) => el(ctx, "grid-chips").querySelector(".grid-chip-filter");
/** The searches asked of the server, as it reads them (the body of POST /api/library/ids). */
const searches = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/library/ids") && c.body && c.body.kind === "search")
  .map((c) => c.body.value);
const idsAsked = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/library/ids")).length;
const navigated = (ctx) => ctx.consoleErrors.some((e) => /navigation/i.test(e && e.message ? e.message : String(e)));

async function tap(ctx, element) {
  click(ctx.window, element);
  await settle(ctx);
}

/** A row of the tab shown tapped: its mark the next. Found again each time, as a finger finds the row drawn now. */
async function tapRow(ctx, label, times = 1) {
  for (let i = 0; i < times; i += 1) await tap(ctx, row(ctx, label).querySelector(".filter-row-mark"));
}

async function tabTo(ctx, name) {
  await tap(ctx, ctx.document.querySelector(`#filter-tabs [data-filter-tab="${name}"]`));
}

async function back(ctx) {
  ctx.window.history.back();
  await settle(ctx);
}

async function forward(ctx) {
  ctx.window.history.forward();
  await settle(ctx);
}

/** Browse's Filter tab: the Grid on the whole library and the sheet over it, its People read. */
async function openFilter(ctx) {
  await tap(ctx, tab(ctx, "filter"));
  await settle(ctx);
  assert.deepEqual(shown(ctx), ["grid"]);
  assert.ok(sheetOpen(ctx));
}

afterEach(() => closeAllApps());

describe("filtering the library on the phone", () => {
  test("two people and a keyword marked any of, then a keyword tapped round to not: one search, its chips, Show its count", async (t) => {
    const ctx = await phone(t);
    await openFilter(ctx);
    const steps = ctx.window.history.length;
    assert.deepEqual(labels(ctx), ["Ada Quill", "Sam Birch", "Wren Alder"]);
    assert.equal(el(ctx, "filter-show").textContent, "Show 6 photos", "the whole library, nothing marked");

    await tapRow(ctx, "Wren Alder");
    await tapRow(ctx, "Sam Birch");
    await tabTo(ctx, "keywords");
    assert.deepEqual(labels(ctx), ["Events", "Holidays", "Sports", "No tag"]);
    await tapRow(ctx, "Events");
    assert.deepEqual(searches(ctx).at(-1), {
      all_of: [
        { kind: "any_of", value: [{ kind: "keyword", value: "Events" }] },
        { kind: "any_of", value: [{ kind: "person", value: "People/Wren Alder" }, { kind: "person", value: "People/Sam Birch" }] },
      ],
    }, "each tab's any of is one union, the tabs ANDed");

    await tapRow(ctx, "Sports", 3);
    assert.equal(markOn(ctx, "Sports"), "-");
    assert.deepEqual(searches(ctx).at(-1).none_of, [{ kind: "keyword", value: "Sports" }]);
    assert.deepEqual(chips(ctx), ["+ Events", "+ Wren Alder", "+ Sam Birch", "- Sports"]);
    assert.equal(filterChip(ctx).querySelector(".grid-chip-count").textContent, "4");
    assert.equal(el(ctx, "filter-show").textContent, "Show 3 photos");
    assert.equal(ctx.window.history.length, steps, "a mark changes the Grid's place, it adds no step");

    await tap(ctx, el(ctx, "filter-show"));
    assert.ok(!sheetOpen(ctx));
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(el(ctx, "grid-count").textContent, "3 photos");
    assert.deepEqual(chips(ctx), ["+ Events", "+ Wren Alder", "+ Sam Birch", "- Sports"], "the filter stays on the Grid");
  });

  test("rapid taps on one row cycle it to & in one step, the reply for + dropped", async (t) => {
    const server = library({ hold: true });
    const ctx = await phone(t, { server });
    await openFilter(ctx);
    const steps = ctx.window.history.length;

    click(ctx.window, row(ctx, "Wren Alder").querySelector(".filter-row-mark"));
    click(ctx.window, row(ctx, "Wren Alder").querySelector(".filter-row-mark"));
    await settle(ctx);
    assert.equal(markOn(ctx, "Wren Alder"), "&", "off, +, & from the last mark each time");
    assert.equal(ctx.window.history.length, steps, "one place of history");
    assert.deepEqual(searches(ctx).map((each) => each.all_of[0].kind), ["any_of", "person"]);

    server.held.at(-1)();       // the reply for & first, then the one for + it left
    await settle(ctx);
    server.held.at(0)();
    await settle(ctx);
    assert.equal(el(ctx, "grid-count").textContent, "5 photos", "the count of &, not of +");
    assert.deepEqual(chips(ctx), ["& Wren Alder"]);
  });

  test("Back closes the sheet keeping the filter, a second Back leaves the Grid, Forward gives the marks back", async (t) => {
    const ctx = await phone(t);
    await openFilter(ctx);
    await tapRow(ctx, "Wren Alder");
    await tabTo(ctx, "keywords");
    await tapRow(ctx, "Events", 2);

    await back(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.ok(!sheetOpen(ctx), "Back closes the sheet first");
    assert.deepEqual(chips(ctx), ["& Events", "+ Wren Alder"]);
    await back(ctx);
    assert.deepEqual(shown(ctx), ["browse"], "a second Back leaves the Grid");

    await forward(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.ok(!sheetOpen(ctx));
    assert.deepEqual(chips(ctx), ["& Events", "+ Wren Alder"], "the marks, from the Grid's step");
    assert.deepEqual(searches(ctx).at(-1).all_of.map((each) => each.kind), ["keyword", "any_of"], "read again as it was");

    await tap(ctx, filterChip(ctx));
    assert.ok(sheetOpen(ctx));
    assert.equal(markOn(ctx, "Events"), "&");
    await tabTo(ctx, "people");
    assert.equal(markOn(ctx, "Wren Alder"), "+");
    assert.equal(markOn(ctx, "Sam Birch"), "");
  });

  test("a filter finding nothing says so with its chips: Remove the last, then Clear all; 0 photos, Show not pressable", async (t) => {
    const ctx = await phone(t);
    await openFilter(ctx);
    await tabTo(ctx, "keywords");
    await tapRow(ctx, "Events", 2);
    await tapRow(ctx, "Sports", 2);
    await tabTo(ctx, "people");
    await tapRow(ctx, "Wren Alder", 2);

    assert.equal(el(ctx, "grid-count").textContent, "0 photos");
    assert.equal(el(ctx, "filter-show").textContent, "No photos");
    assert.ok(el(ctx, "filter-show").disabled);
    await tapRow(ctx, "Ada Quill");
    assert.equal(markOn(ctx, "Ada Quill"), "+", "the marks are still changed while it finds none");
    await tapRow(ctx, "Ada Quill", 3);
    assert.equal(markOn(ctx, "Ada Quill"), "");

    await tap(ctx, el(ctx, "filter-scrim"));
    assert.ok(!sheetOpen(ctx));
    assert.ok(!el(ctx, "grid-none").classList.contains("hidden"), "never an empty grid with no words");
    // In the filter's order (navigator-model.js specFromRowStates): Sports was marked + after Events was &, a tab's
    // any of before its musts, and kept its place when it became & too.
    assert.equal(el(ctx, "grid-none-chips").textContent, "& Sports, & Events, & Wren Alder");
    assert.equal(el(ctx, "grid-none-remove").textContent, "Remove Wren Alder");

    await tap(ctx, el(ctx, "grid-none-remove"));
    assert.deepEqual(chips(ctx), ["& Sports", "& Events"]);
    assert.equal(el(ctx, "grid-count").textContent, "0 photos");
    assert.equal(el(ctx, "grid-none-remove").textContent, "Remove Events");

    await tap(ctx, el(ctx, "grid-none-clear"));
    assert.deepEqual(chips(ctx), []);
    assert.equal(pageState(ctx).place.kind, "all");
    assert.ok(el(ctx, "grid-none").classList.contains("hidden"));
    assert.equal(el(ctx, "grid-count").textContent, "6 photos");
    assert.equal(filterChip(ctx).getAttribute("aria-label"), "Filter");
  });

  test("an address naming a keyword deleted since gives a struck chip", async (t) => {
    const value = JSON.stringify({ all_of: [{ kind: "keyword", value: "Events/Gone" }, { kind: "any_of", value: [{ kind: "keyword", value: "Sports" }] }] });
    const ctx = await phone(t, { url: `${PHONE_PAGE}?view=search&value=${encodeURIComponent(value)}` });

    assert.deepEqual(shown(ctx), ["grid"]);
    assert.deepEqual(chips(ctx), ["& Events/Gone", "+ Sports"]);
    const [gone, kept] = el(ctx, "grid-chips").querySelectorAll(".grid-chip:not(.grid-chip-filter)");
    assert.ok(gone.classList.contains("grid-chip-unknown"), "the library has no such tag now");
    assert.match(gone.title, /the library has no such tag now/);
    assert.ok(!kept.classList.contains("grid-chip-unknown"));

    await tap(ctx, gone);
    assert.deepEqual(chips(ctx), ["+ Sports"], "a struck chip is taken off as any other");
  });

  test("another library is another page: no chip of the first, every request to the other", async (t) => {
    const alpha = await phone(t);
    await openFilter(alpha);
    await tapRow(alpha, "Wren Alder");
    await alpha.window.history.back();
    await settle(alpha);
    assert.deepEqual(chips(alpha), ["+ Wren Alder"]);

    await tap(alpha, tab(alpha, "library"));
    const other = [...el(alpha, "library-list").querySelectorAll(".library-choice")].find((b) => b.textContent === "beta");
    await tap(alpha, other);
    assert.ok(navigated(alpha), "another library is another page");
    assert.equal(pageExports(alpha.window, "web/common/library.js").libraryAddress("beta"), "/beta/mobile.html",
      "the filter is not carried in the address");

    const beta = await phone(t, { url: BETA_PAGE });
    await tap(beta, tab(beta, "grid"));
    assert.deepEqual(chips(beta), []);
    assert.equal(pageState(beta).filter.size, 0);
    await tap(beta, filterChip(beta));
    await tapRow(beta, "Sam Birch");
    assert.deepEqual(chips(beta), ["+ Sam Birch"]);
    const asked = beta.server.urls().filter((u) => u.includes("/api/"));
    assert.ok(asked.length > 0 && asked.every((u) => u.startsWith("/beta/api/") || u.startsWith("/api/databases")), asked.join(" "));
    assert.ok(alpha.server.urls().every((u) => !u.startsWith("/beta/")));
  });

  test("a tab whose section could not be read says so with Try again, and Try again draws its rows", async (t) => {
    let failing = true;
    const server = library({ people: () => (failing ? answer(503, { error: "The library is busy." }) : PEOPLE) });
    const ctx = await phone(t, { server, url: `${PHONE_PAGE}?view=all` });
    assert.deepEqual(shown(ctx), ["grid"]);

    await tap(ctx, filterChip(ctx));
    assert.ok(sheetOpen(ctx));
    assert.match(el(ctx, "filter-said").textContent, /^Could not load people\./);
    assert.ok(el(ctx, "filter-said").classList.contains("problem-text"));
    assert.ok(!el(ctx, "filter-retry").classList.contains("hidden"));
    assert.deepEqual(labels(ctx), []);

    failing = false;
    await tap(ctx, el(ctx, "filter-retry"));
    assert.deepEqual(labels(ctx), ["Ada Quill", "Sam Birch", "Wren Alder"]);
    assert.equal(el(ctx, "filter-said").textContent, "");
    assert.ok(el(ctx, "filter-retry").classList.contains("hidden"));
    await tapRow(ctx, "Ada Quill");
    assert.deepEqual(chips(ctx), ["+ Ada Quill"]);
  });

  test("a filter at the server's cap of rows refuses one more with the desktop's sentence and reads nothing", async (t) => {
    const ctx = await phone(t);
    await openFilter(ctx);
    await tabTo(ctx, "folders");
    await tap(ctx, row(ctx, "Pictures").querySelector(".filter-row-open"));
    const model = pageExports(ctx.window, "web/tagpup/navigator-model.js");
    const { viewOf } = pageExports(ctx.window, "web/tagpup/mobile-browse.js");
    const { replacePlace } = pageExports(ctx.window, "web/tagpup/mobile-screens.js");
    const marks = new Map();
    for (let i = 0; i < 1000; i += 1) marks.set(model.filterKey({ kind: "folder", value: `E:\\Old\\${i}`, recursive: true }), "-");
    marks.set(model.filterKey({ kind: "folder", value: `${ROOT}\\2019`, recursive: true }), "&");
    replacePlace(viewOf({ ...model.specFromRowStates(marks), order: "taken", media: "both" }));
    await settle(ctx);
    assert.equal(pageState(ctx).filter.size, 1001);
    assert.equal(markOn(ctx, "2019"), "&");
    const asked = idsAsked(ctx);
    const place = JSON.stringify(pageState(ctx).place);

    await tapRow(ctx, "2019");
    assert.equal(el(ctx, "filter-said").textContent,
      "A filter holds at most 1,000 rows marked - not: take one off before marking another.");
    assert.ok(el(ctx, "filter-said").classList.contains("problem-text"));
    assert.equal(markOn(ctx, "2019"), "&", "the mark stays as it was");
    assert.equal(idsAsked(ctx), asked, "no read made");
    assert.equal(JSON.stringify(pageState(ctx).place), place);
  });

  test("no cover is asked while the sheet is open", async (t) => {
    const ctx = await phone(t);
    await settle(ctx);
    const covers = () => ctx.server.urls().filter((u) => u.includes("/api/library/covers")).length;
    const before = covers();
    await openFilter(ctx);

    for (const name of ["people", "keywords", "folders", "dates"]) {
      await tabTo(ctx, name);
      await intersect(ctx.window, observed(ctx.window));     // whatever the page watches, come near the screen
    }
    await tapRow(ctx, "2024");
    assert.equal(covers(), before, "the sheet's rows have no covers, and Browse asks none under it");
  });

  test("six chips at 320 px are one row scrolled sideways, never wrapped", async (t) => {
    const ctx = await phone(t);
    ctx.window.innerWidth = 320;
    await openFilter(ctx);
    await tapRow(ctx, "Wren Alder");
    await tapRow(ctx, "Sam Birch");
    await tapRow(ctx, "Ada Quill", 3);
    await tabTo(ctx, "keywords");
    await tapRow(ctx, "Events");
    await tapRow(ctx, "Holidays", 2);
    await tabTo(ctx, "dates");
    await tapRow(ctx, "2024");
    await back(ctx);

    const row6 = el(ctx, "grid-chips");
    assert.equal(row6.querySelectorAll(".grid-chip:not(.grid-chip-filter)").length, 6);
    assert.ok([...row6.children].every((c) => c.classList.contains("grid-chip")), "one row holds them all, Filter first");
    assert.equal(row6.children[0], filterChip(ctx));
    const css = fs.readFileSync(path.join(HERE, "../../web/tagpup/mobile.css"), "utf8");
    const rule = (selector) => (css.match(new RegExp(`(^|\\n)${selector.replace(".", "\\.")} \\{([^}]*)\\}`)) || [])[2] || "";
    assert.match(rule(".grid-chips"), /flex-wrap: nowrap;/);
    assert.match(rule(".grid-chips"), /overflow-x: auto;/);
    assert.match(rule(".grid-chips"), /position: sticky;/);
    assert.match(rule(".grid-chip"), /flex: none;/, "a chip keeps its width rather than squeezing");
    assert.match(rule(".grid-chip"), /white-space: nowrap;/);
  });
});
