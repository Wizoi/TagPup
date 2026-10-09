/**
 * TagPup with two people called alike (identity by id, stage 2, part C, phase 6): the owner's real case is one shared leaf, a pet and
 * a friend both called Sam. Their keywords are different paths (Friends/Sam, Pets/Sam), so a photo can carry both; the answers are
 * the server's shapes (/api/people?records=1, a face's `person` and `suggestion_person`, a match's `person`, a suggestion's
 * `person`). Names are made up.
 *
 * What the page must do: show `Sam · Friends` and `Sam · Pets` wherever the name is shared and the plain name where it is not;
 * never take the second Sam for the first (a photo that has one may be given the other, a face that is one may not be offered
 * again as that one); and name a face by the person's id, with the exact keyword written first.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const SAM_FRIEND = { id: 40, name: "Sam", tag: "Friends/Sam", group: "Friends", shared: true };
const SAM_PET = { id: 41, name: "Sam", tag: "Pets/Sam", group: "Pets", shared: true };
const WREN = { id: 52, name: "Wren", tag: "Family/Ingersoll/Wren", group: "", shared: false };
const RECORDS = [SAM_FRIEND, SAM_PET, WREN];

const TAXONOMY = [
  { id: 1, tag: "Friends", name: "Friends", parent_id: null, has_face: 1 },
  { id: 2, tag: "Friends/Sam", name: "Sam", parent_id: 1, has_face: 1 },
  { id: 3, tag: "Pets", name: "Pets", parent_id: null, has_face: 1 },
  { id: 4, tag: "Pets/Sam", name: "Sam", parent_id: 3, has_face: 1 },
  { id: 5, tag: "Family", name: "Family", parent_id: null, has_face: 1 },
  { id: 6, tag: "Family/Ingersoll", name: "Ingersoll", parent_id: 5, has_face: 1 },
  { id: 7, tag: "Family/Ingersoll/Wren", name: "Wren", parent_id: 6, has_face: 1 },
  { id: 8, tag: "Trips", name: "Trips", parent_id: null, has_face: 0 },
];

const face = (id, box, extra = {}) => ({
  id, box, area: (box[2] - box[0]) * (box[3] - box[1]), name: null, prob: 0.99, excluded: false, suggestion: null, similarity: null, ...extra,
});

/** Two faces on a 4000 x 3000 photo: the first is the friend Sam, the second is not named. */
const FACES = {
  faces: [face(1, [400, 300, 800, 700], { name: "Sam", person: SAM_FRIEND }), face(2, [1000, 500, 1400, 900])],
  total: 2, unmatched: 1, size: [4000, 3000], turned: false,
};

const MATCHES = [
  { name: "Sam", tag: "Friends/Sam", similarity: 0.93, band: "likely", person: SAM_FRIEND },
  { name: "Sam", tag: "Pets/Sam", similarity: 0.88, band: "likely", person: SAM_PET },
  { name: "Wren", tag: "Family/Ingersoll/Wren", similarity: 0.71, band: "possible", person: WREN },
];

function serverFor({ photo = null, faces = FACES, matches = MATCHES, extra = null } = {}) {
  const shown = photo || photoRecord({ filename: "a.jpg", tags: ["Trips", "Friends/Sam"], people: ["Sam"] });
  const held = structuredClone(faces);
  let keywords = [...shown.tags];
  const server = new FakeServer()
    .on("/api/tags", ["Trips", "Friends/Sam", "Pets/Sam", "Family/Ingersoll/Wren"])
    .first("/api/people?records=1&include_hidden=1", RECORDS)
    .first("/api/people?records=1", RECORDS)
    .on("/api/people", ["Sam", "Wren"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100 })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/scan", [shown])
    .on("/api/photo-faces", () => ({ ...structuredClone(held), unmatched: held.faces.filter((each) => !each.name && !each.excluded).length }))
    .on("/api/photo/save-metadata", () => {
      keywords = [...server.calls[server.calls.length - 1].body.tags];
      return { success: true };
    })
    .on("/api/face/match", () => ({ success: true, changed: 1, untag: {} }))
    .first("/api/face-matches", () => structuredClone(matches))
    .first("/api/people-face-samples", { "id:40": [11, 12], "id:41": [21], "id:52": [31] });
  if (extra) extra(server);
  server.shown = shown;
  server.keywords = () => keywords;
  return server;
}

