/**
 * The owner's review of the library views (docs/findings.md #671-#673): several rows of the navigator selected at once in every
 * tab -- click, Ctrl-click, Shift-click, Ctrl+A -- and the grid the union of them; selecting a row selects the rows under it,
 * and Ctrl-click takes one of those off while the row's own photos stay; the People tab under the branches people are filed
 * in; and one sort, Date taken or Name either way, for every tab. The address holds all of it, so Back, Forward and a bookmark
 * restore it. The library is invented at photo_index's size (view-page.mjs) and every name in it is fictional.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, syntheticDates } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const YEAR = new Date().getFullYear();

/** The source the last ids request named: { kind, value (a union's list parsed), order }. */
function asked(ctx) {
  const query = new URLSearchParams(ctx.idsAsked.at(-1).split("?")[1]);
  const kind = query.get("kind");
  const value = kind === "any_of" ? JSON.parse(query.get("value")) : (query.get("value") ?? query.get("folder"));
  return { kind, value, order: query.get("order"), recursive: query.get("recursive") };
}

async function filter(ctx, name, text) {
  const input = ctx.document.querySelector(`#nav-panel-${name} .nav-filter`);
  input.value = text;
  input.dispatchEvent(new ctx.window.Event("input"));
  await ctx.settle(200);
}

