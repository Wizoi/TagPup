/**
 * The camera and the lens a photo was taken with, on one line in Image Details (web/tagpup/photo.js showCamera).
 *
 * The server names them (tagpup.core.photo_meta.gear, in the page's photo record as `camera` and `lens`); the page joins what
 * there is, hides the line for a photo whose metadata names neither, and puts the whole line in the tooltip, since the line
 * is cut with an ellipsis in a narrow panel (style.css `one-line`).
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
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
