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

test("a photo's and a clip's size read on the Size line under Date taken, the camera under both only when the file names one", async (t) => {
  for (const [record, size, camera] of [
    [{ ...PHOTO, size: 820 * 1024 }, "820 KB", "Tidewater TX R6m2"],
    [{ ...PHOTO, path: "D:\\Pictures\\Run\\a.mp4", filename: "a.mp4", media: "video", size: 3 * 1024 * 1024, camera: "" }, "3.0 MB", null],
  ]) {
    const page = await openWith(t, record);
    const lines = [...page.querySelectorAll(".detail-fact")].filter((line) => !line.classList.contains("hidden"));
    assert.deepEqual(lines.map((line) => line.querySelector(".detail-fact-label").textContent),
      camera ? ["Date taken:", "Size:", "Camera:"] : ["Date taken:", "Size:"], record.filename);
    assert.equal(page.getElementById("detail-size").textContent, size);
    assert.equal(new Set(lines.map((line) => line.closest(".detail-item"))).size, 1, "the lines are one column's");
    if (camera) assert.equal(page.getElementById("detail-camera").textContent, camera);
    assert.equal(page.getElementById("detail-path").closest(".detail-item").textContent.includes(size), false, "the size still floats under the path");
    closeAllApps();
  }
});

test("the Date Taken reads whole, as the photo stores it: no zone moves it, and a photo with none says so", async (t) => {
  // Midnight, the date the owner checks; 2:30 AM on the day the clocks go forward, which a Date would read as 3:30; a zone after the
  // seconds, as 106 of the owner's photos carry; and none, as 74 have.
  for (const [taken, shown] of [["2021:03:27 00:00:00", "3/27/21 12:00 AM"], ["2021:03:14 02:30:00", "3/14/21 2:30 AM"],
                                ["2019:11:02 17:45:09-07:00", "11/2/19 5:45 PM"], [null, "Unknown"]]) {
    const page = await openWith(t, { ...PHOTO, taken });
    assert.equal(page.getElementById("detail-date-taken").textContent, shown, `${taken}`);
    closeAllApps();
  }
});
