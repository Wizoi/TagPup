/**
 * Smart Rename of the open photo, from the box beside its name (web/tagpup/rename-one.js; DECISIONS 2026-11-05;
 * scenario F1). The owner opens a photo and finds the grouping its name holds in the box; types another and presses
 * Smart Rename: the panel, the list and the grid show the new name, and the photo's suggestions go with it. A second
 * click goes behind the first under the new name. Moving on quickly to another photo never shows the first one's grouping
 * in the second's box. Text typed on the photo and not saved stops the rename. The server's side of these replies is
 * tests/test_smart_rename_one_photo.py.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, openFolder, closeAllApps, PHOTO_WRITES, SAVE, lastSave, saved } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2020";
const A = `${FOLDER}\\Regatta - 07 - Start.jpg`;
const B = `${FOLDER}\\Lighthouse - 2.jpg`;
const RENAMED = `${FOLDER}\\Harbour Day - 07 - Start.jpg`;
const record = (path) => ({ path, filename: path.split("\\").pop(), tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, mtime: 1, size: 1 });
const OFFER = { tags: [{ tag: "Harbour", score: 0.9 }], people: [], title: "" };
const GROUPING = { [A]: "Regatta", [B]: "Lighthouse" };
const RENAME = /\/api\/photo\/smart-rename$/;

async function load(t, { parts = (url) => ({ grouping: GROUPING[decodeURIComponent(url.split("path=")[1])], number: "07" }),
  rename = () => ({ success: true, renamed: true, new_path: RENAMED, updated_paths: { [A]: RENAMED }, mtime: 2, size: 3 }) } = {}) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "completed", suggestions: { [A]: OFFER } })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/smart-rename-parts", parts)
    .on(RENAME, rename)
    .on(SAVE, saved())
    .on("/api/folder/scan", () => [record(A), record(B)]);
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.alert = () => {};
  await openFolder(ctx, FOLDER);
  await flush(ctx.window, 8);
  return ctx;
}

const row = (ctx, path) => ctx.document.querySelector(`li[data-path="${path.replace(/\\/g, "\\\\")}"]`);
const box = (ctx) => ctx.document.getElementById("input-smart-rename-one");
const renames = (ctx) => ctx.server.calls.filter((c) => RENAME.test(c.url));

async function open(ctx, path) {
  click(ctx.window, row(ctx, path));
  await flush(ctx.window, 10);
}

async function renameTo(ctx, grouping) {
  box(ctx).value = grouping;
  click(ctx.window, ctx.document.getElementById("btn-smart-rename-one"));
  await flush(ctx.window, 12);
}

describe("Smart Rename of the open photo", () => {
  test("the box holds the grouping of the photo's name", async (t) => {
    const ctx = await load(t);
    await open(ctx, A);
    assert.equal(box(ctx).value, "Regatta");
    await open(ctx, B);
    assert.equal(box(ctx).value, "Lighthouse");
  });

  test("the panel, the list and the grid show the new name, and the record takes the file's stamp", async (t) => {
    const ctx = await load(t);
    await open(ctx, A);
    await renameTo(ctx, "Harbour Day");
    assert.deepEqual(renames(ctx).map((c) => c.body), [{ path: A, grouping: "Harbour Day" }]);
    assert.equal(ctx.document.getElementById("detail-file-name").title, `Open in the default app: ${RENAMED}`);
    assert.ok(row(ctx, RENAMED), "the list has the new name");
    assert.ok(!row(ctx, A), "the list still has the old name");
    assert.ok(ctx.document.querySelector(`.thumbnail-card[data-path="${RENAMED.replace(/\\/g, "\\\\")}"]`), "the grid has it");
    assert.match(ctx.document.getElementById("status-text").textContent, /Renamed to Harbour Day - 07 - Start\.jpg/);
    // The next save names the file as the rename left it, or the server would refuse it as changed on disk.
    const title = ctx.document.getElementById("input-photo-title");
    title.value = "Start line";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    ctx.document.getElementById("btn-save-details").click();
    await flush(ctx.window, 10);
    const saved = lastSave(ctx.server);
    assert.deepEqual([saved.path, saved.stamp], [RENAMED, { mtime: 2, size: 3 }]);
  });

  test("the photo's suggestions go with it", async (t) => {
    const ctx = await load(t);
    await open(ctx, A);
    assert.ok(!ctx.document.getElementById("suggestions-section").classList.contains("hidden"), "shown before");
    await renameTo(ctx, "Harbour Day");
    assert.ok(!ctx.document.getElementById("suggestions-section").classList.contains("hidden"), "lost with the old name");
    assert.ok(row(ctx, RENAMED).querySelector(".photo-item-has-sugg"), "its row lost the AI badge");
  });

  test("a second click waits for the first and names the photo by its new name", async (t) => {
    let asked = 0;
    const ctx = await load(t, {
      rename: () => (++asked === 1
        ? { success: true, renamed: true, new_path: RENAMED, updated_paths: { [A]: RENAMED } }
        : { success: true, renamed: false, new_path: RENAMED, updated_paths: { [RENAMED]: RENAMED } }),
    });
    await open(ctx, A);
    box(ctx).value = "Harbour Day";
    const button = ctx.document.getElementById("btn-smart-rename-one");
    click(ctx.window, button);
    click(ctx.window, button);
    await flush(ctx.window, 20);
    assert.deepEqual(renames(ctx).map((c) => c.body.path), [A, RENAMED]);
    assert.equal(ctx.document.getElementById("detail-file-name").title, `Open in the default app: ${RENAMED}`);
    assert.ok(!ctx.document.querySelector('.write-queue-entry[data-status="failed"]'), "a click failed");
    assert.match(ctx.document.getElementById("status-text").textContent, /already has that name/);
  });

  test("a grouping for a photo left since is not put in the next one's box", async (t) => {
    let answerA;
    const ctx = await load(t, {
      parts: (url) => (url.includes(encodeURIComponent(A))
        ? new Promise((resolve) => { answerA = () => resolve({ grouping: "Regatta", number: "07" }); })
        : { grouping: "Lighthouse", number: "2" }),
    });
    await open(ctx, A);
    await open(ctx, B);
    assert.equal(box(ctx).value, "Lighthouse");
    answerA();
    await flush(ctx.window, 10);
    assert.equal(box(ctx).value, "Lighthouse", "the late reply for the photo left was put in the box");
  });

  test("text typed on the photo and not saved stops the rename, and nothing is sent", async (t) => {
    const ctx = await load(t);
    await open(ctx, A);
    const title = ctx.document.getElementById("input-photo-title");
    title.value = "A caption not saved";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    await renameTo(ctx, "Harbour Day");
    assert.equal(renames(ctx).length, 0);
    assert.match(ctx.document.getElementById("status-text").textContent, /Save or discard/);
  });
});
