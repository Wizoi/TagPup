/**
 * Suggest's Cancel, and what a waiting Suggest says (docs/findings.md, #750, #776).
 *
 * A Suggest that waits -- for its folder's index, or for the graphics card another program
 * has -- says so on its progress bar, from the status's `message`, and its Cancel stops it
 * (/api/folder/suggest-cancel). The button is disabled while a cancel is on its way and
 * enabled again when the server says there was nothing to cancel; a "cancelled" status
 * hides the bar, stops the polling and says so; an "error" status says the server's sentence
 * in the status line. Driven through the real page against a scripted server; the status poll is
 * the page's own (every 1.5 s).
 */
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { advance, loadApp, FakeServer, photoRecord, flush, click, openFolder, testClock } from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const FOLDER = "D:\\Library\\Regatta";
const WAITING = "Waiting for the graphics card (in use by: indexing Regatta (harbour), since 15:41)...";
const POLL_MS = 1600;

/** A page on a folder whose Suggest status replies run through `sequence`, the last kept. */
async function pageWithStatus(t, sequence, cancelReply = { success: true, cancelled: true }, indexStatus = { status: "idle" }) {
  const replies = [...sequence];
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/scan", [photoRecord({ filename: "start.jpg" })])
    .on("/api/folder/suggest-cancel", cancelReply)
    .on("/api/folder/index-status", () => indexStatus)
    // The library's queue, as tagpup/jobs/indexing.py IndexQueue.active gives it: the folder, while its index runs.
    .on("/api/folder/index-active", () => (indexStatus.status === "running"
      ? { active: [{ folder: FOLDER, name: "Regatta", percent: indexStatus.percent, message: indexStatus.message }], queued: [],
        busy: true, remaining: 1 }
      : { active: [], queued: [], busy: false, remaining: 0 }))
    .on("/api/folder/suggest-status", () => (replies.length > 1 ? replies.shift() : replies[0]));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/?path=" + encodeURIComponent(FOLDER), server });
  await openFolder(ctx, FOLDER, { settle: 6 });
  return ctx;
}

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

describe("one bar for the work of a folder (#2355)", () => {
  test("a Suggest waiting for the index shows the index's progress in its own bar, and the index's bar stays hidden", async (t) => {
    const ctx = await pageWithStatus(t, [{ status: "preparing", completed: 0, total: 3, message: "Waiting for this folder to be indexed first" }],
      undefined, { status: "running", percent: 40, message: "Generating embeddings: 40% (2/5)" });
    await advance(ctx.window, POLL_MS);
    await flush(ctx.window, 6);
    assert.equal(el(ctx, "suggest-progress-bar").style.width, "40%");
    assert.ok(el(ctx, "index-progress-container").classList.contains("hidden"), "two bars showed");
  });
});

describe("a Suggest that is refused", () => {
  test("says the server's sentence in the status line, and stops polling", async (t) => {
    const refused = "Suggest Regatta (harbour) is using the CLIP model ViT-H-14 (laion2b_s32b_b79k); cove names "
      + "ViT-L-14 (laion2b_s32b_b79k), and TagPup holds one set of models at a time. Nothing was suggested: "
      + "Suggest again once it has ended.";
    const ctx = await pageWithStatus(t, [{ status: "error", completed: 0, total: 3, message: refused }]);
    assert.equal(el(ctx, "status-text").textContent, refused);
    assert.ok(el(ctx, "suggest-progress-container").classList.contains("hidden"));
    const polled = ctx.server.calls.filter((c) => c.url.includes("suggest-status")).length;
    await advance(ctx.window, POLL_MS);
    await flush(ctx.window, 4);
    assert.equal(ctx.server.calls.filter((c) => c.url.includes("suggest-status")).length, polled,
      "the page kept polling a run that failed");
    assert.equal(el(ctx, "status-text").textContent, refused, "the sentence did not stay");
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

    await advance(ctx.window, POLL_MS);
    await flush(ctx.window, 4);
    assert.equal(button.disabled, true, "the poll saying Cancelling enabled it again");
    assert.equal(el(ctx, "suggest-progress-text").textContent, "Cancelling...");

    await advance(ctx.window, POLL_MS);
    await flush(ctx.window, 4);
    assert.ok(el(ctx, "suggest-progress-container").classList.contains("hidden"), "the bar stayed after the cancel");
    assert.match(el(ctx, "status-text").textContent, /cancelled/i);
    const polled = ctx.server.calls.filter((c) => c.url.includes("suggest-status")).length;
    await advance(ctx.window, POLL_MS);
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