describe("several rows at once", () => {
  test("Dates: filter to July, Shift-click the first and the last: every July, one request, the address holds it", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("dates");
    await filter(ctx, "dates", "July");
    const julys = ctx.rows("dates");
    assert.ok(julys.length >= 57, "a July for each year");
    assert.ok(julys.every((row) => row.querySelector(".nav-label").textContent === "July"));
    click(ctx.window, julys[0]);
    await ctx.settle();
    const before = ctx.idsAsked.length;
    click(ctx.window, julys.at(-1), { shiftKey: true });
    await ctx.settle();
    assert.equal(ctx.idsAsked.length, before + 1, "one request for the union");
    const source = asked(ctx);
    assert.equal(source.kind, "any_of");
    assert.equal(source.value.length, julys.length);
    assert.ok(source.value.every((m) => m.kind === "month" && m.value.endsWith("-07")));
    assert.equal(ctx.selectedRows("dates").length, julys.length, "every July is shown selected");
    assert.match(ctx.window.location.search, /view=any_of/);
    assert.match(ctx.stripText(), /July \d{4}, July \d{4}, July \d{4} and \d+ more/);
    // Back to the one July, Forward to all of them.
    const all = ctx.window.location.search;
    await ctx.popTo(`?view=month&value=${YEAR}-07`);
    assert.equal(ctx.selectedRows("dates").length, 1);
    await ctx.popTo(all);
    assert.equal(ctx.state.library.kind, "any_of");
    assert.equal(ctx.selectedRows("dates").length, julys.length);
  });

  test("Ctrl-click adds a row and takes it away; the last row selected stays, and says why", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const [first, second] = ctx.rows("people");
    click(ctx.window, first);
    await ctx.settle();
    click(ctx.window, second, { ctrlKey: true });
    await ctx.settle();
    assert.deepEqual(asked(ctx).value.map((m) => m.kind), ["person", "person"]);
    assert.equal(ctx.selectedRows("people").length, 2);
    click(ctx.window, first, { ctrlKey: true });
    await ctx.settle();
    assert.equal(asked(ctx).kind, "person", "a union of one is that person");
    assert.equal(asked(ctx).value, second.querySelector(".nav-label").textContent);
    const requests = ctx.idsAsked.length;
    click(ctx.window, second, { ctrlKey: true });
    await ctx.settle();
    assert.equal(ctx.idsAsked.length, requests, "nothing would be left: nothing is asked");
    assert.match(ctx.status("people"), /One row at least stays selected/);
    assert.equal(ctx.selectedRows("people").length, 1);
  });

  test("a parent selects its children; Ctrl-click takes one off and the parent's own photos stay in", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    click(ctx.window, ctx.rows("folders")[0]);   // the library folder: opened, and every folder under it selected
    await ctx.settle();
    click(ctx.window, ctx.rowByLabel("folders", "1990"));
    await ctx.settle();
    assert.deepEqual(asked(ctx), { kind: "folder", value: "D:\\Library\\1990", order: null, recursive: "1" });
    const events = ctx.rows("folders").filter((row) => row.getAttribute("aria-level") === "3");
    assert.equal(events.length, 68, "a click opens the branch");
    assert.ok(events.every((row) => row.getAttribute("aria-selected") === "true"), "its children are shown selected");
    const seventh = ctx.rowByLabel("folders", "Event 07");
    click(ctx.window, seventh, { ctrlKey: true });
    await ctx.settle();
    const source = asked(ctx);
    assert.equal(source.kind, "any_of");
    assert.deepEqual(source.value[0], { kind: "folder", value: "D:\\Library\\1990" }, "the year's own photos, not its subfolders");
    assert.equal(source.value.length, 1 + 67);
    assert.ok(!source.value.some((m) => m.value.endsWith("Event 07")));
    assert.equal(ctx.rowByLabel("folders", "1990").getAttribute("aria-selected"), "true", "the parent stays selected");
    assert.equal(seventh.getAttribute("aria-selected"), "false");
    // And back on: the whole year again, one source.
    click(ctx.window, seventh, { ctrlKey: true });
    await ctx.settle();
    assert.deepEqual(asked(ctx), { kind: "folder", value: "D:\\Library\\1990", order: null, recursive: "1" });
  });

  test("Keywords: a branch with one keyword under it taken off is the branch's own node and the others", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=keyword&value=Trips" });
    assert.equal(ctx.state.nav.tab, "keywords");
    click(ctx.window, ctx.rowByLabel("keywords", "Trips").querySelector(".nav-twisty"));
    click(ctx.window, ctx.rowByLabel("keywords", "Group 03"), { ctrlKey: true });
    await ctx.settle();
    const source = asked(ctx);
    assert.equal(source.kind, "any_of");
    assert.deepEqual(source.value[0], { kind: "keyword_only", value: "Trips" });
    assert.equal(source.value.filter((m) => m.kind === "keyword").length, 29);
  });

  test("Ctrl+A selects every row drawn: the whole folder tree is the one top folder with its subfolders", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const top = ctx.rows("folders")[0];
    top.focus();
    ctx.key(top, "a", { ctrlKey: true });
    await ctx.settle();
    assert.deepEqual(asked(ctx), { kind: "folder", value: "D:\\Library", order: null, recursive: "1" });
  });

  test("more rows than a view shows at once: a sentence, and nothing asked", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await filter(ctx, "folders", "Event");
    assert.ok(ctx.rows("folders").length > 1000);
    const before = ctx.idsAsked.length;
    ctx.rows("folders")[0].focus();
    ctx.key(ctx.rows("folders")[0], "a", { ctrlKey: true });
    await ctx.settle();
    assert.equal(ctx.idsAsked.length, before);
    assert.match(ctx.status("folders"), /selects 2,705 rows; a view shows at most 1,000 at once/);
  });

  test("Ctrl-click in another tab adds to the same union; switching tabs keeps the selection", async (t) => {
    const ctx = await loadViewPage(t, { search: `?view=month&value=${YEAR - 1}-07` });
    assert.equal(ctx.state.nav.tab, "dates");
    await ctx.openTab("people");
    assert.equal(ctx.state.library.kind, "month", "showing another tab changes nothing");
    click(ctx.window, ctx.rows("people")[0], { ctrlKey: true });
    await ctx.settle();
    assert.deepEqual(asked(ctx).value.map((m) => m.kind), ["month", "person"]);
    assert.equal(ctx.state.nav.tab, "people", "the tab clicked in stays");
    await ctx.openTab("dates");
    assert.equal(ctx.selectedRows("dates").length, 1, "each tab shows its own rows of the union");
    // A plain click anywhere starts again.
    await ctx.openTab("people");
    click(ctx.window, ctx.rows("people")[3]);
    await ctx.settle();
    assert.equal(asked(ctx).kind, "person");
  });

  test("a row the filter hides stays selected, and the section says so", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    click(ctx.window, ctx.rows("people")[0]);
    await ctx.settle();
    click(ctx.window, ctx.rows("people")[1], { ctrlKey: true });
    await ctx.settle();
    const name = ctx.rows("people")[1].querySelector(".nav-label").textContent;
    await filter(ctx, "people", name);
    assert.match(ctx.note("people"), /1 selected row is hidden by the filter and still selected/);
    assert.equal(ctx.state.library.kind, "any_of");
  });

  test("a bookmark of a union opens it, a row the library no longer has is left out, and a bad one says so", async (t) => {
    const value = JSON.stringify([{ kind: "keyword", value: "Trips/Group 03" }, { kind: "keyword", value: "Gone/Long ago" },
      { kind: "month", value: `${YEAR - 2}-06` }]);
    const ctx = await loadViewPage(t, { search: `?view=any_of&value=${encodeURIComponent(value)}&order=name-desc` });
    assert.equal(ctx.state.library.kind, "any_of");
    assert.equal(asked(ctx).order, "name-desc");
    assert.equal(ctx.state.nav.tab, "keywords");
    assert.equal(ctx.selectedRows("keywords")[0].querySelector(".nav-label").textContent, "Group 03");
    await ctx.openTab("dates");
    assert.equal(ctx.selectedRows("dates")[0].querySelector(".nav-label").textContent, "June");
    assert.equal(ctx.document.getElementById("nav-sort").value, "name-desc");
    for (const bad of ["not json", "[]", JSON.stringify([{ kind: "any_of", value: "[]" }]), JSON.stringify([{ kind: "month", value: "June" }])]) {
      const broken = await loadViewPage(t, { search: `?view=any_of&value=${encodeURIComponent(bad)}` });
      assert.equal(broken.state.library.invalid, true, bad);
      assert.equal(broken.idsAsked.length, 0, "no request");
    }
  });

  test("a Select all of the grid on a union sends the union as its source, not 68,000 ids", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: Array.from({ length: 400 }, (_, i) => i + 1) });
    await ctx.openTab("dates");
    await filter(ctx, "dates", "July");
    click(ctx.window, ctx.rows("dates")[0]);
    await ctx.settle();
    click(ctx.window, ctx.rows("dates")[3], { shiftKey: true });
    await ctx.settle();
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    const request = ctx.module("selected.js").selectionRequest();
    assert.equal(request.ok, true);
    assert.equal(request.body.source.kind, "any_of");
    assert.equal(request.body.source.value.length, 4);
    assert.deepEqual([...request.body.excluded], []);
  });
});

