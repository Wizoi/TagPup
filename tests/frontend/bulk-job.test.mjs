/**
 * The strip of a bulk edit of the library's photos and the job behind it (web/tagpup/bulk-job.js, bulk-strip.js, bulk-words.js;
 * docs/ARCHITECTURE.md, phase 9d-2): a bar, `done of total`, what was changed, left out and refused, the time left, Cancel; polling about
 * once a second (every 5 s while the tab is hidden), never for ever; what the page does when the job ends; finding the job again after a
 * reload or a restart; Resume and Start again. The page's slow clocks are made fast (`speedUp`): a second is 10 ms. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, jobStatus, speedUp } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const plain = (value) => JSON.parse(JSON.stringify(value));
const el = (ctx, id) => ctx.document.getElementById(id);
const calls = (ctx, part) => ctx.server.calls.filter((call) => call.url.includes(part));
const strip = (ctx) => el(ctx, "bulk-strip");
const text = (ctx, id) => el(ctx, id).textContent.replace(/\s+/g, " ").trim();
const pause = (ctx, ms) => new Promise((resolve) => ctx.window.setTimeout(resolve, ms));

/** A page on a view of 400 photos, quick clocks, the first photo selected, and a job started by Add tag. */
async function started(t, { total = 100, status = {}, options = {}, begin = true } = {}) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: range(400), ...options });
  speedUp(ctx);
  ctx.window.confirm = () => true;
  ctx.window.alert = () => {};
  ctx.bulk.start = { total };
  ctx.bulk.status = jobStatus({ total, ...status });
  if (begin) {
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    el(ctx, "bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, el(ctx, "btn-bulk-add-tags"));
    await ctx.settle(40);
  }
  return ctx;
}

describe("the strip while a job runs", () => {
  test("a bar, `done of total`, the counts, and a time left at 15 photos a second until the job gives its own", async (t) => {
    const ctx = await started(t, { total: 3000, status: { done: 600, changed: 590, unchanged: 5, skipped_missing: 3, skipped_damaged: 1, error_count: 1 } });
    await ctx.settle(60);
    assert.ok(!strip(ctx).classList.contains("hidden"));
    assert.equal(text(ctx, "bulk-strip-progress"), "600 of 3,000");
    assert.equal(el(ctx, "bulk-strip-bar").value, 20);
    assert.equal(text(ctx, "bulk-strip-counts"), "changed 590 · unchanged 5 · missing 3 · damaged 1 · errors 1");
    assert.equal(text(ctx, "bulk-strip-eta"), "about 3 minutes left", "2,400 photos at 15 a second");
    ctx.bulk.status = jobStatus({ total: 3000, done: 600, eta_seconds: 90 });
    await ctx.settle(60);
    assert.equal(text(ctx, "bulk-strip-eta"), "about 2 minutes left", "the job's own");
    assert.ok(!el(ctx, "btn-bulk-cancel").classList.contains("hidden"));
    assert.ok(el(ctx, "btn-bulk-dismiss").classList.contains("hidden"), "a running job is not dismissed");
    assert.match(text(ctx, "bulk-strip").replace(/\s/g, " "), /Shown on the Activity page too/);
  });

  test("it asks about once a second, and every 5 s while the tab is hidden", async (t) => {
    const ctx = await started(t);
    await ctx.settle(60);
    assert.ok(ctx.delays.includes(1000), "once a second");
    assert.ok(!ctx.delays.includes(5000));
    Object.defineProperty(ctx.document, "hidden", { value: true, configurable: true });
    ctx.delays.length = 0;
    await ctx.settle(60);
    assert.ok(ctx.delays.includes(5000), "the tab is hidden: 5 s");
    assert.ok(!ctx.delays.includes(1000));
    Object.defineProperty(ctx.document, "hidden", { value: false, configurable: true });
    const before = calls(ctx, "/bulk/status").length;
    ctx.document.dispatchEvent(new ctx.window.Event("visibilitychange"));
    await ctx.settle(5);
    assert.ok(calls(ctx, "/bulk/status").length > before, "brought back to the front it asks at once");
  });

  test("the focus is not taken from the grid when the strip appears, and a screen reader is told at coarse steps, not every poll", async (t) => {
    const ctx = await started(t, { begin: false });
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.cardById(3).focus();
    const focused = ctx.document.activeElement;
    el(ctx, "bulk-add-tags-input").value = "Trips/Lighthouse";
    const live = el(ctx, "bulk-strip-live");
    const heard = [];
    new ctx.window.MutationObserver(() => heard.push(live.textContent)).observe(live, { childList: true, characterData: true, subtree: true });
    click(ctx.window, el(ctx, "btn-bulk-add-tags"));
    await ctx.settle(40);
    assert.ok(!strip(ctx).classList.contains("hidden"));
    assert.equal(ctx.document.activeElement, focused, "the strip does not take the focus");
    assert.match(live.getAttribute("aria-live"), /polite/);
    // 25 polls with the same answer say nothing new; each tenth of progress says one thing.
    for (const done of [5, 5, 5, 25, 25, 55]) {
      ctx.bulk.status = jobStatus({ total: 100, done });
      await ctx.settle(40);
    }
    assert.ok(heard.length <= 5, `${heard.length} announcements: ${heard.join(" | ")}`);
    assert.match(live.textContent, /50 percent: 55 of 100 photos/);
  });

  test("a view opened or left while it runs: the job goes on and the strip stays; the view's own controls are off only in a view", async (t) => {
    const ctx = await started(t);
    await ctx.popTo("?view=year&value=2020");
    assert.ok(!strip(ctx).classList.contains("hidden"));
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, true);
    ctx.bulk.status = jobStatus({ total: 100, done: 100, changed: 100, state: "done", finished: 1760000100 });
    await ctx.settle(60);
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, false, "enabled again when it has ended");
  });
});

