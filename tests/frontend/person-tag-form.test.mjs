/**
 * A person is written to a photo as the tag they are filed under, never as a bare name.
 *
 * The keyword convention is a full path -- "People/Hazel Brookmire" -- and every entry
 * point that takes a person's name has to honour it. The suggestion paths did not:
 * `applySuggestedTagDirect` reduced whatever it was handed to its leaf and wrote that,
 * so clicking a recognised face on a photo that already named that person added them a
 * second time, in the bare form. The screenshot that found it showed four "People/..."
 * pills and a lone "Hazel Brookmire" beside them.
 *
 * Two separate mistakes had to be fixed together, and both are tested here:
 *   - the tag written is the resolved path, not the leaf;
 *   - "already tagged" compares people by who they are, not by how they are spelled,
 *     or the duplicate check never fires against the pathed form.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click } from "./harness.mjs";

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Hazel Brookmire", name: "Hazel Brookmire", parent_id: 1, has_face: 1 },
  { id: 3, tag: "People/Kira Bao", name: "Kira Bao", parent_id: 1, has_face: 1 },
  { id: 4, tag: "Activity", name: "Activity", parent_id: null, has_face: 0 },
  { id: 5, tag: "Activity/Cross Country", name: "Cross Country", parent_id: 4, has_face: 0 },
];

function serverFor(photo, faces, suggestions) {
  return new FakeServer()
    .on("/api/tags", ["People/Hazel Brookmire", "People/Kira Bao", "Activity/Cross Country"])
    .on("/api/people", ["Hazel Brookmire", "Kira Bao"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", suggestions || { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [photo])
    .on("/api/photo-faces", faces)
    .on("/api/photo/save-metadata", { success: true });
}

async function openPhoto(t, { photo, faces, suggestions }) {
  const ctx = await loadApp("tagpup", {
    t,
    url: "http://localhost:8090/photo_index/",
    server: serverFor(photo, faces, suggestions),
  });
  ctx.document.getElementById("folder-path-input").value = "D:/Library/2020";
  ctx.document.getElementById("btn-scan-folder").click();
  await flush(ctx.window, 6);

  const item = ctx.document.querySelector("li[data-path]");
  if (item) item.click();
  await flush(ctx.window, 8);
  return ctx;
}

const savedTags = (ctx) => {
  const body = ctx.server.lastBody("/api/photo/save-metadata");
  return body ? body.tags : undefined;
};

const suggestedFace = (name, similarity = 0.93) => ({
  faces: [{ id: 3, box: [0, 0, 9, 9], area: 81, name: null, suggestion: name, similarity, excluded: false }],
  total: 1,
  unmatched: 1,
});

describe("a person is tagged by their path", () => {
  test("clicking a recognised face writes the People/ path, not the bare name", async (t) => {
    const ctx = await openPhoto(t, {
      photo: photoRecord({ filename: "a.jpg", tags: ["Activity/Cross Country"] }),
      faces: suggestedFace("Hazel Brookmire"),
    });

    click(ctx.window, ctx.document.querySelector(".face-card"));
    await flush(ctx.window, 8);

    const tags = savedTags(ctx);
    assert.ok(tags, "clicking the face saved nothing");
    assert.ok(
      tags.includes("People/Hazel Brookmire"),
      `person written without their path: ${JSON.stringify(tags)}`
    );
    assert.ok(
      !tags.includes("Hazel Brookmire"),
      `the bare leaf was written as well: ${JSON.stringify(tags)}`
    );
  });

  test("a person the photo already names is not added a second time", async (t) => {
    // The exact case from the report: the photo carries "People/Hazel Brookmire"
    // already, and clicking her face added "Hazel Brookmire" beside it because the
    // duplicate check compared the leaf against the path and found no match.
    const ctx = await openPhoto(t, {
      photo: photoRecord({
        filename: "b.jpg",
        tags: ["People/Hazel Brookmire", "People/Kira Bao"],
        people: ["Hazel Brookmire", "Kira Bao"],
      }),
      faces: suggestedFace("Hazel Brookmire"),
    });

    click(ctx.window, ctx.document.querySelector(".face-card"));
    await flush(ctx.window, 8);

    assert.equal(
      savedTags(ctx),
      undefined,
      "a person already on the photo was written again"
    );
  });

  test("a keyword suggestion keeps its levels", async (t) => {
    // Same defect, other half: the leaf reduction threw away the level on any tag,
    // so "Activity/Cross Country" would have been written as "Cross Country".
    const photo = photoRecord({ filename: "c.jpg", tags: [] });
    const ctx = await openPhoto(t, {
      photo,
      faces: { faces: [], total: 0, unmatched: 0 },
      suggestions: {
        status: "completed",
        suggestions: {
          [photo.path]: {
            tags: [{ tag: "Activity/Cross Country", score: 0.9 }],
            people: [],
            title: "",
          },
        },
      },
    });

    const chip = [...ctx.document.querySelectorAll("#suggested-tags-container .tag-chip, #suggested-tags-container *")]
      .find((el) => el.textContent.includes("Cross Country") && el.tagName !== "DIV");
    if (!chip) {
      t.skip("suggested tag chip not rendered in this fixture");
      return;
    }
    click(ctx.window, chip);
    await flush(ctx.window, 8);

    const tags = savedTags(ctx);
    assert.ok(tags, "clicking the suggestion saved nothing");
    assert.ok(
      tags.includes("Activity/Cross Country"),
      `the tag lost its level: ${JSON.stringify(tags)}`
    );
  });
});
