/**
 * Suggest's Cancel, and what a waiting Suggest says (docs/findings.md, #750, #776).
 *
 * A Suggest that waits -- for its folder's index, or for the graphics card another program
 * has -- says so on its progress bar, from the status's `message`, and its Cancel stops it
 * (/api/folder/suggest-cancel). The button is disabled while a cancel is on its way and
 * enabled again when the server says there was nothing to cancel; a "cancelled" status
 * hides the bar, stops the polling and says so. Driven through the real page against a
 * scripted server; the status poll is the page's own (every 1.5 s).
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder } from "./harness.mjs";

const FOLDER = "D:\\Library\\Regatta";
const WAITING = "Waiting for the graphics card (in use by: indexing Regatta (harbour), since 15:41)...";
const POLL_MS = 1600;

/** A page on a folder whose Suggest status replies run through `sequence`, the last kept. */
async function pageWithStatus(t, sequence, cancelReply = { success: true, cancelled: true }) {
  const replies = [...sequence];
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/scan", [photoRecord({ filename: "start.jpg" })])
    .on("/api/folder/suggest-cancel", cancelReply)
    .on("/api/folder/suggest-status", () => (replies.length > 1 ? replies.shift() : replies[0]));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, FOLDER, { settle: 6 });
  return ctx;
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const el = (ctx, id) => ctx.document.getElementById(id);

describe("a Suggest that waits", () => {
  test("says what it waits for while preparing, and its Cancel is enabled", async (t) => {
    const ctx = await pageWithStatus(t, [{ status: "preparing", completed: 0, total: 3, message: WAITING }]);
    assert.equal(el(ctx, "suggest-progress-text").textContent, WAITING);
    assert.ok(!el(ctx, "suggest-progress-container").classList.contains("hidden"));
    assert.equal(el(ctx, "btn-suggest-cancel").disabled, false);
  });

  test("says it under way too, beside how far it has got", async (t) => {
    const ctx = await pageWithStatus(t, [{ status: "running", completed: 1, total: 4, message: WAITING }]);
    assert.equal(el(ctx, "suggest-progress-text").textContent, `1 / 4: ${WAITING}`);
  });

  test("waits for its folder's index, saying so", async (t) => {
    const line = "Waiting for this folder to be indexed first: Generating embeddings: 33% (1/3)";
    const ctx = await pageWithStatus(t, [{ status: "preparing", completed: 0, total: 3, message: line }]);
    assert.equal(el(ctx, "suggest-progress-text").textContent, line);
  });
});

describe("Suggest's Cancel", () => {
  test("asks the server for the folder, says Cancelling, and stays disabled until it is cancelled", async (t) => {
    const ctx = await pageWithStatus(t, [
      { status: "preparing", completed: 0, total: 3, message: WAITING },
      { status: "preparing", completed: 0, total: 3, message: "Cancelling..." },
      { status: "cancelled", completed: 0, total: 3, message: "Cancelled. What was suggested before is kept." },
    ]);
    const button = el(ctx, "btn-suggest-cancel");
    click(ctx.window, button);
    assert.equal(button.disabled, true, "a second click could ask again");
    await flush(ctx.window, 4);
    assert.deepEqual(ctx.server.lastBody("/api/folder/suggest-cancel"), { folder_path: FOLDER });
    assert.equal(el(ctx, "suggest-progress-text").textContent, "Cancelling...");
    assert.equal(button.disabled, true);

    await wait(POLL_MS);
    await flush(ctx.window, 4);
    assert.equal(button.disabled, true, "the poll saying Cancelling enabled it again");
    assert.equal(el(ctx, "suggest-progress-text").textContent, "Cancelling...");

    await wait(POLL_MS);
    await flush(ctx.window, 4);
    assert.ok(el(ctx, "suggest-progress-container").classList.contains("hidden"), "the bar stayed after the cancel");
    assert.match(el(ctx, "status-text").textContent, /cancelled/i);
    const polled = ctx.server.calls.filter((c) => c.url.includes("suggest-status")).length;
    await wait(POLL_MS);
    await flush(ctx.window, 4);
    assert.equal(ctx.server.calls.filter((c) => c.url.includes("suggest-status")).length, polled,
      "the page kept polling a cancelled run");
  });

  test("is enabled again when there was nothing to cancel", async (t) => {
    const ctx = await pageWithStatus(t, [{ status: "running", completed: 1, total: 4 }],
      { success: true, cancelled: false });
    const button = el(ctx, "btn-suggest-cancel");
    click(ctx.window, button);
    await flush(ctx.window, 4);
    assert.equal(button.disabled, false);
    assert.notEqual(el(ctx, "suggest-progress-text").textContent, "Cancelling...");
  });
});
