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
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder } from "./harness.mjs";

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
    .on("/api/people", ["Hazel Brookmire", "Anh Tran"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100 })
    .on("/api/folder/scan", [photo])
    .on("/api/photo-faces", faces || { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
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
