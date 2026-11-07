/**
 * The photo page's video player takes left/right for itself (#2468): focused, they seek the clip by 5 s,
 * where anywhere else they step to the next photo.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

function press(window, target, key, mods = {}) {
  const event = new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...mods });
  target.dispatchEvent(event);
  return event;
}

/** TagPup on the first of two photos, the player given a clip of 60 s at `time`, loaded or not. */
async function onFirstPhoto(t, { readyState = 4, time = 20 } = {}) {
  const server = new FakeServer()
    .on("/api/apps", { this: "tagpup", apps: {} })
    .on("/api/taxonomy/tree", [])
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/databases", { databases: ["photo_index"] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg" }), photoRecord({ filename: "b.jpg" })])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 });
  const ctx = await loadApp("tagpup", { t, server });
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[0]);
  await flush(ctx.window, 6);
  const video = ctx.document.getElementById("main-video");
  const clip = { readyState, currentTime: time, duration: 60 };
  for (const [name, value] of Object.entries(clip)) {
    Object.defineProperty(video, name, { value, writable: true, configurable: true });
  }
  video.tabIndex = 0;
  ctx.video = video;
  ctx.shown = () => ctx.document.querySelector(".photo-item-file.active")?.getAttribute("data-path");
  return ctx;
}

test("the focused player seeks 5 s and the photo stays", async (t) => {
  const ctx = await onFirstPhoto(t);
  const shown = ctx.shown();
  ctx.video.focus();
  assert.equal(ctx.document.activeElement, ctx.video);
  const right = press(ctx.window, ctx.video, "ArrowRight");
  assert.equal(ctx.video.currentTime, 25);
  assert.ok(right.defaultPrevented);
  press(ctx.window, ctx.video, "ArrowLeft");
  press(ctx.window, ctx.video, "ArrowLeft");
  assert.equal(ctx.video.currentTime, 15);
  ctx.video.currentTime = 2;
  press(ctx.window, ctx.video, "ArrowLeft");
  assert.equal(ctx.video.currentTime, 0, "a seek before the start is held at 0");
  ctx.video.currentTime = 58;
  press(ctx.window, ctx.video, "ArrowRight");
  assert.equal(ctx.video.currentTime, 60, "a seek past the end is held at the end");
  await flush(ctx.window, 6);
  assert.equal(ctx.shown(), shown, "an arrow stepped the photo");
});

test("a player with nothing loaded neither seeks nor steps", async (t) => {
  const ctx = await onFirstPhoto(t, { readyState: 0 });
  const shown = ctx.shown();
  ctx.video.focus();
  press(ctx.window, ctx.video, "ArrowRight");
  await flush(ctx.window, 6);
  assert.equal(ctx.video.currentTime, 20);
  assert.equal(ctx.shown(), shown);
});

test("a text field and the modifiers leave the arrows alone", async (t) => {
  const ctx = await onFirstPhoto(t);
  const shown = ctx.shown();
  ctx.video.focus();
  press(ctx.window, ctx.video, "ArrowRight", { ctrlKey: true });
  press(ctx.window, ctx.video, "ArrowRight", { altKey: true });
  press(ctx.window, ctx.video, "ArrowRight", { metaKey: true });
  const field = ctx.document.getElementById("input-add-tag");
  field.focus();
  press(ctx.window, field, "ArrowRight");
  await flush(ctx.window, 6);
  assert.equal(ctx.video.currentTime, 20);
  assert.equal(ctx.shown(), shown);
});

test("with the player not focused, the arrows step the photos", async (t) => {
  const ctx = await onFirstPhoto(t);
  ctx.document.activeElement.blur();
  press(ctx.window, ctx.document.body, "ArrowRight");
  await flush(ctx.window, 6);
  assert.match(ctx.shown(), /b\.jpg$/);
  assert.equal(ctx.video.currentTime, 20);
});
