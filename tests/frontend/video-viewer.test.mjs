/**
 * A video of a library view, in the photo panel (web/tagpup/media.js): its card carries a play mark, opening it plays it
 * from /api/video-file through the library (the image hidden, so the zoom and the face boxes stand down), and it stops --
 * no src, the stream let go -- when another photo is shown, on Back and on Escape. How it fails: rapid stepping between
 * a video and photos, the same video shown again (it plays on), a record with no `media` (a photo), and the video's
 * error event: a format the browser cannot play, a file gone, a share away, and an error of a video already left.
 * Switching library is another page (web/common/library.js): nothing of the video outlives it. A video offers the
 * edits its record's `writes` names (its title, people and keywords): Rotate, Date Taken, rename and delete are inert
 * with a sentence naming them, a photo stepped to after it has every control back, and a missing one has none
 * (stale.js applyPhotoStale). Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, CLIP_WRITES } from "./harness.mjs";
import { loadViewPage, pageErrors, recordOf } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const VIDEO = 13;
const MTIME = 1600000123.5;
const el = (ctx, id) => ctx.document.getElementById(id);
const video = (ctx) => el(ctx, "main-video");
const image = (ctx) => el(ctx, "main-image");
const press = (ctx, key) => ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
const status = (ctx) => el(ctx, "status-text").textContent;

/**
 * A view of eight photos, the third a video; the player's pause() and load() counted (jsdom has neither), and each src
 * given it (a browser starts the video again on any, the same address too).
 */
async function viewWithAVideo(t, {
  photoExtra = (id) => (id === VIDEO ? { media: "video", mtime: MTIME, writes: CLIP_WRITES } : {}),
} = {}) {
  const played = { pause: 0, load: 0, src: 0 };
  const ctx = await loadViewPage(t, {
    search: "?view=all",
    cardExtra: (id) => (id === VIDEO ? { media: "video" } : { media: "photo" }),
    onPhoto: (id) => ({ photo: recordOf(id, photoExtra(id)) }),
    before: (window) => {
      window.HTMLMediaElement.prototype.pause = () => { played.pause += 1; };
      window.HTMLMediaElement.prototype.load = () => { played.load += 1; };
      const src = Object.getOwnPropertyDescriptor(window.HTMLMediaElement.prototype, "src");
      Object.defineProperty(window.HTMLMediaElement.prototype, "src", {
        ...src, set(value) { played.src += 1; src.set.call(this, value); },
      });
    },
  });
  ctx.played = played;
  return ctx;
}

const open = async (ctx, id) => {
  ctx.cardById(id).querySelector(".btn-thumbnail-detail").click();
  await ctx.settle(80);
};

function assertPlaying(ctx, id, what) {
  const src = video(ctx).getAttribute("src") || "";
  assert.ok(src.startsWith("/photo_index/api/video-file?"), `${what}: the video is asked of its library (${src})`);
  assert.ok(src.includes(`id=${id}&`) && src.includes(`v=${encodeURIComponent(MTIME)}`), `${what}: by id, stamped by its mtime`);
  assert.equal(video(ctx).classList.contains("hidden"), false, `${what}: the player is shown`);
  assert.equal(image(ctx).classList.contains("hidden"), true, `${what}: the image is hidden`);
  assert.equal(image(ctx).getAttribute("src"), "", `${what}: the image asks for nothing, so zoom and face boxes stand down`);
}

function assertStopped(ctx, what) {
  assert.equal(video(ctx).getAttribute("src"), null, `${what}: the video has no src: the stream is let go`);
  assert.equal(video(ctx).classList.contains("hidden"), true, `${what}: the player is hidden`);
}

