/**
 * A folder's clips in its grid and its panel (video, merge B2): the folder scan lists a clip beside the photos with
 * `media: "video"`, and a clip the index has recorded with its `id` and `thumb` (its poster by id). A clip's picture is
 * ONLY its poster through /api/photo-thumb -- /api/photo-file refuses a video -- and a clip the index has not recorded
 * is a grey card with the play mark, asking for nothing, until the folder is scanned again after the index records it.
 * A folder's card is selected by its path, a clip's too (a folder view's selection is by path, not by id, whatever
 * picture the card shows). Opening a recorded clip plays it from /api/video-file by its id, as a library view's does;
 * opening one not recorded says so and asks for nothing. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, pageExports } from "./harness.mjs";

const FOLDER = "D:\\Pictures\\Lighthouse";
const record = (name, extra = {}) => ({
  path: `${FOLDER}\\${name}`, filename: name, tags: [], people: [], captions: [], title: "", mtime: 1000,
  media: "photo", ...extra,
});
const SCAN = [
  record("dawn.jpg"),
  record("dusk.jpg"),
  // A clip of the same base name as a photo, recorded by the index: its poster by id.
  record("dusk.mp4", { media: "video", id: 41, thumb: "/api/photo-thumb?id=41&v=1000.0" }),
  // A clip the index has not recorded yet.
  record("noon.mov", { media: "video" }),
];

async function folderWithClips(t) {
  const server = new FakeServer()
    .on("/api/folder/scan", SCAN.map((each) => ({ ...each })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
  const ctx = await loadApp("tagpup", { server, t });
  ctx.window.alert = () => {};
  ctx.window.HTMLMediaElement.prototype.pause = () => {};
  ctx.window.HTMLMediaElement.prototype.load = () => {};
  await openFolder(ctx, FOLDER);
  await new Promise((r) => ctx.window.setTimeout(r, 60));
  ctx.server = server;
  return ctx;
}

const card = (ctx, name) => ctx.document.querySelector(
  `#thumbnails-grid .thumbnail-card[data-path="${CSS_PATH(`${FOLDER}\\${name}`)}"]`);
const CSS_PATH = (path) => path.replace(/\\/g, "\\\\");
const picture = (ctx, name) => card(ctx, name).querySelector(".thumbnail-img-wrapper img");
const asked = (img) => img.getAttribute("src") || img.dataset.src || "";
const wait = (ctx, ms = 60) => new Promise((r) => ctx.window.setTimeout(r, ms));

describe("a folder's clips in its grid", () => {
  afterEach(() => closeAllApps());

  test("a recorded clip is drawn by its poster by id, never through /api/photo-file", async (t) => {
    const ctx = await folderWithClips(t);
    const clip = picture(ctx, "dusk.mp4");
    assert.match(asked(clip), /\/api\/photo-thumb\?id=41&v=1000\.0$/, "the clip's poster, by its id");
    assert.ok(card(ctx, "dusk.mp4").querySelector(".thumbnail-play-mark"), "the clip's card carries the play mark");
    assert.match(asked(picture(ctx, "dusk.jpg")), /\/api\/photo-file\?/, "the photo of the same base name is its file");
    assert.equal(card(ctx, "dusk.jpg").querySelector(".thumbnail-play-mark"), null, "a photo has no play mark");
    const fileOfAClip = ctx.server.urls().filter((url) => url.includes("photo-file") && /\.(mp4|mov)/.test(decodeURIComponent(url)));
    assert.deepEqual(fileOfAClip, [], "no clip was asked of /api/photo-file");
  });

  test("a clip the index has not recorded is a placeholder with the play mark, asking for nothing", async (t) => {
    const ctx = await folderWithClips(t);
    assert.ok(card(ctx, "noon.mov").querySelector(".thumbnail-play-mark"), "the play mark");
    const img = picture(ctx, "noon.mov");
    assert.equal(asked(img), "", "no picture is asked for");
    assert.ok(img.classList.contains("failed"), "drawn as the grey card a picture that cannot be had is");
  });

  test("a folder's clip is selected by its path, as a folder's photo is", async (t) => {
    const ctx = await folderWithClips(t);
    const box = card(ctx, "dusk.mp4").querySelector(".thumbnail-checkbox");
    box.dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    await wait(ctx, 20);
    assert.equal(card(ctx, "dusk.mp4").dataset.id, undefined, "not a library card");
    assert.ok(card(ctx, "dusk.mp4").classList.contains("selected"), "the clip's card is selected");
    assert.deepEqual([...ctx.window.__pageModules["web/tagpup/state.js"].state.selectedThumbnails], [`${FOLDER}\\dusk.mp4`],
      "by its path");
  });
});

describe("a folder's clip in the panel", () => {
  afterEach(() => closeAllApps());

  const open = async (ctx, name) => {
    pageExports(ctx.window, "web/tagpup/photo.js").selectPhoto(`${FOLDER}\\${name}`);
    await wait(ctx);
  };

  test("a recorded clip plays from /api/video-file by its id", async (t) => {
    const ctx = await folderWithClips(t);
    await open(ctx, "dusk.mp4");
    const src = ctx.document.getElementById("main-video").getAttribute("src") || "";
    assert.match(src, /\/api\/video-file\?id=41&/, "played by its id");
    assert.equal(ctx.document.getElementById("main-image").getAttribute("src"), "", "the image asks for nothing");
  });

  test("a clip the index has not recorded says so and asks for nothing", async (t) => {
    const ctx = await folderWithClips(t);
    await open(ctx, "noon.mov");
    assert.equal(ctx.document.getElementById("main-video").getAttribute("src"), null, "no video is asked for");
    assert.equal(ctx.document.getElementById("main-image").getAttribute("src"), "", "nor its file as a picture");
    assert.match(ctx.document.getElementById("status-text").textContent, /not been indexed/);
    const fileOfAClip = ctx.server.urls().filter((url) => /photo-file|video-file/.test(url) && url.includes("noon"));
    assert.deepEqual(fileOfAClip, []);
  });
});
