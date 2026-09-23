/**
 * Nothing typed into Image Details is lost by moving on.
 *
 * The report: "adding a tag or keyword then moving to the next image will not trigger
 * a save". Typed text used to be committed when its field lost focus, but that
 * commit was asynchronous -- it resolved the tag first, and a pathed tag or a new
 * person meant a round trip to the server. Clicking the next photo blurs the field
 * on mousedown and navigates on click, long before that round trip returns; by then
 * selectPhoto had cleared the field and the save found nothing to save. A title was
 * never committed on leaving at all, and a brand-new keyword waited for Enter.
 *
 * Now the panel knows when it holds something the photo does not. The header has a
 * Save button that says so, Ctrl+S saves, and every way off the photo asks first.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\p\\a.jpg", filename: "a.jpg", tags: ["People/Tamsin Ashgrove"], people: ["Tamsin Ashgrove"], captions: [], title: "" },
  { path: "D:\\p\\b.jpg", filename: "b.jpg", tags: [], people: [], captions: [], title: "" },
  { path: "D:\\p\\c.jpg", filename: "c.jpg", tags: [], people: [], captions: [], title: "" },
];

function server({ save = { success: true }, people = [], tags = [] } = {}) {
  return new FakeServer()
    .on("/api/folder/scan", PHOTOS.map((p) => ({ ...p, tags: [...p.tags], people: [...p.people] })))
    .on("/api/taxonomy/tree", [])
    .on("/api/taxonomy/create", { success: true })
    .on("/api/people", people)
    .on("/api/tags", tags)
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", save)
    .on("/api/folder/suggest-status", { status: "idle" });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

/** Open the folder and stand on the first photo. */
async function onFirstPhoto(t, s = server()) {
  const { window, document } = await loadApp("tagpup", { server: s, t });
  window.alert = () => {};
  await openFolder({ document, window }, "D:\\p");
  await settle(window, 60);
  press(document, "ArrowDown");
  assert.equal(activePath(document), "D:\\p\\a.jpg");
  return { window, document, server: s };
}

function activePath(document) {
  const el = document.querySelector(".photo-item-file.active");
  return el && el.getAttribute("data-path");
}

function rows(document) {
  return [...document.querySelectorAll(".photo-item-file")];
}

function press(document, key, target, mods = {}) {
  const ev = new document.defaultView.KeyboardEvent("keydown", {
    key, bubbles: true, cancelable: true, ...mods,
  });
  (target || document.activeElement || document).dispatchEvent(ev);
  return ev;
}

/** Type into a field the way a person does: the value changes, `input` fires. */
function type(window, input, text) {
  input.focus();
  input.value = text;
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
}

function saveButton(document) {
  return document.getElementById("btn-save-details");
}

function prompt(document) {
  return document.querySelector(".unsaved-edits-modal.active");
}

function saves(s) {
  return s.calls.filter((c) => c.url.includes("save-metadata"));
}

function swipe(window, el, dx) {
  const down = new window.Event("pointerdown", { bubbles: true, cancelable: true });
  Object.assign(down, { pointerId: 1, pointerType: "touch", button: 0, clientX: 300, clientY: 200 });
  el.dispatchEvent(down);
  const up = new window.Event("pointerup", { bubbles: true, cancelable: true });
  Object.assign(up, { pointerId: 1, pointerType: "touch", button: 0, clientX: 300 + dx, clientY: 200 });
  el.dispatchEvent(up);
}

afterEach(() => closeAllApps());

describe("the reported bug", () => {
  test("a typed tag survives clicking the next photo", async (t) => {
    // In a browser, leaving the field (mousedown) and choosing the row (click)
    // happen long before the server answers the lookup the blur commit waited on.
    const { window, document, server: s } = await onFirstPhoto(t);
    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Places/Beach");
    tagInput.blur();
    click(window, rows(document)[1]);
    await settle(window, 60);

    const sent = saves(s).find((c) => c.body.path === "D:\\p\\a.jpg");
    const stillHere = activePath(document) === "D:\\p\\a.jpg" && tagInput.value === "Places/Beach";
    assert.ok(sent || stillHere, "the tag was neither saved nor kept -- it was dropped");
  });

  test("a typed title survives moving on", async (t) => {
    const { window, document, server: s } = await onFirstPhoto(t);
    const title = document.getElementById("input-photo-title");
    type(window, title, "Harbour at dusk");
    title.blur();
    press(document, "ArrowDown", document.body);
    await settle(window);

    const sent = saves(s).find((c) => c.body.title === "Harbour at dusk");
    const stillHere = activePath(document) === "D:\\p\\a.jpg" && title.value === "Harbour at dusk";
    assert.ok(sent || stillHere, "the title was neither saved nor kept -- it was dropped");
  });
});

