/**
 * Owner scenario (V22): tagging many photos at once -- a folder of 50 bulk-tagged, a select-all of 1,000 -- while the
 * owner keeps clicking. Each file's tag change waits in the page (web/tagpup/pending.js) and goes 25 files a write, each
 * write one journaled change under its click's group token (docs/plans/one-queue-for-tag-changes.md, cut 3).
 *
 * What the owner lives through: a tag added while the first 25 are out reaches the 25 not yet sent in the same write and
 * the 25 already written in a later one; the last click on a tag wins; a write that fails keeps its files'
 * changes for Retry and never loops by itself; a single save clicked meanwhile goes between two writes; the open photo is
 * read-only while a change it does not draw waits for its file, saying how many, and editable again once that file is
 * written or its change dropped, its viewing and the arrows free throughout; Cancel says how
 * many photos it left unwritten, and a write already out neither says Ready over it nor gives its files back; choosing
 * another library or opening another folder asks while changes are not sent -- the tag question and the open photo's
 * together, nothing dropped or discarded until both are yes -- and leaving drops them; back from a library view to the
 * folder whose changes they are asks nothing, and staying leaves the view standing; a write's reply reaches every scan
 * this browser keeps of a folder holding its photos -- their own folder's, every folder above, while it is open or after it
 * was left, even while a view shows one of its photos -- and a page left with a write out forgets each of them (#3308);
 * photos missing on disk are said once, when the click's change is done.
 *
 * The server's photos/write replies to a tag write (op `tags`) are held here until the test answers them, oldest first,
 * with the real entry point's reply where the test reads what it wrote (harness.mjs taggedAs); a save of one photo (op
 * `set`) is answered at once.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports, SAVE, saved, saveCalls, taggedAs,
  TAGS_REPLIES,
} from "./harness.mjs";
import { loadViewPage, recordOf } from "./view-page.mjs";

const FOLDER = "D:\\Library\\2020";
const pathOf = (i) => `${FOLDER}\\IMG_${String(i).padStart(4, "0")}.JPG`;
const OTHER = "D:\\Library\\2021";

function records(n, tags) {
  return Array.from({ length: n }, (_, i) => photoRecord({ filename: `IMG_${String(i).padStart(4, "0")}.JPG`, path: pathOf(i), tags: [...tags] }));
}

/** What a scan answers: 30 photos in OTHER, `n` holding `tags` in FOLDER. */
const scanOf = (n, tags) => (url) => (url.includes(encodeURIComponent(OTHER))
  ? Array.from({ length: 30 }, (_, i) => photoRecord({ filename: `IMG_9${String(i).padStart(3, "0")}.JPG`, path: `${OTHER}\\IMG_9${String(i).padStart(3, "0")}.JPG` }))
  : records(n, tags));

/** The page's own answers and questions on `ctx`: replies held for `answer`, alerts kept, confirm says OK unless `stay`. */
function heldPage(ctx, held) {
  const page = Object.assign(ctx, { held, alerts: [], confirms: [], stay: false });
  page.window.alert = (text) => page.alerts.push(String(text));
  page.window.confirm = (text) => { page.confirms.push(String(text)); return !page.stay; };
  return page;
}

/** FOLDER's 50 photos, all selected, in a page that also shows library views (view-page.mjs). */
async function viewableFolder(t, options = {}) {
  const held = [];
  const ctx = await loadViewPage(t, options);
  ctx.server.first("/api/folder/scan", scanOf(50, [])).first("/api/photos/write", () => new Promise((resolve) => held.push(resolve)));
  await openFolder(ctx, FOLDER);
  await ctx.settle(60);
  ctx.document.getElementById("btn-select-all-thumbnails").click();
  await flush(ctx.window, 4);
  return heldPage(ctx, held);
}

/** A folder of `n` photos holding `tags`, all selected; its photos/write replies wait for `answer`. */
async function folderOf(t, n, { tags = [], bulk, scan = scanOf(n, tags) } = {}) {
  const held = [];
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", scan)
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photos/write", bulk || (() => new Promise((resolve) => held.push(resolve))))
    .on(SAVE, saved())
    .on("/api/taxonomy/create", { success: true })
    .on("/api/folder/suggest-status", { status: "completed", suggestions: {} });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/alpha/", server });
  await openFolder(ctx, FOLDER);
  ctx.document.getElementById("btn-select-all-thumbnails").click();
  await flush(ctx.window, 4);
  return heldPage({ ...ctx }, held);
}

