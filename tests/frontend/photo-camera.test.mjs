/**
 * The camera and the lens a photo was taken with, on one line in Image Details (web/tagpup/photo.js showCamera).
 *
 * The server names them (tagpup.core.photo_meta.gear, in the page's photo record as `camera` and `lens`); the page joins what
 * there is, hides the line for a photo whose metadata names neither, and puts the whole line in the tooltip, since the line
 * is cut with an ellipsis in a narrow panel (style.css `one-line`).
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const BASE = { tags: [], people: [], captions: [], title: "", camera: "", lens: "" };
const BOTH = { ...BASE, path: "D:\\Pictures\\Run\\a.jpg", filename: "a.jpg", camera: "Tidewater TX R6m2", lens: "TX24-70mm f/2.8L II USM" };
const CAMERA = { ...BASE, path: "D:\\Pictures\\Run\\b.jpg", filename: "b.jpg", camera: "Lanternfly Pixel 8 Pro" };
const LENS = { ...BASE, path: "D:\\Pictures\\Run\\c.jpg", filename: "c.jpg", lens: "Objectif Élan 35mm F1.4" };
const SCAN = { ...BASE, path: "D:\\Pictures\\Run\\d.jpg", filename: "d.jpg" };
const OLD = { path: "D:\\Pictures\\Run\\e.jpg", filename: "e.jpg", tags: [], people: [], captions: [], title: "" };

function server() {
  return new FakeServer()
    .on("/api/folder/scan", [BOTH, CAMERA, LENS, SCAN, OLD])
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

afterEach(() => closeAllApps());

test("the panel shows the camera and the lens on one line, and nothing for a photo with neither", async (t) => {
  const { window, document } = await loadApp("tagpup", { server: server(), t });
  await openFolder({ document, window }, "D:\\Pictures\\Run");
  await settle(window, 60);
  const shown = () => ({
    text: document.getElementById("detail-camera").textContent,
    title: document.getElementById("detail-camera").title,
    hidden: document.getElementById("detail-camera-item").classList.contains("hidden"),
  });
  const open = async (filename) => {
    const item = [...document.querySelectorAll(".photo-item-file")].find((el) => el.dataset.path.endsWith("\\" + filename));
    click(window, item);
    await settle(window);
  };

  await open("a.jpg");
  assert.deepEqual(shown(), { text: "Tidewater TX R6m2 · TX24-70mm f/2.8L II USM", title: "Tidewater TX R6m2 · TX24-70mm f/2.8L II USM", hidden: false });
  await open("b.jpg");
  assert.deepEqual(shown(), { text: "Lanternfly Pixel 8 Pro", title: "Lanternfly Pixel 8 Pro", hidden: false });
  await open("c.jpg");
  assert.equal(shown().text, "Objectif Élan 35mm F1.4");
  await open("d.jpg");
  assert.deepEqual(shown(), { text: "", title: "", hidden: true }, "a scan has no camera: no line, no stale one from the photo before");
  await open("e.jpg");
  assert.equal(shown().hidden, true, "a record from a server that does not say it");
});

test("Shift Date Taken's list of cameras is by the record's camera, the name Image Details shows", async (t) => {
  const named = (filename, camera) => ({ ...BASE, path: "D:\\Pictures\\Shift\\" + filename, filename, camera });
  const fake = new FakeServer()
    .on("/api/folder/scan", [named("a.jpg", "Google Pixel 8 Pro"), named("b.jpg", "Pixel 8 Pro"), named("c.jpg", "Google Pixel 8 Pro"),
                             named("d.jpg", ""), { path: "D:\\Pictures\\Shift\\e.jpg", filename: "e.jpg", tags: [], people: [], captions: [], title: "" }])
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
  const { window, document } = await loadApp("tagpup", { server: fake, t });
  await openFolder({ document, window }, "D:\\Pictures\\Shift");
  await settle(window, 60);
  const options = [...document.getElementById("timeshift-camera-select").options].map((o) => [o.value, o.textContent]);
  assert.deepEqual(options, [
    ["All Cameras", "All Cameras (5 photos)"],
    ["Google Pixel 8 Pro", "Google Pixel 8 Pro (2 photos)"],
    ["Pixel 8 Pro", "Pixel 8 Pro (1 photos)"],
    ["Unknown Camera", "Unknown Camera (2 photos)"],
  ], "a make-less model and the same model with its make are two cameras, as the server's shift takes them");
});

// jsdom does not lay a page out, so what keeps the camera line from widening the details grid (a grid item is as wide as its
// content unless it may shrink: the grid scrolled sideways, as #720's did) is read from the stylesheet. The long live strings
// ('KODAK CX4310 DIGITAL CAMERA' with a lens; the camera and lens line of a phone) are cut with an ellipsis and in the tooltip.
test("the grid item may shrink and the camera line is cut with an ellipsis, not wrapped or widening", () => {
  const css = readFileSync(new URL("../../web/tagpup/style.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = (selector) => {
    const found = css.split("}").map((block) => block.split("{")).find(([head]) => head.trim() === selector);
    assert.ok(found, `${selector} has a rule`);
    return found[1];
  };
  assert.match(rule(".detail-item"), /min-width:\s*0\b/);
  const line = rule(".detail-value.one-line");
  assert.match(line, /overflow:\s*hidden/);
  assert.match(line, /text-overflow:\s*ellipsis/);
  assert.match(line, /white-space:\s*nowrap/);
});
