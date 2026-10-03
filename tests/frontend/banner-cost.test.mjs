/**
 * The "photos on disk the library lacks" banner does not walk a folder on every view change (findings #568; phase 9c): the
 * answer is kept per folder for the page's life and forgotten by Refresh view, an Add and a change in when the library was
 * last in step; it is asked by itself only for a view of this folder only and for a view with subfolders of under 1,000
 * photos; a larger view shows a quiet link that asks when clicked. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

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
const link = (ctx) => ctx.document.getElementById("btn-moves-check");
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

describe("a large view waits to be asked", () => {
  test("a view with subfolders of 1,000 photos or more shows a quiet link and asks nothing", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: OFFER });
    assert.equal(ctx.membershipAsked.length, 0, "no walk");
    assert.ok(!banner(ctx).classList.contains("hidden"));
    assert.ok(!link(ctx).classList.contains("hidden"));
    assert.equal(link(ctx).textContent, "Check this folder on disk for new photos");
    assert.ok(ctx.document.getElementById("btn-moves-add").classList.contains("hidden"));
  });

  test("clicking the link asks, and says what the disk holds", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: OFFER });
    link(ctx).click();
    await ctx.settle(80);
    assert.equal(ctx.membershipAsked.length, 1);
    assert.equal(text(ctx), "5 photos in this folder are not in photo_index.");
    assert.ok(link(ctx).classList.contains("hidden"));
    assert.ok(!ctx.document.getElementById("btn-moves-add").classList.contains("hidden"));
    // And it is kept: the view opened again does not walk.
    await ctx.popTo("?view=all");
    await ctx.popTo(VIEW);
    assert.equal(ctx.membershipAsked.length, 1);
  });

  test("when nothing is missing from the library it says so, quietly", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: { ...OFFER, photos_not_held: 0 } });
    link(ctx).click();
    await ctx.settle(80);
    assert.equal(text(ctx), "Every photo on disk in this folder is in the library.");
  });

  test("when the disk could not be checked it says so and offers the link again next time", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: { folder: FOLDER, could_not_check: true, why: "the network share is away" } });
    link(ctx).click();
    await ctx.settle(80);
    assert.match(text(ctx), /^Could not check this folder on disk just now \(the network share is away\)\.$/);
    assert.equal(ctx.state.moves.cache.size, 0);
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

  test("the quiet link goes with the view, and Refresh view of a large view offers it again, not a walk", async (t) => {
    const ctx = await loadViewPage(t, { search: VIEW, ids: many(1500), membership: OFFER });
    await ctx.popTo("?view=all");
    assert.ok(banner(ctx).classList.contains("hidden"));
    await ctx.popTo(VIEW);
    ctx.document.getElementById("btn-library-refresh").click();
    await ctx.settle(150);
    assert.equal(ctx.membershipAsked.length, 0);
    assert.ok(!link(ctx).classList.contains("hidden"));
  });
});
