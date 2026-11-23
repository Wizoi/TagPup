/**
 * The clip panel's Convert and Make smaller (MP4) (web/tagpup/clip-convert.js; POST /api/clip/convert, a batch of one on
 * a thread of the server, read by its ticket at /api/clip/converted): C7, the page's side. Convert is offered for a clip
 * the browser does not play whose plan (asked once, by media.js, when it failed) is REENCODE; Make smaller for one that
 * plays with a picture, its plan asked only when it is clicked. The confirm is the plan's `replaces`; the ticket is read
 * until it ends; the clip's record follows its MP4 and the sizes are said. How it fails: rapid clicks (one request), a
 * Cancel at the confirm (nothing sent), stepping to another photo while it converts (the photo shown now is not
 * touched), the Cancel of one under way (by its ticket), a stamp the clip no longer has (409 changed_on_disk: read
 * again, said), and the clip read again after the server ended its conversion and before the page read that end (tagged
 * since, in another window): the end changes only the name and stamp of a record still naming the file converted (#3331).
 * A scan this browser keeps holding the clip with an older stamp (tagged since, from the phone) holds it under its MP4's
 * name after a Convert that renamed it, its suggestions with it, since no file is left at the old name (#3337); after a
 * Make smaller, which keeps the name, a record with an older stamp is left as it was (the stamp says it is not the file's).
 * Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, CLIP_WRITES, pageExports, scanned, convertedAs } from "./harness.mjs";
import { loadViewPage, pageErrors, recordOf, speedUp } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const CLIP = 13;
const MTIME = 1600000123.5;
const MB = 1024 * 1024;
const OLD = "D:\\Library\\2020\\Event 01\\IMG_13.wmv";
const NEW = "D:\\Library\\2020\\Event 01\\IMG_13.mp4";
const REPLACES = "IMG_13.wmv is replaced by an MP4 of it; the original is deleted for good once its copy is verified.";
const el = (ctx, id) => ctx.document.getElementById(id);
const video = (ctx) => el(ctx, "main-video");
const status = (ctx) => el(ctx, "status-text").textContent;
const shown = (ctx, id) => !el(ctx, id).classList.contains("hidden");
/** The sentences the batch strip shows (the one under the status line, with History and Dismiss). */
const stripSaid = (ctx) => [...ctx.document.querySelectorAll(".batch-strip-sentence")].map((each) => each.textContent);
const callsTo = (ctx, path) => ctx.server.calls.filter((call) => call.url.split("?")[0].endsWith(path));

/**
 * A view of eight photos, the third a .wmv clip of 120 MB; the player's pause() and load() stubbed (jsdom has neither).
 * The server converts: `ctx.ends` is what /api/clip/converted answers, in turn (the last one kept), each a body or a
 * function returning one.
 */
async function viewWithAClip(t, { plan = { action: "REENCODE", sentence: "It needs converting.", replaces: REPLACES }, path = OLD } = {}) {
  const ctx = await loadViewPage(t, {
    search: "?view=all",
    cardExtra: (id) => ({ media: id === CLIP ? "video" : "photo" }),
    onPhoto: (id) => ({
      photo: recordOf(id, id === CLIP ? clipRecord({ path }) : {}),
    }),
    before: (window) => {
      window.HTMLMediaElement.prototype.pause = () => {};
      window.HTMLMediaElement.prototype.load = () => {};
    },
  });
  speedUp(ctx);
  // Every sentence the status line said: a success's is transient, gone again after its few seconds (made fast here).
  ctx.said = [];
  new ctx.window.MutationObserver(() => ctx.said.push(status(ctx)))
    .observe(el(ctx, "status-text"), { childList: true, characterData: true, subtree: true });
  ctx.ends = [{ done: false }];
  ctx.confirmed = [];
  ctx.window.confirm = (text) => { ctx.confirmed.push(text); return ctx.answerConfirm !== false; };
  ctx.server
    .first("/api/video-file", {}, { status: 206 })
    .first("/api/clip/convert", { success: true, ticket: 7, library: "photo_index" })
    .first("/api/clip/converted", () => {
      const next = ctx.ends.length > 1 ? ctx.ends.shift() : ctx.ends[0];
      return typeof next === "function" ? next() : next;
    })
    .first("/api/clip/convert-plan", () => plan)
    .first("/api/activity/batches/cancel", { success: true });
  return ctx;
}

