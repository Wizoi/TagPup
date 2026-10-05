/**
 * TagTuner: click the photo in Face Crop Details to see it as large as the window allows,
 * the face boxed where it sits.
 *
 * The pane's photo is a 512px copy, 220px tall. The zoom is the one TagPup uses
 * (web/common/image-zoom.js); here it asks for the original from the same route, the
 * pane's preview as its first frame, and the face's box in the stored pixels drawn over
 * it by the arithmetic of a picture fitted inside its window.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps } from "./harness.mjs";
import { boxInContainedImage } from "../../web/common/image-zoom.js";

const NAME = "Unknown Faces";

function face(id, box) {
  return {
    id,
    photo_path: `D:\\xc\\photo${id}.jpg`,
    filename: `photo${id}.jpg`,
    box,
    prob: 0.99,
    mtime: 0,
    year: 2026,
    similarity: 0.0,
    cluster_id: -1,
    cluster_name: "Unclustered",
    other_names: [],
    suggested_name: null,
    suggested_similarity: 0,
    suggestion_strength: null,
  };
}

const FACES = [face(1, [1000, 500, 1400, 900]), face(2, [20, 30, 120, 130])];

async function open(t) {
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: NAME, count: FACES.length, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", {
      faces: FACES, total: FACES.length, total_count: FACES.length, unclustered_total: FACES.length,
      unclustered_shown: FACES.length, has_more: false, page: 1, limit: -1,
    })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photo-details", { tags: [], people: [] })
    .on("/api/face-matches", []);
  const { window, document } = await loadApp("tagtuner", {
    t, server,
    url: `http://localhost:8080/kr-track/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
  });
  await new Promise((r) => window.setTimeout(r, 140));
  return { window, document };
}

const settle = (window) => new Promise((r) => window.setTimeout(r, 40));
const card = (document, id) => document.querySelector(`#matching-faces-grid .face-match-item[data-face-id="${id}"]`);
const pane = (document) => document.getElementById("matching-detail-img");
const overlay = (document) => document.getElementById("image-zoom");
const isOpen = (document) => !overlay(document).classList.contains("hidden");

async function selectFace(window, document, id) {
  click(window, card(document, id));
  await settle(window);
}

function press(window, document, key) {
  const ev = new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  (document.activeElement || document).dispatchEvent(ev);
  return ev;
}

/** The zoom's image as a browser that has loaded `w` by `h` into a window `area` large. */
function loaded(window, document, { w, h, area }) {
  const img = document.getElementById("image-zoom-img");
  Object.defineProperty(img, "complete", { value: true, configurable: true });
  Object.defineProperty(img, "naturalWidth", { value: w, configurable: true });
  Object.defineProperty(img, "naturalHeight", { value: h, configurable: true });
  const rect = (width, height) => ({ left: 0, top: 0, right: width, bottom: height, width, height });
  img.style.padding = "4px";                      // the stylesheet is not loaded here
  img.getBoundingClientRect = () => rect(area.width, area.height);
  overlay(document).getBoundingClientRect = () => rect(area.width, area.height);
  img.dispatchEvent(new window.Event("load"));
  return img;
}

afterEach(() => closeAllApps());

describe("where a box falls on a letterboxed picture", () => {
  test("a landscape photo in a taller window is centred, scaled by its width", () => {
    // 4000 x 3000 in 1000 x 1000: scale 0.25, 750 high, 125 above and below.
    const placed = boxInContainedImage({ width: 4000, height: 3000 }, { width: 1000, height: 1000 }, [1000, 500, 2000, 1500]);
    assert.deepEqual(placed, { left: 250, top: 125 + 125, width: 250, height: 250 });
  });

  test("a portrait photo in a wider window is centred, scaled by its height", () => {
    // 3000 x 4000 in 2000 x 1000: scale 0.25, 750 wide, 625 each side.
    const placed = boxInContainedImage({ width: 3000, height: 4000 }, { width: 2000, height: 1000 }, [0, 0, 400, 400]);
    assert.deepEqual(placed, { left: 625, top: 0, width: 100, height: 100 });
  });

  test("a box past the picture's edge is cut at it, and one outside it is not drawn", () => {
    const area = { width: 100, height: 100 };
    const natural = { width: 100, height: 100 };
    assert.deepEqual(boxInContainedImage(natural, area, [90, 90, 140, 140]), { left: 90, top: 90, width: 10, height: 10 });
    assert.equal(boxInContainedImage(natural, area, [120, 120, 140, 140]), null);
    assert.equal(boxInContainedImage(natural, area, [1, 2, 3]), null);
    assert.equal(boxInContainedImage(natural, area, [1, 2, NaN, 4]), null);
    assert.equal(boxInContainedImage({ width: 0, height: 0 }, area, [1, 2, 3, 4]), null);
  });
});

