/**
 * The AI Suggestions box on a single photo: what it offers, and when it gets out of
 * the way.
 *
 * It used to list every suggestion the analysis produced whether or not the photo
 * already carried it, so after Apply All the box sat there repeating back the tags it
 * had just written. A suggestion you have taken is not a suggestion; it is a tag, and
 * it is already shown as one a few inches above.
 *
 * The layout follows from that: suggestions on the left because they are the thing to
 * act on, the detected faces on the right as reference and as the place to pick up a
 * match the suggester was not confident enough to propose.
 *
 * While the folder's run waits or is under way, a photo with nothing yet says the run's own line or its count, and a
 * photo the run could not read says why (owner, 2026-11-05); the replies are the real SuggestionRuns.status()'s
 * (tests/test_suggest_status_replies.py writes fixtures/suggest_status.json).
 */
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  advance, loadApp, FakeServer, photoRecord, flush, click, openFolder, pageExports, peopleRecordsOf, PEOPLE_ROUTE, REPO_ROOT,
  SAVE, saved, testClock,
} from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Hazel Brookmire", name: "Hazel Brookmire", parent_id: 1, has_face: 1 },
  { id: 3, tag: "People/Anh Tran", name: "Anh Tran", parent_id: 1, has_face: 1 },
  // Present so a suggested keyword resolves without a placement modal; an unknown
  // tag would open one and the click would resolve to nothing.
  { id: 4, tag: "Cross Country", name: "Cross Country", parent_id: null, has_face: 0 },
  { id: 5, tag: "Kentridge", name: "Kentridge", parent_id: null, has_face: 0 },
];

async function openPhoto(t, { tags = [], people = [], suggestions = {}, faces } = {}) {
  const photo = photoRecord({ filename: "a.jpg", tags, people });
  const server = new FakeServer()
    .on("/api/tags", ["People/Hazel Brookmire", "Cross Country"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .first(PEOPLE_ROUTE, peopleRecordsOf(TAXONOMY))
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100 })
    .on("/api/folder/scan", [photo])
    .on("/api/photo-faces", faces || { faces: [], total: 0, unmatched: 0 })
    .on(SAVE, saved())
    .on("/api/folder/suggest-status", {
      status: "completed",
      suggestions: { [photo.path]: suggestions },
    });

  const ctx = await loadApp("tagpup", {
    t, url: "http://localhost:8090/photo_index/", server,
  });
  await openFolder(ctx, "D:/Library/2020");
  const row = ctx.document.querySelector("li[data-path]");
  if (row) row.click();
  await flush(ctx.window, 8);
  ctx.photo = photo;
  return ctx;
}

const section = (ctx) => ctx.document.getElementById("suggestions-section");
const hidden = (el) => !el || el.classList.contains("hidden");
const chips = (ctx, id) =>
  [...ctx.document.querySelectorAll(`#${id} .suggestion-chip`)].map((c) => c.textContent);

