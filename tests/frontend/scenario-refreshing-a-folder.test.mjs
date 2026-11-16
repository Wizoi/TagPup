/**
 * V21: somebody presses Refresh on an open folder (plan docs/plans/folder-refresh-syncs-the-folder.md, cut 2). The folder
 * is synced first (POST /api/sync {folder, apply: true}: tagpup.jobs.recurring.refresh_folder, I12), then its list read
 * again once, and the status line says one sentence from the reply: what it found, and whether the full check that
 * removes a gone file was started. A slow answer says it is checking and three presses send one; the folder changed
 * meanwhile drops the answer; a reply that fails still reads the list again; unsaved edits are asked about first.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, closeAllApps, openFolder, click, PHOTO_WRITES } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\p";
const PHOTOS = ["a.jpg", "b.jpg"].map((filename) => photoRecord({ path: `${FOLDER}\\${filename}`, filename, writes: PHOTO_WRITES }));
const OTHER = [photoRecord({ path: "D:\\q\\c.jpg", filename: "c.jpg" })];

/** The reply of an applied folder sync, as tagpup/web/sync_routes.py gives it. */
function synced({ counts = {}, ...rest } = {}) {
  return {
    success: true, dry_run: false, refused: false, attempted: 0, changed: 0, changed_by_kind: {}, queued: 0, in_step: true,
    change: null, behind: 0, skipped: 0, warnings: [], errors: 0,
    counts: { new: 0, changed: 0, moved: 0, missing: 0, folders_not_looked_at: 0, ...counts },
    folder: "synced", whole: "not needed", whole_run_id: null, whole_why: null, ...rest,
  };
}

/** A page on D:\p whose sync answers `answer()` (a body, or a promise of one) with `status`. */
async function onFolder(t, answer, { status = 200 } = {}) {
  const server = new FakeServer()
    .on(/\/api\/sync$/, () => answer(), { status })
    .on("/api/folder/scan", (url) => (decodeURIComponent(url).includes("D:\\q") ? OTHER : PHOTOS.map((p) => ({ ...p }))))
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", []);
  const ctx = await loadApp("tagpup", { t, server });
  ctx.window.alert = () => {};
  await openFolder(ctx, FOLDER);
  return ctx;
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));
const refresh = (ctx) => click(ctx.window, ctx.document.getElementById("btn-refresh-list"));
const status = (ctx) => ctx.document.getElementById("status-text").textContent;
const syncs = (ctx) => ctx.server.calls.filter((c) => c.method === "POST" && /\/api\/sync$/.test(c.url));
const forcedScans = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/folder/scan") && c.url.includes("force=true"));

/** Press Refresh with the sync answering `body` (or what a function of nothing gives); the sentence the status line says
 * once the list is read again. */
async function said(t, body, options) {
  const ctx = await onFolder(t, typeof body === "function" ? body : () => body, options);
  refresh(ctx);
  await settle(ctx.window);
  assert.equal(syncs(ctx).length, 1);
  assert.equal(forcedScans(ctx).length, 1, "the list is read again once, whatever the sync answered");
  return status(ctx);
}

