/**
 * What the page does with suggestions a Suggest that only looked found (findings #545 and #547).
 *
 * They are kept in the server's memory, for a while, not in the library. When the owner adds the folder
 * the library holds it and the next Suggest must be the run that saves; when the server has let the
 * analysis go the page says so and clears the copy it held. Names are fictional.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "\\\\harbour-nas\\photos\\2019\\Lighthouse Trip";
const A = `${FOLDER}\\IMG_0001.jpg`;
const B = `${FOLDER}\\IMG_0002.jpg`;
const photos = () => [photoRecord({ path: A, filename: "IMG_0001.jpg" }), photoRecord({ path: B, filename: "IMG_0002.jpg" })];

const NOT_HELD = {
  library: "kr-track", folder: FOLDER, photos: 2, photos_held: 0, photos_not_held: 2,
  folders_not_held: 1, first_not_held: FOLDER, has_roots: true, under_roots: false, ignored: false,
};
const HELD = { ...NOT_HELD, photos_held: 2, photos_not_held: 0, folders_not_held: 0, first_not_held: null };
const offer = { tags: [{ tag: "Trips/Lighthouse", score: 0.9 }], people: [], title: null };
const OFFERS = { [A]: offer, [B]: offer };

/** A server whose library holds the folder once it is added (`state.held`), and whose memory of a look is dropped then. */
function server(state) {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["kr-track"] })
    .on("/api/folder/membership", () => (state.held ? HELD : NOT_HELD))
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/add", () => {
      state.held = true;
      state.look = null;   // the server drops the look when the folder is added (tagpup.jobs.indexing)
      return { success: true, status: "running", library: "kr-track", folder: FOLDER, added: 1, queued: [FOLDER],
        already_queued: [], invalid: [], pending: 1 };
    })
    .on("/api/folder/suggest-start", () => {
      state.look = state.held ? null : OFFERS;
      state.saved = state.held ? OFFERS : state.saved;
      return { success: true, status: "running", in_memory: !state.held };
    })
    .on("/api/folder/suggest-status", () => {
      if (state.look) return { status: "completed", completed: 2, total: 2, in_memory: true, suggestions: state.look };
      if (state.saved) return { status: "completed", completed: 2, total: 2, suggestions: state.saved };
      return { status: "idle" };
    })
    .on("/api/folder/auto-apply", () => state.look || state.saved ? { success: true, written: {}, file_only: 0, with_rows: 1 }
      : { success: false, error: "No suggestions found for this folder" }, {})
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/scan", () => photos());
}

async function open(t, state) {
  const s = server(state);
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: s });
  ctx.window.alert = () => {};
  ctx.window.confirm = () => true;
  await openFolder(ctx, FOLDER);
  ctx.$ = (id) => ctx.document.getElementById(id);
  ctx.posts = (route) => s.calls.filter((c) => c.method === "POST" && c.url.includes(route));
  ctx.s = s;
  return ctx;
}

const cachedSuggestions = (ctx) => {
  for (let i = 0; i < ctx.window.localStorage.length; i++) {
    const key = ctx.window.localStorage.key(i);
    if (key.startsWith("tagpup_cache_")) return JSON.parse(ctx.window.localStorage.getItem(key)).suggestions || {};
  }
  return null;
};

describe("a folder looked at, then added", () => {
  test("Suggest is enabled at once, and the next start is the run that saves", async (t) => {
    const state = { held: false, look: null, saved: null };
    const ctx = await open(t, state);
    click(ctx.window, ctx.$("btn-just-look"));
    await flush(ctx.window);
    click(ctx.window, ctx.$("btn-suggest-tags"));
    await flush(ctx.window, 12);
    assert.equal(ctx.posts("/api/folder/suggest-start").length, 1);
    assert.ok(ctx.$("btn-suggest-tags").disabled, "everything is suggested for: nothing to ask for while just looking");

    // Add: the library holds the folder, and what is on the page was only in memory.
    click(ctx.window, ctx.$("btn-add-folder-from-note"));
    await flush(ctx.window, 12);
    assert.equal(ctx.posts("/api/folder/add").length, 1);
    assert.ok(!ctx.document.body.classList.contains("just-looking"));
    assert.ok(!ctx.$("btn-suggest-tags").disabled, "Suggest stayed off after the folder was added");

    // Indexing done, the folder scanned again: still enabled, and the click starts the run that saves.
    await openFolder(ctx, FOLDER);
    await flush(ctx.window, 8);
    assert.ok(!ctx.$("btn-suggest-tags").disabled);
    click(ctx.window, ctx.$("btn-suggest-tags"));
    await flush(ctx.window, 12);
    const starts = ctx.posts("/api/folder/suggest-start");
    assert.equal(starts.length, 2, "Suggest was not started again");
    assert.ok(state.saved, "the second start was not the run that saves");
    assert.equal(state.look, null);
    // What it found is in the library: nothing left to ask for, and no longer marked as in memory.
    assert.ok(ctx.$("btn-suggest-tags").disabled, "Suggest stays on after a run that saved");
  });
});

describe("the server let the analysis go", () => {
  async function lookedAt(t) {
    const state = { held: false, look: null, saved: null };
    const ctx = await open(t, state);
    click(ctx.window, ctx.$("btn-just-look"));
    await flush(ctx.window);
    click(ctx.window, ctx.$("btn-suggest-tags"));
    await flush(ctx.window, 12);
    assert.deepEqual(Object.keys(cachedSuggestions(ctx) || {}), [A, B], "the page did not keep what it was shown");
    return { ctx, state };
  }

  test("Apply All says so, forgets the copy it held, and enables Suggest", async (t) => {
    const { ctx, state } = await lookedAt(t);
    state.look = null;   // idle release
    ctx.document.querySelectorAll(".thumbnail-card .thumbnail-checkbox")[1].click();
    ctx.$("btn-folder-auto-apply").disabled = false;
    click(ctx.window, ctx.$("btn-folder-auto-apply"));
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/folder/auto-apply").length, 1);
    assert.match(ctx.$("status-text").textContent, /The analysis was let go after a while; run Suggest again/);
    assert.deepEqual(cachedSuggestions(ctx), {}, "the copy in this browser's cache stayed");
    assert.ok(!ctx.$("btn-suggest-tags").disabled, "Suggest stayed off");
    assert.ok(ctx.$("btn-folder-auto-apply").disabled);
  });

  test("a status of idle for a folder that showed in-memory suggestions says so too", async (t) => {
    const { ctx, state } = await lookedAt(t);
    state.look = null;
    // Away and back: the folder opens from this browser's cache, and the page asks the status as it opens.
    await openFolder(ctx, `${FOLDER}2`, { settle: 10 });
    await openFolder(ctx, FOLDER, { settle: 14 });
    assert.match(ctx.$("status-text").textContent, /The analysis was let go after a while; run Suggest again/);
    assert.deepEqual(cachedSuggestions(ctx), {});
    assert.ok(!ctx.$("btn-suggest-tags").disabled);
  });
});
