/**
 * V28: somebody turns on the navigator's Filter mode and marks rows of any tab -- `+` any of, `&` must, `-` not -- and the marks
 * are one search (docs/plans/advanced-filtering.md, cut 1a: navigator-model.js specFromRowStates, rowStatesFromSpec). The
 * search goes into the address, and the address read back (Back, a bookmark) gives the same marks; a search made elsewhere --
 * words typed, an Any-of list, the search page's Within -- marks nothing; a view of a row's source, or of a union of one tab's
 * rows (a people group's), is those rows marked `+`, the same photos. tests/fixtures/filter_searches.json holds the values
 * the page sends for the owner's examples, and tests/test_filtering_the_library_by_rows.py the photos the server finds for them.
 * Every name is fictional.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { closeAllApps } from "./harness.mjs";
import { loadViewPage, pageErrors, syntheticDates, syntheticFolders, syntheticKeywords, syntheticPeople } from "./view-page.mjs";

const TABLE = JSON.parse(readFileSync(new URL("../fixtures/filter_searches.json", import.meta.url), "utf-8"));
const YEAR = new Date().getFullYear();

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

/** The page's model and its address, as the page holds them. */
async function model(t) {
  const ctx = await loadViewPage(t);
  return { ...ctx.module("navigator-model.js"), ...ctx.module("library-source.js") };
}

/** A filter's rows: [source, state] in the order marked, as the Map the page keeps. */
const statesOf = (m, rows) => new Map(rows.map(([member, state]) => [m.filterKey(member), state]));
/** A filter's marks to compare, whatever order they were read in (null stays null). */
const marks = (states) => (states === null ? null : JSON.stringify([...states].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))));
/** The search a view's address gives back: Back, Forward, a bookmark. */
const throughTheAddress = (m, spec) => m.viewSpecFromSearch(m.viewSearch(spec));

const ROWAN = { kind: "person", value: "People/Rowan Thackeray", recursive: false };
const ADA = { kind: "person", value: "People/Ada Lindqvist", recursive: false };
const TRIPS = { kind: "folder", value: "E:\\Pictures\\Trips", recursive: true };
const BEACH = { kind: "keyword", value: "Places/Beach", recursive: false };
const UNTAGGED = { kind: "untagged", value: null, recursive: false };
const Y2019 = { kind: "year", value: "2019", recursive: false };
const AUGUST = { kind: "month", value: "2019-08", recursive: false };
const UNDATED_2020 = { kind: "year_other", value: "2020", recursive: false };
const search = (value) => ({ kind: "search", value, recursive: false });

describe("the owner marks rows and the grid is the search they say", () => {
  test("the owner's examples are the searches the server is held to, and the address gives their marks back", async (t) => {
    const m = await model(t);
    for (const each of TABLE.cases) {
      const states = statesOf(m, each.rows);
      const spec = m.specFromRowStates(states);
      assert.equal(spec.kind, "search", each.name);
      assert.deepEqual(JSON.parse(m.searchText(spec.value)), each.value, `${each.name}: the value the page sends`);
      const back = throughTheAddress(m, spec);
      assert.equal(back.error, undefined, `${each.name}: ${back.error}`);
      assert.equal(marks(m.rowStatesFromSpec(back)), marks(states), `${each.name}: Back restores the marks`);
      assert.equal(m.searchText(m.specFromRowStates(m.rowStatesFromSpec(back)).value), m.searchText(spec.value),
        `${each.name}: a click after Back keeps the address`);
    }
  });

  test("every mark of every row of every tab survives the address and back", async (t) => {
    const m = await model(t);
    // A row of each tab, two people (a union of two), a month and a year's Other (two of the dates tab): 4^7 filters.
    const rows = [TRIPS, BEACH, UNTAGGED, ROWAN, ADA, AUGUST, UNDATED_2020];
    const STATES = [null, "+", "&", "-"];
    let filters = 0;
    for (let combination = 0; combination < STATES.length ** rows.length; combination++) {
      const states = new Map();
      let code = combination;
      for (const row of rows) {
        const state = STATES[code % STATES.length];
        code = Math.floor(code / STATES.length);
        if (state) states.set(m.filterKey(row), state);
      }
      const spec = m.specFromRowStates(states);
      if (!states.size) {
        assert.equal(spec, null, "no row marked is no filter");
        continue;
      }
      filters++;
      const counted = { "+": new Set(), "&": 0, "-": 0 };
      for (const [key, state] of states) {
        if (state === "+") counted["+"].add(m.sectionOf(m.filterMember(key)));
        else counted[state]++;
      }
      const label = JSON.stringify([...states]);
      assert.equal((spec.value.all_of || []).length, counted["+"].size + counted["&"], `${label}: one union per tab, each & its own`);
      assert.equal((spec.value.none_of || []).length, counted["-"], `${label}: each - in none_of`);
      assert.ok(!spec.value.any_of && !spec.value.words, `${label}: no Any-of list, no words`);
      assert.equal(marks(m.rowStatesFromSpec(throughTheAddress(m, spec))), marks(states), `${label}: through the address`);
    }
    assert.equal(filters, STATES.length ** rows.length - 1);
  });

  test("every row a tab draws is one a filter can mark", async (t) => {
    const m = await model(t);
    const indexes = {
      folders: m.indexFolders(syntheticFolders()),
      keywords: m.indexKeywords(syntheticKeywords(), 12),
      people: m.indexPeople(syntheticPeople()),
      dates: m.indexDates(syntheticDates(YEAR)),
    };
    for (const [section, index] of Object.entries(indexes)) {
      const tree = m.rowTree(section, index);
      let rows = 0;
      for (const whole of tree.whole.values()) {
        if (!whole) continue;   // a row that only groups (a branch of people, the junk years) is never marked
        rows++;
        const states = new Map([[m.filterKey(whole), "&"]]);
        assert.equal(marks(m.rowStatesFromSpec(m.specFromRowStates(states))), marks(states), `${section}: ${whole.kind}`);
      }
      assert.ok(rows > 0, `${section} has rows`);
    }
  });

  test("a bookmark naming a tag deleted since keeps its mark, and a year spelt as a number is the same row", async (t) => {
    const m = await model(t);
    const gone = { kind: "keyword", value: "Places/Lighthouse", recursive: false };
    assert.ok(!m.indexKeywords(syntheticKeywords(), 0).byTag.has(gone.value), "the tag is not in the library");
    const address = `?view=search&value=${encodeURIComponent(JSON.stringify({ all_of: [{ kind: "keyword", value: gone.value }], none_of: [{ kind: "person", value: ROWAN.value }] }))}`;
    const states = m.rowStatesFromSpec(m.viewSpecFromSearch(address));
    assert.equal(marks(states), marks(new Map([[m.filterKey(gone), "&"], [m.filterKey(ROWAN), "-"]])));
    assert.equal(m.viewSearch(m.specFromRowStates(states)), m.viewSearch(m.viewSpecFromSearch(address)), "the same address");
    assert.equal(m.filterKey({ kind: "year", value: 2019 }), m.filterKey(Y2019));
  });
});

