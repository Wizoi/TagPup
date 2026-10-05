/**
 * Phase 9e-2: the search box of the Library pane (web/tagpup/search.js, search-model.js; a search is a view of kind `search`,
 * library-source.js). Words in one box and Enter; All of / Any of / None of, each a list of the library's tags and people picked
 * by a combobox; Within the sidebar's selection. The address holds the search, so Back, Forward and a bookmark restore it, the
 * box and the chips with it. How it fails: the vocabulary changing while the picker is open, a person named like a branch, a
 * bookmark's tag that is gone, an address too long, a list of 1,000, words of punctuation only, the server's 400 and 503, a
 * later search overtaking an earlier one. Every name is fictional; the real library is photographs of real people.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, flush } from "./harness.mjs";
import { loadViewPage, pageErrors, speedUp } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const YEAR = new Date().getFullYear();

/** The navigator's keywords, as GET /api/library/navigator?section=keywords answers: a People branch with two people in it. */
const KEYWORDS = () => [
  { tag: "People", name: "People", parent: null, count: 30 },
  { tag: "People/Family", name: "Family", parent: "People", count: 20 },
  { tag: "People/Family/Rowan Thackeray", name: "Rowan Thackeray", parent: "People/Family", count: 12 },
  { tag: "People/Family/Hazel Brookmire", name: "Hazel Brookmire", parent: "People/Family", count: 9 },
  { tag: "Trips", name: "Trips", parent: null, count: 50 },
  { tag: "Trips/Coast", name: "Coast", parent: "Trips", count: 40 },
  { tag: "Trips/Lighthouse", name: "Lighthouse", parent: "Trips", count: 6 },
  { tag: "Places", name: "Places", parent: null, count: 8 },
  { tag: "Places/Home", name: "Home", parent: "Places", count: 8 },
];

/**
 * The navigator's people, as the route answers them (tagpup.services.library_view.navigator): the filed people under their
 * branch, and "Family" -- the leaf of a branch, listed because a photo is tagged People/Family -- not filed, as the route says.
 */
const PEOPLE = () => ({
  people: [
    { name: "Rowan Thackeray", count: 12, group: "People/Family" },
    { name: "Hazel Brookmire", count: 9, group: "People/Family" },
    { name: "Family", count: 3, group: null },
    { name: "Wren Halloway", count: 4, group: null },
  ],
  groups: [{ tag: "People/Family", name: "Family", parent: null, count: 20 }],
  unfiled: 7,
});

const NAVIGATOR = () => ({ keywords: { keywords: KEYWORDS() }, people: PEOPLE() });

/** The ids requests the page sent, with their bodies. */
const idsCalls = (ctx) => ctx.server.calls.filter((call) => call.url.includes("/api/library/ids"));
/** The last search asked: its body (a search is always POSTed). */
function lastSearch(ctx) {
  const call = idsCalls(ctx).at(-1);
  assert.equal(call.method, "POST", "a search goes in a body");
  assert.equal(call.body.kind, "search");
  return call.body;
}

const words = (ctx) => ctx.document.getElementById("library-search-words");
const note = (ctx) => ctx.document.getElementById("library-search-note");
const pick = (ctx, list) => ctx.document.querySelector(`.library-search-list[data-list="${list}"] .library-search-pick`);
const options = (ctx, list) => [...ctx.document.querySelectorAll(`#library-search-options-${list} [role="option"]`)];
const optionLabels = (ctx, list) => options(ctx, list).map((o) => o.querySelector(".library-search-option-label").textContent);
const chips = (ctx, list) => [...ctx.document.querySelectorAll(`.library-search-list[data-list="${list}"] .library-search-chip`)];
const chipTexts = (ctx, list) => chips(ctx, list).map((c) => c.querySelector(".library-search-chip-text").textContent);
const valueInAddress = (ctx) => JSON.parse(new URLSearchParams(ctx.window.location.search).get("value"));

async function search(ctx, text) {
  words(ctx).value = text;
  words(ctx).dispatchEvent(new ctx.window.Event("input"));
  ctx.key(words(ctx), "Enter");
  await ctx.settle();
}

async function type(ctx, list, text) {
  const box = pick(ctx, list);
  box.focus();
  box.value = text;
  box.dispatchEvent(new ctx.window.Event("input"));
  await ctx.settle(40);
}

