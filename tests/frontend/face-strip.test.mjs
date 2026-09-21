/**
 * The detected-faces strip in TagPup's details panel.
 *
 * TagPup has always run face recognition -- the suggester needs it to propose people --
 * but showed only a name pill, so an unrecognised face was invisible until you opened
 * TagTuner later. This strip puts the crops in front of you while tagging.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder } from "./harness.mjs";

const PHOTO = photoRecord({ filename: "a.jpg" });

function baseServer(faces) {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", ["Jane Doe"])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [PHOTO])
    .on("/api/photo-faces", faces);
}

async function openPhoto(t, faces) {
  const ctx = await loadApp("tagpup", {
    t,
    url: "http://localhost:8090/photo_index/",
    server: baseServer(faces),
  });
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });

  ctx.document.querySelector(".thumbnail-card .thumbnail-img-wrapper")?.click();
  // Opening via the sidebar list is the reliable path in the test DOM.
  const item = ctx.document.querySelector(`li[data-path]`);
  if (item) item.click();
  await flush(ctx.window, 6);

  ctx.section = () => ctx.document.getElementById("faces-section");
  ctx.cards = () => [...ctx.document.querySelectorAll(".face-card")];
  ctx.summary = () => ctx.document.getElementById("faces-summary").textContent;
  return ctx;
}

const hidden = (el) => !el || el.classList.contains("hidden");

describe("face strip", () => {
  test("stays hidden when the photo has no detected faces", async (t) => {
    const ctx = await openPhoto(t, { faces: [], total: 0, unmatched: 0 });
    assert.ok(hidden(ctx.section()), "strip shown with no faces");
  });

  test("renders one card per detected face", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [
        { id: 1, box: [0, 0, 50, 50], area: 2500, name: "Jane Doe", excluded: false },
        { id: 2, box: [60, 0, 110, 50], area: 2500, name: null, suggestion: null, excluded: false },
      ],
      total: 2,
      unmatched: 1,
    });
    assert.ok(!hidden(ctx.section()), "strip stayed hidden");
    assert.equal(ctx.cards().length, 2);
  });

  test("a named face shows its name", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{ id: 1, box: [0, 0, 9, 9], area: 81, name: "Jane Doe", excluded: false }],
      total: 1,
      unmatched: 0,
    });
    assert.match(ctx.cards()[0].textContent, /Jane Doe/);
  });

  test("an unidentified face is flagged", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{ id: 2, box: [0, 0, 9, 9], area: 81, name: null, suggestion: null, excluded: false }],
      total: 1,
      unmatched: 1,
    });
    const card = ctx.cards()[0];
    assert.ok(card.classList.contains("unmatched"), "face not marked unmatched");
    assert.match(card.textContent, /Unidentified/);
  });

  test("a suggested name is shown with its confidence", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{
        id: 3, box: [0, 0, 9, 9], area: 81, name: null,
        suggestion: "Jane Doe", similarity: 0.7503, excluded: false,
      }],
      total: 1,
      unmatched: 1,
    });
    const text = ctx.cards()[0].textContent;
    assert.match(text, /Jane Doe/);
    assert.match(text, /75/, `confidence not shown: ${text}`);
  });

  test("the summary counts what still needs identifying", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [
        { id: 1, box: [0, 0, 9, 9], area: 81, name: "Jane Doe", excluded: false },
        { id: 2, box: [0, 0, 9, 9], area: 81, name: null, suggestion: null, excluded: false },
      ],
      total: 2,
      unmatched: 1,
    });
    assert.match(ctx.summary(), /2 detected/);
    assert.match(ctx.summary(), /1 unidentified/);
  });

  test("an all-identified photo says so", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{ id: 1, box: [0, 0, 9, 9], area: 81, name: "Jane Doe", excluded: false }],
      total: 1,
      unmatched: 0,
    });
    assert.match(ctx.summary(), /all identified/);
  });

  test("an excluded face is shown dimmed with its reason", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{
        id: 4, box: [0, 0, 9, 9], area: 81, name: null,
        excluded: true, excluded_reason: "stranger",
      }],
      total: 1,
      unmatched: 0,
    });
    const card = ctx.cards()[0];
    assert.ok(card.classList.contains("excluded"), "excluded face not marked");
    assert.match(card.textContent, /stranger/);
  });

  test("crops are requested through the database prefix", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{ id: 7, box: [0, 0, 9, 9], area: 81, name: "Jane Doe", excluded: false }],
      total: 1,
      unmatched: 0,
    });
    const src = ctx.cards()[0].querySelector("img").getAttribute("src");
    assert.ok(
      src.startsWith("/photo_index/api/face-crop"),
      `crop bypassed the database prefix: ${src}`
    );
    assert.match(src, /id=7/);
  });

  test("clicking a suggested face applies that person", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{
        id: 3, box: [0, 0, 9, 9], area: 81, name: null,
        suggestion: "Jane Doe", similarity: 0.9, excluded: false,
      }],
      total: 1,
      unmatched: 1,
    });
    const card = ctx.cards()[0];
    assert.ok(card.classList.contains("face-card-actionable"), "suggestion not clickable");

    click(ctx.window, card);
    await flush(ctx.window, 4);

    const asked = ctx.server.urls().some((u) => u.includes("/api/photo/save-metadata"));
    assert.ok(asked, "clicking a suggestion did not apply the person");
  });

  test("a face with no suggestion is not clickable", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{ id: 2, box: [0, 0, 9, 9], area: 81, name: null, suggestion: null, excluded: false }],
      total: 1,
      unmatched: 1,
    });
    assert.ok(!ctx.cards()[0].classList.contains("face-card-actionable"));
  });

  test("returning to the folder view clears the strip", async (t) => {
    const ctx = await openPhoto(t, {
      faces: [{ id: 1, box: [0, 0, 9, 9], area: 81, name: "Jane Doe", excluded: false }],
      total: 1,
      unmatched: 0,
    });
    assert.ok(!hidden(ctx.section()));

    ctx.document.getElementById("folder-view-header").click();
    await flush(ctx.window, 3);
    assert.ok(hidden(ctx.section()), "strip left visible on the folder view");
  });
});
