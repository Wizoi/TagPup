/**
 * The New Person dialog with a Group box (identity by id, stage 2, part C): a new person whose name another person has is not
 * refused as a duplicate when the owner files them in a group of their own. The person's tag is made there first (the tag editor's
 * create) and the faces are named by the id it answers with; the dialog says another person is called the same, so a typing slip
 * is seen before the person is made. Without a group the usual rule files them, and a name another person has is still refused:
 * the server would take the name as that person's. Names are made up.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const SAM_FRIEND = { id: 40, name: "Sam", tag: "Friends/Sam", group: "Friends", shared: false };
const TREE = [
  { id: 1, tag: "Friends", parent_id: null, name: "Friends", has_face: 1 },
  { id: 2, tag: "Friends/Sam", parent_id: 1, name: "Sam", has_face: 1 },
  { id: 3, tag: "Pets", parent_id: null, name: "Pets", has_face: 1 },
  { id: 4, tag: "Places", parent_id: null, name: "Places", has_face: 0 },
  { id: 5, tag: "Family", parent_id: null, name: "Family", has_face: 1 },
  { id: 6, tag: "Family/Ingersoll", parent_id: 5, name: "Ingersoll", has_face: 1 },
  { id: 7, tag: "Family/Ingersoll/Wren", parent_id: 6, name: "Wren", has_face: 1 },
];

function face(id) {
  return { id, photo_path: `D:\\xc\\photo${id}.jpg`, filename: `photo${id}.jpg`, box: [10, 10, 40, 40], prob: 0.99, mtime: 0, year: 2026,
    similarity: 0, cluster_id: -1, cluster_name: "Unclustered", other_names: [], suggested_name: null, suggested_similarity: 0,
    suggestion_strength: null };
}

async function open(t, { create = { success: true, id: 90, tag: "Pets/Sam" } } = {}) {
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: "Unknown Faces", count: 2, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", { faces: [face(1), face(2)], total_count: 2, unclustered_total: 2,
      unclustered_shown: 2, has_more: false, page: 1, limit: -1 })
    .on("/api/people-with-counts", [])
    .on("/api/face-matches-unmatched", { matches: [] })
    .on("/api/taxonomy/tree", TREE)
    .on("/api/taxonomy/create", create)
    .on("/api/faces/match-bulk", { success: true, matched_ids: [1] })
    .first("/api/people?records=1", [SAM_FRIEND])
    .on("/api/people", ["Sam"]);
  const ctx = await loadApp("tagtuner", { t, server,
    url: "http://localhost:8080/kr-track/?mode=unmatched-faces&person=Unknown%20Faces" });
  await flush(ctx.window, 10);
  const card = ctx.document.querySelector('#matching-faces-grid [data-face-id="1"]');
  click(ctx.window, card);
  click(ctx.window, ctx.document.getElementById("btn-new-person"));
  await flush(ctx.window, 8);
  ctx.server = server;
  ctx.type = (text) => {
    const input = ctx.document.getElementById("new-person-name");
    input.value = text;
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
  };
  ctx.choose = (value) => {
    const select = ctx.document.getElementById("new-person-group");
    select.value = value;
    select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
  };
  ctx.save = () => ctx.document.getElementById("btn-modal-save");
  ctx.error = () => ctx.document.getElementById("modal-name-error");
  return ctx;
}

const wait = (window, ms = 50) => new Promise((resolve) => window.setTimeout(resolve, ms));

describe("the Group box", () => {
  test("offers the usual place, the face roots and the groups under them -- not a person, not a root that holds no faces", async (t) => {
    const ctx = await open(t);
    const options = [...ctx.document.querySelectorAll("#new-person-group option")].map((option) => [option.value, option.textContent]);
    assert.deepEqual(options, [["", "The usual place"], ["5", "Family"], ["6", "Family/Ingersoll"], ["1", "Friends"], ["3", "Pets"]]);
  });

  test("a name another person has is refused with no group, saying who, and allowed in a group, saying so", async (t) => {
    const ctx = await open(t);
    ctx.type("Sam");
    assert.ok(ctx.save().disabled);
    assert.match(ctx.error().textContent, /Another person is called Sam \(Friends\/Sam\)\. Choose a group/);
    ctx.choose("3");
    assert.ok(!ctx.save().disabled);
    assert.ok(ctx.error().classList.contains("hidden"));
    assert.match(ctx.document.getElementById("new-person-note").textContent, /Another person is called Sam \(Friends\/Sam\): this one is filed under Pets/);
  });

  test("in a group the tag is made there first and the faces are named by the id it answers with", async (t) => {
    const ctx = await open(t);
    ctx.type("Sam");
    ctx.choose("3");
    click(ctx.window, ctx.save());
    await wait(ctx.window, 120);
    assert.deepEqual(ctx.server.lastBody("/api/taxonomy/create"), { name: "Sam", parent_id: 3 });
    const body = ctx.server.lastBody("/api/faces/match-bulk");
    assert.equal(body.person_id, 90);
    assert.equal(body.person_name, undefined);
    assert.ok(ctx.document.getElementById("new-person-modal").classList.contains("hidden"));
  });

  test("with the usual place nothing is made first and the name is sent, as before", async (t) => {
    const ctx = await open(t);
    ctx.type("Fenn Ashdown");
    click(ctx.window, ctx.save());
    await wait(ctx.window, 120);
    assert.equal(ctx.server.lastBody("/api/taxonomy/create"), undefined);
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk").person_name, "Fenn Ashdown");
  });

  test("a tag that could not be made names no face and says why", async (t) => {
    const ctx = await open(t, { create: { success: false, error: "Sam is a person (3 faces); a person cannot have tags under them. Choose another group." } });
    const said = [];
    ctx.window.alert = (text) => said.push(String(text));
    ctx.type("Wren Two");
    ctx.choose("5");
    click(ctx.window, ctx.save());
    await wait(ctx.window, 120);
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk"), undefined);
    assert.match(said[0], /a person cannot have tags under them/);
  });

  test("the faces refused after the tag was made: the person is said to exist", async (t) => {
    const ctx = await open(t);
    ctx.server.first("/api/faces/match-bulk", { success: false, error: "The file could not be written" }, { status: 400 });
    const said = [];
    ctx.window.alert = (text) => said.push(String(text));
    ctx.type("Sam");
    ctx.choose("3");
    click(ctx.window, ctx.save());
    await wait(ctx.window, 120);
    assert.match(said[0], /Sam was made under Pets, but the faces were not named/);
  });
});
