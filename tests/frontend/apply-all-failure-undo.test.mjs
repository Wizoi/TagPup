/**
 * Undo is of what the server says it wrote, never of what was attempted (findings #391).
 *
 * An Apply All that failed part-way recorded no undo although its reply named the photos it
 * had written: Ctrl+Z then took back the operation before it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, TAGS, tagFiles } from "./harness.mjs";

const FOLDER = "D:/Library/2020";

const PHOTOS = [
  photoRecord({ filename: "a.jpg", tags: [], people: [] }),
  photoRecord({ filename: "b.jpg", tags: [], people: [] }),
];

const OFFERED = ["Cross Country", "Harbourside"];

const SUGGESTIONS = {
  status: "completed",
  suggestions: Object.fromEntries(PHOTOS.map((p) => [p.path, {
    people: [], tags: OFFERED.map((tag) => ({ tag, score: 0.9 })),
  }])),
};

/**
 * A tag change (an undo) answered as the real entry point does (harness tagFiles), from what the files hold as the
 * writes before it left them, and the files left as it wrote them.
 */
function fromFiles(files, body) {
  const now = tagFiles(PHOTOS.map((p) => ({ ...p, tags: files[p.path] || p.tags })));
  const reply = now.reply(body);
  for (const p of PHOTOS) files[p.path] = now.holds(p.path);
  return reply;
}

function build() {
  const held = [];
  const hold = () => new Promise((resolve) => held.push(resolve));
  // What each file holds, as the replies said they wrote it: a rescan reads the files, so this scan answers with what
  // the writes put in them.
  const files = {};
  const server = new FakeServer()
    .on("/api/tags", OFFERED)
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p, tags: [...(files[p.path] || p.tags)], people: [...p.people] })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on(TAGS, (url, body) => new Promise((resolve) => held.push(() => resolve(fromFiles(files, body)))))
    .on("/api/folder/auto-apply", hold)
    .on("/api/folder/suggest-status", SUGGESTIONS);
  server.files = files;
  return { server, held };
}

async function loadSelected(t) {
  const { server, held } = build();
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, FOLDER);
  for (const box of ctx.document.querySelectorAll(".thumbnail-checkbox")) click(ctx.window, box);
  await flush(ctx.window, 6);
  for (let i = 0; i < 50 && applyAll(ctx).disabled; i++) {
    await new Promise((resolve) => ctx.window.setTimeout(resolve, 20));
  }
  ctx.alerts = [];
  ctx.window.alert = (text) => ctx.alerts.push(String(text));
  ctx.window.confirm = () => true;
  return { ...ctx, held };
}

const applyAll = (ctx) => ctx.document.getElementById("btn-folder-auto-apply");
const bulk = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/photos/write"));
const pathsOf = (body) => Object.keys(body.changes);

/** Answer the request waiting longest: an Apply All with `reply`, a tag change from the files (fromFiles). */
async function answer(ctx, reply = {}) {
  assert.ok(ctx.held.length, "no request was waiting for an answer");
  Object.assign(ctx.server.files, reply.written || {});
  ctx.held.shift()(reply);
  await flush(ctx.window, 10);
}

async function reselect(ctx) {
  for (let i = 0; i < 50 && ctx.document.querySelectorAll(".thumbnail-checkbox").length < 2; i++) {
    await flush(ctx.window, 2);
  }
  for (const box of ctx.document.querySelectorAll(".thumbnail-checkbox")) {
    if (!box.checked) click(ctx.window, box);
  }
  for (let i = 0; i < 50 && applyAll(ctx).disabled; i++) {
    await new Promise((resolve) => ctx.window.setTimeout(resolve, 20));
  }
}

function ctrlZ(ctx) {
  ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", {
    key: "z", ctrlKey: true, bubbles: true, cancelable: true,
  }));
}

afterEach(() => closeAllApps());

describe("a Retry of an Apply All that failed part-way", () => {
  test("keeps the way back to the photo the first attempt wrote (#879)", async (t) => {
    const ctx = await loadSelected(t);
    const [first, second] = PHOTOS;
    // The first attempt wrote the first photo; the rescan after it shows the file as written.
    applyAll(ctx).click();
    await flush(ctx.window, 8);
    await answer(ctx, {
      success: false, error: "The share went away.", written: { [first.path]: ["Cross Country"] },
    });
    await flush(ctx.window, 10);
    const retry = ctx.document.querySelector(".write-queue-retry");
    assert.ok(retry, "no Retry offered");

    click(ctx.window, retry);
    await flush(ctx.window, 10);
    await answer(ctx, {
      success: true,
      written: { [first.path]: ["Cross Country"], [second.path]: ["Cross Country"] },
    });
    await flush(ctx.window, 10);

    ctrlZ(ctx);
    await flush(ctx.window, 10);
    const reached = bulk(ctx).flatMap((call) => pathsOf(call.body)).sort();
    assert.deepEqual(reached, [first.path, second.path].sort(),
      "Ctrl+Z after the retry did not take back the photo the first attempt wrote");
    await answer(ctx);
  });
});

describe("an Apply All that fails part-way", () => {
  test("is what Ctrl+Z takes back, for the photos its reply says it wrote", async (t) => {
    const ctx = await loadSelected(t);
    // An Apply All that worked: both photos took Harbourside.
    applyAll(ctx).click();
    await flush(ctx.window, 8);
    await answer(ctx, { success: true, written: Object.fromEntries(PHOTOS.map((p) => [p.path, ["Harbourside"]])) });
    await reselect(ctx);

    // The next one wrote the first photo, then stopped.
    applyAll(ctx).click();
    await flush(ctx.window, 8);
    await answer(ctx, {
      success: false, error: "ExifTool: could not write the second photo",
      written: { [PHOTOS[0].path]: ["Cross Country", "Harbourside"] },
    });
    assert.equal(ctx.alerts.length, 1, "the failure was not reported");

    ctrlZ(ctx);
    await flush(ctx.window, 10);
    assert.equal(bulk(ctx).length, 1, "Ctrl+Z sent nothing: no undo was recorded");
    const undo = bulk(ctx)[0].body;
    assert.deepEqual(pathsOf(undo), [PHOTOS[0].path], "undo reached a photo the failed Apply All never wrote");
    const removed = undo.changes[PHOTOS[0].path].remove;
    assert.ok(removed.includes("Cross Country"), `undo did not take back the tag written: ${removed}`);
    await answer(ctx);
  });

  test("that wrote nothing leaves the operation before it to Ctrl+Z", async (t) => {
    const ctx = await loadSelected(t);
    applyAll(ctx).click();
    await flush(ctx.window, 8);
    await answer(ctx, { success: true, written: Object.fromEntries(PHOTOS.map((p) => [p.path, ["Harbourside"]])) });
    await reselect(ctx);

    applyAll(ctx).click();
    await flush(ctx.window, 8);
    await answer(ctx, { success: false, error: "The share went away.", written: {} });

    ctrlZ(ctx);
    await flush(ctx.window, 10);
    assert.equal(bulk(ctx).length, 1, "the earlier operation was not left to undo");
    assert.deepEqual(pathsOf(bulk(ctx)[0].body).sort(), PHOTOS.map((p) => p.path).sort());
    await answer(ctx);
  });
});