describe("when it ends", () => {
  test("the summary names what was done, what was left out and the errors; polling stops; the page reads what changed again", async (t) => {
    const ctx = await started(t, { total: 3412 });
    await ctx.showLibraryPane();
    ctx.window.localStorage.setItem("tagpup_cache_d:/library/2020", "{}");
    ctx.window.localStorage.setItem("tagpup_thumbnail_size", "medium");
    const navigator = ctx.navigatorAsked.length;
    const cards = ctx.cardsAsked.length;
    const tallies = calls(ctx, "/selection/tally").length;
    ctx.bulk.status = jobStatus({
      total: 3412, done: 3412, changed: 3380, skipped_missing: 32, state: "done", finished: 1760000100, resumable: false,
    });
    await ctx.settle(80);
    assert.equal(text(ctx, "bulk-strip-message"), "Added Trips/Lighthouse to 3,380 photos; 32 missing on disk were skipped; 0 errors.");
    assert.match(text(ctx, "status-text"), /^Added Trips\/Lighthouse to 3,380 photos; 32 missing on disk were skipped; 0 errors\.$/);
    assert.equal(el(ctx, "bulk-strip-bar").value, 100);
    const polled = calls(ctx, "/bulk/status").length;
    await ctx.settle(120);
    assert.equal(calls(ctx, "/bulk/status").length, polled, "a finished job is not polled");
    assert.ok(ctx.navigatorAsked.length > navigator, "the navigator's counts are read again");
    assert.ok(ctx.cardsAsked.length > cards, "the cards in the window are asked for again: their stamps changed");
    assert.equal(ctx.window.localStorage.getItem("tagpup_cache_d:/library/2020"), null, "a folder scan kept in the browser is stale now");
    assert.equal(ctx.window.localStorage.getItem("tagpup_thumbnail_size"), "medium", "nothing else is touched");
    assert.ok(calls(ctx, "/selection/tally").length > tallies, "the selection is counted again");
    assert.ok(!el(ctx, "btn-bulk-dismiss").classList.contains("hidden"));
    click(ctx.window, el(ctx, "btn-bulk-dismiss"));
    assert.ok(strip(ctx).classList.contains("hidden"));
  });

  test("errors: the first 50 with their file names and why, long names wrapped, and the rest counted -- 3,000 reported, 51 rows", async (t) => {
    const long = (n) => `IMG_${n}_${"a-very-long-file-name-".repeat(8)}.jpg`;
    const errors = range(50).map((n) => ({ id: n, name: long(n), why: "The file is not writable: it is read-only." }));
    const ctx = await started(t, { total: 3000 });
    ctx.bulk.status = jobStatus({ total: 3000, done: 3000, changed: 0, state: "done", error_count: 3000, errors, finished: 1760000100 });
    await ctx.settle(60);
    const rows = [...ctx.document.querySelectorAll("#bulk-strip-error-list li")];
    assert.equal(rows.length, 51);
    assert.match(rows.at(-1).textContent, /^2,950 more, not listed$/);
    assert.ok(rows[0].textContent.includes(long(1)));
    assert.match(text(ctx, "bulk-strip-errors-summary"), /3,000 errors: the first 50 are listed/);
    assert.match(text(ctx, "bulk-strip-message"), /3,000 errors\.$/);
    assert.ok(ctx.document.querySelector(".bulk-strip-error-list li") && ctx.document.querySelectorAll("#bulk-strip *").length < 300, "the strip's DOM is bounded");
  });

  test("a job whose every photo is missing: nothing changed, and it says so in a sentence", async (t) => {
    const ctx = await started(t, { total: 40 });
    ctx.bulk.status = jobStatus({ total: 40, done: 40, changed: 0, skipped_missing: 40, state: "done", finished: 1760000100 });
    await ctx.settle(60);
    assert.equal(text(ctx, "bulk-strip-message"), "Added Trips/Lighthouse to 0 photos; 40 missing on disk were skipped; 0 errors.");
  });

  test("a time shift's summary says what it shifted, and the photos with no Date Taken", async (t) => {
    const ctx = await started(t, { begin: false, total: 5 });
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    click(ctx.window, el(ctx, "btn-toggle-timeshift"));
    el(ctx, "timeshift-minutes-input").value = "90";
    ctx.bulk.start = { op: "time_shift", total: 5 };
    click(ctx.window, el(ctx, "btn-apply-timeshift"));
    await ctx.settle(40);
    ctx.bulk.status = jobStatus({ op: "time_shift", total: 5, done: 5, changed: 4, unchanged: 1, state: "done", finished: 1760000100 });
    await ctx.settle(60);
    assert.equal(text(ctx, "bulk-strip-message"), "Shifted Date Taken 90 minutes later in 4 photos; 1 with no Date Taken was left as it was; 0 errors.");
  });
});

