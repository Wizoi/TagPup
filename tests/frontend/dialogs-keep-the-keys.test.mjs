/**
 * While a dialog is open, the page's shortcuts do not act on the page behind it.
 *
 * TagPup steps its photos on the arrows and acts on Ctrl+D (carry tags forward), Ctrl+Z
 * (undo) and Ctrl+S (save); TagTuner moves its sidebar on ArrowUp and ArrowDown. They
 * listen on the document, so a key pressed in a dialog, or on its background, went on
 * to the page: only the tag editor kept them, with a guard of its own. Each handler
 * now asks web/common/dialog.js's dialogOpen() first, whichever dialog it is.
 *
 * TagPup's Smart Rename and Shift Date Taken ask with the browser's confirm(), which
 * holds every key until it is answered; there is no page of theirs to reach.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const TREE = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1, hidden_from_autocomplete: 0 },
  { id: 2, tag: "Activity", name: "Activity", parent_id: null, has_face: 0, hidden_from_autocomplete: 0 },
  { id: 3, tag: "Places", name: "Places", parent_id: null, has_face: 0, hidden_from_autocomplete: 0 },
];

function press(window, target, key, mods = {}) {
  const event = new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...mods });
  target.dispatchEvent(event);
  return event;
}

/** TagPup on the second of two photos, the first carrying a tag Ctrl+D would carry over. */
async function tagpupOnSecondPhoto(t, server = new FakeServer()) {
  server
    .on("/api/apps", { this: "tagpup", apps: {} })
    .on("/api/taxonomy/tree", TREE)
    .on("/api/taxonomy/delete-check", { success: true, used: true, count: 1 })
    .on("/api/tags", ["Activity"])
    .on("/api/people", [])
    .on("/api/databases", { databases: ["photo_index"] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg", tags: ["Activity"] }), photoRecord({ filename: "b.jpg" })])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true });
  const ctx = await loadApp("tagpup", { t, server });
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[1]);
  await flush(ctx.window, 6);
  ctx.shown = () => ctx.document.querySelector(".photo-item-file.active")?.getAttribute("data-path");
  ctx.status = () => ctx.document.getElementById("status-text").textContent;
  ctx.saves = () => server.calls.filter((c) => c.url.includes("save-metadata")).length;
  return ctx;
}

/** A title typed and not saved: what Ctrl+S would write. */
function typeATitle(ctx, text) {
  const title = ctx.document.getElementById("input-photo-title");
  title.value = text;
  title.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
}

/** The focus on the dialog's background -- where a click on it leaves it, the body -- or on `target`. */
function focusOn(document, target) {
  if (target === document.body) document.activeElement.blur();
  else target.focus();
}

/**
 * Every shortcut TagPup has, with the focus on the dialog's background and on a button
 * in it: none may act. The arrows, Ctrl+D and Ctrl+Z first, with nothing unsaved (an
 * edit would have the arrows ask about it rather than step); then Ctrl+S, with a title
 * typed that it would save.
 */
async function assertThePageKeepsStill(ctx, inDialog, label) {
  const { window, document } = ctx;
  const shown = ctx.shown();
  const saves = ctx.saves();
  document.getElementById("status-text").textContent = "untouched";
  for (const target of [document.body, inDialog]) {
    focusOn(document, target);
    for (const key of ["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"]) press(window, target, key);
    for (const key of ["d", "z"]) {
      press(window, target, key, { ctrlKey: true });
      press(window, target, key, { metaKey: true });
    }
    await flush(window, 6);
    assert.equal(ctx.shown(), shown, `${label}: an arrow stepped the photo behind it`);
    assert.equal(ctx.status(), "untouched", `${label}: Ctrl+D or Ctrl+Z acted behind it`);
    assert.equal(ctx.saves(), saves, `${label}: Ctrl+D wrote the photo behind it`);
  }
  const title = document.getElementById("input-photo-title").value;
  typeATitle(ctx, "Harbour Day");
  for (const target of [document.body, inDialog]) {
    focusOn(document, target);
    press(window, target, "s", { ctrlKey: true });
    press(window, target, "s", { metaKey: true });
    await flush(window, 6);
    assert.equal(ctx.saves(), saves, `${label}: Ctrl+S saved the photo behind it`);
  }
  typeATitle(ctx, title);
}

describe("TagPup: the page behind a dialog hears none of its shortcuts", () => {
  test("the tag editor", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    click(ctx.window, ctx.document.getElementById("btn-gear"));
    click(ctx.window, ctx.document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(ctx.window, 4);
    const editor = ctx.document.getElementById("taxonomy-modal");
    assert.ok(editor.classList.contains("active"));
    await assertThePageKeepsStill(ctx, editor.querySelector("button"), "tag editor");
  });

  test("the tag editor's delete question", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    click(ctx.window, ctx.document.getElementById("btn-gear"));
    click(ctx.window, ctx.document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(ctx.window, 4);
    const remove = [...ctx.document.querySelectorAll('#taxonomy-modal li[data-id="2"] > .taxonomy-node-content button')]
      .find((b) => b.textContent.includes("\u{1F5D1}"));
    click(ctx.window, remove);
    await flush(ctx.window, 4);
    const conflict = ctx.document.querySelector(".tag-editor-conflict");
    assert.ok(conflict, "the delete question did not open");
    await assertThePageKeepsStill(ctx, conflict.querySelector(".btn-cancel"), "delete question");
  });

  test("the placement question", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    const input = ctx.document.getElementById("input-add-tag");
    input.value = "Relays";
    press(ctx.window, input, "Enter");
    await flush(ctx.window, 6);
    const modal = ctx.document.querySelector(".modal-overlay.active");
    assert.ok(modal, "the placement question did not open");
    await assertThePageKeepsStill(ctx, modal.querySelector(".btn-cancel"), "placement question");

    click(ctx.window, modal.querySelector(".btn-cancel"));
    await flush(ctx.window, 6);
    // The tag it asked about is still typed: taken out, and the focus out of its field,
    // whose arrows are its own.
    ctx.document.getElementById("input-add-tag").value = "";
    ctx.document.activeElement.blur();
    press(ctx.window, ctx.document.body, "ArrowUp");
    await flush(ctx.window, 6);
    assert.match(ctx.shown(), /a\.jpg$/, "the arrows stayed kept after the question closed");
  });

  test("the Date Taken dialog", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    click(ctx.window, ctx.document.getElementById("btn-edit-date-taken"));
    await flush(ctx.window, 2);
    const modal = ctx.document.getElementById("date-taken-modal");
    assert.ok(modal.classList.contains("active"), "the Date Taken dialog did not open");
    await assertThePageKeepsStill(ctx, modal.querySelector("button"), "Date Taken");
  });

  test("with no dialog open, the shortcuts act as before", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    press(ctx.window, ctx.document.body, "d", { ctrlKey: true });
    await flush(ctx.window, 8);
    assert.equal(ctx.saves(), 1, "Ctrl+D did not carry the tags over");
    press(ctx.window, ctx.document.body, "ArrowUp");
    await flush(ctx.window, 6);
    assert.match(ctx.shown(), /a\.jpg$/);
  });
});

