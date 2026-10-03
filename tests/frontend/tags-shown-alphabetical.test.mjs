/**
 * TagPup shows tags and people alphabetically wherever they are a set (web/common/vocabulary.js,
 * sortedTags): a photo's pills, the selection's tallies, the autocomplete lists, the question where a new
 * tag belongs. A list ranked on purpose -- the AI suggestions, by how sure it was -- keeps its rank and breaks a
 * tie with the alphabet. The order is the display's only: a save sends the photo's tags in the order the file
 * holds them.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Wen Zhao", name: "Wen Zhao", parent_id: 1, has_face: 1 },
  { id: 3, tag: "People/anh Tran", name: "anh Tran", parent_id: 1, has_face: 1 },
  { id: 4, tag: "Zoo", name: "Zoo", parent_id: null, has_face: 0 },
  { id: 5, tag: "apple", name: "apple", parent_id: null, has_face: 0 },
  { id: 6, tag: "Banana", name: "Banana", parent_id: null, has_face: 0 },
];

// In the order a file would hold them: how they were added, not the alphabet.
const IN_FILE_ORDER = ["Zoo", "Trip 10", "People/Wen Zhao", "apple", "Trip 3", "People/anh Tran", "Banana"];

async function open(t, { photos, suggestions = {}, tags = [], people = [] } = {}) {
  const list = photos || [photoRecord({ filename: "a.jpg", tags: IN_FILE_ORDER, people: ["Wen Zhao", "anh Tran"] })];
  const server = new FakeServer()
    .on("/api/tags", tags)
    .on("/api/people", people)
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100 })
    .on("/api/folder/scan", list)
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/folder/suggest-status", { status: "completed", suggestions });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, "D:/Library/2020");
  ctx.photos = list;
  return ctx;
}

async function openPhoto(ctx) {
  ctx.document.querySelector("li[data-path]").click();
  await flush(ctx.window, 8);
}

const texts = (ctx, selector) => [...ctx.document.querySelectorAll(selector)].map((el) => el.textContent);
const saves = (ctx) => ctx.server.calls.filter((c) => c.url.includes("save-metadata"));

describe("a photo's pills", () => {
  test("tags and people are each alphabetical", async (t) => {
    const ctx = await open(t);
    await openPhoto(ctx);
    assert.deepEqual(texts(ctx, "#detail-tags .tag-pill"), ["apple", "Banana", "Trip 3", "Trip 10", "Zoo"]);
    assert.deepEqual(texts(ctx, "#detail-people .tag-pill"), ["People/anh Tran", "People/Wen Zhao"]);
  });

  test("a save sends the tags in the order the file holds them, minus the one removed", async (t) => {
    const ctx = await open(t);
    await openPhoto(ctx);
    const pill = [...ctx.document.querySelectorAll("#detail-tags .tag-pill")].find((p) => p.textContent === "Banana");
    click(ctx.window, pill);
    await flush(ctx.window, 8);
    assert.equal(saves(ctx).length, 1);
    assert.deepEqual(saves(ctx)[0].body.tags,
      ["Zoo", "Trip 10", "People/Wen Zhao", "apple", "Trip 3", "People/anh Tran"]);  });
});

describe("the lists the fields offer", () => {
  test("the tag list is alphabetical, folder tags merged in", async (t) => {
    const ctx = await open(t, { tags: ["Zoo", "apple", "Trip 10", "Trip 3", "Éclair"] });
    await flush(ctx.window, 8);
    const offered = [...ctx.document.querySelectorAll("#tags-datalist option")].map((o) => o.value);
    assert.deepEqual(offered, ["apple", "Banana", "Éclair", "Trip 3", "Trip 10", "Zoo"]);
  });

  test("the people list is alphabetical", async (t) => {
    const ctx = await open(t, { tags: ["People/Wen Zhao", "People/anh Tran", "People/Émile Roy", "People/Bao Le"] });
    await flush(ctx.window, 8);
    const offered = [...ctx.document.querySelectorAll("#people-datalist option")].map((o) => o.value);
    assert.deepEqual(offered, ["People/anh Tran", "People/Bao Le", "People/Émile Roy", "People/Wen Zhao"]);
  });
});

describe("where a new tag belongs", () => {
  test("the roots offered are alphabetical", async (t) => {
    const ctx = await open(t, { photos: [photoRecord({ filename: "a.jpg", tags: [] })] });
    await openPhoto(ctx);
    const input = ctx.document.getElementById("input-add-tag");
    input.focus();
    input.value = "Newthing";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);
    const labels = texts(ctx, ".placement-options .placement-option-label");
    assert.deepEqual(labels, ["apple", "Banana", "Zoo", "Create a new root category..."]);
  });
});

describe("the selection's tallies", () => {
  test("tags and people are alphabetical", async (t) => {
    const one = photoRecord({ filename: "a.jpg", tags: ["Zoo", "Trip 10", "People/Wen Zhao"], people: ["Wen Zhao"] });
    const two = photoRecord({ filename: "b.jpg", tags: ["apple", "Trip 3", "People/anh Tran"], people: ["anh Tran"] });
    const ctx = await open(t, { photos: [one, two] });
    for (const box of ctx.document.querySelectorAll(".thumbnail-checkbox")) click(ctx.window, box);
    await flush(ctx.window, 4);
    const chips = (id) => texts(ctx, `#${id} .selection-summary-chip`).map((s) => s.replace(/ [^ ]*$/, ""));
    assert.deepEqual(chips("selection-tags-list").map((s) => s.replace(/ \(\d+\).*$/, "")),
      ["apple", "Trip 3", "Trip 10", "Zoo"]);
    assert.deepEqual(chips("selection-people-list").map((s) => s.replace(/ \(\d+\).*$/, "")),
      ["anh Tran", "Wen Zhao"]);
  });
});

describe("the AI suggestions", () => {
  test("stay ranked by confidence, the alphabet breaking a tie", async (t) => {
    const photo = photoRecord({ filename: "a.jpg" });
    const ctx = await open(t, {
      photos: [photo],
      suggestions: {
        [photo.path]: {
          people: [],
          tags: [
            { tag: "Zoo", score: 0.5 }, { tag: "apple", score: 0.5 }, { tag: "Banana", score: 0.9 },
            { tag: "Trip 10", score: 0.3 }, { tag: "Trip 3", score: 0.3 },
          ],
        },
      },
    });
    await openPhoto(ctx);
    assert.deepEqual(texts(ctx, "#suggested-tags-container .suggestion-chip").map((s) => s.replace(/ ·.*$/, "")),
      ["Banana", "apple", "Zoo", "Trip 3", "Trip 10"]);
  });
});
