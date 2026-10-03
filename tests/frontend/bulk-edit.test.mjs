/**
 * Editing the selection of a library view (web/tagpup/bulk-edit.js, bulk-job.js, bulk-words.js; docs/ARCHITECTURE.md, phase 9d-2):
 * Add tag, Add person, the pills' Remove and Apply and Shift Date Taken each ask first, by name and count, and then start a JOB
 * with the selection BY ID -- the source and the few ids excluded, never 68,000 ids or paths. A limit is said in a sentence before any
 * request; a refused question sends nothing; the selection edited meanwhile changes nothing that was asked. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, jobStatus } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const plain = (value) => JSON.parse(JSON.stringify(value));
const el = (ctx, id) => ctx.document.getElementById(id);
const starts = (ctx) => ctx.server.calls.filter((call) => call.url.includes("/api/library/bulk/start"));
const status = (ctx) => el(ctx, "status-text").textContent;

async function view(t, n, options = {}) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: range(n), ...options });
  ctx.questions = [];
  ctx.answer = true;
  ctx.alerts = [];
  ctx.window.confirm = (text) => { ctx.questions.push(text); return typeof ctx.answer === "function" ? ctx.answer(text) : ctx.answer; };
  ctx.window.alert = (text) => { ctx.alerts.push(text); };
  ctx.bulk.start = { total: n };
  ctx.bulk.status = jobStatus({ total: n });
  return ctx;
}

async function addTag(ctx, text = "Trips/Lighthouse") {
  el(ctx, "bulk-add-tags-input").value = text;
  click(ctx.window, el(ctx, "btn-bulk-add-tags"));
  await ctx.settle(60);
}

describe("Add tag to a selection of a library view", () => {
  test("68,000 selected, 3 excluded: the question names the write and the count, and the request is the source and the 3 ids", async (t) => {
    const ctx = await view(t, 68000);
    el(ctx, "btn-select-all-thumbnails").click();
    for (const id of [3, 5, 7]) click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.match(ctx.questions[0], /^Add Trips\/Lighthouse to 67,997 photos\? This changes the photo files and takes about 1 hour 16 minutes\. It cannot be undone as one step; remove the tag to reverse it\.$/);
    assert.equal(ctx.questions[1], "This is 67,997 photos. Continue?", "over 5,000 photos it is asked a second time");
    assert.equal(starts(ctx).length, 1);
    const body = plain(starts(ctx)[0].body);
    assert.equal(body.op, "tags");
    assert.deepEqual(body.params, { add: ["Trips/Lighthouse"], remove: [] });
    assert.deepEqual(body.selection, { source: { kind: "all", value: null, recursive: false }, excluded: [3, 5, 7] });
    assert.ok(!("ids" in body.selection) && !("paths" in body.selection), "never 68,000 ids or paths");
    assert.ok(!ctx.server.calls.some((call) => call.url.includes("/api/photos/bulk-tags")), "not the folder's route");
    assert.ok(!el(ctx, "bulk-strip").classList.contains("hidden"));
    assert.match(el(ctx, "bulk-strip-title").textContent, /Add Trips\/Lighthouse to 68,000 photos/, "the server's count of the photos it found");
  });

  test("a small selection is asked once, still naming the write and the count", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.deepEqual(ctx.questions, ["Add Trips/Lighthouse to 1 photo? This changes the photo files and takes less than a minute. It cannot be undone as one step; remove the tag to reverse it."]);
    assert.deepEqual(plain(starts(ctx)[0].body.selection), { ids: [2] });
  });

  test("the question refused sends nothing and the text stays; the second question refused sends nothing", async (t) => {
    const ctx = await view(t, 6000);
    el(ctx, "btn-select-all-thumbnails").click();
    ctx.answer = false;
    await addTag(ctx);
    assert.equal(starts(ctx).length, 0);
    assert.equal(el(ctx, "bulk-add-tags-input").value, "Trips/Lighthouse");
    ctx.answer = (text) => !text.startsWith("This is");
    await addTag(ctx);
    assert.equal(ctx.questions.at(-1), "This is 6,000 photos. Continue?");
    assert.equal(starts(ctx).length, 0, "a plain second question: no, and nothing was sent");
    assert.ok(el(ctx, "bulk-strip").classList.contains("hidden"));
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
    assert.deepEqual(plain(starts(ctx)[0].body.selection), { ids: [2, 4] });
  });

  test("a tag the rules refuse is said and nothing is asked", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx, "Trips//Lighthouse");
    assert.equal(ctx.alerts.length, 1);
    assert.equal(ctx.questions.length, 0);
    assert.equal(starts(ctx).length, 0);
  });

  test("nothing selected: said, nothing asked", async (t) => {
    const ctx = await view(t, 400);
    await addTag(ctx);
    assert.equal(starts(ctx).length, 0);
    assert.equal(ctx.questions.length, 0);
  });

  test("the typed text is cleared only when a job began", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.server.first("/api/library/bulk/start", { error: "A tag cannot be both added and taken off the same photos." }, { status: 400 });
    await addTag(ctx);
    assert.equal(el(ctx, "bulk-add-tags-input").value, "Trips/Lighthouse");
    assert.match(status(ctx), /A tag cannot be both added/);
    assert.ok(el(ctx, "bulk-strip").classList.contains("hidden"), "no job: no strip");
  });
});

describe("what a request cannot carry is said before it is made", () => {
  test("30,000 picked in 68,000: a sentence, no question, no request", async (t) => {
    const ctx = await view(t, 68000);
    ctx.module("selected.js").setIdRange(0, 29999, true);
    await addTag(ctx);
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /30,000 photos are selected and 38,000 are not/);
    assert.equal(ctx.questions.length, 0);
    assert.equal(starts(ctx).length, 0);
  });

  test("more than 20,000 excluded: the same, and the panel's note says so beforehand", async (t) => {
    const ctx = await view(t, 68000);
    el(ctx, "btn-select-all-thumbnails").click();
    ctx.module("selected.js").setIdRange(0, 25000, false);
    ctx.module("selection.js").updateSelectedThumbnailsCount();
    assert.match(el(ctx, "selection-note").textContent, /42,999 photos are selected and 25,001 are not/);
    await addTag(ctx);
    assert.equal(starts(ctx).length, 0);
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
    const body = plain(starts(ctx)[0].body);
    assert.equal(body.op, "people");
    assert.deepEqual(body.params.add, ["People/Wren Halloway"]);
  });
});

describe("Shift Date Taken in a view", () => {
  async function shifting(t, n = 400) {
    const ctx = await view(t, n);
    click(ctx.window, el(ctx, "btn-toggle-timeshift"));
    return ctx;
  }
  const shift = async (ctx, minutes, direction = "later") => {
    el(ctx, "timeshift-minutes-input").value = String(minutes);
    el(ctx, "timeshift-direction").value = direction;
    click(ctx.window, el(ctx, "btn-apply-timeshift"));
    await ctx.settle(60);
  };

  test("the panel offers minutes and a direction, no camera, and says both Date Taken fields move", async (t) => {
    const ctx = await shifting(t);
    assert.ok(!el(ctx, "timeshift-panel").classList.contains("hidden"));
    assert.ok(el(ctx, "timeshift-camera-field").classList.contains("hidden"), "the camera filter is not offered");
    assert.ok(!el(ctx, "timeshift-direction-field").classList.contains("hidden"));
    assert.match(el(ctx, "timeshift-view-note").textContent, /both Date Taken fields/);
    assert.ok(!el(ctx, "timeshift-view-note").classList.contains("hidden"));
  });

  test("90 minutes earlier: the question names it, and the job's minutes are negative", async (t) => {
    const ctx = await shifting(t);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.bulk.start = { op: "time_shift", total: 1 };
    await shift(ctx, 90, "earlier");
    assert.equal(ctx.questions[0], "Shift Date Taken 90 minutes earlier in 1 photo? This changes the photo files and takes less than a minute. It cannot be undone as one step; shift them later by the same minutes to reverse it.");
    const body = plain(starts(ctx)[0].body);
    assert.equal(body.op, "time_shift");
    assert.deepEqual(body.params, { minutes: -90 });
    assert.deepEqual(body.selection, { ids: [2] });
    assert.equal(el(ctx, "timeshift-minutes-input").value, "0");
  });

  test("0, a negative number, a fraction, nothing and a huge number are refused with a sentence beside the field, and nothing is asked", async (t) => {
    const ctx = await shifting(t);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    for (const [typed, expected] of [
      ["0", /not zero/], ["-5", /positive number/], ["1.5", /whole number of minutes/], ["", /not zero/],
      ["5259600", /more than 10 years/],
    ]) {
      await shift(ctx, typed);
      assert.match(status(ctx), expected, `"${typed}"`);
    }
    assert.equal(ctx.questions.length, 0);
    assert.equal(starts(ctx).length, 0);
  });

  test("ten years of minutes is the most that is taken", async (t) => {
    const ctx = await shifting(t);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.bulk.start = { op: "time_shift", total: 1 };
    await shift(ctx, 5256000);
    assert.equal(starts(ctx).length, 1);
  });

  test("a folder's time shift is as it was: the camera, the sign, and its own route", async (t) => {
    const ctx = await loadViewPage(t, { search: "" });
    assert.ok(!el(ctx, "timeshift-camera-field").classList.contains("hidden"));
    assert.ok(el(ctx, "timeshift-direction-field").classList.contains("hidden"));
    assert.ok(el(ctx, "timeshift-view-note").classList.contains("hidden"));
  });
});

describe("only one bulk edit at a time", () => {
  test("a second start while one runs is refused by the page first: the controls are off, with a tooltip", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.equal(starts(ctx).length, 1);
    for (const id of ["btn-bulk-add-tags", "btn-bulk-add-people", "btn-apply-timeshift"]) {
      assert.equal(el(ctx, id).disabled, true, id);
      assert.match(el(ctx, id).title, /A bulk edit is running/);
    }
    ctx.bulk.tally = { total: 1, tags: [{ tag: "Trips/Coast", count: 1 }], more_tags: 0, people: [], more_people: 0 };
    ctx.module("selection.js").updateSelectedThumbnailsCount();
    await ctx.settle(400);
    const pill = ctx.document.querySelector(".selection-summary-chip-remove");
    assert.equal(pill.getAttribute("aria-disabled"), "true");
    assert.match(pill.title, /A bulk edit is running/);
    pill.click();
    await ctx.settle(60);
    assert.equal(starts(ctx).length, 1, "a pill does nothing while one runs");
  });

  test("the server's 409 shows its sentence in the strip, with Show it, which picks up the one running", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    ctx.server.first("/api/library/bulk/start", { error: "A bulk edit is already running in photo_index (bulk tags; 40 of 100 photos done). Wait for it to finish, or cancel it." }, { status: 409 });
    await addTag(ctx);
    assert.match(el(ctx, "bulk-strip-message").textContent, /already running in photo_index/);
    assert.ok(!el(ctx, "btn-bulk-show").classList.contains("hidden"));
    assert.ok(!el(ctx, "bulk-strip").classList.contains("hidden"));
    ctx.bulk.current = jobStatus({ total: 100, done: 40, changed: 40 });
    ctx.bulk.status = jobStatus({ total: 100, done: 40, changed: 40 });
    click(ctx.window, el(ctx, "btn-bulk-show"));
    await ctx.settle(60);
    assert.equal(el(ctx, "bulk-strip-progress").textContent, "40 of 100");
    assert.equal(el(ctx, "bulk-strip-message").textContent, "");
  });

  test("the folder view's own bulk controls are never disabled by a job", async (t) => {
    const ctx = await view(t, 400);
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await addTag(ctx);
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, true);
    ctx.module("library-view.js").closeLibraryView();
    assert.equal(el(ctx, "btn-bulk-add-tags").disabled, false, "a folder's bulk write is another route");
    assert.ok(!el(ctx, "bulk-strip").classList.contains("hidden"), "the job is the library's: its strip stays");
  });
});

describe("a pill of the tally", () => {
  const tally = { total: 3, tags: [{ tag: "Trips/Coast", count: 3 }, { tag: "Old/Stuff", count: 1 }], more_tags: 0,
    people: [{ name: "Wren Halloway", count: 2 }], more_people: 0 };

  test("× removes the tag from the whole selection, as a job: the source and the excluded ids, params.remove", async (t) => {
    const ctx = await view(t, 68000);
    ctx.bulk.tally = tally;
    el(ctx, "btn-select-all-thumbnails").click();
    click(ctx.window, ctx.cardById(4).querySelector(".thumbnail-checkbox"));
    await ctx.settle(400);
    const pills = [...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")];
    assert.deepEqual(pills.map((pill) => pill.textContent.replace(/[^\w\/() ]/g, "").trim()), ["Old/Stuff (1)", "Trips/Coast (3)"], "alphabetical");
    pills[1].querySelector(".selection-summary-chip-remove").click();
    await ctx.settle(60);
    assert.match(ctx.questions[0], /^Remove Trips\/Coast from 67,999 photos\? .* add the tag again to reverse it\.$/);
    const body = plain(starts(ctx)[0].body);
    assert.equal(body.op, "tags");
    assert.deepEqual(body.params, { add: [], remove: ["Trips/Coast"] });
    assert.deepEqual(body.selection, { source: { kind: "all", value: null, recursive: false }, excluded: [4] });
  });

  test("× on a person removes them from the whole selection (op people); the arrow applies one held by some of the photos", async (t) => {
    const ctx = await view(t, 400);
    ctx.bulk.tally = tally;
    el(ctx, "btn-select-all-thumbnails").click();
    await ctx.settle(400);
    const person = ctx.document.querySelector("#selection-people-list .selection-summary-chip");
    person.querySelector(".selection-summary-chip-remove").click();
    await ctx.settle(60);
    let body = plain(starts(ctx)[0].body);
    assert.equal(body.op, "people");
    assert.deepEqual(body.params, { add: [], remove: ["Wren Halloway"] });
    assert.ok(person.querySelector(".selection-summary-chip-apply"), "2 of 3 hold her: the arrow is offered");
    assert.equal(ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")[1].querySelector(".selection-summary-chip-apply"), null,
      "all 3 hold Trips/Coast: no arrow");
  });

  test("tags with odd characters are text, never markup, and are sent as they are", async (t) => {
    const ctx = await view(t, 400);
    const odd = 'Trips/R&D <img src=x onerror="boom()"> "quoted" \'single\'';
    ctx.bulk.tally = { total: 1, tags: [{ tag: odd, count: 1 }], more_tags: 0, people: [], more_people: 0 };
    click(ctx.window, ctx.cardById(2).querySelector(".thumbnail-checkbox"));
    await ctx.settle(400);
    const pill = ctx.document.querySelector("#selection-tags-list .selection-summary-chip");
    assert.equal(pill.querySelector("img"), null);
    assert.ok(pill.textContent.includes(odd));
    pill.querySelector(".selection-summary-chip-remove").click();
    await ctx.settle(60);
    assert.deepEqual(plain(starts(ctx)[0].body.params), { add: [], remove: [odd] });
  });
});
