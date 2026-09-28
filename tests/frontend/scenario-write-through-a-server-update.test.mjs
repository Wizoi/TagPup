/**
 * Owner scenario: the always-on process moves onto a new version while a photo write
 * is in flight (docs/ARCHITECTURE.md phase 8; web/common/api.js's fetchThroughAnUpdate,
 * covered alone in server-update.test.mjs). Here the write is a real entry in the
 * page's write queue (web/tagpup/write-queue.js): the queued write must still complete,
 * the queue's indicator must say so, and the queue must not be left stuck for what is
 * clicked next.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

const PHOTOS = [
  photoRecord({ filename: "a.jpg", tags: [], people: [] }),
  photoRecord({ filename: "b.jpg", tags: [], people: [] }),
];

const OFFERED = ["Cross Country", "Harbourside"];

async function loadSelected(t) {
  const server = new FakeServer()
    .on("/api/tags", OFFERED)
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p, tags: [], people: [] })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photos/bulk-tags", { success: true, written: {} })
    .on("/api/folder/suggest-status", {
      status: "completed",
      suggestions: Object.fromEntries(PHOTOS.map((p) => [p.path, {
        people: [], tags: OFFERED.map((tag) => ({ tag, score: 0.9 })),
      }])),
    });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, "D:/Library/2020");
  for (const box of ctx.document.querySelectorAll(".thumbnail-checkbox")) click(ctx.window, box);
  for (let i = 0; i < 50 && chips(ctx).length < OFFERED.length; i++) {
    await new Promise((resolve) => ctx.window.setTimeout(resolve, 20));
  }
  ctx.window.alert = () => {};
  return ctx;
}

const chips = (ctx) => [...ctx.document.querySelectorAll("#selection-suggested-tags-list .suggestion-chip")];
const chip = (ctx, name) => chips(ctx).find((c) => c.textContent.includes(name));
const box = (ctx) => ctx.document.getElementById("write-queue");
const said = (ctx) => box(ctx).querySelector(".write-queue-status").textContent;
const bulkWrites = (ctx) => ctx.bulkTagsCalls;

/**
 * Make `bulk-tags` answer, in order: 503 while the server drains, nothing while it
 * restarts, then a real reply -- exactly what fetchThroughAnUpdate retries through
 * (server-update.test.mjs) -- while every other route still answers through the page's
 * FakeServer. `ctx.bulkTagsCalls` counts every attempt, scripted or not.
 */
function throughAnUpdate(ctx) {
  const scripted = [
    () => ({
      ok: false,
      status: 503,
      headers: { get: (name) => ({ "X-TagPup-Updating": "1", "Retry-After": "0" }[name] ?? null) },
      json: () => Promise.resolve({ success: false, updating: true }),
    }),
    () => Promise.reject(new TypeError("Failed to fetch")),
    () => ({
      ok: true, status: 200, headers: { get: () => null },
      json: () => Promise.resolve({ success: true, written: {} }),
    }),
  ];
  ctx.bulkTagsCalls = [];
  const serverFetch = ctx.window.fetch;
  ctx.window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : String(input && input.url);
    if (url.includes("bulk-tags")) {
      ctx.bulkTagsCalls.push(url);
      if (scripted.length) return Promise.resolve(scripted.shift()());
    }
    return serverFetch(input, init);
  };
}

afterEach(() => closeAllApps());

describe("a write queued just as the server updates", () => {
  test("completes, the queue says so, and the next write is unaffected", async (t) => {
    const ctx = await loadSelected(t);
    throughAnUpdate(ctx);

    click(ctx.window, chip(ctx, "Cross Country"));
    await flush(ctx.window, 4);
    assert.equal(said(ctx), "Saving 1 of 1...", "the queue does not show the write as under way");

    // fetchThroughAnUpdate waits RESTART_RETRY_MS (1s) between the drain and the
    // restart answering; give it real time to run out, not fake ticks.
    for (let i = 0; i < 40 && said(ctx) !== "All changes saved"; i++) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      await flush(ctx.window, 2);
    }

    assert.equal(said(ctx), "All changes saved", "the queue never showed the write as done");
    assert.equal(bulkWrites(ctx).length, 3, "the write did not retry through the 503 and the restart");
    assert.ok(!box(ctx).classList.contains("write-queue-failed"), "the write that outlasted the update is marked failed");

    // What is clicked next is not stuck behind the update: one request, no retry needed.
    click(ctx.window, chip(ctx, "Harbourside"));
    await flush(ctx.window, 8);
    assert.equal(bulkWrites(ctx).length, 4);
    assert.equal(said(ctx), "All changes saved");
  });
});
