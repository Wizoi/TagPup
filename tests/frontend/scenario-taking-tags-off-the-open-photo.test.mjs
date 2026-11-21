/**
 * V20: somebody takes tags off the open photo by its pills (owner, 2026-11-15: "when i remove, it seems to delay things until
 * that one tag is removed, can those be queued sort of how clicking on faces are queued"). The pill goes at the click, before
 * the file's write is answered (1-4 s on a share): the removal waits in the page's tag changes (web/tagpup/pending.js), first
 * in line, and is written through /api/photos/write in its turn among the photo's other writes. Pills clicked quickly
 * are written together while a write is out, and the file ends holding none of them; a removal and a save around it neither
 * undo the other, nor does a title's save already out draw the removed pill again; a write that fails draws its own tags
 * again, says so, offers no Retry, and the next removal still goes; another photo chosen meanwhile shows only its own tags,
 * and the first photo, opened again, its tags as written; a pill taken off by the keyboard leaves the focus on the next pill,
 * or on the add box when it was the last. The panel's own removals never make it read-only (owner's V20 request). A tag
 * taken off and not written yet is gone wherever the page shows the photo's tags or decides by them (pending.js shownTags):
 * the list's count, Ctrl+D on the next photo, which copies what the photo before shows when its turn comes, the person's face
 * card, and the selection's chips. A failed removal's words stay on the status line until its row in the list of writes is
 * dismissed. In a library view a photo read from the library while its removal is out, and answered after the write's reply,
 * shows the tags the file holds now (pending.js readHeld).
 *
 * Every write is held until the test answers it, oldest first, as the routes answer it: the server keeps a model of each
 * file's tags, and a photos/write reply's `written` (a save-metadata reply's `base`) is what the file holds after it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, answer, photoRecord, flush, openFolder, closeAllApps, pageExports, PEOPLE_ROUTE, peopleRecordsOf,
  SAVE, saveCalls,
} from "./harness.mjs";
import { loadViewPage, recordOf } from "./view-page.mjs";

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

/** What a write leaves in a file holding `tags`: a photos/write change `{add, remove}`. */
const changed = (tags, { add = [], remove = [] }) => [...tags.filter((t) => !remove.includes(t)), ...add.filter((t) => !tags.includes(t))];

