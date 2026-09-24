/**
 * After a rotate, the grid and the photo show the new turn, not a cached old one.
 *
 * Photo images are served with a day's caching, and their URL never changed: a rotate
 * reloaded the main image once with a throwaway query string, and left the grid's
 * thumbnail -- and the main image on the next visit -- to come from the cache, turned
 * the old way. Every image URL now carries the photo's mtime, which a rotate changes.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\p\\a.jpg", filename: "a.jpg", tags: [], people: [], captions: [], title: "", mtime: 1000 },
  { path: "D:\\p\\b.jpg", filename: "b.jpg", tags: [], people: [], captions: [], title: "", mtime: 1000 },
];

describe("a rotate refreshes every image of the photo", () => {
  afterEach(() => closeAllApps());

  test("the grid thumbnail and the main image get the new version", async (t) => {
    const server = new FakeServer()
      .on("/api/folder/scan", PHOTOS.map((p) => ({ ...p })))
      .on("/api/taxonomy/tree", [])
      .on("/api/people", [])
      .on("/api/tags", [])
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on("/api/photo/rotate", { success: true, mtime: 2000 });
    const { window, document } = await loadApp("tagpup", { server, t });
    window.alert = () => {};
    await openFolder({ window, document }, "D:\\p");
    await new Promise((r) => window.setTimeout(r, 60));

    const thumb = () => document.querySelector('#thumbnails-grid [data-path="D:\\\\p\\\\a.jpg"] img');
    assert.ok(thumb(), "fixture: the grid shows the photo");
    const before = thumb().getAttribute("src");

    document.querySelector('#photo-list .photo-item-file[data-path="D:\\\\p\\\\a.jpg"]')
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 40));
    document.getElementById("btn-rotate-left").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 60));

    assert.notEqual(thumb().getAttribute("src"), before, "the grid kept the cached old turn");
    assert.match(thumb().getAttribute("src"), /v=2000/);
    assert.match(document.getElementById("main-image").getAttribute("src"), /v=2000/);
  });
});
