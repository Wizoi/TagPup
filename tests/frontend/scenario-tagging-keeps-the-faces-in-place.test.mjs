/**
 * V19: somebody tags the open photo while its faces are shown under it. The strip keeps its cards while it is read again,
 * with no "Loading faces..." between (owner, 2026-11-15: "keep the old strip until the new one is ready"), and it is read
 * again only when the save's reply says a face of it changed ("reload only when needed"): the server lists the faces the
 * one-face rule named (#788), those a person taken off unnamed (#908) and those named again (`named_faces`,
 * `unnamed_faces`, `renamed_faces`); a reply that lists none reads nothing, whatever the tag was. A face named by its card,
 * its person then taken off by the pill; two quick saves, another photo chosen meanwhile, back to the grid and the same
 * photo again, a read that fails, the keyboard's focus on a card, a save that ends while a face is being named or while
 * the faces are first read, an undo of another photo: each as it happens on the page. The photo's first read says it is
 * loading (an empty strip looked like a photo with no faces), a failed one says so, and a photo chosen after another never
 * shows the other's faces.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, PEOPLE_ROUTE, peopleRecordsOf,
  SAVE, lastSave,
} from "./harness.mjs";

const A = photoRecord({ filename: "a.jpg" });
const B = photoRecord({ filename: "b.jpg" });
const LOADING = "Loading faces...";
const FAILED = "The faces of this photo could not be loaded. Select the photo again to try once more.";
const NOT_REFRESHED = "The faces of this photo could not be refreshed. Select the photo again to try once more.";

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Jane Doe", name: "Jane Doe", parent_id: 1, has_face: 1 },
  { id: 3, tag: "People/Alex Roe", name: "Alex Roe", parent_id: 1, has_face: 1 },
  { id: 4, tag: "People/Sam Gray", name: "Sam Gray", parent_id: 1, has_face: 1 },
  { id: 5, tag: "Events", name: "Events", parent_id: null, has_face: 0 },
  { id: 6, tag: "Events/Birthday", name: "Birthday", parent_id: 5, has_face: 0 },
];
const JANE = { id: 2, name: "Jane Doe", tag: "People/Jane Doe" };
const ALEX = { id: 3, name: "Alex Roe", tag: "People/Alex Roe" };
const SAM = { id: 4, name: "Sam Gray", tag: "People/Sam Gray" };

const named = (id, who = JANE) => ({ id, box: [0, 0, 50, 50], area: 2500, name: who.name, person: who, excluded: false });
const proposed = (id) => ({
  id, box: [60, 0, 50, 50], area: 2500, name: null, suggestion: "Alex Roe", similarity: 0.8, excluded: false,
});
const nameless = (id) => ({ id, box: [120, 0, 50, 50], area: 2500, name: null, excluded: false });
const reply = (faces) => ({ faces, total: faces.length, unmatched: faces.filter((f) => !f.name).length });
// Jane named, a face that looks like Alex, and one nobody is like (no card): two faces still to be named.
const THREE = () => reply([named(1), proposed(2), nameless(3)]);
// The save's reply when the one-face rule named face 3 (Sam), and when a person taken off unnamed `id`.
const NAMED_3 = { success: true, named_faces: [{ id: 3, name: SAM.name }] };
const unnamed = (id, who) => ({ success: true, unnamed_faces: [{ id, name: who.name }] });

/** A reply the test lets go of when it chooses: [promise, release(body)]. */
function held() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return [promise, release];
}

/**
 * The page on a folder of A and B, A open. `faces(url)` answers each read of the faces unless the test queued an answer
 * of its own (ctx.next): the reads in the order they are asked. A save answers what the test queued (ctx.nextSave), else
 * `{success: true}`, a reply that lists no face; an undo's bulk write likewise (ctx.nextBulk).
 */