const isTagWrite = (c) => c.url.includes("/api/photos/write") && !SAVE(c.url, c.body);
const writes = (ctx) => ctx.server.calls.filter(isTagWrite).map((c) => c.body);
const status = (ctx) => ctx.document.getElementById("status-text").textContent;
const scans = (ctx, folder) => ctx.server.calls.filter((c) => c.url.includes(`/api/folder/scan?path=${encodeURIComponent(folder)}`)).length;
const pendingOf = (ctx) => pageExports(ctx.window, "web/tagpup/state.js").state.pending;
const LEAVE = "Tag changes for 25 photos are not written yet. Leave without writing them?";
const kept = (ctx, folder) => JSON.parse(ctx.window.localStorage.getItem(pageExports(ctx.window, "web/tagpup/cache.js").folderCacheKey(folder)) || "null");

/** Open the first photo and type a tag in its box, not saved. */
async function typeInOpenPhoto(ctx, text) {
  ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
  await flush(ctx.window, 8);
  const input = ctx.document.getElementById("input-add-tag");
  input.value = text;
  input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
  await flush(ctx.window, 4);
}

async function choose(ctx, choice) {
  ctx.document.querySelector(`.unsaved-edits-modal.active [data-choice='${choice}']`).click();
  await flush(ctx.window, 8);
}

/** The real entry point's reply `name` to the last tag write sent (harness.mjs taggedAs), `own(path, n)` over each photo's. */
const replyTo = (ctx, name, own) => taggedAs(name, own)("", writes(ctx).at(-1));

async function answer(ctx, reply = { success: true }) {
  assert.ok(ctx.held.length, "no write was waiting for an answer");
  ctx.held.shift()(reply);
  await flush(ctx.window, 8);
}

async function addTags(ctx, text) {
  ctx.document.getElementById("bulk-add-tags-input").value = text;
  click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
  await flush(ctx.window, 8);
}

afterEach(() => closeAllApps());

describe("a tag added while a 50-photo change is being written", () => {
  test("rides the write of the 25 not yet sent, and reaches the 25 written in a later one; each file once a write", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    assert.equal(writes(ctx).length, 1);
    const first = Object.keys(writes(ctx)[0].changes);
    assert.deepEqual(first, Array.from({ length: 25 }, (_, i) => pathOf(i)), "the first write is not the first 25, as named");
    await addTags(ctx, "Places/Harbour");
    assert.equal(writes(ctx).length, 1, "a second write went beside the first");

    await answer(ctx);
    const second = writes(ctx)[1];
    assert.deepEqual(Object.keys(second.changes), Array.from({ length: 25 }, (_, i) => pathOf(25 + i)));
    for (const change of Object.values(second.changes)) assert.deepEqual(change, { id: null, add: ["Trips/Coast", "Places/Harbour"], remove: [] });
    assert.equal(second.group, writes(ctx)[0].group, "the files not yet sent left their first click's group");

    await answer(ctx);
    const third = writes(ctx)[2];
    assert.deepEqual(Object.keys(third.changes), first);
    for (const change of Object.values(third.changes)) assert.deepEqual(change, { id: null, add: ["Places/Harbour"], remove: [] });
    assert.notEqual(third.group, writes(ctx)[0].group, "a new change for files already written joined the first group");
    await answer(ctx);
    assert.equal(writes(ctx).length, 3);
    assert.equal(ctx.held.length, 0);
  });
});

