/**
 * TagTuner with two people called alike (identity by id, stage 2, part C, phase 6): the owner's real case is one shared leaf, a pet
 * and a friend both called Sam, so the library here has a Sam under Friends and a Sam under Pets beside a Wren nobody shares a
 * name with. The answers are the server's shapes (/api/people?records=1, /api/people-with-counts with `person`, the Identify
 * queue's `person_id`, a face's `suggested_person_id`); names are made up.
 *
 * What the pages must do: show `Sam · Friends` and `Sam · Pets` wherever the name is shared and the plain name where it is not;
 * choose a person by the id of their node, so the second Sam is never the first; and, asked for a bare name two people have, ask
 * WHICH, with both offered by their labels, and send the person_id of the one chosen.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadApp, FakeServer, flush, click, closeAllApps, REPO_ROOT } from "./harness.mjs";

afterEach(() => closeAllApps());

const SAM_FRIEND = { id: 40, name: "Sam", tag: "Friends/Sam", group: "Friends", shared: true };
const SAM_PET = { id: 41, name: "Sam", tag: "Pets/Sam", group: "Pets", shared: true };
const WREN = { id: 52, name: "Wren", tag: "Family/Ingersoll/Wren", group: "", shared: false };
const RECORDS = [SAM_FRIEND, SAM_PET, WREN];

const COUNTS = [
  { name: "Sam", count: 4, person_id: 40, person: SAM_FRIEND },
  { name: "Sam", count: 2, person_id: 41, person: SAM_PET },
  { name: "Wren", count: 3, person_id: 52, person: WREN },
];

function face(id, extra = {}) {
  return {
    id, photo_path: `D:\\xc\\photo${id}.jpg`, filename: `photo${id}.jpg`, box: [10, 10, 40, 40], prob: 0.99, mtime: 0, year: 2026,
    similarity: 0.0, cluster_id: -1, cluster_name: "Unclustered", other_names: [], suggested_name: null, suggested_similarity: 0,
    suggestion_strength: null, ...extra,
  };
}

function reviewPeople(faces = [face(1), face(2)]) {
  return new FakeServer()
    .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/photos", [])
    .on("/api/people-with-counts", COUNTS)
    .first("/api/people?records=1", RECORDS)
    .on("/api/people?include_hidden=1", ["Sam", "Wren"])
    .on("/api/people", ["Sam", "Wren"])
    .on("/api/person-faces", { faces, total: faces.length })
    .on("/api/faces/match-bulk", { success: true, matched_ids: faces.map((each) => each.id) })
    .on("/api/person/rename", { success: true, photos_affected: 1, photos_rewritten: 1 })
    .on("/api/tags/list", { tags: [], buckets: {} });
}

async function openReviewPeople(t, server = reviewPeople()) {
  const ctx = await loadApp("tagtuner", { t, url: "http://localhost:8080/kr-track/", server,
    before: (window) => window.localStorage.setItem("tagtuner.peopleSort", "name") });
  const select = ctx.document.getElementById("tuner-mode");
  select.value = "face-matching";
  select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
  await flush(ctx.window, 8);
  ctx.server = server;
  ctx.rows = () => [...ctx.document.querySelectorAll("#photo-list .photo-item")];
  return ctx;
}

const wait = (window, ms = 40) => new Promise((resolve) => window.setTimeout(resolve, ms));

describe("Review People lists the two Sams as two people", () => {
  test("a shared name shows its group; a name nobody shares is shown as it is; the full tag is the hover", async (t) => {
    const ctx = await openReviewPeople(t);
    assert.deepEqual(ctx.rows().map((row) => row.querySelector(".photo-title").textContent),
      ["Sam · Friends", "Sam · Pets", "Wren"]);
    assert.deepEqual(ctx.rows().map((row) => row.title), ["Friends/Sam", "Pets/Sam", "Family/Ingersoll/Wren"]);
  });

  test("a narrow window cuts the group at its END and never the name: the group's start is what tells people apart", async (t) => {
    const ctx = await openReviewPeople(t);
    const label = ctx.rows()[0].querySelector(".photo-title .person-label");
    assert.equal(label.querySelector(".person-label-name").textContent, "Sam · ");
    assert.equal(label.querySelector(".person-label-group").textContent, "Friends");
    assert.equal(label.querySelector(".person-label-group bdi").textContent, "Friends");
    assert.equal(ctx.rows()[2].querySelector(".person-label-group"), null, "a name nobody shares has no group");
    const css = fs.readFileSync(path.join(REPO_ROOT, "web", "common", "person-choice.css"), "utf8");
    const rule = (name) => css.match(new RegExp(`\\.${name}\\s*\\{([^}]*)\\}`))[1];
    assert.doesNotMatch(rule("person-label-group"), /direction:\s*rtl/, "cutting the left would make two cousins read alike");
    assert.match(rule("person-label-group"), /text-overflow:\s*ellipsis/);
    assert.match(rule("person-label-group"), /overflow:\s*hidden/);
    assert.match(rule("person-label-name"), /flex:\s*none/, "the name keeps its room");
    assert.equal(label.title, "Friends/Sam", "the full tag is the title");
    assert.doesNotMatch(rule("person-label-name"), /overflow|ellipsis/);
  });

  test("the list is searched by label as well as by name", async (t) => {
    const ctx = await openReviewPeople(t);
    const search = ctx.document.getElementById("photo-search");
    search.value = "pets";
    search.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.deepEqual(ctx.rows().filter((row) => row.style.display !== "none").map((row) => row.personId), [41]);
  });

  test("choosing the second Sam opens THEIR faces, by id; only their row is active; the address keeps the id", async (t) => {
    const ctx = await openReviewPeople(t);
    click(ctx.window, ctx.rows()[1]);
    await flush(ctx.window, 8);
    const asked = ctx.server.urls().filter((url) => url.includes("/api/person-faces"));
    assert.equal(asked.length, 1);
    assert.ok(asked[0].includes("person_id=41") && !asked[0].includes("name="), asked[0]);
    assert.deepEqual(ctx.rows().map((row) => row.classList.contains("active")), [false, true, false]);
    assert.equal(ctx.document.getElementById("matching-person-name").textContent, "Sam · Pets");
    assert.ok(ctx.window.location.search.includes("person_id=41"), ctx.window.location.search);
    // The list drawn again keeps the same person active, not the first called Sam.
    ctx.document.getElementById("people-sort").value = "count";
    ctx.document.getElementById("people-sort").dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.deepEqual(ctx.rows().filter((row) => row.classList.contains("active")).map((row) => row.personId), [41]);
  });

  test("a person with no id (a name no tag has) is still asked for by name", async (t) => {
    const server = reviewPeople().first("/api/people-with-counts", [{ name: "Fenn", count: 1, person_id: null, person: null }]);
    const ctx = await openReviewPeople(t, server);
    click(ctx.window, ctx.rows()[0]);
    await flush(ctx.window, 8);
    assert.ok(server.urls().some((url) => url.includes("/api/person-faces?name=Fenn")), server.urls().join("\n"));
  });

  test("an address that names the person and the id opens exactly that person", async (t) => {
    const server = reviewPeople();
    const ctx = await loadApp("tagtuner", { t, server,
      url: "http://localhost:8080/kr-track/?mode=face-matching&person=Sam&person_id=41" });
    await flush(ctx.window, 10);
    const asked = server.urls().filter((url) => url.includes("/api/person-faces"));
    assert.ok(asked.length >= 1 && asked.every((url) => url.includes("person_id=41")), asked.join("\n"));
  });
});

describe("naming faces as one of two people called alike", () => {
  async function withGrid(t) {
    const ctx = await openReviewPeople(t);
    click(ctx.window, ctx.rows()[2]);          // Wren's faces: two to be named as a Sam
    await flush(ctx.window, 8);
    const cards = [...ctx.document.querySelectorAll("#matching-faces-grid .face-match-item")];
    cards.forEach((card) => click(ctx.window, card, { ctrlKey: true }));
    await wait(ctx.window);
    return ctx;
  }

  function type(ctx, text) {
    const input = ctx.document.getElementById("input-reassign-name");
    input.value = text;
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
  }

  test("the picker offers each Sam by label, and a Wren by name", async (t) => {
    const ctx = await withGrid(t);
    const offered = [...ctx.document.querySelectorAll("#people-datalist option")].map((option) => option.value);
    assert.deepEqual(offered, ["Sam · Friends", "Sam · Pets", "Wren"]);
  });

  test("a label typed (picked from the list) names that person by id, with no question", async (t) => {
    const ctx = await withGrid(t);
    ctx.window.confirm = () => true;
    type(ctx, "Sam · Pets");
    click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    await wait(ctx.window);
    const body = ctx.server.lastBody("/api/faces/match-bulk");
    assert.deepEqual(body, { face_ids: body.face_ids, person_id: 41 });
    assert.equal(ctx.document.querySelector(".person-choice"), null, "nothing to ask");
  });

  test("the bare name Sam asks which, offers both by label, and sends the id of the one chosen", async (t) => {
    const ctx = await withGrid(t);
    ctx.window.confirm = () => true;
    type(ctx, "Sam");
    click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    await wait(ctx.window);
    const question = ctx.document.querySelector(".person-choice");
    assert.ok(question, "the page asks");
    assert.deepEqual([...question.querySelectorAll(".person-choice-label")].map((each) => each.textContent),
      ["Sam · Friends", "Sam · Pets"]);
    assert.deepEqual([...question.querySelectorAll(".person-choice-option")].map((each) => each.title), ["Friends/Sam", "Pets/Sam"]);
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk"), undefined, "nothing is sent until the owner says which");
    question.querySelector('input[value="1"]').click();
    question.querySelector("input[value='1']").dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    click(ctx.window, question.querySelector(".btn-confirm"));
    await wait(ctx.window);
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk").person_id, 41);
    assert.equal(ctx.document.querySelector(".person-choice"), null);
  });

  test("declining the question names nobody", async (t) => {
    const ctx = await withGrid(t);
    ctx.window.confirm = () => true;
    type(ctx, "sam");
    click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    await wait(ctx.window);
    click(ctx.window, ctx.document.querySelector(".person-choice .btn-cancel"));
    await wait(ctx.window);
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk"), undefined);
    assert.equal(ctx.document.querySelector(".person-choice"), null);
  });

  test("a person with one name is named by id without being asked", async (t) => {
    const ctx = await withGrid(t);
    ctx.window.confirm = () => true;
    type(ctx, "Wren");
    click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    await wait(ctx.window);
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk").person_id, 52);
  });

  test("a name nobody has makes a new person, by name, after asking", async (t) => {
    const ctx = await withGrid(t);
    const asked = [];
    ctx.window.confirm = (text) => { asked.push(text); return true; };
    type(ctx, "Fenn Ashdown");
    click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    await wait(ctx.window);
    assert.match(asked[0], /not currently in the database/);
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk").person_name, "Fenn Ashdown");
    assert.equal(ctx.server.lastBody("/api/faces/match-bulk").person_id, undefined);
  });

  test("a label before the people are read is never made a person's name", async (t) => {
    const server = reviewPeople().first("/api/people?records=1", []);     // the records did not arrive
    const ctx = await openReviewPeople(t, server);
    click(ctx.window, ctx.rows()[2]);
    await flush(ctx.window, 8);
    [...ctx.document.querySelectorAll("#matching-faces-grid .face-match-item")]
      .forEach((card) => click(ctx.window, card, { ctrlKey: true }));
    const said = [];
    ctx.window.alert = (text) => said.push(String(text));
    ctx.window.confirm = () => true;
    type(ctx, "Sam · Pets");
    click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    await wait(ctx.window);
    assert.equal(server.lastBody("/api/faces/match-bulk"), undefined);
    assert.ok(said.some((text) => text.includes("has not read the people")), said.join("|"));
  });

  test("a person merged or deleted in another window (404) is told in the server's words and the people are read again", async (t) => {
    const ctx = await withGrid(t);
    ctx.window.confirm = () => true;
    const said = [];
    ctx.window.alert = (text) => said.push(String(text));
    ctx.server.first("/api/faces/match-bulk", { success: false, error: "That person is no longer in the tag tree (they may have been merged or removed in another window): reload the page." }, { status: 404 });
    const asked = (what) => ctx.server.urls().filter((url) => url.includes(what)).length;
    const before = [asked("/api/people?records=1"), asked("/api/people-with-counts")];
    type(ctx, "Sam · Pets");
    click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    await wait(ctx.window, 120);
    assert.match(said[0], /no longer in the tag tree/);
    assert.equal(asked("/api/people?records=1"), before[0] + 1, "the people are read again");
    assert.equal(asked("/api/people-with-counts"), before[1] + 1, "so is the list");
  });

  test("a refused rename says why, in the server's words", async (t) => {
    const ctx = await openReviewPeople(t);
    click(ctx.window, ctx.rows()[1]);
    await flush(ctx.window, 8);
    ctx.server.first("/api/person/rename", { success: false, error: "Sammy is already filed there: merge them instead." }, { status: 400 });
    const said = [];
    ctx.window.alert = (text) => said.push(String(text));
    ctx.window.prompt = () => "Sammy";
    ctx.window.confirm = () => true;
    click(ctx.window, ctx.document.getElementById("btn-rename-person"));
    await wait(ctx.window, 100);
    assert.match(said[0], /merge them instead/);
  });

  test("renaming the second Sam renames that person: the id travels with the old name", async (t) => {
    const ctx = await openReviewPeople(t);
    click(ctx.window, ctx.rows()[1]);
    await flush(ctx.window, 8);
    const prompts = [];
    ctx.window.prompt = (text) => { prompts.push(text); return "Sammy"; };
    ctx.window.confirm = () => true;
    click(ctx.window, ctx.document.getElementById("btn-rename-person"));
    await wait(ctx.window);
    assert.match(prompts[0], /Rename person "Sam · Pets" to:/);
    assert.deepEqual(ctx.server.lastBody("/api/person/rename"), { person_id: 41, old_name: "Sam", new_name: "Sammy" });
  });
});

describe("one question at a time, about the people it was asked about", () => {
  const ALEX_T = { id: 60, name: "Alex", tag: "Family/Thackeray/Cousins/Alex", group: "Thackeray/Cousins", shared: true };
  const ALEX_I = { id: 61, name: "Alex", tag: "Family/Ingersoll/Cousins/Alex", group: "Ingersoll/Cousins", shared: true };

  test("a second question about OTHER people replaces the first, which is answered none -- never answered by its choice", async (t) => {
    const server = reviewPeople().first("/api/people?records=1", [...RECORDS, ALEX_T, ALEX_I]);
    const ctx = await openReviewPeople(t, server);
    click(ctx.window, ctx.rows()[2]);
    await flush(ctx.window, 8);
    [...ctx.document.querySelectorAll("#matching-faces-grid .face-match-item")].forEach((card) => click(ctx.window, card, { ctrlKey: true }));
    ctx.window.confirm = () => true;
    const assign = (text) => {
      const input = ctx.document.getElementById("input-reassign-name");
      input.value = text;
      input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
      click(ctx.window, ctx.document.getElementById("btn-reassign-selected"));
    };
    assign("Sam");
    await wait(ctx.window);
    assign("Alex");
    await wait(ctx.window);
    const questions = ctx.document.querySelectorAll(".person-choice");
    assert.equal(questions.length, 1, "one at a time");
    assert.deepEqual([...questions[0].querySelectorAll(".person-choice-label")].map((each) => each.textContent),
      ["Alex · Thackeray/Cousins", "Alex · Ingersoll/Cousins"]);
    click(ctx.window, questions[0].querySelector(".btn-confirm"));
    await wait(ctx.window, 80);
    const bulk = ctx.server.calls.filter((call) => call.url.includes("/api/faces/match-bulk"));
    assert.equal(bulk.length, 1, "the Sam question made no write");
    assert.equal(bulk[0].body.person_id, 60);
  });
});

describe("Identify Faces with the two Sams", () => {
  const QUEUE = [
    { name: "Sam", count: 2, person_id: 40, person: SAM_FRIEND },
    { name: "Sam", count: 1, person_id: 41, person: SAM_PET },
    { name: "Unknown Faces", count: 1, unit: "face", person_id: null, person: null },
  ];

  function identify(faces) {
    return new FakeServer()
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/unmatched-faces/people", QUEUE)
      .on("/api/unmatched-faces/person-matches", {
        faces, total_count: faces.length, unclustered_total: 0, unclustered_shown: 0, has_more: false, page: 1, limit: -1 })
      .on("/api/unmatched-faces/build-status", { active: false })
      .on("/api/faces/match-bulk", { success: true, matched_ids: faces.map((each) => each.id) })
      .on("/api/people-with-counts", [])
      .first("/api/people?records=1", RECORDS)
      .on("/api/people", ["Sam", "Wren"]);
  }

  test("the queue's rows are labelled and each opens its own candidates by id", async (t) => {
    const server = identify([face(1, { cluster_id: 0, cluster_name: "Cluster 1", band: "likely" })]);
    const ctx = await loadApp("tagtuner", { t, server, url: "http://localhost:8080/kr-track/?mode=unmatched-faces" });
    await flush(ctx.window, 8);
    const rows = [...ctx.document.querySelectorAll("#photo-list .photo-item")];
    assert.deepEqual(rows.map((row) => row.querySelector(".photo-title").textContent), ["Unknown Faces", "Sam · Friends", "Sam · Pets"]
      .sort((a, b) => (a === "Unknown Faces" ? -1 : b === "Unknown Faces" ? 1 : 0)));
    click(ctx.window, rows.find((row) => row.personId === 41));
    await flush(ctx.window, 8);
    assert.ok(server.urls().some((url) => url.includes("/api/unmatched-faces/person-matches?person_id=41")), server.urls().join("\n"));
  });

  test("a cluster's guess is the person by id, shown by label, and one click names them by id", async (t) => {
    const guessed = face(1, { cluster_id: 0, cluster_name: "Cluster 1", band: "likely", suggested_name: "Sam", suggested_person_id: 41,
      suggested_similarity: 0.93, suggestion_strength: "likely" });
    const server = identify([guessed, face(2, { cluster_id: 0, cluster_name: "Cluster 1", band: "likely" })]);
    const ctx = await loadApp("tagtuner", { t, server,
      url: "http://localhost:8080/kr-track/?mode=unmatched-faces&person=Unknown%20Faces" });
    await flush(ctx.window, 10);
    // Unknown Faces holds the guess; a cluster offers it.
    const label = ctx.document.querySelector(".cluster-suggestion-label");
    assert.ok(label, "the cluster offers its guess");
    assert.match(label.textContent, /^Looks like Sam · Pets \(93%\)$/);
    click(ctx.window, ctx.document.querySelector(".cluster-suggestion-assign"));
    await wait(ctx.window, 60);
    assert.equal(server.lastBody("/api/faces/match-bulk").person_id, 41);
  });
});