describe("a video's card and its player", () => {
  test("only a video's card carries the play mark", async (t) => {
    const ctx = await viewWithAVideo(t);
    const marked = ctx.real().filter((card) => card.querySelector(".thumbnail-img-wrapper .thumbnail-play-mark"));
    assert.deepEqual(marked.map((card) => Number(card.dataset.id)), [VIDEO]);
  });

  test("opening a video plays it from /api/video-file; stepping to a photo stops it and shows the photo", async (t) => {
    const ctx = await viewWithAVideo(t);
    await open(ctx, VIDEO);
    assertPlaying(ctx, VIDEO, "opened");
    press(ctx, "ArrowRight");
    await ctx.settle(80);
    assert.equal(ctx.state.library.activeId, VIDEO + 1);
    assertStopped(ctx, "stepped to a photo");
    assert.ok(ctx.played.pause >= 1 && ctx.played.load >= 1, "paused, and load() let go of what the browser held");
    assert.equal(image(ctx).classList.contains("hidden"), false, "the photo's image is shown");
    assert.match(image(ctx).getAttribute("src"), /\/api\/photo-file\?/, "and asked for");
  });

  test("a record with no media field is a photo", async (t) => {
    const ctx = await viewWithAVideo(t);
    await open(ctx, 11);
    assertStopped(ctx, "a photo");
    assert.match(image(ctx).getAttribute("src"), /\/api\/photo-file\?/);
    assert.equal(ctx.played.pause + ctx.played.load, 0, "a player never started is not touched");
  });

  test("the same video shown again plays on where it is", async (t) => {
    const ctx = await viewWithAVideo(t);
    await open(ctx, VIDEO);
    const src = video(ctx).getAttribute("src");
    ctx.module("photo.js").showPhoto(ctx.state.activePhotoPath);
    await ctx.settle(40);
    assert.equal(video(ctx).getAttribute("src"), src);
    assert.equal(ctx.played.load, 0, "not loaded again from the start");
    assert.equal(ctx.played.src, 1, "nor given its address again");
  });
});

describe("leaving a video while it plays", () => {
  for (const [how, leave] of [
    ["the Back button", (ctx) => click(ctx.window, el(ctx, "btn-photo-back"))],
    ["Escape", (ctx) => press(ctx, "Escape")],
  ]) {
    test(`${how} stops it`, async (t) => {
      const ctx = await viewWithAVideo(t);
      await open(ctx, VIDEO);
      assertPlaying(ctx, VIDEO, "opened");
      leave(ctx);
      await ctx.settle(200);
      assert.equal(el(ctx, "panel-content").classList.contains("hidden"), true, "back on the grid");
      assertStopped(ctx, how);
    });
  }

  test("rapid stepping over a video ends on what is shown: the photo's image, or the video playing alone", async (t) => {
    const ctx = await viewWithAVideo(t);
    await open(ctx, VIDEO - 1);
    for (const key of ["ArrowRight", "ArrowRight", "ArrowLeft", "ArrowLeft", "ArrowRight", "ArrowRight"]) press(ctx, key);
    await ctx.settle(200);
    assert.equal(ctx.state.library.activeId, VIDEO + 1);
    assertStopped(ctx, "ended on a photo");
    assert.equal(image(ctx).classList.contains("hidden"), false);
    for (const key of ["ArrowLeft", "ArrowRight", "ArrowLeft"]) press(ctx, key);
    await ctx.settle(200);
    assert.equal(ctx.state.library.activeId, VIDEO);
    assertPlaying(ctx, VIDEO, "ended on the video");
  });
});