describe("zooming the Face Crop Details photo", () => {
  test("the photo says it zooms, and can be reached by keyboard", async (t) => {
    const { document } = await open(t);
    const img = pane(document);
    assert.match(img.title, /full size/i);
    assert.ok(img.classList.contains("zoomable"));
    assert.equal(img.tabIndex, 0);
  });

  test("a click opens the original from the pane's route, the pane's copy behind it", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    assert.equal(isOpen(document), false);
    click(window, pane(document));
    assert.equal(isOpen(document), true, "the click did not open the zoom");
    const img = document.getElementById("image-zoom-img");
    const src = new URL(img.src);
    assert.match(src.pathname, /\/kr-track\/api\/photo-file$/);
    assert.equal(src.searchParams.get("path"), "D:\\xc\\photo1.jpg");
    assert.equal(src.searchParams.has("size"), false, "the zoom loads a smaller copy");
    assert.match(img.style.backgroundImage, /size=512/);
  });

  test("Enter and Space on the focused photo open it", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    for (const key of ["Enter", " "]) {
      pane(document).focus();
      const ev = press(window, document, key);
      assert.equal(isOpen(document), true, `${JSON.stringify(key)} did not open it`);
      assert.equal(ev.defaultPrevented, true);
      press(window, document, "Escape");
      assert.equal(isOpen(document), false);
    }
  });

  test("the box is drawn over the original, in the same place as on the picture", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    click(window, pane(document));
    const boxEl = document.querySelector(".image-zoom-box");
    assert.ok(boxEl.classList.contains("hidden"), "a box before there is a picture to put it on");
    // 4000 x 3000 original in a 1000 x 1000 window (the zoom image has 4px of padding).
    loaded(window, document, { w: 4000, h: 3000, area: { width: 1008, height: 1008 } });
    assert.equal(boxEl.classList.contains("hidden"), false);
    // scale 1000/4000; image offset by 4px padding, centred 125px down.
    assert.equal(parseFloat(boxEl.style.left), 4 + 250);
    assert.equal(parseFloat(boxEl.style.top), 4 + 125 + 125);
    assert.equal(parseFloat(boxEl.style.width), 100);
    assert.equal(parseFloat(boxEl.style.height), 100);
    assert.equal(document.getElementById("image-zoom-img").style.backgroundImage, "");
  });

  test("a photo that will not load says so, after trying the smaller copy once", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    click(window, pane(document));
    const img = document.getElementById("image-zoom-img");
    const note = document.querySelector(".image-zoom-note");
    img.dispatchEvent(new window.Event("error"));
    assert.match(new URL(img.src).searchParams.get("size"), /512/, "did not fall back to the copy");
    assert.match(note.textContent, /smaller copy/);
    assert.equal(document.querySelector(".image-zoom-box").classList.contains("hidden"), true,
      "a box drawn on a copy whose pixels are not the original's");
    img.dispatchEvent(new window.Event("error"));
    assert.equal(img.getAttribute("src"), null, "tried the copy again, and again");
    assert.match(note.textContent, /could not be loaded/);
    assert.equal(note.classList.contains("hidden"), false);
  });

  test("Escape closes it from a text box, leaves the text, and gives the focus back", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    const typed = document.getElementById("input-reassign-name");
    typed.value = "Rory";
    pane(document).focus();
    click(window, pane(document));
    typed.focus();                                  // the focus is in a box elsewhere
    press(window, document, "Escape");
    assert.equal(isOpen(document), false);
    assert.equal(typed.value, "Rory");
    assert.equal(document.activeElement, pane(document), "the focus did not go back to the photo");
  });

  test("the arrow keys do not move the page behind it", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    click(window, pane(document));
    const ev = press(window, document, "ArrowDown");
    assert.equal(ev.defaultPrevented, true);
    assert.equal(isOpen(document), true);
  });

  test("a different face selected behind it does not change what it shows", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    click(window, pane(document));
    await selectFace(window, document, 2);          // a refresh, or a key the page still heard
    const src = new URL(document.getElementById("image-zoom-img").src);
    assert.equal(src.searchParams.get("path"), "D:\\xc\\photo1.jpg");
    assert.equal(isOpen(document), true);
    // Closed and opened again, it is the face now in the pane.
    press(window, document, "Escape");
    click(window, pane(document));
    assert.equal(new URL(document.getElementById("image-zoom-img").src).searchParams.get("path"), "D:\\xc\\photo2.jpg");
    loaded(window, document, { w: 200, h: 200, area: { width: 208, height: 208 } });
    assert.equal(parseFloat(document.querySelector(".image-zoom-box").style.width), 100,
      "the box of the face that was open before");
  });

  test("rapid clicks open it once and one Escape closes it", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    for (let i = 0; i < 5; i++) click(window, pane(document));
    assert.equal(document.querySelectorAll("#image-zoom").length, 1);
    press(window, document, "Escape");
    assert.equal(isOpen(document), false);
    assert.equal(document.getElementById("image-zoom-img").getAttribute("src"), null);
  });

  test("a click on the overlay closes it", async (t) => {
    const { window, document } = await open(t);
    await selectFace(window, document, 1);
    click(window, pane(document));
    click(window, document.getElementById("image-zoom-img"));
    assert.equal(isOpen(document), false);
  });

  test("the Original Image of the photo being tuned opens the same zoom, no box", async (t) => {
    const { window, document } = await open(t);
    const main = document.getElementById("main-image");
    main.src = "/kr-track/api/photo-file?path=D%3A%5Cxc%5Cphoto1.jpg&size=1024";
    click(window, main);
    assert.equal(isOpen(document), true);
    const src = new URL(document.getElementById("image-zoom-img").src);
    assert.equal(src.searchParams.get("path"), "D:\\xc\\photo1.jpg");
    assert.equal(src.searchParams.has("size"), false);
    loaded(window, document, { w: 400, h: 300, area: { width: 400, height: 300 } });
    assert.equal(document.querySelector(".image-zoom-box").classList.contains("hidden"), true);
  });
});
