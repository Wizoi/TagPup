/**
 * Owner scenario (V34): tagging a folder and moving on without losing anything (docs/plans/working-on-a-new-folder.md,
 * cut C). The owner tags 50 photos of a folder, and while the page writes them 25 at a time opens the next folder,
 * another library, or closes the tab. It was not clear what could be clicked off, or what would be lost.
 *
 * What the owner lives through: the write line under the status says which writes are sent (the server finishes them
 * whatever the page does) and which wait in this page (lost if it goes), and nothing once everything is written; the
 * browser's own Leave prompt asks only while something waits in the page, never for a write already sent. Opening
 * another folder with changes waiting asks, in the page's own dialog, Keep writing (first: Enter), Discard them or Stay
 * (Escape): Keep writing opens the folder and writes them in the background, every scan kept of the folder left
 * following the writes; another folder opened before they are sent does not ask again; rapid switches while it asks give
 * one question, its answer taking the folder first asked about; a write refused as changed on disk while kept writing is
 * read again and sent once more, and one that fails waits for Retry in the list, saying it is lost if the page goes. A
 * write refused because a photo it names by id was renamed elsewhere is the page's while that photo is read again: the
 * line says it waits, and closing the tab asks.
 * Another library is a new page, which would end the writes: it offers Stay first, and Discard them; one kept writing
 * is asked about there too. The phone's page has its own write line (mobile-edit.js) and does not share pending.js.
 *
 * The server's tag writes (photos/write, op `tags`) and saves (op `set`) are held until the test answers them, oldest
 * first, as the real entry point answers (harness.mjs tagFiles, taggedAs, saved); a photo read again by its id
 * (/api/library/photo) waits in `reads` the same.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, pageExports, SAVE, saved, saveCalls, TAGS, taggedAs,
  tagFiles, testClock,
} from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them
afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2022 Regatta";
const OTHER = "D:\\Library\\2022 Harbour";
const THIRD = "D:\\Library\\2022 Lighthouse";
const pathOf = (i) => `${FOLDER}\\IMG_${String(i).padStart(4, "0")}.JPG`;
const RECORDS = Array.from({ length: 50 }, (_, i) => photoRecord({ filename: `IMG_${String(i).padStart(4, "0")}.JPG`, path: pathOf(i), tags: [] }));
const otherOf = (folder) => Array.from({ length: 3 }, (_, i) => photoRecord({ filename: `DSC_${i}.JPG`, path: `${folder}\\DSC_${i}.JPG`, tags: [] }));

/**
 * FOLDER's 50 photos all selected, on library alpha; tag writes, saves and reads by id wait in `held`, `saves` and `reads`
 * for an answer. `ids` gives a photo the id its scan names it by (a clip's).
 */
async function taggingAFolder(t, { ids = {} } = {}) {
  const held = [];
  const saves = [];
  const reads = [];
  const files = tagFiles(RECORDS);
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", (url) => {
      const asked = new URL(url, "http://localhost:8090/").searchParams.get("path");
      return asked === FOLDER ? RECORDS.map((r) => ({ ...r, tags: [], ...(ids[r.path] ? { id: ids[r.path] } : {}) })) : otherOf(asked);
    })
    .on("/api/library/photo", () => new Promise((resolve) => reads.push((photo) => resolve({ photo }))))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on(TAGS, (url, body) => new Promise((resolve) => held.push({ resolve, body })))
    .on(SAVE, (url, body) => new Promise((resolve) => saves.push(() => resolve(saved()(url, body)))))
    .on("/api/taxonomy/create", { success: true })
    .on("/api/folder/suggest-status", { status: "completed", suggestions: {} });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/alpha/", server });
  ctx.window.alert = () => {};
  await openFolder(ctx, FOLDER);
  ctx.document.getElementById("btn-select-all-thumbnails").click();
  await flush(ctx.window, 4);
  return Object.assign(ctx, { held, saves, reads, files, state: pageExports(ctx.window, "web/tagpup/state.js").state });
}