describe("words, Enter, and the address", () => {
  test("Enter searches the words: one POST, the address holds it, the header names it; Back and Forward restore the box", async (t) => {
    const ctx = await loadViewPage(t, { search: `?view=year&value=${YEAR - 1}` });
    const before = idsCalls(ctx).length;
    words(ctx).value = "beach";
    words(ctx).dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle();
    assert.equal(idsCalls(ctx).length, before, "typing searches nothing: Enter does");
    ctx.key(words(ctx), "Enter");
    await ctx.settle();
    assert.equal(idsCalls(ctx).length, before + 1);
    assert.deepEqual(lastSearch(ctx).value, { words: "beach" });
    assert.equal(new URLSearchParams(ctx.window.location.search).get("view"), "search");
    assert.deepEqual(valueInAddress(ctx), { words: "beach" });
    assert.match(ctx.stripText(), /Search: “beach”/);
    assert.equal(ctx.state.library.status, "ready");
    assert.equal(ctx.real().length > 0 || ctx.cards().length > 0, true, "the result is in the grid");
    // Back: the year, the box empty; Forward: the search, the words back in the box.
    const searched = ctx.window.location.search;
    await ctx.popTo(`?view=year&value=${YEAR - 1}`);
    assert.equal(ctx.state.library.kind, "year");
    assert.equal(words(ctx).value, "");
    await ctx.popTo(searched);
    assert.equal(ctx.state.library.kind, "search");
    assert.equal(words(ctx).value, "beach");
  });

  test("a bookmark of a search opens it, the box and the chips holding it; its parts are sent in their one order", async (t) => {
    const value = { words: "sand dunes", none_of: [{ kind: "person", value: "Wren Halloway" }], all_of: [{ kind: "keyword", value: "Trips/Coast" }] };
    const ctx = await loadViewPage(t, { search: `?view=search&value=${encodeURIComponent(JSON.stringify(value))}&order=name`, navigator: NAVIGATOR() });
    assert.equal(ctx.state.library.kind, "search");
    assert.equal(words(ctx).value, "sand dunes");
    assert.deepEqual(chipTexts(ctx, "all_of"), ["Trips/Coast"]);
    assert.deepEqual(chipTexts(ctx, "none_of"), ["Wren Halloway"]);
    assert.deepEqual(Object.keys(lastSearch(ctx).value), ["all_of", "none_of", "words"]);
    assert.equal(lastSearch(ctx).order, "name");
    assert.equal(ctx.document.getElementById("btn-library-search-filters").getAttribute("aria-expanded"), "true", "the filters show their chips");
  });

  test("rapid Enter: the same words twice ask once; new words while the first answer is out -- the later search wins", async (t) => {
    let ctx = null;
    const onIds = () => {
      const body = ctx && ctx.server.lastBody("/api/library/ids");
      const asked = body && body.value && body.value.words;
      const ids = asked === "beach" ? [1, 2, 3] : asked === "sand" ? [7, 8] : [11, 12, 13];
      return { source: {}, total: ids.length, ids, complete: true };
    };
    ctx = await loadViewPage(t, { search: "?view=all", onIds });
    await search(ctx, "beach");
    const asked = idsCalls(ctx).length;
    await search(ctx, "beach");
    assert.equal(idsCalls(ctx).length, asked, "the same search again asks nothing");
    ctx.hold.ids = true;
    words(ctx).value = "beaches";
    ctx.key(words(ctx), "Enter");
    words(ctx).value = "sand";
    ctx.key(words(ctx), "Enter");
    ctx.hold.ids = false;
    await ctx.release("ids");
    await ctx.settle();
    assert.equal(ctx.state.library.value.words, "sand");
    assert.deepEqual(ctx.state.library.ids, [7, 8], "the earlier answer is dropped");
    assert.equal(words(ctx).value, "sand");
    // Each change of a search open replaced it: Back is the view before the first search.
    assert.equal(ctx.window.history.state && ctx.window.history.state.searchBack, 1);
  });

  test("a search with no results says so, in the strip and the grid", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", onIds: () => ({ source: {}, total: 0, ids: [], complete: true }) });
    await search(ctx, "zeppelin");
    assert.equal(ctx.state.library.status, "empty");
    assert.match(ctx.stripText(), /Nothing in the library matches this search\./);
    assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /Nothing in the library matches this search\./);
  });

  test("words of only punctuation: a sentence and nothing sent; beside a chip they are left out, and said so", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: NAVIGATOR() });
    const before = idsCalls(ctx).length;
    await search(ctx, "- !! ?");
    assert.equal(idsCalls(ctx).length, before, "nothing asked");
    assert.match(note(ctx).textContent, /has no letter or digit/);
    assert.equal(ctx.state.library.kind, "all");
    // "..." is three characters: the server looks for it inside file names, so it is sent as typed.
    await search(ctx, "...");
    assert.deepEqual(lastSearch(ctx).value, { words: "..." });
    // With a tag beside them, the punctuation is left out.
    words(ctx).value = "--";
    await type(ctx, "all_of", "Coast");
    ctx.key(pick(ctx, "all_of"), "Enter");
    await ctx.settle();
    assert.deepEqual(lastSearch(ctx).value, { all_of: [{ kind: "keyword", value: "Trips/Coast" }] });
    assert.match(note(ctx).textContent, /left out of the search/);
  });

  test("Clear returns to the view before the search, even after a sort of it; a bookmarked search closes onto no view", async (t) => {
    const ctx = await loadViewPage(t, { search: `?view=month&value=${YEAR - 1}-07` });
    await search(ctx, "beach");
    // Sort by name: a new place in the history, two back from the month.
    ctx.document.getElementById("btn-sort-by").click();
    ctx.document.querySelector('#sort-menu [data-field="name"]').click();
    await ctx.settle();
    assert.equal(ctx.state.library.order, "name");
    assert.equal(ctx.window.history.state.searchBack, 2);
    const went = [];
    ctx.window.history.go = (delta) => went.push(delta);
    ctx.document.getElementById("btn-library-search-clear").click();
    assert.deepEqual(went, [-2], "back to the month");
    assert.equal(words(ctx).value, "");
  });

  test("a bookmarked search cleared: no view, the address loses it, the box is empty", async (t) => {
    const ctx = await loadViewPage(t, { search: `?view=search&value=${encodeURIComponent('{"words":"beach"}')}` });
    assert.equal(words(ctx).value, "beach");
    ctx.document.getElementById("btn-library-search-clear").click();
    await ctx.settle();
    assert.equal(ctx.state.library, null);
    assert.equal(new URLSearchParams(ctx.window.location.search).get("view"), null);
    assert.equal(words(ctx).value, "");
  });

  test("a Ctrl-click in the navigator while a search is open starts a selection, never a union holding the search", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.showLibraryPane();
    await search(ctx, "beach");
    await ctx.openTab("people");
    click(ctx.window, ctx.rows("people")[0], { ctrlKey: true });
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "person");
    assert.equal(words(ctx).value, "", "the search was left: the box is empty");
  });
});