async function open(t, { b = [], faces = { faces: [], total: 0, unmatched: 0 } } = {}) {
  const held = [];
  // The files as the server would hold them; a write changes its file when it is answered, in the order it was sent.
  const files = new Map([[A.path, TAGS.slice()], [B.path, b.slice()]]);
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
    .on("/api/folder/scan", [{ ...A, tags: TAGS.slice() }, { ...B, tags: b.slice() }])
    .on("/api/photo-faces", faces)
    .on("/api/photos/write", hold("bulk"))
    .on(SAVE, hold("save"));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.console.error = () => {};
  ctx.alerts = [];
  ctx.window.alert = (text) => ctx.alerts.push(text);
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  const $ = (id) => ctx.document.getElementById(id);
  ctx.$ = $;
  ctx.file = (photo) => files.get(photo.path).slice().sort();
  ctx.bulks = () => server.calls.filter((c) => c.url.includes("photos/write")).map((c) => c.body);
  ctx.saves = () => saveCalls(server).map((c) => c.body);
  ctx.order = () => server.calls.filter((c) => /photos\/write|save-metadata/.test(c.url)).map((c) => (c.url.includes("photos/write") ? "bulk" : "save"));
  // Which photos each write named, in the order sent: "bulk:a,b", "save:b".
  const named = (path) => (path === A.path ? "a" : "b");
  ctx.writes = () => server.calls.filter((c) => /photos\/write|save-metadata/.test(c.url))
    .map((c) => (c.url.includes("photos/write") ? `bulk:${Object.keys(c.body.changes).map(named).join(",")}` : `save:${named(c.body.path)}`));
  // Answer the oldest write still held: written, the file then holding what the route leaves in it; or refused with `error`.
  ctx.answer = async (error = null) => {
    const { kind, body, resolve } = held.shift();
    if (error) {
      // A tag write that failed answers each photo among its `errors`, nothing written; a save says why in `error`.
      resolve(kind === "bulk" ? { success: false, errors: Object.keys(body.changes).map((path) => ({ path, id: null, why: error })), written: {} }
        : { success: false, error });
    } else if (kind === "bulk") {
      const written = {};
      for (const [path, change] of Object.entries(body.changes)) {
        files.set(path, changed(files.get(path), change));
        written[path] = { tags: files.get(path).slice() };
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

  test("a pill taken off while another removal is out, then typed back: written in the order clicked, the tag stays", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    await ctx.takeOff("Harbourside");
    ctx.$("input-add-tag").value = "Harbourside";
    ctx.$("btn-add-tag").click();
    await flush(ctx.window, 8);
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.order(), ["bulk", "bulk", "save"], "the save went ahead of the removal clicked before it");
    assert.deepEqual(ctx.file(A), ["Harbourside", "Kentridge"], "the removal clicked first undid the tag typed after it");
    assert.deepEqual(sorted(ctx.pills()), ["Harbourside", "Kentridge"]);
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
    assert.deepEqual(ctx.bulks()[1].changes[A.path], { id: null, add: [], remove: ["Harbourside"] }, "the failed tag was sent again");
    // The failure stays on the status line until its row is dismissed: the next write's "Writing tags..." and "Ready" do not
    // say over it (#3221).
    assert.equal(ctx.status(), "Not taken off Cross Country: The share did not answer", "the next write said over the failure");
    const { dismissEntry } = pageExports(ctx.window, "web/tagpup/write-queue.js");
    dismissEntry(row);
    await ctx.takeOff("Kentridge");
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.file(A), ["Cross Country"]);
    assert.deepEqual(ctx.pills(), ["Cross Country"]);
    assert.equal(ctx.status(), "Ready");
  });

  test("a second failure replaces the first on the status line", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    await ctx.takeOff("Harbourside");
    await ctx.answer("The share did not answer");
    await ctx.answer("The file is locked");
    assert.equal(ctx.status(), "Not taken off Harbourside: The file is locked");
  });

  test("a failed write of a pill's removal with another change of the photo says the tag is not taken off, and keeps the other for Retry", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    await ctx.takeOff("Harbourside");
    // A bulk click's change for the open photo, waiting with the pill's removal: one write of both.
    pageExports(ctx.window, "web/tagpup/pending.js").pendTags([A.path], { add: ["Places/Beach"] });
    await flush(ctx.window, 4);
    await ctx.answer();
    await ctx.answer("The share did not answer");
    assert.deepEqual(sorted(ctx.pills()), ["Harbourside", "Kentridge", "Places/Beach"], "the failed removal's tag was not drawn again");
    assert.deepEqual(ctx.alerts, ["Tags not written to 1 photo: The share did not answer. Not taken off Harbourside. "
      + "Retry or Cancel it in the list of writes (bottom right)."]);
    assert.equal(ctx.status(), "Tags not written to 1 photo. Not taken off Harbourside.");
    assert.equal(ctx.retries(), 1, "the other change lost its Retry");
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

// The one rule (pending.js takeChunk): a write -- a save, a chunk -- never runs against a record missing a change clicked before
// it, on any photo; so the chunk ahead of a save takes every photo's own removals waiting, in the order clicked, and they go even
// while a failed write waits for its Retry (#3210).
describe("V20: pills taken off on several photos, then a tag given back: the writes go in the order clicked", () => {
  const typeBack = async (ctx, tag) => {
    ctx.$("input-add-tag").value = tag;
    ctx.$("btn-add-tag").click();
    await flush(ctx.window, 8);
  };
  const pillsOnTwoPhotos = async (ctx) => {
    await ctx.takeOff("Cross Country");   // A: written at once, its write out
    await ctx.select(B);
    await ctx.takeOff("Kentridge");
    await ctx.select(A);
    await ctx.takeOff("Harbourside");
    await ctx.select(B);
  };

  test("pill off A, pill off B, pill off A, then B's tag typed back: B keeps it", async (t) => {
    const ctx = await open(t, { b: ["Kentridge"] });
    await pillsOnTwoPhotos(ctx);
    await typeBack(ctx, "Kentridge");
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.writes(), ["bulk:a", "bulk:b,a", "save:b"], "the save went ahead of a removal clicked before it");
    assert.deepEqual(ctx.file(B), ["Kentridge"], "the tag typed back was lost");
    assert.deepEqual(ctx.file(A), ["Kentridge"]);
    assert.deepEqual(ctx.pills(), ["Kentridge"]);
    assert.equal(ctx.$("input-add-tag").value, "");
    assert.equal(ctx.status(), "Saved b.jpg");
  });

  test("the same, B's tag given back by its suggestion: B keeps it, never 'already on this photo'", async (t) => {
    const ctx = await open(t, { b: ["Kentridge"] });
    pageExports(ctx.window, "web/tagpup/state.js").state.folderSuggestions[B.path] = { people: [], tags: [{ tag: "Kentridge", score: 0.9 }] };
    await pillsOnTwoPhotos(ctx);
    const chip = Array.from(ctx.document.querySelectorAll("#suggestions-section .suggestion-chip")).find((each) => each.textContent.startsWith("Kentridge"));
    assert.ok(chip, "the tag taken off was not offered again");
    chip.click();
    await flush(ctx.window, 8);
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.writes(), ["bulk:a", "bulk:b,a", "save:b"]);
    assert.deepEqual(ctx.file(B), ["Kentridge"], "the suggestion clicked was lost");
    assert.notEqual(ctx.status(), "Kentridge is already on this photo");
  });

  test("while a failed write waits for its Retry, a pill taken off and typed back: the removal goes first, the tag stays", async (t) => {
    const ctx = await open(t, { b: ["Kentridge"] });
    pageExports(ctx.window, "web/tagpup/pending.js").pendTags([A.path], { add: ["Places/Beach"] });
    await flush(ctx.window, 4);
    await ctx.answer("The share did not answer");
    assert.equal(ctx.retries(), 1);
    await ctx.select(B);
    await ctx.takeOff("Kentridge");
    await typeBack(ctx, "Kentridge");
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.writes(), ["bulk:a", "bulk:b", "save:b"], "the removal waited for the Retry behind the save");
    assert.deepEqual(ctx.file(B), ["Kentridge"], "the tag typed back was lost");
    assert.equal(ctx.retries(), 1, "the failed write's Retry went by itself, or was lost");
    assert.deepEqual(ctx.file(A), TAGS.slice().sort(), "the failed write went without its Retry");
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

  test("a person's pill taken off: their face card offers them again at once, and again settled if the write fails", async (t) => {
    const named = { id: 1, box: [400, 300, 800, 700], shown_box: [400, 300, 800, 700], area: 160000, name: "Rowan Ashby", prob: 0.99,
      excluded: false, suggestion: null, similarity: null };
    const ctx = await open(t, { b: ["People/Rowan Ashby"], faces: { faces: [named], total: 1, unmatched: 0, size: [4000, 3000], shown_size: [4000, 3000] } });
    await ctx.select(B);
    const card = () => ctx.document.querySelector(".face-card");
    assert.ok(card().classList.contains("face-card-settled"));
    await ctx.takeOff("People/Rowan Ashby");
    assert.ok(card().classList.contains("face-card-actionable"), "the face card still said the person is on the photo");
    await ctx.answer("The share did not answer");
    assert.ok(card().classList.contains("face-card-settled"), "the failed removal left the card offering the person");
  });

  test("a tag taken off the whole selection leaves its chips at the click, before the write is answered", async (t) => {
    const ctx = await open(t, { b: ["Kentridge"] });
    ctx.$("btn-select-all-thumbnails").click();
    await flush(ctx.window, 8);
    const chips = () => Array.from(ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")).map((c) => c.textContent);
    assert.ok(chips().some((c) => c.includes("Kentridge")));
    pageExports(ctx.window, "web/tagpup/selection.js").removeTagFromAllSelected("Kentridge", false);
    await flush(ctx.window, 4);
    assert.equal(ctx.unanswered(), 1);
    assert.ok(!chips().some((c) => c.includes("Kentridge")), "the selection's chips counted the tag taken off");
    while (ctx.unanswered()) await ctx.answer();
    assert.ok(!chips().some((c) => c.includes("Kentridge")));
  });

  test("Delete while the photo's own removal is out is refused, as the selection's is (#3203)", async (t) => {
    const ctx = await open(t);
    ctx.confirms = [];
    ctx.window.confirm = (text) => { ctx.confirms.push(text); return true; };
    await ctx.takeOff("Cross Country");
    ctx.$("btn-delete-photo").click();
    await flush(ctx.window, 4);
    assert.deepEqual(ctx.alerts, ["Tag changes for this photo are not written yet: wait for them, or cancel them in the list of writes, then delete."]);
    assert.deepEqual(ctx.confirms, []);
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.file(A), ["Harbourside", "Kentridge"]);
  });

  test("Ctrl+D on a photo read-only under another change waiting for it does nothing (#3204)", async (t) => {
    const ctx = await open(t);
    await ctx.takeOff("Cross Country");
    // A bulk click's change for B, waiting behind the removal out: B's panel is read-only until it is written.
    pageExports(ctx.window, "web/tagpup/pending.js").pendTags([B.path], { add: ["Places/Beach"] });
    await flush(ctx.window, 4);
    await ctx.select(B);
    assert.ok(!ctx.editable(), "the photo with a change waiting was editable");
    await ctx.carry();
    while (ctx.unanswered()) await ctx.answer();
    assert.deepEqual(ctx.saves(), [], "Ctrl+D wrote a photo whose panel is read-only");
    assert.deepEqual(ctx.file(B), ["Places/Beach"]);
  });
});

