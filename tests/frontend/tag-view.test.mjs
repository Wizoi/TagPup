/**
 * Review Tags: the word tags, which face curation could not reach.
 *
 * A tagged photo is what the suggester learns the next photo from, so a misspelling
 * spreads exactly as a wrong name does. Until this view there was nowhere to ask
 * "where is this tag, and what does it touch" -- finding one typo took a database
 * query, an ExifTool sweep and three wrong guesses.
 *
 * The buckets are the point of the sidebar. A flat alphabetical list of 881 tags
 * hides the handful worth looking at; the buckets put those on top, the way Unknown
 * Faces is pinned in the people list.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click } from "./harness.mjs";

const TAGS = {
  tags: [
    { tag: "Activity/Cross Country", leaf: "Cross Country", count: 316, flat: false,
      in_taxonomy: true, has_embedding: true, is_person: false },
    { tag: "Kentridge", leaf: "Kentridge", count: 12, flat: true,
      in_taxonomy: true, has_embedding: true, is_person: false },
    { tag: "Regata", leaf: "Regata", count: 1, flat: true,
      in_taxonomy: false, has_embedding: true, is_person: false },
    { tag: "Activity/Rowing", leaf: "Rowing", count: 0, flat: false,
      in_taxonomy: true, has_embedding: true, is_person: false },
  ],
  buckets: {
    flat: ["Kentridge", "Regata"],
    used_once: ["Regata"],
    unused: ["Activity/Rowing"],
    people_without_a_path: [],
  },
};

function build(extra = (s) => s) {
  return extra(
    new FakeServer()
      .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/people-with-counts", [])
      .on("/api/people", [])
      .on("/api/photos", [])
      .on("/api/tags/list", TAGS)
      .on("/api/tags/photos", {
        tag: "Kentridge",
        total: 2,
        photos: [
          { path: "D:\\x\\a.jpg", filename: "a.jpg", mtime: 2, tags: ["Kentridge"] },
          { path: "D:\\x\\b.jpg", filename: "b.jpg", mtime: 1, tags: ["Kentridge"] },
        ],
      })
  );
}

async function openTagView(t, extra) {
  const ctx = await loadApp("tagtuner", {
    t, url: "http://localhost:8080/kr-track/", server: build(extra),
  });
  const mode = ctx.document.getElementById("tuner-mode");
  mode.value = "tags";
  mode.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
  await flush(ctx.window, 6);
  return ctx;
}

const rows = (ctx) => [...ctx.document.querySelectorAll("#photo-list .photo-item")];
const titles = (ctx) => rows(ctx).map((r) => r.querySelector(".photo-title").textContent);
const panel = (ctx) => ctx.document.getElementById("tag-view-content");

describe("opening the view", () => {
  test("the mode is offered", async (t) => {
    const ctx = await loadApp("tagtuner", { t, server: build() });
    const values = [...ctx.document.querySelectorAll("#tuner-mode option")].map((o) => o.value);
    assert.ok(values.includes("tags"), `no tags mode: ${values}`);
  });

  test("choosing it shows the panel and asks the server for the tags", async (t) => {
    const ctx = await openTagView(t);
    assert.ok(!panel(ctx).classList.contains("hidden"), "the panel stayed hidden");
    assert.ok(ctx.server.urls().some((u) => u.includes("/api/tags/list")));
  });

  test("it hides the face-matching panel", async (t) => {
    // Two panels on screen at once is how the sidebar and the dropdown started
    // disagreeing about what you were looking at.
    const ctx = await openTagView(t);
    assert.ok(
      ctx.document.getElementById("face-matching-content").classList.contains("hidden"));
  });
});

describe("the sidebar", () => {
  test("every tag is listed with how many photos carry it", async (t) => {
    const ctx = await openTagView(t);
    const shown = titles(ctx);
    for (const tag of ["Activity/Cross Country", "Kentridge", "Regata", "Activity/Rowing"]) {
      assert.ok(shown.includes(tag), `${tag} missing from ${JSON.stringify(shown)}`);
    }
    const row = rows(ctx).find((r) => r.dataset.tag === "Activity/Cross Country");
    assert.match(row.querySelector(".photo-badge").textContent, /316 photos/);
  });

  test("the buckets are pinned above the tags", async (t) => {
    // A flat list of 881 tags hides the handful worth looking at.
    const ctx = await openTagView(t);
    const first = rows(ctx).slice(0, 3).map((r) => r.dataset.bucket);
    assert.deepEqual(first, ["flat", "used_once", "unused"]);
  });

  test("an empty bucket is not shown", async (t) => {
    const ctx = await openTagView(t);
    const buckets = rows(ctx).map((r) => r.dataset.bucket).filter(Boolean);
    assert.ok(!buckets.includes("people_without_a_path"));
  });

  test("a tag on no photo says so", async (t) => {
    // It can still be suggested, which is usually a reason to retire it.
    const ctx = await openTagView(t);
    const row = rows(ctx).find((r) => r.dataset.tag === "Activity/Rowing");
    assert.match(row.querySelector(".photo-badge").textContent, /no photos/);
    assert.match(row.querySelector(".tag-item-note").textContent, /orphan/);
  });

  test("a tag with no hierarchy is marked", async (t) => {
    const ctx = await openTagView(t);
    const row = rows(ctx).find((r) => r.dataset.tag === "Kentridge");
    assert.match(row.querySelector(".tag-item-note").textContent, /flat/);
  });

  test("a pathed tag in use is not marked", async (t) => {
    const ctx = await openTagView(t);
    const row = rows(ctx).find((r) => r.dataset.tag === "Activity/Cross Country");
    assert.equal(row.querySelector(".tag-item-note"), null);
  });

  test("the search box filters it", async (t) => {
    const ctx = await openTagView(t);
    const search = ctx.document.getElementById("photo-search");
    search.value = "kentridge";
    search.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    await flush(ctx.window, 4);
    const listed = titles(ctx).filter((s) => !s.includes("hierarchy") && !s.includes("Used")
      && !s.includes("On no photo"));
    assert.ok(listed.includes("Kentridge"));
    assert.ok(!listed.includes("Activity/Cross Country"));
  });
});

describe("opening a tag", () => {
  async function openKentridge(t) {
    const ctx = await openTagView(t);
    click(ctx.window, rows(ctx).find((r) => r.dataset.tag === "Kentridge"));
    await flush(ctx.window, 6);
    return ctx;
  }

  test("its photos are asked for and shown", async (t) => {
    const ctx = await openKentridge(t);
    assert.ok(ctx.server.urls().some((u) => u.includes("/api/tags/photos")));
    assert.equal(ctx.document.querySelectorAll(".tag-photo-card").length, 2);
  });

  test("the header names it and says what it is", async (t) => {
    const ctx = await openKentridge(t);
    assert.equal(ctx.document.getElementById("tag-view-name").textContent, "Kentridge");
    const summary = ctx.document.getElementById("tag-view-summary").textContent;
    assert.match(summary, /2 photos/);
    assert.match(summary, /no hierarchy/);
  });

  test("the actions become available", async (t) => {
    const ctx = await openKentridge(t);
    for (const id of ["btn-tag-rename", "btn-tag-merge", "btn-tag-retire"]) {
      assert.equal(ctx.document.getElementById(id).disabled, false, id);
    }
  });

  test("they are unavailable before a tag is chosen", async (t) => {
    const ctx = await openTagView(t);
    for (const id of ["btn-tag-rename", "btn-tag-merge", "btn-tag-retire"]) {
      assert.equal(ctx.document.getElementById(id).disabled, true, id);
    }
  });
});

describe("changing a tag", () => {
  async function renameKentridge(t, { answer = "School/Kentridge", agree = true } = {}) {
    const ctx = await openTagView(t, (s) =>
      s.on("/api/tags/merge", {
        from: "Kentridge", into: "School/Kentridge", photos: 12,
        photos_already_carrying_the_target: 0, embeddings_to_drop: 1,
        taxonomy_rows_to_drop: 1, applied: false,
      }));
    click(ctx.window, rows(ctx).find((r) => r.dataset.tag === "Kentridge"));
    await flush(ctx.window, 6);

    ctx.window.prompt = () => answer;
    ctx.window.confirm = () => agree;
    ctx.document.getElementById("btn-tag-rename").click();
    await flush(ctx.window, 8);
    return ctx;
  }

  test("the plan is asked for before anything is written", async (t) => {
    // It rewrites every photo file carrying the tag. The plan is the last point at
    // which that costs nothing.
    const ctx = await renameKentridge(t, { agree: false });
    const calls = ctx.server.calls.filter((c) => c.url.includes("/api/tags/merge"));
    assert.equal(calls.length, 1, "more than a dry run was sent before confirming");
    assert.ok(!calls[0].body.apply, "the first call asked to write");
  });

  test("declining writes nothing", async (t) => {
    const ctx = await renameKentridge(t, { agree: false });
    const applied = ctx.server.calls
      .filter((c) => c.url.includes("/api/tags/merge") && c.body && c.body.apply);
    assert.equal(applied.length, 0);
  });

  test("agreeing sends the write", async (t) => {
    const ctx = await renameKentridge(t);
    const applied = ctx.server.calls
      .filter((c) => c.url.includes("/api/tags/merge") && c.body && c.body.apply);
    assert.equal(applied.length, 1);
    assert.equal(applied[0].body.from, "Kentridge");
    assert.equal(applied[0].body.into, "School/Kentridge");
  });

  test("cancelling the prompt sends nothing at all", async (t) => {
    const ctx = await renameKentridge(t, { answer: "" });
    assert.equal(
      ctx.server.calls.filter((c) => c.url.includes("/api/tags/merge")).length, 0);
  });

  test("a name that cannot be set is refused before the plan is asked for", async (t) => {
    // The server refuses it too; asking first keeps a doomed rename from reaching it.
    const alerts = [];
    const ctx = await openTagView(t, (s) => s.on("/api/tags/merge", { photos: 12 }));
    click(ctx.window, rows(ctx).find((r) => r.dataset.tag === "Kentridge"));
    await flush(ctx.window, 6);
    ctx.window.prompt = () => "School|Kentridge";
    ctx.window.alert = (message) => alerts.push(message);
    ctx.document.getElementById("btn-tag-rename").click();
    await flush(ctx.window, 8);

    assert.equal(
      ctx.server.calls.filter((c) => c.url.includes("/api/tags/merge")).length, 0);
    assert.match(alerts[0] || "", /cannot contain "\|"/);
  });

  test("renaming a tag to itself does nothing", async (t) => {
    const ctx = await renameKentridge(t, { answer: "Kentridge" });
    assert.equal(
      ctx.server.calls.filter((c) => c.url.includes("/api/tags/merge")).length, 0);
  });

  test("the list is reloaded afterwards", async (t) => {
    const ctx = await renameKentridge(t);
    const listCalls = ctx.server.urls().filter((u) => u.includes("/api/tags/list"));
    assert.ok(listCalls.length >= 2, "the sidebar still shows the old tag");
  });
});