async function addTags(ctx, text) {
  ctx.document.getElementById("bulk-add-tags-input").value = text;
  ctx.document.getElementById("btn-bulk-add-tags").click();
  await flush(ctx.window, 8);
}

/** Answer the oldest tag write waiting, as its files make it, or by `how`, a route's function (harness.mjs taggedAs). */
async function answer(ctx, how = {}) {
  assert.ok(ctx.held.length, "no tag write was waiting for an answer");
  const { resolve, body } = ctx.held.shift();
  resolve(typeof how === "function" ? how("", body) : ctx.files.reply(body, how));
  await flush(ctx.window, 8);
}

const writes = (ctx) => ctx.server.calls.filter((c) => TAGS(c.url, c.body)).map((c) => c.body);
const line = (ctx) => {
  const said = ctx.document.querySelector("#write-queue .write-queue-leave");
  return said && !said.hidden ? said.textContent : "";
};
const status = (ctx) => ctx.document.querySelector("#write-queue .write-queue-status").textContent;
const question = (ctx) => ctx.document.querySelector(".waiting-changes-modal.active");
const choices = (ctx) => [...question(ctx).querySelectorAll("[data-choice]")].map((b) => [b.dataset.choice, b.textContent]);
const waiting = (ctx) => ctx.state.pending.files.size;
const kept = (ctx, folder) => JSON.parse(ctx.window.localStorage.getItem(pageExports(ctx.window, "web/tagpup/cache.js").folderCacheKey(folder)) || "null");

/** Does closing the tab now ask (the browser's own Leave site?)? */
function leaving(ctx) {
  const event = new ctx.window.Event("beforeunload", { cancelable: true });
  ctx.window.dispatchEvent(event);
  return event.defaultPrevented;
}

async function choose(ctx, choice) {
  question(ctx).querySelector(`[data-choice='${choice}']`).click();
  await flush(ctx.window, 8);
}

function press(ctx, key) {
  const target = ctx.document.activeElement;
  target.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
}

describe("the write line says what leaving does", () => {
  test("25 sent and 25 waiting in this page, then 25 sent, then nothing: the browser asks only while some wait", async (t) => {
    const ctx = await taggingAFolder(t);
    assert.equal(line(ctx), "", "a line with nothing written");
    await addTags(ctx, "Events/Regatta");
    assert.equal(line(ctx), "25 photos sent, 25 waiting in this page: stay until they are sent");
    assert.equal(leaving(ctx), true, "closing the tab did not ask while 25 photos' changes were in the page");
    await answer(ctx);
    assert.equal(line(ctx), "25 photos sent: the server finishes them even if you leave");
    assert.equal(leaving(ctx), false, "closing the tab asked about a write the server finishes");
    await answer(ctx);
    assert.equal(line(ctx), "");
    assert.equal(status(ctx), "All changes saved");
    assert.equal(leaving(ctx), false);
  });

  test("a failed write's changes wait in this page for Retry, and closing the tab asks", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await answer(ctx, { fails: { [pathOf(0)]: true } });
    assert.equal(line(ctx), "26 photos waiting in this page for Retry: lost if you leave");
    assert.equal(leaving(ctx), true);
  });

  test("a single save sent says the server finishes it, and closing the tab asks nothing", async (t) => {
    const ctx = await taggingAFolder(t);
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);
    const box = ctx.document.getElementById("input-add-tag");
    box.value = "Places/Harbour";
    box.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    assert.equal(leaving(ctx), true, "text typed and not saved is in the page");
    ctx.document.getElementById("btn-save-details").click();
    await flush(ctx.window, 8);
    assert.equal(saveCalls(ctx.server).length, 1, "the save was not sent");
    assert.equal(line(ctx), "1 photo sent: the server finishes it even if you leave");
    assert.equal(leaving(ctx), false, "closing the tab asked about a save already sent");
    ctx.saves.shift()();
    await flush(ctx.window, 8);
    assert.equal(line(ctx), "");
  });
});