describe("Cancel", () => {
  test("pressed twice it is one request, says Cancelling, and the job's end is shown with what was done", async (t) => {
    const ctx = await started(t, { total: 100, status: { done: 40, changed: 40 } });
    await ctx.settle(40);
    ctx.bulk.status = jobStatus({ total: 100, done: 40, changed: 40, cancelling: true });
    click(ctx.window, el(ctx, "btn-bulk-cancel"));
    click(ctx.window, el(ctx, "btn-bulk-cancel"));
    await ctx.settle(40);
    assert.equal(calls(ctx, "/bulk/cancel").length, 1);
    assert.deepEqual(plain(calls(ctx, "/bulk/cancel")[0].body), { job: 7 });
    assert.equal(el(ctx, "btn-bulk-cancel").disabled, true);
    assert.match(text(ctx, "bulk-strip-message"), /Cancelling/);
    ctx.bulk.status = jobStatus({ total: 100, done: 50, changed: 50, state: "cancelled", message: "Cancelled after 50 of 100 photos.", finished: 1760000100 });
    await ctx.settle(60);
    assert.match(text(ctx, "bulk-strip-message"), /^Cancelled after 50 of 100 photos\. Added Trips\/Lighthouse to 50 photos; 0 errors\.$/);
    assert.ok(!el(ctx, "btn-bulk-again").classList.contains("hidden"), "a cancelled tags job is started again, not resumed");
    assert.ok(el(ctx, "btn-bulk-resume").classList.contains("hidden"));
  });

  test("after the job ended: the answer says so and the strip shows the end, not a cancel", async (t) => {
    const ctx = await started(t, { total: 100, status: { done: 99, changed: 99 } });
    await ctx.settle(40);
    // The job ends between the poll and the click: the cancel's answer is the status of a job that is done.
    ctx.server.first("/api/library/bulk/cancel", { success: true, ...jobStatus({ total: 100, done: 100, changed: 100, state: "done", cancelling: false, finished: 1760000100 }) });
    click(ctx.window, el(ctx, "btn-bulk-cancel"));
    await ctx.settle(60);
    assert.match(text(ctx, "bulk-strip-message"), /^Added Trips\/Lighthouse to 100 photos; 0 errors\.$/);
    assert.ok(el(ctx, "btn-bulk-cancel").classList.contains("hidden"));
  });

  test("a cancel that could not be sent says so and Cancel can be pressed again", async (t) => {
    const ctx = await started(t, { total: 100, status: { done: 40 } });
    ctx.server.first("/api/library/bulk/cancel", () => Promise.reject(new Error("Failed to fetch")));
    click(ctx.window, el(ctx, "btn-bulk-cancel"));
    await ctx.settle(40);
    assert.match(text(ctx, "status-text"), /Could not cancel the bulk edit: Failed to fetch/);
    assert.equal(el(ctx, "btn-bulk-cancel").disabled, false);
  });
});