describe("the People under their branches", () => {
  const groups = [
    { tag: "Family", name: "Family", parent: null, count: 30 },
    { tag: "Family/Immediate", name: "Immediate", parent: "Family", count: 20 },
    { tag: "Family/Cousins", name: "Cousins", parent: "Family", count: 15 },
    { tag: "Friends", name: "Friends", parent: null, count: 9 },
  ];
  const people = [
    { name: "Rowan Thackeray", count: 12, group: "Family/Immediate" }, { name: "Wren Halloway", count: 10, group: "Family/Immediate" },
    { name: "Hazel Brookmire", count: 15, group: "Family/Cousins" }, { name: "Orla Pemberley", count: 9, group: "Friends" },
    { name: "Ash Gantry", count: 3, group: null },
  ];
  const navigator = { people: { people, groups, unfiled: 3 } };

  test("the people are under their branches as the tree nests them, open, with the ones not filed at the end", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", navigator });
    await ctx.openTab("people");
    const shown = ctx.rows("people").map((row) => `${row.getAttribute("aria-level")}:${row.querySelector(".nav-label").textContent}`);
    assert.deepEqual(shown, ["1:Family", "2:Cousins", "3:Hazel Brookmire", "2:Immediate", "3:Rowan Thackeray", "3:Wren Halloway",
      "1:Friends", "2:Orla Pemberley", "1:Not filed in the tag tree", "2:Ash Gantry"]);
    assert.equal(ctx.rowByLabel("people", "Family").querySelector(".nav-count").textContent, "30", "each photo once");
    assert.match(ctx.rowByLabel("people", "Immediate").title, /Family\/Immediate\n2 people/);
  });

  test("a branch's row selects its people: the view is the union of them", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", navigator });
    await ctx.openTab("people");
    click(ctx.window, ctx.rowByLabel("people", "Family"));
    await ctx.settle();
    const source = asked(ctx);
    assert.equal(source.kind, "any_of");
    assert.deepEqual(source.value.map((m) => m.value).sort(), ["Hazel Brookmire", "Rowan Thackeray", "Wren Halloway"]);
    assert.equal(ctx.selectedRows("people").length, 6, "the branch, its two branches and its three people");
    click(ctx.window, ctx.rowByLabel("people", "Wren Halloway"), { ctrlKey: true });
    await ctx.settle();
    assert.deepEqual(asked(ctx).value.map((m) => m.value).sort(), ["Hazel Brookmire", "Rowan Thackeray"]);
  });

  test("a person opened from the address is found under their branch, and the filter finds people with their place", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=person&value=orla%20pemberley", navigator });
    assert.equal(ctx.state.nav.tab, "people");
    // Orla is the only person of Friends: the branch, holding nothing of its own, is all selected too.
    assert.deepEqual(ctx.selectedRows("people").map((row) => row.querySelector(".nav-label").textContent), ["Friends", "Orla Pemberley"]);
    await filter(ctx, "people", "rowan");
    assert.deepEqual(ctx.rows("people").map((row) => row.querySelector(".nav-hint").textContent), ["Family/Immediate"]);
  });
});

