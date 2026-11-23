/**
 * Undo sets the page's tags to what the reply says each file holds (findings #390), not by hand
 * (kept + removed): where the server resolved a bare person name, the page's record differed
 * from the file until the next scan.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports, TAGS, tagFiles } from "./harness.mjs";

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

function build() {
  const held = [];
  const hold = () => new Promise((resolve) => held.push(resolve));
  const files = {};   // what each file holds, as the replies said they wrote it: a rescan reads the files
  const server = new FakeServer()
    .on("/api/tags", OFFERED)
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p, tags: [...(files[p.path] || p.tags)], people: [...p.people] })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on(TAGS, (url, body) => new Promise((resolve) => held.push((reply) => resolve(reply(body)))))
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

/** Answer the request waiting longest: an Apply All with `reply`, its list of tags for each file it wrote; a tag change
 * with `reply` a function of its body (harness tagFiles reply). */
async function answer(ctx, reply) {
  assert.ok(ctx.held.length, "no request was waiting for an answer");
  for (const [path, held] of Object.entries(reply.written || {})) ctx.server.files[path] = held;
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

describe("undo", () => {
  test("sets each photo's tags to what the reply says its file holds", async (t) => {
    const ctx = await loadSelected(t);
    applyAll(ctx).click();
    await flush(ctx.window, 8);
    await answer(ctx, { success: true, written: Object.fromEntries(PHOTOS.map((p) => [p.path, ["Harbourside"]])) });
    ctrlZ(ctx);
    await flush(ctx.window, 10);
    // Another program put Places/Coast in each file since: taking Harbourside off leaves it there, which the page
    // would not have by hand (kept + removed). The file holds what the reply says.
    const onDisk = tagFiles(PHOTOS.map((p) => ({ ...p, tags: ["Harbourside", "Places/Coast"] })));
    await answer(ctx, (body) => {
      const reply = onDisk.reply(body);
      for (const p of PHOTOS) ctx.server.files[p.path] = onDisk.holds(p.path);
      return reply;
    });
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    for (const photo of state.folderPhotos) {
      assert.deepEqual(photo.tags, ["Places/Coast"], `the page's record is not of the file: ${photo.tags}`);
    }
  });
});