async function openPhoto(t, options = {}) {
  const server = serverFor(options);
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:/Library/2020");
  const row = ctx.document.querySelector("li[data-path]");
  if (row) row.click();
  ctx.server = server;
  ctx.$ = (id) => ctx.document.getElementById(id);
  const image = ctx.$("main-image");
  for (const [name, value] of [["clientWidth", 400], ["clientHeight", 300], ["offsetLeft", 20], ["offsetTop", 20]]) {
    Object.defineProperty(image, name, { value, configurable: true });
  }
  await flush(ctx.window, 8);
  image.dispatchEvent(new ctx.window.Event("load"));
  ctx.layer = () => ctx.$("face-layer");
  ctx.boxes = () => [...ctx.layer().querySelectorAll(".face-box")];
  ctx.panel = () => ctx.layer().querySelector(".face-panel");
  ctx.posts = (route) => server.calls.filter((call) => call.method === "POST" && call.url.includes(route));
  ctx.wait = (ms = 40) => new Promise((resolve) => ctx.window.setTimeout(resolve, ms));
  ctx.openFace = async (index) => {
    click(ctx.window, ctx.layer().querySelector(".face-boxes-toggle"));
    click(ctx.window, ctx.boxes()[index]);
    await flush(ctx.window, 6);
  };
  return ctx;
}

describe("a face named as one of two people called alike", () => {
  test("is told by its label on its box, in the panel and on the strip", async (t) => {
    const ctx = await openPhoto(t);
    click(ctx.window, ctx.layer().querySelector(".face-boxes-toggle"));
    assert.equal(ctx.boxes()[0].querySelector(".face-box-name").textContent, "Sam · Friends");
    assert.match(ctx.boxes()[0].getAttribute("aria-label"), /Face 1 of 2: Sam · Friends/);
    assert.equal(ctx.boxes()[0].title, "Friends/Sam. Click to change it.");
    assert.equal(ctx.document.querySelector(".face-card-label").textContent, "Sam · Friends");
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    assert.equal(ctx.panel().querySelector(".face-panel-who").textContent, "Sam · Friends");
  });

  test("a name nobody shares is shown as it is", async (t) => {
    const ctx = await openPhoto(t, { faces: { ...FACES, faces: [face(1, [400, 300, 800, 700], { name: "Wren", person: WREN }), FACES.faces[1]] } });
    assert.equal(ctx.document.querySelector(".face-card-label").textContent, "Wren");
  });
});

