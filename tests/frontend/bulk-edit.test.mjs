/**
 * Editing the selection of a library view (web/tagpup/bulk-edit.js, bulk-run.js, bulk-words.js; the strip is web/common/batch-strip.js; docs/ARCHITECTURE.md):
 * Add tag, Add person and the pills' Remove and Apply each ask first, by name and count, and then write in ONE request that answers
 * when every photo is done, with the selection BY ID -- the source and the few ids excluded, never 5,000 ids or paths. While the
 * request is out the page says what is being written, to how many and for how long, and the view's edit controls are off. A limit
 * (5,000 photos) is said in a sentence before any request; a refused question sends nothing; the selection edited meanwhile changes
 * nothing that was asked; an answer that does not come is not retried. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";
import fs from "node:fs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

/** The replies the real routes make (tests/test_batch_strip_replies.py). */
const served = JSON.parse(fs.readFileSync(new URL("./fixtures/batches.json", import.meta.url), "utf8"));
const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const plain = (value) => JSON.parse(JSON.stringify(value));
const el = (ctx, id) => ctx.document.getElementById(id);
const edits = (ctx) => ctx.server.calls.filter((call) => call.url.includes("/api/library/bulk-edit"));
const status = (ctx) => el(ctx, "status-text").textContent;
const strip = (ctx) => ctx.document.querySelector(".batch-strip");
const sentence = (ctx) => strip(ctx).querySelector(".batch-strip-sentence").textContent;

async function view(t, n, options = {}) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: range(n), ...options });
  ctx.questions = [];
  ctx.answer = true;
  ctx.alerts = [];
  ctx.window.confirm = (question) => { ctx.questions.push(question); return typeof ctx.answer === "function" ? ctx.answer(question) : ctx.answer; };
  ctx.window.alert = (message) => { ctx.alerts.push(message); };
  ctx.bulk.edit = { total: n, changed: n };
  return ctx;
}

async function addTag(ctx, typed = "Trips/Lighthouse") {
  el(ctx, "bulk-add-tags-input").value = typed;
  click(ctx.window, el(ctx, "btn-bulk-add-tags"));
  await ctx.settle(60);
}

describe("Add tag to a selection of a library view", () => {
  test("5,000 selected of 5,003, 3 excluded: the question names the write and the count, and the request is the source and the 3 ids", async (t) => {
    const ctx = await view(t, 5003);
    ctx.bulk.edit = { total: 5000, changed: 5000 };
    el(ctx, "btn-select-all-thumbnails").click();
    for (const id of [3, 5, 7]) click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.deepEqual(ctx.questions, ["Add Trips/Lighthouse to 5,000 photos? This changes the photo files and takes about 6 minutes. History lists it as one change and can undo it."],
      "one question, however many photos");
    assert.equal(edits(ctx).length, 1);
    const body = plain(edits(ctx)[0].body);
    assert.equal(body.op, "tags");
    assert.deepEqual(body.params, { add: ["Trips/Lighthouse"], remove: [] });
    assert.deepEqual(body.selection, { source: { kind: "all", value: null, recursive: false }, excluded: [3, 5, 7] });
    assert.ok(!("ids" in body.selection) && !("paths" in body.selection), "never 5,000 ids or paths");
    assert.ok(!ctx.server.calls.some((call) => call.url.includes("/api/photos/bulk-tags")), "not the folder's route");
    assert.equal(sentence(ctx), "Added Trips/Lighthouse to 5,000 photos; 0 errors.", "the server's count of the photos it found");
  });

  test("rapid clicks on Add (and Enter): one question and one request", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    el(ctx, "bulk-add-tags-input").value = "Trips/Lighthouse";
    for (let n = 0; n < 4; n++) click(ctx.window, el(ctx, "btn-bulk-add-tags"));
    el(ctx, "bulk-add-tags-input").dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await ctx.settle(80);
    assert.equal(ctx.questions.length, 1);
    assert.equal(edits(ctx).length, 1);
  });

  test("a small selection is asked once, still naming the write and the count", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.deepEqual(ctx.questions, ["Add Trips/Lighthouse to 1 photo? This changes the photo files and takes less than a minute. History lists it as one change and can undo it."]);
    assert.deepEqual(plain(edits(ctx)[0].body.selection), { ids: [2] });
  });

  test("the question refused sends nothing and the text stays", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.answer = false;
    await addTag(ctx);
    assert.equal(edits(ctx).length, 0);
    assert.equal(el(ctx, "bulk-add-tags-input").value, "Trips/Lighthouse");
    assert.equal(strip(ctx).hidden, true);
  });

  test("the selection edited after it was read changes nothing that was asked: the request is what the question named", async (t) => {
    const ctx = await view(t, 400);
    for (const id of [2, 4]) click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
    ctx.answer = () => {
      el(ctx, "btn-select-none-thumbnails").click();
      click(ctx.window, ctx.cardById(9).querySelector(".thumbnail-checkbox"));
      return true;
    };
    await addTag(ctx);
    assert.match(ctx.questions[0], /to 2 photos\?/);
    assert.deepEqual(plain(edits(ctx)[0].body.selection), { ids: [2, 4] });
  });

  test("a tag the rules refuse is said and nothing is asked", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx, "Trips//Lighthouse");
    assert.equal(ctx.alerts.length, 1);
    assert.equal(ctx.questions.length, 0);
    assert.equal(edits(ctx).length, 0);
  });

  test("nothing selected: said, nothing asked", async (t) => {
    const ctx = await view(t, 400);
    await addTag(ctx);
    assert.equal(edits(ctx).length, 0);
    assert.equal(ctx.questions.length, 0);
  });

  test("the typed text is cleared only when the edit was written", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.server.first("/api/library/bulk-edit", { error: "A tag cannot be both added and taken off the same photos." }, { status: 400 });
    await addTag(ctx);
    assert.equal(el(ctx, "bulk-add-tags-input").value, "Trips/Lighthouse");
    assert.match(status(ctx), /A tag cannot be both added/);
    assert.match(sentence(ctx), /A tag cannot be both added/);
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, false, "the controls are back");
    ctx.bulk.edit = { total: 1, changed: 1 };
    ctx.server.routes.shift();
    await addTag(ctx);
    assert.equal(el(ctx, "bulk-add-tags-input").value, "");
  });
});