describe("clicks that take each other back", () => {
  test("the last click on a tag wins: remove-then-add is the add, add-then-remove the removal", async (t) => {
    const ctx = await folderOf(t, 30, { tags: ["Places/Coast"] });
    await addTags(ctx, "Trips/Kept");   // holds the queue: the 25 first files are out
    const chip = [...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")]
      .find((c) => c.textContent.includes("Places/Coast"));
    click(ctx.window, chip.querySelector(".selection-summary-chip-remove"));
    await flush(ctx.window, 4);
    await addTags(ctx, "Places/Coast");
    await addTags(ctx, "Trips/Gone");
    const gone = [...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")]
      .find((c) => c.textContent.includes("Trips/Gone"));
    assert.ok(gone, "a tag waiting to be written is not shown on the selection (pending.js shownTags)");
    const { removeTagFromAllSelected } = pageExports(ctx.window, "web/tagpup/selection.js");
    removeTagFromAllSelected("Trips/Gone", false);
    await flush(ctx.window, 4);

    await answer(ctx);
    // The 5 files never sent carry Trips/Kept and the Places/Coast that came off and back as an add (never a removal);
    // Trips/Gone, added and taken off, is a removal (the last click wins), never an add.
    const second = writes(ctx)[1].changes;
    assert.equal(Object.keys(second).length, 5);
    for (const change of Object.values(second)) assert.deepEqual(change, { id: null, add: ["Trips/Kept", "Places/Coast"], remove: ["Trips/Gone"] });
    await answer(ctx);
    const third = writes(ctx)[2].changes;
    assert.equal(Object.keys(third).length, 25);
    for (const change of Object.values(third)) assert.deepEqual(change, { id: null, add: ["Places/Coast"], remove: ["Trips/Gone"] });
    await answer(ctx);
    assert.equal(writes(ctx).length, 3);
    assert.ok(!JSON.stringify(writes(ctx).map((w) => Object.values(w.changes).map((c) => c.add))).includes("Trips/Gone"), "a tag taken off before it was sent was still added");
  });
});

describe("a record older than its file", () => {
  test("an add the record says the file holds is still sent, and so is a removal of a tag the record lacks", async (t) => {
    const ctx = await folderOf(t, 3, { tags: ["Places/Coast"], bulk: () => ({ success: true }) });
    await addTags(ctx, "Places/Coast");
    assert.deepEqual(Object.values(writes(ctx)[0].changes)[0], { id: null, add: ["Places/Coast"], remove: [] });
    const { removeTagFromAllSelected } = pageExports(ctx.window, "web/tagpup/selection.js");
    removeTagFromAllSelected("Trips/Elsewhere", false);
    await flush(ctx.window, 8);
    assert.equal(writes(ctx).length, 2, "a removal the record thought done was not sent");
    assert.deepEqual(Object.values(writes(ctx)[1].changes)[0], { id: null, add: [], remove: ["Trips/Elsewhere"] });
  });
});

describe("a write that fails part-way", () => {
  test("its unwritten files' changes go back first, nothing more is sent, and Retry goes on from them", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    const written = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [pathOf(i), ["Trips/Coast"]]));
    await answer(ctx, { success: false, error: "ExifTool: could not write IMG_0010.JPG", written });
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /15 photos: ExifTool: could not write/);
    for (let i = 0; i < 4; i++) await flush(ctx.window, 4);
    assert.equal(writes(ctx).length, 1, "the drain went on by itself after a failure");
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.deepEqual(state.folderPhotos[0].tags, ["Trips/Coast"], "a file the reply wrote is not shown holding it");
    assert.deepEqual(state.folderPhotos[10].tags, [], "a file the write never reached is shown holding it");

    click(ctx.window, ctx.document.querySelector(".write-queue-retry"));
    await flush(ctx.window, 8);
    const again = Object.keys(writes(ctx)[1].changes);
    assert.deepEqual(again, Array.from({ length: 25 }, (_, i) => pathOf(10 + i)), "the files given back did not go first");
    assert.equal(writes(ctx)[1].group, writes(ctx)[0].group);
    await answer(ctx);
    await answer(ctx);
    assert.equal(writes(ctx).length, 3);
  });

  test("a page that fails to draw the reply has still given the unwritten files' changes back", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    pageExports(ctx.window, "web/tagpup/hooks.js").upper.renderThumbnails = () => { throw new Error("drawing failed"); };
    const written = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [pathOf(i), ["Trips/Coast"]]));
    await answer(ctx, { success: false, error: "ExifTool: could not write IMG_0010.JPG", written });
    assert.equal(pendingOf(ctx).files.size, 40, "the 15 files the write never reached lost their changes");
    assert.equal(pendingOf(ctx).stopped, true, "the drain did not stop");
  });

  test("a network that is gone keeps every change, says so, and Retry sends them", async (t) => {
    let refuse = true;
    const ctx = await folderOf(t, 3, {
      bulk: () => (refuse ? Promise.reject(new TypeError("Failed to fetch")) : { success: true }),
    });
    await addTags(ctx, "Trips/Coast");
    assert.equal(ctx.alerts.length, 1, "the failure was not said");
    assert.match(status(ctx), /Tags not written to 3 photos/);
    refuse = false;
    click(ctx.window, ctx.document.querySelector(".write-queue-retry"));
    await flush(ctx.window, 8);
    assert.equal(writes(ctx).length, 2);
    assert.deepEqual(writes(ctx)[1].changes, writes(ctx)[0].changes);
  });
});

describe("a single save clicked between two writes", () => {
  test("goes between them, and the file gets both the tag and the save", async (t) => {
    const ctx = await folderOf(t, 75);
    await addTags(ctx, "Trips/Coast");
    await answer(ctx);
    // The first photo's chunk is written (one still waiting would hold its panel read-only); the second is out.
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);
    const input = ctx.document.getElementById("input-add-tag");
    input.focus();
    input.value = "Places/Beach";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);

    await answer(ctx);
    for (let i = 0; i < 20 && ctx.server.calls.filter(isTagWrite).length < 3; i++) await flush(ctx.window, 2);
    const order = ctx.server.calls.filter((c) => c.url.includes("/api/photos/write")).map((c) => (SAVE(c.url, c.body) ? "save" : "bulk"));
    assert.deepEqual(order, ["bulk", "bulk", "save", "bulk"]);
    const save = saveCalls(ctx.server)[0].body;
    assert.deepEqual(save.tags, ["Trips/Coast", "Places/Beach"]);
    await answer(ctx);
  });
});