describe("the picker: the library's tags and people", () => {
  test("people by name, tags as paths; a person's own node is not offered twice; a branch named like a person is never a person", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: NAVIGATOR() });
    ctx.document.getElementById("btn-library-search-filters").click();
    await type(ctx, "all_of", "row");
    assert.deepEqual(optionLabels(ctx, "all_of"), ["Rowan Thackeray"], "the person, by name, not People/Family/Rowan Thackeray");
    assert.equal(pick(ctx, "all_of").getAttribute("aria-expanded"), "true");
    await type(ctx, "all_of", "fam");
    assert.deepEqual(optionLabels(ctx, "all_of"), ["People/Family"], "the branch is a tag; its name is not offered as a person (#660)");
    ctx.key(pick(ctx, "all_of"), "Enter");
    await ctx.settle();
    assert.deepEqual(lastSearch(ctx).value, { all_of: [{ kind: "keyword", value: "People/Family" }] });
    assert.deepEqual(chipTexts(ctx, "all_of"), ["People/Family"]);
  });

  test("keys: ArrowDown and Enter add; Escape closes; Backspace on an empty box takes the last chip off and searches again", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: NAVIGATOR() });
    await type(ctx, "any_of", "h");
    const labels = optionLabels(ctx, "any_of");
    assert.ok(labels.includes("Hazel Brookmire") && labels.includes("Places/Home"), labels.join(", "));
    assert.equal(labels[0], "Hazel Brookmire", "a name that starts with it first, people first");
    ctx.key(pick(ctx, "any_of"), "ArrowDown");
    const second = labels[1];
    ctx.key(pick(ctx, "any_of"), "Enter");
    await ctx.settle();
    await type(ctx, "any_of", "wren");
    ctx.key(pick(ctx, "any_of"), "Enter");
    await ctx.settle();
    assert.deepEqual(chipTexts(ctx, "any_of"), [second, "Wren Halloway"]);
    assert.equal(lastSearch(ctx).value.any_of.length, 2);
    await type(ctx, "none_of", "lig");
    assert.equal(optionLabels(ctx, "none_of").length, 1);
    ctx.key(pick(ctx, "none_of"), "Escape");
    assert.ok(ctx.document.getElementById("library-search-options-none_of").classList.contains("hidden"));
    assert.equal(pick(ctx, "none_of").value, "lig", "Escape closes the list and keeps the text");
    ctx.key(pick(ctx, "none_of"), "Escape");
    assert.equal(pick(ctx, "none_of").value, "", "a second Escape empties the box");
    pick(ctx, "any_of").focus();
    ctx.key(pick(ctx, "any_of"), "Backspace");
    await ctx.settle();
    assert.deepEqual(chipTexts(ctx, "any_of"), [second]);
    assert.deepEqual(lastSearch(ctx).value, { any_of: [lastSearch(ctx).value.any_of[0]] });
    // The chip's x takes it off too: nothing left, the search is cleared.
    const went = [];
    ctx.window.history.go = (delta) => went.push(delta);
    chips(ctx, "any_of")[0].querySelector("button").click();
    await ctx.settle();
    assert.deepEqual(went, [-1]);
  });

  test("a name that is no tag or person of the library: the list says so, and Enter adds nothing", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: NAVIGATOR() });
    const before = idsCalls(ctx).length;
    await type(ctx, "all_of", "zeppelin");
    assert.match(ctx.document.getElementById("library-search-options-all_of").textContent, /No tag or person of this library is called “zeppelin”/);
    ctx.key(pick(ctx, "all_of"), "Enter");
    await ctx.settle();
    assert.equal(idsCalls(ctx).length, before);
    assert.match(note(ctx).textContent, /pick one from the list/);
  });

  test("the vocabulary changes while the picker is in use: a tag renamed in the tag editor is offered by its new name", async (t) => {
    const world = { keywords: KEYWORDS() };
    const ctx = await loadViewPage(t, {
      search: "?view=all", navigator: { keywords: () => ({ keywords: world.keywords }), people: PEOPLE() },
    });
    // The page's tag tree, as the tag editor reads it.
    const tree = [
      { id: 1, tag: "Trips", name: "Trips", parent_id: null, has_face: 0, hidden_from_autocomplete: 0, usage_count: 0 },
      { id: 2, tag: "Trips/Coast", name: "Coast", parent_id: 1, has_face: 0, hidden_from_autocomplete: 0, usage_count: 40 },
    ];
    ctx.server.first("/api/taxonomy/tree", () => JSON.parse(JSON.stringify(tree)));
    ctx.server.first("/api/taxonomy/rename", () => {
      tree[1] = { ...tree[1], tag: "Trips/Shore", name: "Shore" };
      world.keywords = KEYWORDS().map((k) => (k.tag === "Trips/Coast" ? { ...k, tag: "Trips/Shore", name: "Shore" } : k));
      return { success: true };
    });
    await ctx.module("tags.js").loadTaxonomy();
    await type(ctx, "all_of", "coa");
    assert.deepEqual(optionLabels(ctx, "all_of"), ["Trips/Coast"]);
    ctx.key(pick(ctx, "all_of"), "Enter");
    await ctx.settle();
    assert.deepEqual(chipTexts(ctx, "all_of"), ["Trips/Coast"]);
    await type(ctx, "all_of", "s");
    // The tag editor opens over the page (the picker loses the focus and closes), renames, and closes onto the picker's box.
    const editor = ctx.window.__pageModules["web/common/tag-editor.js"];
    editor.openTagEditor();
    await ctx.settle(20);
    await editor.renameTaxonomyNode(2, "Shore");
    await ctx.settle();
    editor.closeTagEditor();
    assert.equal(ctx.document.activeElement, pick(ctx, "all_of"), "the focus is back on the picker's box");
    pick(ctx, "all_of").dispatchEvent(new ctx.window.Event("focus"));
    await ctx.until(() => optionLabels(ctx, "all_of").includes("Trips/Shore"));
    assert.ok(!optionLabels(ctx, "all_of").includes("Trips/Coast"), "the old name is not offered");
    // The chip already added names a tag the library no longer has: it says so; the search answers what the library holds.
    const chip = chips(ctx, "all_of")[0];
    assert.ok(chip.classList.contains("library-search-chip-unknown"));
    assert.match(chip.title, /no such tag now/);
  });

  test("a bookmark's tag that no longer exists: the chip says so, the search is sent, the empty view says why", async (t) => {
    const value = { all_of: [{ kind: "keyword", value: "Trips/Pier" }, { kind: "person", value: "Rowan Thackeray" }] };
    const ctx = await loadViewPage(t, {
      search: `?view=search&value=${encodeURIComponent(JSON.stringify(value))}`, navigator: NAVIGATOR(),
      onIds: () => ({ source: {}, total: 0, ids: [], complete: true }),
    });
    await ctx.until(() => chips(ctx, "all_of").some((c) => c.classList.contains("library-search-chip-unknown")));
    const [pier, rowan] = chips(ctx, "all_of");
    assert.ok(pier.classList.contains("library-search-chip-unknown"));
    assert.ok(!rowan.classList.contains("library-search-chip-unknown"));
    assert.deepEqual(lastSearch(ctx).value.all_of.map((m) => m.value), ["Trips/Pier", "Rowan Thackeray"]);
    assert.match(ctx.stripText(), /Nothing in the library matches this search/);
  });

  test("names go into the page as text: a tag holding markup is shown as typed and makes no element", async (t) => {
    const keywords = [...KEYWORDS(), { tag: "Trips/<img src=x onerror=alert(1)>", name: "<img src=x onerror=alert(1)>", parent: "Trips", count: 2 }];
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: { keywords: { keywords }, people: PEOPLE() } });
    await type(ctx, "all_of", "<img");
    assert.deepEqual(optionLabels(ctx, "all_of"), ["Trips/<img src=x onerror=alert(1)>"]);
    ctx.key(pick(ctx, "all_of"), "Enter");
    await ctx.settle();
    assert.equal(ctx.document.querySelector("#library-search img"), null);
    assert.equal(ctx.document.querySelector("#library-strip img"), null);
    assert.deepEqual(chipTexts(ctx, "all_of"), ["Trips/<img src=x onerror=alert(1)>"]);
    assert.match(ctx.stripText(), /Search: all of Trips\/<img src=x onerror=alert\(1\)>/);
  });
});

