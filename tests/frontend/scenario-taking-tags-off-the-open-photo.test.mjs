/**
 * V20: somebody takes tags off the open photo by its pills (owner, 2026-11-15: "when i remove, it seems to delay things until
 * that one tag is removed, can those be queued sort of how clicking on faces are queued"). The pill goes at the click, before
 * the file's write is answered (1-4 s on a share): the removal waits in the page's tag changes (web/tagpup/pending.js), first
 * in line, and is written through /api/photos/bulk-tags in its turn among the photo's other writes. Pills clicked quickly
 * are written together while a write is out, and the file ends holding none of them; a removal and a save around it neither
 * undo the other, nor does a title's save already out draw the removed pill again; a write that fails draws its own tags
 * again, says so, offers no Retry, and the next removal still goes; another photo chosen meanwhile shows only its own tags,
 * and the first photo, opened again, its tags as written; a pill taken off by the keyboard leaves the focus on the next pill,
 * or on the add box when it was the last. The panel's own removals never make it read-only (owner's V20 request). A tag
 * taken off and not written yet is gone wherever the page shows the photo's tags or decides by them (pending.js shownTags):
 * the list's count, and Ctrl+D on the next photo, which copies what the photo before shows when its turn comes.
 *
 * Every write is held until the test answers it, oldest first, as the routes answer it: the server keeps a model of each
 * file's tags, and a bulk-tags reply's `written` (a save-metadata reply's `base`) is what the file holds after it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, pageExports, PEOPLE_ROUTE, peopleRecordsOf,
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

/** What a write leaves in a file holding `tags`: a bulk-tags change `{add, remove}`. */
const changed = (tags, { add = [], remove = [] }) => [...tags.filter((t) => !remove.includes(t)), ...add.filter((t) => !tags.includes(t))];