describe("the open photo while its tag changes wait", () => {
  const WAITING_ONE = "1 tag change is waiting for this photo. Edit it when it is written.";
  const panel = (ctx) => {
    const note = ctx.document.getElementById("photo-missing-note");
    return {
      editable: ["btn-edit-tags", "btn-edit-people", "btn-edit-date-taken"].every((id) => !ctx.document.getElementById(id).hasAttribute("inert")),
      deletes: !ctx.document.getElementById("btn-delete-photo").hasAttribute("inert"),
      said: note.classList.contains("hidden") ? "" : note.textContent,
    };
  };
  const active = (ctx) => pageExports(ctx.window, "web/tagpup/state.js").state.activePhotoPath;
  const arrow = async (ctx) => {
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);
  };
  const openPhoto = async (ctx, i) => {
    ctx.document.querySelector(`li[data-path="${pathOf(i).replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 8);
  };

  test("a bulk click that includes it makes it read-only, saying so; viewing and the arrows go on; written, it is editable; its Delete waits", async (t) => {
    const ctx = await folderOf(t, 50);
    await arrow(ctx);
    assert.equal(active(ctx), pathOf(0));
    assert.deepEqual(panel(ctx), { editable: true, deletes: true, said: "" });
    await addTags(ctx, "Trips/Coast");
    assert.deepEqual(panel(ctx), { editable: false, deletes: true, said: WAITING_ONE }, "the open photo's panel stayed editable under a change it does not draw");
    await arrow(ctx);
    assert.equal(active(ctx), pathOf(1), "the arrows stopped while changes wait");
    assert.deepEqual(panel(ctx), { editable: false, deletes: true, said: WAITING_ONE });
    // Its Delete is offered, and refused with the selection's rule while the change waits (pending.js deleteWaits).
    ctx.document.getElementById("btn-delete-photo").click();
    await flush(ctx.window, 4);
    assert.deepEqual(ctx.alerts, ["Tag changes for this photo are not written yet: wait for them, or cancel them in the list of writes, then delete."]);
    assert.deepEqual(ctx.confirms, [], "the delete was asked about while its tag change waits");
    assert.equal(ctx.server.calls.filter((c) => c.url.includes("/api/photo/delete")).length, 0);
    await answer(ctx);
    assert.deepEqual(panel(ctx), { editable: true, deletes: true, said: "" }, "the photo stayed read-only once its file was written");
    assert.ok(ctx.document.querySelector("#detail-tags .tag-pill"), "the written tag is not drawn");
  });

  test("Cancel while a write is out gives back the panel of a photo whose changes were not sent", async (t) => {
    const ctx = await folderOf(t, 60);
    await addTags(ctx, "Trips/Coast");
    await addTags(ctx, "Places/Harbour");
    await openPhoto(ctx, 30);
    assert.deepEqual(panel(ctx), { editable: false, deletes: true, said: "2 tag changes are waiting for this photo. Edit it when they are written." });
    click(ctx.window, ctx.document.querySelector(".write-queue-entry[data-status='writing'] .write-queue-cancel"));
    await flush(ctx.window, 4);
    assert.deepEqual(panel(ctx), { editable: true, deletes: true, said: "" }, "the changes dropped still hold the panel");
    await answer(ctx);
  });

  test("a failed write keeps the panel read-only until it is cancelled", async (t) => {
    const ctx = await folderOf(t, 60);
    await addTags(ctx, "Trips/Coast");
    await openPhoto(ctx, 0);
    assert.equal(panel(ctx).editable, false, "the file in the write out is editable");
    await answer(ctx, { success: false, error: "The share did not answer" });
    assert.deepEqual(panel(ctx), { editable: false, deletes: true, said: WAITING_ONE }, "the failed write's changes, given back, no longer hold the panel");
    click(ctx.window, ctx.document.querySelector(".write-queue-entry[data-status='failed'] .write-queue-cancel"));
    await flush(ctx.window, 4);
    assert.deepEqual(panel(ctx), { editable: true, deletes: true, said: "" }, "the failed write's changes, cancelled, still hold the panel");
    assert.equal(writes(ctx).length, 1, "a cancelled change was written");
  });
});

describe("a select-all of 1,000", () => {
  test("is 40 writes under one group, and a later click's change rides them on the shared files", async (t) => {
    let first = true;
    const held = [];
    const ctx = await folderOf(t, 1000, {
      bulk: () => (first ? (first = false, new Promise((resolve) => held.push(resolve))) : { success: true }),
    });
    await addTags(ctx, "Trips/Coast");
    assert.ok(ctx.confirms.some((m) => m === "Add Trips/Coast to 1,000 photos?"), ctx.confirms.join("|"));
    await addTags(ctx, "Places/Harbour");
    // The later click queued the next chunk at the click (one queue, in the order clicked); the row out says what it writes.
    const label = ctx.document.querySelector(".write-queue-entry[data-status='writing'] .write-queue-label").textContent;
    assert.equal(label, "Add Trips/Coast to 25 photos, 975 more pending");
    held.shift()({ success: true });
    for (let i = 0; i < 100 && writes(ctx).length < 41; i++) await flush(ctx.window, 2);

    const sent = writes(ctx);
    assert.equal(sent.length, 41);
    const groups = sent.map((body) => body.group);
    assert.equal(new Set(groups.slice(0, 40)).size, 1, "one owner action's writes carry more than one group");
    assert.match(groups[0], /^[A-Za-z0-9_.:-]{1,64}$/);
    assert.notEqual(groups[40], groups[0]);
    const paths = sent.slice(0, 40).flatMap((body) => Object.keys(body.changes));
    assert.equal(new Set(paths).size, 1000, "a file was left out or written twice");
    assert.deepEqual(Object.keys(sent[40].changes), Object.keys(sent[0].changes), "the later tag missed the files written first");
  });
});

describe("Cancel while a change is being written", () => {
  test("the write out finishes, the rest is dropped, and the page says how many photos were left unwritten", async (t) => {
    const ctx = await folderOf(t, 60);
    await addTags(ctx, "Trips/Coast");
    const cancel = ctx.document.querySelector(".write-queue-entry[data-status='writing'] .write-queue-cancel");
    assert.ok(cancel, "a change being written offers no Cancel");
    click(ctx.window, cancel);
    await flush(ctx.window, 2);
    assert.match(status(ctx), /35 photos' tag changes were not written/);
    await answer(ctx);
    assert.equal(writes(ctx).length, 1, "a cancelled change was written");
    assert.match(status(ctx), /35 photos' tag changes were not written/, "the write out said Ready over the Cancel");
  });

  test("the write out failing after Cancel does not give its files back nor offer Retry", async (t) => {
    const ctx = await folderOf(t, 60);
    await addTags(ctx, "Trips/Coast");
    click(ctx.window, ctx.document.querySelector(".write-queue-entry[data-status='writing'] .write-queue-cancel"));
    await flush(ctx.window, 2);
    await answer(ctx, { success: false, error: "ExifTool: could not write IMG_0001.JPG" });
    assert.equal(pendingOf(ctx).files.size, 0, "a cancelled drain took the failed write's files back");
    assert.equal(pendingOf(ctx).stopped, false);
    assert.equal(ctx.document.querySelector(".write-queue-retry"), null, "a cancelled drain offers Retry");
    assert.equal(ctx.alerts.length, 0);
    assert.match(status(ctx), /Tags not written to 25 more photos/);
    for (let i = 0; i < 4; i++) await flush(ctx.window, 4);
    assert.equal(writes(ctx).length, 1);
  });
});

describe("photos missing on disk", () => {
  test("are said once, when the click's change is done, not by each write", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    await answer(ctx, { success: true, skipped_missing: 1, skipped: [{ path: pathOf(3), why: "missing, file gone" }] });
    assert.doesNotMatch(status(ctx), /missing/, "said before the change was done");
    await answer(ctx);
    assert.match(status(ctx), /^1 photo is missing on disk and was left out\./);
  });
});

describe("the record's own spelling of its path", () => {
  test("is the key the change is sent under, and a reply in another spelling still reaches the record", async (t) => {
    const ctx = await folderOf(t, 1);
    await addTags(ctx, "Trips/Coast");
    assert.deepEqual(Object.keys(writes(ctx)[0].changes), [pathOf(0)]);
    await answer(ctx, { success: true, written: { [pathOf(0).toLowerCase()]: { tags: ["Places/Held", "Trips/Coast"] } } });
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.deepEqual(state.folderPhotos[0].tags, ["Places/Held", "Trips/Coast"]);
  });
});

describe("choosing another library while changes wait", () => {
  test("asks while files are not sent, and staying keeps them and the library", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    ctx.stay = true;
    const select = ctx.document.getElementById("db-select");
    select.value = "beta";
    select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.ok(ctx.confirms.includes("Tag changes for 25 photos are not written yet. Leave without writing them?"), ctx.confirms.join("|"));
    assert.equal(select.value, "alpha", "the picker still shows the library left");
    await answer(ctx);
    assert.equal(writes(ctx).length, 2, "staying lost the changes not yet sent");
    await answer(ctx);
  });

  test("leaving drops the changes not sent: once the write out is answered, nothing more is written or asked", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    const select = ctx.document.getElementById("db-select");
    select.value = "beta";
    select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.ok(ctx.confirms.includes("Tag changes for 25 photos are not written yet. Leave without writing them?"), ctx.confirms.join("|"));
    assert.equal(pendingOf(ctx).files.size, 0, "the changes not sent are still waiting");
    await answer(ctx);
    assert.equal(writes(ctx).length, 1, "a change left behind was written");
    const unload = new ctx.window.Event("beforeunload", { cancelable: true });
    ctx.window.dispatchEvent(unload);
    assert.equal(unload.defaultPrevented, false, "the browser's own Leave site? follows the page's");
  });
});

describe("leaving with changes waiting and a photo's edits typed", () => {
  test("another library: Cancel on the photo's question drops nothing, and the drain goes on", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    await typeInOpenPhoto(ctx, "Places/Typed");
    const select = ctx.document.getElementById("db-select");
    select.value = "beta";
    select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.ok(ctx.confirms.includes(LEAVE), ctx.confirms.join("|"));
    await choose(ctx, "cancel");
    assert.equal(select.value, "alpha");
    assert.equal(pendingOf(ctx).files.size, 25, "the changes not sent were dropped though the owner stayed");
    assert.doesNotMatch(status(ctx), /not written/);
    await answer(ctx);
    assert.equal(writes(ctx).length, 2, "the drain stopped though the owner stayed");
    await answer(ctx);
  });

  test("another folder: staying on the tag question keeps what was typed in the photo", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    await typeInOpenPhoto(ctx, "Places/Typed");
    ctx.stay = true;
    await openFolder(ctx, OTHER);
    if (ctx.document.querySelector(".unsaved-edits-modal.active")) await choose(ctx, "discard");
    assert.ok(ctx.confirms.includes(LEAVE), ctx.confirms.join("|"));
    assert.equal(ctx.document.getElementById("input-add-tag").value, "Places/Typed", "the typed tag was discarded though the owner stayed");
    assert.equal(pageExports(ctx.window, "web/tagpup/state.js").state.scannedFolder, FOLDER);
    await answer(ctx);
    await answer(ctx);
  });

  test("a failed write's row left behind cannot Cancel the next folder's changes", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    await answer(ctx, { success: false, error: "ExifTool: could not write IMG_0001.JPG" });
    await openFolder(ctx, OTHER);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    await flush(ctx.window, 4);
    await addTags(ctx, "Trips/Hills");
    assert.equal(pendingOf(ctx).files.size, 5);
    const old = ctx.document.querySelector(".write-queue-entry[data-status='failed']");
    assert.ok(old, "the failed write's row is gone");
    assert.equal(old.querySelector(".write-queue-cancel"), null, "the old row offers Cancel of a drain that is gone");
    assert.equal(old.querySelector(".write-queue-retry"), null, "the old row offers Retry of a drain that is gone");
    await answer(ctx);
    assert.equal(writes(ctx).length, 3);
    assert.deepEqual(Object.keys(writes(ctx)[2].changes).length, 5);
  });
});

describe("a library view between a folder and its changes", () => {
  test("back from the view onto the folder whose changes wait asks nothing and keeps them", async (t) => {
    const ctx = await viewableFolder(t);
    await addTags(ctx, "Trips/Coast");
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle(100);
    assert.ok(ctx.state.library, "no view opened");
    ctx.window.history.back();
    await ctx.settle(300);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, FOLDER);
    assert.ok(!ctx.confirms.some((m) => /not written yet/.test(m)), ctx.confirms.join("|"));
    assert.equal(pendingOf(ctx).files.size, 25, "the folder's own changes were dropped");
    await answer(ctx);
    await answer(ctx);
  });

  test("back onto another folder asks before the view goes: staying leaves the view standing", async (t) => {
    const ctx = await viewableFolder(t);
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle(100);
    await pageExports(ctx.window, "web/tagpup/library-moves.js").openInOrganize(OTHER);
    await ctx.settle(200);
    assert.equal(ctx.state.scannedFolder, OTHER);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    await flush(ctx.window, 4);
    await addTags(ctx, "Trips/Hills");
    ctx.window.history.back();
    await ctx.settle(300);
    assert.ok(ctx.state.library, "Back onto a view did not open it");
    ctx.stay = true;
    ctx.window.history.back();
    await ctx.settle(300);
    assert.ok(ctx.confirms.includes("Tag changes for 5 photos are not written yet. Leave without writing them?"), ctx.confirms.join("|"));
    assert.ok(ctx.state.library, "staying left a page with neither the view nor a folder");
    assert.equal(pendingOf(ctx).files.size, 5);
    await answer(ctx);
    await answer(ctx);
  });

  test("a write out reaches the folder's kept records while the view shows one of its photos", async (t) => {
    const ctx = await viewableFolder(t, { onPhoto: (id) => ({ photo: recordOf(id, { path: pathOf(0), filename: "IMG_0000.JPG", tags: [] }) }) });
    await addTags(ctx, "Trips/Coast");
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle(100);
    ctx.cardById(11).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(80);
    assert.equal(ctx.state.folderPhotos[0].path, pathOf(0), "the view does not show the folder's photo");
    await answer(ctx, replyTo(ctx, "written", () => ({ tags: ["Trips/Coast"], mtime: 1700000000, size: 2048 })));
    const record = kept(ctx, FOLDER).photos[0];
    assert.equal(record.mtime, 1700000000, "the kept record names the file's old stamp: a save from it is refused");
    assert.deepEqual(record.tags, ["Trips/Coast"]);
  });
});

describe("a write's reply and the scans this browser keeps of folders holding its photos", () => {
  // A scan is recursive: LIBRARY's holds FOLDER's photos, SUB's and OTHER's; FOLDER's holds its own and SUB's.
  const LIBRARY = "D:\\Library";
  const SUB = `${FOLDER}\\Heats`;
  const NESTED = [
    [FOLDER, "IMG_0001.JPG"], [FOLDER, "IMG_0002.JPG"], [SUB, "IMG_0101.JPG"], [SUB, "IMG_0102.JPG"], [OTHER, "IMG_9001.JPG"],
  ].map(([folder, filename]) => photoRecord({ filename, path: `${folder}\\${filename}`, tags: [] }));
  const nestedScan = (url) => {
    const asked = new URL(url, "http://localhost:8090/").searchParams.get("path");
    return NESTED.filter((p) => p.path.startsWith(`${asked}\\`)).map((p) => ({ ...p, tags: [...p.tags] }));
  };
  const WRITTEN = NESTED.slice(0, 4).map((p) => p.path);
  const FILE_TAGS = TAGS_REPLIES.written.reply.written.PHOTO_0.tags;

  /** LIBRARY, SUB and OTHER looked at first, then FOLDER opened and its photos (its own and SUB's) all selected. */
  async function nestedFolder(t) {
    const ctx = await folderOf(t, 0, { scan: nestedScan });
    for (const folder of [LIBRARY, SUB, OTHER, FOLDER]) await openFolder(ctx, folder);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    await flush(ctx.window, 4);
    return ctx;
  }

  for (const left of [false, true]) {
    test(`reaches the kept scan of the folder above and of the subfolder${left ? ", the folder left before the reply" : ""}`, async (t) => {
      const ctx = await nestedFolder(t);
      await addTags(ctx, "Trips/Coast");
      assert.deepEqual(Object.keys(writes(ctx)[0].changes), WRITTEN);
      if (left) await openFolder(ctx, OTHER);
      await answer(ctx, replyTo(ctx, "written", (path, n) => ({ mtime: 1700000000 + n })));
      for (const folder of [LIBRARY, FOLDER, SUB]) {
        const records = kept(ctx, folder).photos.filter((p) => WRITTEN.includes(p.path));
        assert.ok(records.length, `${folder} kept none of the photos`);
        for (const record of records) {
          assert.equal(record.mtime, 1700000000 + WRITTEN.indexOf(record.path),
            `${folder}'s kept ${record.filename} names its file's old stamp: a save from it is refused`);
          assert.deepEqual(record.tags, FILE_TAGS, `${folder}'s kept ${record.filename} does not hold what its file holds`);
        }
      }
      const other = kept(ctx, LIBRARY).photos.find((p) => p.path === NESTED[4].path);
      assert.deepEqual(other.tags, [], "a photo the write did not name took its tags");
      await openFolder(ctx, SUB);
      assert.equal(scans(ctx, SUB), 1, "the subfolder was scanned again rather than drawn from its kept scan");
    });
  }

  test("a write that wrote nothing (a share gone) changes no kept scan", async (t) => {
    const ctx = await nestedFolder(t);
    const before = [LIBRARY, SUB].map((folder) => kept(ctx, folder));
    await addTags(ctx, "Trips/Coast");
    await answer(ctx, replyTo(ctx, "not_written"));
    assert.deepEqual([LIBRARY, SUB].map((folder) => kept(ctx, folder)), before);
  });

  test("the page left with a write out forgets every kept scan holding its photos, above and below; another stays", async (t) => {
    const ctx = await nestedFolder(t);
    await addTags(ctx, "Trips/Coast");
    assert.equal(ctx.held.length, 1, "the write is not out");
    ctx.window.dispatchEvent(new ctx.window.Event("pagehide"));
    for (const folder of [LIBRARY, FOLDER, SUB]) {
      assert.equal(kept(ctx, folder), null, `${folder}'s kept records keep the stamps from before a write that may have happened`);
    }
    assert.ok(kept(ctx, OTHER), "a kept scan holding none of the photos was forgotten");
  });
});

