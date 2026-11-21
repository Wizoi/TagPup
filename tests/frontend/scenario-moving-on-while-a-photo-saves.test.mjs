/**
 * V27: somebody moves on from a photo whose save is still out (owner, 2026-11-19: "the status in the top right is too far
 * away and it just seems like the app is non-responsive, causing multiple clicks to then queue up by the user skipping past
 * photos once the save completes"). A move held for the photo's write says so over the photo once the wait is longer than a
 * moment; one move waits, the last asked, so presses made meanwhile move once; the note clears itself when the write ends and
 * the move goes; a save that failed with its text still in the panel asks, as it always has, and does not move; Escape or
 * Stay here lets the waiting move go; a write answered at once shows nothing.
 *
 * Every save is held until the test answers it, as the route answers it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, PEOPLE_ROUTE, peopleRecordsOf,
  SAVE, saved, saveCalls,
  notSaved,
} from "./harness.mjs";

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "Cross Country", name: "Cross Country", parent_id: null, has_face: 0 },
  { id: 3, tag: "Harbourside", name: "Harbourside", parent_id: null, has_face: 0 },
];
const TAGS = ["Cross Country", "Harbourside"];
const PHOTOS = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"].map((filename) => photoRecord({ filename }));
const [A, B] = PHOTOS;
// Longer than the moment a save may take before the page says the move waits for it.
const PAST_THE_MOMENT = 450;

async function open(t) {
  const held = [];
  const server = new FakeServer()
    .on("/api/tags", TAGS)
    .first(PEOPLE_ROUTE, peopleRecordsOf(TAXONOMY))
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", PHOTOS.map((p) => ({ ...p, tags: TAGS.slice() })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on(SAVE, (url, body) => new Promise((resolve) => held.push((reply) => resolve(reply(url, body)))));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.console.error = () => {};
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  const { window, document } = ctx;
  ctx.wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  ctx.select = async (photo) => {
    document.querySelector(`li[data-path="${photo.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(window, 8);
  };
  ctx.active = () => {
    const row = document.querySelector(".photo-item-file.active");
    return row && row.getAttribute("data-path");
  };
  ctx.key = async (key, target = document.body) => {
    target.dispatchEvent(new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    await flush(window, 4);
  };
  // The save of A under way: its title saved (its write is A's, queued at the click). A pill's removal is no save of
  // the photo's: it waits with the page's tag changes (pending.js), which a move does not wait for (V20, V22).
  ctx.startSave = async () => {
    document.getElementById("input-photo-title").value = "Harbour run";
    document.getElementById("btn-save-title").click();
    await flush(window, 4);
    document.activeElement && document.activeElement.blur();
    assert.equal(held.length, 1, "the save was not sent");
  };
  let answered = 0;
  ctx.answer = async (error = null) => {
    const body = saveCalls(server)[answered].body;
    answered += 1;
    held.shift()(error ? notSaved(error) : saved({ base: { tags: (body.tags || []).slice(), title: body.title || "" } }));
    await flush(window, 12);
  };
  ctx.note = () => document.querySelector(".move-waiting");
  ctx.unanswered = () => held.length;
  await ctx.select(A);
  return ctx;
}

afterEach(() => closeAllApps());

describe("V27: moving on from a photo while its save is out", () => {
  test("the move waits for the save, says so over the photo, and goes once the save is written", async (t) => {
    const ctx = await open(t);
    await ctx.startSave();
    const focused = ctx.document.activeElement;
    await ctx.key("ArrowDown");
    assert.equal(ctx.active(), A.path, "left the photo before its save was written");
    await ctx.wait(PAST_THE_MOMENT);
    const note = ctx.note();
    assert.ok(note, "nothing over the photo says the move waits for the save");
    assert.match(note.textContent, /Saving a\.jpg/);
    assert.ok(ctx.document.getElementById("image-viewer").contains(note), "the note is not over the photo");
    assert.equal(ctx.document.activeElement, focused, "the note took the focus");
    await ctx.answer();
    assert.equal(ctx.note(), null, "the note stayed after the save was written");
    assert.equal(ctx.active(), B.path, "the move did not go once the save was written");
  });

  test("presses made while the move waits move once, not once each", async (t) => {
    const ctx = await open(t);
    await ctx.startSave();
    await ctx.key("ArrowDown");
    await ctx.wait(PAST_THE_MOMENT);
    await ctx.key("ArrowDown");
    await ctx.key("ArrowRight");
    await ctx.key("ArrowDown");
    const notes = ctx.document.querySelectorAll(".move-waiting").length;
    await ctx.answer();
    await ctx.wait(60);
    assert.equal(ctx.active(), B.path, "the presses made while it waited each moved on, skipping photos");
    assert.equal(notes, 1, "not one note while the move waited");
  });

  test("a save that fails with its text still in the panel asks, as before, and does not move", async (t) => {
    const ctx = await open(t);
    const title = ctx.document.getElementById("input-photo-title");
    title.value = "Sports day";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    ctx.document.getElementById("btn-save-details").click();
    await flush(ctx.window, 4);
    title.blur();
    assert.equal(ctx.unanswered(), 1, "the save was not sent");
    await ctx.key("ArrowDown");
    await ctx.wait(PAST_THE_MOMENT);
    assert.ok(ctx.note(), "nothing says the move waits for the save");
    await ctx.answer("The file is locked.");
    assert.equal(ctx.note(), null, "the note stayed after the save failed");
    assert.ok(ctx.document.querySelector(".unsaved-edits-modal.active"), "did not ask about the text the save failed to write");
    assert.equal(ctx.active(), A.path, "moved on over a save that failed");
  });

  test("Escape lets the waiting move go: the save goes on, the photo stays", async (t) => {
    const ctx = await open(t);
    await ctx.startSave();
    await ctx.key("ArrowDown");
    await ctx.wait(PAST_THE_MOMENT);
    assert.ok(ctx.note());
    await ctx.key("Escape");
    assert.equal(ctx.note(), null, "Escape left the note");
    await ctx.answer();
    assert.equal(ctx.active(), A.path, "moved on after Escape let the move go");
    await ctx.key("ArrowDown");
    assert.equal(ctx.active(), B.path, "the next press after the save did not move");
  });

  test("Stay here lets the waiting move go, and a move asked after it waits on its own", async (t) => {
    const ctx = await open(t);
    await ctx.startSave();
    await ctx.key("ArrowDown");
    await ctx.wait(PAST_THE_MOMENT);
    ctx.note().querySelector("button").click();
    await flush(ctx.window, 4);
    assert.equal(ctx.note(), null);
    await ctx.key("ArrowDown");
    await ctx.answer();
    await ctx.wait(60);
    assert.equal(ctx.active(), B.path, "the move asked after Stay here did not go, or went twice");
  });

  test("a save answered at once shows nothing over the photo", async (t) => {
    const ctx = await open(t);
    await ctx.startSave();
    await ctx.key("ArrowDown");
    let seen = false;
    const watch = new ctx.window.MutationObserver(() => { seen = seen || Boolean(ctx.note()); });
    watch.observe(ctx.document.body, { childList: true, subtree: true });
    await ctx.answer();
    await ctx.wait(PAST_THE_MOMENT);
    watch.disconnect();
    assert.equal(seen, false, "a save answered at once flashed the note");
    assert.equal(ctx.active(), B.path);
  });

  test("after Save in the question, a slow save shows the note, and Escape lets the move go (#3181)", async (t) => {
    const ctx = await open(t);
    const title = ctx.document.getElementById("input-photo-title");
    title.value = "Sports day";
    title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    title.blur();
    await ctx.key("ArrowDown");
    ctx.document.querySelector('.unsaved-edits-modal.active [data-choice="save"]').click();
    await flush(ctx.window, 6);
    assert.equal(ctx.unanswered(), 1, "the save was not sent");
    await ctx.wait(PAST_THE_MOMENT);
    assert.ok(ctx.note(), "no note while the move waits for the save chosen in the question");
    await ctx.key("Escape");
    assert.equal(ctx.note(), null);
    await ctx.answer();
    await ctx.wait(60);
    assert.equal(ctx.active(), A.path, "the move went after it was let go");
  });

  test("Escape pressed in a field does not let the waiting move go (#3182)", async (t) => {
    const ctx = await open(t);
    await ctx.startSave();
    await ctx.key("ArrowDown");
    await ctx.wait(PAST_THE_MOMENT);
    assert.ok(ctx.note());
    const field = ctx.document.getElementById("input-add-tag");
    field.focus();
    await ctx.key("Escape", field);
    assert.ok(ctx.note(), "Escape meant for the field let the move go");
    await ctx.answer();
    await ctx.wait(60);
    assert.equal(ctx.active(), B.path);
  });
});
