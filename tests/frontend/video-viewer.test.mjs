/**
 * A video of a library view, in the photo panel (web/tagpup/media.js): its card carries a play mark, opening it plays it
 * from /api/video-file through the library (the image hidden, so the zoom and the face boxes stand down), and it stops --
 * no src, the stream let go -- when another photo is shown, on Back and on Escape. How it fails: rapid stepping between
 * a video and photos, the same video shown again (it plays on), a record with no `media` (a photo), and the video's
 * error event: a format the browser cannot play, a file gone, a share away, and an error of a video already left. What a
 * clip needs to play is the server's sentence (/api/clip/convert-plan, tagpup.files.convertible), asked on a format error
 * and when the browser plays the sound and shows no picture with no error at all (Chrome and an MPEG-4 Part 2 clip:
 * videoWidth 0 once its first data is loaded); a late answer about a clip stepped past says nothing.
 * Switching library is another page (web/common/library.js): nothing of the video outlives it. A video offers the
 * edits its record's `writes` names (its title, people, keywords, name and Date Taken: DECISIONS 2026-11-08, A5 (D)
 * REVISED): Rotate is inert with a sentence naming it, a photo stepped to after it has every control back, and a missing one has none (stale.js
 * applyPhotoStale). Its delete is live, as a photo's (DECISIONS 2026-11-08, A5, cut B): a clip playing is stopped before
 * the delete is sent, so the server holds no range of its file open, and shown again when the delete fails. A clip its
 * drive's Recycle Bin would not keep is answered with its name and nothing deleted: the page warns, naming it, and sends
 * `for_good` only on OK; on Cancel nothing is deleted and the clip plays again (DECISIONS 2026-11-08, 'FOR GOOD'). Fictional
 * names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, CLIP_WRITES, answer as replied, deletedAs } from "./harness.mjs";
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

describe("a video's title, keywords, name and date are edited, and its turn is not offered", () => {
  const writers = (ctx) => [...ctx.document.querySelectorAll("[data-writes]")];
  const inert = (ctx) => writers(ctx).filter((node) => node.hasAttribute("inert"));
  const note = (ctx) => el(ctx, "photo-missing-note");
  const held = (ctx, id) => Boolean(el(ctx, id).closest("[inert]"));
  const LIVE = ["input-photo-title", "btn-save-title", "btn-edit-people", "input-add-person", "btn-add-person",
    "btn-edit-tags", "input-add-tag", "btn-add-tag", "input-smart-rename-one", "btn-delete-photo", "btn-edit-date-taken"];
  const TURNS = ["btn-rotate-left", "btn-rotate-right", "btn-mirror"];
  const turnsOffered = (ctx) => TURNS.map((id) => !el(ctx, id).closest(".hidden"));

  function assertClip(ctx, what) {
    for (const id of LIVE) assert.equal(held(ctx, id), false, `${what}: ${id} is live`);
    assert.deepEqual(turnsOffered(ctx), [false, false, false], `${what}: the rotate and mirror buttons are not offered`);
    assert.equal(note(ctx).classList.contains("hidden"), true, `${what}: no sentence: every field it holds is edited`);
    assert.equal(note(ctx).textContent, "", `${what}: and none left over`);
  }

  function assertEditable(ctx, what) {
    assert.ok(writers(ctx).length >= 6, `${what}: the page has its write controls`);
    assert.deepEqual(inert(ctx), [], `${what}: every control that writes is live`);
    assert.ok(writers(ctx).every((node) => !node.hasAttribute("aria-disabled")), `${what}: none says it is not`);
    assert.equal(note(ctx).classList.contains("hidden"), true, `${what}: no sentence`);
    assert.deepEqual(turnsOffered(ctx), [true, true, true], `${what}: the rotate and mirror buttons are offered`);
  }

  test("a clip opened: its title, keywords, name, date and delete live, its turn not offered, and no sentence", async (t) => {
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

  test("a clip's record that names no writes (a scan kept from before an install) holds every field and names them", async (t) => {
    const ctx = await viewWithAVideo(t, {
      photoExtra: (id) => (id === VIDEO ? { media: "video", mtime: MTIME, writes: undefined } : {}),
    });
    await open(ctx, VIDEO);
    assert.equal(note(ctx).textContent, "Not edited here: date, title and people and keywords.");
    assert.deepEqual(turnsOffered(ctx), [true, true, true], "nothing says it cannot be turned: shown, inert");
    assert.equal(held(ctx, "btn-rotate-left"), true);
  });

  test("the video cannot be popped out: it would play on after the owner moves to another photo", async (t) => {
    const ctx = await viewWithAVideo(t);
    assert.equal(el(ctx, "main-video").hasAttribute("disablepictureinpicture"), true);
  });

  test("a clip whose file is gone has every control inert, and says the file is gone", async (t) => {
    const ctx = await viewWithAVideo(t, {
      photoExtra: (id) => (id === VIDEO ? { media: "video", mtime: MTIME, writes: CLIP_WRITES, missing: true } : {}),
    });
    await open(ctx, VIDEO);
    assert.ok(writers(ctx).length >= 6);
    assert.equal(inert(ctx).length, writers(ctx).length);
    assert.equal(held(ctx, "btn-delete-photo"), true, "its delete too: there is no file to delete");
    assert.match(note(ctx).textContent, /not on disk/);
    assert.doesNotMatch(note(ctx).textContent, /video/);
  });

  test("a clip playing is stopped before its delete is sent, and plays again when the delete fails", async (t) => {
    const ctx = await viewWithAVideo(t);
    const answer = [];
    ctx.server.first("/api/photo/delete", () => new Promise((resolve) => answer.push(resolve)));
    ctx.window.confirm = () => true;
    const alerts = [];
    ctx.window.alert = (said) => alerts.push(said);
    await open(ctx, VIDEO);
    assertPlaying(ctx, VIDEO, "opened");
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(40);
    assert.equal(answer.length, 1, "the delete was sent");
    assertStopped(ctx, "while the delete is out");
    const FAILED = "A copy is in this PC's Recycle Bin, but the original could not be deleted";
    answer[0](replied(500, { success: false, error: FAILED }));
    await ctx.settle(80);
    assert.equal(alerts.length, 1, "the failure is said");
    assert.deepEqual(ctx.consoleErrors.map(String), ["Error: " + FAILED], "and logged, as a photo's failed delete is");
    ctx.consoleErrors.length = 0;
    assertPlaying(ctx, VIDEO, "the delete failed: the clip is still there and shown again");
  });

  const deletes = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/photo/delete"));

  test("its Delete is inert from the click to the reply: a second click sends no second delete", async (t) => {
    const ctx = await viewWithAVideo(t);
    const answer = [];
    ctx.server.first("/api/photo/delete", () => new Promise((resolve) => answer.push(resolve)));
    ctx.window.confirm = () => true;
    ctx.window.alert = () => {};
    await open(ctx, VIDEO);
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(40);
    assert.ok(el(ctx, "btn-delete-photo").hasAttribute("inert"), "inert while the delete is out");
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(40);
    assert.equal(deletes(ctx).length, 1, "one delete, one copy into its .partial");
    answer[0](replied(500, { success: false, error: "The file is in use" }));
    await ctx.settle(80);
    ctx.consoleErrors.length = 0;
    assert.ok(!el(ctx, "btn-delete-photo").hasAttribute("inert"), "answered: offered again");
  });

  const NOT_KEPT = "Beach_Day_Long_Take.mp4";
  const REFUSED = "The Recycle Bin of its drive would not keep this file: it is deleted for good only once you confirm it.";

  /**
   * The delete's replies in turn, as the server sends them (harness.mjs deletedAs): the first a 400 naming the file its
   * drive's Bin would not keep (`not_kept: [name]`), the second the confirmed delete's. Each route answers once and goes.
   */
  function answersInTurn(ctx, replies) {
    for (const reply of [...replies].reverse()) {
      const route = { match: "/api/photo/delete", status: 200, headers: {} };
      route.body = (url, body) => { ctx.server.routes.splice(ctx.server.routes.indexOf(route), 1); return reply(url, body); };
      ctx.server.routes.unshift(route);
    }
  }

  function asked(ctx, answers) {
    const questions = [];
    ctx.window.confirm = (question) => { questions.push(question); return answers[questions.length - 1]; };
    return questions;
  }

  test("a clip its drive's Bin would not keep: the warning names it, and OK alone sends the delete for good", async (t) => {
    const ctx = await viewWithAVideo(t);
    answersInTurn(ctx, [
      deletedAs("not_kept", { error: REFUSED, not_kept: [NOT_KEPT], bin_full: 0 }),
      deletedAs("for_good", { message: "Deleted for good, as you confirmed: the Recycle Bin of its drive would not keep it." }),
    ]);
    const questions = asked(ctx, [true, true]);
    ctx.window.alert = (said) => assert.fail(`nothing failed, yet the page said: ${said}`);
    await open(ctx, VIDEO);
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(120);
    assert.equal(questions.length, 2, "the delete was asked, then the warning");
    assert.match(questions[1], /deleted for good, not moved to the Recycle Bin/);
    assert.ok(questions[1].includes(NOT_KEPT), "the warning names the file");
    assert.match(questions[1], /Cancel deletes nothing/);
    const sent = deletes(ctx).map((c) => c.body);
    assert.equal(sent.length, 2);
    assert.equal(sent[0].for_good, undefined, "the first delete does not say for good: nobody had confirmed it");
    assert.equal(sent[1].for_good, true, "the second does, once OK was pressed");
    assert.equal(sent[1].path, sent[0].path);
    assert.equal(ctx.state.library.activeId, VIDEO + 1, "deleted: the next photo opens");
    assert.ok(!ctx.state.library.ids.includes(VIDEO) || ctx.cardById(VIDEO) === null, "the clip has left the view");
    assert.equal(status(ctx), "Deleted for good, as you confirmed: the Recycle Bin of its drive would not keep it.",
      "the status line says what the server did, never Ready");
  });

  test("a share's clip this PC's full Recycle Bin would not keep: asked to delete permanently, OK resends, Cancel sends nothing", async (t) => {
    // Owner, 2026-11-12: a full Bin asks instead of refusing (tagpup_routes.photo_delete, `bin_full: 1`).
    const FULL = "Beach_Day_Long_Take.mp4 would be deleted for good, not moved to the Recycle Bin: this PC's Recycle Bin is full.";
    const ctx = await viewWithAVideo(t);
    answersInTurn(ctx, [
      deletedAs("not_kept", { error: FULL, not_kept: [NOT_KEPT] }),
      deletedAs("for_good", { message: "Deleted for good, as you confirmed: this PC's Recycle Bin is full." }),
    ]);
    const questions = asked(ctx, [true, true]);
    ctx.window.alert = (said) => assert.fail(`nothing failed, yet the page said: ${said}`);
    await open(ctx, VIDEO);
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(120);
    assert.equal(questions.length, 2);
    assert.match(questions[1], /^The Recycle Bin is full: delete permanently instead\?/);
    assert.match(questions[1], /a network share/);
    assert.match(questions[1], /cannot be restored/);
    assert.ok(questions[1].includes(NOT_KEPT), "it names the file");
    assert.match(questions[1], /OK deletes it permanently; Cancel deletes nothing\.$/);
    const sent = deletes(ctx).map((c) => c.body);
    assert.deepEqual([sent.length, sent[0].for_good, sent[1].for_good], [2, undefined, true]);

    const kept = await viewWithAVideo(t);
    answersInTurn(kept, [deletedAs("not_kept", { error: FULL, not_kept: [NOT_KEPT] })]);
    const again = asked(kept, [true, false]);
    kept.window.alert = (said) => assert.fail(`a Cancel is not a failure, yet the page said: ${said}`);
    await open(kept, VIDEO);
    click(kept.window, el(kept, "btn-delete-photo"));
    await kept.settle(120);
    assert.match(again[1], /^The Recycle Bin is full/);
    assert.equal(deletes(kept).length, 1, "Cancel: nothing resent");
    assert.equal(status(kept), "Nothing was deleted.");
  });

  test("a clip opened again while its delete is out is asked of the server, which refuses it; the delete failed, it plays", async (t) => {
    // Only the server knows which file it is deleting (a range read of it as its original is removed makes the remove fail).
    const ctx = await viewWithAVideo(t);
    const answer = [];
    ctx.server.first("/api/photo/delete", () => new Promise((resolve) => answer.push(resolve)));
    ctx.window.confirm = () => true;
    ctx.window.alert = () => {};
    await open(ctx, VIDEO);
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(40);
    assert.equal(answer.length, 1, "the delete was sent");
    assertStopped(ctx, "stopped before the delete");
    press(ctx, "ArrowRight");
    await ctx.settle(80);
    press(ctx, "ArrowLeft");
    await ctx.settle(80);
    assert.equal(ctx.state.library.activeId, VIDEO, "the clip is the one shown again");
    assertPlaying(ctx, VIDEO, "opened again: asked of the server");
    ctx.server.first("/api/video-file", { success: false, error: "This video is being deleted." }, { status: 409 });
    Object.defineProperty(video(ctx), "error", { value: { code: 4 }, configurable: true });
    video(ctx).dispatchEvent(new ctx.window.Event("error"));
    await ctx.settle(40);
    assert.equal(status(ctx), "This video is being deleted.", "the server's sentence, never 'not there' or the format");
    const asked = ctx.played.src;
    answer[0](replied(500, { success: false, error: "The file is in use" }));
    await ctx.settle(80);
    ctx.consoleErrors.length = 0;
    assertPlaying(ctx, VIDEO, "the delete failed");
    assert.ok(ctx.played.src > asked, "the clip that was refused is asked for again, not left failed");
  });

  test("Cancel at the warning deletes nothing: one delete sent, nothing said deleted, the clip plays again", async (t) => {
    const ctx = await viewWithAVideo(t);
    answersInTurn(ctx, [deletedAs("not_kept", { error: REFUSED, not_kept: [NOT_KEPT], bin_full: 0 })]);
    const questions = asked(ctx, [true, false]);
    ctx.window.alert = (said) => assert.fail(`a Cancel is not a failure, yet the page said: ${said}`);
    await open(ctx, VIDEO);
    click(ctx.window, el(ctx, "btn-delete-photo"));
    await ctx.settle(120);
    assert.equal(questions.length, 2, "the delete was asked, then the warning");
    assert.equal(deletes(ctx).length, 1, "no second delete: the server was never told for good");
    assert.equal(status(ctx), "Nothing was deleted.");
    assert.equal(ctx.state.library.activeId, VIDEO, "the clip is still the one shown");
    assertPlaying(ctx, VIDEO, "cancelled: the clip plays again");
    assert.ok(!el(ctx, "btn-delete-photo").hasAttribute("inert"), "and its Delete is offered again");
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

  test("a stream that broke (error 2) on a clip that plays is not called damaged: the plan is not asked", async (t) => {
    const ctx = await viewWithAVideo(t);
    ctx.server.first("/api/video-file", {}, { status: 206 });
    ctx.server.first("/api/clip/convert-plan", { success: true, plan: "NOTHING", sentence: "its file may be damaged." });
    await open(ctx, VIDEO);
    fail(ctx, 2);
    await ctx.settle(60);
    assert.match(status(ctx), /could not be read\.$/);
    assert.doesNotMatch(status(ctx), /damaged/);
    assert.equal(ctx.server.calls.filter((call) => call.url.includes("/api/clip/convert-plan")).length, 0, "a network error asks nothing of ffprobe");
  });

  const PLAN = "This video's picture is mpeg4, a format browsers do not play (its sound may play without it): it plays here once it is converted (Convert to play).";
  const loaded = (ctx, width) => {
    Object.defineProperty(video(ctx), "videoWidth", { value: width, configurable: true });
    video(ctx).dispatchEvent(new ctx.window.Event("loadeddata"));
  };
  const plansAsked = (ctx) => ctx.server.calls.filter((call) => call.url.includes("/api/clip/convert-plan"));

  test("a format it cannot play: the server's sentence of what it needs is said, asked of the clip's library by its id", async (t) => {
    const ctx = await viewWithAVideo(t);
    ctx.server.first("/api/video-file", {}, { status: 206 });
    ctx.server.first("/api/clip/convert-plan", { action: "REENCODE", sentence: PLAN });
    await open(ctx, VIDEO);
    fail(ctx, 4);
    await ctx.settle(40);
    assert.equal(status(ctx), PLAN);
    assert.deepEqual(plansAsked(ctx).map((call) => call.url), [`/photo_index/api/clip/convert-plan?id=${VIDEO}`]);
  });

  test("sound and no picture, with no error (videoWidth 0): the server is asked what it needs, and its sentence said", async (t) => {
    const ctx = await viewWithAVideo(t);
    ctx.server.first("/api/clip/convert-plan", { action: "REENCODE", sentence: PLAN });
    await open(ctx, VIDEO);
    loaded(ctx, 0);
    await ctx.settle(40);
    assert.equal(status(ctx), PLAN, "the panel says why there is no picture, never nothing");
  });

  test("a picture shown (videoWidth above 0) asks nothing and says nothing", async (t) => {
    const ctx = await viewWithAVideo(t);
    await open(ctx, VIDEO);
    loaded(ctx, 160);
    await ctx.settle(40);
    assert.deepEqual(plansAsked(ctx), []);
    assert.doesNotMatch(status(ctx), /video/);
  });

  for (const [what, reply, said] of [
    ["the server cannot say (a reply that is no plan)", [{}, { status: 500 }], /format cannot be played in this browser/],
    ["the share is away (503 with its sentence)", [{ success: false, error: "The share the video is on did not answer." }, { status: 503 }], /share the video is on did not answer/],
  ]) {
    test(`no picture and ${what}: the page says ${said}`, async (t) => {
      const ctx = await viewWithAVideo(t);
      ctx.server.first("/api/clip/convert-plan", ...reply);
      await open(ctx, VIDEO);
      loaded(ctx, 0);
      await ctx.settle(40);
      assert.match(status(ctx), said);
    });
  }

  test("stepped on while the plan is asked: the late answer says nothing of the photo shown now", async (t) => {
    const ctx = await viewWithAVideo(t);
    let answer;
    ctx.server.first("/api/clip/convert-plan", () => new Promise((resolve) => { answer = resolve; }));
    await open(ctx, VIDEO);
    loaded(ctx, 0);
    await ctx.settle(20);
    press(ctx, "ArrowRight");
    await ctx.settle(120);
    assertStopped(ctx, "stepped on");
    answer({ action: "REENCODE", sentence: PLAN });
    await ctx.settle(40);
    assert.doesNotMatch(status(ctx), /video/, "the photo shown now is not said to need converting");
  });

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
