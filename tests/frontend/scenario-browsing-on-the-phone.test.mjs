/**
 * Owner scenario: on the phone page (web/tagpup/mobile.html, docs/plans/phone-viewer.md, Screens 2) the owner finds
 * photos by walking Folders, Keywords, People or Dates down one level at a time, or by searching words, and opens them.
 *
 * - Each level is a step in history: the phone's Back goes up one level, and from the top of Browse to the list of
 *   libraries, not off the page (web/tagpup/mobile-screens.js).
 * - "Show these photos" and Search open the Grid on a view, the names of library-source.js (a folder with its subfolders);
 *   the Grid says how many photos it holds (web/tagpup/mobile-grid.js; its thumbnails are cut 2b).
 * - A section that could not be read says so with Try again; a slow reply never draws over a newer one; two quick taps
 *   open one level; an address naming a view opens its photos (web/tagpup/mobile-browse.js).
 * - Each row shows a cover in a placeholder of fixed size, asked only for the rows near the screen (docs/plans/
 *   phone-redesign.md, cut 2): a slow one, a failed one, one scrolled away, a level of 1,000 rows, a library switched; the
 *   top of Browse shows the people with the most photos. Each covers reply is one the real route made (harness.mjs coveredAs).
 *
 * Folder and person names hold spaces, apostrophes and a leading dot, as the live libraries' do (counted read-only
 * 2026-11-17: 197 folder names with an apostrophe in photo_index, 2,573 with a space). Every name here is made up.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps, pageExports, answer, intersect, observed, coveredAs, coverRowsOf }
  from "./harness.mjs";

const PAGE = "http://localhost:8090/alpha/mobile.html";
const ROOT = "D:\\Pictures";
const YEAR = `${ROOT}\\2019`;
const MEET = `${YEAR}\\Rowan's Meet`;

const FOLDERS = {
  folders: [
    { path: ROOT, name: "Pictures", parent: null, direct: 0, recursive: 30 },
    { path: YEAR, name: "2019", parent: ROOT, direct: 2, recursive: 20 },
    { path: `${ROOT}\\2020`, name: "2020", parent: ROOT, direct: 10, recursive: 10 },
    { path: MEET, name: "Rowan's Meet", parent: YEAR, direct: 12, recursive: 15 },
    { path: `${YEAR}\\Spring Day`, name: "Spring Day", parent: YEAR, direct: 3, recursive: 3 },
    { path: `${MEET}\\.edits`, name: ".edits", parent: MEET, direct: 3, recursive: 3 },
  ],
};

const KEYWORDS = {
  keywords: [
    { tag: "Events", name: "Events", parent: null, count: 12 },
    { tag: "Events/Track Meet", name: "Track Meet", parent: "Events", count: 7 },
  ],
  untagged: 3,
};

const IDS = { ids: [4, 5, 6], total: 3, of: 3 };

/** The cards of the ids asked (the Grid asks for the window's, mobile-grid.js): a photo with no card has left the library. */
const cardsOf = (url) => ({
  cards: decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").map(Number)
    .map((id) => ({ id, name: `IMG_${id}.jpg`, thumb: `/api/photo-thumb?id=${id}&v=1`, media: "photo" })),
});

const el = (ctx, id) => ctx.document.getElementById(id);

const shown = (ctx) =>
  [...ctx.document.querySelectorAll("[data-screen]")].filter((s) => !s.classList.contains("hidden")).map((s) => s.dataset.screen);

const rows = (ctx) => [...el(ctx, "browse-rows").querySelectorAll(".browse-item")];

const labels = (ctx) => rows(ctx).map((r) => r.querySelector(".browse-item-label").textContent);

const counts = (ctx) => rows(ctx).map((r) => r.querySelector(".browse-item-count").textContent);

const row = (ctx, label) => rows(ctx).find((r) => r.querySelector(".browse-item-label").textContent === label);

const section = (ctx, name) => ctx.document.querySelector(`[data-section="${name}"]`);

const pageState = (ctx) => pageExports(ctx.window, "web/tagpup/mobile-state.js").state;

