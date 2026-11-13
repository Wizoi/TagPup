/**
 * Image Details, laid out for the person tagging: the camera and lens read only under the Date Taken in its own box, and the boxes that
 * add a person or a keyword shown by a pencil, closed until it is clicked (web/tagpup/edit-toggles.js).
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click, PHOTO_WRITES } from "./harness.mjs";

const PHOTO = { path: "D:\\Pictures\\Run\\a.jpg", filename: "a.jpg", tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, camera: "Tidewater TX R6m2", lens: "" };

function server(records = [PHOTO]) {
  return new FakeServer()
    .on("/api/folder/scan", records)
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

afterEach(() => closeAllApps());

async function openPhoto(t) {
  const { window, document } = await loadApp("tagpup", { server: server(), t });
  await openFolder({ document, window }, "D:\\Pictures\\Run");
  await settle(window, 60);
  click(window, document.querySelector(".photo-item-file"));
  await settle(window);
  return { window, document };
}

test("the camera and lens are a line inside the Date Taken's box", async (t) => {
  const { document } = await openPhoto(t);
  const camera = document.getElementById("detail-camera-item");
  assert.ok(!camera.classList.contains("hidden"));
  assert.equal(camera.closest(".detail-item"), document.getElementById("detail-date-taken").closest(".detail-item"));
});

test("the add boxes of people and keywords are closed until their pencil is clicked", async (t) => {
  const { window, document } = await openPhoto(t);
  for (const [pencil, group, input] of [["btn-edit-people", "add-people-group", "input-add-person"],
                                        ["btn-edit-tags", "add-tags-group", "input-add-tag"]]) {
    const box = document.getElementById(group);
    assert.ok(box.classList.contains("hidden"), `${group} was open`);
    click(window, document.getElementById(pencil));
    assert.ok(!box.classList.contains("hidden"), `${pencil} opened nothing`);
    assert.equal(document.getElementById(pencil).getAttribute("aria-expanded"), "true");
    click(window, document.getElementById(pencil));
    assert.ok(box.classList.contains("hidden"), `${pencil} did not close it`);
    // With a name typed, the pencil keeps the box open: that text is not yet added.
    click(window, document.getElementById(pencil));
    document.getElementById(input).value = "Someone";
    click(window, document.getElementById(pencil));
    assert.ok(!box.classList.contains("hidden"), `${group} closed over a typed name`);
  }
});

async function openWith(t, record) {
  const srv = server([record]);
  const { window, document } = await loadApp("tagpup", { server: srv, t });
  await openFolder({ document, window }, "D:\\Pictures\\Run");
  await settle(window, 60);
  click(window, document.querySelector(".photo-item-file"));
  await settle(window);
  return document;
}

test("a video's file size reads under its Date Taken, a photo's under its path", async (t) => {
  const page = await openWith(t, { ...PHOTO, path: "D:\\Pictures\\Run\\a.mp4", filename: "a.mp4", media: "video", size: 3 * 1024 * 1024 });
  const underDate = page.getElementById("detail-video-size");
  assert.equal(underDate.textContent, "3.0 MB");
  assert.ok(!underDate.classList.contains("hidden"));
  assert.equal(underDate.closest(".detail-item"), page.getElementById("detail-date-taken").closest(".detail-item"));
  assert.equal(page.getElementById("detail-file-size").textContent, "", "the size was also shown under the path");
});

test("a photo's file size stays under its path, none under the date", async (t) => {
  const page = await openWith(t, { ...PHOTO, size: 820 * 1024 });
  assert.equal(page.getElementById("detail-file-size").textContent, "820 KB");
  assert.equal(page.getElementById("detail-video-size").textContent, "");
  assert.ok(page.getElementById("detail-video-size").classList.contains("hidden"));
});
