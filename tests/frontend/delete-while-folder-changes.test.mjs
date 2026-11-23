/**
 * Delete finds the photo to take off the display after the request, by its path (findings #532).
 *
 * deleteActivePhoto spliced folderPhotos by an index found before the request, so a folder
 * opened while the delete was in flight lost the wrong card from the display.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports, deletedAs } from "./harness.mjs";

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
      .on("/api/photo/delete", () => new Promise((resolve) => release.push(() => resolve(deletedAs("binned")()))))
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
    // The first folder's scan kept in the browser, which lists the photo deleted, is forgotten (#880; cache.js photosGone).
    const key = Object.keys(ctx.window.localStorage).find((k) => /^tagpup_cache_.*2020$/.test(k));
    assert.equal(key, undefined, "the kept scan still lists the deleted photo");
  });

  test("a photo's own folder opened (Back from a library view) while its delete was out loses its card at once", async (t) => {
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
      .on("/api/photo/delete", () => new Promise((resolve) => release.push(() => resolve(deletedAs("binned")()))))
      .on("/api/folder/scan", () => inFirst.map((p) => ({ ...p })));
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
    ctx.window.confirm = () => true;
    ctx.window.alert = () => {};
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    await openFolder(ctx, FIRST);
    click(ctx.window, ctx.document.querySelector(`li[data-path="${inFirst[1].path.replace(/\\/g, "\\\\")}"]`));
    await flush(ctx.window, 6);
    // The photo was opened from a library view: that view is what the delete was asked in.
    state.library = { ids: [], activeId: null };

    click(ctx.window, ctx.document.getElementById("btn-delete-photo"));
    await flush(ctx.window, 4);
    assert.equal(release.length, 1, "the delete was not sent");

    // Back opens the photo's own folder (the kept scan holds the photo), and the view is no longer the open one.
    state.library = null;
    await openFolder(ctx, FIRST);
    assert.equal(state.folderPhotos.length, 2, "the folder did not open");

    release.shift()();
    await flush(ctx.window, 8);
    assert.deepEqual(state.folderPhotos.map((p) => p.filename), ["a.jpg"], "the record is still held");
    const card = (p) => ctx.document.querySelector(`[data-path="${p.path.replace(/\\/g, "\\\\")}"]`);
    assert.equal(card(inFirst[1]), null, "the grid or the list still shows the deleted photo's card");
    assert.ok(card(inFirst[0]), "the other photo lost its card");
  });
});
