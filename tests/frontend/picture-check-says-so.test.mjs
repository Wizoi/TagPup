/**
 * A save or a turn whose write was thrown away and made again, because the new file would have changed the picture's
 * bytes (DECISIONS 2026-11-03, #2377), tells the owner in a plain sentence: nothing was lost. The replies are the real
 * routes' (tests/test_tag_a_photo_read_it_back_and_undo.py, ASaveFromThePage, writes fixtures/save_replies.json).
 */
import fs from "node:fs";
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click, PHOTO_WRITES, SAVE, savedAs } from "./harness.mjs";

const served = JSON.parse(fs.readFileSync(new URL("./fixtures/save_replies.json", import.meta.url), "utf8"));
const A = "D:\\p\\a.jpg";
const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));
const SAID = "The first try would have changed the photo's picture, so it was thrown away and the photo written again; nothing was lost.";

async function openA(t, replies) {
  const server = new FakeServer()
    .on("/api/folder/scan", () => [{ path: A, filename: "a.jpg", tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, mtime: 1 }])
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on(SAVE, savedAs(replies.save, { new_path: A }))
    .on("/api/photo/rotate", () => replies.rotate);
  const { window, document } = await loadApp("tagpup", { server, t });
  window.alert = () => {};
  await openFolder({ document, window }, "D:\\p");
  await settle(window, 60);
  click(window, document.querySelector(`li[data-path="${A.replace(/\\/g, "\\\\")}"]`));
  await settle(window, 60);
  return { window, document, server };
}

async function saveCaption({ window, document }, text) {
  const input = document.getElementById("input-photo-title");
  input.focus();
  input.value = text;
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  document.getElementById("btn-save-details").click();
  await settle(window, 60);
}

const status = (document) => document.getElementById("status-text").textContent;
afterEach(() => closeAllApps());

describe("A write the picture check made again says so", () => {
  test("a save says the photo is saved, what was thrown away, and that nothing was lost", async (t) => {
    const app = await openA(t, { save: served.picture_changed, rotate: served.rotated_picture_changed });
    await saveCaption(app, "A caption");
    assert.equal(status(app.document), `Saved a.jpg. ${SAID}`);
  });

  test("a save the check never touched says only Saved", async (t) => {
    const app = await openA(t, { save: served.no_rename, rotate: served.rotated_picture_changed });
    await saveCaption(app, "A caption");
    assert.equal(status(app.document), "Saved a.jpg");
  });

  test("a turn says it too", async (t) => {
    const app = await openA(t, { save: served.no_rename, rotate: served.rotated_picture_changed });
    app.document.getElementById("btn-rotate-left").click();
    await settle(app.window, 60);
    assert.equal(status(app.document), `Rotated. ${SAID}`);
  });

  test("a turn the check never touched still says Ready", async (t) => {
    const app = await openA(t, { save: served.no_rename, rotate: { ...served.rotated_picture_changed, pictures_refused: undefined } });
    app.document.getElementById("btn-rotate-left").click();
    await settle(app.window, 60);
    assert.equal(status(app.document), "Ready");
  });
});