describe("an Undo pressed in another folder", () => {
  test("its write reaches the records kept of the photos' own folder, so a save there opened again is not refused", async (t) => {
    const ctx = await folderOf(t, 3, { tags: ["Trips/Coast"] });
    assert.ok(kept(ctx, FOLDER), "the folder's scan was not kept");
    await openFolder(ctx, OTHER);
    // As undo.js pends it: the change made in FOLDER taken back while OTHER is open (Ctrl+Z).
    const { pendChanges } = pageExports(ctx.window, "web/tagpup/pending.js");
    pendChanges([{ path: pathOf(1), remove: ["Trips/Coast"] }]);
    await flush(ctx.window, 8);
    await answer(ctx, replyTo(ctx, "written", () => ({ tags: [], mtime: 1700000500, size: 4096 })));
    const record = kept(ctx, FOLDER).photos[1];
    assert.equal(record.mtime, 1700000500, "the kept record names the stamp the file had before the Undo: a save from it is refused");
    assert.deepEqual(record.tags, [], "the kept record still holds the tag the Undo took off");
    assert.ok(!JSON.stringify(kept(ctx, OTHER)).includes("IMG_0001"), "the folder open when Undo was pressed holds no record of the photo");
  });
});

describe("opening another folder while changes wait", () => {
  test("asks; leaving drops the files not sent, and the write out reaches the records kept of the folder left", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    await openFolder(ctx, OTHER);
    assert.ok(ctx.confirms.includes("Tag changes for 25 photos are not written yet. Leave without writing them?"), ctx.confirms.join("|"));
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.equal(state.scannedFolder, OTHER);
    await answer(ctx, replyTo(ctx, "written", () => ({ tags: ["Trips/Coast"], mtime: 1700000000, size: 2048 })));
    assert.equal(writes(ctx).length, 1, "the files not sent were written after the owner left them");

    await openFolder(ctx, FOLDER);
    assert.equal(scans(ctx, FOLDER), 1, "the folder was scanned again rather than shown as this browser kept it");
    assert.deepEqual([...state.folderPhotos[0].tags], ["Trips/Coast"], "the kept record does not hold what was written");
    assert.equal(state.folderPhotos[0].mtime, 1700000000, "the kept record names the file's old stamp: a save is refused");
    assert.deepEqual([...state.folderPhotos[30].tags], [], "a change dropped on leaving is shown as written");
  });

  test("staying keeps the folder and its changes", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    ctx.stay = true;
    await openFolder(ctx, OTHER);
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.equal(state.scannedFolder, FOLDER);
    assert.equal(ctx.document.getElementById("folder-path-input").value, FOLDER);
    await answer(ctx);
    assert.equal(writes(ctx).length, 2, "staying lost the changes not yet sent");
    await answer(ctx);
  });
});