const asked = (ctx, part) => ctx.server.urls().filter((u) => u.includes(part)).length;

const navigated = (ctx) => ctx.consoleErrors.some((e) => /navigation/i.test(e && e.message ? e.message : String(e)));

async function tap(ctx, element) {
  click(ctx.window, element);
  await flush(ctx.window);
}

/** Back, as the phone's gesture: the history steps back and the page hears popstate. */
async function back(ctx) {
  ctx.window.history.back();
  await flush(ctx.window);
}

function library() {
  return new FakeServer()
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/library/navigator?section=folders", FOLDERS)
    .on("/api/library/navigator?section=keywords", KEYWORDS)
    .on("/api/library/ids", IDS)
    .on("/api/library/cards", cardsOf);
}

afterEach(() => closeAllApps());

describe("browsing a library on the phone", () => {
  test("folders walked down three levels, and Back up one at a time to the top and then the list of libraries", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: PAGE });
    const start = ctx.window.history.length;
    assert.deepEqual(shown(ctx), ["browse"]);

    await tap(ctx, section(ctx, "folders"));
    assert.deepEqual(labels(ctx), ["Pictures"]);
    assert.deepEqual(counts(ctx), ["30"], "the count the model gives: with its subfolders");
    await tap(ctx, row(ctx, "Pictures"));
    assert.deepEqual(labels(ctx), ["2019", "2020"]);
    await tap(ctx, row(ctx, "2019"));
    assert.deepEqual(labels(ctx), ["Rowan's Meet", "Spring Day"]);
    await tap(ctx, row(ctx, "Rowan's Meet"));
    assert.deepEqual(labels(ctx), [".edits"]);
    assert.equal(el(ctx, "browse-path").textContent, "Folders \u203a Pictures \u203a 2019 \u203a Rowan's Meet");
    assert.equal(ctx.window.history.length, start + 4, "each level is a step in history");
    assert.equal(asked(ctx, "section=folders"), 1, "the levels under the top are drawn from the section read once");

    await back(ctx);
    assert.deepEqual(labels(ctx), ["Rowan's Meet", "Spring Day"]);
    await tap(ctx, el(ctx, "browse-up"));
    assert.deepEqual(labels(ctx), ["2019", "2020"], "the path's Back goes up a level too");
    await back(ctx);
    assert.deepEqual(labels(ctx), ["Pictures"]);
    await back(ctx);
    assert.ok(!el(ctx, "browse-top").classList.contains("hidden"), "the top of Browse: search and the four sections");
    assert.ok(el(ctx, "bar-back").classList.contains("hidden"));

    await back(ctx);
    assert.deepEqual(shown(ctx), ["library"], "Back at the top of Browse opens the list of libraries");
    assert.ok(!navigated(ctx), "not off the page");
    assert.ok(el(ctx, "bar-back").classList.contains("hidden"), "the list is the first step: nothing above it");
    await tap(ctx, el(ctx, "library-list").querySelector(".library-choice.current"));
    assert.deepEqual(shown(ctx), ["browse"], "the library open goes back to browsing it");
    assert.ok(!navigated(ctx));
  });

  test("a folder read that fails says so with Try again, never an empty list", async (t) => {
    const server = library().first("/api/library/navigator?section=folders", { error: "The library is busy." }, { status: 503 });
    const ctx = await loadApp("phone", { t, server, url: PAGE });

    await tap(ctx, section(ctx, "folders"));
    const problem = el(ctx, "browse-problem");
    assert.ok(!problem.classList.contains("hidden"));
    assert.match(problem.textContent, /Could not load folders\. The library is busy\./);
    assert.deepEqual(labels(ctx), []);
    assert.ok(el(ctx, "browse-show").classList.contains("hidden"), "nothing to show photos of");

    server.routes.shift();
    await tap(ctx, el(ctx, "browse-retry"));
    assert.equal(asked(ctx, "section=folders"), 2, "Try again asks once more");
    assert.ok(problem.classList.contains("hidden"));
    assert.deepEqual(labels(ctx), ["Pictures"]);
  });

  test("a slow first reply does not draw over a newer one", async (t) => {
    const held = [];
    let n = 0;
    const server = library().first("/api/library/navigator?section=folders", () => {
      n += 1;
      return n === 1 ? new Promise((resolve) => held.push(resolve)) : FOLDERS;
    });
    const ctx = await loadApp("phone", { t, server, url: PAGE });

    await tap(ctx, section(ctx, "folders"));
    assert.match(el(ctx, "browse-note").textContent, /Loading/);
    await back(ctx);
    await tap(ctx, section(ctx, "folders"));
    assert.deepEqual(labels(ctx), ["Pictures"]);

    held[0]({ folders: [{ path: "D:\\Elsewhere", name: "Elsewhere", parent: null, direct: 1, recursive: 1 }] });
    await flush(ctx.window);
    assert.deepEqual(labels(ctx), ["Pictures"], "the older reply is dropped");
    await tap(ctx, row(ctx, "Pictures"));
    assert.deepEqual(labels(ctx), ["2019", "2020"], "and the level under is the newer reply's");
  });

  test("two quick taps on a row open one level", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: PAGE });
    const start = ctx.window.history.length;

    const folders = section(ctx, "folders");
    click(ctx.window, folders);
    click(ctx.window, folders);
    await flush(ctx.window);
    const pictures = row(ctx, "Pictures");
    click(ctx.window, pictures);
    click(ctx.window, pictures);
    await flush(ctx.window);

    assert.deepEqual(labels(ctx), ["2019", "2020"], "one level under Pictures, not two");
    assert.equal(ctx.window.history.length, start + 2);
  });

  test("a keyword, then Show these photos, opens the grid on the keyword and everything under it", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: PAGE });

    await tap(ctx, section(ctx, "keywords"));
    assert.deepEqual(labels(ctx), ["Events", "No tag"]);
    await tap(ctx, row(ctx, "Events"));
    assert.deepEqual(labels(ctx), ["Track Meet"]);
    await tap(ctx, el(ctx, "browse-show"));

    assert.deepEqual(shown(ctx), ["grid"]);
    assert.deepEqual({ ...pageState(ctx).view },
      { kind: "keyword", value: "Events", recursive: false, order: "taken", media: "both" });
    const url = ctx.server.urls().find((u) => u.includes("/api/library/ids"));
    assert.ok(url.startsWith("/alpha/"), "asked of the page's library");
    assert.match(url, /kind=keyword&value=Events(&|$)/);
    assert.equal(el(ctx, "grid-count").textContent, "3 photos");

    await back(ctx);
    assert.deepEqual(labels(ctx), ["Track Meet"], "Back from the grid is the level it was opened from");
  });

  test("Show these photos on a folder holds its subfolders; a folder with none says so on the grid", async (t) => {
    const server = library().first("/api/library/ids", { ids: [], total: 0, of: 0 });
    const ctx = await loadApp("phone", { t, server, url: PAGE });

    await tap(ctx, section(ctx, "folders"));
    await tap(ctx, row(ctx, "Pictures"));
    await tap(ctx, row(ctx, "2019"));
    await tap(ctx, el(ctx, "browse-show"));

    assert.deepEqual({ ...pageState(ctx).view }, { kind: "folder", value: YEAR, recursive: true, order: "taken", media: "both" });
    const url = ctx.server.urls().find((u) => u.includes("/api/library/ids"));
    assert.ok(url.includes(`folder=${encodeURIComponent(YEAR)}&recursive=1`));
    assert.match(el(ctx, "grid-count").textContent, /holds no photo in .*2019 or below it/);
  });

  test("a search with words opens the grid on them", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: PAGE });

    el(ctx, "browse-words").value = "  relay   meet ";
    await tap(ctx, el(ctx, "browse-search").querySelector("button[type=submit]"));

    assert.deepEqual(shown(ctx), ["grid"]);
    assert.deepEqual(JSON.parse(JSON.stringify(pageState(ctx).view)),
      { kind: "search", value: { words: "relay meet" }, recursive: false, order: "taken", media: "both" });
    assert.deepEqual(ctx.server.lastBody("/api/library/ids"), { kind: "search", value: { words: "relay meet" } });
    assert.equal(el(ctx, "grid-count").textContent, "3 photos");
  });

  test("a search with no words does nothing", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: PAGE });
    const start = ctx.window.history.length;

    el(ctx, "browse-words").value = "   ";
    await tap(ctx, el(ctx, "browse-search").querySelector("button[type=submit]"));

    assert.deepEqual(shown(ctx), ["browse"]);
    assert.equal(ctx.window.history.length, start);
    assert.equal(asked(ctx, "/api/library/ids"), 0);
  });

  test("an address naming a view opens its photos; Back browses the library, then lists the libraries", async (t) => {
    const url = `${PAGE}?view=keyword&value=${encodeURIComponent("Events/Track Meet")}`;
    const ctx = await loadApp("phone", { t, server: library(), url });

    assert.deepEqual(shown(ctx), ["grid"]);
    assert.deepEqual({ ...pageState(ctx).view },
      { kind: "keyword", value: "Events/Track Meet", recursive: false, order: "taken", media: "both" });
    assert.ok(ctx.server.urls().some((u) => u.includes(`kind=keyword&value=${encodeURIComponent("Events/Track Meet")}`)));
    assert.equal(el(ctx, "grid-count").textContent, "3 photos");

    await back(ctx);
    assert.deepEqual(shown(ctx), ["browse"]);
    await back(ctx);
    assert.deepEqual(shown(ctx), ["library"]);
    assert.ok(!navigated(ctx));
  });

  test("an address naming a view TagPup does not know says so above Browse", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: `${PAGE}?view=albums&value=x` });

    assert.deepEqual(shown(ctx), ["browse"]);
    assert.ok(!el(ctx, "browse-link-problem").classList.contains("hidden"));
    assert.match(el(ctx, "browse-link-problem").textContent, /does not know/);
    assert.equal(asked(ctx, "/api/library/ids"), 0);
  });

  test("a second tap on a row of a level already left opens nothing more, even on another row", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: PAGE });
    await tap(ctx, section(ctx, "folders"));
    await tap(ctx, row(ctx, "Pictures"));
    const start = ctx.window.history.length;
    const [first, second] = [row(ctx, "2019"), row(ctx, "2020")];

    click(ctx.window, first);
    click(ctx.window, second); // the level under Pictures is already gone
    await flush(ctx.window);

    assert.deepEqual(labels(ctx), ["Rowan's Meet", "Spring Day"], "the level of the first tap, not the second row's");
    assert.equal(ctx.window.history.length, start + 1);
  });

  test("opening the screen already shown, at the same place, is not a second step", async (t) => {
    const ctx = await loadApp("phone", { t, server: library(), url: PAGE });
    const { openScreen } = pageExports(ctx.window, "web/tagpup/mobile-screens.js");
    await tap(ctx, section(ctx, "folders"));
    const start = ctx.window.history.length;

    openScreen("browse", ["folders", "p"]);
    openScreen("browse", ["folders", "p"]);

    assert.equal(ctx.window.history.length, start + 1);
  });

  test("people filed in groups: down through a subgroup to a person, and a person's photos are asked by their tag path", async (t) => {
    const people = {
      groups: [
        { tag: "People/Family", name: "Family", parent: null, count: 9 },
        { tag: "People/Family/Cousins", name: "Cousins", parent: "People/Family", count: 4 },
      ],
      people: [
        { name: "Wren Alder", count: 4, group: "People/Family/Cousins", person_id: 11, person: { tag: "People/Family/Cousins/Wren Alder" } },
        { name: "Sam Birch", count: 5, group: "People/Family", person_id: 12, person: { tag: "People/Family/Sam Birch" } },
      ],
      unfiled: 0,
    };
    const server = library().on("/api/library/navigator?section=people", people);
    const ctx = await loadApp("phone", { t, server, url: PAGE });
    const asView = () => JSON.parse(JSON.stringify(pageState(ctx).view));

    await tap(ctx, section(ctx, "people"));
    assert.deepEqual(labels(ctx), ["Family"]);
    await tap(ctx, row(ctx, "Family"));
    assert.deepEqual(labels(ctx), ["Cousins", "Sam Birch"], "the subgroup before the people");
    await tap(ctx, el(ctx, "browse-show"));
    assert.equal(asView().kind, "any_of");
    assert.deepEqual(asView().value, [
      { kind: "person", value: "People/Family/Cousins/Wren Alder", recursive: false },
      { kind: "person", value: "People/Family/Sam Birch", recursive: false },
    ], "a group shows its people, each by tag path");

    await back(ctx);
    await tap(ctx, row(ctx, "Cousins"));
    assert.deepEqual(labels(ctx), ["Wren Alder"]);
    assert.equal(el(ctx, "browse-path").textContent, "People › Family › Cousins");
    await tap(ctx, el(ctx, "browse-show"));
    assert.equal(asView().kind, "person");
    assert.equal(asView().value, "People/Family/Cousins/Wren Alder", "never the leaf alone");

    await back(ctx);
    await tap(ctx, row(ctx, "Wren Alder"));
    assert.deepEqual(shown(ctx), ["grid"], "a person has nothing under: a tap opens their photos");
    assert.equal(asView().value, "People/Family/Cousins/Wren Alder");
    assert.ok(ctx.server.urls().filter((u) => u.includes("/api/library/ids")).at(-1)
      .includes(encodeURIComponent("People/Family/Cousins/Wren Alder")));
  });

  test("dates: a year's months and its Other; odd years apart under Other years, drilled to their months", async (t) => {
    const dates = {
      years: [
        { year: 2021, count: 5, months: [{ month: "2021-03", count: 3 }], other: 2 },
        { year: 1850, count: 4, months: [{ month: "1850-06", count: 3 }], other: 1, implausible: true },
      ],
      undated: 0,
    };
    const server = library().on("/api/library/navigator?section=dates", dates);
    const ctx = await loadApp("phone", { t, server, url: PAGE });
    const asView = () => JSON.parse(JSON.stringify(pageState(ctx).view));

    await tap(ctx, section(ctx, "dates"));
    assert.deepEqual(labels(ctx), ["2021", "Other years (1)"]);
    await tap(ctx, row(ctx, "2021"));
    assert.deepEqual(labels(ctx), ["March", "Other"], "a year's months, and its photos naming no month");
    await tap(ctx, row(ctx, "Other"));
    assert.deepEqual([asView().kind, asView().value], ["year_other", "2021"]);

    await back(ctx);
    await back(ctx);
    await tap(ctx, row(ctx, "Other years (1)"));
    assert.deepEqual(labels(ctx), ["1850"]);
    await tap(ctx, row(ctx, "1850"));
    assert.deepEqual(labels(ctx), ["June", "Other"], "level three: the odd year's months");
    assert.equal(el(ctx, "browse-path").textContent, "Dates › Other years (1) › 1850");
    await tap(ctx, row(ctx, "June"));
    assert.deepEqual([asView().kind, asView().value], ["month", "1850-06"]);
  });

  test("a reload at a level shows that level again; a level gone from the library says so", async (t) => {
    const first = await loadApp("phone", { t, server: library(), url: PAGE });
    await tap(first, section(first, "folders"));
    await tap(first, row(first, "Pictures"));
    await tap(first, row(first, "2019"));
    const kept = JSON.parse(JSON.stringify(first.window.history.state));
    assert.equal(kept.screen, "browse");
    assert.equal(kept.place[0], "folders", "history holds [section, row ids...]");
    assert.equal(kept.place.length, 3);

    const reload = (server) => loadApp("phone", {
      t, server, url: PAGE, before: (window) => window.history.replaceState(kept, ""),
    });
    const again = await reload(library());
    assert.deepEqual(shown(again), ["browse"]);
    assert.deepEqual(labels(again), ["Rowan's Meet", "Spring Day"], "the level, after the section is read again");
    assert.equal(el(again, "browse-path").textContent, "Folders › Pictures › 2019");

    const changed = new FakeServer()
      .on("/api/databases", { databases: ["alpha", "beta"] })
      .on("/api/library/navigator?section=folders", { folders: [{ path: ROOT, name: "Pictures", parent: null, direct: 1, recursive: 1 }] })
      .on("/api/library/ids", IDS);
    const gone = await reload(changed);
    assert.deepEqual(labels(gone), []);
    assert.equal(el(gone, "browse-note").textContent, "This is no longer in the library.");
    assert.ok(!el(gone, "browse-note").classList.contains("hidden"));
    assert.ok(el(gone, "browse-show").classList.contains("hidden"), "no photos offered of a level that is not there");
  });
});