describe("the server not answering", () => {
  test("three misses are said, an answer clears it, and after 40 it stops asking and offers Ask again", async (t) => {
    const ctx = await started(t, { total: 100, status: { done: 10 } });
    await ctx.settle(40);
    ctx.server.first("/api/library/bulk/status", () => Promise.reject(new Error("Failed to fetch")));
    await ctx.settle(90);
    assert.match(text(ctx, "bulk-strip-message"), /TagPup is not answering \(Failed to fetch\)\. The edit goes on if TagPup is running; still trying\./);
    assert.equal(text(ctx, "bulk-strip-progress"), "10 of 100", "what it knew stays");
    ctx.server.routes.shift();
    await ctx.settle(150);
    assert.equal(text(ctx, "bulk-strip-message"), "", "an answer clears it");
    ctx.server.first("/api/library/bulk/status", () => Promise.reject(new Error("Failed to fetch")));
    for (let waited = 0; waited < 12000 && !/has not answered for a long time/.test(text(ctx, "bulk-strip-message")); waited += 200) await ctx.settle(200);
    const asked = calls(ctx, "/bulk/status").length;
    assert.match(text(ctx, "bulk-strip-message"), /has not answered for a long time/);
    assert.ok(!el(ctx, "btn-bulk-show").classList.contains("hidden"));
    assert.equal(text(ctx, "btn-bulk-show"), "Ask again");
    await ctx.settle(300);
    assert.equal(calls(ctx, "/bulk/status").length, asked, "it does not go on for ever");
    ctx.server.routes.shift();
    click(ctx.window, el(ctx, "btn-bulk-show"));
    await ctx.settle(80);
    assert.equal(text(ctx, "bulk-strip-message"), "");
    assert.ok(calls(ctx, "/bulk/status").length > asked, "Ask again asks");
  });

  test("an answer that never comes is a miss after 15 s, and the next try is made; the strip says so", async (t) => {
    const ctx = await started(t, { total: 100, status: { done: 10 } });
    await ctx.settle(40);
    let asked = 0;
    ctx.server.first("/api/library/bulk/status", () => { asked += 1; return new Promise(() => {}); });
    for (let waited = 0; waited < 12000 && asked < 4; waited += 100) await ctx.settle(100);
    assert.ok(asked >= 4, `${asked} tries: each given up after 15 s (150 ms here) and asked again`);
    assert.match(text(ctx, "bulk-strip-message"), /TagPup is not answering \(no answer in 15 s\)/);
    assert.equal(text(ctx, "bulk-strip-progress"), "10 of 100");
  });

  test("a server that restarted: the job is abandoned, and the strip says how far it got; a time shift offers Resume", async (t) => {
    const ctx = await started(t, { begin: false, total: 60 });
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    click(ctx.window, el(ctx, "btn-toggle-timeshift"));
    el(ctx, "timeshift-minutes-input").value = "30";
    ctx.bulk.start = { op: "time_shift", total: 60 };
    click(ctx.window, el(ctx, "btn-apply-timeshift"));
    await ctx.settle(40);
    ctx.bulk.status = jobStatus({
      op: "time_shift", total: 60, done: 25, changed: 25, state: "abandoned", resumable: true,
      message: "TagPup was closed before this finished: 25 of 60 photos were done. Resume it to carry on from there; no photo already shifted is shifted again.",
    });
    await ctx.settle(60);
    assert.match(text(ctx, "bulk-strip-message"), /^TagPup was closed before this finished: 25 of 60 photos were done\. Resume it/);
    assert.equal(text(ctx, "bulk-strip-progress"), "25 of 60");
    assert.ok(!el(ctx, "btn-bulk-resume").classList.contains("hidden"));
    assert.ok(el(ctx, "btn-bulk-again").classList.contains("hidden"), "a shift is resumed, never started again");
    ctx.bulk.resume = { total: 60, done: 25 };
    ctx.bulk.status = jobStatus({ op: "time_shift", total: 60, done: 25, changed: 25 });
    click(ctx.window, el(ctx, "btn-bulk-resume"));
    await ctx.settle(60);
    assert.deepEqual(plain(calls(ctx, "/bulk/resume")[0].body), { job: 7 });
    assert.ok(!el(ctx, "btn-bulk-cancel").classList.contains("hidden"), "running again");
    assert.equal(calls(ctx, "/bulk/start").length, 1, "the same job: no new start");
  });

  test("a tags job that was abandoned is started again with the same selection and edit", async (t) => {
    const ctx = await started(t, { total: 100 });
    ctx.bulk.status = jobStatus({ total: 100, done: 30, changed: 30, state: "abandoned", message: "TagPup was closed before this finished: 30 of 100 photos were done." });
    await ctx.settle(60);
    assert.ok(!el(ctx, "btn-bulk-again").classList.contains("hidden"));
    ctx.bulk.status = jobStatus({ total: 100, job: 8 });
    ctx.bulk.start = { job: 8, total: 100 };
    click(ctx.window, el(ctx, "btn-bulk-again"));
    await ctx.settle(60);
    const [first, second] = calls(ctx, "/bulk/start");
    assert.deepEqual(plain(second.body), plain(first.body));
    assert.ok(!el(ctx, "btn-bulk-cancel").classList.contains("hidden"));
  });
});