describe("the sort", () => {
  test("one control for every tab: the view is read again in the order chosen, the address keeps it, and Back restores it", async (t) => {
    const ctx = await loadViewPage(t, { search: `?view=year&value=${YEAR - 1}` });
    const select = ctx.document.getElementById("nav-sort");
    assert.deepEqual([...select.options].map((o) => o.textContent),
      ["Date taken, oldest first", "Date taken, newest first", "Name, A to Z", "Name, Z to A"]);
    assert.equal(select.value, "taken");
    select.value = "name";
    select.dispatchEvent(new ctx.window.Event("change"));
    await ctx.settle();
    assert.deepEqual(asked(ctx), { kind: "year", value: String(YEAR - 1), order: "name", recursive: null });
    assert.match(ctx.window.location.search, /order=name/);
    // Another row, in another tab, is read in the same order.
    await ctx.openTab("people");
    click(ctx.window, ctx.rows("people")[0]);
    await ctx.settle();
    assert.equal(asked(ctx).order, "name");
    // Back to the year by date.
    await ctx.popTo(`?view=year&value=${YEAR - 1}`);
    assert.equal(asked(ctx).order, null);
    assert.equal(select.value, "taken");
    assert.equal(ctx.state.nav.order, "taken");
  });

  test("an order an address names that is none is a sentence, not a request", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all&order=sideways" });
    assert.equal(ctx.state.library.invalid, true);
    assert.equal(ctx.idsAsked.length, 0);
    assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /an order \(.sideways.\) that TagPup does not know/);
  });

  test("the sort keys do not step the open photo", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const select = ctx.document.getElementById("nav-sort");
    select.focus();
    const before = ctx.photosAsked.length;
    ctx.key(select, "ArrowDown");
    await ctx.settle(20);
    assert.equal(ctx.photosAsked.length, before);
  });
});

describe("the model, alone", () => {
  test("compress gives the fewest sources and selectedRows gives the rows back", async (t) => {
    const ctx = await loadViewPage(t);
    const m = ctx.module("navigator-model.js");
    const dates = m.indexDates(syntheticDates(2026), 2026);
    const tree = m.rowTree("dates", dates);
    const year = m.withRowsUnder(tree, ["y:2020"]);
    assert.equal(JSON.stringify(m.compress(tree, year)), JSON.stringify([{ kind: "year", value: "2020", recursive: false }]));
    year.delete("m:2020-03");
    const parts = m.compress(tree, year);
    assert.equal(parts.length, 12, "eleven months and the Other of the year");
    assert.ok(parts.some((p) => p.kind === "year_other"));
    const back = m.selectedRows("dates", dates, m.specOfMembers(parts)).rows;
    assert.ok(!back.has("m:2020-03") && back.has("m:2020-04") && back.has("o:2020"));
    assert.equal(m.specOfMembers([]), null);
  });
});
