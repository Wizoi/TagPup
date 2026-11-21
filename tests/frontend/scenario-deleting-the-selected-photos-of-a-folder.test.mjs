/**
 * I13: somebody opens a folder of five in Organize (four photos and a clip), selects three, and clicks the selection's Delete
 * (plan docs/plans/organize-deletes-the-selected-photos.md). It is the library view's delete: the question names the server's
 * count, OK sends the selection by path with the question's token (web/tagpup/bulk-edit.js deleteSelection), and the folder is
 * read again afterwards (folder.js folderPhotosDeleted): the deleted leave the list, the kept scan and the selection, and the
 * photo open among them closes onto the next one still listed. A photo the delete left (a share that did not answer, a file
 * not indexed yet) stays listed and selected; a write that stopped part-way (a 500) is followed the same; a clip played among
 * them stops first and plays again only if it stayed. Another folder or a view opened while it runs is left alone (#532). Rapid
 * clicks ask once; one delete at a time on a page, the panel's one-photo Delete included (bulk-run.js bulkBusy), and a delete
 * the panel's began while this one was being asked says why nothing was deleted; the sidebar's Refresh is off while it runs;
 * the status line says how it ended once the folder is read again. In a folder looked at without adding it (Just look) the
 * button is shown inert with its sentence and a click sends nothing.
 *
 * The replies of the delete come from tests/frontend/fixtures/batches.json, written from the real routes
 * (tests/test_batch_strip_replies.py). The folder is invented; the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { closeAllApps, click, openFolder, photoRecord, SAVE, saveCalls } from "./harness.mjs";
import { loadViewPage, pageErrors, speedUp } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const served = JSON.parse(fs.readFileSync(new URL("./fixtures/batches.json", import.meta.url), "utf8"));

const FOLDER = "D:\\Library\\2021\\Harbour Walk";
const OTHER = "D:\\Library\\2021\\Hill Fair";
const at = (name) => `${FOLDER}\\${name}`;
const NAMES = ["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg", "IMG_0004.jpg", "CLIP_0005.mp4"];
/** The folder as /api/folder/scan lists it: four photos and a clip the library holds (a clip plays by its id). */
const folderOfFive = () => NAMES.map((filename, n) => (filename.endsWith(".mp4")
  ? { ...photoRecord({ filename, path: at(filename) }), media: "video", id: 5 + n }
  : photoRecord({ filename, path: at(filename) })));
const OTHER_PHOTOS = [photoRecord({ filename: "IMG_0101.jpg", path: `${OTHER}\\IMG_0101.jpg` })];

const plain = (value) => JSON.parse(JSON.stringify(value));
const el = (ctx, id) => ctx.document.getElementById(id);
const calls = (ctx, part) => ctx.server.calls.filter((call) => call.url.includes(part));
const scansOf = (ctx, folder) => calls(ctx, "/api/folder/scan").filter((call) => call.url.includes(encodeURIComponent(folder)));
const listed = (ctx) => [...ctx.document.querySelectorAll(".photo-item-file")].map((item) => item.getAttribute("data-path").split("\\").pop());
const names = (paths) => [...paths].map((path) => path.split("\\").pop());
const button = (ctx) => el(ctx, "btn-delete-selection");
const BIN_SENTENCE = "A bulk edit is being written (see the strip at the top). Wait for it to finish.";
const ONE_DELETE_OUT = "A photo is being deleted; wait for it to finish.";
const statusLine = (ctx) => el(ctx, "status-text").textContent;
const stripSaid = (ctx) => ctx.document.querySelector(".batch-strip-sentence").textContent;
const refresh = (ctx) => el(ctx, "btn-refresh-list");

/**
 * The page in Organize on FOLDER. The server's files are `ctx.files` (what a scan lists); a delete takes `ctx.deletes` off them
 * when it answers, so the scan after it lists what the server left, as the real one does.
 */
