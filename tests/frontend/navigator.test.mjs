/**
 * The navigator beside the grid (web/tagpup/navigator.js, navigator-model.js, navigator-tree.js; phase 9c): the sidebar's
 * two panes, and in the library pane the four sections -- Folders, Keywords, People, Dates -- as tabs, each a tree or a list
 * read from GET /api/library/navigator when its tab is first opened, with a filter, counts, and the open view's source
 * highlighted. A click opens the library view of the item (so the address names it), and the sections are read again after
 * an edit that changes counts. The library is invented at photo_index's size (2,746 folders, 895 keyword nodes, 413 people,
 * 61 years) and every name in it is fictional.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, syntheticFolders, syntheticKeywords, syntheticPeople, syntheticDates } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const YEAR = new Date().getFullYear();

describe("the sidebar's two panes", () => {
  test("a page with no view shows the folder pane, the library pane unread; a view shows the library pane", async (t) => {
    const ctx = await loadViewPage(t);
    assert.equal(ctx.state.nav.shown, "folder");
    assert.ok(ctx.pane("library").classList.contains("hidden"));
    assert.ok(!ctx.pane("folder").classList.contains("hidden"));
    assert.deepEqual(ctx.navigatorAsked, [], "nothing is read until the library pane is shown");
    assert.equal(ctx.paneTab("folder").getAttribute("aria-selected"), "true");
    assert.equal(ctx.paneTab("library").getAttribute("aria-selected"), "false");

    const view = await loadViewPage(t, { search: "?view=all" });
    assert.equal(view.state.nav.shown, "library");
    assert.ok(!view.pane("library").classList.contains("hidden"));
    assert.ok(view.pane("folder").classList.contains("hidden"));
    assert.equal(view.paneTab("library").getAttribute("aria-selected"), "true");
    assert.deepEqual(view.navigatorAsked, ["folders"], "the first tab, and only it, is read");
  });

  test("the switch is a tab list: arrow keys move between its two tabs, and the choice is remembered for that kind of view", async (t) => {
    const ctx = await loadViewPage(t);
    ctx.key(ctx.paneTab("folder"), "ArrowLeft");
    await ctx.settle(20);
    assert.equal(ctx.state.nav.shown, "library");
    assert.equal(ctx.document.activeElement, ctx.paneTab("library"));
    assert.equal(ctx.paneTab("library").tabIndex, 0);
    assert.equal(ctx.paneTab("folder").tabIndex, -1);
    // In a folder view the choice is the Library pane for the rest of this page.
    assert.equal(ctx.state.nav.choice.folder, "library");
    assert.equal(ctx.state.nav.choice.library, "library");
    // A view opened by a click in the navigator: the pane stays.
    const row = ctx.rowByLabel("folders", "Library");
    click(ctx.window, row);
    await ctx.settle();
    assert.equal(ctx.state.nav.shown, "library");
  });

  test("a person who chooses the folder pane in a view keeps it for views, until the page is left", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    ctx.paneTab("folder").click();
    await ctx.settle(20);
    assert.equal(ctx.state.nav.shown, "folder");
    assert.equal(ctx.state.nav.choice.library, "folder");
    // Another view is opened (the gear's menu): the pane they chose stays.
    ctx.module("library-view.js").openLibraryView({ kind: "year", value: "2020", recursive: false });
    await ctx.settle();
    assert.equal(ctx.state.nav.shown, "folder");
    assert.equal(ctx.state.nav.choice.folder, "folder", "a folder view's choice is its own");
  });
});

describe("the four sections", () => {
  test("Folders: the top of the tree only, 2,746 folders never drawn at once; a branch opens on a click of its arrow", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    assert.equal(ctx.rows("folders").length, 1, "one library folder at the top");
    assert.equal(ctx.state.nav.sections.folders.index.size, 2746);
    const top = ctx.rows("folders")[0];
    assert.equal(top.getAttribute("aria-expanded"), "false");
    assert.match(top.querySelector(".nav-count").textContent, /^\d/);
    click(ctx.window, top.querySelector(".nav-twisty"));
    assert.equal(ctx.rows("folders").length, 1 + 40);
    assert.equal(ctx.idsAsked.length, 1, "opening a branch is not opening a view");
    const first = ctx.rowByLabel("folders", "1985");
    click(ctx.window, first.querySelector(".nav-twisty"));
    assert.equal(ctx.rows("folders").length, 1 + 40 + 68);
    assert.ok(ctx.rows("folders").length < 400, "two levels of a library: a few hundred rows, not 2,746");
    assert.equal(ctx.row("folders", "f:d:\\library\\1985").getAttribute("aria-level"), "2");
  });

  test("Folders: a click on a folder opens the library view of it with its subfolders, and the address says so", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const top = ctx.rows("folders")[0];
    click(ctx.window, top);
    await ctx.settle();
    assert.match(ctx.idsAsked.at(-1), /kind=folder&folder=D%3A%5CLibrary&recursive=1$/);
    assert.match(ctx.window.location.search, /view=folder&value=D%3A%5CLibrary&recursive=1/);
    assert.equal(ctx.row("folders", "f:d:\\library").getAttribute("aria-selected"), "true");
    assert.equal(ctx.row("folders", "f:d:\\library").getAttribute("aria-expanded"), "true", "a click also opens a closed branch");
  });

  test("Keywords: the tag tree with counts, alphabetically, 895 nodes never drawn at once", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("keywords");
    const labels = ctx.rows("keywords").map((row) => row.querySelector(".nav-label").textContent);
    assert.deepEqual(labels, ["Activity", "Events", "People", "Places", "Trips"]);
    assert.equal(ctx.state.nav.sections.keywords.index.size, 895);
    click(ctx.window, ctx.rowByLabel("keywords", "Trips").querySelector(".nav-twisty"));
    const trips = ctx.rows("keywords").map((row) => row.querySelector(".nav-label").textContent);
    assert.equal(trips.length, 5 + 30);
    assert.deepEqual(trips.slice(5), [...trips.slice(5)].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })), "children alphabetically");
    assert.match(ctx.rowByLabel("keywords", "Trips").title, /Trips\n[\d,]+ photos with it or a keyword under it/);
  });

  test("Keywords: a click opens the keyword and everything under it", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("keywords");
    click(ctx.window, ctx.rowByLabel("keywords", "Events"));
    await ctx.settle();
    assert.match(ctx.idsAsked.at(-1), /kind=keyword&value=Events$/);
    assert.equal(ctx.row("keywords", "k:Events").getAttribute("aria-selected"), "true");
  });

  test("People: a list, alphabetical, with counts; a click opens the person", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const rows = ctx.rows("people");
    assert.equal(rows.length, 413, "all 413 people are drawn and reachable, none cut");
    assert.equal(ctx.note("people"), "");
    const names = rows.map((row) => row.querySelector(".nav-label").textContent);
    assert.deepEqual(names, [...names].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true })));
    assert.equal(rows[0].getAttribute("role"), "option");
    assert.equal(ctx.document.querySelector("#nav-panel-people .nav-tree").getAttribute("role"), "listbox");
    click(ctx.window, rows[1]);
    await ctx.settle();
    assert.match(ctx.idsAsked.at(-1), /kind=person&value=/);
  });

  test("Dates: years newest first, months January to December, and the years that are not dates under one collapsed entry", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("dates");
    const labels = ctx.rows("dates").map((row) => row.querySelector(".nav-label").textContent);
    assert.equal(labels[0], String(YEAR));
    assert.match(labels.at(-1), /^Other years \(\d+\)$/);
    const usual = labels.slice(0, -1).map(Number);
    assert.deepEqual(usual, [...usual].sort((a, b) => b - a));
    assert.ok(usual.every((year) => year >= 1970 && year <= YEAR + 1));
    assert.equal(ctx.state.nav.sections.dates.index.size, 61);
    const other = ctx.rows("dates").at(-1);
    assert.equal(other.getAttribute("aria-expanded"), "false");
    assert.match(ctx.note("dates"), /1,180 photos have no date/);
    // A year opens to its months, January first.
    click(ctx.window, ctx.rowByLabel("dates", String(YEAR - 1)).querySelector(".nav-twisty"));
    const after = ctx.rows("dates").map((row) => row.querySelector(".nav-label").textContent);
    const at = after.indexOf(String(YEAR - 1));
    assert.deepEqual(after.slice(at + 1, at + 4), ["January", "February", "March"]);
    // The other years are reachable: open the group, then a year of it.
    click(ctx.window, ctx.rows("dates").at(-1).querySelector(".nav-twisty"));
    const odd = ctx.rows("dates").filter((row) => row.getAttribute("aria-level") === "2" && /^\d{1,4}$/.test(row.querySelector(".nav-label").textContent));
    assert.ok(odd.length >= 1);
    click(ctx.window, odd[0]);
    await ctx.settle();
    assert.match(ctx.idsAsked.at(-1), /kind=year&value=\d+$/);
  });

  test("Dates: a month with photos of its year that name no month shows 'Other', which opens the year", async (t) => {
    const dates = { years: [{ year: 2022, count: 30, months: [{ month: "2022-03", count: 20 }], other: 10 }], undated: 0 };
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: { dates } });
    await ctx.openTab("dates");
    click(ctx.window, ctx.rowByLabel("dates", "2022").querySelector(".nav-twisty"));
    const names = ctx.rows("dates").map((row) => row.querySelector(".nav-label").textContent);
    assert.deepEqual(names, ["2022", "March", "Other"]);
    click(ctx.window, ctx.rowByLabel("dates", "Other"));
    await ctx.settle();
    assert.match(ctx.idsAsked.at(-1), /kind=year&value=2022$/);
  });

  test("an expanded tree is drawn at most 1,500 rows and the count left out is the real one (49 of 1,549)", async (t) => {
    const nodes = [{ tag: "Big", name: "Big", parent: null, count: 1 }];
    for (let i = 0; i < 1548; i++) nodes.push({ tag: `Big/Item ${String(i).padStart(4, "0")}`, name: `Item ${String(i).padStart(4, "0")}`, parent: "Big", count: 1 });
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: { keywords: { keywords: nodes } } });
    await ctx.openTab("keywords");
    click(ctx.window, ctx.rowByLabel("keywords", "Big").querySelector(".nav-twisty"));
    assert.equal(ctx.rows("keywords").length, 1500);
    assert.match(ctx.note("keywords"), /^49 more are not shown/);
  });

  test("a parent with 1,000 children shows every one of them", async (t) => {
    const nodes = [{ tag: "Wide", name: "Wide", parent: null, count: 1 }];
    for (let i = 0; i < 1000; i++) nodes.push({ tag: `Wide/Child ${String(i).padStart(4, "0")}`, name: `Child ${String(i).padStart(4, "0")}`, parent: "Wide", count: 1 });
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: { keywords: { keywords: nodes } } });
    await ctx.openTab("keywords");
    click(ctx.window, ctx.rowByLabel("keywords", "Wide").querySelector(".nav-twisty"));
    assert.equal(ctx.rows("keywords").length, 1001);
    assert.ok(ctx.rowByLabel("keywords", "Child 0999"), "the last child is there");
    assert.equal(ctx.note("keywords"), "");
  });

  test("the whole keyword tree of photo_index's size, everything open, is drawn (895 rows) with nothing left out", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("keywords");
    const sec = ctx.state.nav.sections.keywords;
    for (const node of sec.index.byTag.values()) sec.expanded.add(node.id);
    ctx.document.querySelector("#nav-panel-keywords .nav-filter").dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    assert.equal(ctx.rows("keywords").length, 895);
    assert.equal(ctx.note("keywords"), "");
  });

  test("the last person of 413 is reachable without typing", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const names = syntheticPeople().map((each) => each.name).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }));
    assert.ok(ctx.rowByLabel("people", names.at(-1)), "the alphabetically last person has a row");
    assert.ok(ctx.rowByLabel("people", names[0]));
  });
});

describe("the filter", () => {
  test("People: typing narrows the list after a moment, and nothing that matches is said", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const input = ctx.document.querySelector("#nav-panel-people .nav-filter");
    input.value = "wren";
    input.dispatchEvent(new ctx.window.Event("input"));
    assert.equal(ctx.rows("people").length, 413, "not at once: the filter waits for the next key");
    await ctx.settle(200);
    const names = ctx.rows("people").map((row) => row.querySelector(".nav-label").textContent);
    assert.ok(names.length > 0 && names.length < 80);
    assert.ok(names.every((name) => /wren/i.test(name)));
    input.value = "zzzz";
    input.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    assert.equal(ctx.rows("people").length, 0);
    assert.match(ctx.status("people"), /Nothing matches .zzzz./);
  });

  test("Folders and Keywords: a filter lists what matches wherever it is filed, each with its place", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const input = ctx.document.querySelector("#nav-panel-folders .nav-filter");
    input.value = "1999";
    input.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    const rows = ctx.rows("folders");
    assert.ok(rows.length >= 69, "the year and its events");
    assert.ok(rows.every((row) => row.querySelector(".nav-hint").textContent.includes("1999")));
    await ctx.openTab("keywords");
    const kw = ctx.document.querySelector("#nav-panel-keywords .nav-filter");
    kw.value = "trips/group 03/item";
    kw.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    assert.ok(ctx.rows("keywords").length > 0);
    assert.ok(ctx.rows("keywords").every((row) => row.querySelector(".nav-hint").textContent.startsWith("Trips/Group 03/Item")));
  });

  test("Escape in the filter clears it; ArrowDown goes to the rows", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const input = ctx.document.querySelector("#nav-panel-people .nav-filter");
    input.value = "wren";
    input.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    ctx.key(input, "Escape");
    await ctx.settle(200);
    assert.equal(input.value, "");
    assert.equal(ctx.rows("people").length, 413);
    ctx.key(input, "ArrowDown");
    assert.equal(ctx.document.activeElement, ctx.rows("people")[0]);
  });

  test("the arrow keys in the filter box do not step the open photo", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const input = ctx.document.querySelector("#nav-panel-people .nav-filter");
    input.focus();
    const before = ctx.photosAsked.length;
    ctx.key(input, "ArrowRight");
    ctx.key(input, "ArrowLeft");
    await ctx.settle(20);
    assert.equal(ctx.photosAsked.length, before);
  });
});

describe("loading, failing and nothing", () => {
  test("a section says it is loading, then shows its rows", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", before: undefined });
    ctx.hold.keywords = true;
    ctx.tab("keywords").click();
    await ctx.settle(20);
    assert.match(ctx.status("keywords"), /Loading the keywords/);
    assert.equal(ctx.rows("keywords").length, 0);
    await ctx.release("keywords");
    assert.equal(ctx.rows("keywords").length, 5);
    assert.equal(ctx.status("keywords"), "");
  });

  test("a library with no photos: each section says there is nothing, in a sentence", async (t) => {
    const ctx = await loadViewPage(t, {
      search: "?view=all", ids: [],
      navigator: { folders: { folders: [] }, keywords: { keywords: [] }, people: { people: [] }, dates: { years: [], undated: 0 } },
    });
    for (const [name, words] of [["folders", /no photos in any folder/], ["keywords", /no keywords/], ["people", /names a person/], ["dates", /has a date/]]) {
      await ctx.openTab(name);
      assert.match(ctx.status(name), words, name);
      assert.equal(ctx.rows(name).length, 0, name);
    }
    assert.deepEqual(ctx.consoleErrors, []);
  });

  test("a library at schema 18: the server's sentence is shown in each section, not a trace, and no row", async (t) => {
    const sentence = "photo_index has not been brought up to date for browsing by folder, keyword, person or date yet. Open it in TagPup once.";
    const behind = { status: 409, body: { error: sentence } };
    const ctx = await loadViewPage(t, {
      search: "?view=all",
      navigator: {
        folders: () => behind.body, keywords: () => behind.body, people: () => behind.body, dates: () => behind.body,
      },
    });
    ctx.server.first("/api/library/navigator", { error: sentence }, { status: 409 });
    for (const name of ["folders", "keywords", "people", "dates"]) {
      ctx.state.nav.sections[name].status = "idle";
      await ctx.openTab(name);
      assert.equal(ctx.status(name), sentence, name);
      assert.equal(ctx.rows(name).length, 0, name);
    }
    assert.ok(!ctx.consoleErrors.some((each) => /Traceback/.test(String(each))));
  });

  test("an unplaced root: the roots banner is shown (by api.js) and the sections say the same sentence", async (t) => {
    const sentence = "This computer does not know where this library keeps its photos.";
    const ctx = await loadViewPage(t, { search: "?view=all" });
    ctx.server.first("/api/library/", { error: sentence }, { status: 409, headers: { "X-TagPup-Roots-Problem": "1" } });
    ctx.state.nav.sections.people.status = "idle";
    await ctx.openTab("people");
    await ctx.settle(60);
    assert.equal(ctx.status("people"), sentence);
    assert.ok(!ctx.document.getElementById("roots-banner").classList.contains("hidden"));
  });

  test("a section that cannot be reached says so and is read again when its tab is opened again", async (t) => {
    let fail = true;
    const ctx = await loadViewPage(t, {
      search: "?view=all",
      navigator: { people: () => { if (fail) throw new Error("down"); return { people: syntheticPeople().slice(0, 3) }; } },
    });
    ctx.server.first("/api/library/navigator?section=people", () => Promise.reject(new TypeError("Failed to fetch")));
    await ctx.openTab("people");
    assert.match(ctx.status("people"), /Could not read the people/);
    assert.match(pageErrors().join(), /Failed to fetch/, "a failure of the network is logged, as the page's others are");
    ctx.server.routes.shift();
    fail = false;
    await ctx.openTab("keywords");
    await ctx.openTab("people");
    assert.equal(ctx.rows("people").length, 3);
    assert.equal(ctx.status("people"), "");
  });

  test("two libraries: the navigator asks the library the page is on, and starts with none of the other's", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", library: "other" });
    assert.ok(ctx.server.urls().some((url) => url.startsWith("/other/api/library/navigator?section=folders")));
    assert.ok(!ctx.server.urls().some((url) => url.startsWith("/photo_index/api/library/navigator")));
  });
});

describe("a stale answer paints nowhere", () => {
  test("a tab opened while another is loading: each answer is drawn in its own panel, whatever order they come in", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    ctx.hold.keywords = true;
    ctx.hold.people = true;
    ctx.tab("keywords").click();
    await ctx.settle(20);
    ctx.tab("people").click();
    await ctx.settle(20);
    await ctx.release("people");
    assert.equal(ctx.rows("people").length, 413);
    assert.equal(ctx.rows("keywords").length, 0, "keywords is still loading");
    await ctx.release("keywords");
    assert.equal(ctx.rows("keywords").length, 0, "keywords is not on screen: it is drawn when its tab is");
    assert.ok(ctx.panel("keywords").classList.contains("hidden"), "the tab that is shown is still People");
    assert.equal(ctx.rows("people").length, 413);
    const asked = ctx.navigatorAsked.length;
    await ctx.openTab("keywords");
    assert.equal(ctx.rows("keywords").length, 5, "drawn at once from what was read");
    assert.equal(ctx.navigatorAsked.length, asked, "and not read again");
  });

  test("an older answer for the same section is dropped when a newer one has been asked for", async (t) => {
    let calls = 0;
    const ctx = await loadViewPage(t, {
      search: "?view=all",
      navigator: { people: () => ({ people: [{ name: `Call ${++calls}`, count: 1 }] }) },
    });
    ctx.hold.people = true;
    ctx.tab("people").click();
    await ctx.settle(20);
    ctx.module("navigator.js").navigatorCountsChanged({ now: true });   // a first ask is out; counts changed
    ctx.state.nav.sections.people.index = null;
    ctx.state.nav.sections.people.status = "idle";
    ctx.tab("keywords").click();
    await ctx.settle(20);
    ctx.tab("people").click();
    await ctx.settle(20);
    assert.equal(ctx.held.filter((h) => h.key === "people").length, 2);
    const [older, newer] = ctx.held.filter((h) => h.key === "people");
    ctx.held = [];
    newer.release();
    await ctx.settle(20);
    older.release();
    await ctx.settle(20);
    assert.deepEqual(ctx.rows("people").map((row) => row.querySelector(".nav-label").textContent), ["Call 2"]);
  });

  test("a click on an item while a view is loading: the first view is cancelled, only the second's cards are asked for", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    ctx.hold.ids = true;
    await ctx.openTab("people");
    click(ctx.window, ctx.rows("people")[0]);
    await ctx.settle(20);
    click(ctx.window, ctx.rows("people")[1]);
    await ctx.settle(20);
    assert.equal(ctx.state.library.kind, "person");
    assert.equal(ctx.state.library.value, ctx.rows("people")[1].querySelector(".nav-label").textContent);
    ctx.hold.ids = false;
    await ctx.release("ids");
    await ctx.settle();
    // Only the second view's order was ever used: the first's reply (held) drew nothing.
    assert.equal(ctx.state.library.status, "ready");
    assert.equal(ctx.selectedRows("people").length, 1);
    assert.equal(ctx.selectedRows("people")[0], ctx.rows("people")[1]);
  });
});

describe("following the view that is open", () => {
  test("a view opened from the address cold: its tab is selected, the path to it opened, its row highlighted", async (t) => {
    const folder = "D:\\Library\\1990\\Event 07";
    const ctx = await loadViewPage(t, { search: `?view=folder&value=${encodeURIComponent(folder)}&recursive=1` });
    assert.equal(ctx.state.nav.tab, "folders");
    assert.equal(ctx.tab("folders").getAttribute("aria-selected"), "true");
    const selected = ctx.selectedRows("folders");
    assert.equal(selected.length, 1);
    assert.equal(selected[0].querySelector(".nav-label").textContent, "Event 07");
    assert.equal(ctx.row("folders", "f:d:\\library").getAttribute("aria-expanded"), "true");
    assert.equal(ctx.row("folders", "f:d:\\library\\1990").getAttribute("aria-expanded"), "true");
  });

  test("a keyword, a person, a month, and a year among the odd ones: the right tab, opened to it", async (t) => {
    const keyword = await loadViewPage(t, { search: "?view=keyword&value=Trips%2FGroup%2003" });
    assert.equal(keyword.state.nav.tab, "keywords");
    assert.equal(keyword.selectedRows("keywords")[0].querySelector(".nav-label").textContent, "Group 03");
    assert.equal(keyword.row("keywords", "k:Trips").getAttribute("aria-expanded"), "true");

    const person = syntheticPeople()[250].name;
    const people = await loadViewPage(t, { search: `?view=person&value=${encodeURIComponent(person.toLowerCase())}` });
    assert.equal(people.state.nav.tab, "people");
    assert.equal(people.selectedRows("people").length, 1, "found without regard to case");

    const month = await loadViewPage(t, { search: `?view=month&value=${YEAR - 2}-06` });
    assert.equal(month.state.nav.tab, "dates");
    assert.equal(month.selectedRows("dates")[0].querySelector(".nav-label").textContent, "June");
    assert.equal(month.row("dates", `y:${YEAR - 2}`).getAttribute("aria-expanded"), "true");

    const odd = await loadViewPage(t, { search: "?view=year&value=2099" });
    assert.equal(odd.selectedRows("dates")[0].querySelector(".nav-label").textContent, "2099");
    assert.equal(odd.row("dates", "y:other").getAttribute("aria-expanded"), "true");
  });

  test("the whole library names no tab: the tab that was shown stays, nothing is highlighted", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    assert.equal(ctx.state.nav.tab, "folders");
    assert.equal(ctx.selectedRows("folders").length, 0);
  });

  test("a folder or a keyword the library no longer has: an empty view with its sentence, and no row highlighted", async (t) => {
    const folder = await loadViewPage(t, { search: `?view=folder&value=${encodeURIComponent("D:\\Library\\Gone")}&recursive=1`, ids: [] });
    assert.match(folder.document.getElementById("thumbnails-grid").textContent, /holds no photo in D:\\Library\\Gone/);
    assert.equal(folder.selectedRows("folders").length, 0);
    assert.equal(folder.state.nav.tab, "folders");
    const keyword = await loadViewPage(t, { search: "?view=keyword&value=No%2FSuch", ids: [] });
    assert.match(keyword.document.getElementById("thumbnails-grid").textContent, /No photo carries the keyword/);
    assert.equal(keyword.selectedRows("keywords").length, 0);
    assert.deepEqual(keyword.consoleErrors, []);
  });

  test("Back and Forward restore the view, the highlighted item and the tab", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    click(ctx.window, ctx.rows("folders")[0]);
    await ctx.settle();
    await ctx.openTab("people");
    const person = ctx.rows("people")[2];
    const name = person.querySelector(".nav-label").textContent;
    click(ctx.window, person);
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "person");
    assert.equal(ctx.selectedRows("people").length, 1);
    // Back to the folder view (the address of the one before).
    await ctx.popTo("?view=folder&value=D%3A%5CLibrary&recursive=1");
    assert.equal(ctx.state.library.kind, "folder");
    assert.equal(ctx.state.nav.tab, "folders");
    assert.equal(ctx.selectedRows("folders").length, 1);
    await ctx.openTab("people");
    assert.equal(ctx.selectedRows("people").length, 0, "the person is no longer the view");
    await ctx.openTab("folders");
    // Forward again.
    await ctx.popTo(`?view=person&value=${encodeURIComponent(name)}`);
    assert.equal(ctx.state.nav.tab, "people");
    assert.equal(ctx.selectedRows("people")[0].querySelector(".nav-label").textContent, name);
    await ctx.openTab("folders");
    assert.equal(ctx.selectedRows("folders").length, 0, "the folder is no longer the view");
  });

  test("Back to a folder view: the sidebar returns to the pane a folder view has", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    assert.equal(ctx.state.nav.shown, "library");
    await ctx.popTo("");
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.nav.shown, "folder");
    assert.equal(ctx.selectedRows("folders").length, 0);
  });
});

describe("counts after an edit", () => {
  test("a write that finishes: the section on screen is read again after a moment, quietly -- its rows stay the same elements, what is open stays open", async (t) => {
    let count = 10;
    const ctx = await loadViewPage(t, {
      search: "?view=keyword&value=Trips",
      navigator: { keywords: () => ({ keywords: syntheticKeywords().map((n) => (n.tag === "Trips" ? { ...n, count } : n)) }) },
    });
    click(ctx.window, ctx.rowByLabel("keywords", "Activity").querySelector(".nav-twisty"));
    const trips = ctx.rowByLabel("keywords", "Trips");
    const activity = ctx.rowByLabel("keywords", "Activity");
    assert.equal(trips.querySelector(".nav-count").textContent, "10");
    const asked = ctx.navigatorAsked.filter((name) => name === "keywords").length;
    count = 11;
    // A tag is added to a photo in the view: the write queue finishes an entry.
    const queue = ctx.module("write-queue.js");
    queue.markEntry(queue.queueEntry("Add tag"), "done");
    await ctx.settle(40);
    assert.equal(ctx.navigatorAsked.filter((name) => name === "keywords").length, asked, "not at once: edits come in runs");
    await ctx.settle(1400);
    assert.equal(ctx.navigatorAsked.filter((name) => name === "keywords").length, asked + 1, "one ask for the run");
    assert.equal(ctx.rowByLabel("keywords", "Trips"), trips, "the same element: no rebuild, no flicker");
    assert.equal(trips.querySelector(".nav-count").textContent, "11");
    assert.equal(ctx.rowByLabel("keywords", "Activity"), activity);
    assert.equal(activity.getAttribute("aria-expanded"), "true", "what was open is open");
    assert.equal(trips.getAttribute("aria-selected"), "true");
  });

  test("a run of writes is one read; a section not on screen is read when its tab is opened", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const queue = ctx.module("write-queue.js");
    for (let i = 0; i < 5; i++) queue.markEntry(queue.queueEntry("Add tag"), "done");
    await ctx.settle(1500);
    assert.equal(ctx.navigatorAsked.filter((name) => name === "people").length, 2);
    assert.equal(ctx.navigatorAsked.filter((name) => name === "folders").length, 1, "Folders is not on screen: not read yet");
    await ctx.openTab("folders");
    assert.equal(ctx.navigatorAsked.filter((name) => name === "folders").length, 2, "stale: read when shown");
  });

  test("Refresh view reads the counts again at once", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const before = ctx.navigatorAsked.length;
    ctx.document.getElementById("btn-library-refresh").click();
    await ctx.settle(100);
    assert.equal(ctx.navigatorAsked.length, before + 1);
  });

  test("a failed re-read keeps the rows and says it could not read them", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    ctx.server.first("/api/library/navigator?section=people", { error: "The library is busy." }, { status: 500 });
    ctx.module("navigator.js").navigatorCountsChanged({ now: true });
    await ctx.settle(60);
    assert.equal(ctx.rows("people").length, 413, "the rows that were read stay");
    assert.equal(ctx.status("people"), "The library is busy.");
  });
});

describe("the keys", () => {
  test("a tree is one tab stop; the arrows move through it, Right and Left open and close, Enter opens the view", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const tree = ctx.document.querySelector("#nav-panel-folders .nav-tree");
    const stops = () => ctx.rows("folders").filter((row) => row.tabIndex === 0);
    assert.equal(stops().length, 1);
    const top = ctx.rows("folders")[0];
    top.focus();
    ctx.key(top, "ArrowRight");
    assert.equal(ctx.rows("folders").length, 41, "Right opens a closed row");
    ctx.key(top, "ArrowRight");
    assert.equal(ctx.document.activeElement, ctx.rows("folders")[1], "Right on an open row goes to its first child");
    assert.equal(stops().length, 1);
    assert.equal(stops()[0], ctx.rows("folders")[1]);
    ctx.key(ctx.document.activeElement, "ArrowDown");
    assert.equal(ctx.document.activeElement, ctx.rows("folders")[2]);
    ctx.key(ctx.document.activeElement, "ArrowUp");
    ctx.key(ctx.document.activeElement, "ArrowLeft");
    assert.equal(ctx.document.activeElement, ctx.rows("folders")[0], "Left on a closed row goes to its parent");
    ctx.key(ctx.document.activeElement, "End");
    assert.equal(ctx.document.activeElement, ctx.rows("folders").at(-1));
    ctx.key(ctx.document.activeElement, "Home");
    assert.equal(ctx.document.activeElement, ctx.rows("folders")[0]);
    ctx.key(ctx.document.activeElement, "ArrowLeft");
    assert.equal(ctx.rows("folders").length, 1, "Left on an open row closes it");
    ctx.key(ctx.document.activeElement, "Enter");
    await ctx.settle();
    assert.match(ctx.idsAsked.at(-1), /kind=folder/);
    assert.ok(tree.getAttribute("role") === "tree");
    assert.equal(ctx.rows("folders")[0].getAttribute("aria-selected"), "true");
  });

  test("the tabs are a tab list: Left and Right move and select, Home and End go to the ends; one tab stop", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    const tabs = ["folders", "keywords", "people", "dates"].map((name) => ctx.tab(name));
    assert.deepEqual(tabs.map((tab) => tab.tabIndex), [0, -1, -1, -1]);
    ctx.key(tabs[0], "ArrowRight");
    await ctx.settle(20);
    assert.equal(ctx.state.nav.tab, "keywords");
    assert.equal(ctx.document.activeElement, tabs[1]);
    assert.deepEqual(tabs.map((tab) => tab.getAttribute("aria-selected")), ["false", "true", "false", "false"]);
    ctx.key(tabs[1], "End");
    assert.equal(ctx.state.nav.tab, "dates");
    ctx.key(tabs[3], "ArrowRight");
    assert.equal(ctx.state.nav.tab, "folders", "the ends wrap");
    ctx.key(tabs[0], "ArrowLeft");
    assert.equal(ctx.state.nav.tab, "dates");
    assert.equal(ctx.tab("dates").getAttribute("aria-controls"), "nav-panel-dates");
    assert.equal(ctx.panel("dates").getAttribute("role"), "tabpanel");
    await ctx.settle(100);
  });

  test("the arrow keys in the navigator do not step the open photo", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    await ctx.openTab("people");
    const row = ctx.rows("people")[0];
    row.focus();
    const before = ctx.photosAsked.length;
    ctx.key(row, "ArrowDown");
    ctx.key(ctx.document.activeElement, "ArrowRight");
    await ctx.settle(20);
    assert.equal(ctx.photosAsked.length, before);
    assert.equal(ctx.document.activeElement, ctx.rows("people")[1]);
  });
});

describe("the model, alone", () => {
  test("the junk years are the ones outside 1970 and next year", async (t) => {
    const ctx = await loadViewPage(t);
    const { indexDates } = ctx.module("navigator-model.js");
    const dates = indexDates(syntheticDates(2026), 2026);
    assert.equal(dates.size, 61);
    assert.ok(dates.usual.every((each) => each.year >= 1970 && each.year <= 2027));
    assert.ok(dates.odd.every((each) => each.year < 1970 || each.year > 2027));
    assert.equal(dates.usual.length + dates.odd.length, 61);
  });

  test("names that differ only in case are found as one, a cyclic tree ends, a folder whose parent is missing is a top", async (t) => {
    const ctx = await loadViewPage(t);
    const m = ctx.module("navigator-model.js");
    const keywords = m.indexKeywords([
      { tag: "A", name: "A", parent: "B", count: 1 }, { tag: "B", name: "B", parent: "A", count: 1 },
    ]);
    const located = m.locate("keywords", keywords, { kind: "keyword", value: "a" });
    assert.ok(located && located.open.length <= 2, "a damaged tree does not loop");
    const folders = m.indexFolders([{ path: "E:\\Orphan\\Sub", name: "Sub", parent: "E:\\Orphan", direct: 1, recursive: 1 }]);
    assert.equal(folders.tops.length, 1);
  });

  test("the folders of the sandbox library have the size the brief names", () => {
    assert.equal(syntheticFolders().length, 2746);
    assert.equal(syntheticKeywords().length, 895);
    assert.equal(syntheticPeople().length, 413);
    assert.equal(syntheticDates().years.length, 61);
  });
});
