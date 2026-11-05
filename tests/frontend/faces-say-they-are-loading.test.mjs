/**
 * The faces of the open photo while they are being fetched: the section says "Loading faces..." instead of an empty
 * area that looks like a photo with no faces; a fetch that fails says so and does not stay loading; and a photo
 * selected after another never shows the other's placeholder or faces when its own answer comes (rapid clicks).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, PEOPLE_ROUTE } from "./harness.mjs";

const A = photoRecord({ filename: "a.jpg" });
const B = photoRecord({ filename: "b.jpg" });
const LOADING = "Loading faces...";
const FAILED = "The faces of this photo could not be loaded. Select the photo again to try once more.";

const face = (id, name) => ({ id, box: [0, 0, 50, 50], area: 2500, name, excluded: false });
const reply = (faces) => ({ faces, total: faces.length, unmatched: 0 });

/** A reply the test lets go of when it chooses: [promise, release(body)]. */
function held() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return [promise, release];
}

async function open(t, facesRoute, status = 200) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on(PEOPLE_ROUTE, [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [A, B])
    .on("/api/photo-faces", facesRoute, { status });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.console.error = () => {};
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  ctx.select = async (photo) => {
    ctx.document.querySelector(`li[data-path="${photo.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 6);
  };
  ctx.section = () => ctx.document.getElementById("faces-section");
  ctx.shown = () => (ctx.section().classList.contains("hidden") ? null : ctx.document.getElementById("faces-strip").textContent);
  ctx.cards = () => ctx.document.querySelectorAll(".face-card").length;
  return ctx;
}

afterEach(() => closeAllApps());

describe("the faces of the open photo", () => {
  test("say they are loading until the answer comes, then show the faces", async (t) => {
    const [pending, answer] = held();
    const ctx = await open(t, () => pending);
    await ctx.select(A);
    assert.equal(ctx.shown(), LOADING);
    answer(reply([face(1, "Jane Doe")]));
    await flush(ctx.window, 6);
    assert.equal(ctx.cards(), 1);
    assert.ok(!ctx.shown().includes("Loading"), "the placeholder stayed after the faces came");
  });

  test("a photo with no faces ends with no section, not a placeholder", async (t) => {
    const ctx = await open(t, reply([]));
    await ctx.select(A);
    assert.equal(ctx.shown(), null);
  });

  test("a fetch that fails says so and is not left loading", async (t) => {
    const ctx = await open(t, () => Promise.reject(new Error("network down")));
    await ctx.select(A);
    assert.equal(ctx.shown(), FAILED);
  });

  test("an answer of the server's refusal says so too, where it used to read as no faces", async (t) => {
    const ctx = await open(t, { error: "no library" }, 500);
    await ctx.select(A);
    assert.equal(ctx.shown(), FAILED);
  });

  test("another photo selected meanwhile: the first photo's late answer shows nowhere", async (t) => {
    const [slow, answerSlow] = held();
    const [quick, answerQuick] = held();
    const ctx = await open(t, (url) => (url.includes("a.jpg") ? slow : quick));
    await ctx.select(A);
    await ctx.select(B);
    assert.equal(ctx.shown(), LOADING, "B is still being fetched");
    answerSlow(reply([face(1, "Jane Doe"), face(2, "Sam Gray")]));
    await flush(ctx.window, 6);
    assert.equal(ctx.shown(), LOADING, "A's answer replaced B's placeholder");
    answerQuick(reply([face(3, "Rowan Thackeray")]));
    await flush(ctx.window, 6);
    assert.equal(ctx.cards(), 1);
  });

  test("another photo selected meanwhile: the first photo's late failure does not mark the second", async (t) => {
    const [slow, failSlow] = held();
    const [quick, answerQuick] = held();
    const ctx = await open(t, (url) => (url.includes("a.jpg") ? slow.then(() => { throw new Error("late"); }) : quick));
    await ctx.select(A);
    await ctx.select(B);
    failSlow();
    await flush(ctx.window, 6);
    assert.equal(ctx.shown(), LOADING);
    answerQuick(reply([face(3, "Rowan Thackeray")]));
    await flush(ctx.window, 6);
    assert.equal(ctx.cards(), 1);
  });
});