async function organize(t, { membership } = {}) {
  const ctx = await loadViewPage(t, { search: "", ...(membership ? { membership } : {}) });
  speedUp(ctx);
  ctx.window.HTMLMediaElement.prototype.pause = () => {};   // jsdom plays nothing
  ctx.window.HTMLMediaElement.prototype.load = () => {};
  ctx.files = folderOfFive();
  ctx.server.first("/api/folder/scan", (url) => (url.includes(encodeURIComponent(OTHER)) ? OTHER_PHOTOS : ctx.files));
  ctx.server.on("/api/library/selection/delete-check", () => ctx.where);
  ctx.where = served.delete_check;
  ctx.questions = [];
  ctx.alerts = [];
  ctx.window.confirm = (text) => { ctx.questions.push(text); return true; };
  ctx.window.alert = (text) => ctx.alerts.push(text);
  await openFolder(ctx, FOLDER);
  await ctx.settle(60);
  return ctx;
}

/**
 * The server's answer to the delete: `gone` (names) leave the folder, `reply` is what it says (a body, or { status, body }).
 * With `hold`, it answers only when `ctx.finish()` is called.
 */
function serverDeletes(ctx, gone, reply, { hold = false } = {}) {
  const { status = 200, body = reply } = reply.status ? reply : {};
  ctx.server.first("/api/library/bulk-edit", () => {
    const answer = () => {
      ctx.files = ctx.files.filter((record) => !gone.includes(record.filename));
      return body;
    };
    if (!hold) return answer();
    return new Promise((resolve) => { ctx.finish = () => resolve(answer()); });
  }, { status });
}

/** Tick the cards of these names, as the owner does. */
function pick(ctx, ...picked) {
  for (const name of picked) {
    const card = [...ctx.document.querySelectorAll("#thumbnails-grid [data-path]")].find((each) => each.getAttribute("data-path") === at(name));
    click(ctx.window, card.querySelector(".thumbnail-checkbox"));
  }
}

/** Open a photo in the panel by its row in the folder's list. */
async function openPhoto(ctx, name) {
  const item = [...ctx.document.querySelectorAll(".photo-item-file")].find((row) => row.getAttribute("data-path") === at(name));
  click(ctx.window, item);
  await ctx.settle(60);
}

const videoSrc = (ctx) => el(ctx, "main-video").getAttribute("src") || "";