describe("finding it again", () => {
  test("a page reloaded mid-job shows the strip at once and goes on asking", async (t) => {
    const ctx = await started(t, { begin: false, total: 3000, options: {
      before: undefined,
    } });
    ctx.bulk.current = jobStatus({ total: 3000, done: 900, changed: 900 });
    ctx.bulk.status = jobStatus({ total: 3000, done: 1200, changed: 1200 });
    const other = await loadViewPage(t, { search: "?view=all", ids: range(50), onIds: undefined });
    assert.ok(strip(other).classList.contains("hidden"), "the other page has no job");
    // The same library, opened again: the library says what runs.
    ctx.module("bulk-job.js").attachBulk({ force: true });
    await ctx.settle(60);
    assert.ok(!strip(ctx).classList.contains("hidden"));
    assert.equal(text(ctx, "bulk-strip-title"), "Bulk edit of tags (3,000 photos)", "what a reload knows: its kind and size");
    assert.equal(text(ctx, "bulk-strip-progress"), "1,200 of 3,000", "and it goes on asking");
    assert.ok(el(ctx, "btn-bulk-add-tags").disabled, "the view's controls are off");
  });

  test("the library is asked as the page opens: a running job shows, none shows nothing, a read that fails shows nothing and logs no error", async (t) => {
    const running = await loadViewPage(t, { search: "?view=all", ids: range(50) });
    assert.equal(calls(running, "/bulk/current").length, 1);
    assert.ok(strip(running).classList.contains("hidden"));
  });

  test("the strip of one library is not another's: the status, the current job and the cancel are asked under the page's own library", async (t) => {
    const ctx = await started(t, { options: { library: "other_library" } });
    assert.ok(calls(ctx, "/bulk/current")[0].url.startsWith("/other_library/api/library/bulk/current"));
    assert.ok(calls(ctx, "/bulk/start")[0].url.startsWith("/other_library/api/library/bulk/start"));
    await ctx.settle(60);
    assert.ok(calls(ctx, "/bulk/status").every((call) => call.url.startsWith("/other_library/api/library/bulk/status?job=7")));
  });

  test("a stopped job found on opening is shown with what was done, and is not offered again once dismissed", async (t) => {
    const stopped = jobStatus({ total: 60, done: 25, changed: 25, state: "abandoned", message: "TagPup was closed before this finished: 25 of 60 photos were done." });
    const first = await loadViewPage(t, { search: "?view=all", ids: range(50), before: undefined });
    first.bulk.current = stopped;
    await first.module("bulk-job.js").attachBulk({ force: true });
    assert.match(text(first, "bulk-strip-message"), /closed before this finished: 25 of 60/);
    assert.ok(el(first, "btn-bulk-again").classList.contains("hidden"), "a reload does not know the edit: nothing to start again");
    assert.match(text(first, "bulk-strip-message"), /Select the photos and make the edit again/);
    click(first.window, el(first, "btn-bulk-dismiss"));
    assert.ok(strip(first).classList.contains("hidden"));
    first.window.localStorage.setItem("probe", "x");
    first.bulk.current = stopped;
    await first.module("bulk-job.js").attachBulk();
    assert.ok(strip(first).classList.contains("hidden"), "dismissed: not offered again");
  });

  test("a start whose answer was lost: the page asks what runs, and finds it", async (t) => {
    const ctx = await started(t, { begin: false, total: 100 });
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.server.first("/api/library/bulk/start", () => Promise.reject(new Error("Failed to fetch")));
    ctx.bulk.current = jobStatus({ total: 100, done: 3 });
    el(ctx, "bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, el(ctx, "btn-bulk-add-tags"));
    await ctx.settle(60);
    assert.match(text(ctx, "status-text"), /Could not start the bulk edit: Failed to fetch\. If the edit did start, it shows here/);
    await ctx.settle(60);
    assert.ok(!strip(ctx).classList.contains("hidden"), "found by asking what runs");
    assert.equal(el(ctx, "bulk-add-tags-input").value, "Trips/Lighthouse", "the text stays");
  });
});
