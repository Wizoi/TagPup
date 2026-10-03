/**
 * A bulk write leaves out the photos whose cards say their files are gone (findings #566; phase 9c): Select all, a Shift
 * range and a click can all select one, and the page drops it from the paths it sends, says how many were left out, names
 * the count actually written in the confirmation, shows the missing ones in the selection panel, and sends nothing when
 * every selected photo is missing. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, cardOf } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const gone = (ids) => (id) => (ids.includes(id) ? { stale: "missing" } : {});
const status = (ctx) => ctx.document.getElementById("status-text").textContent;

async function selectAllThenAddTag(ctx) {
  ctx.document.getElementById("btn-select-all-thumbnails").click();
  await ctx.settle();
  ctx.document.getElementById("bulk-add-tags-input").value = "Trips/Lighthouse";
  click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
  await ctx.settle();
}

describe("a bulk write leaves out the missing", () => {
  test("Select all, then Add: the missing are not sent, and the status says how many were left out", async (t) => {
    const ids = Array.from({ length: 8 }, (_, i) => 11 + i);
    const ctx = await loadViewPage(t, { search: "?view=all", ids, cardExtra: gone([13, 15]) });
    ctx.server.first("/api/photos/bulk-tags", { success: true, written: {}, skipped: [], skipped_missing: 0 });
    await selectAllThenAddTag(ctx);
    const sent = ctx.server.lastBody("/api/photos/bulk-tags");
    assert.equal(sent.paths.length, 6);
    assert.ok(!sent.paths.includes(cardOf(13).path) && !sent.paths.includes(cardOf(15).path));
    assert.match(status(ctx), /^2 photos are missing on disk and were left out/);
    assert.equal(ctx.state.selectedThumbnails.length, 8, "they stay selected, and are shown as left out");
  });

  test("a Shift range across unloaded cards leaves them out too", async (t) => {
    const ids = Array.from({ length: 400 }, (_, i) => 1000 + i);
    const ctx = await loadViewPage(t, { search: "?view=all", ids, cardExtra: gone([1100, 1300]) });
    ctx.server.first("/api/photos/bulk-tags", { success: true, written: {}, skipped: [], skipped_missing: 0 });
    ctx.cardById(1000).focus();
    ctx.key(ctx.document.activeElement, "End", { shiftKey: true });
    await ctx.settle(500);
    assert.equal(ctx.state.selectedThumbnails.length, 400);
    ctx.document.getElementById("bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
    await ctx.settle();
    assert.equal(ctx.server.lastBody("/api/photos/bulk-tags").paths.length, 398);
  });

  test("the confirmation for a large selection names the count that will be written", async (t) => {
    const ids = Array.from({ length: 300 }, (_, i) => 1000 + i);
    const missing = ids.slice(0, 50);
    const ctx = await loadViewPage(t, { search: "?view=all", ids, cardExtra: gone(missing) });
    ctx.server.first("/api/photos/bulk-tags", { success: true, written: {}, skipped: [], skipped_missing: 0 });
    const asked = [];
    ctx.window.confirm = (text) => { asked.push(text); return true; };
    await selectAllThenAddTag(ctx);
    assert.deepEqual(asked, ["Add Trips/Lighthouse to 250 photos?"]);
    assert.equal(ctx.server.lastBody("/api/photos/bulk-tags").paths.length, 250);
  });

  test("the selection panel shows how many of the selected are left out", async (t) => {
    const ids = Array.from({ length: 8 }, (_, i) => 11 + i);
    const ctx = await loadViewPage(t, { search: "?view=all", ids, cardExtra: gone([13]) });
    const note = ctx.document.getElementById("selection-missing-note");
    assert.ok(note.classList.contains("hidden"));
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    await ctx.settle();
    assert.ok(!note.classList.contains("hidden"));
    assert.match(note.textContent, /^1 photo is missing on disk and was left out of bulk edits\.$/);
    ctx.document.getElementById("btn-select-none-thumbnails").click();
    assert.ok(note.classList.contains("hidden"));
  });

  test("a selection of only missing photos writes nothing, and says so", async (t) => {
    const ids = [11, 12, 13];
    const ctx = await loadViewPage(t, { search: "?view=all", ids, cardExtra: () => ({ stale: "missing" }) });
    await selectAllThenAddTag(ctx);
    assert.equal(ctx.server.calls.filter((call) => call.url.includes("bulk-tags")).length, 0);
    assert.match(status(ctx), /^Nothing was written: all 3 selected photos are missing on disk\.$/);
  });

  test("photos the page did not know were missing but the server skipped are said too, and keep their records", async (t) => {
    const ids = [11, 12, 13];
    const ctx = await loadViewPage(t, { search: "?view=all", ids });
    ctx.server.first("/api/photos/bulk-tags", {
      success: true, written: {}, skipped_missing: 1, skipped: [{ path: cardOf(12).path, why: "missing, nothing is written to it: its file is not on disk" }],
    });
    await selectAllThenAddTag(ctx);
    assert.match(status(ctx), /^1 photo is missing on disk and was left out/);
  });

  test("a library with none missing writes all and says Ready", async (t) => {
    const ids = [11, 12, 13];
    const ctx = await loadViewPage(t, { search: "?view=all", ids });
    ctx.server.first("/api/photos/bulk-tags", { success: true, written: {}, skipped: [], skipped_missing: 0 });
    await selectAllThenAddTag(ctx);
    assert.equal(ctx.server.lastBody("/api/photos/bulk-tags").paths.length, 3);
    assert.equal(status(ctx), "Ready");
  });

  test("a photo opened and found missing is left out from then on", async (t) => {
    const ids = [11, 12, 13];
    const ctx = await loadViewPage(t, { search: "?view=all", ids, onPhoto: (id) => ({ photo: { ...(id === 12 ? { missing: true } : {}), id, path: cardOf(id).path, filename: `IMG_${id}.jpg`, tags: [], people: [], title: "", mtime: 1, size: 1, raw_metadata: {} } }) });
    ctx.server.first("/api/photos/bulk-tags", { success: true, written: {}, skipped: [], skipped_missing: 0 });
    ctx.cardById(12).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(100);
    ctx.document.getElementById("folder-view-header").click();
    await ctx.settle(100);
    await selectAllThenAddTag(ctx);
    assert.equal(ctx.server.lastBody("/api/photos/bulk-tags").paths.length, 2);
  });
});