describe("I13: deleting the selected photos of a folder", () => {
  test("three of five, the open one among them: asked once by the server's count, sent by path; the list, kept scan and selection drop the three, the next photo opens", async (t) => {
    const ctx = await organize(t);
    await openPhoto(ctx, "IMG_0002.jpg");
    pick(ctx, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg");
    serverDeletes(ctx, ["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg"], served.deleted);
    const before = scansOf(ctx, FOLDER).length;
    click(ctx.window, button(ctx));
    await ctx.until(() => listed(ctx).length === 2);
    await ctx.settle(60);
    const picked = ["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg"].map(at);
    assert.deepEqual(plain(calls(ctx, "/selection/delete-check")[0].body), { selection: { paths: picked } });
    assert.equal(ctx.questions.length, 1);
    assert.match(ctx.questions[0], /^Delete 3 photos\? The files go to the Recycle Bin/);
    assert.deepEqual(plain(calls(ctx, "/api/library/bulk-edit")[0].body),
      { op: "delete", selection: { paths: picked }, params: { token: served.delete_check.token } });
    assert.equal(calls(ctx, "/api/photo/delete").length, 0, "never the one-photo route");
    assert.equal(scansOf(ctx, FOLDER).length, before + 1, "the folder is read again once");
    assert.deepEqual(listed(ctx), ["IMG_0004.jpg", "CLIP_0005.mp4"]);
    assert.deepEqual(names(ctx.state.selectedThumbnails), [], "the deleted are no longer selected");
    assert.equal(ctx.state.activePhotoPath, at("IMG_0004.jpg"), "the open photo went: the next one still listed opens");
    const kept = Object.keys(ctx.window.localStorage).filter((key) => key.startsWith("tagpup_cache_"))
      .map((key) => ctx.window.localStorage.getItem(key)).join("");
    assert.ok(!kept.includes("IMG_0001.jpg") && kept.includes("IMG_0004.jpg"), "the kept scan is the new one");
    assert.ok(button(ctx).classList.contains("hidden"), "nothing selected: the button is not shown");
    assert.match(stripSaid(ctx), /^Deleted 3 photos/);
    assert.equal(statusLine(ctx), stripSaid(ctx), "the status line says how it ended, not Ready");
  });

  test("a card's title saved and not written yet: the Delete of its photo is refused naming the save, nothing sent; written, it goes", async (t) => {
    const ctx = await organize(t);
    let answer;
    ctx.server.first(SAVE, () => new Promise((resolve) => { answer = resolve; }));
    const card = [...ctx.document.querySelectorAll("#thumbnails-grid [data-path]")].find((each) => each.getAttribute("data-path") === at("IMG_0003.jpg"));
    click(ctx.window, card.querySelector(".thumbnail-filename"));
    const input = card.querySelector(".thumbnail-filename-input");
    input.value = "Harbour lights";
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await ctx.until(() => saveCalls(ctx.server).length === 1);
    assert.notEqual(ctx.state.activePhotoPath, at("IMG_0003.jpg"), "the photo saved is not the open one");
    pick(ctx, "IMG_0003.jpg", "IMG_0004.jpg");
    click(ctx.window, button(ctx));
    await ctx.settle(20);
    assert.deepEqual(ctx.alerts, ["A save of 1 of these photos is not written yet: wait for it (the list of writes, bottom right), then delete."]);
    assert.equal(calls(ctx, "/selection/delete-check").length, 0, "nothing asked of the server");
    assert.equal(ctx.questions.length, 0);
    answer({ success: true, mtime: 1700000000, size: 2048 });
    await ctx.until(() => !Object.keys(ctx.state.photoWrites).length);
    serverDeletes(ctx, ["IMG_0003.jpg", "IMG_0004.jpg"], served.deleted);
    click(ctx.window, button(ctx));
    await ctx.until(() => listed(ctx).length === 3);
    assert.equal(ctx.alerts.length, 1, "written, the delete goes on");
  });

  test("a file not indexed yet is said in the question and left; a photo on a share that did not answer stays listed and selected", async (t) => {
    const ctx = await organize(t);
    pick(ctx, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg");
    ctx.where = served.delete_check_with_missing;
    const away = "its folder could not be reached just now (a network share that did not answer), so nothing was written to it";
    serverDeletes(ctx, ["IMG_0001.jpg"], { ...served.deleted_with_missing, changed: 1, error_count: 1,
      errors: [{ id: 2, name: "IMG_0002.jpg", why: away }] });
    click(ctx.window, button(ctx));
    await ctx.until(() => listed(ctx).length === 4);
    await ctx.settle(60);
    assert.match(ctx.questions[0], /^Delete 2 photos\? 1 of the 3 selected is not in the library \(not indexed yet\) and is left where it is\./);
    assert.deepEqual(plain(calls(ctx, "/api/library/bulk-edit")[0].body.params), { token: served.delete_check_with_missing.token });
    assert.deepEqual(listed(ctx), ["IMG_0002.jpg", "IMG_0003.jpg", "IMG_0004.jpg", "CLIP_0005.mp4"]);
    assert.deepEqual(names(ctx.state.selectedThumbnails), ["IMG_0002.jpg", "IMG_0003.jpg"], "what stayed stays selected");
    assert.ok(!button(ctx).classList.contains("hidden"), "still something selected: the button is shown");
  });

  test("a write that stopped part-way (a 500): the folder is read again all the same, what went leaves the list", async (t) => {
    const ctx = await organize(t);
    pick(ctx, "IMG_0001.jpg", "IMG_0002.jpg");
    serverDeletes(ctx, ["IMG_0001.jpg"], { status: 500, body: { error: "The library stopped answering." } });
    const before = scansOf(ctx, FOLDER).length;
    click(ctx.window, button(ctx));
    await ctx.until(() => listed(ctx).length === 4);
    assert.equal(scansOf(ctx, FOLDER).length, before + 1);
    assert.deepEqual(listed(ctx), ["IMG_0002.jpg", "IMG_0003.jpg", "IMG_0004.jpg", "CLIP_0005.mp4"]);
    assert.deepEqual(names(ctx.state.selectedThumbnails), ["IMG_0002.jpg"]);
    assert.match(stripSaid(ctx), /The library stopped answering\./, "the strip says why");
    await ctx.settle(60);
    assert.equal(statusLine(ctx), "The library stopped answering.", "and the status line, once the folder is read again");
  });

  test("a clip playing among them stops before the request; deleted, it is not played again", async (t) => {
    const ctx = await organize(t);
    await openPhoto(ctx, "CLIP_0005.mp4");
    assert.match(videoSrc(ctx), /video-file\?id=9/, "the clip plays");
    pick(ctx, "IMG_0004.jpg", "CLIP_0005.mp4");
    let atRequest = null;
    serverDeletes(ctx, ["IMG_0004.jpg", "CLIP_0005.mp4"], { ...served.deleted, total: 2, requested: 2, changed: 2 });
    const route = ctx.server.routes[0].body;
    ctx.server.routes[0].body = (url) => { atRequest = videoSrc(ctx); return route(url); };
    click(ctx.window, button(ctx));
    await ctx.until(() => listed(ctx).length === 3);
    await ctx.settle(60);
    assert.equal(atRequest, "", "stopped before the server deletes its file");
    assert.equal(videoSrc(ctx), "", "a deleted clip is not asked for again");
    assert.equal(ctx.state.activePhotoPath, at("IMG_0003.jpg"), "the last of the list went: the one before it opens");
  });

  test("a clip the delete left (its file in use) plays again once the folder still lists it", async (t) => {
    const ctx = await organize(t);
    await openPhoto(ctx, "CLIP_0005.mp4");
    pick(ctx, "IMG_0004.jpg", "CLIP_0005.mp4");
    serverDeletes(ctx, ["IMG_0004.jpg"], { ...served.deleted, total: 2, requested: 2, changed: 1, error_count: 1,
      errors: [{ id: 9, name: "CLIP_0005.mp4", why: "the file is in use" }] });
    click(ctx.window, button(ctx));
    await ctx.until(() => listed(ctx).length === 4);
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, at("CLIP_0005.mp4"));
    assert.match(videoSrc(ctx), /video-file\?id=9/, "it plays again");
  });

  test("another folder opened while the delete is out: it is left as it is, and the first folder is not scanned", async (t) => {
    const ctx = await organize(t);
    pick(ctx, "IMG_0001.jpg");
    serverDeletes(ctx, ["IMG_0001.jpg"], { ...served.deleted, total: 1, requested: 1, changed: 1 }, { hold: true });
    click(ctx.window, button(ctx));
    await ctx.until(() => typeof ctx.finish === "function");
    await openFolder(ctx, OTHER);
    await ctx.settle(60);
    const before = scansOf(ctx, FOLDER).length;
    ctx.finish();
    await ctx.settle(120);
    assert.equal(scansOf(ctx, FOLDER).length, before, "nothing asks for the first folder");
    assert.equal(ctx.state.scannedFolder, OTHER);
    assert.deepEqual(listed(ctx), ["IMG_0101.jpg"], "the folder open now loses no card");
  });

  test("a library view opened while the delete is out: the view stays, no folder is scanned", async (t) => {
    const ctx = await organize(t);
    pick(ctx, "IMG_0001.jpg");
    serverDeletes(ctx, ["IMG_0001.jpg"], { ...served.deleted, total: 1, requested: 1, changed: 1 }, { hold: true });
    click(ctx.window, button(ctx));
    await ctx.until(() => typeof ctx.finish === "function");
    await ctx.popTo("?view=all");
    await ctx.until(() => ctx.state.library && ctx.state.library.status === "ready");
    const scans = calls(ctx, "/api/folder/scan").length;
    ctx.finish();
    await ctx.settle(120);
    assert.equal(calls(ctx, "/api/folder/scan").length, scans, "no folder is read for a delete asked in another");
    assert.ok(ctx.state.library, "the view stays open");
    assert.equal(refresh(ctx).disabled, false);
    assert.equal(refresh(ctx).title, "Ask the library for this view again", "the view's Refresh, not the folder's put back");
  });

  test("four clicks while the first is out: one check, one question, one delete", async (t) => {
    const ctx = await organize(t);
    pick(ctx, "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg");
    serverDeletes(ctx, ["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg"], served.deleted);
    for (let n = 0; n < 4; n++) click(ctx.window, button(ctx));
    await ctx.until(() => listed(ctx).length === 2);
    await ctx.settle(60);
    assert.equal(calls(ctx, "/selection/delete-check").length, 1);
    assert.equal(ctx.questions.length, 1);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 1);
  });

  test("Just look: the button is shown inert with its sentence, and a click sends nothing", async (t) => {
    const notHeld = { library: "photo_index", folder: FOLDER, photos: 5, photos_held: 0, photos_not_held: 5, folders_not_held: 1,
      first_not_held: FOLDER, has_roots: true, under_roots: false, ignored: false };
    const ctx = await organize(t, { membership: notHeld });
    click(ctx.window, el(ctx, "btn-just-look"));
    await ctx.settle(40);
    assert.ok(ctx.document.body.classList.contains("just-looking"));
    pick(ctx, "IMG_0001.jpg", "IMG_0002.jpg");
    const said = "photo_index does not hold this folder: delete one photo at a time with the panel's Delete";
    assert.ok(!button(ctx).classList.contains("hidden"), "shown by the selection");
    assert.equal(button(ctx).getAttribute("aria-disabled"), "true");
    assert.equal(button(ctx).title, said);
    click(ctx.window, button(ctx));
    await ctx.settle(60);
    assert.deepEqual(ctx.alerts, [said]);
    assert.equal(calls(ctx, "/selection/delete-check").length, 0);
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 0);
  });

  test("tag changes waiting for a selected photo: refused with the sentence, nothing asked", async (t) => {
    const ctx = await organize(t);
    pick(ctx, "IMG_0001.jpg", "IMG_0002.jpg");
    ctx.state.pending.stopped = true;   // the drain held, as after a failure: the change waits
    ctx.module("pending.js").pendTags([`${FOLDER}\\img_0002.JPG`], { add: ["Trips/Coast"] });
    click(ctx.window, button(ctx));
    await ctx.settle(60);
    assert.deepEqual(ctx.alerts, ["Tag changes for 1 of these photos are not written yet: wait for them, or cancel them in the list of writes, then delete."]);
    assert.equal(calls(ctx, "/selection/delete-check").length, 0);
  });

  test("one delete at a time: the panel's Delete refuses while the selection's runs, and the selection's while the panel's runs", async (t) => {
    const ctx = await organize(t);
    await openPhoto(ctx, "IMG_0004.jpg");
    pick(ctx, "IMG_0001.jpg");
    serverDeletes(ctx, ["IMG_0001.jpg"], { ...served.deleted, total: 1, requested: 1, changed: 1 }, { hold: true });
    click(ctx.window, button(ctx));
    await ctx.until(() => typeof ctx.finish === "function");
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(40);
    assert.deepEqual(ctx.alerts, [BIN_SENTENCE]);
    assert.equal(calls(ctx, "/api/photo/delete").length, 0, "the panel's photo is not deleted beside it");
    ctx.finish();
    await ctx.until(() => listed(ctx).length === 4);
    await ctx.settle(60);

    let answerPanel;
    ctx.server.first("/api/photo/delete", () => new Promise((resolve) => { answerPanel = () => resolve({ success: true }); }));
    pick(ctx, "IMG_0002.jpg");
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.until(() => typeof answerPanel === "function");
    assert.equal(button(ctx).disabled, true);
    assert.equal(button(ctx).title, ONE_DELETE_OUT);
    ctx.alerts.length = 0;
    await ctx.module("bulk-edit.js").deleteSelection();
    assert.deepEqual(ctx.alerts, [ONE_DELETE_OUT]);
    assert.equal(calls(ctx, "/selection/delete-check").length, 1, "only the first delete's check");
    answerPanel();
    await ctx.settle(80);
    assert.equal(button(ctx).disabled, false, "the panel's delete answered: the selection's Delete is back");
  });

  test("the panel's Delete clicked while the selection's check is out: the selection's, confirmed, says why it deletes nothing", async (t) => {
    const ctx = await organize(t);
    await openPhoto(ctx, "IMG_0004.jpg");
    pick(ctx, "IMG_0001.jpg");
    let answerCheck;
    ctx.server.first("/api/library/selection/delete-check", () => new Promise((resolve) => { answerCheck = () => resolve(ctx.where); }));
    let answerPanel;
    ctx.server.first("/api/photo/delete", () => new Promise((resolve) => { answerPanel = () => resolve({ success: true }); }));
    click(ctx.window, button(ctx));
    await ctx.until(() => typeof answerCheck === "function");
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.until(() => typeof answerPanel === "function");
    assert.deepEqual(ctx.alerts, [], "the panel's Delete went ahead: nothing of this page was busy");
    answerCheck();
    await ctx.settle(80);
    assert.equal(ctx.questions.length, 2, "the panel's question and the selection's");
    assert.equal(calls(ctx, "/api/library/bulk-edit").length, 0, "one delete at a time");
    assert.deepEqual(ctx.alerts, [ONE_DELETE_OUT], "said, not silently dropped");
    assert.equal(statusLine(ctx), ONE_DELETE_OUT);
    answerPanel();
    await ctx.settle(80);
    assert.equal(button(ctx).disabled, false, "the panel's delete answered: the selection's Delete is back");
  });

  test("in a folder: hidden with nothing selected, shown by a selection, off with the busy sentence while a delete runs", async (t) => {
    const ctx = await organize(t);
    assert.ok(button(ctx).classList.contains("hidden"), "nothing selected");
    pick(ctx, "IMG_0003.jpg");
    assert.ok(!button(ctx).classList.contains("hidden"));
    assert.equal(button(ctx).disabled, false);
    serverDeletes(ctx, ["IMG_0003.jpg"], { ...served.deleted, total: 1, requested: 1, changed: 1 }, { hold: true });
    click(ctx.window, button(ctx));
    await ctx.until(() => typeof ctx.finish === "function");
    assert.equal(button(ctx).disabled, true);
    assert.equal(button(ctx).title, BIN_SENTENCE);
    assert.equal(refresh(ctx).disabled, true, "the sidebar's Refresh is off: its scan would replace the delete's");
    assert.equal(refresh(ctx).title, BIN_SENTENCE);
    ctx.finish();
    await ctx.until(() => listed(ctx).length === 4);
    await ctx.settle(60);
    assert.ok(button(ctx).classList.contains("hidden"), "the one selected went: nothing selected");
    assert.equal(button(ctx).disabled, false);
    assert.equal(refresh(ctx).disabled, false);
    assert.equal(refresh(ctx).title, "Refresh files list");
  });
});
