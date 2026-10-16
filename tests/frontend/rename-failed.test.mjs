/**
 * A caption saved whose rename then failed (findings #884). The replies are the real route's
 * (tests/test_tag_a_photo_read_it_back_and_undo.py, ASaveFromThePage, writes fixtures/save_replies.json): the page says the caption saved and the
 * photo kept its name, says it once however often the save is repeated, and says plain "Saved" when no rename was wanted.
 */
import fs from "node:fs";
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const served = JSON.parse(fs.readFileSync(new URL("./fixtures/save_replies.json", import.meta.url), "utf8"));
const A = "D:\\p\\a.jpg";
const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

async function openA(t, reply) {
  const server = new FakeServer()
    .on("/api/folder/scan", () => [{ path: A, filename: "a.jpg", tags: [], people: [], captions: [], title: "" }])
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", () => ({ ...reply, new_path: A }));
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

describe("A save whose rename failed", () => {
  test("says the caption saved and why the photo was not renamed, in one line", async (t) => {
    const app = await openA(t, served.rename_failed);
    await saveCaption(app, "Start line");
    assert.equal(status(app.document),
      `Saved the caption, but could not rename the photo: ${served.rename_failed.rename_failed}`);
    assert.ok(!app.document.querySelector('.write-queue-entry[data-status="failed"]'), "the caption saved: no Retry");
  });

  test("the same save three times does not pile the warning up", async (t) => {
    const app = await openA(t, served.rename_failed);
    await saveCaption(app, "Start line");
    await saveCaption(app, "Start line 2");
    await saveCaption(app, "Start line 3");
    const text = status(app.document);
    assert.equal(text.split("could not rename").length, 2, text);
    assert.equal(app.server.calls.filter((c) => c.url.includes("save-metadata")).length, 3);
  });

  test("a save with no rename wanted says Saved, as it always did", async (t) => {
    const app = await openA(t, served.no_rename);
    await saveCaption(app, "A caption");
    assert.equal(status(app.document), "Saved a.jpg");
  });
});
