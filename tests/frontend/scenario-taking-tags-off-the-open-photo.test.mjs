/**
 * V20: somebody takes tags off the open photo by its pills (owner, 2026-11-15: "when i remove, it seems to delay things until
 * that one tag is removed, can those be queued sort of how clicking on faces are queued"). The pill goes at the click, before
 * the file's write is answered (1-4 s on a share), and the write waits its turn behind the photo's others; pills clicked
 * quickly all go at once, those clicked while a write is out written together, and the file ends holding none of them; a removal and an add queued around it neither undo the other,
 * nor a title saved meanwhile; a write that fails puts back exactly the tags it took, says so, and a removal queued behind it
 * still goes; another photo chosen meanwhile shows only its own tags, and the first photo, opened again, its tags as written;
 * a pill taken off by the keyboard leaves the focus on the next pill, or on the add box when it was the last. The status line
 * says 'Deleting tag...' until the last removal is answered.
 *
 * Every save is held until the test answers it, as the route answers it (its `base`: what the file holds now).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, PEOPLE_ROUTE, peopleRecordsOf,
} from "./harness.mjs";

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Rowan Ashby", name: "Rowan Ashby", parent_id: 1, has_face: 1 },
  { id: 3, tag: "Cross Country", name: "Cross Country", parent_id: null, has_face: 0 },
  { id: 4, tag: "Harbourside", name: "Harbourside", parent_id: null, has_face: 0 },
  { id: 5, tag: "Kentridge", name: "Kentridge", parent_id: null, has_face: 0 },
  { id: 6, tag: "Places", name: "Places", parent_id: null, has_face: 0 },
  { id: 7, tag: "Places/Beach", name: "Beach", parent_id: 6, has_face: 0 },
];
const TAGS = ["Cross Country", "Harbourside", "Kentridge"];
const A = photoRecord({ filename: "a.jpg" });
const B = photoRecord({ filename: "b.jpg" });

async function open(t) {
  const held = [];
  const server = new FakeServer()
    .on("/api/tags", TAGS)
    .first(PEOPLE_ROUTE, peopleRecordsOf(TAXONOMY))
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [{ ...A, tags: TAGS.slice() }, { ...B, tags: [] }])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", () => new Promise((resolve) => held.push(resolve)));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.console.error = () => {};
  ctx.alerts = [];
  ctx.window.alert = (text) => ctx.alerts.push(text);
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  const $ = (id) => ctx.document.getElementById(id);
  ctx.$ = $;
  ctx.saves = () => server.calls.filter((c) => c.url.includes("save-metadata")).map((c) => c.body);
  let answered = 0;
  // Answer the oldest save still held: written, the file then holding what it was sent; or refused with `error`.
  ctx.answer = async (error = null) => {
    const sent = ctx.saves()[answered];
    answered += 1;
    held.shift()(error ? { success: false, error } : { success: true, base: { tags: sent.tags.slice(), title: "" } });
    await flush(ctx.window, 12);
  };
  ctx.unanswered = () => held.length;
  ctx.pills = () => Array.from(ctx.document.querySelectorAll("#detail-tags .tag-pill, #detail-people .tag-pill"))
    .map((each) => each.textContent);
  ctx.pill = (text) => Array.from(ctx.document.querySelectorAll(".tag-pill")).find((each) => each.textContent === text);
  ctx.takeOff = async (text) => {
    ctx.pill(text).click();
    await flush(ctx.window, 4);
  };
  ctx.status = () => $("status-text").textContent;
  ctx.select = async (photo) => {
    ctx.document.querySelector(`li[data-path="${photo.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 8);
  };
  ctx.active = () => {
    const row = ctx.document.querySelector(".photo-item-file.active");
    return row && row.getAttribute("data-path");
  };
  await ctx.select(A);
  return ctx;
}

const sorted = (list) => list.slice().sort();

afterEach(() => closeAllApps());

describe("V20: taking tags off the open photo is answered at the click, the writes queued", () => {
  test("pills clicked quickly: each goes before any write is answered; those clicked while one is out are written together", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    assert.deepEqual(sorted(ctx.pills()), ["Harbourside", "Kentridge"], "the pill waited for the write");
    assert.equal(ctx.unanswered(), 1, "the write was not sent");
    assert.equal(ctx.status(), "Deleting tag...");
    await ctx.takeOff("Harbourside");
    assert.deepEqual(ctx.pills(), ["Kentridge"], "the second pill waited behind the first write");
    await ctx.takeOff("Kentridge");
    assert.deepEqual(ctx.pills(), []);
    await ctx.answer();
    assert.equal(ctx.status(), "Deleting tag...", "said Ready after the first of two writes");
    while (ctx.unanswered()) await ctx.answer();
    assert.equal(ctx.saves().length, 2, "the two pills clicked while the first write was out were not written as one");
    assert.deepEqual(ctx.saves().at(-1).tags, []);
    assert.deepEqual(ctx.pills(), []);
    assert.equal(ctx.status(), "Ready");
  });

  test("a removal and an add queued around it: neither undoes the other, either way round", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    ctx.$("input-add-tag").value = "Places/Beach";
    ctx.$("btn-add-tag").click();
    await flush(ctx.window, 8);
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(sorted(ctx.saves().at(-1).tags), ["Harbourside", "Kentridge", "Places/Beach"]);
    assert.deepEqual(sorted(ctx.pills()), ["Harbourside", "Kentridge", "Places/Beach"]);

    // A title's save out first: its reply carries the tag the click took off, not drawn again; the removal writes after it.
    ctx.$("input-photo-title").value = "Harbour run";
    ctx.$("btn-save-title").click();
    await flush(ctx.window, 8);
    await ctx.takeOff("Harbourside");
    assert.deepEqual(sorted(ctx.pills()), ["Kentridge", "Places/Beach"]);
    await ctx.answer();
    assert.deepEqual(sorted(ctx.pills()), ["Kentridge", "Places/Beach"], "the title's reply drew the pill taken off again");
    while (ctx.unanswered()) await ctx.answer();
    const last = ctx.saves().at(-1);
    assert.deepEqual(sorted(last.tags), ["Kentridge", "Places/Beach"]);
    assert.equal(last.title, "Harbour run");
  });

  test("a write that fails puts back only its own tag, says so, and the removal queued behind it still goes", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    await ctx.takeOff("Harbourside");
    await ctx.answer("The share did not answer");
    assert.deepEqual(sorted(ctx.pills()), ["Cross Country", "Kentridge"], "not exactly the failed removal's tag came back");
    assert.match(ctx.status(), /Cross Country.*The share did not answer/);
    assert.equal(ctx.alerts.length, 1);
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(sorted(ctx.saves().at(-1).tags), ["Cross Country", "Kentridge"]);
    assert.deepEqual(sorted(ctx.pills()), ["Cross Country", "Kentridge"]);
    assert.match(ctx.status(), /Cross Country/, "the failure was said over by the next removal");
    assert.ok(ctx.document.querySelector(".write-queue-retry"), "the failed write offers no Retry");
  });

  test("another photo chosen while a removal is out shows its own tags; the first, opened again, its tags as written", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    await ctx.select(B);
    while (ctx.unanswered()) await ctx.answer();
    await flush(ctx.window, 8);
    assert.equal(ctx.active(), B.path);
    assert.deepEqual(ctx.pills(), [], "the first photo's tags drawn on the second");
    await ctx.select(A);
    assert.deepEqual(sorted(ctx.pills()), ["Harbourside", "Kentridge"]);
  });

  test("a pill taken off by the keyboard leaves the focus on the next pill, the last one on the add box", async (t) => {
    const ctx = await open(t);
    const press = (element) => element.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    ctx.pill("Harbourside").focus();
    press(ctx.pill("Harbourside"));
    await flush(ctx.window, 4);
    assert.equal(ctx.document.activeElement.textContent, "Kentridge", "the focus left the pills");
    while (ctx.unanswered()) await ctx.answer();
    assert.equal(ctx.document.activeElement.textContent, "Kentridge", "the reply's redraw dropped the focus");
    press(ctx.document.activeElement);
    await flush(ctx.window, 4);
    assert.equal(ctx.document.activeElement.textContent, "Cross Country");
    press(ctx.document.activeElement);
    await flush(ctx.window, 4);
    assert.equal(ctx.document.activeElement.id, "input-add-tag");
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.saves().at(-1).tags, []);
  });
});
