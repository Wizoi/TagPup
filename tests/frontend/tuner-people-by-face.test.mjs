/**
 * TagTuner's people list: a name and its count on one line, and a By name / By face choice (remembered per
 * browser) that draws the same people as a grid of the face most like each (/api/people-faces). Records are
 * shaped as the routes answer; names are made up.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const PEOPLE = [
  { name: "Wren Halloway", count: 8, person_id: 11 },
  { name: "Ines Okafor", count: 3, person_id: 12 },
  { name: "Bao Lindqvist", count: 5, person_id: 13 },
];
const FACES = { "id:11": 101, "id:13": 303 }; // Ines has no readable face; the server keys a person by the id of their node alone

const base = (faces = FACES, people = PEOPLE) => new FakeServer()
  .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
  .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
  .on("/api/photos", [])
  .on("/api/people-with-counts", people)
  .on("/api/people-faces", faces)
  .on("/api/people", [])
  .on("/api/tags/list", { tags: [], buckets: {} });

async function open(t, { server = base(), view, mode = "face-matching" } = {}) {
  const ctx = await loadApp("tagtuner", {
    t, url: "http://localhost:8080/kr-track/", server,
    before: (window) => {
      if (view) window.localStorage.setItem("tagtuner.peopleView", view);
      // These tests read the biggest first, so they come with that remembered (the first visit is by name, #793).
      window.localStorage.setItem("tagtuner.peopleSort", "count");
    },
  });
  const select = ctx.document.getElementById("tuner-mode");
  select.value = mode;
  select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
  await flush(ctx.window, 6);
  return { ctx, server };
}

const items = (ctx) => [...ctx.document.querySelectorAll("#photo-list .photo-item")];
const names = (ctx) => items(ctx).map((li) => li.personName);
const choose = async (ctx, id) => {
  ctx.document.getElementById(id).click();
  await flush(ctx.window, 6);
};

describe("by name", () => {
  test("a row is one line: the name, then its count", async (t) => {
    const { ctx } = await open(t);
    const row = items(ctx)[0];
    assert.ok(row.classList.contains("person-row"));
    assert.deepEqual([...row.children].map((el) => el.className.split(" ")[0]), ["photo-title", "photo-badge"]);
    assert.match(row.querySelector(".photo-badge").textContent, /^8 faces$/);
    assert.equal(ctx.document.getElementById("photo-list").classList.contains("by-face"), false);
  });

  test("it does not ask for the faces", async (t) => {
    const { server } = await open(t);
    assert.equal(server.urls().filter((u) => u.includes("people-faces")).length, 0);
  });

  test("clicking a row selects the person as before", async (t) => {
    const { ctx, server } = await open(t);
    items(ctx)[1].click();
    await flush(ctx.window, 6);
    assert.ok(items(ctx)[1].classList.contains("active"));
    assert.ok(server.urls().some((u) => u.includes("/api/person-faces?person_id=13")));
  });
});

describe("by face", () => {
  test("choosing it draws a card per person with a crop, the name and the count", async (t) => {
    const { ctx, server } = await open(t);
    await choose(ctx, "people-view-face");
    const list = ctx.document.getElementById("photo-list");
    assert.ok(list.classList.contains("by-face"));
    assert.equal(server.urls().filter((u) => u.includes("people-faces")).length, 1);
    const first = items(ctx)[0];
    assert.equal(first.personName, "Wren Halloway");
    const img = first.querySelector(".person-face-crop img");
    assert.match(img.getAttribute("src"), /^\/kr-track\/api\/face-crop\?id=101$/);
    assert.equal(img.getAttribute("loading"), "lazy");
    assert.equal(first.querySelector(".person-face-crop .photo-badge").textContent, "8");
    assert.equal(first.querySelector(".photo-title").textContent, "Wren Halloway");
  });

  test("two people called alike each get their own crop, by the id the server keys them under", async (t) => {
    // /api/people-faces keys a name two people have only as id:<id>; the list's rows carry person_id.
    const server = base({ "id:7": 701, "id:9": 901 }, [
      { name: "Sam", count: 4, person_id: 7 },
      { name: "Sam", count: 2, person_id: 9 },
    ]);
    const { ctx } = await open(t, { server, view: "face" });
    assert.deepEqual(items(ctx).map((li) => li.querySelector("img").getAttribute("src")),
      ["/kr-track/api/face-crop?id=701", "/kr-track/api/face-crop?id=901"]);
  });

  test("a person with no face gets a placeholder, not an image", async (t) => {
    const { ctx } = await open(t, { view: "face" });
    const ines = items(ctx).find((li) => li.personName === "Ines Okafor");
    assert.equal(ines.querySelector("img"), null);
    assert.equal(ines.querySelector(".person-face-blank").textContent, "I");
  });

  test("a library with no faces draws every person as a placeholder", async (t) => {
    const { ctx } = await open(t, { server: base({}), view: "face" });
    assert.equal(ctx.document.querySelectorAll("#photo-list img").length, 0);
    assert.equal(items(ctx).length, 3);
  });

  test("a row with no id is nobody's face, even when the answer holds its name", async (t) => {
    const server = base({ "Loki": 777, "id:11": 101 }, [
      { name: "Loki", count: 1, person_id: null },
      { name: "Wren Halloway", count: 8, person_id: 11 },
    ]);
    const { ctx } = await open(t, { server, view: "face" });
    const loki = items(ctx).find((li) => li.personName === "Loki");
    assert.equal(loki.querySelector("img"), null);
    assert.equal(items(ctx).find((li) => li.personName === "Wren Halloway").querySelector("img").getAttribute("src"),
      "/kr-track/api/face-crop?id=101");
  });

  test("a failed ask for the faces still lists the people", async (t) => {
    const server = base().first("/api/people-faces", { error: "no" }, { status: 500 });
    const { ctx } = await open(t, { server, view: "face" });
    assert.equal(items(ctx).length, 3);
    assert.equal(ctx.document.querySelectorAll("#photo-list img").length, 0);
  });

  test("the choice is remembered, and starts the next visit by face", async (t) => {
    const { ctx } = await open(t);
    await choose(ctx, "people-view-face");
    assert.equal(ctx.window.localStorage.getItem("tagtuner.peopleView"), "face");
    const again = await open(t, { view: "face" });
    assert.ok(again.ctx.document.getElementById("photo-list").classList.contains("by-face"));
    assert.ok(again.ctx.document.getElementById("people-view-face").classList.contains("active"));
  });

  test("a browser that will not remember still switches", async (t) => {
    const { ctx } = await open(t);
    Object.defineProperty(ctx.window, "localStorage", { get() { throw new Error("blocked"); } });
    await choose(ctx, "people-view-face");
    assert.ok(ctx.document.getElementById("photo-list").classList.contains("by-face"));
  });

  test("the filter box narrows the cards, and clearing it brings them back", async (t) => {
    const { ctx } = await open(t, { view: "face" });
    const search = ctx.document.getElementById("photo-search");
    search.value = "bao";
    search.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    assert.deepEqual(items(ctx).filter((li) => li.style.display !== "none").map((li) => li.personName),
      ["Bao Lindqvist"]);
    search.value = "";
    search.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    assert.equal(items(ctx).filter((li) => li.style.display === "none").length, 0);
  });

  test("clicking a card selects the person, and going back to By name keeps them selected", async (t) => {
    const { ctx } = await open(t, { view: "face" });
    items(ctx)[2].click();
    await flush(ctx.window, 6);
    const chosen = items(ctx)[2].personName;
    assert.ok(items(ctx)[2].classList.contains("active"));
    await choose(ctx, "people-view-name");
    assert.equal(items(ctx).find((li) => li.classList.contains("active")).personName, chosen);
  });

  test("the cards keep the order of the list", async (t) => {
    const { ctx } = await open(t, { view: "face" });
    const sort = ctx.document.getElementById("people-sort");
    sort.value = "name";
    sort.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.deepEqual(names(ctx), ["Bao Lindqvist", "Ines Okafor", "Wren Halloway"]);
  });
});

describe("where the choice is offered", () => {
  test("in Review People, not in Identify Faces", async (t) => {
    const { ctx } = await open(t);
    const choice = ctx.document.getElementById("people-view");
    assert.equal(choice.classList.contains("hidden"), false);
    const select = ctx.document.getElementById("tuner-mode");
    select.value = "unmatched-faces";
    select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 6);
    assert.equal(choice.classList.contains("hidden"), true);
  });

  test("Identify Faces stays a list of rows whatever was chosen", async (t) => {
    const { ctx, server } = await open(t, { view: "face", mode: "unmatched-faces" });
    assert.equal(ctx.document.getElementById("photo-list").classList.contains("by-face"), false);
    assert.equal(server.urls().filter((u) => u.includes("people-faces")).length, 0);
  });
});
