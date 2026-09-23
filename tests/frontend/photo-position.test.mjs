/**
 * "12 of 48" beside the Original Image heading: how far along you are.
 *
 * The count has to be over the list the arrow keys walk -- the rendered rows, after
 * any search -- or Next would not be N+1 of M.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\p\\beach_1.jpg", filename: "beach_1.jpg", tags: [], people: [], captions: [], title: "" },
  { path: "D:\\p\\city_1.jpg", filename: "city_1.jpg", tags: [], people: [], captions: [], title: "" },
  { path: "D:\\p\\beach_2.jpg", filename: "beach_2.jpg", tags: [], people: [], captions: [], title: "" },
];

function server() {
  return new FakeServer()
    .on("/api/folder/scan", PHOTOS.map((p) => ({ ...p })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/delete", { success: true })
    .on("/api/folder/suggest-status", { status: "idle" });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

async function opened(t) {
  const { window, document } = await loadApp("tagpup", { server: server(), t });
  window.confirm = () => true;
  await openFolder({ document, window }, "D:\\p");
  await settle(window, 60);
  return { window, document };
}

function press(document, key) {
  document.dispatchEvent(new document.defaultView.KeyboardEvent("keydown", {
    key, bubbles: true, cancelable: true,
  }));
}

function position(document) {
  const el = document.getElementById("photo-position");
  assert.ok(el, "no position counter");
  return el.classList.contains("hidden") ? null : el.textContent;
}

afterEach(() => closeAllApps());

describe("where you are in the list", () => {
  test("sits in the Original Image header", async (t) => {
    const { document } = await opened(t);
    const el = document.getElementById("photo-position");
    assert.ok(el && el.closest(".image-section .card-header"), "not in the image header");
  });

  test("is hidden with no photo open", async (t) => {
    const { document } = await opened(t);
    assert.equal(position(document), null);
  });

  test("says 1 of 3 on opening the first photo", async (t) => {
    const { document, window } = await opened(t);
    click(window, document.querySelectorAll(".photo-item-file")[0]);
    assert.equal(position(document), "1 of 3");
  });

  test("counts up and down with Next and Previous", async (t) => {
    const { document } = await opened(t);
    press(document, "ArrowDown");
    press(document, "ArrowDown");
    assert.equal(position(document), "2 of 3");
    press(document, "ArrowUp");
    assert.equal(position(document), "1 of 3");
  });

  test("stops at the ends with the list", async (t) => {
    const { document } = await opened(t);
    for (let i = 0; i < 6; i++) press(document, "ArrowDown");
    assert.equal(position(document), "3 of 3");
    for (let i = 0; i < 6; i++) press(document, "ArrowUp");
    assert.equal(position(document), "1 of 3");
  });

  test("counts within the list a search leaves", async (t) => {
    const { document, window } = await opened(t);
    click(window, document.querySelector('.photo-item-file[data-path="D:\\\\p\\\\beach_2.jpg"]'));
    assert.equal(position(document), "3 of 3");

    const search = document.getElementById("photo-search");
    search.value = "beach";
    search.dispatchEvent(new window.Event("input", { bubbles: true }));
    await settle(window, 250);   // the search is debounced
    assert.equal(position(document), "2 of 2");

    press(document, "ArrowUp");
    assert.equal(position(document), "1 of 2");
  });

  test("hides when the search leaves the open photo out", async (t) => {
    const { document, window } = await opened(t);
    click(window, document.querySelectorAll(".photo-item-file")[1]);   // city_1
    const search = document.getElementById("photo-search");
    search.value = "beach";
    search.dispatchEvent(new window.Event("input", { bubbles: true }));
    await settle(window, 250);
    assert.equal(position(document), null);
  });

  test("recounts after a delete", async (t) => {
    const { document, window } = await opened(t);
    press(document, "ArrowDown");
    press(document, "ArrowDown");          // city_1, 2 of 3
    document.getElementById("btn-delete-photo").click();
    await settle(window, 60);
    // The next photo takes its place: beach_2, now 2 of 2.
    assert.equal(position(document), "2 of 2");
  });

  test("hides on going back to the folder view", async (t) => {
    const { document, window } = await opened(t);
    press(document, "ArrowDown");
    click(window, document.getElementById("folder-view-header"));
    assert.equal(position(document), null);
  });
});