describe("it offers only what is outstanding", () => {
  test("a person already on the photo is not suggested", async (t) => {
    const ctx = await openPhoto(t, {
      tags: ["People/Hazel Brookmire"],
      people: ["Hazel Brookmire"],
      suggestions: {
        people: [{ name: "Hazel Brookmire", score: 0.9 }, { name: "Anh Tran", score: 0.8 }],
        tags: [],
      },
    });
    const offered = chips(ctx, "suggested-people-container").join(" ");
    assert.ok(!offered.includes("Hazel Brookmire"), `already applied, still offered: ${offered}`);
    assert.ok(offered.includes("Anh Tran"), `the outstanding one is missing: ${offered}`);
  });

  test("a person tagged by their path is matched against a bare suggestion", async (t) => {
    // The suggester speaks in leaf names; the photo carries paths.
    const ctx = await openPhoto(t, {
      tags: ["People/Anh Tran"],
      people: [],
      suggestions: { people: [{ name: "Anh Tran", score: 0.9 }], tags: [] },
    });
    assert.deepEqual(chips(ctx, "suggested-people-container"), []);
  });

  test("a keyword already on the photo is not suggested", async (t) => {
    const ctx = await openPhoto(t, {
      tags: ["Cross Country"],
      suggestions: { people: [], tags: [{ tag: "Cross Country", score: 0.9 }] },
    });
    assert.deepEqual(chips(ctx, "suggested-tags-container"), []);
  });

  test("the whole box goes away when nothing is left to act on", async (t) => {
    // Otherwise it is a heading over two empty lists, which is what it became after
    // Apply All.
    const ctx = await openPhoto(t, {
      tags: ["People/Hazel Brookmire", "Cross Country"],
      people: ["Hazel Brookmire"],
      suggestions: {
        people: [{ name: "Hazel Brookmire", score: 0.9 }],
        tags: [{ tag: "Cross Country", score: 0.9 }],
      },
    });
    assert.ok(hidden(section(ctx)), "the box stayed up with nothing in it");
  });

  test("it stays when something is outstanding", async (t) => {
    const ctx = await openPhoto(t, {
      tags: [],
      suggestions: { people: [], tags: [{ tag: "Cross Country", score: 0.9 }] },
    });
    assert.ok(!hidden(section(ctx)));
  });

  test("a chip carries its confidence", async (t) => {
    const ctx = await openPhoto(t, {
      tags: [],
      suggestions: { people: [], tags: [{ tag: "Cross Country", score: 0.87 }] },
    });
    assert.match(chips(ctx, "suggested-tags-container")[0], /87%/);
  });

  test("a person a clip's frames showed says in how many of them; a photo's person says nothing of frames", async (t) => {
    const ctx = await openPhoto(t, {
      tags: [],
      suggestions: {
        people: [{ name: "Anh Tran", score: 0.8, frames: { seen: 1, of: 5 } },
                 { name: "Hazel Brookmire", score: 0.9 }],
        tags: [],
      },
    });
    const shown = [...ctx.document.querySelectorAll("#suggested-people-container .suggestion-chip")];
    const titleOf = (name) => shown.find((c) => c.textContent.startsWith(name))?.title;
    assert.match(titleOf("Anh Tran"), /^Seen in 1 of 5 frames\. Click to add Anh Tran/);
    assert.match(titleOf("Hazel Brookmire"), /^Click to add Hazel Brookmire/);
  });

  test("taking a suggestion removes it from the box", async (t) => {
    const ctx = await openPhoto(t, {
      tags: [],
      suggestions: {
        people: [],
        tags: [{ tag: "Cross Country", score: 0.9 }, { tag: "Kentridge", score: 0.8 }],
      },
    });
    assert.equal(chips(ctx, "suggested-tags-container").length, 2);

    const chip = [...ctx.document.querySelectorAll("#suggested-tags-container .suggestion-chip")]
      .find((c) => c.textContent.includes("Cross Country"));
    click(ctx.window, chip);
    await flush(ctx.window, 8);

    const left = chips(ctx, "suggested-tags-container").join(" ");
    assert.ok(!left.includes("Cross Country"), `the applied suggestion stayed: ${left}`);
    assert.ok(left.includes("Kentridge"), `the other one vanished too: ${left}`);
  });
});

describe("the analysis row", () => {
  test("suggestions come before the faces", async (t) => {
    const ctx = await openPhoto(t, {
      tags: [],
      suggestions: { people: [], tags: [{ tag: "Cross Country", score: 0.9 }] },
      faces: {
        faces: [{ id: 1, box: [0, 0, 9, 9], area: 81, name: "Hazel Brookmire", excluded: false }],
        total: 1, unmatched: 0,
      },
    });
    const row = ctx.document.querySelector(".analysis-row");
    assert.ok(row, "the analysis row is gone");
    const order = [...row.children].map((el) => el.id);
    assert.deepEqual(order, ["suggestions-section", "faces-section"]);
  });

  test("both halves live in the same row", async (t) => {
    // Half and half: the faces used to span the full width below the details.
    const ctx = await openPhoto(t, {
      tags: [],
      suggestions: { people: [], tags: [{ tag: "Cross Country", score: 0.9 }] },
      faces: {
        faces: [{ id: 1, box: [0, 0, 9, 9], area: 81, name: "Hazel Brookmire", excluded: false }],
        total: 1, unmatched: 0,
      },
    });
    const row = ctx.document.querySelector(".analysis-row");
    assert.equal(ctx.document.getElementById("suggestions-section").parentElement, row);
    assert.equal(ctx.document.getElementById("faces-section").parentElement, row);
  });
});

const served = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "tests", "frontend", "fixtures", "suggest_status.json"), "utf8"));
const OTHER = "D:\\Library\\2021";
const POLL_MS = 1600;
const note = (ctx) => ctx.document.querySelector("#suggestions-section .suggestions-note");

/**
 * The folder D:\Library\2020 of the fixture's run (a, b, c) open, and `photo` of it. `status(url)` answers each poll;
 * D:\Library\2021 holds one photo of its own and no run; `theirScan`, if given, answers its scan.
 */
