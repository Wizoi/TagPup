/**
 * Clicking the path in Image Details opens the photo in its default app.
 *
 * It asked Explorer to select the file with the whole "/select,<path>" switch
 * quoted, which Explorer does not parse as a selection -- what happened depended on
 * how it treated the malformed argument. Opening the photo is what the click is for;
 * "Show in File Explorer" stays on the right-click menu.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const PHOTO = { path: "D:\\Pictures\\Run 2026\\Meet - 01.jpg", filename: "Meet - 01.jpg",
                tags: [], people: [], captions: [], title: "" };

function server() {
  return new FakeServer()
    .on("/api/folder/scan", [{ ...PHOTO }])
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/photo/open", { success: true })
    .on("/api/photo/open-explorer", { success: true });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

afterEach(() => closeAllApps());

test("clicking the path opens the photo, not Explorer", async (t) => {
  const fake = server();
  const { window, document } = await loadApp("tagpup", { server: fake, t });
  await openFolder({ document, window }, "D:\\Pictures\\Run 2026");
  await settle(window, 60);
  click(window, document.querySelector(".photo-item-file"));
  await settle(window);

  const link = document.getElementById("detail-path");
  assert.equal(link.title, "Open in default app");
  click(window, link);
  await settle(window);

  assert.deepEqual(fake.lastBody("/api/photo/open"), { path: PHOTO.path });
  assert.equal(fake.urls().filter((u) => u.includes("open-explorer")).length, 0);
});