/** The cover boxes of the level shown, in its rows' order. */
const boxes = (ctx) => rows(ctx).map((r) => r.querySelector(".browse-cover"));

/** The thumbnails drawn in `list` (boxes), each its address or null for a placeholder alone. */
const drawn = (list) => list.map((box) => box.querySelector("img")?.getAttribute("src") ?? null);

/** The covers asks made, each the rows it named (coverRowsOf). */
const coverAsks = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/library/covers")).map(coverRowsOf);

/**
 * A library whose covers route holds each ask until the test lets it go (`release(n, photoOf)`, the reply the real route
 * makes, coveredAs; `fail(n)`, a 503 as a busy library answers).
 */
function heldCovers(server = library()) {
  const held = [];
  server.first("/api/library/covers", (url) => new Promise((resolve) => held.push({ url, resolve })));
  return {
    server,
    held,
    release: (n, photoOf = () => 1) => held[n].resolve(coveredAs(held[n].url, photoOf)),
    fail: (n) => held[n].resolve(answer(503, { error: "The library is busy." })),
  };
}

describe("covers on Browse's rows", () => {
  test("a folder of 1,000 subfolders: every row drawn with its placeholder and count, covers asked only near the screen, 24 an ask, two out at once", async (t) => {
    const days = Array.from({ length: 1000 }, (_, n) => `Day ${String(n + 1).padStart(4, "0")}`);
    const server = library().first("/api/library/navigator?section=folders", {
      folders: [
        { path: ROOT, name: "Pictures", parent: null, direct: 0, recursive: 1000 },
        ...days.map((name) => ({ path: `${ROOT}\\${name}`, name, parent: ROOT, direct: 1, recursive: 1 })),
      ],
    });
    const covers = heldCovers(server);
    const ctx = await loadApp("phone", { t, server, url: PAGE });
    await tap(ctx, section(ctx, "folders"));
    await tap(ctx, row(ctx, "Pictures"));

    assert.equal(rows(ctx).length, 1000);
    assert.ok(boxes(ctx).every((box) => box && !box.querySelector("img")), "each row its placeholder, before anything is asked");
    assert.equal(counts(ctx)[0], "1", "and its count");
    assert.equal(observed(ctx.window).length, 1000, "the rows of the level shown are watched, the level above's no more");
    assert.equal(coverAsks(ctx).length, 0, "nothing is near the screen until the browser says so");

    await intersect(ctx.window, boxes(ctx).slice(0, 100));
    assert.deepEqual(coverAsks(ctx).map((asked) => asked.length), [24, 24], "two asks out at once, 24 rows each");
    const [first] = coverAsks(ctx);
    assert.deepEqual(first[0], { id: first[0].id, kind: "folder", value: `${ROOT}\\Day 0001`, recursive: true },
      "a row's source as a tap opens it: the folder with its subfolders");

    // A fling: the first hundred leave before the rest of them were asked, the last hundred come near.
    await intersect(ctx.window, boxes(ctx).slice(0, 100), false);
    await intersect(ctx.window, boxes(ctx).slice(900));
    covers.release(0);
    await flush(ctx.window);
    assert.equal(coverAsks(ctx).length, 3);
    assert.equal(coverAsks(ctx)[2][0].value, `${ROOT}\\Day 0901`, "the rows that left unasked are not asked; the ones near are");
    assert.equal(drawn(boxes(ctx).slice(0, 24)).filter(Boolean).length, 24, "the answer for rows scrolled away is kept and drawn");
    assert.ok(coverAsks(ctx).every((asked) => asked.length <= 24));
  });

  test("a slow cover: the placeholder holds, the row opens, and the cover lands on the level drawn again without a second ask", async (t) => {
    const covers = heldCovers();
    const ctx = await loadApp("phone", { t, server: covers.server, url: PAGE });
    await tap(ctx, section(ctx, "folders"));
    await tap(ctx, row(ctx, "Pictures"));
    await intersect(ctx.window, boxes(ctx));
    assert.deepEqual(coverAsks(ctx).map((asked) => asked.map((each) => each.value)), [[YEAR, `${ROOT}\\2020`]]);
    assert.ok(ctx.server.urls().find((u) => u.includes("/api/library/covers")).startsWith("/alpha/api/library/covers?section=folders&"));
    assert.deepEqual(drawn(boxes(ctx)), [null, null]);

    await tap(ctx, row(ctx, "2019"));
    assert.deepEqual(labels(ctx), ["Rowan's Meet", "Spring Day"], "the row is a button while its cover is out");
    await back(ctx);
    covers.release(0, (asked) => (asked.value === YEAR ? 7 : null));
    await flush(ctx.window);

    const [thumb, none] = drawn(boxes(ctx));
    assert.match(thumb, /^\/alpha\/api\/photo-thumb\?id=7&v=/, "the reply's thumbnail, asked of the page's library");
    assert.equal(none, null, "a folder with no photo keeps its placeholder");
    const img = boxes(ctx)[0].querySelector("img");
    assert.deepEqual([img.getAttribute("loading"), img.getAttribute("decoding")], ["lazy", "async"]);
    await intersect(ctx.window, boxes(ctx));
    assert.equal(coverAsks(ctx).length, 1, "answered, a row is not asked again");
  });

  test("a cover read that fails leaves the placeholder and the row tappable; near the screen again, it is asked again", async (t) => {
    const covers = heldCovers();
    const ctx = await loadApp("phone", { t, server: covers.server, url: PAGE });
    await tap(ctx, section(ctx, "folders"));
    await tap(ctx, row(ctx, "Pictures"));
    await intersect(ctx.window, boxes(ctx));
    covers.fail(0);
    await flush(ctx.window);

    assert.deepEqual(drawn(boxes(ctx)), [null, null]);
    assert.ok(el(ctx, "browse-problem").classList.contains("hidden"), "a cover never says Browse failed");
    await intersect(ctx.window, boxes(ctx), false);
    await intersect(ctx.window, boxes(ctx));
    assert.equal(coverAsks(ctx).length, 2);
    covers.release(1, () => 3);
    await flush(ctx.window);
    assert.equal(drawn(boxes(ctx)).filter(Boolean).length, 2);
    await tap(ctx, row(ctx, "2020"));
    assert.deepEqual(shown(ctx), ["grid"]);
  });

  test("no cover is asked while a row's photos are open; back on Browse the answers kept are drawn", async (t) => {
    const covers = heldCovers();
    const ctx = await loadApp("phone", { t, server: covers.server, url: PAGE });
    await tap(ctx, section(ctx, "keywords"));
    await intersect(ctx.window, boxes(ctx));
    assert.equal(coverAsks(ctx).length, 1);
    assert.deepEqual(coverAsks(ctx)[0].map((each) => each.kind), ["keyword", "untagged"], "No tag's cover too");

    await tap(ctx, row(ctx, "No tag"));
    assert.deepEqual(shown(ctx), ["grid"]);
    const left = boxes(ctx);
    covers.release(0, () => 5);
    await flush(ctx.window);
    await intersect(ctx.window, left);
    assert.equal(coverAsks(ctx).length, 1, "the grid is shown: nothing more is asked");

    await back(ctx);
    assert.deepEqual(drawn(boxes(ctx)).map((src) => src && src.includes("id=5&")), [true, true], "the answers kept, drawn at once");
    assert.equal(coverAsks(ctx).length, 1);
  });

  test("the library switched mid-scroll: the covers out land in this page alone, and nothing more is asked", async (t) => {
    const covers = heldCovers();
    const ctx = await loadApp("phone", { t, server: covers.server, url: PAGE });
    const { openScreen } = pageExports(ctx.window, "web/tagpup/mobile-screens.js");
    await tap(ctx, section(ctx, "folders"));
    await tap(ctx, row(ctx, "Pictures"));
    await intersect(ctx.window, boxes(ctx));

    openScreen("library");
    await flush(ctx.window);
    const beta = [...el(ctx, "library-list").querySelectorAll(".library-choice")].find((b) => b.textContent === "beta");
    await tap(ctx, beta);
    assert.ok(navigated(ctx), "another library is another page");
    covers.release(0, () => 9);
    await flush(ctx.window);

    assert.equal(coverAsks(ctx).length, 1);
    assert.ok(ctx.server.urls().filter((u) => u.includes("/api/library/covers")).every((u) => u.startsWith("/alpha/")));
  });

  test("the top of Browse: the people with the most photos, each with a cover; a tap opens their photos; See all opens People", async (t) => {
    const people = {
      groups: [{ tag: "People/Family", name: "Family", parent: null, count: 9 }],
      people: [
        { name: "Ada Lindqvist", count: 2, group: "People/Family", person_id: 21, person: { tag: "People/Family/Ada Lindqvist" } },
        { name: "Rowan Thackeray", count: 40, group: "People/Family", person_id: 22, person: { tag: "People/Family/Rowan Thackeray" } },
        { name: "Mina Okafor", count: 1, group: null, person_id: 23, person: { tag: "People/Mina Okafor" } },
      ],
      unfiled: 1,
    };
    const covers = heldCovers(library().on("/api/library/navigator?section=people", people));
    const ctx = await loadApp("phone", { t, server: covers.server, url: PAGE });
    const strip = ctx.document.querySelector(".browse-people");
    const tiles = () => [...strip.querySelectorAll(".browse-person")];

    assert.ok(!strip.classList.contains("hidden"));
    assert.equal(strip.previousElementSibling, el(ctx, "browse-search"), "under the search box");
    assert.deepEqual(tiles().map((b) => b.querySelector(".browse-person-name").textContent),
      ["Rowan Thackeray", "Ada Lindqvist", "Mina Okafor"], "most photos first, whatever branch files them");
    assert.deepEqual(tiles().map((b) => b.querySelector(".browse-person-count").textContent), ["40", "2", "1"]);

    await intersect(ctx.window, tiles().map((b) => b.querySelector(".browse-cover")));
    assert.ok(ctx.server.urls().find((u) => u.includes("/api/library/covers")).includes("section=people&"));
    assert.deepEqual(coverAsks(ctx)[0].map(({ kind, value }) => [kind, value]), [
      ["person", "People/Family/Rowan Thackeray"], ["person", "People/Family/Ada Lindqvist"], ["person", "People/Mina Okafor"],
    ], "each by tag path");
    covers.release(0, (asked) => (asked.value.endsWith("Rowan Thackeray") ? 12 : null));
    await flush(ctx.window);
    assert.deepEqual(drawn(tiles().map((b) => b.querySelector(".browse-cover"))).map(Boolean), [true, false, false]);

    await tap(ctx, tiles()[0]);
    assert.deepEqual(JSON.parse(JSON.stringify(pageState(ctx).view)),
      { kind: "person", value: "People/Family/Rowan Thackeray", recursive: false, order: "taken", media: "both" });
    await back(ctx);
    await tap(ctx, strip.querySelector(".browse-people-all"));
    assert.deepEqual([...pageState(ctx).place], ["people"]);
    assert.deepEqual(labels(ctx), ["Family", "Not filed in the tag tree"]);
    assert.ok(drawn(boxes(ctx)).every((src) => src === null), "a branch's cover is its own ask");
  });

  test("a library naming no one shows no People strip", async (t) => {
    const ctx = await loadApp("phone", { t, server: library().on("/api/library/navigator?section=people", { groups: [], people: [], unfiled: 0 }), url: PAGE });
    assert.ok(ctx.document.querySelector(".browse-people").classList.contains("hidden"));
    assert.equal(coverAsks(ctx).length, 0);
  });
});
