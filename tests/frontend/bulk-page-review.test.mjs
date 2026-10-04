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
    ctx.window.localStorage.setItem("tagpup_cache_d:/library/2020", "{}");
    const navigator = ctx.navigatorAsked.length;
    const before = text(ctx, "status-text");
    ctx.bulk.current = jobStatus({ total: 60, done: 25, changed: 25, state: "abandoned", message: "TagPup was closed before this finished: 25 of 60 photos were done." });
    await ctx.module("bulk-job.js").attachBulk({ force: true });
    await ctx.settle(60);
    assert.ok(!el(ctx, "bulk-strip").classList.contains("hidden"), "shown");
    assert.match(text(ctx, "bulk-strip-message"), /closed before this finished: 25 of 60/);
    assert.equal(ctx.window.localStorage.getItem("tagpup_cache_d:/library/2020"), "{}", "no folder scan was forgotten");
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
  test("Shift Date Taken does not start while a question or placement dialog of another edit is open", async (t) => {
    const ctx = await view(t, 400);
    pick(ctx, 2);
    click(ctx.window, el(ctx, "btn-toggle-timeshift"));
    el(ctx, "timeshift-minutes-input").value = "30";
    ctx.state.bulk.asking = true;
    click(ctx.window, el(ctx, "btn-apply-timeshift"));
    await ctx.settle(60);
    assert.equal(ctx.questions.length, 0, "nothing asked");
    assert.equal(calls(ctx, "/bulk/start").length, 0);
    ctx.state.bulk.asking = false;
    click(ctx.window, el(ctx, "btn-apply-timeshift"));
    await ctx.settle(60);
    assert.equal(calls(ctx, "/bulk/start").length, 1, "and when nothing is open it starts");
  });

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
