/**
 * The photo write queue's status, bottom right (web/tagpup/write-queue.js).
 *
 * Writes to photos go one at a time, and on a network share each takes seconds. Three
 * clicks queued three writes and nothing said whether they were done, waiting or had
 * failed. The status says "Saving 2 of 3..." while the queue works, "1 failed" when a
 * write failed, "All changes saved" when it is done; its list names each write, its
 * state and a failure's reason, and a failed one can be retried. Closing the tab asks
 * while a write is waiting or under way.
 *
 * The server's bulk-tags replies are held here until the test releases them.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

const PHOTOS = [
  photoRecord({ filename: "a.jpg", tags: [], people: [] }),
  photoRecord({ filename: "b.jpg", tags: [], people: [] }),
];

const OFFERED = ["Cross Country", "Harbourside", "Kentridge"];

const REFUSED = "ExifTool: Error: Temporary file already exists: <file>_exiftool_tmp (exit status 1)";

async function loadSelected(t) {
  const held = [];
  const server = new FakeServer()
    .on("/api/tags", OFFERED)
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p, tags: [], people: [] })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photos/bulk-tags", () => new Promise((resolve) => held.push(resolve)))
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
  return { ...ctx, held };
}

const chips = (ctx) => [...ctx.document.querySelectorAll("#selection-suggested-tags-list .suggestion-chip")];
const chip = (ctx, name) => chips(ctx).find((c) => c.textContent.includes(name));
const box = (ctx) => ctx.document.getElementById("write-queue");
const said = (ctx) => box(ctx).querySelector(".write-queue-status").textContent;
const rows = (ctx) => [...box(ctx).querySelectorAll(".write-queue-entry")];

async function answer(ctx, reply) {
  assert.ok(ctx.held.length, "no request was waiting for an answer");
  ctx.held.shift()(reply);
  await flush(ctx.window, 8);
}

async function clickThree(ctx) {
  for (const name of OFFERED) click(ctx.window, chip(ctx, name));
  await flush(ctx.window, 8);
}

function leaving(ctx) {
  const event = new ctx.window.Event("beforeunload", { cancelable: true });
  ctx.window.dispatchEvent(event);
  return event.defaultPrevented;
}

afterEach(() => closeAllApps());

describe("the status", () => {
  test("is hidden until something is written", async (t) => {
    const ctx = await loadSelected(t);
    assert.equal(box(ctx).hidden, true);
  });

  test("counts through a queue of three, and says one failed", async (t) => {
    const ctx = await loadSelected(t);
    await clickThree(ctx);
    assert.equal(box(ctx).hidden, false);
    assert.equal(said(ctx), "Saving 1 of 3...");
    await answer(ctx, { success: true, written: {} });
    assert.equal(said(ctx), "Saving 2 of 3...");
    await answer(ctx, { success: false, error: REFUSED, written: {} });
    assert.equal(said(ctx), "Saving 3 of 3...");
    await answer(ctx, { success: true, written: {} });
    assert.equal(said(ctx), "1 failed");
    assert.ok(box(ctx).classList.contains("write-queue-failed"), "a failure is not marked");
  });

  test("says all is saved when every write went through", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Cross Country"));
    await flush(ctx.window, 8);
    await answer(ctx, { success: true, written: {} });
    assert.equal(said(ctx), "All changes saved");
    assert.ok(box(ctx).classList.contains("write-queue-idle"));
  });
});

describe("the list", () => {
  test("names each write, its state, and a failure's reason", async (t) => {
    const ctx = await loadSelected(t);
    await clickThree(ctx);
    const states = () => rows(ctx).map((r) => r.dataset.status);
    // Newest first.
    assert.deepEqual(states(), ["waiting", "waiting", "writing"]);
    assert.deepEqual(rows(ctx).map((r) => r.querySelector(".write-queue-label").textContent).reverse(),
      OFFERED.map((name) => `Add ${name} to 2 photo(s)`));
    await answer(ctx, { success: true, written: {} });
    await answer(ctx, { success: false, error: REFUSED, written: {} });
    await answer(ctx, { success: true, written: {} });
    assert.deepEqual(states(), ["done", "failed", "done"]);
    const failed = rows(ctx)[1];
    assert.match(failed.querySelector(".write-queue-state").textContent, /Temporary file already exists/);
    assert.ok(failed.querySelector(".write-queue-retry") && failed.querySelector(".write-queue-dismiss"));
    assert.equal(rows(ctx)[0].querySelector(".write-queue-retry"), null, "a done write offers Retry");
  });

  test("Retry sends the failed write again, and Dismiss takes it off", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Harbourside"));
    await flush(ctx.window, 8);
    await answer(ctx, { success: false, error: REFUSED, written: {} });
    const sent = () => ctx.server.calls.filter((c) => c.url.includes("bulk-tags"));
    assert.equal(sent().length, 1);

    click(ctx.window, rows(ctx)[0].querySelector(".write-queue-retry"));
    await flush(ctx.window, 8);
    assert.equal(sent().length, 2, "Retry sent nothing");
    assert.deepEqual(sent()[1].body, sent()[0].body);
    assert.equal(said(ctx), "Saving 1 of 1...");
    await answer(ctx, { success: false, error: REFUSED, written: {} });
    assert.equal(said(ctx), "1 failed", "the retried entry is listed once, not twice");

    click(ctx.window, rows(ctx).find((r) => r.dataset.status === "failed").querySelector(".write-queue-dismiss"));
    await flush(ctx.window, 2);
    assert.equal(said(ctx), "All changes saved");
    assert.equal(rows(ctx).filter((r) => r.dataset.status === "failed").length, 0);
  });

  test("opens on a click, for a touch screen", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Cross Country"));
    await flush(ctx.window, 8);
    click(ctx.window, box(ctx).querySelector(".write-queue-status"));
    assert.ok(box(ctx).classList.contains("open"));
    assert.equal(box(ctx).querySelector(".write-queue-status").getAttribute("aria-expanded"), "true");
    await answer(ctx, { success: true, written: {} });
  });
});

describe("closing the tab", () => {
  test("asks only while a write is waiting or under way", async (t) => {
    const ctx = await loadSelected(t);
    assert.equal(leaving(ctx), false, "asked with nothing to write");
    click(ctx.window, chip(ctx, "Cross Country"));
    await flush(ctx.window, 8);
    assert.equal(leaving(ctx), true, "did not ask while a write was out");
    await answer(ctx, { success: true, written: {} });
    assert.equal(leaving(ctx), false, "still asked once everything was written");
  });
});