describe("within the sidebar's selection", () => {
  test("ticked in a month's view: the month is the first of All of, a chip from then on", async (t) => {
    const ctx = await loadViewPage(t, { search: `?view=month&value=${YEAR - 1}-07`, navigator: NAVIGATOR() });
    const within = ctx.document.getElementById("library-search-within");
    assert.equal(within.disabled, false);
    assert.ok(!ctx.document.getElementById("library-search-within-row").classList.contains("hidden"), "offered under the box");
    assert.match(ctx.document.getElementById("library-search-within-name").textContent, /July/);
    within.checked = true;
    within.dispatchEvent(new ctx.window.Event("change"));
    await ctx.settle();
    assert.equal(idsCalls(ctx).at(-1).method, "GET", "ticked alone it searches nothing");
    await search(ctx, "beach");
    assert.deepEqual(lastSearch(ctx).value, { all_of: [{ kind: "month", value: `${YEAR - 1}-07` }], words: "beach" });
    assert.deepEqual(chipTexts(ctx, "all_of"), [`Within July ${YEAR - 1}`]);
    assert.equal(within.disabled, true, "a search is no selection of the navigator");
    assert.ok(ctx.document.getElementById("library-search-within-row").classList.contains("hidden"));
  });

  test("a union of many rows within, and an address it would make too long: the sentence, nothing sent", async (t) => {
    const share = "\\\\photo-server-in-the-hall\\family-pictures-archive";
    const folders = (n) => Array.from({ length: n }, (_, i) => ({
      kind: "folder", value: `${share}\\${String(i).padStart(4, "0")} ${"a long event name ".repeat(6)}`.trim(), recursive: true,
    }));
    // As many folders as a search within them for one short word holds: the union opens, and so does that search; a search
    // within it for a long word does not.
    let ctx = await loadViewPage(t, { search: "?view=all" });
    const source = ctx.module("library-source.js");
    const within = (n, words) => ({ kind: "search", value: source.searchValue({ all_of: [{ kind: "any_of", value: folders(n) }], words }), order: "taken" });
    let n = 420;
    while (source.addressTooLong(within(n, "beach"))) n -= 1;
    assert.ok(source.addressTooLong(within(n, "x".repeat(400))));
    closeAllApps();
    pageErrors();
    ctx = await loadViewPage(t, { search: `?view=any_of&value=${encodeURIComponent(JSON.stringify(folders(n)))}` });
    assert.equal(ctx.state.library.kind, "any_of");
    assert.equal(ctx.state.library.value.length, n);
    const box = ctx.document.getElementById("library-search-within");
    box.checked = true;
    box.dispatchEvent(new ctx.window.Event("change"));
    const before = idsCalls(ctx).length;
    await search(ctx, "x".repeat(400));
    assert.equal(idsCalls(ctx).length, before, "nothing asked");
    assert.match(note(ctx).textContent, /too long to name in the page’s address/);
    assert.equal(ctx.state.library.kind, "any_of", "the view is as it was");
    // A shorter search within it fits.
    await search(ctx, "beach");
    assert.equal(lastSearch(ctx).value.all_of[0].kind, "any_of");
    assert.equal(lastSearch(ctx).value.all_of[0].value.length, n);
  });
});