describe("opening another folder with changes waiting", () => {
  test("asks Keep writing, Discard them or Stay, Keep writing focused; Keep writing opens it and writes them all", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    assert.ok(question(ctx), "opening another folder did not ask");
    assert.equal(question(ctx).querySelector("h2").textContent, "Tag changes for 25 photos are not written yet");
    assert.deepEqual(choices(ctx), [["stay", "Stay"], ["discard", "Discard them"], ["keep", "Keep writing"]]);
    assert.equal(ctx.document.activeElement.dataset.choice, "keep", "Keep writing is not the default");
    assert.equal(ctx.state.scannedFolder, FOLDER, "the folder went before the owner answered");
    await choose(ctx, "keep");
    assert.equal(ctx.state.scannedFolder, OTHER);
    assert.equal(waiting(ctx), 25, "Keep writing dropped the changes");
    assert.equal(line(ctx), "25 photos sent, 25 waiting in this page: stay until they are sent");
    await answer(ctx);
    await answer(ctx);
    assert.equal(writes(ctx).length, 2, "the changes kept writing were not all sent");
    assert.deepEqual(Object.keys(writes(ctx)[1].changes), RECORDS.slice(25).map((r) => r.path));
    assert.equal(line(ctx), "");
    assert.ok(kept(ctx, FOLDER).photos.every((p) => p.tags.includes("Events/Regatta")),
      "the scan kept of the folder left does not hold what its files hold");
  });

  test("Enter is Keep writing; Escape is Stay, and the changes go on writing in the folder", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    press(ctx, "Escape");
    await flush(ctx.window, 8);
    assert.equal(question(ctx), null, "Escape did not close the question");
    assert.equal(ctx.state.scannedFolder, FOLDER, "Escape left the folder");
    assert.equal(ctx.document.getElementById("folder-path-input").value, FOLDER, "the box names the folder not opened");
    assert.equal(waiting(ctx), 25);
    await openFolder(ctx, OTHER);
    press(ctx, "Enter");
    await flush(ctx.window, 8);
    assert.equal(ctx.state.scannedFolder, OTHER, "Enter did not Keep writing");
    assert.equal(waiting(ctx), 25);
    await answer(ctx);
    await answer(ctx);
    assert.equal(writes(ctx).length, 2);
  });

  test("Discard them drops what was not sent; the write out finishes, and nothing is asked on closing", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    await choose(ctx, "discard");
    assert.equal(ctx.state.scannedFolder, OTHER);
    assert.equal(waiting(ctx), 0);
    assert.equal(line(ctx), "25 photos sent: the server finishes them even if you leave");
    assert.equal(leaving(ctx), false);
    await answer(ctx);
    assert.equal(writes(ctx).length, 1, "a change discarded was written");
  });

  test("rapid switches while it asks give one question, and the answer takes the folder first asked about", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    await openFolder(ctx, THIRD);
    await openFolder(ctx, FOLDER);
    assert.equal(ctx.document.querySelectorAll(".waiting-changes-modal").length, 1, "asked more than once");
    await choose(ctx, "keep");
    assert.equal(ctx.state.scannedFolder, OTHER, "the answer opened a folder other than the one asked about");
    assert.equal(waiting(ctx), 25);
    await answer(ctx);
    await answer(ctx);
    assert.equal(writes(ctx).length, 2);
  });

  test("kept writing, another folder opened before they are sent asks nothing and they are still written", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    await choose(ctx, "keep");
    await openFolder(ctx, THIRD);
    assert.equal(question(ctx), null, "asked again about the changes kept writing");
    assert.equal(ctx.state.scannedFolder, THIRD);
    await answer(ctx);
    await answer(ctx);
    assert.equal(writes(ctx).length, 2);
    assert.equal(waiting(ctx), 0);
  });

  test("kept writing, a write refused as changed on disk is read again and sent once more", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    await choose(ctx, "keep");
    await answer(ctx);
    await answer(ctx, taggedAs("renamed_since"));
    assert.equal(writes(ctx).length, 3, "the refused write was not sent once more");
    assert.deepEqual(Object.keys(writes(ctx)[2].changes), Object.keys(writes(ctx)[1].changes));
    await answer(ctx);
    assert.equal(waiting(ctx), 0);
    assert.equal(line(ctx), "");
  });

  test("a photo named by id renamed elsewhere: while it is read again the write is the page's, and closing the tab asks", async (t) => {
    const renamed = `${FOLDER}\\IMG_0003 Harbour.JPG`;
    const ctx = await taggingAFolder(t, { ids: { [pathOf(3)]: 503 } });
    await addTags(ctx, "Events/Regatta");
    await answer(ctx, taggedAs("renamed_since"));
    assert.equal(ctx.reads.length, 1, "the photo named by id was not read again");
    assert.equal(line(ctx), "50 photos waiting in this page: stay until they are sent", "a write refused was called sent");
    assert.equal(leaving(ctx), true, "closing the tab while the refused write was read again asked nothing: it was lost");
    ctx.reads.shift()({ ...RECORDS[3], id: 503, path: renamed, filename: "IMG_0003 Harbour.JPG", tags: [] });
    await flush(ctx.window, 8);
    assert.equal(writes(ctx).length, 2, "the refused write was not sent once more");
    assert.ok(Object.keys(writes(ctx)[1].changes).includes(renamed), "not named where it is now");
    await answer(ctx);
    await answer(ctx);
    assert.equal(waiting(ctx), 0);
    assert.equal(line(ctx), "");
    assert.equal(leaving(ctx), false);
  });

  test("kept writing, a write that fails waits for Retry in the list, and Retry writes it from the other folder", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    await choose(ctx, "keep");
    await answer(ctx);
    await answer(ctx, { fails: { [pathOf(30)]: true } });
    assert.equal(line(ctx), "1 photo waiting in this page for Retry: lost if you leave");
    assert.equal(leaving(ctx), true);
    ctx.document.querySelector(".write-queue-entry[data-status='failed'] .write-queue-retry").click();
    await flush(ctx.window, 8);
    assert.deepEqual(Object.keys(writes(ctx)[2].changes), [pathOf(30)]);
    await answer(ctx);
    assert.equal(waiting(ctx), 0);
    assert.equal(ctx.state.scannedFolder, OTHER);
  });
});

