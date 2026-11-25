/**
 * The one strip for the batches of photo files (web/common/batch-strip.js, both pages), answered from the replies the real routes make
 * (tests/test_batch_strip_replies.py writes fixtures/batches.json). A reload shows a batch still going; two tabs both see it; a poll
 * that fails says it cannot tell and never claims the write finished; another library's rows and a library left mid-run are not this
 * page's; Cancel of a batch that has finished says the server's sentence; polling stops when nothing is going. Fictional names only.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { advance, loadApp, FakeServer, closeAllApps, click, pageExports, REPO_ROOT, testClock } from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

afterEach(() => closeAllApps());

const served = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "tests", "frontend", "fixtures", "batches.json"), "utf8"));
const [RUNNING, QUEUED] = served.running_and_queued;
/** A batch 30 s old with 10 of its 60 photos handled. */
const going = (extra = {}) => ({ ...RUNNING, done: 10, planned: 50, started: Date.now() / 1000 - 30, ...extra });

/** What the server lists now (`world.batches`) and how it answers (`world.fail`: a status and body, or "network"). */
function fakeServer(world) {
  const server = new FakeServer()
    .on("/api/activity/batches", () => {
      if (world.fail === "network") throw new TypeError("Failed to fetch");
      return world.fail ? world.fail.body : { batches: world.batches };
    })
    .first("/api/activity/batches/cancel", () => world.cancel || served.cancel_answer);
  const route = server.routes.find((each) => each.match === "/api/activity/batches");
  const reply = route.body;
  route.body = (url) => reply(url);
  return server;
}

async function open(t, world, { url = "http://localhost:8090/library/", app = "tagpup", server = fakeServer(world) } = {}) {
  // The slow clocks run a hundred times faster: the 1.5 s poll is 15 ms.
  const before = (window) => {
    const real = window.setTimeout.bind(window);
    window.setTimeout = (fn, ms, ...rest) => real(fn, ms >= 1000 ? ms / 100 : ms, ...rest);
  };
  const ctx = await loadApp(app, { t, url, server, before });
  await advance(ctx.window, 40);
  return ctx;
}

const strip = (ctx) => ctx.document.querySelector(".batch-strip");
const sentences = (ctx) => [...strip(ctx).querySelectorAll(".batch-strip-sentence")].map((each) => each.textContent);
const asked = (ctx) => ctx.server.calls.filter((call) => call.url.endsWith("/api/activity/batches")).length;
const button = (ctx, name, n = 0) => [...strip(ctx).querySelectorAll("button")].filter((each) => each.textContent === name)[n];

