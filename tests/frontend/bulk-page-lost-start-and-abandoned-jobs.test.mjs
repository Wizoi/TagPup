/**
 * The third review of the 9d-2 page (#624-#628): a lost start's answer is only a job begun since it was sent, an abandoned job (no end
 * time) forgets every scan, the lost start's job refreshes what a job's end does, a 409 with no sentence is neutral, and the details panel
 * saves when the active path is another spelling of the photo's. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, jobStatus, speedUp } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const el = (ctx, id) => ctx.document.getElementById(id);
const calls = (ctx, part) => ctx.server.calls.filter((call) => call.url.includes(part));
const text = (ctx, id) => el(ctx, id).textContent.replace(/\s+/g, " ").trim();
const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
const nowS = () => Math.floor(Date.now() / 1000);

async function view(t, n = 400) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: range(n) });
  speedUp(ctx);
  ctx.window.confirm = () => true;
  ctx.window.alert = () => {};
  ctx.bulk.start = { total: n };
  ctx.bulk.status = jobStatus({ total: n });
  return ctx;
}

async function addTag(ctx) {
  el(ctx, "bulk-add-tags-input").value = "Trips/Lighthouse";
  click(ctx.window, el(ctx, "btn-bulk-add-tags"));
  await ctx.settle(60);
}

describe("the third review (#624-#628)", () => {
  test("#624: after a lost start, an OLD job that stopped part-way is not taken for it", async (t) => {
    const ctx = await view(t);
    pick(ctx, 2);
    ctx.window.localStorage.setItem("tagpup_cache_d:/old", JSON.stringify({ timestamp: 1000 }));
    ctx.server.first("/api/library/bulk/start", () => Promise.reject(new Error("Failed to fetch")));
    ctx.bulk.current = jobStatus({ total: 100, done: 30, changed: 30, state: "cancelled", started: nowS() - 86400, finished: nowS() - 86000, message: "The bulk edit was cancelled." });
    await addTag(ctx);
    await ctx.settle(100);
    assert.match(text(ctx, "status-text"), /Could not start the bulk edit: Failed to fetch/);
    assert.ok(el(ctx, "bulk-strip").classList.contains("hidden"), "nothing shown");
    assert.notEqual(ctx.window.localStorage.getItem("tagpup_cache_d:/old"), null, "nothing refreshed");
  });

  test("#625: an abandoned job has no end time: every scan kept is forgotten", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(50) });
    ctx.window.localStorage.setItem("tagpup_cache_d:/a", JSON.stringify({ timestamp: Date.now() }));
    ctx.bulk.current = jobStatus({ total: 60, done: 25, changed: 25, state: "abandoned", finished: null, message: "closed." });
    await ctx.module("bulk-job.js").attachBulk({ force: true });
    assert.equal(ctx.window.localStorage.getItem("tagpup_cache_d:/a"), null);
  });

  test("#626: the lost start's job that stopped: the selection is counted again and the open photo is read again", async (t) => {
    const ctx = await view(t);
    pick(ctx, 2);
    await ctx.module("photo.js").openLibraryPhoto(2);
    await ctx.settle(700);
    const tallies = calls(ctx, "/selection/tally").length;
    ctx.photosAsked.length = 0;
    ctx.server.first("/api/library/bulk/start", () => Promise.reject(new Error("Failed to fetch")));
    ctx.bulk.current = jobStatus({ total: 1, done: 1, changed: 1, state: "cancelled", started: nowS(), finished: nowS(), message: "The bulk edit was cancelled." });
    await addTag(ctx);
    await ctx.settle(900);
    assert.match(text(ctx, "status-text"), /cancelled/);
    assert.ok(ctx.photosAsked.includes(2), "the open photo is read again");
    assert.ok(calls(ctx, "/selection/tally").length > tallies, "the selection is counted again");
  });

  test("#627: a 409 with no sentence is neutral: the did-not-start title, no Show it", async (t) => {
    const ctx = await view(t);
    pick(ctx, 2);
    ctx.server.first("/api/library/bulk/start", {}, { status: 409 });
    await addTag(ctx);
    assert.equal(text(ctx, "bulk-strip-title"), "The edit did not start");
    assert.match(text(ctx, "bulk-strip-message"), /refused the edit \(409\)/);
    assert.ok(el(ctx, "btn-bulk-show").classList.contains("hidden"));
  });

  test("#628: the details panel saves when the active path is another spelling of the photo's path", async (t) => {
    const ctx = await view(t);
    await ctx.module("photo.js").openLibraryPhoto(2);
    await ctx.settle(60);
    const photo = ctx.state.folderPhotos[0];
    ctx.state.activePhotoPath = photo.path.replace(/\\/g, "/").toLowerCase();
    el(ctx, "input-add-tag").value = "Trips/Lighthouse";
    await ctx.module("edits.js").saveDetailEdits({ title: false, tags: true, people: false });
    await ctx.settle(60);
    assert.equal(calls(ctx, "save-metadata").length, 1, "the write happened");
    assert.equal(el(ctx, "input-add-tag").value, "", "and the typed text was cleared");
  });
});