describe("TagTuner: the sidebar does not move behind a dialog", () => {
  async function tuner(t) {
    const server = new FakeServer()
      .on("/api/apps", { this: "tuner", apps: {} })
      .on("/api/taxonomy/tree", TREE)
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/databases", { databases: ["photo_index"] })
      .on("/api/people-with-counts", [])
      .on("/api/people", [])
      .on("/api/photos", ["a.jpg", "b.jpg"].map((filename) => ({
        path: `D:/Library/2020/${filename}`, filename, folder: "D:/Library/2020", year: "2020", unmatched_count: 1,
      })));
    const ctx = await loadApp("tagtuner", { t, server });
    await flush(ctx.window, 6);
    click(ctx.window, ctx.document.querySelector("#photo-list .folder-header"));
    ctx.active = () => ctx.document.querySelector("#photo-list .photo-item.active")?.textContent || null;
    return ctx;
  }

  // Shown the way the page shows them: `hidden` taken off.
  for (const id of ["new-person-modal", "exclude-reason-modal", "ignore-confirm-modal", "folder-picker-modal"]) {
    test(id, async (t) => {
      const ctx = await tuner(t);
      const modal = ctx.document.getElementById(id);
      assert.ok(modal.classList.contains("hidden"), "it starts open");
      modal.classList.remove("hidden");
      const before = ctx.active();
      for (const target of [ctx.document.body, modal.querySelector("button")]) {
        for (const key of ["ArrowDown", "ArrowUp", "ArrowDown"]) press(ctx.window, target, key);
      }
      await flush(ctx.window, 4);
      assert.equal(ctx.active(), before, "the sidebar moved behind it");

      modal.classList.add("hidden");
      press(ctx.window, ctx.document.body, "ArrowDown");
      await flush(ctx.window, 4);
      assert.notEqual(ctx.active(), before, "the sidebar stayed still once it closed");
    });
  }
});
