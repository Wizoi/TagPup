/**
 * TagTuner's lists of people and tags are alphabetical by the page's shared order (web/common/vocabulary.js):
 * the people sidebar by name, and by count with the alphabet breaking a tie, the tag list likewise, the
 * bucket members, the name picker. The pinned buckets stay on top.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const PEOPLE = [
  { name: "Zoe Abbott", count: 3 },
  { name: "Émile Roy", count: 8 },
  { name: "anh Tran", count: 3 },
  { name: "Bao Le", count: 8 },
  { name: "Ines Tran 10", count: 1 },
  { name: "Ines Tran 3", count: 1 },
];

async function open(t, { mode, order, server }) {
  const ctx = await loadApp("tagtuner", { t, url: "http://localhost:8080/kr-track/", server });
  const select = ctx.document.getElementById("tuner-mode");
  select.value = mode;
  select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
  await flush(ctx.window, 6);
  if (order) {
    const sort = ctx.document.getElementById("people-sort");
    sort.value = order;
    sort.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
  }
  return ctx;
}

const base = () => new FakeServer()
  .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
  .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
  .on("/api/photos", []);

const titles = (ctx) => [...ctx.document.querySelectorAll("#photo-list .photo-title")].map((el) => el.textContent);

describe("the people sidebar", () => {
  const server = () => base().on("/api/people-with-counts", PEOPLE).on("/api/people", []).on("/api/tags/list", { tags: [], buckets: {} });

  test("by name it is alphabetical, accents and case ignored, numbers in order", async (t) => {
    const ctx = await open(t, { mode: "face-matching", order: "name", server: server() });
    assert.deepEqual(titles(ctx),
      ["anh Tran", "Bao Le", "Émile Roy", "Ines Tran 3", "Ines Tran 10", "Zoe Abbott"]);
  });

  test("by count the biggest come first and the alphabet breaks a tie", async (t) => {
    const ctx = await open(t, { mode: "face-matching", order: "count", server: server() });
    assert.deepEqual(titles(ctx),
      ["Bao Le", "Émile Roy", "anh Tran", "Zoe Abbott", "Ines Tran 3", "Ines Tran 10"]);
  });

  test("the first visit is by name, and a later one starts on the last choice (#793)", async (t) => {
    const first = await open(t, { mode: "face-matching", server: server() });
    assert.equal(first.document.getElementById("people-sort").value, "name");
    assert.deepEqual(titles(first),
      ["anh Tran", "Bao Le", "Émile Roy", "Ines Tran 3", "Ines Tran 10", "Zoe Abbott"]);
    const sort = first.document.getElementById("people-sort");
    sort.value = "count";
    sort.dispatchEvent(new first.window.Event("change", { bubbles: true }));
    assert.equal(first.window.localStorage.getItem("tagtuner.peopleSort"), "count");
  });
});

describe("the tag list", () => {
  const TAGS = ["Zoo", "apple", "Trip 10", "Trip 3", "Éclair"].map((tag, i) => ({
    tag, leaf: tag, count: [5, 5, 2, 2, 9][i], flat: true, in_taxonomy: true, has_embedding: true, is_person: false,
  }));
  const server = () => base().on("/api/people-with-counts", []).on("/api/people", [])
    .on("/api/tags/list", { tags: TAGS, buckets: { flat: ["Zoo", "apple", "Trip 10", "Trip 3"], used_once: [], unused: [], people_without_a_path: [] } });

  test("by name it is alphabetical", async (t) => {
    const ctx = await open(t, { mode: "tags", order: "name", server: server() });
    const names = titles(ctx).filter((s) => !/hierarchy/.test(s));
    assert.deepEqual(names, ["apple", "Éclair", "Trip 3", "Trip 10", "Zoo"]);
  });

  test("by count the alphabet breaks a tie", async (t) => {
    const ctx = await open(t, { mode: "tags", order: "count", server: server() });
    const names = titles(ctx).filter((s) => !/hierarchy/.test(s));
    assert.deepEqual(names, ["Éclair", "apple", "Zoo", "Trip 3", "Trip 10"]);
  });

  test("a bucket's tags are alphabetical", async (t) => {
    const ctx = await open(t, { mode: "tags", server: server() });
    ctx.document.querySelector("#photo-list .tag-bucket-item").click();
    await flush(ctx.window, 4);
    const members = [...ctx.document.querySelectorAll(".tag-bucket-member")].map((el) => el.textContent);
    assert.deepEqual(members, ["apple", "Trip 3", "Trip 10", "Zoo"]);
  });
});

describe("the name picker", () => {
  test("offers people alphabetically", async (t) => {
    const server = base().on("/api/people-with-counts", []).on("/api/people", ["Zoe Abbott", "émile Roy", "anh Tran", "Bao Le"])
      .on("/api/tags/list", { tags: [], buckets: {} });
    const ctx = await open(t, { mode: "face-matching", server });
    const offered = [...ctx.document.querySelectorAll("#people-datalist option")].map((o) => o.value);
    assert.deepEqual(offered, ["anh Tran", "Bao Le", "émile Roy", "Zoe Abbott"]);
  });
});