async function open(t) {
  const held = [];
  // The files as the server would hold them; a write changes its file when it is answered, in the order it was sent.
  const files = new Map([[A.path, TAGS.slice()], [B.path, []]]);
  // The request just made is the last call the server saw (FakeServer hands a route only its url).
  const hold = (kind) => () => new Promise((resolve) => held.push({ kind, body: server.calls.at(-1).body, resolve }));
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
    .on("/api/photos/bulk-tags", hold("bulk"))
    .on("/api/photo/save-metadata", hold("save"));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.console.error = () => {};
  ctx.alerts = [];
  ctx.window.alert = (text) => ctx.alerts.push(text);
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  const $ = (id) => ctx.document.getElementById(id);
  ctx.$ = $;
  ctx.file = (photo) => files.get(photo.path).slice().sort();
  ctx.bulks = () => server.calls.filter((c) => c.url.includes("bulk-tags")).map((c) => c.body);
  ctx.saves = () => server.calls.filter((c) => c.url.includes("save-metadata")).map((c) => c.body);
  ctx.order = () => server.calls.filter((c) => /bulk-tags|save-metadata/.test(c.url)).map((c) => (c.url.includes("bulk") ? "bulk" : "save"));
  // Answer the oldest write still held: written, the file then holding what the route leaves in it; or refused with `error`.
  ctx.answer = async (error = null) => {
    const { kind, body, resolve } = held.shift();
    if (error) {
      resolve({ success: false, error });
    } else if (kind === "bulk") {
      const written = {};
      for (const [path, change] of Object.entries(body.changes)) {
        files.set(path, changed(files.get(path), change));
        written[path] = files.get(path).slice();
      }
      resolve({ success: true, written });
    } else {
      // A save writes what it changed from the record it was made from (`base`) onto what the file holds.
      const was = body.base ? body.base.tags : files.get(body.path);
      files.set(body.path, changed(files.get(body.path), {
        add: body.tags.filter((t) => !was.includes(t)),
        remove: was.filter((t) => !body.tags.includes(t)),
      }));
      resolve({ success: true, base: { tags: files.get(body.path).slice(), title: body.title || "" } });
    }
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
  ctx.retries = () => pageExports(ctx.window, "web/tagpup/state.js").state.writeQueue.entries.filter((e) => e.status === "failed" && (e.retry || e.cancel)).length;
  ctx.select = async (photo) => {
    ctx.document.querySelector(`li[data-path="${photo.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 8);
  };
  ctx.active = () => {
    const row = ctx.document.querySelector(".photo-item-file.active");
    return row && row.getAttribute("data-path");
  };
  ctx.editable = () => !$("btn-edit-tags").hasAttribute("inert");
  ctx.tagCount = (photo) => {
    const count = ctx.document.querySelector(`li[data-path="${photo.path.replace(/\\/g, "\\\\")}"] .photo-item-tagcount`);
    return count ? count.textContent : "";
  };
  ctx.carry = async () => {
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "d", ctrlKey: true, bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);
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
    assert.ok(ctx.editable(), "the panel's own removal made it read-only");
    await ctx.takeOff("Harbourside");
    assert.deepEqual(ctx.pills(), ["Kentridge"], "the second pill waited behind the first write");
    await ctx.takeOff("Kentridge");
    assert.deepEqual(ctx.pills(), []);
    assert.equal(ctx.unanswered(), 1, "a second write went beside the first");
    await ctx.answer();
    assert.deepEqual(ctx.pills(), [], "the first write's reply drew the pills taken off since");
    while (ctx.unanswered()) await ctx.answer();
    assert.equal(ctx.bulks().length, 2, "the two pills clicked while the first write was out were not written as one");
    assert.deepEqual(sorted(ctx.bulks()[1].changes[A.path].remove), ["Harbourside", "Kentridge"]);
    assert.deepEqual(ctx.file(A), []);
    assert.deepEqual(ctx.pills(), []);
    assert.equal(ctx.status(), "Ready");
  });

  test("a removal and a save around it: neither undoes the other, and a title's save already out draws no pill again", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    ctx.$("input-add-tag").value = "Places/Beach";
    ctx.$("btn-add-tag").click();
    await flush(ctx.window, 8);
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.order(), ["bulk", "save"]);
    assert.deepEqual(ctx.file(A), ["Harbourside", "Kentridge", "Places/Beach"]);
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
    assert.deepEqual(ctx.order(), ["bulk", "save", "save", "bulk"]);
    assert.equal(ctx.saves().at(-1).title, "Harbour run");
    assert.deepEqual(ctx.file(A), ["Kentridge", "Places/Beach"]);
    assert.deepEqual(sorted(ctx.pills()), ["Kentridge", "Places/Beach"]);
  });

  test("a write that fails draws its own tag again, says so, offers no Retry, and the next removal still goes", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    await ctx.takeOff("Harbourside");
    await ctx.answer("The share did not answer");
    assert.deepEqual(sorted(ctx.pills()), ["Cross Country", "Kentridge"], "not exactly the failed removal's tag came back");
    assert.deepEqual(ctx.alerts, ["Not taken off Cross Country: The share did not answer"]);
    const row = pageExports(ctx.window, "web/tagpup/state.js").state.writeQueue.entries.find((e) => e.status === "failed");
    assert.equal(row && row.error, "The share did not answer", "the list of writes does not say the removal failed");
    assert.equal(ctx.retries(), 0, "the failed removal offers a Retry or a Cancel");
    assert.equal(ctx.unanswered(), 1, "the removal clicked behind the failed one did not go");
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.bulks()[1].changes[A.path], { add: [], remove: ["Harbourside"] }, "the failed tag was sent again");
    await ctx.takeOff("Kentridge");
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.file(A), ["Cross Country"]);
    assert.deepEqual(ctx.pills(), ["Cross Country"]);
    assert.equal(ctx.status(), "Ready");
  });

  test("another photo chosen while a removal is out shows its own tags; the first, opened again, its tags as written", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    await ctx.select(B);
    assert.deepEqual(ctx.pills(), [], "the first photo's tags drawn on the second");
    while (ctx.unanswered()) await ctx.answer();
    assert.equal(ctx.active(), B.path);
    assert.deepEqual(ctx.pills(), [], "the first photo's reply drew its tags on the second");
    await ctx.select(A);
    assert.deepEqual(sorted(ctx.pills()), ["Harbourside", "Kentridge"]);
    assert.deepEqual(ctx.file(A), ["Harbourside", "Kentridge"]);
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
    assert.deepEqual(ctx.file(A), []);
  });
});

describe("V20: a tag taken off is gone wherever the page shows or copies the photo's tags", () => {
  test("Ctrl+D on the next photo while the removal is out copies the tags shown, not the one taken off", async (t) => {
    const ctx = await open(t);
    assert.equal(ctx.tagCount(A), "3");
    await ctx.takeOff("Cross Country");
    assert.equal(ctx.tagCount(A), "2", "the list counted the tag taken off");
    await ctx.select(B);
    assert.equal(ctx.$("btn-carry-forward").title, "Copy 2 tag(s) from a.jpg  (Ctrl+D)", "the button offered the tag taken off");
    await ctx.carry();
    assert.equal(ctx.unanswered(), 1, "the copy did not wait behind the removal");
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.order(), ["bulk", "save"]);
    assert.deepEqual(sorted(ctx.saves()[0].tags), ["Harbourside", "Kentridge"], "the copy carried the tag the pill took off");
    assert.equal(ctx.status(), "Copied 2 tag(s) from a.jpg");
    assert.deepEqual(ctx.file(B), ["Harbourside", "Kentridge"]);
    assert.deepEqual(ctx.file(A), ["Harbourside", "Kentridge"]);
  });
});
