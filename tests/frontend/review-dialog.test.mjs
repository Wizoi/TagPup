/**
 * Folders to review, in TagTuner (web/tuner/review.js; docs/ARCHITECTURE.md, phase 8).
 *
 * Sync walks the library's roots and never indexes a folder that holds no indexed photo
 * on its own: it lists it to review. A notice in the header says how many there are, as
 * the last sync counted them (/api/sync); it and the gear's Folders to review... open a
 * dialog listing them (/api/sync/review), each with Include -- queued for indexing with
 * its subfolders -- and Ignore, which adds it to the library's ignored folders. Either
 * takes the folder off the list and the count down.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";
import { OPEN_DIALOG } from "../../web/common/dialog.js";

afterEach(() => closeAllApps());

const LIBRARY = "kr-track";
const LIGHTS = "D:/Pictures/2025-12 Harbour Lights";
const SCANS = "D:/Pictures/Scans";

async function tuner(t, { reviewFolders = 2 } = {}) {
  const server = new FakeServer()
    .on("/api/apps", { this: "tuner", apps: {} })
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", [])
    // The longest first: the first route that matches answers.
    .on("/api/sync/review/include", { success: true, queued: 1 })
    .on("/api/sync/review/ignore", { success: true, changed: 1, change: 31 })
    .on("/api/sync/review", { library: LIBRARY, count: 2, photos: 5,
                              folders: [{ path: LIGHTS, photos: 3 }, { path: SCANS, photos: 2 }] })
    .on("/api/sync", { library: LIBRARY, last_in_step: null,
                       last_run: reviewFolders === null ? null : { found: { review_folders: reviewFolders } } });
  const ctx = await loadApp("tagtuner", { t, url: `http://localhost:8080/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  ctx.server = server;
  ctx.notice = ctx.document.getElementById("review-notice");
  ctx.posted = (path) => server.calls.filter((c) => c.url.includes(path) && c.method === "POST").map((c) => c.body);
  return ctx;
}

async function openFromGear(ctx) {
  const { window, document } = ctx;
  click(window, document.getElementById("btn-gear"));
  await flush(window);
  click(window, document.querySelector('[data-action="folders-review"]'));
  await flush(window, 6);
  return document.getElementById("review-modal");
}

describe("TagTuner's folders to review", () => {
  test("a notice says how many the last sync found, and none says nothing", async (t) => {
    const { notice } = await tuner(t);
    assert.ok(!notice.classList.contains("hidden"));
    assert.equal(notice.textContent, "2 folders to review");
    closeAllApps();
    const quiet = await tuner(t, { reviewFolders: null });
    assert.ok(quiet.notice.classList.contains("hidden"));
  });

  test("the gear opens a dialog listing each folder with its photos", async (t) => {
    const ctx = await tuner(t);
    const dialog = await openFromGear(ctx);
    assert.ok(dialog && !dialog.classList.contains("hidden"));
    assert.ok(dialog.matches(OPEN_DIALOG), "the page's shortcuts would act behind it");
    const rows = [...dialog.querySelectorAll(".review-folder")];
    assert.deepEqual(rows.map((r) => r.querySelector(".review-path").textContent), [LIGHTS, SCANS]);
    assert.deepEqual(rows.map((r) => r.querySelector(".review-count").textContent), ["3 photo(s)", "2 photo(s)"]);
  });

  test("Include queues a folder and Ignore ignores one; each leaves the list", async (t) => {
    const ctx = await tuner(t);
    const { window, notice } = ctx;
    const dialog = await openFromGear(ctx);
    click(window, dialog.querySelector(`.review-folder[data-path="${LIGHTS}"] .review-include`));
    await flush(window, 6);
    assert.deepEqual(ctx.posted("/api/sync/review/include"), [{ folder: LIGHTS }]);
    assert.equal(dialog.querySelector(`.review-folder[data-path="${LIGHTS}"]`), null);
    assert.equal(notice.textContent, "1 folder to review");
    click(window, dialog.querySelector(`.review-folder[data-path="${SCANS}"] .review-ignore`));
    await flush(window, 6);
    assert.deepEqual(ctx.posted("/api/sync/review/ignore"), [{ folder: SCANS }]);
    assert.ok(notice.classList.contains("hidden"));
    assert.ok(dialog.querySelector(".review-empty"), "an empty list says so");
  });

  test("the notice opens the same dialog", async (t) => {
    const { window, notice, document } = await tuner(t);
    click(window, notice);
    await flush(window, 6);
    assert.ok(!document.getElementById("review-modal").classList.contains("hidden"));
  });
});