describe("choosing another library with changes waiting", () => {
  const choose_library = async (ctx, name) => {
    const select = ctx.document.getElementById("db-select");
    select.value = name;
    select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    return select;
  };

  test("offers Stay first and Discard them; Stay keeps the library and the changes writing", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    const select = await choose_library(ctx, "beta");
    assert.ok(question(ctx), "another library did not ask");
    assert.deepEqual(choices(ctx), [["discard", "Discard them"], ["stay", "Stay"]]);
    assert.equal(ctx.document.activeElement.dataset.choice, "stay", "Stay is not the default");
    assert.match(question(ctx).textContent, /new page/);
    press(ctx, "Enter");
    await flush(ctx.window, 8);
    assert.equal(select.value, "alpha", "the picker names the library not opened");
    await answer(ctx);
    await answer(ctx);
    assert.equal(writes(ctx).length, 2, "staying lost the changes not sent");
  });

  test("changes kept writing from a folder are asked about; Discard them goes, the write out sent asks nothing more", async (t) => {
    const ctx = await taggingAFolder(t);
    await addTags(ctx, "Events/Regatta");
    await openFolder(ctx, OTHER);
    await choose(ctx, "keep");
    await choose_library(ctx, "beta");
    assert.ok(question(ctx), "the changes kept writing were not asked about before a new page");
    assert.equal(question(ctx).querySelector("h2").textContent, "Tag changes for 25 photos are not written yet");
    await choose(ctx, "discard");
    assert.equal(waiting(ctx), 0);
    assert.equal(leaving(ctx), false, "the browser's own Leave site? follows the page's, for a write already sent");
    await answer(ctx);
    assert.equal(writes(ctx).length, 1);
  });
});