describe("while it is written, and when it is done", () => {
  test("the status line says what is written and to how many; the controls are off until the answer", async (t) => {
    const ctx = await view(t, 400);
    ctx.hold.bulkEdit = true;
    el(ctx, "btn-select-all-thumbnails").click();
    await addTag(ctx);
    assert.equal(status(ctx), "Add Trips/Lighthouse to 400 photos...");
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, true);
    assert.equal(el(ctx, "btn-bulk-add-people").disabled, true);
    assert.match(el(ctx, "btn-bulk-add-tags").title, /A bulk edit is being written/);
    ctx.held.splice(0).forEach((each) => each.release());
    await ctx.settle(60);
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, false);
    assert.equal(sentence(ctx), "Added Trips/Lighthouse to 400 photos; 0 errors.");
  });

  test("a done edit reads what changed again: the caches, the navigator's counts, the cards and the tally", async (t) => {
    const ctx = await view(t, 400);
    await ctx.showLibraryPane();
    ctx.window.localStorage.setItem("tagpup_cache_d:/library/2020", "{}");
    const navigator = ctx.navigatorAsked.length;
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.bulk.edit = { total: 1, changed: 1 };
    await addTag(ctx);
    assert.equal(ctx.window.localStorage.getItem("tagpup_cache_d:/library/2020"), null, "the folders' saved scans are forgotten");
    assert.ok(ctx.navigatorAsked.length > navigator, "the counts are read again");
  });

  test("the errors: the first 50 with their file names and why, and the rest counted", async (t) => {
    const ctx = await view(t, 400);
    const errors = range(50).map((n) => ({ id: n, name: `IMG_${n}.jpg`, why: "its folder could not be reached just now" }));
    ctx.bulk.edit = { total: 400, changed: 340, error_count: 60, errors };
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    el(ctx, "btn-select-all-thumbnails").click();
    await addTag(ctx);
    assert.equal(sentence(ctx), "Added Trips/Lighthouse to 340 photos; 60 errors.");
    assert.equal(strip(ctx).querySelector("summary").textContent, "60 errors: the first 50 are listed");
    const rows = [...strip(ctx).querySelectorAll(".batch-strip-errors li")];
    assert.equal(rows.length, 51);
    assert.equal(rows[0].textContent, "IMG_1.jpg — its folder could not be reached just now");
    assert.equal(rows[50].textContent, "10 more, not listed");
    assert.equal(status(ctx), "Added Trips/Lighthouse to 340 photos; 60 errors.");
  });

  test("photos left out and photos already as asked are counted in the sentence", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.edit = { total: 100, changed: 90, unchanged: 4, skipped_missing: 3 };
    el(ctx, "btn-select-all-thumbnails").click();
    await addTag(ctx);
    assert.equal(sentence(ctx),
      "Added Trips/Lighthouse to 90 photos; 3 missing on disk were skipped; 4 already were as asked; 0 errors.");
  });

  test("more photos than were picked (they came into the view since it was read) is said after the fact", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.edit = { total: 403, changed: 403 };
    el(ctx, "btn-select-all-thumbnails").click();
    await addTag(ctx);
    assert.equal(sentence(ctx),
      "Added Trips/Lighthouse to 403 photos; 0 errors. TagPup took 403 photos but 400 were selected: 3 more came into this view since it was read.");
  });

  test("an answer that does not come is said, with what to do, and nothing is sent again", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.server.first("/api/library/bulk-edit", () => { throw new Error("connection reset"); });
    await addTag(ctx);
    assert.equal(edits(ctx).length, 1, "not sent again");
    assert.match(status(ctx), /TagPup did not answer \(.*connection reset.*\)\. The edit may still be being written, or may be written already: look at the photos before making it again\./);
    assert.match(sentence(ctx), /^TagPup did not answer/);
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, false);
    pageErrors();
  });

  test("a server that failed part-way says so, and the page reads the photos again", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.server.first("/api/library/bulk-edit", { success: false, error: "The bulk edit stopped (RuntimeError); the server's log says why. Photos already written keep the edit: make it again to do the rest." }, { status: 500 });
    const navigator = ctx.navigatorAsked.length;
    await addTag(ctx);
    assert.match(status(ctx), /^The bulk edit stopped \(RuntimeError\)/);
    assert.ok(ctx.navigatorAsked.length >= navigator);
    assert.match(sentence(ctx), /^The bulk edit stopped/);
  });

  test("Dismiss takes the strip away and the focus goes to the grid", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.bulk.edit = { total: 1, changed: 1 };
    await addTag(ctx);
    assert.equal(strip(ctx).hidden, false);
    click(ctx.window, [...strip(ctx).querySelectorAll("button")].find((button) => button.textContent === "Dismiss"));
    assert.equal(strip(ctx).hidden, true);
  });
});

