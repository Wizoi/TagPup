/**
 * Tagging a shoot from the keyboard: type or pick a tag, Enter, arrow on to the next photo, without the mouse.
 *
 * The owner's three asks (docs/findings.md #2834, #2835, #2836): a pick from the box's list saves at once (a second Enter
 * was needed); Left and Right step between photos from the tag and person boxes when the caret has nowhere to go; and
 * what is typed while a save is out goes to the next tag, never onto the end of the one being saved.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, flush, pageExports, peopleRecordsOf, PEOPLE_ROUTE, SAVE, saved, saveCalls, notSaved } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\p\\a.jpg", filename: "a.jpg", tags: [], people: [], captions: [], title: "", mtime: 1600000000, size: 10 },
  { path: "D:\\p\\b.jpg", filename: "b.jpg", tags: [], people: [], captions: [], title: "", mtime: 1600000000, size: 10 },
  { path: "D:\\p\\c.jpg", filename: "c.jpg", tags: [], people: [], captions: [], title: "", mtime: 1600000000, size: 10 },
];

const TREE = [
  { id: 1, name: "People", parent_id: null, has_face: 1, tag: "People" },
  { id: 2, name: "Rowan Thackeray", parent_id: 1, has_face: 1, tag: "People/Rowan Thackeray" },
  { id: 3, name: "Places", parent_id: null, has_face: 0, tag: "Places" },
  { id: 4, name: "Beach", parent_id: 3, has_face: 0, tag: "Places/Beach" },
];

/** A save the test answers when it chooses: `release(reply)`. Each save waits on the gate open when it was asked. */
function saves() {
  const gates = [];
  return {
    gates,
    reply: (url, body) => new Promise((resolve) => gates.push((reply) => resolve(reply(url, body)))),
    release: (reply = saved()) => gates.shift()(reply),
  };
}

function server(save) {
  return new FakeServer()
    .on("/api/folder/scan", PHOTOS.map((p) => ({ ...p, tags: [...p.tags] })))
    .on("/api/taxonomy/tree", TREE)
    .on("/api/taxonomy/create", { success: true })
    .on(PEOPLE_ROUTE, peopleRecordsOf(TREE))
    .on("/api/tags", ["Places/Beach", "People/Rowan Thackeray"])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on(SAVE, save ? save.reply : saved())
    .on("/api/folder/suggest-status", { status: "idle" });
}

async function opened(t, save) {
  const s = server(save);
  const ctx = await loadApp("tagpup", { server: s, t });
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:\\p");
  await flush(ctx.window, 4);
  press(ctx.document, "ArrowDown");   // a.jpg open
  return { ...ctx, server: s, tagBox: ctx.document.getElementById("input-add-tag"),
    personBox: ctx.document.getElementById("input-add-person") };
}

function press(document, key, target, extra = {}) {
  const ev = new document.defaultView.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...extra });
  (target || document).dispatchEvent(ev);
  return ev.defaultPrevented;
}

/** A pick from the box's list, as Chrome reports it: the value replaced, then an `input` event with no inputType. */
function pick(window, box, value) {
  box.focus();
  box.value = value;
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
}

/** Keys typed: each one an InputEvent of `insertText`, onto the end of what the box holds. */
function type(window, box, text) {
  box.focus();
  for (const ch of text) {
    box.value += ch;
    box.dispatchEvent(new window.InputEvent("input", { bubbles: true, inputType: "insertText", data: ch }));
  }
}

function activePath(document) {
  const el = document.querySelector(".photo-item-file.active");
  return el && el.getAttribute("data-path");
}

function saveBodies(s) {
  return saveCalls(s).map((c) => c.body);
}

afterEach(() => closeAllApps());

describe("a pick from the list saves it (#2834)", () => {
  test("picking a tag from the list writes it, with no second Enter", async (t) => {
    const { window, server: s, tagBox } = await opened(t);
    pick(window, tagBox, "Places/Beach");
    await flush(window, 6);
    const bodies = saveBodies(s);
    assert.equal(bodies.length, 1, "the pick filled the box and wrote nothing");
    assert.deepEqual(bodies[0].tags, ["Places/Beach"]);
    assert.equal(bodies[0].path, "D:\\p\\a.jpg");
    assert.equal(tagBox.value, "");
  });

  test("picking a person from the list writes them", async (t) => {
    const { window, server: s, personBox } = await opened(t);
    pick(window, personBox, "People/Rowan Thackeray");
    await flush(window, 6);
    const bodies = saveBodies(s);
    assert.equal(bodies.length, 1, "the pick filled the box and wrote nothing");
    assert.deepEqual(bodies[0].tags, ["People/Rowan Thackeray"]);
  });

  test("typing a tag the list holds, key by key, writes nothing until Enter", async (t) => {
    const { window, document, server: s, tagBox } = await opened(t);
    type(window, tagBox, "Places/Beach");
    await flush(window, 4);
    assert.equal(saveBodies(s).length, 0, "a typed text was taken for a pick");
    press(document, "Enter", tagBox);
    await flush(window, 6);
    assert.equal(saveBodies(s).length, 1);
  });
});

