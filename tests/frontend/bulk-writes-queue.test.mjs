/**
 * Bulk tag writes and Apply All wait their turn in the photo write queue.
 *
 * Three suggestion chips clicked quickly on a selection posted three bulk-tags requests
 * at once. On the server they were three writes of the same files at the same time:
 * each planned from the files as they were before the others, ExifTool refused a file
 * another write had open, and one set of tags was written, one "wrote 0 file(s)", and
 * one lost without a word. Now each goes after the one clicked before it, in the order
 * clicked, and a failed one is reported without costing the rest.
 *
 * The server's bulk-tags replies are held here until the test releases them.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

const FOLDER = "D:/Library/2020";

const PHOTOS = [
  photoRecord({ filename: "a.jpg", tags: [], people: [] }),
  photoRecord({ filename: "b.jpg", tags: [], people: [] }),
];

const OFFERED = ["Cross Country", "Harbourside", "Kentridge"];

const SUGGESTIONS = {
  status: "completed",
  suggestions: Object.fromEntries(PHOTOS.map((p) => [p.path, {
    people: [], tags: OFFERED.map((tag) => ({ tag, score: 0.9 })),
  }])),
};

/** A server whose bulk-tags and auto-apply replies wait until released, oldest first. */
function build() {
  const held = [];
  const hold = () => new Promise((resolve) => held.push(resolve));
  const server = new FakeServer()
    .on("/api/tags", OFFERED)
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    // Copies: the page adds what it wrote to the records it was given.
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p, tags: [...p.tags], people: [...p.people] })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photos/bulk-tags", hold)
    .on("/api/folder/auto-apply", hold)
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/taxonomy/create", { success: true })
    .on("/api/folder/suggest-status", SUGGESTIONS);
  return { server, held };
}

async function loadSelected(t) {
  const { server, held } = build();
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, FOLDER);
  for (const box of ctx.document.querySelectorAll(".thumbnail-checkbox")) click(ctx.window, box);
  await flush(ctx.window, 6);
  // The suggestions arrive from the poller.
  for (let i = 0; i < 50 && chips(ctx).length < OFFERED.length; i++) {
    await new Promise((resolve) => ctx.window.setTimeout(resolve, 20));
  }
  ctx.alerts = [];
  ctx.window.alert = (text) => ctx.alerts.push(String(text));
  ctx.window.confirm = () => true;
  return { ...ctx, held };
}

const writes = (ctx) => ctx.server.calls.filter((c) => /bulk-tags|auto-apply/.test(c.url));

const chips = (ctx) =>
  [...ctx.document.querySelectorAll("#selection-suggested-tags-list .suggestion-chip")];

const chip = (ctx, name) => chips(ctx).find((c) => c.textContent.includes(name));

/** Answer the oldest request waiting, with `reply`. */
async function answer(ctx, reply) {
  assert.ok(ctx.held.length, "no request was waiting for an answer");
  ctx.held.shift()(reply);
  await flush(ctx.window, 8);
}

afterEach(() => closeAllApps());