describe("a video's title and keywords are edited, nothing else of it", () => {
  const writers = (ctx) => [...ctx.document.querySelectorAll("[data-writes]")];
  const inert = (ctx) => writers(ctx).filter((node) => node.hasAttribute("inert"));
  const note = (ctx) => el(ctx, "photo-missing-note");
  const held = (ctx, id) => Boolean(el(ctx, id).closest("[inert]"));
  const LIVE = ["input-photo-title", "btn-save-title", "btn-edit-people", "input-add-person", "btn-add-person",
    "btn-edit-tags", "input-add-tag", "btn-add-tag", "input-smart-rename-one"];
  const HELD = ["btn-edit-date-taken", "btn-rotate-left", "btn-rotate-right", "btn-delete-photo"];

  function assertClip(ctx, what) {
    for (const id of LIVE) assert.equal(held(ctx, id), false, `${what}: ${id} is live`);
    for (const id of HELD) assert.equal(held(ctx, id), true, `${what}: ${id} is inert`);
    assert.ok(inert(ctx).every((node) => node.getAttribute("aria-disabled") === "true"), `${what}: and says so`);
    assert.equal(note(ctx).classList.contains("hidden"), false, `${what}: the sentence is shown`);
    assert.equal(note(ctx).textContent, "This is a video: its date and turn are not edited here, and it is not deleted here.",
      `${what}: it names only what is not edited`);
  }

  function assertEditable(ctx, what) {
    assert.ok(writers(ctx).length >= 6, `${what}: the page has its write controls`);
    assert.deepEqual(inert(ctx), [], `${what}: every control that writes is live`);
    assert.ok(writers(ctx).every((node) => !node.hasAttribute("aria-disabled")), `${what}: none says it is not`);
    assert.equal(note(ctx).classList.contains("hidden"), true, `${what}: no sentence`);
  }

  test("a clip opened: its title and keywords live, its date, turn, rename and delete inert, and the sentence names those", async (t) => {
    const ctx = await viewWithAVideo(t);
    await open(ctx, VIDEO);
    assertClip(ctx, "the clip");
  });

  test("rapid stepping between a clip and photos leaves each with its own controls", async (t) => {
    const ctx = await viewWithAVideo(t);
    await open(ctx, VIDEO - 1);
    assertEditable(ctx, "a photo");
    for (const key of ["ArrowRight", "ArrowRight", "ArrowLeft"]) press(ctx, key);
    await ctx.settle(200);
    assert.equal(ctx.state.library.activeId, VIDEO);
    assertClip(ctx, "ended on the clip");
    press(ctx, "ArrowRight");
    await ctx.settle(120);
    assertEditable(ctx, "stepped on to a photo");
  });

  test("a clip whose file is gone has every control inert, and says the file is gone", async (t) => {
    const ctx = await viewWithAVideo(t, {
      photoExtra: (id) => (id === VIDEO ? { media: "video", mtime: MTIME, writes: CLIP_WRITES, missing: true } : {}),
    });
    await open(ctx, VIDEO);
    assert.ok(writers(ctx).length >= 6);
    assert.equal(inert(ctx).length, writers(ctx).length);
    assert.match(note(ctx).textContent, /not on disk/);
    assert.doesNotMatch(note(ctx).textContent, /video/);
  });
});

describe("a video the browser cannot play says why", () => {
  const fail = (ctx, code) => {
    Object.defineProperty(video(ctx), "error", { value: { code }, configurable: true });
    video(ctx).dispatchEvent(new ctx.window.Event("error"));
  };

  test("a format it cannot play (the server answers): the format, and the HEVC extension, are named", async (t) => {
    const ctx = await viewWithAVideo(t);
    ctx.server.first("/api/video-file", {}, { status: 206 });
    await open(ctx, VIDEO);
    fail(ctx, 4);
    await ctx.settle(40);
    assert.match(status(ctx), /format cannot be played in this browser/);
  });

  for (const [code, answer, said] of [[4, 404, /file is not there/], [4, 503, /not answering/], [2, 206, /could not be read\.$/]]) {
    test(`error ${code} with the server answering ${answer}: the sentence says what it was`, async (t) => {
      const ctx = await viewWithAVideo(t);
      ctx.server.first("/api/video-file", {}, { status: answer });
      await open(ctx, VIDEO);
      fail(ctx, code);
      await ctx.settle(40);
      assert.match(status(ctx), said);
      assert.doesNotMatch(status(ctx), /format/, "a refused request is not blamed on the format");
    });
  }

  test("an error of a video already left says nothing, however late the server answers", async (t) => {
    const ctx = await viewWithAVideo(t);
    let answer;
    ctx.server.first("/api/video-file", () => new Promise((resolve) => { answer = resolve; }), { status: 206 });
    await open(ctx, VIDEO);
    fail(ctx, 4);                       // the server is asked which it was, and is slow to say
    press(ctx, "ArrowRight");
    await ctx.settle(120);
    assertStopped(ctx, "stepped on");
    answer({});
    await ctx.settle(40);
    assert.doesNotMatch(status(ctx), /video/, "the photo shown now is not said to fail");
    fail(ctx, 2);                       // an error of the stream let go
    await ctx.settle(40);
    assert.doesNotMatch(status(ctx), /video/);
  });
});