describe("Left and Right step between photos from the tag and person boxes (#2835)", () => {
  test("in an empty box, Right steps to the next photo and Left back, and the box keeps the keys after", async (t) => {
    const { document, tagBox, personBox } = await opened(t);
    tagBox.focus();
    assert.equal(press(document, "ArrowRight", tagBox), true);
    assert.equal(activePath(document), "D:\\p\\b.jpg");
    assert.equal(press(document, "ArrowRight", tagBox), true);
    assert.equal(activePath(document), "D:\\p\\c.jpg");
    personBox.focus();
    press(document, "ArrowLeft", personBox);
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });

  test("with text in the box the caret moves first: Left steps only from the start, Right only from the end", async (t) => {
    const { window, document, tagBox } = await opened(t);
    type(window, tagBox, "Sun");
    tagBox.setSelectionRange(1, 1);
    assert.equal(press(document, "ArrowLeft", tagBox), false, "the caret's key was taken");
    assert.equal(press(document, "ArrowRight", tagBox), false, "the caret's key was taken");
    tagBox.setSelectionRange(0, 3);
    assert.equal(press(document, "ArrowRight", tagBox), false, "a selection is the field's to collapse");
    tagBox.setSelectionRange(0, 0);
    assert.equal(press(document, "ArrowLeft", tagBox, { shiftKey: true }), false, "Shift selects; it does not step");
    assert.equal(activePath(document), "D:\\p\\a.jpg");
    assert.equal(tagBox.value, "Sun");
  });

  test("Right at the end of unsaved text asks before leaving it, as every way off the photo does", async (t) => {
    const { window, document, tagBox } = await opened(t);
    type(window, tagBox, "Sun");
    press(document, "ArrowRight", tagBox);
    assert.ok(document.querySelector(".unsaved-edits-modal"), "it left the text without asking");
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });

  test("Up and Down stay the box's: they walk its list", async (t) => {
    const { document, tagBox } = await opened(t);
    tagBox.focus();
    assert.equal(press(document, "ArrowDown", tagBox), false);
    assert.equal(press(document, "ArrowUp", tagBox), false);
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });
});

describe("what is typed while a save is out goes to the next tag (#2836)", () => {
  test("keys typed after Enter are not added to the saved tag, and stay for the next one", async (t) => {
    const save = saves();
    const { window, document, server: s, tagBox } = await opened(t, save);
    type(window, tagBox, "Places/Beach");
    press(document, "Enter", tagBox);
    await flush(window, 4);
    type(window, tagBox, "Sun");          // the save is still out
    save.release();
    await flush(window, 6);
    assert.deepEqual(saveBodies(s).map((b) => b.tags), [["Places/Beach"]]);
    assert.equal(tagBox.value, "Sun", "the keys typed during the save were added to the saved tag's text");
  });

  test("a pick, then keys at once: the pick is saved alone and the keys wait in the box", async (t) => {
    const save = saves();
    const { window, server: s, tagBox } = await opened(t, save);
    pick(window, tagBox, "Places/Beach");
    type(window, tagBox, "x");
    await flush(window, 4);
    save.release();
    await flush(window, 6);
    assert.deepEqual(saveBodies(s).map((b) => b.tags), [["Places/Beach"]]);
    assert.equal(tagBox.value, "x");
  });

  test("Enter pressed again and again while the save is out writes the tag once", async (t) => {
    const save = saves();
    const { window, document, server: s, tagBox } = await opened(t, save);
    const { state } = pageExports(window, "web/tagpup/state.js");
    type(window, tagBox, "Places/Beach");
    for (let i = 0; i < 4; i++) press(document, "Enter", tagBox);
    await flush(window, 4);
    save.release();
    await flush(window, 6);
    assert.equal(saveBodies(s).length, 1);
    assert.equal(state.writeQueue.entries.length, 1, "each empty Enter queued a save of its own");
  });

  test("a save that fails gives the text back to the box, to be tried again", async (t) => {
    const save = saves();
    const { window, document, tagBox } = await opened(t, save);
    type(window, tagBox, "Places/Beach");
    press(document, "Enter", tagBox);
    await flush(window, 4);
    save.release(notSaved("the share stopped answering"));
    await flush(window, 6);
    assert.equal(tagBox.value, "Places/Beach");
  });

  test("Right during the save waits for it: the tag goes to the photo it was typed on and the box is empty on the next", async (t) => {
    const save = saves();
    const { window, document, server: s, tagBox } = await opened(t, save);
    type(window, tagBox, "Places/Beach");
    press(document, "Enter", tagBox);
    await flush(window, 4);
    press(document, "ArrowRight", tagBox);
    assert.equal(activePath(document), "D:\\p\\a.jpg", "it left before the save was written");
    save.release();
    await flush(window, 8);
    assert.equal(saveBodies(s)[0].path, "D:\\p\\a.jpg");
    assert.equal(activePath(document), "D:\\p\\b.jpg");
    assert.equal(tagBox.value, "");
  });
});
