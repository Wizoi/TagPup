/**
 * TagPup's lists with two people called alike (identity by id, stage 2, part C, phase 6): Suggest's chips, the selection panel's
 * people, the library view's tally and the pill edits it starts, the navigator's people, the search's chips and the words of a view.
 * The owner's real case is one shared leaf, a pet and a friend both called Sam. Answers are the server's shapes; names are made up.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, loadApp, FakeServer, photoRecord, flush, click, openFolder } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const SAM_FRIEND = { id: 40, name: "Sam", tag: "Friends/Sam", group: "Friends", shared: true };
const SAM_PET = { id: 41, name: "Sam", tag: "Pets/Sam", group: "Pets", shared: true };
const WREN = { id: 52, name: "Wren", tag: "Family/Ingersoll/Wren", group: "", shared: false };
const RECORDS = [SAM_FRIEND, SAM_PET, WREN];
const NO_ID = { id: null, name: "Sam", tag: null, group: "", shared: true };   // a name two people have: the server cannot say which

const TAXONOMY = [
  { id: 1, tag: "Friends", name: "Friends", parent_id: null, has_face: 1 },
  { id: 2, tag: "Friends/Sam", name: "Sam", parent_id: 1, has_face: 1 },
  { id: 3, tag: "Pets", name: "Pets", parent_id: null, has_face: 1 },
  { id: 4, tag: "Pets/Sam", name: "Sam", parent_id: 3, has_face: 1 },
  { id: 5, tag: "Family", name: "Family", parent_id: null, has_face: 1 },
  { id: 6, tag: "Family/Ingersoll", name: "Ingersoll", parent_id: 5, has_face: 1 },
  { id: 7, tag: "Family/Ingersoll/Wren", name: "Wren", parent_id: 6, has_face: 1 },
];

const wait = (window, ms = 40) => new Promise((resolve) => window.setTimeout(resolve, ms));
const chipsOf = (ctx, id) => [...ctx.document.querySelectorAll(`#${id} .suggestion-chip`)].map((chip) => chip.textContent);

async function openPhoto(t, { tags = [], people = [], suggestions = {} } = {}) {
  const photo = photoRecord({ filename: "a.jpg", tags, people });
  const server = new FakeServer()
    .on("/api/tags", ["Friends/Sam", "Pets/Sam", "Family/Ingersoll/Wren"])
    .first("/api/people?records=1&include_hidden=1", RECORDS)
    .first("/api/people?records=1", RECORDS)
    .on("/api/people", ["Sam", "Wren"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100 })
    .on("/api/folder/scan", [photo])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/folder/suggest-status", { status: "completed", suggestions: { [photo.path]: suggestions } });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, "D:/Library/2020");
  const row = ctx.document.querySelector("li[data-path]");
  if (row) row.click();
  await flush(ctx.window, 8);
  ctx.server = server;
  return ctx;
}

describe("Suggest's chips", () => {
  test("a name two people have is offered as two chips, one each, by label -- in place of asking which folder", async (t) => {
    const ctx = await openPhoto(t, { suggestions: { people: [{ name: "Sam", score: 0.8, person: NO_ID }, { name: "Wren", score: 0.7, person: WREN }], tags: [] } });
    assert.deepEqual(chipsOf(ctx, "suggested-people-container"), ["Sam · Friends · 80%", "Sam · Pets · 80%", "Wren · 70%"]);
  });

  test("a click on one writes that person's exact keyword, with no question", async (t) => {
    const ctx = await openPhoto(t, { suggestions: { people: [{ name: "Sam", score: 0.8, person: NO_ID }], tags: [] } });
    const chip = [...ctx.document.querySelectorAll("#suggested-people-container .suggestion-chip")].find((each) => /Pets/.test(each.textContent));
    assert.match(chip.title, /Click to add Sam · Pets to this photo\. \(Pets\/Sam\)/);
    click(ctx.window, chip);
    await wait(ctx.window, 80);
    assert.equal(ctx.document.querySelector(".modal-overlay.active"), null, "no placement question");
    const saved = ctx.server.calls.filter((call) => call.url.includes("/api/photo/save-metadata"));
    assert.deepEqual(saved[0].body.tags, ["Pets/Sam"]);
  });

  test("the Sam the photo already has is not offered; the other Sam is", async (t) => {
    const ctx = await openPhoto(t, { tags: ["Friends/Sam"], people: ["Sam"],
      suggestions: { people: [{ name: "Sam", score: 0.8, person: NO_ID }], tags: [] } });
    assert.deepEqual(chipsOf(ctx, "suggested-people-container"), ["Sam · Pets · 80%"]);
  });

  test("a suggestion that names one person by their path is that person: a name nobody shares stays as it is", async (t) => {
    const ctx = await openPhoto(t, { suggestions: { people: [{ name: "Family/Ingersoll/Wren", score: 0.9, person: WREN }], tags: [] } });
    assert.deepEqual(chipsOf(ctx, "suggested-people-container"), ["Wren · 90%"]);
  });

  test("the people not read yet leave a shared name as the one chip it was", async (t) => {
    const photo = photoRecord({ filename: "a.jpg" });
    const server = new FakeServer()
      .on("/api/tags", []).on("/api/people", ["Sam"]).on("/api/taxonomy/tree", TAXONOMY)
      .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
      .on("/api/folder/index-status", { status: "completed", percent: 100 })
      .on("/api/folder/scan", [photo]).on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/folder/suggest-status", { status: "completed", suggestions: { [photo.path]: { people: [{ name: "Sam", score: 0.8, person: NO_ID }], tags: [] } } });
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
    await openFolder(ctx, "D:/Library/2020");
    ctx.document.querySelector("li[data-path]")?.click();
    await flush(ctx.window, 8);
    assert.deepEqual(chipsOf(ctx, "suggested-people-container"), ["Sam · 80%"]);
  });
});

describe("the selection panel of a folder", () => {
  async function select(t, photos) {
    const server = new FakeServer()
      .on("/api/tags", []).first("/api/people?records=1", RECORDS).on("/api/people", ["Sam", "Wren"]).on("/api/taxonomy/tree", TAXONOMY)
      .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on("/api/folder/index-status", { status: "completed", percent: 100 })
      .on("/api/folder/scan", photos);
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
    await openFolder(ctx, "D:/Library/2020", { settle: 6 });
    [...ctx.document.querySelectorAll(".thumbnail-card .thumbnail-checkbox")].forEach((box) => click(ctx.window, box));
    await flush(ctx.window, 4);
    return ctx;
  }

  test("two people called Sam are two chips, each by label, each counted apart", async (t) => {
    const ctx = await select(t, [
      photoRecord({ filename: "a.jpg", tags: ["Friends/Sam", "Family/Ingersoll/Wren"], people: ["Sam", "Wren"] }),
      photoRecord({ filename: "b.jpg", tags: ["Pets/Sam"], people: ["Sam"] }),
      photoRecord({ filename: "c.jpg", tags: ["Friends/Sam"], people: ["Sam"] }),
    ]);
    const chips = [...ctx.document.querySelectorAll("#selection-people-list .selection-summary-chip")];
    assert.deepEqual(chips.map((chip) => chip.firstChild.textContent), ["Sam · Friends (2)", "Sam · Pets (1)", "Wren (1)"]);
    assert.deepEqual(chips.map((chip) => chip.title), ["Friends/Sam", "Pets/Sam", "Family/Ingersoll/Wren"]);
  });
});

describe("a library view's tally", () => {
  const TALLY = {
    total: 4, tags: [], more_tags: 0, more_people: 0,
    people: [{ name: "Sam", count: 3, has_node: true, person: SAM_FRIEND }, { name: "Sam", count: 1, has_node: true, person: SAM_PET },
      { name: "Wren", count: 2, has_node: true, person: WREN }],
  };

  async function tallied(t) {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: [11, 12, 13, 14] });
    ctx.bulk.tally = TALLY;
    click(ctx.window, ctx.cardById(11).querySelector(".thumbnail-checkbox"));
    click(ctx.window, ctx.cardById(12).querySelector(".thumbnail-checkbox"));
    await ctx.settle(700);
    return ctx;
  }

  test("each Sam is a chip under their label; the jump list opens each by tag", async (t) => {
    const ctx = await tallied(t);
    const chips = [...ctx.document.querySelectorAll("#selection-people-list .selection-summary-chip")];
    assert.deepEqual(chips.map((chip) => chip.firstChild.textContent), ["Sam · Friends (3)", "Sam · Pets (1)", "Wren (2)"]);
    assert.deepEqual(chips.map((chip) => chip.title), ["Friends/Sam", "Pets/Sam", "Family/Ingersoll/Wren"]);
    const jumps = [...ctx.document.querySelectorAll("#selection-people-jump a, #selection-people-jump button")].map((each) => each.textContent);
    assert.ok(jumps.some((text) => text.startsWith("Sam · Friends")), jumps.join("|"));
    assert.ok(jumps.some((text) => text.startsWith("Sam · Pets")), jumps.join("|"));
  });

  test("taking the pet Sam off the selection names that person by id, not the name the friend has too", async (t) => {
    const ctx = await tallied(t);
    ctx.window.confirm = () => true;
    const pet = [...ctx.document.querySelectorAll("#selection-people-list .selection-summary-chip")].find((chip) => /Pets/.test(chip.firstChild.textContent));
    click(ctx.window, pet.querySelector(".selection-summary-chip-remove"));
    await ctx.settle(60);
    const started = ctx.server.calls.filter((call) => call.url.includes("/api/library/bulk/start"));
    assert.equal(started.length, 1);
    assert.equal(started[0].body.op, "people");
    assert.deepEqual(started[0].body.params, { add: [], remove: [], remove_ids: [41] });
  });

  test("putting the friend Sam on every selected photo names that person by id", async (t) => {
    const ctx = await tallied(t);
    ctx.window.confirm = () => true;
    const friend = [...ctx.document.querySelectorAll("#selection-people-list .selection-summary-chip")].find((chip) => /Friends/.test(chip.firstChild.textContent));
    click(ctx.window, friend.querySelector(".selection-summary-chip-apply"));
    await ctx.settle(60);
    const started = ctx.server.calls.filter((call) => call.url.includes("/api/library/bulk/start"));
    assert.deepEqual(started[0].body.params, { add: [], remove: [], add_ids: [40] });
  });

  test("a person with no id (a name no tag has) is edited by name, as before", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", ids: [11, 12] });
    ctx.bulk.tally = { total: 2, tags: [], more_tags: 0, more_people: 0,
      people: [{ name: "Fenn Ashdown", count: 1, has_node: false, person: null }] };
    click(ctx.window, ctx.cardById(11).querySelector(".thumbnail-checkbox"));
    click(ctx.window, ctx.cardById(12).querySelector(".thumbnail-checkbox"));
    await ctx.settle(700);
    ctx.window.confirm = () => true;
    click(ctx.window, ctx.document.querySelector("#selection-people-list .selection-summary-chip-remove"));
    await ctx.settle(60);
    const started = ctx.server.calls.filter((call) => call.url.includes("/api/library/bulk/start"));
    assert.deepEqual(started[0].body.params, { add: [], remove: ["Fenn Ashdown"] });
  });
});

describe("the navigator's people", () => {
  const LIST = [
    { name: "Sam", count: 5, group: null, person_id: 40, person: SAM_FRIEND },
    { name: "Sam", count: 2, group: null, person_id: 41, person: SAM_PET },
    { name: "Wren", count: 3, group: null, person_id: 52, person: WREN },
  ];

  async function people(t, answer) {
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: { people: answer } });
    await ctx.openTab("people");
    return ctx;
  }
  const labels = (ctx) => ctx.rows("people").map((row) => row.querySelector(".nav-label").textContent);

  test("in a flat list each Sam is a row under their label; a name nobody shares is its name", async (t) => {
    const ctx = await people(t, { people: LIST, groups: [], unfiled: 0 });
    assert.deepEqual(labels(ctx), ["Sam · Friends", "Sam · Pets", "Wren"]);
    assert.deepEqual(ctx.rows("people").map((row) => row.title.split("\n")[0]), ["Friends/Sam", "Pets/Sam", "Wren"]);
  });

  test("in a tree the branch above says which, so the row is the name", async (t) => {
    const grouped = LIST.map((each) => ({ ...each, group: each.person.tag.replace(/\/[^/]*$/, "") }));
    const ctx = await people(t, {
      people: grouped,
      groups: [{ tag: "Friends", name: "Friends", parent: null, count: 5 }, { tag: "Pets", name: "Pets", parent: null, count: 2 },
        { tag: "Family/Ingersoll", name: "Ingersoll", parent: null, count: 3 }],
      unfiled: 0,
    });
    assert.deepEqual(labels(ctx).filter((label) => /^Sam/.test(label)), ["Sam", "Sam"]);
  });

  test("a filter lists each match with its label, so two Sams are told apart out of their branches", async (t) => {
    const grouped = LIST.map((each) => ({ ...each, group: each.person.tag.replace(/\/[^/]*$/, "") }));
    const ctx = await people(t, {
      people: grouped,
      groups: [{ tag: "Friends", name: "Friends", parent: null, count: 5 }, { tag: "Pets", name: "Pets", parent: null, count: 2 }],
      unfiled: 0,
    });
    const input = ctx.document.querySelector("#nav-panel-people .nav-filter");
    input.value = "sam";
    input.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    assert.deepEqual(labels(ctx), ["Sam · Friends", "Sam · Pets"]);
    input.value = "pets";
    input.dispatchEvent(new ctx.window.Event("input"));
    await ctx.settle(200);
    assert.ok(labels(ctx).includes("Sam · Pets"), "found by its group");
  });

  test("a click on the pet Sam opens the view of THAT person, by tag", async (t) => {
    const ctx = await people(t, { people: LIST, groups: [], unfiled: 0 });
    click(ctx.window, ctx.rows("people")[1]);
    await ctx.settle();
    assert.match(decodeURIComponent(ctx.idsAsked.at(-1)), /kind=person&value=Pets\/Sam/);
  });
});
