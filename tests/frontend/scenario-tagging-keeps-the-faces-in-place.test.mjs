/**
 * V19: somebody tags the open photo while its faces are shown under it. The strip keeps its cards while it is read again,
 * with no "Loading faces..." between (owner, 2026-11-15: "keep the old strip until the new one is ready"), and it is read
 * again only when the tag can have changed its faces ("reload only when needed"): a keyword under a root of keywords
 * reads nothing, a person put on a photo with a face to be named does (the server may have named it, #788). Two quick
 * saves, another photo chosen meanwhile, a read that fails, the keyboard's focus on a card, a save that ends while a face
 * is being named: each as it happens on the page. The photo's first read says it is loading (an empty strip looked like a
 * photo with no faces), a failed one says so, and a photo chosen after another never shows the other's faces.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, PEOPLE_ROUTE, peopleRecordsOf,
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

const named = (id) => ({ id, box: [0, 0, 50, 50], area: 2500, name: JANE.name, person: JANE, excluded: false });
const proposed = (id) => ({
  id, box: [60, 0, 50, 50], area: 2500, name: null, suggestion: "Alex Roe", similarity: 0.8, excluded: false,
});
const nameless = (id) => ({ id, box: [120, 0, 50, 50], area: 2500, name: null, excluded: false });
const reply = (faces) => ({ faces, total: faces.length, unmatched: faces.filter((f) => !f.name).length });
// Jane named, a face that looks like Alex, and one nobody is like (no card): two faces still to be named.
const THREE = () => reply([named(1), proposed(2), nameless(3)]);

/** A reply the test lets go of when it chooses: [promise, release(body)]. */
function held() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return [promise, release];
}

/**
 * The page on a folder of A and B, A open. `faces(url)` answers each read of the faces unless the test queued an answer
 * of its own (ctx.next): the reads in the order they are asked.
 */
async function open(t, { faces = THREE, tags = [], status = 200 } = {}) {
  const queued = [];
  const server = new FakeServer()
    .on("/api/tags", [])
    .first(PEOPLE_ROUTE, peopleRecordsOf(TAXONOMY))
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [{ ...A, tags: tags.slice() }, { ...B, tags: [] }])
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/photo-faces", (url) => (queued.length ? queued.shift()(url) : faces(url)), { status });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.console.error = () => {};
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  const $ = (id) => ctx.document.getElementById(id);
  ctx.next = (answer) => queued.push(answer);
  ctx.select = async (photo) => {
    ctx.document.querySelector(`li[data-path="${photo.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 8);
  };
  ctx.shown = () => ($("faces-section").classList.contains("hidden") ? null : $("faces-strip").textContent);
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

  test("a person put on the photo reads its faces again, and the cards stay, never 'Loading', until the answer", async (t) => {
    const ctx = await open(t);
    assert.equal(ctx.ids(), "1,2");
    const before = ctx.reads();
    const [pending, answer] = held();
    ctx.next(() => pending);
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.reads(), before + 1, "a person on a photo with a face to be named did not read the faces again");
    assert.ok(!ctx.shown().includes(LOADING), "the strip said it was loading on a read of the same photo");
    assert.equal(ctx.ids(), "1,2", "the cards went while the faces were read again");
    answer(reply([named(1), proposed(2), { ...nameless(3), name: "Sam Gray", person: { id: 4, name: "Sam Gray", tag: "People/Sam Gray" } }]));
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "1,2,3", "the new answer was not drawn");
  });

  test("a keyword under a root of keywords does not read the faces again", async (t) => {
    const ctx = await open(t);
    const before = ctx.reads();
    await ctx.addTag("Events/Birthday");
    assert.equal(ctx.reads(), before, "a keyword that can name nobody read the faces again");
    assert.equal(ctx.ids(), "1,2");
  });

  test("a person taken off a photo whose face is them reads the faces again (the server unnames it, #908)", async (t) => {
    const ctx = await open(t, { faces: () => reply([named(1)]), tags: ["People/Jane Doe"] });
    const before = ctx.reads();
    const pill = Array.from(ctx.document.querySelectorAll(".tag-pill")).find((each) => each.textContent.includes("Jane Doe"));
    pill.click();
    await flush(ctx.window, 10);
    assert.deepEqual(ctx.server.lastBody("/api/photo/save-metadata").tags, [], "the person was not taken off");
    assert.equal(ctx.reads(), before + 1, "the faces were not read again after their person went");
  });

  test("a person put on the photo while its faces are first read is followed when they come", async (t) => {
    const [pending, answer] = held();
    const ctx = await open(t, { faces: () => pending });
    await ctx.addPerson("Sam Gray");
    const before = ctx.reads();
    answer(THREE());
    await flush(ctx.window, 10);
    assert.equal(ctx.reads(), before + 1, "the faces read before the person was saved were left as they were");
  });

  test("a photo with no faces is not read again after a person is put on it", async (t) => {
    const ctx = await open(t, { faces: () => reply([]) });
    assert.equal(ctx.shown(), null);
    const before = ctx.reads();
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.reads(), before);
  });

  test("two quick saves, their reads answered out of order: the later answer is the one shown", async (t) => {
    const ctx = await open(t);
    const [first, answerFirst] = held();
    const [second, answerSecond] = held();
    ctx.next(() => first);
    ctx.next(() => second);
    await ctx.addPerson("Sam Gray");
    await ctx.addPerson("Alex Roe");
    answerSecond(reply([named(1), named(7)]));
    await flush(ctx.window, 6);
    answerFirst(reply([named(1), proposed(2), named(8)]));
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "1,7", "the earlier read's late answer replaced the later one");
  });

  test("another photo chosen while the faces are read again: the old photo's answer is drawn nowhere", async (t) => {
    const ctx = await open(t, { faces: (url) => (url.includes("b.jpg") ? reply([named(9)]) : THREE()) });
    const [pending, answer] = held();
    ctx.next(() => pending);
    await ctx.addPerson("Sam Gray");
    await ctx.select(B);
    assert.equal(ctx.ids(), "9");
    answer(THREE());
    await flush(ctx.window, 6);
    assert.equal(ctx.ids(), "9", "A's late answer was drawn over B's faces");
  });

  test("a read again that fails keeps the cards and says they could not be refreshed", async (t) => {
    const ctx = await open(t);
    ctx.next(() => Promise.reject(new Error("network down")));
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
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.document.activeElement, card, "the focus left the card while the faces were read");
    answer(THREE());
    await flush(ctx.window, 6);
    const now = ctx.document.activeElement;
    assert.ok(now && now.classList.contains("face-card"), "the focus left the strip");
    assert.equal(ctx.faceOf(now), "2", "the focus left the face it was on");
    assert.ok(now.isConnected, "the focus is on a card no longer shown");
  });

  test("a person saved while a face is being named is followed once the naming ends", async (t) => {
    const ctx = await open(t);
    const [matched, answerMatch] = held();
    ctx.server.first("/api/face/match", () => matched);
    // Alex's card pressed: the face is being named (its person on the photo, then the face itself, held here).
    ctx.card(2).click();
    await flush(ctx.window, 10);
    const before = ctx.reads();
    await ctx.addPerson("Sam Gray");
    assert.equal(ctx.reads(), before, "the faces were read while a face was being named");
    answerMatch({ success: true });
    await flush(ctx.window, 10);
    assert.equal(ctx.reads(), before + 1, "the person saved during the naming was not followed when it ended");
  });
});