describe("the panel of the second face", () => {
  test("offers the OTHER Sam, by label, and not the one the first face already is; the title says which", async (t) => {
    const ctx = await openPhoto(t);
    await ctx.openFace(1);
    const offered = [...ctx.panel().querySelectorAll(".face-panel-suggestion")];
    assert.deepEqual(offered.map((button) => button.querySelector(".face-panel-suggestion-name").textContent), ["Sam · Pets", "Wren"]);
    assert.match(offered[0].title, /Looks like Sam · Pets\. Click to name this face/);
    assert.match(offered[0].title, /\(Pets\/Sam\)/);
  });

  test("clicking Sam · Pets writes the exact keyword first, then names the face by id -- though the photo has the other Sam", async (t) => {
    const ctx = await openPhoto(t);
    await ctx.openFace(1);
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    await ctx.wait(80);
    const saved = ctx.posts("/api/photo/save-metadata");
    assert.equal(saved.length, 1, "the keyword is written first");
    assert.deepEqual(saved[0].body.tags.slice().sort(), ["Friends/Sam", "Pets/Sam", "Trips"]);
    const named = ctx.posts("/api/face/match");
    assert.equal(named.length, 1);
    assert.deepEqual(named[0].body, { face_id: 2, person_id: 41, page_writes_tags: true });
    assert.ok(ctx.server.calls.indexOf(saved[0]) < ctx.server.calls.indexOf(named[0]), "the tag before the face");
  });

  test("a hover shows the faces of THAT Sam, by id, under the label", async (t) => {
    const ctx = await openPhoto(t);
    await ctx.openFace(1);
    ctx.panel().querySelector(".face-panel-suggestion").dispatchEvent(new ctx.window.MouseEvent("mouseenter"));
    await ctx.wait(190);
    const popup = ctx.$("person-faces-popup");
    assert.equal(popup.querySelector(".person-faces-name").textContent, "Sam · Pets");
    assert.deepEqual([...popup.querySelectorAll("img")].map((img) => img.getAttribute("src")), ["/photo_index/api/face-crop?id=21"]);
  });

  test("typing Sam asks which, and the one chosen is named by id", async (t) => {
    const ctx = await openPhoto(t);
    await ctx.openFace(1);
    const input = ctx.panel().querySelector(".face-panel-input");
    input.value = "Sam";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    click(ctx.window, ctx.panel().querySelector(".face-panel-name"));
    await ctx.wait();
    const question = ctx.document.querySelector(".person-choice");
    assert.ok(question, "the page asks");
    assert.deepEqual([...question.querySelectorAll(".person-choice-label")].map((each) => each.textContent), ["Sam · Friends", "Sam · Pets"]);
    assert.equal(ctx.posts("/api/face/match").length, 0, "nothing is written until the owner says which");
    question.querySelector('input[value="1"]').click();
    question.querySelector('input[value="1"]').dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    click(ctx.window, question.querySelector(".btn-confirm"));
    await ctx.wait(80);
    assert.equal(ctx.posts("/api/face/match")[0].body.person_id, 41);
    assert.ok(ctx.posts("/api/photo/save-metadata")[0].body.tags.includes("Pets/Sam"));
  });

  test("typing a tag path from the list names exactly that person", async (t) => {
    const ctx = await openPhoto(t);
    await ctx.openFace(1);
    const input = ctx.panel().querySelector(".face-panel-input");
    input.value = "Pets/Sam";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    click(ctx.window, ctx.panel().querySelector(".face-panel-name"));
    await ctx.wait(80);
    assert.equal(ctx.document.querySelector(".person-choice"), null, "a path is no question");
    assert.equal(ctx.posts("/api/face/match")[0].body.person_id, 41);
  });

  test("declining the question writes nothing", async (t) => {
    const ctx = await openPhoto(t);
    await ctx.openFace(1);
    const input = ctx.panel().querySelector(".face-panel-input");
    input.value = "sam";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    click(ctx.window, ctx.panel().querySelector(".face-panel-name"));
    await ctx.wait();
    click(ctx.window, ctx.document.querySelector(".person-choice .btn-cancel"));
    await ctx.wait();
    assert.equal(ctx.posts("/api/face/match").length, 0);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 0);
  });
});

describe("the strip's suggested face", () => {
  test("is the person by id: a click on it names that Sam and writes that keyword", async (t) => {
    const faces = { ...FACES, faces: [face(1, [400, 300, 800, 700], { name: "Sam", person: SAM_FRIEND }),
      face(2, [1000, 500, 1400, 900], { suggestion: "Sam", suggestion_person: SAM_PET, similarity: 0.9 })] };
    const ctx = await openPhoto(t, { faces });
    const card = [...ctx.document.querySelectorAll(".face-card")].find((each) => each.classList.contains("face-card-actionable") && /\?/.test(each.textContent));
    assert.ok(card, "the suggested face is actionable: the photo has the other Sam, not this one");
    assert.equal(card.querySelector(".face-card-label").textContent, "Sam · Pets?");
    assert.match(card.title, /Closest match: Sam · Pets/);
    click(ctx.window, card);
    await ctx.wait(80);
    assert.equal(ctx.posts("/api/face/match")[0].body.person_id, 41);
  });

  test("a suggested person the photo already has is settled, not offered again", async (t) => {
    const faces = { ...FACES, faces: [FACES.faces[0], face(2, [1000, 500, 1400, 900], { suggestion: "Sam", suggestion_person: SAM_FRIEND, similarity: 0.9 })] };
    const ctx = await openPhoto(t, { faces });
    const settled = ctx.document.querySelector(".face-card-settled");
    assert.ok(settled);
    assert.match(settled.title, /Sam · Friends is already tagged on this photo/);
  });
});

describe("the add-person list", () => {
  test("offers each Sam as a person of their own, by tag, with the label beside it", async (t) => {
    const ctx = await openPhoto(t);
    const options = [...ctx.document.querySelectorAll("#people-datalist option")].map((option) => [option.value, option.label]);
    assert.deepEqual(options.filter(([value]) => value.endsWith("/Sam")),
      [["Friends/Sam", "Sam · Friends"], ["Pets/Sam", "Sam · Pets"]]);
    assert.equal(options.filter(([value]) => value === "Sam").length, 0, "a bare name two people have is no one of them");
    assert.ok(options.some(([value]) => value === "Family/Ingersoll/Wren"));
  });
});