describe("long lists", () => {
  test("a bookmark of 1,000 people in Any of: one request, 50 chips and a count; a 1,001st is refused with a sentence", async (t) => {
    const people = Array.from({ length: 1000 }, (_, i) => ({ kind: "person", value: `Quill Ironside ${i + 1}` }));
    const ctx = await loadViewPage(t, {
      search: `?view=search&value=${encodeURIComponent(JSON.stringify({ any_of: people }))}`, navigator: NAVIGATOR(),
    });
    assert.equal(idsCalls(ctx).length, 1);
    assert.equal(lastSearch(ctx).value.any_of.length, 1000);
    assert.equal(chips(ctx, "any_of").length, 50);
    assert.match(ctx.document.querySelector('.library-search-list[data-list="any_of"] .library-search-more').textContent, /and 950 more/);
    await type(ctx, "any_of", "wren");
    ctx.key(pick(ctx, "any_of"), "Enter");
    await ctx.settle();
    assert.equal(idsCalls(ctx).length, 1, "nothing more asked");
    assert.match(note(ctx).textContent, /Any of holds at most 1,000/);
    assert.equal(ctx.state.search.lists.any_of.length, 1000);
  });

  test("an address of a search that cannot be one: a sentence, no request", async (t) => {
    for (const [value, said] of [
      ['{"nones_of":[]}', /part TagPup does not know/],
      ['{"all_of":[{"kind":"search","value":{}}]}', /cannot hold a search/],
      ["{}", /asks for nothing/],
      ["[1,2]", /cannot be read/],
      [JSON.stringify({ any_of: Array.from({ length: 1001 }, (_, i) => ({ kind: "year", value: String(1000 + i) })) }), /at most 1,000/],
    ]) {
      const ctx = await loadViewPage(t, { search: `?view=search&value=${encodeURIComponent(value)}` });
      assert.equal(idsCalls(ctx).length, 0, value.slice(0, 40));
      assert.match(ctx.stripText(), said);
      closeAllApps();
    }
  });
});