describe("a search made elsewhere marks no row", () => {
  test("words, an Any-of list, Within's union of two tabs, and a source no row is are no filter", async (t) => {
    const m = await model(t);
    const alone = { kind: "folder", value: TRIPS.value, recursive: false };
    const notFilters = {
      "words typed": search({ all_of: [ROWAN], words: "beach" }),
      "an Any-of list": search({ any_of: [ROWAN, ADA] }),
      "Within: a union of a folder and a tag": search({ all_of: [{ kind: "any_of", value: [TRIPS, BEACH], recursive: false }] }),
      "two unions of one tab": search({ all_of: [{ kind: "any_of", value: [ROWAN], recursive: false }, { kind: "any_of", value: [ADA], recursive: false }] }),
      "a folder without its subfolders": search({ all_of: [alone] }),
      "a keyword without the tags under it": search({ all_of: [{ kind: "keyword_only", value: BEACH.value, recursive: false }] }),
      "the whole library as a member": search({ all_of: [{ kind: "all", value: null, recursive: false }, ROWAN] }),
      "a union in none_of": search({ none_of: [{ kind: "any_of", value: [ROWAN, ADA], recursive: false }] }),
      "a person both must and not": search({ all_of: [ROWAN], none_of: [ROWAN] }),
      "a person both any of and must": search({ all_of: [{ kind: "any_of", value: [ROWAN], recursive: false }, ROWAN] }),
      "a year as a number and as text, must and not": search({ all_of: [Y2019], none_of: [{ kind: "year", value: 2019, recursive: false }] }),
    };
    for (const [name, spec] of Object.entries(notFilters)) {
      assert.equal(m.rowStatesFromSpec(spec), null, name);
      const back = throughTheAddress(m, spec);
      if (!back.error) assert.equal(m.rowStatesFromSpec(back), null, `${name}, through the address`);
    }
  });

  test("the whole library, a source no row is, a union of two tabs, or nothing at all, marks nothing and throws nothing", async (t) => {
    const m = await model(t);
    for (const spec of [undefined, null, {}, { kind: "search" }, { kind: "search", value: null }, search({}),
      { kind: "folder", value: TRIPS.value, recursive: false }, { kind: "keyword_only", value: BEACH.value, recursive: false },
      { kind: "any_of", value: [ROWAN, TRIPS], recursive: false }, { kind: "any_of", value: [ROWAN, ROWAN], recursive: false },
      { kind: "any_of", value: [], recursive: false }, { kind: "all", value: null, recursive: false }]) {
      assert.equal(m.rowStatesFromSpec(spec), null, JSON.stringify(spec));
    }
    assert.equal(m.specFromRowStates(undefined), null);
    assert.equal(m.specFromRowStates(new Map()), null);
  });

  test("a view of a row's source, or a union of one tab's rows (a people group's), is those rows marked any of", async (t) => {
    const m = await model(t);
    const views = {
      "a person": [ROWAN, [[ROWAN, "+"]]],
      "a folder with its subfolders": [TRIPS, [[TRIPS, "+"]]],
      "a keyword": [BEACH, [[BEACH, "+"]]],
      "No tag": [UNTAGGED, [[UNTAGGED, "+"]]],
      "a group's people": [{ kind: "any_of", value: [ROWAN, ADA], recursive: false }, [[ROWAN, "+"], [ADA, "+"]]],
    };
    for (const [name, [view, rows]] of Object.entries(views)) {
      const states = m.rowStatesFromSpec(view);
      assert.equal(marks(states), marks(statesOf(m, rows)), name);
      assert.equal(m.rowStatesFromSpec(throughTheAddress(m, view)) && marks(m.rowStatesFromSpec(throughTheAddress(m, view))),
        marks(states), `${name}, through the address`);
      // The same photos as a filter: the tab's union of those rows, and read back the same marks.
      const spec = m.specFromRowStates(states);
      assert.deepEqual(JSON.parse(JSON.stringify(spec.value.all_of)).map((each) => each.kind), ["any_of"], name);
      assert.equal(marks(m.rowStatesFromSpec(spec)), marks(states), `${name}, as a search`);
    }
  });
});
