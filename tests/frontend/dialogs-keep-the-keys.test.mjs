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
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports } from "./harness.mjs";

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
async function tagpupOnSecondPhoto(t, server = new FakeServer(), second = {}) {
  server
    .on("/api/apps", { this: "tagpup", apps: {} })
    .on("/api/taxonomy/tree", TREE)
    .on("/api/taxonomy/delete-check", { success: true, used: true, count: 1 })
    .on("/api/tags", ["Activity"])
    .on("/api/people", [])
    .on("/api/databases", { databases: ["photo_index"] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg", tags: ["Activity"] }), photoRecord({ filename: "b.jpg", ...second })])
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

  test("the Date Taken dialog: Enter saves, Escape cancels, the file name is shown", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    const { window, document } = ctx;
    const modal = document.getElementById("date-taken-modal");
    const input = document.getElementById("input-date-taken");
    const dateSaves = () => ctx.saves();
    const open = async () => {
      click(window, document.getElementById("btn-edit-date-taken"));
      await flush(window, 2);
    };

    await open();
    assert.equal(document.getElementById("date-taken-file").textContent, "b.jpg", "the dialog does not name the file");
    assert.equal(document.getElementById("date-taken-file").title, "b.jpg");
    assert.match(document.getElementById("date-taken-note").textContent, /this photo was taken/);

    // Enter with nothing typed (or a part-typed date, which the field reports as empty): no save, still open.
    let alerted = 0;
    window.alert = () => { alerted += 1; };
    input.value = "";
    press(window, input, "Enter");
    await flush(window, 6);
    assert.equal(dateSaves(), 0, "an empty date was written");
    assert.ok(modal.classList.contains("active"), "the dialog closed on an empty date");
    assert.equal(alerted, 1, "the owner was not told why nothing was saved");

    // Enter on a good date saves once and closes, and does not reach the page (it would open a photo); rapid Enters write once.
    input.value = "2018-03-04T09:08:07";
    const first = press(window, input, "Enter");
    const second = press(window, input, "Enter");
    await flush(window, 8);
    assert.equal(dateSaves(), 1, "Enter did not save once");
    assert.ok(!modal.classList.contains("active"), "Enter did not close the dialog");
    assert.ok(first.defaultPrevented && second.defaultPrevented);

    // Escape cancels without a write.
    await open();
    press(window, input, "Escape");
    await flush(window, 4);
    assert.ok(!modal.classList.contains("active"), "Escape did not close the dialog");
    assert.equal(dateSaves(), 1);

    // Enter on the Cancel button is the button's own: it does not save.
    await open();
    press(window, document.getElementById("btn-cancel-date-modal"), "Enter");
    await flush(window, 4);
    assert.equal(dateSaves(), 1, "Enter on Cancel saved");
  });

  test("the Date Taken dialog says photo or video for a clip", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t, new FakeServer(), { filename: "c.mp4", media: "video" });
    click(ctx.window, ctx.document.getElementById("btn-edit-date-taken"));
    await flush(ctx.window, 2);
    assert.equal(ctx.document.getElementById("date-taken-file").textContent, "c.mp4");
    assert.match(ctx.document.getElementById("date-taken-note").textContent, /this photo or video was taken/);
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

/**
 * Every dialog keeps the keyboard one way (web/common/dialog.js, owner 2026-11-15): Escape
 * cancels it, Tab and Shift+Tab go round its controls, and the focus goes back to what
 * opened it -- so after Date Taken, saved or cancelled, the arrows step photos at once.
 */
const OPEN = ".modal-overlay.active, .tag-editor.active, .modal:not(.hidden)";
const openDialogs = (document) => [...document.querySelectorAll(OPEN)];
const PEOPLE = [{ id: 7, name: "Sam", tag: "People/Friends/Sam" }, { id: 8, name: "Sam", tag: "Pets/Sam" }];

function viaGear(action, id) {
  return {
    opener: (ctx) => ctx.document.getElementById("btn-gear"),
    open: (ctx) => {
      click(ctx.window, ctx.document.getElementById("btn-gear"));
      click(ctx.window, ctx.document.querySelector(`#gear-menu [data-action="${action}"]`));
    },
    dialog: (ctx) => ctx.document.getElementById(id),
  };
}

const dateButton = (ctx) => ctx.document.getElementById("btn-edit-date-taken");

// Each opened the way the page opens it, from the control the owner would be on.
const TAGPUP_DIALOGS = {
  "Date Taken": {
    opener: dateButton,
    open: (ctx) => click(ctx.window, dateButton(ctx)),
    dialog: (ctx) => ctx.document.getElementById("date-taken-modal"),
  },
  "the tag editor": viaGear("tag-editor", "taxonomy-modal"),
  "Library settings": viaGear("library-settings", "settings-modal"),
  "History": viaGear("library-history", "history-modal"),
  "Name faces from tags": viaGear("name-faces", "name-faces-modal"),
  "the placement question": {
    opener: (ctx) => {
      // The tag field, shown the way the owner shows it.
      click(ctx.window, ctx.document.getElementById("btn-edit-tags"));
      return ctx.document.getElementById("input-add-tag");
    },
    open: (ctx) => {
      const input = ctx.document.getElementById("input-add-tag");
      input.value = "Relays";
      press(ctx.window, input, "Enter");
    },
    dialog: (ctx) => ctx.document.querySelector(".modal-overlay.active"),
  },
  "Save changes?": {
    opener: dateButton,
    open: (ctx) => {
      typeATitle(ctx, "Harbour Day");
      press(ctx.window, dateButton(ctx), "ArrowLeft");
    },
    dialog: (ctx) => ctx.document.querySelector(".unsaved-edits-modal"),
    cancelled: (ctx) => assert.match(ctx.shown(), /b\.jpg$/, "Escape on Save changes? stepped the photo"),
  },
  "Which person?": {
    opener: dateButton,
    open: (ctx) => { ctx.answer = pageExports(ctx.window, "web/common/person-choice.js").choosePerson(PEOPLE); },
    dialog: (ctx) => ctx.document.querySelector(".person-choice"),
    cancelled: async (ctx) => assert.equal(await ctx.answer, null, "Escape chose a person"),
  },
  // Escape on "Add this folder?" is Just look (Not now), never Add.
  "Add this folder?": {
    opener: dateButton,
    open: (ctx) => pageExports(ctx.window, "web/tagpup/membership.js").askToAdd({ photos: 2, photos_held: 0, folder: "D:/Library/2020" }),
    dialog: (ctx) => ctx.document.getElementById("add-folder-modal"),
    cancelled: (ctx) => {
      assert.ok(ctx.document.body.classList.contains("just-looking"), "Escape was not Just look");
      assert.equal(ctx.server.calls.filter((c) => c.method === "POST" && c.url.includes("/api/folder/add")).length, 0,
        "Escape added the folder");
    },
  },
};

/** Tab from the page behind comes in; Shift+Tab on the first goes round to the last, and Tab on the last to the first. */
function assertTabGoesRound(ctx, dialog, label) {
  const { window, document } = ctx;
  document.activeElement.blur();
  press(window, document.body, "Tab");
  const first = document.activeElement;
  assert.ok(dialog.contains(first), `${label}: Tab from the page behind did not come into the dialog`);
  const back = press(window, first, "Tab", { shiftKey: true });
  const last = document.activeElement;
  assert.ok(back.defaultPrevented && dialog.contains(last) && last !== first,
    `${label}: Shift+Tab on the first control did not go round to the last`);
  assert.ok(press(window, last, "Tab").defaultPrevented, `${label}: Tab on the last control left the dialog`);
  assert.equal(document.activeElement, first, `${label}: Tab on the last control did not go round to the first`);
}

/** Thrown in a listener: jsdom reports it on the window, where a test would not otherwise see it. */
function catchErrors(window) {
  const errors = [];
  window.addEventListener("error", (event) => errors.push(event.error || event.message));
  return errors;
}

async function openTagEditor(ctx) {
  ctx.document.getElementById("btn-gear").focus();
  click(ctx.window, ctx.document.getElementById("btn-gear"));
  click(ctx.window, ctx.document.querySelector('#gear-menu [data-action="tag-editor"]'));
  await flush(ctx.window, 4);
  return ctx.document.getElementById("taxonomy-modal");
}

/** The delete question over the tag editor, asked from the bin of the row "Activity" (used by a photo). */
async function askToDelete(ctx) {
  const bin = [...ctx.document.querySelectorAll('#taxonomy-modal li[data-id="2"] > .taxonomy-node-content button')]
    .find((b) => b.textContent.includes("\u{1F5D1}"));
  bin.focus();
  click(ctx.window, bin);
  await flush(ctx.window, 4);
  const question = ctx.document.querySelector(".tag-editor-conflict.active");
  assert.ok(question, "the delete question did not open");
  return { bin, question };
}

describe("TagPup: every dialog keeps the keyboard -- Escape cancels, Tab goes round, the focus goes back", () => {
  for (const [label, how] of Object.entries(TAGPUP_DIALOGS)) {
    test(label, async (t) => {
      const ctx = await tagpupOnSecondPhoto(t);
      const { window, document } = ctx;
      const errors = catchErrors(window);
      const opener = how.opener(ctx);
      opener.focus();
      assert.equal(document.activeElement, opener);
      await how.open(ctx);
      await flush(window, 6);
      const dialog = how.dialog(ctx);
      assert.ok(dialog && openDialogs(document).includes(dialog), `${label} did not open`);
      assert.ok(dialog.contains(document.activeElement), `${label}: the focus was not put in it`);

      assertTabGoesRound(ctx, dialog, label);

      const escape = press(window, document.activeElement, "Escape");
      await flush(window, 6);
      assert.ok(escape.defaultPrevented, `${label}: Escape went on to the page`);
      assert.deepEqual(openDialogs(document), [], `${label}: Escape did not close it`);
      assert.equal(document.activeElement, opener, `${label}: the focus did not go back to what opened it`);
      if (how.cancelled) await how.cancelled(ctx);
      assert.deepEqual(errors, []);
    });
  }

  test("the owner's steps: Date Taken cancelled or saved, then the arrows step photos without a click", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    const { window, document } = ctx;
    dateButton(ctx).focus();
    click(window, dateButton(ctx));
    await flush(window, 2);
    assert.equal(document.activeElement, document.getElementById("input-date-taken"), "the date field is not focused");
    press(window, document.activeElement, "Escape");
    await flush(window, 4);
    assert.equal(document.activeElement, dateButton(ctx), "after Escape the focus is not on the Date Taken button");
    press(window, document.activeElement, "ArrowLeft");
    await flush(window, 6);
    assert.match(ctx.shown(), /a\.jpg$/, "ArrowLeft after Escape did not step the photo");

    // Saved by Enter in the date field: the focus is back on the button, and ArrowRight steps on.
    dateButton(ctx).focus();
    click(window, dateButton(ctx));
    await flush(window, 2);
    const input = document.getElementById("input-date-taken");
    input.value = "2018-03-04T09:08:07";
    press(window, input, "Enter");
    await flush(window, 8);
    assert.equal(ctx.saves(), 1, "the date was not saved");
    assert.equal(document.activeElement, dateButton(ctx), "after Save the focus is not on the Date Taken button");
    press(window, document.activeElement, "ArrowRight");
    await flush(window, 6);
    assert.match(ctx.shown(), /b\.jpg$/, "ArrowRight after Save did not step the photo");
  });

  test("a dialog over another: Escape closes the top one alone, held or pressed again", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    const { window, document } = ctx;
    let reachedThePage = 0;
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") reachedThePage += 1; });
    const editor = await openTagEditor(ctx);
    const { bin, question } = await askToDelete(ctx);
    // Confirm deletes a tag from its photos: the focus starts on Cancel.
    assert.ok(document.activeElement.classList.contains("btn-cancel") && question.contains(document.activeElement),
      "the delete question's focus is not on its Cancel");
    assertTabGoesRound(ctx, question, "delete question");

    // Escape held: the first press closes the question, its repeats close nothing and reach nothing.
    press(window, document.activeElement, "Escape");
    for (let i = 0; i < 3; i++) {
      assert.ok(press(window, document.activeElement, "Escape", { repeat: true }).defaultPrevented);
    }
    await flush(window, 4);
    assert.ok(!question.classList.contains("active"), "Escape did not close the delete question");
    assert.ok(editor.classList.contains("active"), "a held Escape closed the editor under the question too");
    assert.equal(document.activeElement, bin, "the focus did not go back to the row's bin");
    assert.equal(reachedThePage, 0, "Escape reached the page behind");

    // Let go and pressed again: the editor closes, the focus back on the gear.
    window.dispatchEvent(new window.KeyboardEvent("keyup", { key: "Escape" }));
    press(window, document.activeElement, "Escape");
    await flush(window, 4);
    assert.ok(!editor.classList.contains("active"), "a second Escape did not close the editor");
    assert.equal(document.activeElement, document.getElementById("btn-gear"));
  });

  test("Enter held on the Date Taken button opens it and does not save from the date field on its repeat", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    const { window, document } = ctx;
    dateButton(ctx).focus();
    press(window, dateButton(ctx), "Enter");
    click(window, dateButton(ctx));             // the browser's click, after the keydown
    await flush(window, 2);
    const repeat = press(window, document.activeElement, "Enter", { repeat: true });
    await flush(window, 6);
    assert.ok(repeat.defaultPrevented);
    assert.equal(ctx.saves(), 0, "the held Enter saved the date it had just shown");
    assert.ok(document.getElementById("date-taken-modal").classList.contains("active"), "the held Enter closed it");
  });

  test("a rapid second open keeps the first opener", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    const { window, document } = ctx;
    dateButton(ctx).focus();
    click(window, dateButton(ctx));
    await flush(window, 1);
    click(window, dateButton(ctx));
    await flush(window, 2);
    press(window, document.activeElement, "Escape");
    await flush(window, 2);
    assert.ok(!document.getElementById("date-taken-modal").classList.contains("active"), "Escape did not close it");
    assert.equal(document.activeElement, dateButton(ctx), "the second open took the date field for the opener");

    // The gear's Tag editor activated twice: the second open finds the focus in the editor's search.
    const editor = await openTagEditor(ctx);
    pageExports(window, "web/common/tag-editor.js").openTagEditor();
    await flush(window, 2);
    press(window, document.activeElement, "Escape");
    await flush(window, 2);
    assert.ok(!editor.classList.contains("active"), "Escape did not close the editor");
    assert.equal(document.activeElement, document.getElementById("btn-gear"), "the second open took the search for the opener");
  });

  test("an opener gone when the dialog closes: the focus goes to the dialog beneath, or nowhere, and nothing throws", async (t) => {
    const ctx = await tagpupOnSecondPhoto(t);
    const { window, document } = ctx;
    const errors = catchErrors(window);
    const choose = pageExports(window, "web/common/person-choice.js").choosePerson;
    const asked = () => document.querySelector(".person-choice");

    // Over nothing: a button that is gone by the time the question closes.
    const gone = document.createElement("button");
    document.body.append(gone);
    gone.focus();
    const answer = choose(PEOPLE);
    gone.remove();
    press(window, document.activeElement, "Escape");
    assert.equal(await answer, null);
    assert.equal(asked(), null, "Escape did not close the question");
    assert.equal(document.activeElement, document.body);

    // Over the tag editor: its row's button gone, the focus goes into the editor.
    const editor = await openTagEditor(ctx);
    const row = document.createElement("button");
    editor.querySelector(".tag-editor-body, .taxonomy-tree, div").append(row);
    row.focus();
    const second = choose(PEOPLE);
    row.remove();
    press(window, document.activeElement, "Escape");
    assert.equal(await second, null);
    assert.ok(editor.classList.contains("active"), "Escape on the question closed the editor too");
    assert.ok(editor.contains(document.activeElement), "the focus did not go into the editor beneath");
    assert.deepEqual(errors, []);
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
