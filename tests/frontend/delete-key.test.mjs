/**
 * The Delete key presses the red Delete button that is showing, and is held to what a click is.
 *
 * It goes through the page's one document key handler (navigation.js), so the question the button asks, the busy rule
 * and Just look are the button's own. It does not act in a field (there it edits text), with a dialog open, with a
 * modifier, with the button hidden or disabled, or as a held key repeating.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

function press(window, target, key, mods = {}) {
  const event = new window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...mods });
  target.dispatchEvent(event);
  return event;
}

/** TagPup on the second of two photos; `release` holds the deletes sent and not yet answered. */
async function onSecondPhoto(t, { answer = true } = {}) {
  const release = [];
  const server = new FakeServer()
    .on("/api/apps", { this: "tagpup", apps: {} })
    .on("/api/taxonomy/tree", [{ id: 2, tag: "Activity", name: "Activity", parent_id: null, has_face: 0, hidden_from_autocomplete: 0 }])
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/databases", { databases: ["photo_index"] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", ["a", "b", "c"].map((n) => photoRecord({ filename: `${n}.jpg` })))
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/delete", () => new Promise((resolve) => release.push(() => resolve({ success: true }))));
  const ctx = await loadApp("tagpup", { t, server });
  const asked = [];
  ctx.window.alert = () => {};
  ctx.window.confirm = (text) => { asked.push(text); return answer; };
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[1]);
  await flush(ctx.window, 6);
  ctx.asked = asked;
  ctx.release = release;
  ctx.sent = () => server.calls.filter((c) => c.url.includes("/api/photo/delete")).length;
  return ctx;
}

describe("the Delete key where the red Delete button shows", () => {
  test("asks what the button asks, and deletes only on OK", async (t) => {
    const ctx = await onSecondPhoto(t, { answer: false });
    const event = press(ctx.window, ctx.document.body, "Delete");
    await flush(ctx.window, 6);
    assert.equal(ctx.asked.length, 1, "the key did not ask the button's question");
    assert.match(ctx.asked[0], /Are you sure you want to delete "b\.jpg"/);
    assert.equal(ctx.sent(), 0, "Delete deleted after Cancel");
    assert.ok(event.defaultPrevented);
    ctx.window.confirm = (text) => { ctx.asked.push(text); return true; };
    press(ctx.window, ctx.document.body, "Delete");
    await flush(ctx.window, 6);
    assert.equal(ctx.sent(), 1, "Delete did not send after OK");
  });

  test("rapid double press and a held key delete once", async (t) => {
    const ctx = await onSecondPhoto(t);
    press(ctx.window, ctx.document.body, "Delete");
    press(ctx.window, ctx.document.body, "Delete");
    await flush(ctx.window, 6);
    for (let i = 0; i < 5; i++) press(ctx.window, ctx.document.body, "Delete", { repeat: true });
    await flush(ctx.window, 6);
    assert.equal(ctx.asked.length, 1, "a second press asked again while the first delete was written");
    assert.equal(ctx.sent(), 1, "more than one delete was sent");
  });

  test("a held key does not start a delete", async (t) => {
    const ctx = await onSecondPhoto(t);
    press(ctx.window, ctx.document.body, "Delete", { repeat: true });
    await flush(ctx.window, 4);
    assert.equal(ctx.asked.length, 0);
  });

  test("in a text field it edits text and deletes nothing", async (t) => {
    const ctx = await onSecondPhoto(t);
    for (const id of ["input-photo-title", "input-add-tag", "input-add-person", "photo-search"]) {
      const box = ctx.document.getElementById(id);
      if (!box) continue;
      box.focus();
      press(ctx.window, box, "Delete");
    }
    await flush(ctx.window, 6);
    assert.equal(ctx.asked.length, 0, "Delete asked while a field had the focus");
    assert.equal(ctx.sent(), 0);
  });

  test("with a dialog open, a modifier, or the button hidden or disabled, it does nothing", async (t) => {
    const ctx = await onSecondPhoto(t);
    const { window, document } = ctx;
    for (const mods of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }]) press(window, document.body, "Delete", mods);
    const button = document.getElementById("btn-delete-photo");
    button.disabled = true;
    press(window, document.body, "Delete");
    button.disabled = false;
    button.closest(".panel-content").classList.add("hidden");
    press(window, document.body, "Delete");
    button.closest(".panel-content").classList.remove("hidden");
    click(window, document.getElementById("btn-gear"));
    click(window, document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(window, 4);
    assert.ok(document.getElementById("taxonomy-modal").classList.contains("active"));
    press(window, document.body, "Delete");
    press(window, document.getElementById("taxonomy-modal").querySelector("button"), "Delete");
    await flush(window, 6);
    assert.equal(ctx.asked.length, 0, "Delete asked where the button could not be pressed");
    assert.equal(ctx.sent(), 0);
  });

  test("with focus in a menu that owns its keys it deletes nothing", async (t) => {
    const ctx = await onSecondPhoto(t);
    const button = ctx.document.querySelector("#sidebar-switch button");
    button.focus();
    assert.equal(ctx.document.activeElement, button, "the menu button took no focus");
    const event = press(ctx.window, button, "Delete");
    await flush(ctx.window, 6);
    assert.equal(ctx.asked.length, 0, "Delete asked while a menu that owns its keys had the focus");
    assert.equal(ctx.sent(), 0);
    assert.equal(event.defaultPrevented, false);
  });
});
