/**
 * The selection panel of a library view counts what the selected photos carry on the server (web/tagpup/tally.js; docs/ARCHITECTURE.md,
 * phase 9d-2; POST /api/library/selection/tally from 9d-1): 250 ms after the selection stops changing, "counting..." while it is out, a
 * stale answer dropped, the lists alphabetical, "N more" over 500, nothing for no selection. A folder's panel is as it was. Fictional
 * names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const plain = (value) => JSON.parse(JSON.stringify(value));
const el = (ctx, id) => ctx.document.getElementById(id);
const tallies = (ctx) => ctx.server.calls.filter((call) => call.url.includes("/selection/tally"));
const chips = (ctx, list) => [...ctx.document.querySelectorAll(`#${list} .selection-summary-chip`)].map((chip) => chip.firstChild.textContent);
const listText = (ctx, list) => el(ctx, list).textContent.replace(/\s+/g, " ").trim();
const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));

async function view(t, n = 400) {
  return loadViewPage(t, { search: "?view=all", ids: range(n) });
}

describe("counting what the selection carries", () => {
  test("a selection of 1: asked 250 ms after the last change, once for three quick clicks, alphabetical, with counts", async (t) => {
    const ctx = await view(t);
    ctx.bulk.tally = {
      total: 3, more_tags: 0, more_people: 0,
      tags: [{ tag: "Zoo/Cats", count: 1 }, { tag: "Animals/Dogs", count: 3 }, { tag: "Trips/Coast", count: 2 }],
      people: [{ name: "Wren Halloway", count: 2 }, { name: "Ash Brookmire", count: 3 }],
    };
    pick(ctx, 2);
    pick(ctx, 3);
    pick(ctx, 4);
    assert.equal(tallies(ctx).length, 0, "not before the selection settles");
    assert.equal(listText(ctx, "selection-tags-list"), "counting\u2026");
    await ctx.settle(700);
    assert.equal(tallies(ctx).length, 1, "one request for three clicks");
    assert.deepEqual(plain(tallies(ctx)[0].body), { selection: { ids: [2, 3, 4] } });
    assert.deepEqual(chips(ctx, "selection-tags-list"), ["Animals/Dogs (3)", "Trips/Coast (2)", "Zoo/Cats (1)"]);
    assert.deepEqual(chips(ctx, "selection-people-list"), ["Ash Brookmire (3)", "Wren Halloway (2)"]);
    assert.ok(!/Not tallied/.test(el(ctx, "folder-selection-sidebar").textContent));
  });

  test("68,000 selected is the source and the excluded ids, never the ids", async (t) => {
    const ctx = await view(t, 68000);
    ctx.bulk.tally = { total: 67999, tags: [{ tag: "Trips/Coast", count: 40 }], more_tags: 0, people: [], more_people: 0 };
    el(ctx, "btn-select-all-thumbnails").click();
    pick(ctx, 5);
    await ctx.settle(700);
    assert.deepEqual(plain(tallies(ctx)[0].body), { selection: { source: { kind: "all", value: null, recursive: false }, excluded: [5] } });
    assert.deepEqual(chips(ctx, "selection-tags-list"), ["Trips/Coast (40)"]);
    assert.equal(listText(ctx, "selection-people-list"), "None");
  });

  test("a selection of 0 shows no tally and asks nothing; the lists are empty and the panel's scroll is hidden", async (t) => {
    const ctx = await view(t);
    pick(ctx, 2);
    await ctx.settle(700);
    const asked = tallies(ctx).length;
    el(ctx, "btn-select-none-thumbnails").click();
    await ctx.settle(700);
    assert.equal(tallies(ctx).length, asked);
    assert.equal(listText(ctx, "selection-tags-list"), "");
    assert.ok(ctx.document.querySelector(".selection-summary-scroll").classList.contains("hidden"));
  });

  test("an answer for a selection that has changed is dropped: only the newest is shown", async (t) => {
    const ctx = await view(t);
    const held = [];
    ctx.bulk.tally = () => new Promise((resolve) => held.push(resolve));
    pick(ctx, 2);
    await ctx.settle(700);
    assert.equal(held.length, 1);
    pick(ctx, 3);
    await ctx.settle(700);
    assert.equal(held.length, 2);
    held[1]({ total: 2, tags: [{ tag: "New/Answer", count: 2 }], more_tags: 0, people: [], more_people: 0 });
    await ctx.settle(60);
    held[0]({ total: 1, tags: [{ tag: "Old/Answer", count: 1 }], more_tags: 0, people: [], more_people: 0 });
    await ctx.settle(60);
    assert.deepEqual(chips(ctx, "selection-tags-list"), ["New/Answer (2)"]);
  });

  test("counting... shows while the request is out, and says so, with no old chips left to mislead", async (t) => {
    const ctx = await view(t);
    ctx.bulk.tally = { total: 1, tags: [{ tag: "Trips/Coast", count: 1 }], more_tags: 0, people: [], more_people: 0 };
    pick(ctx, 2);
    await ctx.settle(700);
    assert.deepEqual(chips(ctx, "selection-tags-list"), ["Trips/Coast (1)"]);
    ctx.bulk.tally = () => new Promise(() => {});
    pick(ctx, 3);
    assert.equal(listText(ctx, "selection-tags-list"), "counting\u2026");
    assert.equal(listText(ctx, "selection-people-list"), "counting\u2026");
  });

  test("over 500 the server keeps the most used and counts the rest: 'N more'", async (t) => {
    const ctx = await view(t);
    ctx.bulk.tally = {
      total: 9, more_tags: 120, more_people: 3,
      tags: range(500).map((n) => ({ tag: `Trips/Place ${String(n).padStart(3, "0")}`, count: 1 })), people: [{ name: "Wren Halloway", count: 9 }],
    };
    pick(ctx, 2);
    await ctx.settle(700);
    assert.equal(ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip").length, 500);
    assert.match(listText(ctx, "selection-tags-list"), /120 more, not listed$/);
    assert.match(listText(ctx, "selection-people-list"), /3 more, not listed$/);
  });

  test("a failing count says so, and the next selection counts again", async (t) => {
    const ctx = await view(t);
    ctx.server.first("/api/library/selection/tally", { error: "The library is busy." }, { status: 500 });
    pick(ctx, 2);
    await ctx.settle(700);
    assert.match(listText(ctx, "selection-tags-list"), /Could not count what these photos carry \(The library is busy\.\)\./);
    ctx.server.routes.shift();
    ctx.bulk.tally = { total: 2, tags: [{ tag: "Trips/Coast", count: 2 }], more_tags: 0, people: [], more_people: 0 };
    pick(ctx, 3);
    await ctx.settle(700);
    assert.deepEqual(chips(ctx, "selection-tags-list"), ["Trips/Coast (2)"]);
  });

  test("a selection no request can carry is not counted, and the panel points to the note that says why", async (t) => {
    const ctx = await view(t, 68000);
    ctx.module("selected.js").setIdRange(0, 29999, true);
    ctx.module("selection.js").updateSelectedThumbnailsCount();
    await ctx.settle(700);
    assert.equal(tallies(ctx).length, 0);
    assert.match(listText(ctx, "selection-tags-list"), /Not counted: see the note above\./);
    assert.match(el(ctx, "selection-note").textContent, /30,000 photos are selected/);
  });

  test("the whole library may be counted: more than a job takes is no limit to a tally", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(300), onIds: () => ({ source: {}, total: 250000, ids: range(300), complete: false }) });
    ctx.bulk.tally = { total: 250000, tags: [{ tag: "Trips/Coast", count: 9 }], more_tags: 0, people: [], more_people: 0 };
    el(ctx, "btn-select-all-thumbnails").click();
    await ctx.settle(700);
    assert.equal(tallies(ctx).length, 1);
    assert.deepEqual(chips(ctx, "selection-tags-list"), ["Trips/Coast (9)"]);
  });

  test("leaving the view while a count is out: its answer is not shown in the next view", async (t) => {
    const ctx = await view(t);
    const held = [];
    ctx.bulk.tally = () => new Promise((resolve) => held.push(resolve));
    pick(ctx, 2);
    await ctx.settle(700);
    await ctx.popTo("?view=year&value=2020");
    held[0]({ total: 1, tags: [{ tag: "Late/Answer", count: 1 }], more_tags: 0, people: [], more_people: 0 });
    await ctx.settle(60);
    assert.ok(!/Late\/Answer/.test(el(ctx, "folder-selection-sidebar").textContent));
  });
});

describe("a folder's selection panel is as it was", () => {
  test("it tallies the open folder's records itself and asks the library for nothing", async (t) => {
    const FOLDER = "D:\\Library\\2020\\Event 01";
    const scan = () => [1, 2, 3].map((n) => photoRecord({ filename: `IMG_000${n}.jpg`, path: `${FOLDER}\\IMG_000${n}.jpg`, tags: ["Trips/Coast"], people: [] }));
    const ctx = await loadViewPage(t, { search: "", scan });
    await openFolder(ctx, FOLDER);
    await ctx.settle(100);
    el(ctx, "btn-select-all-thumbnails").click();
    await ctx.settle(700);
    assert.equal(tallies(ctx).length, 0);
    assert.deepEqual(chips(ctx, "selection-tags-list"), ["Trips/Coast (3)"]);
  });
});