describe("what a request cannot carry is said before it is made", () => {
  test("5,001 selected: the limit, no question, no request", async (t) => {
    const ctx = await view(t, 6000);
    ctx.module("selected.js").setIdRange(0, 5000, true);
    await addTag(ctx);
    assert.deepEqual(ctx.alerts, [served.over_the_cap.body.error], "the page says what the server would");
    assert.equal(ctx.questions.length, 0);
    assert.equal(edits(ctx).length, 0);
    assert.equal(strip(ctx).hidden, true);
  });

  test("exactly 5,000 is sent", async (t) => {
    const ctx = await view(t, 6000);
    ctx.bulk.edit = { total: 5000, changed: 5000 };
    ctx.module("selected.js").setIdRange(0, 4999, true);
    await addTag(ctx);
    assert.equal(ctx.alerts.length, 0);
    assert.equal(plain(edits(ctx)[0].body.selection).ids.length, 5000);
  });

  test("a Select all of 68,000 less 2: the same sentence, and the panel's note says so beforehand", async (t) => {
    const ctx = await view(t, 68000);
    el(ctx, "btn-select-all-thumbnails").click();
    ctx.module("selected.js").setIdRange(0, 1, false);
    ctx.module("selection.js").updateSelectedThumbnailsCount();
    assert.equal(el(ctx, "selection-note").textContent, "Select up to 5,000 photos at a time (67,998 are selected).");
    await addTag(ctx);
    assert.equal(edits(ctx).length, 0);
  });
});

describe("Add person", () => {
  test("a person is added as the op `people`, by the path the page resolved", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    el(ctx, "bulk-add-people-input").value = "People/Wren Halloway";
    click(ctx.window, el(ctx, "btn-bulk-add-people"));
    await ctx.settle(80);
    assert.match(ctx.questions[0], /^Add People\/Wren Halloway to 1 photo\?/);
    const body = plain(edits(ctx)[0].body);
    assert.equal(body.op, "people");
    assert.deepEqual(body.params.add, ["People/Wren Halloway"]);
  });
});