describe("the Save button", () => {
  test("sits in the Image Details header and names its shortcut", async (t) => {
    const { document } = await onFirstPhoto(t);
    const btn = saveButton(document);
    assert.ok(btn, "no Save button");
    assert.ok(btn.closest(".details-section .card-header"), "not in the Image Details header");
    assert.equal(btn.title, "Save (Ctrl+S)");
  });

  test("is disabled until something differs, and again once it does not", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    const btn = saveButton(document);
    assert.equal(btn.disabled, true, "enabled with nothing to save");

    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Sunset");
    assert.equal(btn.disabled, false, "a typed tag did not enable it");
    type(window, tagInput, "");
    assert.equal(btn.disabled, true, "typing and deleting left it enabled");

    const title = document.getElementById("input-photo-title");
    type(window, title, "Harbour");
    assert.equal(btn.disabled, false, "a changed title did not enable it");
    type(window, title, "");
    assert.equal(btn.disabled, true, "restoring the title left it enabled");

    type(window, document.getElementById("input-add-person"), "Corin Vale");
    assert.equal(btn.disabled, false, "a typed person did not enable it");
  });

  test("saves title and tag together, then disables", async (t) => {
    const { window, document, server: s } = await onFirstPhoto(t);
    type(window, document.getElementById("input-photo-title"), "Harbour");
    type(window, document.getElementById("input-add-tag"), "Places/Beach");
    saveButton(document).click();
    await settle(window, 60);

    const sent = saves(s);
    assert.equal(sent.length, 1, "expected one write for the whole panel");
    assert.equal(sent[0].body.path, "D:\\p\\a.jpg");
    assert.equal(sent[0].body.title, "Harbour");
    assert.deepEqual(sent[0].body.tags, ["People/Tamsin Ashgrove", "Places/Beach"]);
    assert.equal(document.getElementById("input-add-tag").value, "");
    assert.equal(saveButton(document).disabled, true);
  });

  test("saves, and moves on, on a library whose people and tag lookups failed", async (t) => {
    // Found in a real browser against a fresh library: /api/people and /api/tags
    // answered {error: ...}, those objects became the lists, and isPersonTag and
    // updateTagsDatalist threw -- first stopping the save, then, once it was
    // written, reporting it as "Not saved" and refusing to leave the photo.
    const s = server({
      people: { error: "no such table: faces" },
      tags: { error: "no such table: photos" },
    });
    const { window, document, server: srv } = await onFirstPhoto(t, s);
    type(window, document.getElementById("input-add-tag"), "Places/Beach");
    document.getElementById("input-add-tag").blur();
    press(document, "ArrowDown", document.body);
    press(document, "Enter", document.activeElement);
    await settle(window, 80);

    assert.equal(saves(srv).length, 1, "the save never went out");
    assert.equal(activePath(document), "D:\\p\\b.jpg", "a written save was reported as failed");
    assert.doesNotMatch(document.getElementById("status-text").textContent, /Not saved/);
  });

  test("a tag the photo already has is not written again", async (t) => {
    const { window, document, server: s } = await onFirstPhoto(t);
    type(window, document.getElementById("input-add-person"), "People/Tamsin Ashgrove");
    saveButton(document).click();
    await settle(window, 60);

    assert.equal(saves(s).length, 0, "rewrote the photo to add what it already had");
    assert.equal(document.getElementById("input-add-person").value, "");
    assert.equal(saveButton(document).disabled, true);
  });
});

describe("Ctrl+S", () => {
  test("saves from inside the field being typed in", async (t) => {
    const { window, document, server: s } = await onFirstPhoto(t);
    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Places/Beach");
    const ev = press(document, "s", tagInput, { ctrlKey: true });
    await settle(window, 60);

    assert.equal(ev.defaultPrevented, true, "the browser's Save Page would open");
    assert.ok(saves(s)[0].body.tags.includes("Places/Beach"));
  });

  test("Cmd+S does the same", async (t) => {
    const { window, document, server: s } = await onFirstPhoto(t);
    type(window, document.getElementById("input-photo-title"), "Harbour");
    const ev = press(document, "s", document.body, { metaKey: true });
    await settle(window, 60);
    assert.equal(ev.defaultPrevented, true);
    assert.equal(saves(s)[0].body.title, "Harbour");
  });

  test("with nothing to save it writes nothing, and still keeps the browser out", async (t) => {
    const { window, document, server: s } = await onFirstPhoto(t);
    const ev = press(document, "S", document.body, { ctrlKey: true, shiftKey: true });
    await settle(window);
    assert.equal(ev.defaultPrevented, true);
    assert.equal(saves(s).length, 0);
  });
});

describe("every way off the photo asks first", () => {
  const routes = {
    "arrow key": (ctx) => press(ctx.document, "ArrowDown", ctx.document.body),
    "swipe": (ctx) => swipe(ctx.window, ctx.document.getElementById("main-image"), -150),
    "clicking another row": (ctx) => click(ctx.window, rows(ctx.document)[2]),
    "Enter on another row": (ctx) => press(ctx.document, "Enter", rows(ctx.document)[2]),
    "the folder view": (ctx) => click(ctx.window, ctx.document.getElementById("folder-view-header")),
    "refreshing the list": (ctx) => click(ctx.window, ctx.document.getElementById("btn-refresh-list")),
    "opening another folder": (ctx) => openFolder(ctx, "D:\\q", { settle: 1 }),
  };

  for (const [name, go] of Object.entries(routes)) {
    test(`${name}: prompts when there are unsaved edits`, async (t) => {
      const ctx = await onFirstPhoto(t);
      const tagInput = ctx.document.getElementById("input-add-tag");
      type(ctx.window, tagInput, "Sunset");
      tagInput.blur();
      await go(ctx);
      await settle(ctx.window);

      assert.ok(prompt(ctx.document), "moved on without asking");
      assert.match(prompt(ctx.document).textContent, /Save changes to a\.jpg\?/);
      assert.equal(activePath(ctx.document), "D:\\p\\a.jpg", "left before the answer");
      assert.equal(tagInput.value, "Sunset");
    });

    test(`${name}: does not prompt when there is nothing to save`, async (t) => {
      const ctx = await onFirstPhoto(t);
      await go(ctx);
      await settle(ctx.window);
      assert.equal(prompt(ctx.document), null);
    });
  }
});

