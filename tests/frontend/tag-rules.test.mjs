/**
 * A tag or a name that breaks the rules is refused before anything is sent.
 *
 * The servers refuse them as well (tests/test_tags_are_checked_where_they_are_set.py).
 * Asking here first matters because TagPup creates the tag-tree node before it
 * writes the photo: refused only at the write, a bad tag would already be in the
 * tree. And the typed text stays where it was, with the reason, to be corrected.
 *
 * Both pages ask web/common/vocabulary.js, which asks web/common/validate.js, which
 * applies the rules the server publishes; tests/frontend/validation.test.mjs runs it
 * against the cases the server is held to (tests/validation_cases.json).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps, pageSource } from "./harness.mjs";

const RULES = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "tests", "validation_cases.json"), "utf8"));

/** TagPup's page: its modules, as the browser loads them (harness.mjs). */
const TAGPUP = pageSource("tagpup");

function functionSource(source, name) {
  const lines = source.split(/\r?\n/);
  const start = lines.findIndex((l) => new RegExp(`^\\s*function\\s+${name}\\s*\\(`).test(l));
  assert.ok(start >= 0, `function ${name} is gone`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((l, i) => i > start && l === `${indent}}`);
  assert.ok(end > start, `could not find the end of ${name}`);
  return lines.slice(start, end + 1).join("\n");
}

describe("web/tagpup/tags.js: a typed tag is set in its one spelling", () => {
  // Only TagPup's page turns typed text into a tag path.
  const source = TAGPUP;
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
