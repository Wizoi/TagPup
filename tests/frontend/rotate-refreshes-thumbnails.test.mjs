/**
 * After a rotate, the grid and the photo show the new turn, not a cached old one.
 *
 * Photo images are served with a day's caching, and their URL never changed: a rotate
 * reloaded the main image once with a throwaway query string, and left the grid's
 * thumbnail -- and the main image on the next visit -- to come from the cache, turned
 * the old way. Every image URL now carries the photo's mtime, which a rotate changes.
 *
 * A rotate is a write of the photo, and waits its turn behind a save of it (#2587): posted at once, it turned the file
 * under a queued save, which then named the stamp from before the turn and was refused as changed on disk.
 *
 * Mirror, beside the two, is the same write asked with the direction "mirror": the same images, the same queue.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, PHOTO_WRITES, SAVE } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\p\\a.jpg", filename: "a.jpg", tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, mtime: 1000 },
  { path: "D:\\p\\b.jpg", filename: "b.jpg", tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, mtime: 1000 },
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

  test("Mirror asks the route to turn the photo with the direction mirror, and the photo gets the new version", async (t) => {
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
    document.querySelector('#photo-list .photo-item-file[data-path="D:\\\\p\\\\a.jpg"]')
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 40));
    document.getElementById("btn-mirror").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 60));

    assert.deepEqual(server.lastBody("/api/photo/rotate"), { path: "D:\\p\\a.jpg", direction: "mirror" });
    assert.match(document.getElementById("main-image").getAttribute("src"), /v=2000/);
  });

  test("a rotate whose stamp the route could not look at reads the photo again for its real stamp", async (t) => {
    // The route answers shares.known_stamp(path) or (None, None): a share slow to answer gives no stamp.
    let scans = 0;
    const server = new FakeServer()
      .on("/api/folder/scan", () => {
        scans += 1;
        return PHOTOS.map((p) => ({ ...p, size: 500, ...(scans > 1 && p.filename === "a.jpg" ? { mtime: 3000, size: 640 } : {}) }));
      })
      .on("/api/taxonomy/tree", [])
      .on("/api/people", [])
      .on("/api/tags", [])
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on("/api/photo/rotate", { success: true, mtime: null, size: null });
    const { window, document } = await loadApp("tagpup", { server, t });
    window.alert = () => {};
    await openFolder({ window, document }, "D:\\p");
    await new Promise((r) => window.setTimeout(r, 60));
    document.querySelector('#photo-list .photo-item-file[data-path="D:\\\\p\\\\a.jpg"]')
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 40));
    const before = scans;
    document.getElementById("btn-rotate-left").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 120));

    assert.equal(scans, before + 1, "the photo was read again after a rotate with no stamp");
    assert.match(document.getElementById("main-image").getAttribute("src"), /v=3000/);
  });

  for (const [button, direction] of [["btn-rotate-left", "left"], ["btn-mirror", "mirror"]]) {
  test(`${button} clicked while a save of the photo is under way waits for the save to answer`, async (t) => {
    const held = [];
    const server = new FakeServer()
      .on("/api/folder/scan", PHOTOS.map((p) => ({ ...p, size: 500 })))
      .on("/api/taxonomy/tree", [])
      .on("/api/people", [])
      .on("/api/tags", [])
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on(SAVE, () => new Promise((resolve) => held.push(() => resolve({ success: true, mtime: 1500, size: 520 }))))
      .on("/api/photo/rotate", { success: true, mtime: 2000, size: 530 });
    const { window, document } = await loadApp("tagpup", { server, t });
    window.alert = () => {};
    await openFolder({ window, document }, "D:\\p");
    await new Promise((r) => window.setTimeout(r, 60));
    document.querySelector('#photo-list .photo-item-file[data-path="D:\\\\p\\\\a.jpg"]')
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 40));

    const input = document.getElementById("input-add-title") || document.getElementById("input-photo-title");
    input.focus();
    input.value = "Sports day";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    document.getElementById("btn-save-details").click();
    await new Promise((r) => window.setTimeout(r, 40));
    assert.equal(held.length, 1, "fixture: the save is held on the server");

    document.getElementById(button).dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 60));
    const rotates = () => server.calls.filter((c) => c.url.includes("/api/photo/rotate"));
    assert.equal(rotates().length, 0, "the rotate was posted while the save of the photo was under way");

    held.shift()();
    await new Promise((r) => window.setTimeout(r, 80));
    assert.equal(rotates().length, 1, "the rotate was not posted once the save answered");
    assert.equal(server.lastBody("/api/photo/rotate").direction, direction);
    assert.match(document.getElementById("main-image").getAttribute("src"), /v=2000/);
  });
  }
});