describe("V21: Refresh on a folder syncs it, then reads its list again", () => {
  test("the folder is synced first, applied, then its list is read once", async (t) => {
    const ctx = await onFolder(t, () => synced());
    refresh(ctx);
    await settle(ctx.window);
    const sync = ctx.server.calls.indexOf(syncs(ctx)[0]);
    const scan = ctx.server.calls.indexOf(forcedScans(ctx)[0]);
    assert.ok(sync >= 0 && scan > sync, "the sync is asked before the scan");
    assert.deepEqual(syncs(ctx)[0].body, { folder: FOLDER, apply: true });
    assert.ok(syncs(ctx)[0].url.startsWith("/photo_index/api/sync"), "through api.js, under the page's library");
  });

  test("three presses while the answer is slow send one sync, and the line says it is checking", async (t) => {
    let release;
    const ctx = await onFolder(t, () => new Promise((r) => { release = () => r(synced()); }));
    refresh(ctx);
    refresh(ctx);
    refresh(ctx);
    await settle(ctx.window);
    assert.equal(syncs(ctx).length, 1);
    assert.equal(status(ctx), "Checking this folder...");
    assert.equal(ctx.document.getElementById("btn-refresh-list").disabled, true);
    assert.equal(forcedScans(ctx).length, 0, "no scan before the sync answers");
    release();
    await settle(ctx.window);
    assert.equal(forcedScans(ctx).length, 1);
    assert.equal(ctx.document.getElementById("btn-refresh-list").disabled, false);
    assert.equal(status(ctx), "This folder is in step.");
  });

  test("a file found gone starts the full check, and the sentence says it removes the file only if it is gone everywhere", async (t) => {
    const sentence = await said(t, synced({ counts: { new: 2, changed: 1, missing: 1 }, whole: "started", whole_run_id: 41 }));
    assert.equal(sentence, "Checked this folder: 2 new, 1 changed. 1 file is gone; a full check was started and removes it if it is gone from the whole library.");
  });

  test("files gone while a full check already runs: press Refresh again when it ends", async (t) => {
    const sentence = await said(t, synced({ counts: { missing: 3 }, whole: "running", whole_why: "a run of it is under way already" }));
    assert.equal(sentence, "Checked this folder. 3 files are gone; another check of this library is under way; press Refresh again when it ends.");
  });

  test("a server that runs no recurring jobs says why no full check was started", async (t) => {
    const why = "This server runs no recurring jobs (one a test started, or TAGPUP_NO_JOBS is set)";
    const sentence = await said(t, synced({ counts: { moved: 1, missing: 1 }, whole: "no runner", whole_why: why }));
    assert.equal(sentence, "Checked this folder: 1 moved. 1 file is gone; no full check was started: this server runs no recurring jobs (one a test started, or TAGPUP_NO_JOBS is set).");
  });

  test("the library's walk held by another check: nothing synced, and it says to press again", async (t) => {
    const sentence = await said(t, { success: false, dry_run: false, folder: "running", whole: null, whole_run_id: null, whole_why: null });
    assert.equal(sentence, "Another check of this library is under way; press Refresh again when it ends.");
  });

  test("nothing found: the folder is in step", async (t) => {
    assert.equal(await said(t, synced()), "This folder is in step.");
  });

  test("a share that did not answer is not 'in step'", async (t) => {
    const sentence = await said(t, synced({ counts: { folders_not_looked_at: 1 }, in_step: false }));
    assert.match(sentence, /^This folder could not be looked at/);
  });

  test("a refused folder sync says the refusal", async (t) => {
    const error = "That folder is not there.";
    const body = { ...synced(), success: false, refused: true, folder: "not synced", whole: null, error };
    assert.equal(await said(t, body, { status: 400 }), error);
  });

  test("while names are written from tags, the 409's own sentence", async (t) => {
    const error = "Names are being given from tags; this waits until it finishes.";
    assert.equal(await said(t, { error }, { status: 409 }), error);
  });

  test("a sync that fails (500) still reads the list, and says it could not check", async (t) => {
    const sentence = await said(t, { error: "database is locked" }, { status: 500 });
    assert.equal(sentence, "Could not check this folder against the library (database is locked); its list was read again from the disk.");
  });

  test("a sync that never answers still reads the list, and says it could not check", async (t) => {
    const sentence = await said(t, () => Promise.reject(new Error("Failed to fetch")));
    assert.match(sentence, /^Could not check this folder against the library \(Failed to fetch\)/);
  });
});

describe("V21: what Refresh does not do", () => {
  test("another folder opened while the sync is out: its answer is dropped, nothing of the first is read or said", async (t) => {
    let release;
    const ctx = await onFolder(t, () => new Promise((r) => { release = () => r(synced({ counts: { missing: 1 }, whole: "started" })); }));
    refresh(ctx);
    await settle(ctx.window);
    await openFolder(ctx, "D:\\q");
    const before = forcedScans(ctx).length;
    release();
    await settle(ctx.window);
    assert.equal(forcedScans(ctx).length, before, "the first folder is not read again");
    assert.equal(ctx.document.getElementById("folder-path-input").value, "D:\\q");
    assert.doesNotMatch(status(ctx), /gone/);
    assert.equal(ctx.document.getElementById("btn-refresh-list").disabled, false);
  });

  test("unsaved edits: the question comes first, and no sync is sent until it is answered", async (t) => {
    const ctx = await onFolder(t, () => synced());
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await settle(ctx.window);
    const title = ctx.document.getElementById("input-photo-title");
    title.focus();
    title.value = "Harbour";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    refresh(ctx);
    await settle(ctx.window);
    const modal = ctx.document.querySelector(".unsaved-edits-modal");
    assert.ok(modal, "Save, Discard or Cancel");
    assert.equal(syncs(ctx).length, 0);
    click(ctx.window, modal.querySelector('[data-choice="discard"]'));
    await settle(ctx.window);
    assert.equal(syncs(ctx).length, 1);
    assert.equal(forcedScans(ctx).length, 1);
    assert.equal(ctx.document.querySelector(".unsaved-edits-modal"), null, "asked once, not again by the scan");
  });
});
