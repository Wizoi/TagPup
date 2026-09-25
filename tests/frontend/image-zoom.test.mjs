/**
 * Click the photo to see it as big as the window allows; click again to close.
 *
 * For reading the writing on a sign or a name tag, which the 800px preview cannot
 * show. The zoom loads the original, and opening it is not leaving the photo.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\p\\a.jpg", filename: "a.jpg", tags: [], people: [], captions: [], title: "" },
  { path: "D:\\p\\b.jpg", filename: "b.jpg", tags: [], people: [], captions: [], title: "" },
];

function server() {
  return new FakeServer()
    .on("/api/folder/scan", PHOTOS.map((p) => ({ ...p })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

async function onFirstPhoto(t) {
  const { window, document } = await loadApp("tagpup", { server: server(), t });
  await openFolder({ document, window }, "D:\\p");
  await new Promise((r) => window.setTimeout(r, 60));
  press(document, "ArrowDown");
  return { window, document };
}

function press(document, key, mods = {}) {
  const ev = new document.defaultView.KeyboardEvent("keydown", {
    key, bubbles: true, cancelable: true, ...mods,
  });
  (document.activeElement || document).dispatchEvent(ev);
  return ev;
}

function activePath(document) {
  const el = document.querySelector(".photo-item-file.active");
  return el && el.getAttribute("data-path");
}

function zoom(document) {
  const el = document.getElementById("image-zoom");
  assert.ok(el, "no zoom overlay");
  return el;
}

const isOpen = (document) => !zoom(document).classList.contains("hidden");

afterEach(() => closeAllApps());

describe("zooming the photo", () => {
  test("a click on the photo opens it, from the original file", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    assert.equal(isOpen(document), false);
    click(window, document.getElementById("main-image"));
    assert.equal(isOpen(document), true, "the click did not open the zoom");

    const img = document.getElementById("image-zoom-img");
    const src = new URL(img.src);
    assert.match(src.pathname, /\/api\/photo-file$/);
    assert.equal(src.searchParams.get("path"), "D:\\p\\a.jpg");
    assert.equal(src.searchParams.has("size"), false, "the zoom loads the 800px preview");
    // The preview is already there, and is shown until the original arrives.
    assert.match(img.style.backgroundImage, /size=800/);
  });

  test("a click on it closes it", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    click(window, document.getElementById("main-image"));
    click(window, document.getElementById("image-zoom-img"));
    assert.equal(isOpen(document), false);
  });

  test("Escape closes it", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    click(window, document.getElementById("main-image"));
    press(document, "Escape");
    assert.equal(isOpen(document), false);
  });

  test("Escape closes the zoom and leaves typed text alone", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    const tagInput = document.getElementById("input-add-tag");
    tagInput.focus();
    tagInput.value = "Sunset";
    click(window, document.getElementById("main-image"));
    press(document, "Escape");
    assert.equal(isOpen(document), false);
    assert.equal(tagInput.value, "Sunset", "Escape also abandoned the typed tag");
  });

  test("the arrow keys do not change the photo underneath it", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    click(window, document.getElementById("main-image"));
    const ev = press(document, "ArrowDown");
    press(document, "ArrowRight");
    assert.equal(activePath(document), "D:\\p\\a.jpg", "navigated under the zoom");
    assert.equal(ev.defaultPrevented, true, "the page scrolled under the zoom");
    assert.equal(isOpen(document), true);
  });

  test("the arrow keys work again once it is closed", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    click(window, document.getElementById("main-image"));
    press(document, "Escape");
    press(document, "ArrowDown");
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });

  test("opening and closing it leaves nothing to save and asks nothing", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    const save = document.getElementById("btn-save-details");
    click(window, document.getElementById("main-image"));
    click(window, document.getElementById("image-zoom"));
    if (save) assert.equal(save.disabled, true, "zooming made the photo look edited");
    assert.equal(document.querySelector(".unsaved-edits-modal"), null);
    press(document, "ArrowDown");
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });

  test("a swipe is not a click: it moves on without opening the zoom", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    const image = document.getElementById("main-image");
    const down = new window.Event("pointerdown", { bubbles: true, cancelable: true });
    Object.assign(down, { pointerId: 1, pointerType: "mouse", button: 0, clientX: 300, clientY: 200 });
    image.dispatchEvent(down);
    const up = new window.Event("pointerup", { bubbles: true, cancelable: true });
    Object.assign(up, { pointerId: 1, pointerType: "mouse", button: 0, clientX: 150, clientY: 200 });
    image.dispatchEvent(up);
    click(window, image);             // the browser's click after a mouse drag

    assert.equal(activePath(document), "D:\\p\\b.jpg");
    assert.equal(isOpen(document), false, "the swipe also opened the zoom");
  });

  test("the photo shows it can be zoomed", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const { REPO_ROOT } = await import("./harness.mjs");
    const css = fs.readFileSync(path.join(REPO_ROOT, "web", "tagpup", "style.css"), "utf8");
    assert.match(css, /#main-image\s*\{[^}]*cursor:\s*zoom-in/);
    assert.match(css, /\.image-zoom-overlay\s*\{[^}]*cursor:\s*zoom-out/);
  });
});
