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
  const PHOTOS = ["a.jpg", "b.jpg"].map((filename) => ({
    path: `D:/Library/2020/${filename}`, filename, folder: "D:/Library/2020", year: "2020", unmatched_count: 1,
  }));

  function server() {
    return new FakeServer()
      .on("/api/apps", { this: "tuner", apps: {} })
      .on("/api/taxonomy/tree", TREE)
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/databases", { databases: ["photo_index"] })
      .on("/api/people-with-counts", [])
      .on("/api/people", [])
      .on("/api/photos", PHOTOS)
      .on("/api/photo-details", (url) => ({
        path: decodeURIComponent(url.split("path=")[1]), filename: "a.jpg", year: "2020", caption: "",
        tags: [], people: [],
        faces: [{ id: 41, box: [10, 10, 40, 40], name: null, excluded: false, max_similarity: 0 }],
      }))
      .on("/api/face-matches", { matches: [] })
      .on("/api/unmatched-faces/people", [{ name: "Unknown Faces", count: 3, unit: "face" },
                                          { name: "Ungrouped", count: 2, unit: "face" }])
      .on("/api/unmatched-faces/person-matches", {
        faces: [1, 2, 3].map((id) => ({
          id, photo_path: `D:\\meets\\m${id}.jpg`, filename: `m${id}.jpg`, box: [10, 10, 40, 40], prob: 0.99,
          mtime: 0, year: 2025, similarity: 0.9, person_similarity: 0.9, cluster_id: 5, cluster_name: "Cluster 5",
          other_names: [], suggested_name: null, suggested_similarity: 0, suggestion_strength: null,
        })),
        total_count: 3, unclustered_total: 0, unclustered_shown: 0, has_more: false,
      });
  }

  /** The folder view, its first photo shown (the sidebar's first item active). */
  async function folderView(t) {
    const ctx = await loadApp("tagtuner", { t, server: server() });
    await flush(ctx.window, 6);
    click(ctx.window, ctx.document.querySelector("#photo-list .folder-header"));
    click(ctx.window, ctx.document.querySelector("#photo-list .photo-item"));
    await flush(ctx.window, 6);
    return ctx;
  }

  /** Identify Faces on its first person (the sidebar's first item active). */
  async function identifyView(t) {
    const ctx = await loadApp("tagtuner", {
      t, server: server(),
      url: `http://localhost:8080/photo_index/?mode=unmatched-faces&person=${encodeURIComponent("Unknown Faces")}`,
    });
    ctx.window.confirm = () => true;
    ctx.window.alert = () => {};
    await new Promise((r) => ctx.window.setTimeout(r, 150));
    return ctx;
  }

  const active = (ctx) => ctx.document.querySelector("#photo-list .photo-item.active")?.textContent || null;

  // Each opened the way the page opens it -- its own button -- and closed by its own Cancel.
  const DIALOGS = {
    "folder-picker-modal": {
      view: folderView,
      open: (ctx) => click(ctx.window, ctx.document.getElementById("btn-add-folder")),
      close: "btn-folder-picker-cancel",
    },
    "remove-folder-modal": {
      view: folderView,
      open: (ctx) => click(ctx.window, ctx.document.getElementById("btn-remove-folder")),
      close: "btn-remove-folder-cancel",
    },
    "new-person-modal": {
      view: folderView,
      open: async (ctx) => {
        click(ctx.window, ctx.document.querySelector("#faces-grid .face-card, .face-card"));
        await flush(ctx.window, 4);
        const create = [...ctx.document.querySelectorAll(".face-card button")]
          .find((b) => b.title === "Create new person profile");
        assert.ok(create, "the face has no Create new person button");
        click(ctx.window, create);
      },
      close: "btn-modal-cancel",
    },
    "exclude-reason-modal": {
      view: identifyView,
      open: async (ctx) => {
        click(ctx.window, ctx.document.querySelector('#matching-faces-grid [data-face-id="1"]'));
        await flush(ctx.window, 4);
        click(ctx.window, ctx.document.getElementById("btn-exclude-selected"));
      },
      close: "btn-exclude-reason-cancel",
    },
    "ignore-confirm-modal": {
      view: identifyView,
      open: (ctx) => {
        const ignore = [...ctx.document.querySelectorAll("#matching-faces-grid .matching-group-section button")]
          .find((b) => b.textContent.includes("Ignore Cluster"));
        assert.ok(ignore, "the cluster has no Ignore Cluster button");
        click(ctx.window, ignore);
      },
      close: "btn-ignore-confirm-cancel",
    },
  };

  for (const [id, how] of Object.entries(DIALOGS)) {
    test(id, async (t) => {
      const ctx = await how.view(t);
      const modal = ctx.document.getElementById(id);
      assert.ok(modal.classList.contains("hidden"), "it starts open");
      const before = active(ctx);
      assert.ok(before, "nothing in the sidebar is active to move from");
      await how.open(ctx);
      await flush(ctx.window, 4);
      assert.ok(!modal.classList.contains("hidden"), "the page's own button did not open it");
      for (const target of [ctx.document.body, modal.querySelector("button")]) {
        for (const key of ["ArrowDown", "ArrowUp", "ArrowDown"]) press(ctx.window, target, key);
      }
      await flush(ctx.window, 4);
      assert.equal(active(ctx), before, "the sidebar moved behind it");

      click(ctx.window, ctx.document.getElementById(how.close));
      await flush(ctx.window, 4);
      assert.ok(modal.classList.contains("hidden"), "its Cancel did not close it");
      press(ctx.window, ctx.document.body, "ArrowDown");
      await flush(ctx.window, 4);
      assert.notEqual(active(ctx), before, "the sidebar stayed still once it closed");
    });
  }
});