describe("what the server says", () => {
  test("400: the server's sentence by the box and in the strip", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    ctx.server.first("/api/library/ids", { success: false, error: "A search looks for at most 20 words at once; that is 21." }, { status: 400 });
    await search(ctx, Array.from({ length: 21 }, (_, i) => `w${i}`).join(" "));
    assert.equal(ctx.state.library.status, "error");
    assert.equal(note(ctx).textContent, "A search looks for at most 20 words at once; that is 21.");
    assert.ok(note(ctx).classList.contains("library-search-problem"));
    assert.match(ctx.stripText(), /at most 20 words/);
  });

  test("503 while the word index is being made: the sentence in the strip, asked again after Retry-After, then the result", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    speedUp(ctx);
    let refused = 0;
    const sentence = "The word index is being made now; try again in a few seconds.";
    ctx.server.first("/api/library/ids", () => {
      refused += 1;
      if (refused === 2) ctx.server.routes.shift();   // the third ask finds the index made
      return { success: false, error: sentence };
    }, { status: 503, headers: { "Retry-After": "5" } });
    words(ctx).value = "beach";
    ctx.key(words(ctx), "Enter");
    await flush(ctx.window, 6);
    assert.match(ctx.stripText(), /being made now/);
    assert.ok(ctx.document.getElementById("library-strip-status").classList.contains("library-strip-problem"));
    await ctx.until(() => ctx.state.library.status === "ready");
    assert.equal(refused, 2);
    assert.deepEqual(ctx.delays.filter((ms) => ms === 5000), [5000, 5000], "each wait is the Retry-After");
    assert.doesNotMatch(ctx.stripText(), /being made now/);
    assert.equal(note(ctx).textContent, "", "a 503 is the strip's, not the box's");
  });

  test("503 that never ends: about a minute of asking again, then it stops and leaves the sentence", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    speedUp(ctx);
    let refused = 0;
    ctx.server.first("/api/library/ids", () => {
      refused += 1;
      return { success: false, error: "The word index is being made now; try again in a few seconds." };
    }, { status: 503, headers: { "Retry-After": "5" } });
    await search(ctx, "beach");
    await ctx.until(() => ctx.state.library.status === "error");
    const asked = refused;
    assert.equal(asked, 13, "the first ask and twelve more, five seconds apart: a minute");
    await ctx.settle(300);
    assert.equal(refused, asked, "and then nothing more");
    assert.match(ctx.stripText(), /being made now/);
  });

  test("a new search while one waits out a 503: the waiting one asks nothing more", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    speedUp(ctx);
    const asked = [];
    ctx.server.first("/api/library/ids", () => {
      const body = ctx.server.lastBody("/api/library/ids");
      asked.push(body.value.words);
      if (body.value.words === "sand") return { source: {}, total: 1, ids: [5], complete: true };
      return { success: false, error: "The word index is being made now; try again in a few seconds." };
    }, { status: 503, headers: { "Retry-After": "5" } });
    await search(ctx, "beach");
    assert.ok(asked.length >= 1);
    ctx.server.routes.shift();
    ctx.server.first("/api/library/ids", () => {
      asked.push(ctx.server.lastBody("/api/library/ids").value.words);
      return { source: {}, total: 1, ids: [5], complete: true };
    });
    words(ctx).value = "sand";
    ctx.key(words(ctx), "Enter");
    const beachAsks = asked.filter((w) => w === "beach").length;
    await ctx.settle(400);
    assert.equal(asked.filter((w) => w === "beach").length, beachAsks, "the replaced search was not asked again");
    assert.equal(ctx.state.library.value.words, "sand");
    assert.equal(ctx.state.library.status, "ready");
  });
});

describe("a search is a view like any other", () => {
  test("Select all and the tally name the search as the selection's source", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await search(ctx, "beach");
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    await ctx.until(() => ctx.server.calls.some((call) => call.url.includes("/selection/tally")));
    const tally = ctx.server.calls.filter((call) => call.url.includes("/selection/tally")).at(-1);
    assert.deepEqual(tally.body.selection.source, { kind: "search", value: { words: "beach" }, recursive: false });
  });

  test("going to another library takes the search out of the address (VIEW_PARAMS)", async () => {
    const calls = [];
    globalThis.window = { location: { search: `?view=search&value=${encodeURIComponent('{"words":"beach"}')}&order=name`, set href(v) { calls.push(v); } } };
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    try {
      const { goToLibrary } = await import("../../web/common/library.js");
      goToLibrary("other");
    } finally {
      delete globalThis.window;
      delete globalThis.localStorage;
    }
    assert.deepEqual(calls, ["/other/"]);
  });
});
