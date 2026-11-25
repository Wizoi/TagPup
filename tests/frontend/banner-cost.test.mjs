/**
 * The "photos on disk the library lacks" banner does not walk a folder on every view change (findings #568; phase 9c): the
 * answer is kept per folder for the page's life and forgotten by Refresh view, an Add and a change in when the library was
 * last in step; it is asked by itself only for a view of this folder only and for a view with subfolders of under 1,000
 * photos; a larger view is asked by Refresh view (the owner, 2026-11-13: the quiet link "Check this folder on disk for new
 * photos" that asked it is gone). Fictional names only.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, testClock } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const FOLDER = "D:\\Library\\2020\\Event 01";
const VIEW = `?view=folder&value=${encodeURIComponent(FOLDER)}&recursive=1`;
const ONLY = `?view=folder&value=${encodeURIComponent(FOLDER)}`;
const OFFER = { folder: FOLDER, photos: 12, photos_held: 7, photos_not_held: 5, folders_not_held: 1 };
const banner = (ctx) => ctx.document.getElementById("moves-banner");
const text = (ctx) => ctx.document.getElementById("moves-banner-text").textContent;
const refresh = (ctx) => ctx.document.getElementById("btn-library-refresh");
const many = (n) => Array.from({ length: n }, (_, i) => 1000 + i);

describe("what the disk held is kept for the page", () => {
  test("another view and back does not walk the folder again", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, membership: OFFER });
    assert.equal(ctx.membershipAsked.length, 1);
    await ctx.popTo("?view=all");
    await ctx.popTo(VIEW);
    await ctx.popTo(ONLY);
    await ctx.popTo(VIEW);
    assert.equal(ctx.membershipAsked.length, 1, "asked once");
    assert.equal(text(ctx), "5 photos in this folder are not in photo_index.", "and still said");
  });

  test("Refresh view forgets it and asks again", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, membership: OFFER });
    ctx.document.getElementById("btn-library-refresh").click();
    await ctx.settle(150);
    assert.equal(ctx.membershipAsked.length, 2);
  });

  test("an Add forgets it", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, membership: OFFER });
    ctx.server.on("/api/folder/add", { success: true });
    ctx.document.getElementById("btn-moves-add").click();
    ctx.document.getElementById("btn-add-folder").click();
    await ctx.settle(80);
    assert.equal(ctx.state.moves.cache.size, 0);
  });

  test("a change in when the library was last in step forgets it; the same answer again does not", async (t) => {
    let when = "2026-10-01 10:00:00";
    const ctx = await loadViewPage(t, { search: VIEW, membership: OFFER, sync: () => ({ library: "photo_index", last_in_step: when, syncing: false }) });
    assert.equal(ctx.state.moves.cache.size, 1);
    await ctx.popTo("?view=all");
    assert.equal(ctx.state.moves.cache.size, 1, "the same sync answer: kept");
    when = "2026-10-02 10:00:00";
    await ctx.popTo("?view=year&value=2020");
    assert.equal(ctx.state.moves.cache.size, 0, "a sync finished since: forgotten");
    const before = ctx.membershipAsked.length;
    await ctx.popTo(VIEW);
    assert.equal(ctx.membershipAsked.length, before + 1);
  });

  test("a sync that starts or ends is a change too", async (t) => {
    let syncing = false;
    const ctx = await loadViewPage(t, { search: VIEW, membership: OFFER, sync: () => ({ library: "photo_index", last_in_step: null, syncing }) });
    syncing = true;
    await ctx.popTo("?view=all");
    assert.equal(ctx.state.moves.cache.size, 0);
  });

  test("a 'could not check' is not kept: the next view asks again", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, membership: { folder: FOLDER, could_not_check: true, why: "the network share is away" } });
    assert.ok(banner(ctx).classList.contains("hidden"), "nothing shown unasked");
    assert.equal(ctx.state.moves.cache.size, 0);
    await ctx.popTo("?view=all");
    await ctx.popTo(VIEW);
    assert.equal(ctx.membershipAsked.length, 2);
  });
});

describe("a large view is asked by Refresh view", () => {
  test("a view with subfolders of 1,000 photos or more asks nothing when it opens and shows nothing: no link beside Refresh", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: OFFER });
    assert.equal(ctx.membershipAsked.length, 0, "no walk");
    assert.ok(banner(ctx).classList.contains("hidden"));
    assert.equal(ctx.document.getElementById("btn-moves-check"), null, "the quiet link is gone");
    assert.match(refresh(ctx).title, /also look on disk for photos the library does not hold yet/);
  });

  test("Refresh view asks, and the offer appears; the answer is kept for the page", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: OFFER });
    refresh(ctx).click();
    await ctx.settle(150);
    assert.equal(ctx.membershipAsked.length, 1);
    assert.equal(text(ctx), "5 photos in this folder are not in photo_index.");
    assert.ok(!banner(ctx).classList.contains("hidden"));
    assert.ok(!ctx.document.getElementById("btn-moves-add").classList.contains("hidden"));
    assert.ok(!ctx.document.getElementById("btn-moves-dismiss").classList.contains("hidden"));
    // And it is kept: the view opened again does not walk.
    await ctx.popTo("?view=all");
    await ctx.popTo(VIEW);
    assert.equal(ctx.membershipAsked.length, 1);
    assert.equal(text(ctx), "5 photos in this folder are not in photo_index.");
  });

  test("when nothing is missing from the library, Refresh shows nothing", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: { ...OFFER, photos_not_held: 0 } });
    refresh(ctx).click();
    await ctx.settle(150);
    assert.equal(ctx.membershipAsked.length, 1);
    assert.ok(banner(ctx).classList.contains("hidden"));
  });

  test("a share that does not answer: Refresh shows nothing, keeps nothing, and the next Refresh asks again", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: { folder: FOLDER, could_not_check: true, why: "the network share is away" } });
    refresh(ctx).click();
    await ctx.settle(150);
    assert.ok(banner(ctx).classList.contains("hidden"));
    assert.equal(ctx.state.moves.cache.size, 0);
    ctx.server.first("/api/folder/membership", () => Promise.reject(new TypeError("Failed to fetch")));
    refresh(ctx).click();
    await ctx.settle(150);
    assert.ok(banner(ctx).classList.contains("hidden"), "a request that fails is no fault either");
    assert.equal(ctx.server.urls().filter((url) => url.includes("/api/folder/membership")).length, 2, "each Refresh asks again");
  });

  test("rapid Refresh clicks: one question is listened to, an older answer arriving late is dropped", async (t) => {
    let missing = 2;
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: () => ({ ...OFFER, photos_not_held: missing }) });
    ctx.hold.membership = true;
    refresh(ctx).click();
    await ctx.settle(150);
    const first = ctx.state.moves.controller;
    missing = 7;
    refresh(ctx).click();
    await ctx.settle(150);
    assert.equal(first.signal.aborted, true, "the first question was given up when the second was asked");
    assert.equal(ctx.membershipAsked.length, 2);
    await ctx.release("membership");
    await ctx.settle(60);
    assert.equal(text(ctx), "7 photos in this folder are not in photo_index.", "the newer answer, not the older");
    assert.deepEqual([...ctx.state.moves.cache.values()].map((found) => found.photos_not_held), [7], "and it is the one kept");
  });

  test("another view while Refresh's question is out: its late answer is not shown there, nor kept", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: OFFER });
    ctx.hold.membership = true;
    refresh(ctx).click();
    await ctx.settle(150);
    assert.equal(ctx.membershipAsked.length, 1);
    await ctx.popTo("?view=keyword&value=Trips");
    await ctx.release("membership");
    await ctx.settle(60);
    assert.ok(banner(ctx).classList.contains("hidden"));
    assert.equal(ctx.state.moves.cache.size, 0);
  });

  test("a Refresh question that never answers is given up at its deadline; the view goes on being usable", async (t) => {
    const ctx = await loadViewPage(t, {
      search: VIEW, ids: many(1500), membership: OFFER,
      before: (window) => {
        const set = window.setTimeout.bind(window);
        window.setTimeout = (fn, ms, ...rest) => set(fn, ms === 15000 ? 30 : ms, ...rest);
      },
    });
    ctx.hold.membership = true;
    refresh(ctx).click();
    await ctx.settle(200);
    assert.equal(ctx.state.moves.controller.signal.aborted, true, "the request was abandoned");
    assert.ok(banner(ctx).classList.contains("hidden"));
    assert.equal(ctx.state.library.status, "ready");
    assert.equal(refresh(ctx).disabled, false, "Refresh can be pressed again");
  });

  test("a view of this folder only asks by itself however many photos it holds", async (t) => {
    const ctx = await loadViewPage(t, { search: ONLY, ids: many(1500), membership: OFFER });
    assert.equal(ctx.membershipAsked.length, 1);
    assert.equal(text(ctx), "5 photos in this folder are not in photo_index.");
  });

  test("a view with subfolders of under 1,000 photos asks by itself", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(999), membership: OFFER });
    assert.equal(ctx.membershipAsked.length, 1);
  });

  test("the offer goes with the view, and a dismissed folder is not asked again by Refresh", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: OFFER });
    refresh(ctx).click();
    await ctx.settle(150);
    await ctx.popTo("?view=all");
    assert.ok(banner(ctx).classList.contains("hidden"));
    await ctx.popTo(VIEW);
    ctx.document.getElementById("btn-moves-dismiss").click();
    refresh(ctx).click();
    await ctx.settle(150);
    assert.equal(ctx.membershipAsked.length, 1);
    assert.ok(banner(ctx).classList.contains("hidden"));
  });
});
