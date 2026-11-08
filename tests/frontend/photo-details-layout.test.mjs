/**
 * Image Details, laid out for the person tagging: the camera and lens read only under the Date Taken in its own box, and the boxes that
 * add a person or a keyword shown by a pencil, closed until it is clicked (web/tagpup/edit-toggles.js).
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click, PHOTO_WRITES } from "./harness.mjs";

const PHOTO = { path: "D:\\Pictures\\Run\\a.jpg", filename: "a.jpg", tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, camera: "Tidewater TX R6m2", lens: "" };

function server() {
  return new FakeServer()
    .on("/api/folder/scan", [PHOTO])
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