describe("a batch the page did not start", () => {
  test("a reload shows the batch still going: the bar from the server's counts, the time left, Cancel; its end is one sentence", async (t) => {
    const world = { batches: [going()] };
    const ctx = await open(t, world);
    assert.equal(strip(ctx).hidden, false);
    const bar = strip(ctx).querySelector("progress");
    assert.deepEqual([bar.getAttribute("value"), bar.getAttribute("max")], ["10", "60"]);
    assert.match(strip(ctx).textContent, /writing 60 photos \(bulk tags\)/);
    assert.match(strip(ctx).textContent, /10 of 60, about 3 minutes left/);
    assert.ok(button(ctx, "Cancel"));
    world.batches = [];
    await advance(ctx.window, 150);
    assert.deepEqual(sentences(ctx), ["bulk tags has ended (10 of 60 photos handled when last seen)."]);
    const calls = asked(ctx);
    await advance(ctx.window, 150);
    assert.equal(asked(ctx), calls, "nothing is going: nothing is asked");
    click(ctx.window, button(ctx, "Dismiss"));
    assert.equal(strip(ctx).hidden, true);
  });

  test("with nothing going the page asks once, when it opens, and never again", async (t) => {
    const ctx = await open(t, { batches: [] });
    await advance(ctx.window, 150);
    assert.equal(asked(ctx), 1);
    assert.equal(strip(ctx).hidden, true);
  });

  test("a waiting batch says how many are ahead and has no bar; Cancel sends its library and ticket", async (t) => {
    const world = { batches: [going(), QUEUED] };
    const ctx = await open(t, world);
    const rows = [...strip(ctx).querySelectorAll(".batch-strip-row")];
    assert.equal(rows.length, 2);
    assert.match(rows[1].textContent, /Waiting, 1 ahead: writing 60 photos/);
    assert.equal(rows[1].querySelector("progress"), null);
    click(ctx.window, button(ctx, "Cancel", 1));
    await advance(ctx.window, 40);
    const sent = ctx.server.calls.find((call) => call.url.endsWith("/api/activity/batches/cancel"));
    assert.deepEqual(sent.body, { library: "library", ticket: 2 });
  });

  test("a Cancel of a batch that has finished says the server's sentence and claims nothing", async (t) => {
    const world = { batches: [going()], cancel: served.cancel_of_a_batch_that_is_gone.body };
    const ctx = await open(t, world);
    click(ctx.window, button(ctx, "Cancel"));
    await advance(ctx.window, 40);
    assert.ok(strip(ctx).textContent.includes(served.cancel_of_a_batch_that_is_gone.body.error));
    assert.deepEqual(sentences(ctx), []);
  });

  test("a batch that ends between two polls ends on the true total, from the journal, not on the last count seen", async (t) => {
    // The last poll saw 84 of 86; the final chunks were written before the batch left the list.
    const world = { batches: [going({ photos: 86, done: 84, planned: 2 })], history: [{ id: 10, files: { done: 86 } }] };
    const server = fakeServer(world).on("/api/history", () => ({ library: "library", changes: world.history }));
    const ctx = await open(t, world, { server });
    assert.match(strip(ctx).textContent, /84 of 86/);
    world.batches = [];
    await advance(ctx.window, 150);
    assert.deepEqual(sentences(ctx), ["bulk tags has ended (86 of 86 photos handled)."]);
  });

  test("a batch asked to stop is said so, and its end is the stop sentence with its change", async (t) => {
    const world = { batches: [going({ state: "stopping", done: 25, planned: 35 })] };
    const ctx = await open(t, world);
    assert.match(strip(ctx).textContent, /Stopping after this chunk/);
    assert.equal(button(ctx, "Cancel"), undefined, "asked already");
    world.batches = [];
    await advance(ctx.window, 150);
    assert.deepEqual(sentences(ctx), ["bulk tags was stopped as asked (25 of 60 photos handled when last seen)."]);
  });

  test("a conversion the page did not see end says no photo count: it is one file, and the journal has the change", async (t) => {
    const world = { batches: [going({ operation: "convert", photos: 1, done: 0, planned: 1 })] };
    const ctx = await open(t, world);
    world.batches = [];
    await advance(ctx.window, 150);
    assert.deepEqual(sentences(ctx), ["convert has ended. History has the change."]);
  });

  test("a conversion asked to stop ends as stopped, with no photo count; a bulk edit keeps its count", async (t) => {
    const world = { batches: [going({ operation: "convert", photos: 1, done: 0, planned: 1, state: "stopping" })] };
    const ctx = await open(t, world);
    world.batches = [];
    await advance(ctx.window, 150);
    assert.deepEqual(sentences(ctx), ["convert was stopped as asked."]);
    const other = { batches: [going()] };
    const bulk = await open(t, other);
    other.batches = [];
    await advance(ctx.window, 150);
    assert.deepEqual(sentences(bulk), ["bulk tags has ended (10 of 60 photos handled when last seen)."]);
  });

  test("another program's batch is listed and cannot be cancelled from here", async (t) => {
    const ctx = await open(t, { batches: served.another_program });
    assert.match(strip(ctx).textContent, /run by another program/);
    assert.equal(button(ctx, "Cancel"), undefined);
  });

  test("two tabs both see it, either can cancel, and each ends with one sentence", async (t) => {
    const world = { batches: [going()] };
    const [one, two] = [await open(t, world), await open(t, world)];
    for (const ctx of [one, two]) assert.match(strip(ctx).textContent, /10 of 60/);
    world.cancel = { success: true };
    click(two.window, button(two, "Cancel"));
    await advance(two.window, 40);   // both pages are on the one test clock
    world.batches = [];
    await advance(two.window, 150);
    for (const ctx of [one, two]) assert.equal(sentences(ctx).length, 1);
  });

  test("TagTuner draws the same strip", async (t) => {
    const ctx = await open(t, { batches: [going()] }, { app: "tagtuner" });
    assert.match(strip(ctx).textContent, /10 of 60/);
  });
});