describe("tags clicked quickly on a selection", () => {
  test("go to the server one at a time, in the order clicked", async (t) => {
    const ctx = await loadSelected(t);
    const [first, second, third] = OFFERED.map((name) => chip(ctx, name));
    assert.ok(first && second && third, "the suggestions were not offered");
    click(ctx.window, first);
    click(ctx.window, second);
    click(ctx.window, third);
    await flush(ctx.window, 8);

    assert.equal(writes(ctx).length, 1, "a second write was sent before the first was answered");
    await answer(ctx, { success: true });
    assert.equal(writes(ctx).length, 2);
    await answer(ctx, { success: true });
    assert.equal(writes(ctx).length, 3);
    await answer(ctx, { success: true });

    assert.deepEqual(writes(ctx).map((c) => c.body.add_tags), OFFERED.map((name) => [name]));
    // The selection's tags show each of the three on both photos.
    const held = [...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")]
      .map((c) => c.textContent);
    for (const name of OFFERED) {
      assert.ok(held.some((c) => c.includes(`${name} (2)`)), `${name} not shown on both: ${held}`);
    }
    assert.deepEqual(ctx.alerts, []);
  });

  test("a failed one is reported, and the ones after it still go", async (t) => {
    const ctx = await loadSelected(t);
    const [first, second, third] = OFFERED.map((name) => chip(ctx, name));
    click(ctx.window, first);
    click(ctx.window, second);
    click(ctx.window, third);
    await flush(ctx.window, 8);

    await answer(ctx, { success: true });
    await answer(ctx, { success: false, error: "ExifTool: Error: Temporary file already exists: <file>_exiftool_tmp" });
    assert.equal(ctx.alerts.length, 1, "the failure was not reported");
    assert.match(ctx.alerts[0], /Temporary file already exists/);
    assert.equal(writes(ctx).length, 3, "the write after the failed one was never sent");
    await answer(ctx, { success: true });
    assert.equal(ctx.held.length, 0);
  });
});

describe("a bulk write that stops part-way", () => {
  test("the photos it wrote show the tag, the rest do not", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Cross Country"));
    await flush(ctx.window, 8);
    await answer(ctx, {
      success: false,
      error: "ExifTool: Error: Temporary file already exists: <file>_exiftool_tmp (exit status 1)",
      written: { [PHOTOS[0].path]: ["Cross Country"] },
    });
    assert.equal(ctx.alerts.length, 1, "the failure was not reported");
    const held = [...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")]
      .map((c) => c.textContent);
    assert.ok(held.some((c) => c.includes("Cross Country (1)")),
      `the photo written is not shown holding it: ${held}`);
  });
});

describe("Ctrl+Z while a later write is out", () => {
  test("takes back only what Apply All added, after it, and the later tag stays", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Cross Country"));
    ctx.document.getElementById("btn-folder-auto-apply").click();
    await flush(ctx.window, 8);
    await answer(ctx, { success: true, written: {} });
    // Apply All added Harbourside to both.
    const both = ["Cross Country", "Harbourside"];
    await answer(ctx, { success: true, written: Object.fromEntries(PHOTOS.map((p) => [p.path, both])) });
    for (let i = 0; i < 50 && !chip(ctx, "Kentridge"); i++) await flush(ctx.window, 2);

    click(ctx.window, chip(ctx, "Kentridge"));
    await flush(ctx.window, 8);
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", {
      key: "z", ctrlKey: true, bubbles: true, cancelable: true,
    }));
    await flush(ctx.window, 8);
    assert.equal(ctx.server.calls.filter((c) => c.url.includes("save-metadata")).length, 0,
      "undo posted whole tag lists");
    assert.equal(writes(ctx).length, 3, "undo was sent while the later write was still out");

    await answer(ctx, { success: true, written: {} });
    assert.equal(writes(ctx).length, 4);
    const undo = writes(ctx).at(-1).body;
    assert.deepEqual(undo.remove_tags, ["Harbourside"]);
    assert.deepEqual(undo.add_tags, []);
    assert.ok(!undo.remove_tags.includes("Kentridge"), "undo took off the later write's tag");
    await answer(ctx, { success: true, written: {} });
  });
});

describe("moving on while a bulk write is out", () => {
  const key = (ctx, name, target) => (target || ctx.document).dispatchEvent(
    new ctx.window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }));
  const openPath = (ctx) => {
    const el = ctx.document.querySelector(".photo-item-file.active");
    return el && el.getAttribute("data-path");
  };

  test("an arrow key moves at once, and a save then lands after it, holding its tag", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Cross Country"));
    await flush(ctx.window, 8);
    key(ctx, "ArrowDown");
    await flush(ctx.window, 8);
    assert.equal(openPath(ctx), PHOTOS[0].path, "the arrow key waited for the bulk write");

    const input = ctx.document.getElementById("input-add-tag");
    input.focus();
    input.value = "Places/Beach";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    assert.equal(ctx.document.getElementById("btn-save-details").disabled, false,
      "Save is held back by another photo's bulk write");
    key(ctx, "Enter", input);
    await flush(ctx.window, 8);
    const saves = () => ctx.server.calls.filter((c) => c.url.includes("save-metadata"));
    assert.equal(saves().length, 0, "the save went beside the bulk write");

    await answer(ctx, { success: true, written: {} });
    for (let i = 0; i < 20 && !saves().length; i++) await flush(ctx.window, 2);
    assert.equal(saves().length, 1);
    assert.deepEqual(saves()[0].body.tags, ["Cross Country", "Places/Beach"]);
  });
});

describe("Apply All after a tag clicked", () => {
  test("waits for the tag's write", async (t) => {
    const ctx = await loadSelected(t);
    click(ctx.window, chip(ctx, "Cross Country"));
    ctx.document.getElementById("btn-folder-auto-apply").click();
    await flush(ctx.window, 8);

    assert.deepEqual(writes(ctx).map((c) => c.url.includes("auto-apply")), [false],
      "Apply All was sent while the tag's write was still out");
    await answer(ctx, { success: true });
    assert.deepEqual(writes(ctx).map((c) => c.url.includes("auto-apply")), [false, true]);
    await answer(ctx, { success: true });
  });
});