describe("answering the prompt", () => {
  async function prompted(t, s) {
    const ctx = await onFirstPhoto(t, s);
    const tagInput = ctx.document.getElementById("input-add-tag");
    type(ctx.window, tagInput, "Places/Beach");
    tagInput.blur();
    press(ctx.document, "ArrowDown", ctx.document.body);
    await settle(ctx.window);
    assert.ok(prompt(ctx.document), "no prompt");
    return { ...ctx, tagInput };
  }

  test("Save is focused, so Enter saves and then moves on", async (t) => {
    const { window, document, server: s } = await prompted(t);
    const focused = document.activeElement;
    assert.equal(focused.dataset.choice, "save", "Save is not the focused button");

    press(document, "Enter", focused);
    await settle(window, 60);

    const sent = saves(s);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].body.path, "D:\\p\\a.jpg");
    assert.ok(sent[0].body.tags.includes("Places/Beach"));
    assert.equal(prompt(document), null);
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });

  test("Escape cancels: same photo, edits intact, nothing written", async (t) => {
    const { window, document, server: s, tagInput } = await prompted(t);
    press(document, "Escape", document.activeElement);
    await settle(window);

    assert.equal(prompt(document), null);
    assert.equal(activePath(document), "D:\\p\\a.jpg");
    assert.equal(tagInput.value, "Places/Beach");
    assert.equal(saves(s).length, 0);
    assert.equal(saveButton(document).disabled, false);
  });

  test("Discard moves on without writing, and the edit does not follow", async (t) => {
    const { window, document, server: s, tagInput } = await prompted(t);
    click(window, prompt(document).querySelector('[data-choice="discard"]'));
    await settle(window);

    assert.equal(saves(s).length, 0);
    assert.equal(activePath(document), "D:\\p\\b.jpg");
    assert.equal(tagInput.value, "");
    assert.equal(saveButton(document).disabled, true);
  });

  test("a failed save keeps you on the photo with the edit and the error", async (t) => {
    const s = server({ save: { success: false, error: "file is read-only" } });
    const { window, document, tagInput } = await prompted(t, s);
    press(document, "Enter", document.activeElement);
    await settle(window, 60);

    assert.equal(activePath(document), "D:\\p\\a.jpg", "moved on after a failed save");
    assert.equal(tagInput.value, "Places/Beach");
    assert.match(document.getElementById("status-text").textContent, /read-only/);
    assert.equal(saveButton(document).disabled, false);
  });

  test("arrow keys do not navigate underneath the prompt", async (t) => {
    const { window, document } = await prompted(t);
    press(document, "ArrowDown", document.activeElement);
    await settle(window);
    assert.equal(document.querySelectorAll(".unsaved-edits-modal").length, 1, "a second prompt");
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });
});

describe("leaving the page", () => {
  function unload(window) {
    const ev = new window.Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    return ev;
  }

  test("asks the browser to confirm when there are unsaved edits", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    type(window, document.getElementById("input-add-tag"), "Sunset");
    assert.equal(unload(window).defaultPrevented, true);
  });

  test("does not when there are none", async (t) => {
    const { window } = await onFirstPhoto(t);
    assert.equal(unload(window).defaultPrevented, false);
  });
});

describe("leaving a field is not saving", () => {
  test("blurring a typed tag writes nothing and leaves it waiting to be saved", async (t) => {
    // The blur commit raced navigation (see the top of this file), and with the
    // prompt in place it has nothing left to protect.
    const { window, document, server: s } = await onFirstPhoto(t);
    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Places/Beach");
    tagInput.blur();
    await settle(window, 60);
    assert.equal(saves(s).length, 0);
    assert.equal(saveButton(document).disabled, false);
  });

  test("Escape still abandons typed text", async (t) => {
    const { window, document } = await onFirstPhoto(t);
    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Mistake");
    press(document, "Escape", tagInput);
    assert.equal(tagInput.value, "");
    assert.equal(saveButton(document).disabled, true);
  });

  test("Enter still adds a tag straight away", async (t) => {
    const { window, document, server: s } = await onFirstPhoto(t);
    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Places/Beach");
    press(document, "Enter", tagInput);
    await settle(window, 60);
    assert.ok(saves(s)[0].body.tags.includes("Places/Beach"));
    assert.equal(saveButton(document).disabled, true);
  });
});