describe("when the list cannot be read", () => {
  for (const [what, fail] of [["a 404", { body: { success: false, error: "no" } }], ["a 403", { body: { error: "This PC only" } }], ["no answer", "network"]]) {
    test(`${what}: the strip says it cannot tell, keeps the batch, and does not claim it finished`, async (t) => {
      const world = { batches: [going()] };
      const ctx = await open(t, world);
      world.fail = fail === "network" ? "network" : fail;
      await advance(ctx.window, 150);
      assert.match(strip(ctx).textContent, /cannot tell how far the write has got/);
      assert.match(strip(ctx).textContent, /10 of 60/);
      assert.deepEqual(sentences(ctx), []);
      world.fail = null;
      await advance(ctx.window, 150);
      assert.doesNotMatch(strip(ctx).textContent, /cannot tell/);
      world.batches = [];       // a server restarted in between lists nothing: the strip does not say it finished
      await advance(ctx.window, 150);
      assert.match(sentences(ctx)[0], /has ended \(10 of 60 photos handled when last seen\)/);
    });
  }

  test("a page that is idle and cannot ask says nothing", async (t) => {
    const ctx = await open(t, { batches: [], fail: "network" });
    assert.equal(strip(ctx).hidden, true);
  });
});

describe("which library", () => {
  test("another library's batch is Activity's to show, not this page's", async (t) => {
    const ctx = await open(t, { batches: [going({ library: "other" })] });
    assert.equal(strip(ctx).hidden, true);
  });

  test("the library left mid-run: its rows go with no sentence claiming they finished", async (t) => {
    const world = { batches: [going()] };
    const ctx = await open(t, world);
    assert.equal(strip(ctx).hidden, false);
    ctx.window.history.pushState({}, "", "/other/");
    await advance(ctx.window, 150);
    assert.equal(strip(ctx).hidden, true);
    assert.deepEqual(sentences(ctx), []);
  });
});

describe("while the page writes", () => {
  test("a POST of the page is a write in flight: the strip asks until it is answered and nothing is listed, then stops", async (t) => {
    const ctx = await open(t, { batches: [] });
    let answer;
    ctx.server.first("/api/save-something", () => new Promise((resolve) => { answer = resolve; }));
    const sending = pageExports(ctx.window, "web/common/api.js").api.fetch("/api/save-something", { method: "POST" });
    await advance(ctx.window, 150);
    assert.ok(asked(ctx) >= 3, "asked again and again while the write was out");
    answer({ success: true });
    await sending;
    await advance(ctx.window, 150);
    const after = asked(ctx);
    await advance(ctx.window, 150);
    assert.equal(asked(ctx), after);
  });

  test("the page's own sentence is not overwritten by the strip's when the batch leaves the list", async (t) => {
    const world = { batches: [going()] };
    const ctx = await open(t, world);
    pageExports(ctx.window, "web/common/batch-strip.js").showEnded({ text: "Added Trips/Coast to 60 photos; 0 errors." });
    world.batches = [];
    await advance(ctx.window, 150);
    assert.deepEqual(sentences(ctx), ["Added Trips/Coast to 60 photos; 0 errors."]);
  });
});

describe("the request that stops a batch by its ticket", () => {
  test("is written once, in batch-cancel.js, and the strip, Activity and the clip panel call it", () => {
    const code = (file) => fs.readFileSync(path.join(REPO_ROOT, file), "utf8").split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line)).join("\n");
    const sends = (file) => code(file).includes("/api/activity/batches/cancel");
    assert.ok(sends("web/common/batch-cancel.js"));
    assert.ok(code("web/common/batch-cancel.js").includes("export async function cancelByTicket"));
    for (const file of ["web/common/batch-strip.js", "web/activity/now.js", "web/tagpup/clip-convert.js"]) {
      assert.equal(sends(file), false, `${file} writes the cancel request itself`);
      assert.match(code(file), /cancelByTicket\(/, `${file} does not call the one helper`);
    }
  });
});