describe("only one bulk edit at a time", () => {
  test("a second one while one is written is refused by the page first: the controls are off, with a tooltip", async (t) => {
    const ctx = await view(t, 400);
    // What the server counts of the selection, from the start: the page asks for it once, TALLY_DELAY_MS after the click,
    // and on a busy machine that was before a tally set after the start (#721).
    ctx.bulk.tally = { total: 1, tags: [{ tag: "Trips/Coast", count: 1 }], more_tags: 0, people: [], more_people: 0 };
    ctx.hold.bulkEdit = true;
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.equal(edits(ctx).length, 1);
    for (const id of ["btn-bulk-add-tags", "btn-bulk-add-people"]) {
      assert.equal(el(ctx, id).disabled, true, id);
      assert.match(el(ctx, id).title, /A bulk edit is being written/);
    }
    ctx.module("selection.js").updateSelectedThumbnailsCount();
    await ctx.until(() => ctx.document.querySelector(".selection-summary-chip-remove"));
    const pill = ctx.document.querySelector(".selection-summary-chip-remove");
    assert.equal(pill.getAttribute("aria-disabled"), "true");
    assert.match(pill.title, /A bulk edit is being written/);
    pill.click();
    await ctx.settle(60);
    assert.equal(edits(ctx).length, 1, "a pill does nothing while one is written");
    ctx.held.splice(0).forEach((each) => each.release());
    await ctx.settle(60);
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, false);
  });

  test("the server's 409 (the line of batches is full) shows its sentence in the strip", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.server.first("/api/library/bulk-edit", served.queue_full.body, { status: served.queue_full.status });
    await addTag(ctx);
    assert.equal(sentence(ctx), served.queue_full.body.error);
  });

  test("a batch stopped as asked: the reply's own sentence (N of M, the change in History) is the strip's, not 35 errors", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.bulk.edit = served.stopped_reply;
    await addTag(ctx);
    assert.equal(sentence(ctx), served.stopped_reply.errors[0].why);
    assert.equal(strip(ctx).querySelector("details"), null);
    assert.match(status(ctx), /^Stopped as asked after 25 of 60 photos/);
  });

  test("the folder view's own bulk controls are never disabled by it", async (t) => {
    const ctx = await view(t, 400);
    ctx.hold.bulkEdit = true;
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, true);
    ctx.module("library-view.js").closeLibraryView();
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, false, "a folder's bulk write is another route");
    ctx.held.splice(0).forEach((each) => each.release());
    await ctx.settle(40);
  });
});

describe("a pill of the tally", () => {
  const tally = { total: 3, tags: [{ tag: "Trips/Coast", count: 3 }, { tag: "Old/Stuff", count: 1 }], more_tags: 0,
    people: [{ name: "Wren Halloway", count: 2 }], more_people: 0 };

  test("× removes the tag from the whole selection: the source and the excluded ids, params.remove", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.tally = tally;
    el(ctx, "btn-select-all-thumbnails").click();
    click(ctx.window, ctx.cardById(4).querySelector(".thumbnail-checkbox"));
    await ctx.until(() => ctx.document.querySelector("#selection-tags-list .selection-summary-chip"));
    const pills = [...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")];
    assert.deepEqual(pills.map((pill) => pill.textContent.replace(/[^\w\/() ]/g, "").trim()), ["Old/Stuff (1)", "Trips/Coast (3)"], "alphabetical");
    pills[1].querySelector(".selection-summary-chip-remove").click();
    await ctx.settle(60);
    assert.match(ctx.questions[0], /^Remove Trips\/Coast from 399 photos\? .* History lists it as one change and can undo it\.$/);
    const body = plain(edits(ctx)[0].body);
    assert.equal(body.op, "tags");
    assert.deepEqual(body.params, { add: [], remove: ["Trips/Coast"] });
    assert.deepEqual(body.selection, { source: { kind: "all", value: null, recursive: false }, excluded: [4] });
  });

  test("× on a person removes them from the whole selection (op people); the arrow applies one held by some of the photos", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.tally = tally;
    el(ctx, "btn-select-all-thumbnails").click();
    await ctx.until(() => ctx.document.querySelector("#selection-people-list .selection-summary-chip"));
    const person = ctx.document.querySelector("#selection-people-list .selection-summary-chip");
    assert.ok(person.querySelector(".selection-summary-chip-apply"), "2 of 3 hold her: the arrow is offered");
    assert.equal(ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")[1].querySelector(".selection-summary-chip-apply"), null,
      "all 3 hold Trips/Coast: no arrow");
    person.querySelector(".selection-summary-chip-remove").click();
    await ctx.settle(60);
    const body = plain(edits(ctx)[0].body);
    assert.equal(body.op, "people");
    assert.deepEqual(body.params, { add: [], remove: ["Wren Halloway"] });
  });

  test("tags with odd characters are text, never markup, and are sent as they are", async (t) => {
    const ctx = await view(t, 400);
    const odd = 'Trips/R&D <img src=x onerror="boom()"> "quoted" \'single\'';
    ctx.bulk.tally = { total: 1, tags: [{ tag: odd, count: 1 }], more_tags: 0, people: [], more_people: 0 };
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await ctx.until(() => ctx.document.querySelector("#selection-tags-list .selection-summary-chip"));
    const pill = ctx.document.querySelector("#selection-tags-list .selection-summary-chip");
    assert.equal(pill.querySelector("img"), null);
    assert.ok(pill.textContent.includes(odd));
    pill.querySelector(".selection-summary-chip-remove").click();
    await ctx.settle(60);
    assert.deepEqual(plain(edits(ctx)[0].body.params), { add: [], remove: [odd] });
  });
});
