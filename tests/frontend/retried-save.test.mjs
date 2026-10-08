/**
 * A save that failed and is retried (findings #389).
 *
 * Retry queued the save again through queuePhotoWrite, not queueWriteOf: the retried
 * save was not the write leaving the photo waits for, and a retry whose photo had been
 * left found the fields no longer its own, wrote nothing and was marked done.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const A = "D:\\p\\a.jpg";
const B = "D:\\p\\b.jpg";

const PHOTOS = () => [
  { path: A, filename: "a.jpg", tags: [], people: [], captions: [], title: "" },
  { path: B, filename: "b.jpg", tags: [], people: [], captions: [], title: "" },
];

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

/** A server whose first save fails and whose later saves wait for release(). */
function failingOnce() {
  const held = [];
  let calls = 0;
  const server = new FakeServer()
    .on("/api/folder/scan", () => PHOTOS())
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", () => {
      calls += 1;
      if (calls === 1) return { success: false, error: "The file is locked." };
      return new Promise((resolve) => held.push(() => resolve({ success: true })));
    });
  return { server, held };
}

async function failedSaveOnA(t) {
  const { server, held } = failingOnce();
  const { window, document } = await loadApp("tagpup", { server, t });
  window.alert = () => {};
  await openFolder({ document, window }, "D:\\p");
  await settle(window, 60);
  click(window, document.querySelector(`li[data-path="${A.replace(/\\/g, "\\\\")}"]`));
  await settle(window, 60);
  const input = document.getElementById("input-add-title") || document.getElementById("input-photo-title");
  input.focus();
  input.value = "Sports day";
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  document.getElementById("btn-save-details").click();
  await settle(window, 60);
  const failed = document.querySelector('.write-queue-entry[data-status="failed"]');
  assert.ok(failed, "the first save did not fail");
  return { window, document, server, held, input };
}

const saves = (s) => s.calls.filter((c) => c.url.includes("save-metadata"));
const entries = (document) => [...document.querySelectorAll(".write-queue-entry")]
  .map((e) => `${e.dataset.status}: ${e.textContent}`);
const activePath = (document) => {
  const el = document.querySelector(".photo-item-file.active");
  return el && el.getAttribute("data-path");
};

afterEach(() => closeAllApps());

describe("Retry of a failed save", () => {
  test("is the write that leaving the photo waits for", async (t) => {
    const { window, document, held } = await failedSaveOnA(t);
    click(window, document.querySelector(".write-queue-retry"));
    await settle(window);
    assert.equal(held.length, 1, "the retry did not send the save");

    document.activeElement && document.activeElement.blur();
    document.body.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await settle(window);
    assert.ok(!document.querySelector(".unsaved-edits-modal.active"),
      "asked to save text that the retried save is writing");
    assert.equal(activePath(document), A, "left the photo before its retried save finished");

    held.shift()();
    await settle(window, 80);
    assert.equal(activePath(document), B, "did not move on once the retried save finished");
  });

  test("of a photo left meanwhile does not say done having written nothing", async (t) => {
    const { window, document, server } = await failedSaveOnA(t);
    // Leave the photo, discarding the text the failed save held.
    document.activeElement && document.activeElement.blur();
    document.body.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await settle(window);
    click(window, document.querySelector('.unsaved-edits-modal [data-choice="discard"]'));
    await settle(window, 60);
    assert.equal(activePath(document), B);

    const before = saves(server).length;
    click(window, document.querySelector(".write-queue-retry"));
    await settle(window, 60);
    assert.equal(saves(server).length, before, "a retry wrote a photo that is not open");
    const rows = entries(document);
    assert.ok(rows.every((r) => !r.startsWith("done")), `marked done having written nothing: ${rows.join(" | ")}`);
    assert.ok(rows.some((r) => r.startsWith("failed")), `no failed entry: ${rows.join(" | ")}`);
  });
});