const open = async (ctx, id) => {
  ctx.cardById(id).querySelector(".btn-thumbnail-detail").click();
  await ctx.settle(80);
};
/** The browser cannot play it (a format error): media.js asks its plan. */
const fails = async (ctx) => {
  Object.defineProperty(video(ctx), "error", { value: { code: 4 }, configurable: true });
  video(ctx).dispatchEvent(new ctx.window.Event("error"));
  await ctx.settle(40);
};
/** It plays, with a picture `width` wide. */
const plays = async (ctx, width = 160) => {
  Object.defineProperty(video(ctx), "videoWidth", { value: width, configurable: true });
  video(ctx).dispatchEvent(new ctx.window.Event("loadeddata"));
  await ctx.settle(20);
};
const press = async (ctx, key) => {
  ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  await ctx.settle(120);
};
const converted = { done: true, success: true, new_path: NEW, renamed: true, size_before: 120 * MB, size_after: 18 * MB, mtime: 1700000000.25, size: 18 * MB };
/** The clip's record as the library gives it, at `path` (its file's name with it), `own` over the rest. */
const clipRecord = ({ path, ...own }) => ({ media: "video", filename: path.split("\\").pop(), path, mtime: MTIME, size: 120 * MB,
  writes: CLIP_WRITES, tags: [], ...own });