const theirs = () => [photoRecord({ filename: "d.jpg", path: `${OTHER}\\d.jpg` })];

async function openDuringARun(t, photo, status, { theirScan = theirs } = {}) {
  const ours = ["a.jpg", "b.jpg", "c.jpg"].map((filename) => photoRecord({ filename }));
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/taxonomy/tree", TAXONOMY)
    .first(PEOPLE_ROUTE, peopleRecordsOf(TAXONOMY))
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100 })
    .on("/api/folder/membership", { photos_not_held: 0 })
    .on("/api/folder/scan", (url) => (url.includes("2021") ? theirScan() : ours))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/library/ids", { source: {}, total: 0, ids: [], videos: [], complete: true })
    .on("/api/folder/suggest-status", status);
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, "D:/Library/2020");
  await openRow(ctx, photo);
  return ctx;
}

async function openRow(ctx, filename) {
  const row = [...ctx.document.querySelectorAll("li[data-path]")].find((li) => li.dataset.path.endsWith(filename));
  assert.ok(row, `no row for ${filename}`);
  row.click();
  await flush(ctx.window, 8);
}

/** Replies in turn, the last kept. */
const inTurn = (...replies) => () => (replies.length > 1 ? replies.shift() : replies[0]);

describe("while the folder's run goes, the box says how it stands", () => {
  test("opened while the run waits for the graphics card: the server's own line, then its chips when it completes", async (t) => {
    const ctx = await openDuringARun(t, "b.jpg", inTurn(served.waiting_for_the_card, served.completed));
    assert.ok(!hidden(section(ctx)), "the box is hidden while the run waits");
    assert.equal(note(ctx).textContent, served.waiting_for_the_card.message);
    assert.equal(note(ctx).getAttribute("role"), "status");
    await advance(ctx.window, POLL_MS);
    await flush(ctx.window, 8);
    assert.equal(note(ctx), null, "the waiting line stayed after the run completed");
    assert.match(chips(ctx, "suggested-tags-container").join(" "), /Activity\/Rowing/);
  });

  test("waiting for the folder's index: that line", async (t) => {
    const ctx = await openDuringARun(t, "b.jpg", inTurn(served.waiting_for_the_index));
    assert.equal(note(ctx).textContent, served.waiting_for_the_index.message);
  });

  test("under way, this photo not done yet: how far the run has got", async (t) => {
    const ctx = await openDuringARun(t, "b.jpg", inTurn(served.running));
    assert.equal(note(ctx).textContent, "Suggesting... 2 / 3");
    assert.ok(hidden(ctx.document.querySelector("#suggestions-section .suggestions-content")));
  });

  test("a photo the run could not read says why, and how to try again", async (t) => {
    const ctx = await openDuringARun(t, "c.jpg", inTurn(served.completed));
    assert.match(note(ctx).textContent, /cannot identify image file/);
    assert.match(note(ctx).textContent, /Start Photo Analysis again/);
    // Every photo has an entry, one of them a failure: the button the line names can be clicked.
    assert.equal(ctx.document.getElementById("btn-suggest-tags").disabled, false);
  });

  test("the AI badge marks a photo with suggestions, not one the run could not read", async (t) => {
    // One rule of what counts as suggested (suggestions.js `suggested`): the list's row and the grid's card follow the
    // Suggest button, which counts a failed photo as not done.
    const ctx = await openDuringARun(t, "a.jpg", inTurn(served.completed));
    const badge = (filename, selector, mark) => {
      const el = [...ctx.document.querySelectorAll(selector)].find((each) => each.dataset.path.endsWith(filename));
      assert.ok(el, `no ${selector} for ${filename}`);
      return Boolean(el.querySelector(mark));
    };
    assert.equal(badge("a.jpg", "li[data-path]", ".photo-item-has-sugg"), true, "a.jpg's row has no badge");
    assert.equal(badge("c.jpg", "li[data-path]", ".photo-item-has-sugg"), false, "c.jpg's row, unread, has a badge");
    pageExports(ctx.window, "web/tagpup/folder.js").openFolderView();   // back to the grid, drawn as the run left it
    await flush(ctx.window, 8);
    assert.equal(badge("a.jpg", ".thumbnail-card[data-path]", ".thumbnail-has-sugg"), true, "a.jpg's card has no badge");
    assert.equal(badge("c.jpg", ".thumbnail-card[data-path]", ".thumbnail-has-sugg"), false,
      "c.jpg's card, unread, has a badge");
  });

  test("a photo never analysed with no run says nothing: the box stays hidden", async (t) => {
    for (const reply of [served.idle, served.cancelled, served.error]) {
      const ctx = await openDuringARun(t, "b.jpg", inTurn(reply));
      assert.ok(hidden(section(ctx)), `a box for ${reply.status}`);
      assert.equal(note(ctx), null);
    }
  });

  test("the late reply of a folder left behind changes nothing in the folder opened after it", async (t) => {
    let release;
    const late = new Promise((resolve) => { release = resolve; });
    let first = true;
    const ctx = await openDuringARun(t, "a.jpg", (url) => {
      if (url.includes("2021")) return served.idle;
      if (first) { first = false; return served.waiting_for_the_card; }
      return late;   // the poll of 2020 under way when 2021 is opened
    });
    await advance(ctx.window, POLL_MS);   // 2020's next poll is asked, and not answered yet
    await openFolder(ctx, OTHER);
    await openRow(ctx, "d.jpg");
    release(served.running);
    await flush(ctx.window, 8);
    assert.ok(hidden(section(ctx)), "2020's run shows in 2021's photo");
    assert.ok(hidden(ctx.document.getElementById("suggest-progress-container")), "2020's progress shows in 2021");
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.equal(Object.keys(state.folderSuggestions).length, 0, "2020's suggestions are held for 2021");
    assert.equal(state.suggestRun.status, "idle");
  });

  // A folder opened forgets the one left behind -- its suggestions, its run and its poll -- in one place
  // (forgetFolderSuggestions): the old poll asked again while the new folder's scan is under way landed its run in the
  // page, and the scan's reply saved 2020's suggestions as 2021's in this browser's cache.
  for (const when of ["asked again while the scan is under way", "under way when the folder is opened"]) {
    test(`a poll of the folder left behind, ${when}, lands nowhere`, async (t) => {
      let releasePoll;
      let releaseScan;
      const late = new Promise((resolve) => { releasePoll = resolve; });
      const scanning = new Promise((resolve) => { releaseScan = resolve; });
      let first = true;
      const ctx = await openDuringARun(t, "a.jpg", (url) => {
        if (url.includes("2021")) return served.idle;
        if (first) { first = false; return served.waiting_for_the_card; }
        return late;
      }, { theirScan: () => scanning });
      if (when.startsWith("under way")) await advance(ctx.window, POLL_MS);   // 2020's next poll is asked, and not answered yet
      await openFolder(ctx, OTHER);   // 2021's scan is under way
      if (when.startsWith("asked again")) await advance(ctx.window, POLL_MS);   // a poll of 2020 still going is asked now
      releasePoll(served.running);
      await flush(ctx.window, 8);
      releaseScan(theirs());
      await flush(ctx.window, 8);
      const { state } = pageExports(ctx.window, "web/tagpup/state.js");
      assert.deepEqual(Object.keys(state.folderSuggestions), [], "2020's suggestions are held for 2021");
      assert.notEqual(state.suggestRun && state.suggestRun.status, "running", "2020's run is 2021's");
      const { folderCacheKey } = pageExports(ctx.window, "web/tagpup/cache.js");
      const kept = JSON.parse(ctx.window.localStorage.getItem(folderCacheKey(OTHER)));
      assert.ok(kept, "2021's scan was not kept");
      const leaked = Object.keys(kept.suggestions || {}).filter((key) => key.includes("2020"));
      assert.deepEqual(leaked, [], "2021's cache holds 2020's suggestions");
    });
  }

  test("the library view opened mid-poll shows no box", async (t) => {
    let release;
    const late = new Promise((resolve) => { release = resolve; });
    let first = true;
    const ctx = await openDuringARun(t, "b.jpg", () => {
      if (first) { first = false; return served.waiting_for_the_card; }
      return late;
    });
    assert.ok(note(ctx), "no line before the view opened");
    await advance(ctx.window, POLL_MS);
    pageExports(ctx.window, "web/tagpup/library-view.js").openLibraryView({ kind: "all" });
    await flush(ctx.window, 8);
    release(served.running);
    await flush(ctx.window, 8);
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    assert.equal(state.suggestRun, null);
    assert.ok(hidden(ctx.document.getElementById("suggest-progress-container")));
    // The photo's pane closes with the folder; nothing redraws the box with the run's line.
    assert.ok(section(ctx).closest(".hidden"), "the box shows in the library view");
    assert.equal(state.activePhotoPath, null);
  });
});