describe("leaving asks about the changes it drops, no others", () => {
  test("back onto the folder whose changes wait asks about an undo made in the view, drops only it, and keeps the folder's own", async (t) => {
    const ctx = await viewableFolder(t);
    await addTags(ctx, "Trips/Coast");
    ctx.document.getElementById("btn-show-in-library").click();
    await ctx.settle(100);
    pendingOf(ctx).files.set("undo", { path: `${FOLDER}\\IMG_0049.JPG`, add: new Set(["Places/Undone"]), remove: new Set(), panel: new Set(), token: "u", actions: new Set(), folder: null });
    ctx.window.history.back();
    await ctx.settle(300);
    assert.ok(ctx.confirms.some((m) => /Tag changes for 1 photo are not written yet/.test(m)), ctx.confirms.join("|"));
    assert.equal(pendingOf(ctx).files.size, 25, "the folder's own changes were dropped with the one asked about");
    assert.ok(!pendingOf(ctx).files.has("undo"));
    await answer(ctx);
    await answer(ctx);
  });
});

describe("a second way out while the photo's question is open", () => {
  test("is not asked about the changes again, and the owner's answer takes the folder first asked about", async (t) => {
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    await typeInOpenPhoto(ctx, "Places/Typed");
    await openFolder(ctx, OTHER);
    await openFolder(ctx, OTHER);
    assert.equal(ctx.confirms.filter((m) => /not written yet/.test(m)).length, 1, "the tag question was asked twice");
    await choose(ctx, "discard");
    await flush(ctx.window, 8);
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.equal(state.scannedFolder, OTHER, "the owner went to the folder and stayed in the one left");
    await answer(ctx);
  });
});