// A library view holds only the open photo's record, and the one before it (Ctrl+D's), read from the library when the open one
// is shown: a write's reply reaches whichever of them the page holds (pending.js heldRecords).
describe("V20: in a library view", () => {
  async function view(t) {
    const files = new Map([[11, ["Cross Country", "Harbourside"]], [12, []]]);
    // A photo renamed since the view read it: the path the library holds it at now, by id (`moved`).
    const moved = new Map();
    // A read of a photo in `held` is read now (what its file holds now) and answered when the test says (`answerReads`).
    const held = new Set();
    const reads = [];
    const onPhoto = (id) => {
      const now = moved.has(id) ? { path: moved.get(id), filename: moved.get(id).split("\\").pop() } : {};
      const photo = recordOf(id, { tags: (files.get(id) || []).slice(), ...now });
      return held.has(id) ? new Promise((resolve) => reads.push(() => resolve({ photo }))) : { photo };
    };
    const ctx = await loadViewPage(t, { search: "?view=all", onPhoto });
    ctx.out = [];
    ctx.files = files;
    ctx.moved = moved;
    ctx.holdReads = (...ids) => ids.forEach((id) => held.add(id));
    ctx.answerReads = async () => {
      held.clear();
      reads.splice(0).forEach((answer) => answer());
      await ctx.settle(80);
    };
    ctx.pills = () => Array.from(ctx.document.querySelectorAll("#detail-tags .tag-pill, #detail-people .tag-pill")).map((each) => each.textContent);
    ctx.server.first("/api/photos/write", () => new Promise((resolve) => ctx.out.push({ body: ctx.server.calls.at(-1).body, resolve })));
    ctx.answerWrite = async () => {
      const { body, resolve } = ctx.out.shift();
      const written = {};
      for (const [path, change] of Object.entries(body.changes)) {
        // A library view's photo is named by the id of the record the page holds of it.
        const id = change.id;
        assert.equal(id, Number(path.match(/IMG_(\d+)/)[1]), "a view's photo was not named by its id");
        files.set(id, changed(files.get(id), change));
        written[path] = { tags: files.get(id).slice() };
      }
      resolve({ success: true, written });
      await ctx.settle(40);
    };
    // The oldest write out refused whole, as the server refuses a photo named by an id no longer at the path sent (409).
    ctx.refuseWrite = async () => {
      const { body, resolve } = ctx.out.shift();
      const [path] = Object.keys(body.changes);
      resolve(answer(409, { success: false, changed_on_disk: true, written: {}, errors: [], skipped: [],
        error: `${path.split("\\").pop()} is not the photo this page read (renamed, moved or deleted since): reload it first.` }));
      await ctx.settle(40);
    };
    ctx.sent = () => ctx.server.calls.filter((c) => c.url.includes("/api/photos/write")).map((c) => c.body);
    ctx.reads = (id) => ctx.server.calls.filter((c) => c.url.includes(`/api/library/photo?id=${id}`)).length;
    ctx.failedRows = () => [...pageExports(ctx.window, "web/tagpup/state.js").state.writeQueue.entries.filter((e) => e.status === "failed")];
    ctx.openPhoto = async (id) => {
      ctx.cardById(id).querySelector(".btn-thumbnail-detail").click();
      await ctx.settle(80);
    };
    ctx.takeOff = async (text) => {
      Array.from(ctx.document.querySelectorAll(".tag-pill")).find((each) => each.textContent === text).click();
      await ctx.settle(20);
    };
    ctx.saves = () => saveCalls(ctx.server).map((c) => c.body);
    return ctx;
  }

  test("a pill taken off, the next photo opened, the write answered: Ctrl+D copies the tags the photo before holds now", async (t) => {
    const ctx = await view(t);
    await ctx.openPhoto(11);
    await ctx.takeOff("Cross Country");
    await ctx.openPhoto(12);
    assert.equal(ctx.out.length, 1, "the removal was not out while the next photo was opened");
    await ctx.answerWrite();
    assert.equal(ctx.document.getElementById("btn-carry-forward").title, "Copy 1 tag(s) from IMG_11.jpg  (Ctrl+D)",
      "the button offered the tag taken off");
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "d", ctrlKey: true, bubbles: true, cancelable: true }));
    await ctx.settle(80);
    assert.deepEqual(ctx.saves().map((s) => s.tags), [["Harbourside"]], "Ctrl+D copied the tag the pill took off");
  });

  test("the photo before, read while its removal is out and answered after the write: Ctrl+D offers what its file holds (#3225)", async (t) => {
    const ctx = await view(t);
    await ctx.openPhoto(11);
    await ctx.takeOff("Cross Country");
    ctx.holdReads(11);
    await ctx.openPhoto(12);
    await ctx.answerWrite();
    await ctx.answerReads();
    assert.deepEqual(ctx.files.get(11), ["Harbourside"]);
    assert.equal(ctx.document.getElementById("btn-carry-forward").title, "Copy 1 tag(s) from IMG_11.jpg  (Ctrl+D)",
      "the photo before was read before the write and kept the tag taken off");
  });

  test("a pill taken off, away and straight back as the write lands: the photo shows what its file holds (#3227)", async (t) => {
    const ctx = await view(t);
    await ctx.openPhoto(11);
    await ctx.takeOff("Cross Country");
    await ctx.openPhoto(12);
    ctx.holdReads(11);
    await ctx.openPhoto(11);
    await ctx.answerWrite();
    await ctx.answerReads();
    assert.deepEqual(ctx.files.get(11), ["Harbourside"]);
    assert.deepEqual(ctx.pills(), ["Harbourside"], "the photo read before the write showed the tag taken off");
  });

  test("the selection's Delete while the open photo's removal is out is refused, as a folder's is (#3203)", async (t) => {
    const ctx = await view(t);
    ctx.alerts = [];
    ctx.window.alert = (text) => ctx.alerts.push(text);
    await ctx.openPhoto(11);
    await ctx.takeOff("Cross Country");
    ctx.cardById(11).querySelector(".thumbnail-checkbox").click();
    ctx.document.getElementById("btn-delete-selection").click();
    await ctx.settle(40);
    assert.deepEqual(ctx.alerts, ["Tag changes for 1 photo are not written yet: wait for them, or cancel them in the list of writes, then delete."]);
    assert.deepEqual(ctx.server.calls.filter((c) => c.url.includes("/selection/delete-check")), [], "the delete was asked of the server");
    await ctx.answerWrite();
    assert.deepEqual(ctx.files.get(11), ["Harbourside"]);
  });

  describe("V20: a photo renamed or deleted since the library view read it", () => {
    const RENAMED = "D:\\Library\\2020\\Event 01\\IMG_11 Harbour run.jpg";
    const AGAIN = "D:\\Library\\2020\\Event 01\\IMG_11 Harbour run at dusk.jpg";
    const OLD = recordOf(11).path;
    const outcome = (settled, ctx) => Promise.race([settled.then((counts) => ({ ...counts })), ctx.settle(200).then(() => "never settled")]);

    test("a pill taken off: the write is refused, the photo read again by its id, and the change sent to its new name", async (t) => {
      const ctx = await view(t);
      ctx.alerts = [];
      ctx.window.alert = (text) => ctx.alerts.push(text);
      await ctx.openPhoto(11);
      await ctx.takeOff("Cross Country");
      assert.deepEqual(Object.keys(ctx.sent()[0].changes), [OLD]);
      ctx.moved.set(11, RENAMED);   // renamed by a save in another tab
      const reads = ctx.reads(11);
      await ctx.refuseWrite();
      assert.equal(ctx.reads(11), reads + 1, "the photo was not read again by its id");
      assert.equal(ctx.out.length, 1, "the refused change was not sent again");
      assert.deepEqual(ctx.sent()[1].changes, { [RENAMED]: { id: 11, add: [], remove: ["Cross Country"] } });
      await ctx.answerWrite();
      assert.deepEqual(ctx.files.get(11), ["Harbourside"]);
      assert.deepEqual(ctx.pills(), ["Harbourside"]);
      assert.equal(ctx.state.activePhotoPath, RENAMED, "the open photo is still named by the path it had");
      assert.deepEqual(ctx.alerts, [], "a photo read again and written was said as a failure");
      assert.deepEqual(ctx.failedRows(), []);
    });

    test("an Undo's change on a photo deleted since: the read answers 404, it settles skipped, and nothing is sent again", async (t) => {
      const ctx = await view(t);
      await ctx.openPhoto(11);
      ctx.server.first(/\/api\/library\/photo\?id=11(?!\d)/, { error: "No such photo." }, { status: 404 });
      // As undo.js pends a change taken back.
      const { pendChanges } = pageExports(ctx.window, "web/tagpup/pending.js");
      const settled = pendChanges([{ path: OLD, add: ["Places/Coast"] }]);
      await ctx.settle(20);
      await ctx.refuseWrite();
      assert.deepEqual(await outcome(settled, ctx), { written: 0, failed: 0, skipped: 1 });
      assert.equal(ctx.sent().length, 1, "a photo the library no longer has was sent again");
      assert.equal(ctx.out.length, 0);
      assert.deepEqual(ctx.failedRows(), [], "a photo deleted since was said as a failure");
      // #3289: said, as the phone says it, and the row does not read as done.
      assert.match(ctx.document.getElementById("status-text").textContent, /^1 photo is missing on disk and was left out\./);
      const labels = pageExports(ctx.window, "web/tagpup/state.js").state.writeQueue.entries.map((e) => e.label);
      assert.ok(labels.some((l) => /1 photo no longer in the library/.test(l)), `the row did not say the photo is gone: ${labels}`);
    });

    test("two pills taken off while the refused photo is read again: one write to its new name, no failure (#3290)", async (t) => {
      const ctx = await view(t);
      ctx.alerts = [];
      ctx.window.alert = (text) => ctx.alerts.push(text);
      await ctx.openPhoto(11);
      await ctx.takeOff("Cross Country");
      ctx.moved.set(11, RENAMED);
      ctx.holdReads(11);
      await ctx.refuseWrite();
      await ctx.takeOff("Harbourside");   // clicked within the re-read: keyed at the old path
      await ctx.answerReads();
      assert.equal(ctx.out.length, 1, "the re-read change was not sent");
      assert.deepEqual(ctx.sent()[1].changes, { [RENAMED]: { id: 11, add: [], remove: ["Cross Country", "Harbourside"] } });
      await ctx.answerWrite();
      assert.deepEqual(ctx.files.get(11), []);
      assert.deepEqual(ctx.alerts, [], "a photo read again was said as a failure");
      assert.deepEqual(ctx.failedRows(), []);
    });

    test("refused again after the read: a failure the drain stops at; its Retry reads the photo again and writes it", async (t) => {
      const ctx = await view(t);
      ctx.alerts = [];
      ctx.window.alert = (text) => ctx.alerts.push(text);
      await ctx.openPhoto(11);
      const { pendChanges } = pageExports(ctx.window, "web/tagpup/pending.js");
      const settled = pendChanges([{ path: OLD, add: ["Places/Coast"] }]);
      await ctx.settle(20);
      ctx.moved.set(11, RENAMED);
      await ctx.refuseWrite();
      ctx.moved.set(11, AGAIN);   // renamed again between the read and the write
      await ctx.refuseWrite();
      assert.equal(ctx.out.length, 0, "a second refusal was sent again by itself");
      const [row] = ctx.failedRows();
      assert.ok(row && row.retry, "the second refusal is not a failure with a Retry");
      assert.match(row.error, /is not the photo this page read/);
      assert.equal(ctx.alerts.length, 1, "the failure was not said");

      row.retry();
      await ctx.settle(40);
      assert.deepEqual(Object.keys(ctx.sent()[2].changes), [RENAMED], "Retry did not send the change waiting");
      await ctx.refuseWrite();   // still not there: read again, once more
      assert.deepEqual(Object.keys(ctx.sent()[3].changes), [AGAIN], "Retry's refusal did not read the photo again");
      await ctx.answerWrite();
      assert.deepEqual(ctx.files.get(11), ["Cross Country", "Harbourside", "Places/Coast"]);
      assert.deepEqual(await outcome(settled, ctx), { written: 1, failed: 0, skipped: 0 }, "the change named at its new path never settled");
    });

    test("a Delete while the refused photo is read again waits for it, as for any change not written", async (t) => {
      const ctx = await view(t);
      ctx.alerts = [];
      ctx.window.alert = (text) => ctx.alerts.push(text);
      await ctx.openPhoto(11);
      await ctx.takeOff("Cross Country");
      ctx.moved.set(11, RENAMED);
      ctx.holdReads(11);
      await ctx.refuseWrite();
      ctx.cardById(11).querySelector(".thumbnail-checkbox").click();
      ctx.document.getElementById("btn-delete-selection").click();
      await ctx.settle(40);
      assert.deepEqual(ctx.alerts, ["Tag changes for 1 photo are not written yet: wait for them, or cancel them in the list of writes, then delete."]);
      assert.deepEqual(ctx.server.calls.filter((c) => c.url.includes("/selection/delete-check")), [], "the delete was asked of the server");
      await ctx.answerReads();
      await ctx.answerWrite();
      assert.deepEqual(ctx.files.get(11), ["Harbourside"]);
    });
  });
});