describe("the clip panel's Convert and Make smaller (MP4)", () => {
  test("a clip the browser cannot play, planned REENCODE: Convert offered, confirmed by its `replaces`, read to its end, followed to its MP4", async (t) => {
    const ctx = await viewWithAClip(t);
    await open(ctx, CLIP);
    assert.equal(shown(ctx, "btn-clip-convert"), false, "nothing offered before the plan is read");
    await fails(ctx);
    assert.equal(shown(ctx, "btn-clip-convert"), true, "Convert is offered");
    assert.equal(shown(ctx, "btn-clip-shrink"), false, "Make smaller is not");
    assert.equal(callsTo(ctx, "/api/clip/convert-plan").length, 1, "the plan media.js asked is the one used");

    el(ctx, "btn-clip-convert").click();
    el(ctx, "btn-clip-convert").click();
    await ctx.settle(5);
    assert.deepEqual(ctx.confirmed, [REPLACES], "the confirm is the plan's sentence, asked once");
    assert.equal(el(ctx, "btn-clip-convert").disabled, true, "the buttons are inert while it is under way");
    await ctx.until(() => callsTo(ctx, "/api/clip/convert").length > 0);
    assert.equal(callsTo(ctx, "/api/clip/convert").length, 1, "rapid clicks send one conversion");
    assert.deepEqual(callsTo(ctx, "/api/clip/convert")[0].body, { id: CLIP, mode: "convert", stamp: { mtime: MTIME, size: 120 * MB } });
    assert.equal(callsTo(ctx, "/api/clip/convert-plan").length, 1, "the plan is not asked a second time");
    assert.ok(await ctx.until(() => shown(ctx, "btn-clip-convert-cancel") && callsTo(ctx, "/api/clip/converted").length >= 2));
    assert.match(el(ctx, "clip-convert-note").textContent, /Converting IMG_13\.wmv/);
    ctx.ends = [converted];

    assert.ok(await ctx.until(() => ctx.said.some((each) => /Converted/.test(each))), `the end is said (${ctx.said})`);
    assert.ok(ctx.said.includes("Converted IMG_13.mp4: 120 MB to 18 MB"), ctx.said.join(" | "));
    assert.deepEqual(stripSaid(ctx), ["Converted IMG_13.mp4: 120 MB to 18 MB"], "the strip says the page's sentence, with the file's name");
    assert.ok(callsTo(ctx, "/api/clip/converted").length >= 3, "its ticket is read until it ends");
    assert.ok(callsTo(ctx, "/api/clip/converted").every((call) => call.url.endsWith("?ticket=7")));
    const record = ctx.state.folderPhotos.find((photo) => photo.id === CLIP);
    assert.equal(record.path, NEW, "the clip's record follows its MP4");
    assert.equal(record.mtime, converted.mtime, "and takes its stamp, the next write's");
    assert.equal(ctx.state.activePhotoPath, NEW, "the panel shows it under its new name");
    assert.ok((video(ctx).getAttribute("src") || "").includes(`v=${encodeURIComponent(converted.mtime)}`), "and plays the MP4, asked by its new stamp");
    assert.equal(shown(ctx, "btn-clip-convert-cancel"), false);
    assert.equal(shown(ctx, "clip-convert-note"), false);
    const asked = callsTo(ctx, "/api/clip/converted").length;
    await ctx.settle(120);
    assert.equal(callsTo(ctx, "/api/clip/converted").length, asked, "the reading stops at its end");
  });

  test("a conversion done whose original stayed says so as a sentence of its own: a capital, one full stop", async (t) => {
    const ctx = await viewWithAClip(t);
    await open(ctx, CLIP);
    await fails(ctx);
    ctx.ends = [convertedAs("end", "original_kept", converted)];
    el(ctx, "btn-clip-convert").click();
    const expected = "Converted IMG_13.mp4: 120 MB to 18 MB. It was converted, but its original changed meanwhile (another program "
      + "wrote it), so it is not deleted: it and its verified copy are both kept, and the next sync finds it as a clip of its own.";
    assert.ok(await ctx.until(() => stripSaid(ctx).length > 0), "the end is said");
    assert.deepEqual(stripSaid(ctx), [expected]);
  });

  test("a clip that plays with a picture: Make smaller offered, nothing asked until it is clicked; a shrink no smaller says the server's sentence", async (t) => {
    const ctx = await viewWithAClip(t, { plan: { action: "NOTHING", sentence: "", replaces: REPLACES } });
    await open(ctx, CLIP);
    await plays(ctx);
    assert.equal(shown(ctx, "btn-clip-shrink"), true, "Make smaller is offered");
    assert.equal(shown(ctx, "btn-clip-convert"), false);
    assert.equal(callsTo(ctx, "/api/clip/convert-plan").length, 0, "a clip that plays asks nothing of ffprobe");

    const LEFT = "its MP4 came out no smaller than it is: left as it is";
    ctx.ends = [{ done: true, success: false, error: LEFT, size_before: 120 * MB, size_after: 130 * MB }];
    el(ctx, "btn-clip-shrink").click();
    assert.ok(await ctx.until(() => callsTo(ctx, "/api/clip/convert").length > 0));
    assert.equal(callsTo(ctx, "/api/clip/convert-plan").length, 1, "its plan is asked once, at the click");
    assert.deepEqual(ctx.confirmed, [REPLACES]);
    assert.equal(callsTo(ctx, "/api/clip/convert")[0].body.mode, "shrink");
    assert.ok(await ctx.until(() => status(ctx).includes(LEFT)), `the server's sentence is said (${status(ctx)})`);
    assert.deepEqual(stripSaid(ctx), [`Could not convert IMG_13.wmv: ${LEFT}`], "the strip names the file and gives the server's sentence");
    assert.equal(ctx.state.folderPhotos.find((photo) => photo.id === CLIP).path, OLD, "the clip is left as it is");
    assert.equal(el(ctx, "btn-clip-shrink").disabled, false, "it may be asked again");
  });

  test("a clip that fails and is planned NOTHING (damaged) offers neither; Cancel at the confirm sends nothing", async (t) => {
    const ctx = await viewWithAClip(t, { plan: { action: "NOTHING", sentence: "Its file may be damaged.", replaces: REPLACES } });
    await open(ctx, CLIP);
    await fails(ctx);
    assert.equal(shown(ctx, "btn-clip-convert") || shown(ctx, "btn-clip-shrink"), false, "a clip that does not play and needs nothing offers no conversion");

    const other = await viewWithAClip(t);
    other.answerConfirm = false;
    await open(other, CLIP);
    await fails(other);
    el(other, "btn-clip-convert").click();
    await other.settle(60);
    assert.deepEqual(other.confirmed, [REPLACES]);
    assert.equal(callsTo(other, "/api/clip/convert").length, 0, "Cancel at the confirm sends nothing");
    assert.equal(el(other, "btn-clip-convert").disabled, false);
  });

  test("stepped to another photo while it converts: its end is taken by the clip's record, the photo shown now is not touched", async (t) => {
    const ctx = await viewWithAClip(t);
    let finish;
    ctx.ends = [() => new Promise((resolve) => { finish = resolve; })];
    await open(ctx, CLIP);
    await fails(ctx);
    el(ctx, "btn-clip-convert").click();
    assert.ok(await ctx.until(() => finish !== undefined), "the ticket is being read");
    await press(ctx, "ArrowRight");
    const here = ctx.state.activePhotoPath;
    assert.notEqual(here, OLD, "stepped on");
    assert.equal(shown(ctx, "btn-clip-convert-cancel"), false, "a photo shows no conversion of another");
    finish(converted);
    assert.ok(await ctx.until(() => ctx.said.some((each) => /Converted/.test(each))));
    assert.equal(ctx.state.activePhotoPath, here, "the photo shown now stays shown");
    assert.equal(video(ctx).getAttribute("src"), null, "and no clip is played in its place");
    const now = ctx.state.folderPhotos.find((photo) => ctx.state.activePhotoPath === photo.path);
    assert.ok(now && now.id !== CLIP && now.path !== NEW, "its record is its own");
    assert.ok(ctx.cardsAsked.some((ids) => ids.includes(CLIP)), "the clip's card is asked for again under its new name");
  });

  test("Cancel while it converts: asked of the server by its ticket, and the end it reaches said", async (t) => {
    const ctx = await viewWithAClip(t);
    await open(ctx, CLIP);
    await fails(ctx);
    el(ctx, "btn-clip-convert").click();
    assert.ok(await ctx.until(() => shown(ctx, "btn-clip-convert-cancel")));
    el(ctx, "btn-clip-convert-cancel").click();
    await ctx.settle(20);
    const cancels = callsTo(ctx, "/api/activity/batches/cancel");
    assert.equal(cancels.length, 1);
    assert.deepEqual(cancels[0].body, { library: "photo_index", ticket: 7 });
    assert.ok(!cancels[0].url.startsWith("/photo_index/"), "asked of the site, naming its library");
    assert.equal(el(ctx, "btn-clip-convert-cancel").disabled, true, "pressed once");
    ctx.ends = [{ done: true, success: false, error: "Cancelled: IMG_13.wmv is left as it is." }];
    assert.ok(await ctx.until(() => /Cancelled/.test(status(ctx))), `its end is said (${status(ctx)})`);
    assert.deepEqual(stripSaid(ctx), ["Could not convert IMG_13.wmv: Cancelled: IMG_13.wmv is left as it is."]);
    assert.equal(ctx.state.folderPhotos.find((photo) => photo.id === CLIP).path, OLD);
    assert.equal(el(ctx, "btn-clip-convert").disabled, false);
  });

  test("Make smaller says so in the strip, with the sizes; no photo count is ever said for a conversion", async (t) => {
    const ctx = await viewWithAClip(t, { plan: { action: "NOTHING", sentence: "", replaces: REPLACES } });
    await open(ctx, CLIP);
    await plays(ctx);
    ctx.ends = [{ ...converted, size_after: 4 * MB }];
    el(ctx, "btn-clip-shrink").click();
    assert.ok(await ctx.until(() => stripSaid(ctx).length > 0));
    assert.deepEqual(stripSaid(ctx), ["Made smaller IMG_13.mp4: 120 MB to 4 MB"]);
    assert.ok(!/photo/.test(stripSaid(ctx)[0]));
  });

  for (const [what, path, end] of [
    ["Convert, its MP4 a name of its own", OLD, converted],
    ["Make smaller, an MP4 keeping its name", NEW, { ...converted, new_path: NEW, renamed: false }],
  ]) {
    test(`${what}: the clip read again after the server ended it and tagged since keeps its tags and stamp when the page reads the end`, async (t) => {
      const ctx = await viewWithAClip(t, path === OLD ? { path } : { path, plan: { action: "NOTHING", sentence: "", replaces: REPLACES } });
      let finish;
      ctx.ends = [() => new Promise((resolve) => { finish = resolve; })];
      await open(ctx, CLIP);
      if (path === OLD) await fails(ctx); else await plays(ctx);
      el(ctx, path === OLD ? "btn-clip-convert" : "btn-clip-shrink").click();
      assert.ok(await ctx.until(() => finish !== undefined), "the ticket is being read");
      // The server has ended the conversion; the clip, read again before this page reads that end, is its MP4 as the
      // library holds it now: tagged since (another window), its stamp the one that save left.
      const since = clipRecord({ path: NEW, mtime: end.mtime + 60, size: end.size + 512, tags: ["Places/Harbour"] });
      ctx.server.first("/api/library/photo", (url) => {
        const id = Number(url.match(/id=(\d+)/)[1]);
        return { photo: recordOf(id, id === CLIP ? since : {}) };
      });
      await press(ctx, "ArrowRight");
      await press(ctx, "ArrowLeft");
      assert.equal(ctx.state.folderPhotos.find((photo) => photo.id === CLIP).mtime, since.mtime, "the clip was not read again");
      finish(end);
      assert.ok(await ctx.until(() => ctx.said.some((each) => /(Converted|Made smaller) IMG_13\.mp4/.test(each))), ctx.said.join(" | "));
      const record = ctx.state.folderPhotos.find((photo) => photo.id === CLIP);
      assert.deepEqual([record.path, record.filename, record.tags, record.mtime, record.size],
        [NEW, "IMG_13.mp4", ["Places/Harbour"], since.mtime, since.size],
        "the record from the click was copied over the clip as the library holds it now");
    });
  }

  for (const [what, path, end] of [
    ["Convert, its MP4 a name of its own", OLD, converted],
    ["Make smaller, an MP4 keeping its name", NEW, { ...converted, new_path: NEW, renamed: false }],
  ]) {
    test(`${what}: a scan this browser keeps holding the clip with an older stamp, when the conversion ends`, async (t) => {
      const ctx = await viewWithAClip(t, path === OLD ? { path } : { path, plan: { action: "NOTHING", sentence: "", replaces: REPLACES } });
      // The clip's folder kept by this browser from before the clip was tagged on the phone: its stamp older than the one
      // the panel converts from, its suggestions under the name it had.
      const { folderCacheKey, CACHE_VERSION } = pageExports(ctx.window, "web/tagpup/cache.js");
      const key = folderCacheKey("D:\\Library\\2020\\Event 01");
      const older = scanned(clipRecord({ path, mtime: MTIME - 600, size: 120 * MB - 512, taken: "2020:01:01 10:00:00" }));
      const offer = { tags: [{ tag: "Places/Harbour", score: 0.9 }], people: [], title: "" };
      ctx.window.localStorage.setItem(key, JSON.stringify({ version: CACHE_VERSION, timestamp: Date.now(), photos: [older],
        suggestions: { [path]: offer }, inMemory: false }));
      ctx.ends = [end];
      await open(ctx, CLIP);
      if (path === OLD) await fails(ctx); else await plays(ctx);
      el(ctx, path === OLD ? "btn-clip-convert" : "btn-clip-shrink").click();
      assert.ok(await ctx.until(() => ctx.said.some((each) => /(Converted|Made smaller) IMG_13\.mp4/.test(each))), ctx.said.join(" | "));
      const kept = JSON.parse(ctx.window.localStorage.getItem(key));
      const record = kept.photos[0];
      if (path === OLD) {
        assert.deepEqual([record.path, record.filename, record.mtime, record.size], [NEW, "IMG_13.mp4", end.mtime, end.size],
          "the kept scan holds the clip under a name no file has");
        assert.deepEqual(Object.keys(kept.suggestions), [NEW], "its suggestions stayed under the old name");
      } else {
        assert.deepEqual([record.path, record.filename, record.mtime, record.size], [NEW, "IMG_13.mp4", older.mtime, older.size],
          "a record with a stamp the file did not have took the conversion's");
        assert.deepEqual(Object.keys(kept.suggestions), [NEW]);
      }
    });
  }

  test("a stamp the clip no longer has (409 changed_on_disk): nothing converted, the clip read again and the owner told", async (t) => {
    const ctx = await viewWithAClip(t);
    await open(ctx, CLIP);
    await fails(ctx);
    ctx.server.first("/api/clip/convert", { success: false, error: "This video changed on disk.", changed_on_disk: true }, { status: 409 });
    const read = ctx.photosAsked.length;
    el(ctx, "btn-clip-convert").click();
    assert.ok(await ctx.until(() => /changed on disk since it was opened/.test(status(ctx))), `said (${status(ctx)})`);
    assert.ok(ctx.photosAsked.length > read, "the clip is read again");
    assert.equal(callsTo(ctx, "/api/clip/converted").length, 0, "no ticket is read");
    assert.equal(el(ctx, "btn-clip-convert").disabled, false);
  });
});
