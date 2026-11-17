/**
 * Owner scenario (V22): tagging many photos at once -- a folder of 50 bulk-tagged, a select-all of 1,000 -- while the
 * owner keeps clicking. Each file's tag change waits in the page (web/tagpup/pending.js) and goes 25 files a write, each
 * write one journaled change under its click's group token (docs/plans/one-queue-for-tag-changes.md, cut 3).
 *
 * What the owner lives through: a tag added while the first 25 are out reaches the 25 not yet sent in the same write and
 * the 25 already written in a later one; the last click on a tag wins; a write that fails keeps its files'
 * changes for Retry and never loops by itself; a single save clicked meanwhile goes between two writes; Cancel says how
 * many photos it left unwritten; choosing another library asks while changes are not sent.
 *
 * The server's bulk-tags replies are held here until the test answers them, oldest first.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports } from "./harness.mjs";

const FOLDER = "D:\\Library\\2020";
const pathOf = (i) => `${FOLDER}\\IMG_${String(i).padStart(4, "0")}.JPG`;

function records(n, tags) {
  return Array.from({ length: n }, (_, i) => photoRecord({ filename: `IMG_${String(i).padStart(4, "0")}.JPG`, path: pathOf(i), tags: [...tags] }));
}

/** A folder of `n` photos holding `tags`, all selected; its bulk-tags replies wait for `answer`. */
async function folderOf(t, n, { tags = [], bulk } = {}) {
  const held = [];
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => records(n, tags))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photos/bulk-tags", bulk || (() => new Promise((resolve) => held.push(resolve))))
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/taxonomy/create", { success: true })
    .on("/api/folder/suggest-status", { status: "completed", suggestions: {} });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/alpha/", server });
  await openFolder(ctx, FOLDER);
  ctx.document.getElementById("btn-select-all-thumbnails").click();
  await flush(ctx.window, 4);
  const page = { ...ctx, held, alerts: [], confirms: [], stay: false };   // the closures below read this object
  page.window.alert = (text) => page.alerts.push(String(text));
  page.window.confirm = (text) => { page.confirms.push(String(text)); return !page.stay; };
  return page;
}

const writes = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/photos/bulk-tags")).map((c) => c.body);
const status = (ctx) => ctx.document.getElementById("status-text").textContent;

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
    for (const change of Object.values(second.changes)) assert.deepEqual(change, { add: ["Trips/Coast", "Places/Harbour"], remove: [] });
    assert.equal(second.group, writes(ctx)[0].group, "the files not yet sent left their first click's group");

    await answer(ctx);
    const third = writes(ctx)[2];
    assert.deepEqual(Object.keys(third.changes), first);
    for (const change of Object.values(third.changes)) assert.deepEqual(change, { add: ["Places/Harbour"], remove: [] });
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
    assert.equal(gone, undefined, "a tag not yet written is shown as held");
    const { removeTagFromAllSelected } = pageExports(ctx.window, "web/tagpup/selection.js");
    removeTagFromAllSelected("Trips/Gone", false);
    await flush(ctx.window, 4);

    await answer(ctx);
    // The 5 files never sent carry Trips/Kept and the Places/Coast that came off and back as an add (never a removal);
    // Trips/Gone, added and taken off, is a removal (the last click wins), never an add.
    const second = writes(ctx)[1].changes;
    assert.equal(Object.keys(second).length, 5);
    for (const change of Object.values(second)) assert.deepEqual(change, { add: ["Trips/Kept", "Places/Coast"], remove: ["Trips/Gone"] });
    await answer(ctx);
    const third = writes(ctx)[2].changes;
    assert.equal(Object.keys(third).length, 25);
    for (const change of Object.values(third)) assert.deepEqual(change, { add: ["Places/Coast"], remove: ["Trips/Gone"] });
    await answer(ctx);
    assert.equal(writes(ctx).length, 3);
    assert.ok(!JSON.stringify(writes(ctx).map((w) => Object.values(w.changes).map((c) => c.add))).includes("Trips/Gone"), "a tag taken off before it was sent was still added");
  });
});

describe("a record older than its file", () => {
  test("an add the record says the file holds is still sent, and so is a removal of a tag the record lacks", async (t) => {
    const ctx = await folderOf(t, 3, { tags: ["Places/Coast"], bulk: () => ({ success: true }) });
    await addTags(ctx, "Places/Coast");
    assert.deepEqual(Object.values(writes(ctx)[0].changes)[0], { add: ["Places/Coast"], remove: [] });
    const { removeTagFromAllSelected } = pageExports(ctx.window, "web/tagpup/selection.js");
    removeTagFromAllSelected("Trips/Elsewhere", false);
    await flush(ctx.window, 8);
    assert.equal(writes(ctx).length, 2, "a removal the record thought done was not sent");
    assert.deepEqual(Object.values(writes(ctx)[1].changes)[0], { add: [], remove: ["Trips/Elsewhere"] });
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
    const ctx = await folderOf(t, 50);
    await addTags(ctx, "Trips/Coast");
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);
    const input = ctx.document.getElementById("input-add-tag");
    input.focus();
    input.value = "Places/Beach";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);

    await answer(ctx);
    for (let i = 0; i < 20 && ctx.server.calls.filter((c) => c.url.includes("bulk-tags")).length < 2; i++) await flush(ctx.window, 2);
    const order = ctx.server.calls.filter((c) => /bulk-tags|save-metadata/.test(c.url)).map((c) => (c.url.includes("bulk") ? "bulk" : "save"));
    assert.deepEqual(order, ["bulk", "save", "bulk"]);
    const save = ctx.server.calls.find((c) => c.url.includes("save-metadata")).body;
    assert.deepEqual(save.tags, ["Trips/Coast", "Places/Beach"]);
    await answer(ctx);
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
    const label = ctx.document.querySelector(".write-queue-entry .write-queue-label").textContent;
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
  });
});

describe("the record's own spelling of its path", () => {
  test("is the key the change is sent under, and a reply in another spelling still reaches the record", async (t) => {
    const ctx = await folderOf(t, 1);
    await addTags(ctx, "Trips/Coast");
    assert.deepEqual(Object.keys(writes(ctx)[0].changes), [pathOf(0)]);
    await answer(ctx, { success: true, written: { [pathOf(0).toLowerCase()]: ["Places/Held", "Trips/Coast"] } });
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
});
