/**
 * Delete finds the photo to take off the display after the request, by its path (findings #532).
 *
 * deleteActivePhoto spliced folderPhotos by an index found before the request, so a folder
 * opened while the delete was in flight lost the wrong card from the display.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports } from "./harness.mjs";

const FIRST = "D:/Library/2020";
const SECOND = "D:/Library/2021";

const inFirst = ["a", "b"].map((n) => photoRecord({ filename: `${n}.jpg`, path: `D:\\Library\\2020\\${n}.jpg` }));
const inSecond = ["c", "d", "e"].map((n) => photoRecord({ filename: `${n}.jpg`, path: `D:\\Library\\2021\\${n}.jpg` }));

afterEach(() => closeAllApps());

describe("a delete in flight while another folder is opened", () => {
  test("takes nothing off the new folder's display", async (t) => {
    const release = [];
    const server = new FakeServer()
      .on("/api/tags", [])
      .on("/api/people", [])
      .on("/api/taxonomy/tree", [])
      .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
      .on("/api/autocomplete-folder", [])
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/photo/delete", () => new Promise((resolve) => release.push(() => resolve({ success: true }))))
      .on("/api/folder/scan", (url) => (url.includes("2021") ? inSecond : inFirst).map((p) => ({ ...p })));
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
    ctx.window.confirm = () => true;
    ctx.window.alert = () => {};
    await openFolder(ctx, FIRST);
    click(ctx.window, ctx.document.querySelector(`li[data-path="${inFirst[1].path.replace(/\\/g, "\\\\")}"]`));
    await flush(ctx.window, 6);

    click(ctx.window, ctx.document.getElementById("btn-delete-photo"));
    await flush(ctx.window, 4);
    assert.equal(release.length, 1, "the delete was not sent");

    await openFolder(ctx, SECOND);
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.equal(state.folderPhotos.length, 3, "the second folder did not open");

    release.shift()();
    await flush(ctx.window, 8);
    assert.deepEqual(state.folderPhotos.map((p) => p.filename), ["c.jpg", "d.jpg", "e.jpg"],
      "a card of the folder now open was taken off for a photo of the one before");
    // The first folder's scan kept in the browser no longer lists the photo that was deleted (#880).
    const key = Object.keys(ctx.window.localStorage).find((k) => /^tagpup_cache_.*2020$/.test(k));
    const kept = key && JSON.parse(ctx.window.localStorage.getItem(key));
    assert.ok(kept, "the first folder's scan was not kept");
    assert.deepEqual(kept.photos.map((p) => p.filename), ["a.jpg"], "the kept scan still lists the deleted photo");
  });
});
