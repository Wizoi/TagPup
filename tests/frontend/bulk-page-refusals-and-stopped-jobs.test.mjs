/**
 * The 9d-2 page review's findings (#605-#609), each as the owner meets it: a refusal that is always said, a stopped job found on
 * opening that changes nothing, a job that took more photos than were picked, a tally that follows an edit of the details panel,
 * and the small things (Shift Date Taken asks once, paths compared as paths, the live region outside the hidden strip).
 * Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, jobStatus, recordOf, speedUp } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const el = (ctx, id) => ctx.document.getElementById(id);
const calls = (ctx, part) => ctx.server.calls.filter((call) => call.url.includes(part));
const text = (ctx, id) => el(ctx, id).textContent.replace(/\s+/g, " ").trim();
const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));

async function view(t, n = 400) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: range(n) });
  speedUp(ctx);
  ctx.questions = [];
  ctx.window.confirm = (question) => { ctx.questions.push(question); return true; };
  ctx.window.alert = () => {};
  ctx.bulk.start = { total: n };
  ctx.bulk.status = jobStatus({ total: n });
  return ctx;
}

async function addTag(ctx, name = "Trips/Lighthouse") {
  el(ctx, "bulk-add-tags-input").value = name;
  click(ctx.window, el(ctx, "btn-bulk-add-tags"));
  await ctx.settle(60);
}

const SENTENCE = "A bulk edit is already running in photo_index (bulk tags; 40 of 100 photos done). Wait for it to finish, or cancel it.";

describe("#605: a refusal is always said", () => {
  test("Add tag, done, Add again, and the server refuses: the sentence is in the strip and the status line", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.start = { total: 1 };
    pick(ctx, 2);
    await addTag(ctx);
    ctx.bulk.status = jobStatus({ total: 1, done: 1, changed: 1, state: "done", finished: 1760000100 });
    await ctx.settle(80);
    assert.ok(!el(ctx, "btn-bulk-dismiss").classList.contains("hidden"), "the finished job is still in the strip");
    ctx.server.first("/api/library/bulk/start", { error: SENTENCE }, { status: 409 });
    await addTag(ctx);
    assert.equal(calls(ctx, "/bulk/start").length, 2);
    assert.match(text(ctx, "bulk-strip-message"), /already running in photo_index/);
    assert.match(text(ctx, "status-text"), /already running in photo_index/);
    assert.ok(!el(ctx, "btn-bulk-show").classList.contains("hidden"), "a way to the one that runs");
  });

  test("Show it when nothing runs any more says so, rather than letting the strip vanish", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    ctx.server.first("/api/library/bulk/start", { error: SENTENCE }, { status: 409 });
    await addTag(ctx);
    ctx.bulk.current = null;
    click(ctx.window, el(ctx, "btn-bulk-show"));
    await ctx.settle(60);
    assert.ok(el(ctx, "bulk-strip").classList.contains("hidden"));
    assert.match(text(ctx, "status-text"), /No bulk edit is running now/);
  });
});

describe("#606: a stopped job found on opening changes nothing", () => {
  test("the caches, the navigator and the status line are left alone; a job that ends under the page still acts", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(50) });
    ctx.window.localStorage.setItem("tagpup_cache_d:/library/2020", JSON.stringify({ timestamp: 1760000200000 }));
    const navigator = ctx.navigatorAsked.length;
    const before = text(ctx, "status-text");
    ctx.bulk.current = jobStatus({ total: 60, done: 25, changed: 25, state: "abandoned", finished: 1760000100, message: "TagPup was closed before this finished: 25 of 60 photos were done." });
    await ctx.module("bulk-job.js").attachBulk({ force: true });
    await ctx.settle(60);
    assert.ok(!el(ctx, "bulk-strip").classList.contains("hidden"), "shown");
    assert.match(text(ctx, "bulk-strip-message"), /closed before this finished: 25 of 60/);
    assert.equal(ctx.window.localStorage.getItem("tagpup_cache_d:/library/2020"), JSON.stringify({ timestamp: 1760000200000 }), "no folder scan was forgotten");
    assert.equal(ctx.navigatorAsked.length, navigator, "the navigator's counts were not read again");
    assert.equal(text(ctx, "status-text"), before, "no error in the status line");
  });
});

describe("#607: a job that took more photos than were picked is said", () => {
  test("50,000 picked of 68,000 (sent the other way round); the server took 50,500: the owner is told and Cancel is there", async (t) => {
    const ctx = await view(t, 68000);
    ctx.module("selected.js").setIdRange(0, 49999, true);
    assert.equal(ctx.module("selected.js").selectionRequest().body.source.kind, "all", "sent as the source and what is left out");
    ctx.bulk.start = { total: 50500 };
    ctx.bulk.status = jobStatus({ total: 50500 });
    await addTag(ctx);
    assert.equal(calls(ctx, "/bulk/start").length, 1);
    assert.match(text(ctx, "bulk-strip-message"), /50,500 photos.*50,000 were selected.*500 more/);
    assert.match(text(ctx, "status-text"), /50,500 photos.*50,000 were selected/);
    assert.ok(!el(ctx, "btn-bulk-cancel").classList.contains("hidden"));
    await ctx.settle(60);
    assert.match(text(ctx, "bulk-strip-message"), /500 more/, "a poll does not wipe it");
  });

  test("the server taking the same number, or fewer (photos gone), says nothing", async (t) => {
    const ctx = await view(t, 68000);
    ctx.module("selected.js").setIdRange(0, 49999, true);
    ctx.bulk.start = { total: 49990, missing: 10 };
    ctx.bulk.status = jobStatus({ total: 49990 });
    await addTag(ctx);
    assert.equal(text(ctx, "bulk-strip-message"), "");
  });
});

describe("#608: the tally follows an edit of the details panel", () => {
  test("a write that finished counts the selection again", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.tally = { total: 1, tags: [{ tag: "Trips/Coast", count: 1 }], more_tags: 0, people: [], more_people: 0 };
    pick(ctx, 2);
    await ctx.settle(700);
    const asked = calls(ctx, "/selection/tally").length;
    assert.equal(asked, 1);
    ctx.bulk.tally = { total: 1, tags: [{ tag: "Trips/Coast", count: 1 }, { tag: "Trips/Lighthouse", count: 1 }], more_tags: 0, people: [], more_people: 0 };
    await ctx.module("edits.js").queuePhotoWrite(async () => true);
    await ctx.settle(700);
    assert.equal(calls(ctx, "/selection/tally").length, asked + 1, "counted again");
    assert.match(text(ctx, "selection-tags-list"), /Trips\/Lighthouse/);
  });

  test("a write that failed changes nothing", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    await ctx.settle(700);
    const asked = calls(ctx, "/selection/tally").length;
    await ctx.module("edits.js").queuePhotoWrite(async () => false);
    await ctx.settle(700);
    assert.equal(calls(ctx, "/selection/tally").length, asked);
  });
});

describe("#609: the small things", () => {
  test("the open photo is found by path, as a path: another spelling of it is still the photo", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.start = { total: 1 };
    pick(ctx, 2);
    await addTag(ctx);
    const record = recordOf(2);
    ctx.state.folderPhotos = [record];
    ctx.state.activePhotoPath = record.path.replace(/\\/g, "/").toLowerCase();
    ctx.photosAsked.length = 0;
    ctx.bulk.status = jobStatus({ total: 1, done: 1, changed: 1, state: "done", finished: 1760000100 });
    await ctx.settle(80);
    assert.ok(ctx.photosAsked.includes(2), "the open photo is read again");
  });

  test("the live region is not inside the strip that is hidden", async (t) => {
    const ctx = await view(t, 400);
    assert.ok(!el(ctx, "bulk-strip").contains(el(ctx, "bulk-strip-live")));
    assert.equal(el(ctx, "bulk-strip-live").getAttribute("role"), "status");
  });
});

describe("the follow-up review (#613-#617)", () => {
  async function widened(t) {
    const ctx = await view(t, 68000);
    ctx.module("selected.js").setIdRange(0, 49999, true);
    ctx.bulk.start = { total: 50500 };
    ctx.bulk.status = jobStatus({ total: 50500 });
    await addTag(ctx);
    return ctx;
  }

  test("#613: Start again repeats the notice that the job took more than was picked", async (t) => {
    const ctx = await widened(t);
    ctx.bulk.status = jobStatus({ total: 50500, done: 100, changed: 100, state: "abandoned", message: "TagPup was closed before this finished." });
    await ctx.settle(80);
    assert.ok(!el(ctx, "btn-bulk-again").classList.contains("hidden"));
    ctx.bulk.status = jobStatus({ total: 50500, job: 8 });
    ctx.bulk.start = { job: 8, total: 50500 };
    click(ctx.window, el(ctx, "btn-bulk-again"));
    await ctx.settle(80);
    assert.equal(calls(ctx, "/bulk/start").length, 2);
    assert.match(text(ctx, "bulk-strip-message"), /500 more/);
  });

  test("#614: a refusal that is not 'another is running' is not titled so and offers no Show it", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    ctx.server.first("/api/library/bulk/start", { error: "photo_index has not been brought up to date yet: open it in TagPup once and try again." }, { status: 409 });
    await addTag(ctx);
    assert.equal(text(ctx, "bulk-strip-title"), "The edit did not start");
    assert.match(text(ctx, "bulk-strip-message"), /not been brought up to date/);
    assert.ok(el(ctx, "btn-bulk-show").classList.contains("hidden"));
    assert.ok(!el(ctx, "btn-bulk-dismiss").classList.contains("hidden"));
  });

  test("#614: the running-job refusal keeps its title and Show it", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    ctx.server.first("/api/library/bulk/start", { error: SENTENCE }, { status: 409 });
    await addTag(ctx);
    assert.equal(text(ctx, "bulk-strip-title"), "Another bulk edit is running");
    assert.ok(!el(ctx, "btn-bulk-show").classList.contains("hidden"));
  });

  test("#615: after the job ends the notice is information: no Cancel clause, and a done job with 0 errors is no problem", async (t) => {
    const ctx = await widened(t);
    assert.match(text(ctx, "bulk-strip-message"), /Cancel stops it/);
    assert.ok(el(ctx, "bulk-strip-message").classList.contains("bulk-strip-problem"), "while it runs it asks for a look");
    ctx.bulk.status = jobStatus({ total: 50500, done: 50500, changed: 50500, state: "done", finished: 1760000100 });
    await ctx.settle(80);
    assert.match(text(ctx, "bulk-strip-message"), /500 more/);
    assert.doesNotMatch(text(ctx, "bulk-strip-message"), /Cancel/);
    assert.ok(!el(ctx, "bulk-strip-message").classList.contains("bulk-strip-problem"));
  });

  test("#616: a start whose answer was lost and whose job stopped: the status line says how it ended; only older folder scans are forgotten", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    ctx.window.localStorage.setItem("tagpup_cache_d:/old", JSON.stringify({ timestamp: 1760000000000 }));
    ctx.window.localStorage.setItem("tagpup_cache_d:/new", JSON.stringify({ timestamp: 1760000200000 }));
    ctx.server.first("/api/library/bulk/start", () => Promise.reject(new Error("Failed to fetch")));
    ctx.bulk.current = jobStatus({ total: 100, done: 3, changed: 3, state: "cancelled", started: Math.floor(Date.now() / 1000), finished: 1760000100, message: "The bulk edit was cancelled." });
    await addTag(ctx);
    await ctx.settle(100);
    assert.match(text(ctx, "status-text"), /The bulk edit was cancelled\./);
    assert.equal(ctx.window.localStorage.getItem("tagpup_cache_d:/old"), null);
    assert.notEqual(ctx.window.localStorage.getItem("tagpup_cache_d:/new"), null, "a scan kept after the job is current");
    assert.match(text(ctx, "bulk-strip-message"), /cancelled/);
  });

  test("#616: a stopped job found on opening forgets only the scans older than its end, and says nothing in the status line", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: range(50) });
    ctx.window.localStorage.setItem("tagpup_cache_d:/old", JSON.stringify({ timestamp: 1760000000000 }));
    ctx.window.localStorage.setItem("tagpup_cache_d:/new", JSON.stringify({ timestamp: 1760000200000 }));
    const before = text(ctx, "status-text");
    ctx.bulk.current = jobStatus({ total: 60, done: 25, changed: 25, state: "abandoned", finished: 1760000100, message: "closed." });
    await ctx.module("bulk-job.js").attachBulk({ force: true });
    assert.equal(ctx.window.localStorage.getItem("tagpup_cache_d:/old"), null);
    assert.notEqual(ctx.window.localStorage.getItem("tagpup_cache_d:/new"), null);
    assert.equal(text(ctx, "status-text"), before);
  });

  test("#616: the status line does not promise that a finished edit shows", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    ctx.server.first("/api/library/bulk/start", () => Promise.reject(new Error("Failed to fetch")));
    await addTag(ctx);
    assert.match(text(ctx, "status-text"), /shows here while it runs/);
    assert.match(text(ctx, "status-text"), /look at the photos/);
  });

  test("#617: the live region is told of a 409 and of a widened job", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    ctx.server.first("/api/library/bulk/start", { error: SENTENCE }, { status: 409 });
    await addTag(ctx);
    assert.match(text(ctx, "bulk-strip-live"), /already running in photo_index/);
    const big = await widenedLive(t);
    assert.match(text(big, "bulk-strip-live"), /500 more/);
  });
});

async function widenedLive(t) {
  const ctx = await view(t, 68000);
  ctx.module("selected.js").setIdRange(0, 49999, true);
  ctx.bulk.start = { total: 50500 };
  ctx.bulk.status = jobStatus({ total: 50500 });
  await addTag(ctx);
  return ctx;
}