async function open(t, { faces = THREE, tags = [], status = 200 } = {}) {
  const queued = [];
  const saves = [];
  const bulks = [];
  const server = new FakeServer()
    .on("/api/tags", [])
    .first(PEOPLE_ROUTE, peopleRecordsOf(TAXONOMY))
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [{ ...A, tags: tags.slice() }, { ...B, tags: [] }])
    .on(SAVE, () => (saves.length ? saves.shift() : { success: true }))
    .on("/api/photos/write", () => (bulks.length ? bulks.shift() : { success: true }))
    .on("/api/face/match", { success: true })
    .on("/api/photo-faces", (url) => (queued.length ? queued.shift()(url) : faces(url)), { status });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.console.error = () => {};
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  const $ = (id) => ctx.document.getElementById(id);
  ctx.next = (answer) => queued.push(answer);
  ctx.nextSave = (body) => saves.push(body);
  ctx.nextBulk = (body) => bulks.push(body);
  ctx.select = async (photo) => {
    ctx.document.querySelector(`li[data-path="${photo.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 8);
  };
  ctx.shown = () => ($("faces-section").classList.contains("hidden") ? null : $("faces-strip").textContent);
  ctx.summary = () => $("faces-summary").textContent;
  ctx.cards = () => Array.from(ctx.document.querySelectorAll("#faces-strip .face-card"));
  // A card is its face's by the crop it shows.
  ctx.faceOf = (card) => new URL(card.querySelector("img").src, "http://localhost").searchParams.get("id");
  ctx.ids = () => ctx.cards().map(ctx.faceOf).join(",");
  ctx.card = (id) => ctx.cards().find((card) => ctx.faceOf(card) === String(id));
  ctx.reads = () => server.urls().filter((u) => u.includes("/api/photo-faces")).length;
  // The tag and the person fields of Image Details, as somebody types into them and presses Add.
  ctx.addTag = async (text) => {
    $("input-add-tag").value = text;
    $("btn-add-tag").click();
    await flush(ctx.window, 10);
  };
  ctx.addPerson = async (text) => {
    $("input-add-person").value = text;
    $("btn-add-person").click();
    await flush(ctx.window, 10);
  };
  ctx.pill = async (text) => {
    Array.from(ctx.document.querySelectorAll(".tag-pill")).find((each) => each.textContent.includes(text)).click();
    await flush(ctx.window, 12);
  };
  ctx.click = async (id) => {
    $(id).click();
    await flush(ctx.window, 12);
  };
  await ctx.select(A);
  return ctx;
}

afterEach(() => closeAllApps());

describe("V19: tagging the open photo keeps its faces in place", () => {
  test("the first read says it is loading, then shows the faces", async (t) => {
    const [pending, answer] = held();
    const ctx = await open(t, { faces: () => pending });
    assert.equal(ctx.shown(), LOADING);
    answer(THREE());
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "1,2");
    assert.ok(!ctx.shown().includes("Loading"), "the placeholder stayed after the faces came");
  });

  test("a person put on the photo whose reply names a face reads its faces again, the cards kept until the answer", async (t) => {
    const ctx = await open(t);
    assert.equal(ctx.ids(), "1,2");
    const before = ctx.reads();
    const [pending, answer] = held();
    ctx.next(() => pending);
    ctx.nextSave(NAMED_3);
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.reads(), before + 1, "the faces were not read again after the reply named one");
    assert.ok(!ctx.shown().includes(LOADING), "the strip said it was loading on a read of the same photo");
    assert.equal(ctx.ids(), "1,2", "the cards went while the faces were read again");
    answer(reply([named(1), proposed(2), named(3, SAM)]));
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "1,2,3", "the new answer was not drawn");
  });

  test("a reply that lists no face reads nothing: a keyword, and a person put on a photo with faces to be named", async (t) => {
    const ctx = await open(t);
    const before = ctx.reads();
    await ctx.addTag("Events/Birthday");
    assert.equal(ctx.reads(), before, "a keyword read the faces again");
    await ctx.addPerson("Sam Gray");
    assert.deepEqual(lastSave(ctx.server).tags, ["Events/Birthday", "People/Sam Gray"]);
    assert.equal(ctx.reads(), before, "a person the server named no face after read the faces again");
    assert.equal(ctx.ids(), "1,2");
  });

  test("a person taken off whose reply unnames their face reads the faces again (#908)", async (t) => {
    const ctx = await open(t, { faces: () => reply([named(1)]), tags: ["People/Jane Doe"] });
    const before = ctx.reads();
    // A pill's removal is written as the page's tag changes are (pending.js), and its reply says the faces it unnamed.
    ctx.nextBulk(unnamed(1, JANE));
    await ctx.pill("Jane Doe");
    assert.deepEqual(Object.values(ctx.server.lastBody("/api/photos/write").changes), [{ id: null, add: [], remove: ["People/Jane Doe"] }],
      "the person was not taken off");
    assert.equal(ctx.reads(), before + 1, "the faces were not read again after their person went");
  });

  test("a face named by its card, its person then taken off by the pill: the strip is read again, not left 'all identified'", async (t) => {
    const ctx = await open(t, { faces: () => reply([named(1), proposed(2)]) });
    // Alex's card: his tag is written (the one-face rule names face 2 on the server), then the face itself is named.
    ctx.nextSave({ success: true, named_faces: [{ id: 2, name: ALEX.name }] });
    ctx.next(() => reply([named(1), named(2, ALEX)]));
    ctx.card(2).click();
    await flush(ctx.window, 12);
    assert.match(ctx.summary(), /all identified/);
    const before = ctx.reads();
    ctx.nextBulk(unnamed(2, ALEX));
    await ctx.pill("Alex Roe");
    assert.deepEqual(Object.values(ctx.server.lastBody("/api/photos/write").changes), [{ id: null, add: [], remove: [ALEX.tag] }],
      "the person was not taken off");
    assert.equal(ctx.reads(), before + 1, "the faces were not read again after their person went");
    assert.doesNotMatch(ctx.summary(), /all identified/, "the strip still said the unnamed face was named");
    assert.equal(ctx.card(2).classList.contains("unmatched"), true);
  });

  test("a save that lands while the faces are first read is followed once they come, if its reply lists a face", async (t) => {
    for (const [said, more] of [[NAMED_3, 1], [{ success: true }, 0]]) {
      const [pending, answer] = held();
      const ctx = await open(t, { faces: () => pending });
      ctx.nextSave(said);
      await ctx.addPerson("Sam Gray");
      const before = ctx.reads();
      answer(THREE());
      await flush(ctx.window, 10);
      assert.equal(ctx.reads(), before + more, `a reply of ${JSON.stringify(said)} during the first read`);
      closeAllApps();
    }
  });

  test("two quick saves, their reads answered out of order: the later answer is the one shown", async (t) => {
    const ctx = await open(t);
    const [first, answerFirst] = held();
    const [second, answerSecond] = held();
    ctx.next(() => first);
    ctx.next(() => second);
    ctx.nextSave(NAMED_3);
    ctx.nextSave(NAMED_3);
    await ctx.addPerson("Sam Gray");
    await ctx.addPerson("Alex Roe");
    answerSecond(reply([named(1), named(3)]));
    await flush(ctx.window, 6);
    answerFirst(reply([named(1), proposed(2), named(8)]));
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "1,3", "the earlier read's late answer replaced the later one");
  });

  test("another photo chosen while the faces are read again: the old photo's answer is drawn nowhere", async (t) => {
    const ctx = await open(t, { faces: (url) => (url.includes("b.jpg") ? reply([named(9)]) : THREE()) });
    const [pending, answer] = held();
    ctx.next(() => pending);
    ctx.nextSave(NAMED_3);
    await ctx.addPerson("Sam Gray");
    await ctx.select(B);
    assert.equal(ctx.ids(), "9");
    answer(THREE());
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "9", "A's late answer was drawn over B's faces");
  });

  test("back to the grid, then the same photo again: its faces are read and say they are loading", async (t) => {
    const ctx = await open(t);
    assert.equal(ctx.ids(), "1,2");
    await ctx.click("folder-view-header");
    const [pending, answer] = held();
    ctx.next(() => pending);
    await ctx.select(A);
    assert.equal(ctx.shown(), LOADING, "the faces section was hidden, or showed the faces left behind as if read");
    answer(THREE());
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "1,2");
  });

  test("an undo is followed by the open photo's faces only: another photo's face in its reply reads nothing", async (t) => {
    const ctx = await open(t, { faces: (url) => (url.includes("b.jpg") ? reply([named(9)]) : THREE()), tags: ["Events/Birthday"] });
    await ctx.select(B);
    await ctx.click("btn-carry-forward");
    assert.deepEqual(lastSave(ctx.server).tags, ["Events/Birthday"]);
    await ctx.select(A);
    const before = ctx.reads();
    ctx.nextBulk({ success: true, unnamed_faces: [{ id: 9, name: JANE.name }] });
    await ctx.click("btn-undo");
    assert.deepEqual(Object.keys(ctx.server.lastBody("/api/photos/write").changes), [B.path], "the undo did not write B");
    assert.equal(ctx.reads(), before, "B's face in the undo's reply read A's faces again");
  });

  test("an undo of the open photo whose reply lists its face reads the faces again", async (t) => {
    const ctx = await open(t, { faces: (url) => (url.includes("b.jpg") ? reply([named(9)]) : THREE()), tags: ["Events/Birthday"] });
    await ctx.select(B);
    await ctx.click("btn-carry-forward");
    const before = ctx.reads();
    ctx.nextBulk({ success: true, renamed_faces: [{ id: 9, name: JANE.name }] });
    await ctx.click("btn-undo");
    assert.equal(ctx.reads(), before + 1, "the undo that named B's face again did not read B's faces");
  });

  test("a read again that fails keeps the cards and says they could not be refreshed", async (t) => {
    const ctx = await open(t);
    ctx.next(() => Promise.reject(new Error("network down")));
    ctx.nextSave(NAMED_3);
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.ids(), "1,2", "the cards went with the failed read");
    assert.ok(ctx.shown().includes(NOT_REFRESHED), ctx.shown());
    assert.ok(!ctx.shown().includes(FAILED));
    assert.equal(ctx.document.querySelectorAll("#faces-strip .faces-note").length, 1);
  });

  test("a first read that fails says so, and is not left loading", async (t) => {
    const ctx = await open(t, { faces: () => Promise.reject(new Error("network down")) });
    assert.equal(ctx.shown(), FAILED);
  });

  test("a first read the server refuses says so too, where it used to read as no faces", async (t) => {
    const ctx = await open(t, { faces: () => ({ error: "no library" }), status: 500 });
    assert.equal(ctx.shown(), FAILED);
  });

  test("another photo chosen during the first read: the first's late answer or failure shows nowhere", async (t) => {
    for (const late of [(slow) => slow.then(() => THREE()), (slow) => slow.then(() => { throw new Error("late"); })]) {
      const [slow, letGo] = held();
      const [quick, answerQuick] = held();
      const ctx = await open(t, { faces: (url) => (url.includes("b.jpg") ? quick : late(slow)) });
      await ctx.select(B);
      assert.equal(ctx.shown(), LOADING, "B is still being read");
      letGo();
      await flush(ctx.window, 6);
      assert.equal(ctx.shown(), LOADING, "A's late reply replaced B's placeholder");
      answerQuick(reply([named(9)]));
      await flush(ctx.window, 6);
      assert.equal(ctx.ids(), "9");
      closeAllApps();
    }
  });

  test("the card with the keyboard's focus keeps it while the faces are read again and drawn", async (t) => {
    const ctx = await open(t);
    const card = ctx.card(2);
    card.focus();
    const [pending, answer] = held();
    ctx.next(() => pending);
    ctx.nextSave(NAMED_3);
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.document.activeElement, card, "the focus left the card while the faces were read");
    answer(THREE());
    await flush(ctx.window, 6);
    const now = ctx.document.activeElement;
    assert.ok(now && now.classList.contains("face-card"), "the focus left the strip");
    assert.equal(ctx.faceOf(now), "2", "the focus left the face it was on");
    assert.ok(now.isConnected, "the focus is on a card no longer shown");
  });

  test("a save whose reply lists a face, landing while a face is being named, is followed once the naming ends", async (t) => {
    const ctx = await open(t);
    const [matched, answerMatch] = held();
    ctx.server.first("/api/face/match", () => matched);
    // Alex's card pressed: the face is being named (its person on the photo, then the face itself, held here).
    ctx.card(2).click();
    await flush(ctx.window, 10);
    const before = ctx.reads();
    ctx.nextSave(NAMED_3);
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.reads(), before, "the faces were read while a face was being named");
    answerMatch({ success: true });
    await flush(ctx.window, 10);
    assert.equal(ctx.reads(), before + 1, "the face the save named during the naming was not followed when it ended");
  });
});
