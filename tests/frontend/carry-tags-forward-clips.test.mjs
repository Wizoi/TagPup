/**
 * "Same as previous" going through a folder of clips (finding #2832): the button copies the tags of the record before
 * the open one in the list, whatever that record is -- a clip after a clip, a clip after a photo, a photo after a clip.
 * In a library view (a search of videos) the page holds only the open record, so the one before it is read from the library.
 * Fictional names only.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadViewPage, recordOf } from "./view-page.mjs";
import { loadApp, FakeServer, advance, closeAllApps, openFolder, until, PHOTO_WRITES, CLIP_WRITES, SAVE, saved, saveCalls, testClock } from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const FOLDER = "D:\\Pictures\\Lighthouse";
const record = (name, extra = {}) => ({
  path: `${FOLDER}\\${name}`, filename: name, tags: [], people: [], captions: [], title: "", mtime: 1000,
  media: "photo", writes: PHOTO_WRITES, ...extra,
});
const clip = (name, extra = {}) => record(name, { media: "video", id: 40 + name.length, writes: CLIP_WRITES, ...extra });

async function folder(t, scan) {
  const server = new FakeServer()
    .on("/api/folder/scan", scan.map((each) => ({ ...each, tags: [...each.tags] })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on(SAVE, saved())
    .on("/api/folder/suggest-status", { status: "idle" });
  const ctx = await loadApp("tagpup", { server, t });
  ctx.window.alert = () => {};
  ctx.window.HTMLMediaElement.prototype.pause = () => {};
  ctx.window.HTMLMediaElement.prototype.load = () => {};
  await openFolder(ctx, FOLDER);
  await advance(ctx.window, 60);
  ctx.server = server;
  return ctx;
}

const sent = (ctx) => until(ctx, () => saveCalls(ctx.server).length > 0, "the save");
const press = (ctx, key) => ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
const button = (ctx) => ctx.document.getElementById("btn-carry-forward");
const status = (ctx) => ctx.document.getElementById("status-text")?.textContent
  ?? ctx.document.querySelector("#status-bar, .status-bar")?.textContent ?? "";

describe("Same as previous through clips", () => {
  afterEach(() => closeAllApps());

  for (const [name, scan] of [
    ["a clip after a clip", [clip("a.mp4", { tags: ["Beach"] }), clip("b.mp4")]],
    ["a clip after a photo", [record("a.jpg", { tags: ["Beach"] }), clip("b.mp4")]],
    ["a photo after a clip", [clip("a.mp4", { tags: ["Beach"] }), record("b.jpg")]],
  ]) {
    test(`${name}: the button is offered and copies the tags`, async (t) => {
      const ctx = await folder(t, scan);
      press(ctx, "ArrowDown");
      press(ctx, "ArrowDown");
      assert.equal(button(ctx).disabled, false, "offered");
      button(ctx).click();
      await sent(ctx);
      assert.deepEqual(saveCalls(ctx.server).at(-1).body.tags, ["Beach"]);
    });
  }

});

// The same button in a library view -- a Videos view, or any view -- where the page holds only the open photo, so the
// one before it in the view's order is read from the library when the open one is shown.
describe("Same as previous in a library view", () => {
  afterEach(() => closeAllApps());
  const TAGS = { 12: ["Beach"], 13: ["Reef"] };   // 13 is a clip; the rest of the view holds nothing
  const view = (t) => loadViewPage(t, {
    search: "?view=all",
    cardExtra: (id) => ({ media: id === 13 ? "video" : "photo" }),
    onPhoto: (id) => ({ photo: recordOf(id, { tags: TAGS[id] || [], ...(id === 13 ? { media: "video", writes: CLIP_WRITES } : {}) }) }),
  });
  const open = async (ctx, id) => {
    ctx.cardById(id).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(80);
  };
  const carry = (ctx) => ctx.document.getElementById("btn-carry-forward");
  const saves = (ctx) => saveCalls(ctx.server);

  test("a clip after a photo, a photo after a clip: offered, and copies the tags", async (t) => {
    const ctx = await view(t);
    await open(ctx, 13);
    assert.equal(carry(ctx).disabled, false, "the clip has a photo before it");
    carry(ctx).click();
    await ctx.settle(80);
    assert.deepEqual(saves(ctx).at(-1).body.tags, ["Reef", "Beach"], "the photo's tag, added to the clip's own");
    await open(ctx, 14);
    assert.equal(carry(ctx).disabled, false, "the photo has a clip before it");
    carry(ctx).click();
    await ctx.settle(80);
    assert.deepEqual(saves(ctx).at(-1).body.tags, ["Reef"]);
  });

  test("the first photo of the view has none before it", async (t) => {
    const ctx = await view(t);
    await open(ctx, 11);
    assert.equal(carry(ctx).disabled, true);
  });
});
