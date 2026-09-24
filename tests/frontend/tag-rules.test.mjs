/**
 * A tag or a name that breaks the rules is refused before anything is sent.
 *
 * The servers refuse them as well (tests/test_tags_are_checked_where_they_are_set.py).
 * Asking here first matters because TagPup creates the tag-tree node before it
 * writes the photo: refused only at the write, a bad tag would already be in the
 * tree. And the typed text stays where it was, with the reason, to be corrected.
 *
 * Each page carries its own copy of the rules. Both are run against
 * tests/tag_rules.json, the cases the server's copy is held to, so all three give
 * the same answer in the same words.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps } from "./harness.mjs";

const RULES = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "tests", "tag_rules.json"), "utf8"));

const PAGES = {
  "gui_tagpup/app.js": path.join(REPO_ROOT, "gui_tagpup", "app.js"),
  "gui/app.js": path.join(REPO_ROOT, "gui", "app.js"),
};

function functionSource(source, name) {
  const lines = source.split(/\r?\n/);
  const start = lines.findIndex((l) => new RegExp(`^\\s*function\\s+${name}\\s*\\(`).test(l));
  assert.ok(start >= 0, `function ${name} is gone`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((l, i) => i > start && l === `${indent}}`);
  assert.ok(end > start, `could not find the end of ${name}`);
  return lines.slice(start, end + 1).join("\n");
}

/** The page's own tagProblem and nameProblem, exactly as shipped. */
function rulesOf(file) {
  const source = fs.readFileSync(PAGES[file], "utf8");
  const body = ["tagProblem", "nameProblem", "textProblem"]
    .map((name) => functionSource(source, name))
    .join("\n");
  return new Function(`${body}\nreturn { tagProblem, nameProblem };`)();
}

for (const file of Object.keys(PAGES)) {
  describe(`${file}: what may be set`, () => {
    const { tagProblem, nameProblem } = rulesOf(file);

    test("tags, as the server answers them", () => {
      for (const [text, expected] of RULES.tags) {
        assert.equal(tagProblem(text), expected, JSON.stringify(text));
      }
    });

    test("names, as the server answers them", () => {
      for (const [text, expected] of RULES.names) {
        assert.equal(nameProblem(text), expected, JSON.stringify(text));
      }
    });
  });
}

describe("gui_tagpup/app.js: what a Smart Rename grouping may hold", () => {
  const source = fs.readFileSync(PAGES["gui_tagpup/app.js"], "utf8");
  const groupingProblem = new Function(
    `${functionSource(source, "groupingProblem")}\nreturn groupingProblem;`)();

  test("as the server answers it", () => {
    for (const [grouping, expected] of RULES.groupings) {
      assert.equal(groupingProblem(grouping), expected, JSON.stringify(grouping));
    }
  });
});

describe("gui_tagpup/app.js: a typed tag is set in its one spelling", () => {
  // Only TagPup's page turns typed text into a tag path.
  const source = fs.readFileSync(PAGES["gui_tagpup/app.js"], "utf8");
  const normalizeTag = new Function(`${functionSource(source, "normalizeTag")}\nreturn normalizeTag;`)();

  test("as the server spells it", () => {
    for (const [typed, stored] of RULES.spelled) {
      assert.equal(normalizeTag(typed), stored, JSON.stringify(typed));
    }
  });
});

afterEach(() => closeAllApps());

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Hazel Brookmire", name: "Hazel Brookmire", parent_id: 1, has_face: 1 },
  { id: 3, tag: "Activity", name: "Activity", parent_id: null, has_face: 0 },
];

async function onAPhoto(t) {
  const server = new FakeServer()
    .on("/api/tags", ["People/Hazel Brookmire"])
    .on("/api/people", ["Hazel Brookmire"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg", tags: ["Activity"] })])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/photos/bulk-tags", { success: true });
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  ctx.document.querySelector("li[data-path]").click();
  await flush(ctx.window, 8);
  ctx.alerts = [];
  ctx.window.alert = (message) => ctx.alerts.push(message);
  return ctx;
}

function typeAndEnter(ctx, id, text) {
  const input = ctx.document.getElementById(id);
  input.focus();
  input.value = text;
  input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
  input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  return input;
}

const writes = (ctx) =>
  ctx.server.calls.filter((c) => /taxonomy\/create|save-metadata|bulk-tags/.test(c.url));

describe("TagPup: a tag that cannot be set", () => {
  test("typed on a photo, sends nothing, says why, and stays in the box", async (t) => {
    const ctx = await onAPhoto(t);
    const input = typeAndEnter(ctx, "input-add-tag", "Places|Harbour");
    await flush(ctx.window, 8);

    assert.deepEqual(writes(ctx), [], "a refused tag still reached the server");
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /cannot contain "\|"/);
    assert.equal(input.value, "Places|Harbour");
  });

  test("typed as a person, is held to the same rule", async (t) => {
    const ctx = await onAPhoto(t);
    typeAndEnter(ctx, "input-add-person", "People/Rowan\u2028Thackeray");
    await flush(ctx.window, 8);

    assert.deepEqual(writes(ctx), []);
    assert.match(ctx.alerts[0] || "", /control character/);
  });

  test("a path typed with spaces is written as the tree spells it", async (t) => {
    // It was written as typed, beside the tree's People/Rowan Thackeray.
    const ctx = await onAPhoto(t);
    typeAndEnter(ctx, "input-add-tag", "People / Rowan Thackeray");
    await flush(ctx.window, 12);

    const saved = ctx.server.lastBody("/api/photo/save-metadata");
    assert.ok(saved, "nothing was saved");
    assert.ok(saved.tags.includes("People/Rowan Thackeray"), `wrote: ${saved.tags}`);
    assert.ok(!saved.tags.includes("People / Rowan Thackeray"), `wrote: ${saved.tags}`);
  });

  test("a tag the server refuses to create is not written", async (t) => {
    const ctx = await onAPhoto(t);
    ctx.server.routes.unshift({
      match: "/api/taxonomy/create",
      body: { success: false, error: "Tag name cannot be empty" },
      status: 400,
    });
    typeAndEnter(ctx, "input-add-tag", "Places/Harbour");
    await flush(ctx.window, 12);

    assert.equal(ctx.server.lastBody("/api/photo/save-metadata"), undefined);
    assert.match(ctx.alerts[0] || "", /Tag name cannot be empty/);
  });

  test("a good tag typed beside it is not written either", async (t) => {
    // All or nothing: half a line written and half left in the box reads as saved.
    const ctx = await onAPhoto(t);
    typeAndEnter(ctx, "input-add-tag", "Places/Harbour, Places//Dunes");
    await flush(ctx.window, 8);

    assert.deepEqual(writes(ctx), []);
    assert.match(ctx.alerts[0] || "", /empty level/);
  });
});
