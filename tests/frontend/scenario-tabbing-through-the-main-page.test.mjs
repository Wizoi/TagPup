/**
 * Somebody works TagPup's main page by the keyboard alone: Tab goes over the page's controls in the order they read,
 * reaches every control the mouse does, and nothing it cannot be seen on (the owner, 2026-11-15: "check the main screen
 * for the tab order over the buttons"). The order is computed as a browser does, from the page's own stylesheets
 * (tests/frontend/tab-order.mjs).
 *
 * How it failed: a closed dialog was only see-through (opacity 0), so after the page's last control Tab walked through
 * the Date Taken dialog, Add folder's two buttons and the tag editor's four controls unseen; every file of the Organize
 * list was a stop, a folder of 30 photos 30 Tabs between Refresh and the photo; the Folder View row, the file path, the
 * picture, the tag pills (a click is the only way to take a tag off), a keyword's suggestion and a face card were the
 * mouse's alone; the folder box and the Date Taken field took the focus with no sign of it. How it must not fail now:
 * the visibility that keeps a closed dialog out of the order must not keep an opened one from taking the focus, and
 * Escape still gives it back. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click, flush, photoRecord, CLIP_WRITES } from "./harness.mjs";
import {
  withStylesheets, recordClickTargets, tabOrder, tabStop, nameOf, invisibleStops, mouseOnly,
} from "./tab-order.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2020";
const CLIP = photoRecord({ id: 99, filename: "CLIP_0001.mp4", media: "video", writes: CLIP_WRITES });
const photos = (n) => Array.from({ length: n }, (_, i) => photoRecord({
  id: i + 1, filename: `IMG_${String(i + 1).padStart(4, "0")}.jpg`, tags: ["Places/Harbour"],
}));

/** Shown with a click, no tabindex, on purpose: the reason each is not a stop of its own. */
const DELEGATED = {
  // The grid is ONE stop: the arrows move over its cards; a card's file name is renamed in place by a click, and
  // a stop for each would undo the one stop (the brief: no new thumbnail stops).
  "span.thumbnail-filename": "renamed in place from the grid, which is one stop",
};

function server(list, { faces = [], suggestions = {} } = {}) {
  return new FakeServer()
    .on("/api/folder/scan", list.map((p) => ({ ...p })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces, total: faces.length, unmatched: faces.filter((f) => !f.name).length })
    .on("/api/folder/suggest-status", { status: "completed", suggestions });
}

async function page(t, list = [], options = {}) {
  const ctx = await loadApp("tagpup", {
    server: server(list, options), t,
    before: (window) => { withStylesheets("tagpup")(window); recordClickTargets(window); },
  });
  return ctx;
}

const settle = (window, ms = 60) => new Promise((r) => window.setTimeout(r, ms));
const names = (ctx) => tabOrder(ctx.document).map(nameOf);
const key = (ctx, target, name) => {
  const event = new ctx.window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
};
const fileStops = (ctx) => tabOrder(ctx.document).filter((el) => el.classList.contains("photo-item-file"));

async function openPhoto(ctx, index = 0) {
  click(ctx.window, ctx.document.querySelectorAll(".photo-item-file")[index]);
  await settle(ctx.window);
}

function noHiddenOrMouseOnlyStops(ctx) {
  assert.deepEqual(invisibleStops(ctx.document).map(nameOf), [], "Tab reaches nothing nobody can see");
  const left = mouseOnly(ctx.window).map(nameOf).filter((name) => !(name in DELEGATED));
  assert.deepEqual(left, [], "every shown control a click acts on is reached by Tab");
  const positive = [...ctx.document.querySelectorAll("[tabindex]")].filter((el) => el.tabIndex > 0).map(nameOf);
  assert.deepEqual(positive, [], "no tabindex above 0: the order is the page's");
}

describe("the main page by the keyboard", () => {
  test("before any folder is open, Tab reaches no photo control and no closed dialog", async (t) => {
    const ctx = await page(t);
    const stops = tabOrder(ctx.document);
    assert.ok(stops.length > 3, `the page has its header's stops: ${names(ctx)}`);
    const inside = stops.filter((el) => el.closest("#panel-content, .modal-overlay, .tag-editor")).map(nameOf);
    assert.deepEqual(inside, [], "nothing of the photo panel, the Date Taken and Add folder dialogs or the tag editor");
    noHiddenOrMouseOnlyStops(ctx);
  });

  test("a folder of 30 photos costs the list one Tab, the open photo's row, whichever photo is open", async (t) => {
    const ctx = await page(t, photos(30));
    await openFolder(ctx, FOLDER);
    await settle(ctx.window);
    assert.equal(ctx.document.querySelectorAll(".photo-item-file").length, 30);
    assert.deepEqual(fileStops(ctx).map(nameOf), [], "the grid shows: it is the stop, not a row");
    assert.ok(names(ctx).includes("#folder-view-header"), "the Folder View row is reachable");
    noHiddenOrMouseOnlyStops(ctx);

    await openPhoto(ctx, 4);
    const rows = fileStops(ctx);
    assert.equal(rows.length, 1, "one stop for the list, not 30");
    assert.equal(rows[0].dataset.path, `${FOLDER}\\IMG_0005.jpg`);
    const order = names(ctx);
    const after = order.slice(order.indexOf("#btn-refresh-list") + 1, order.indexOf("#btn-refresh-list") + 3);
    assert.deepEqual(after, ["#folder-view-header", "li.photo-item"], "Refresh, the way back to the grid, the photo");

    // The arrow steps to the next photo, and the Tab goes with it.
    rows[0].focus();
    key(ctx, rows[0], "ArrowDown");
    await settle(ctx.window);
    assert.deepEqual(fileStops(ctx).map((el) => el.dataset.path), [`${FOLDER}\\IMG_0006.jpg`]);
    // The focus went with it: a Tab from it goes on past the list, not onto the open row a second time (#3047).
    assert.equal(ctx.document.activeElement, fileStops(ctx)[0], "the focus stayed on the row the arrow left");
    noHiddenOrMouseOnlyStops(ctx);
  });

  test("Enter on the Folder View row opens the grid, as a click does", async (t) => {
    const ctx = await page(t, photos(5));
    await openFolder(ctx, FOLDER);
    await openPhoto(ctx, 2);
    const header = ctx.document.getElementById("folder-view-header");
    assert.ok(tabStop(header), "a stop while a photo is open: the way back to the grid");
    assert.ok(ctx.document.getElementById("folder-view-content").classList.contains("hidden"));
    header.focus();
    key(ctx, header, "Enter");
    await settle(ctx.window);
    assert.ok(!ctx.document.getElementById("folder-view-content").classList.contains("hidden"), "the grid shows");
    assert.ok(ctx.document.getElementById("panel-content").classList.contains("hidden"), "the photo is left");
  });

  test("a photo open: Date Taken, its title, people and keywords in reading order; pills, suggestions, faces and picture reached", async (t) => {
    const list = photos(3);
    const ctx = await page(t, list, {
      faces: [{ id: 1, box: [0, 0, 9, 9], area: 81, name: null, suggestion: "Ada Quill", similarity: 0.8, excluded: false }],
      suggestions: { [list[0].path]: { people: [{ name: "Ada Quill", score: 0.9 }], tags: [{ tag: "Cross Country", score: 0.9 }] } },
    });
    await openFolder(ctx, FOLDER);
    await openPhoto(ctx, 0);
    const order = names(ctx);
    const at = order.indexOf("#btn-edit-date-taken");
    assert.ok(at > 0, `the Date Taken button is a stop: ${order}`);
    assert.deepEqual(order.slice(at + 1, at + 5), ["#input-photo-title", "#btn-save-title", "#btn-edit-people", "#btn-edit-tags"]);
    for (const name of ["#detail-folder", "#detail-file-name", "#btn-copy-path", "#main-image", "span.tag-pill", "span.suggestion-chip", "div.face-card"]) {
      assert.ok(order.includes(name), `${name} is a stop: ${order}`);
    }
    assert.equal(order.filter((name) => name === "span.suggestion-chip").length, 2, "the person's chip and the keyword's");
    noHiddenOrMouseOnlyStops(ctx);

    // The path's links are buttons (Enter and Space are the browser's); on each of the others they are its click. The
    // click is counted, not run: what a pill or a chip writes is other tests'.
    for (const id of ["detail-folder", "detail-file-name", "btn-copy-path"]) assert.equal(ctx.document.getElementById(id).tagName, "BUTTON");
    for (const selector of [".tag-pill", "#suggested-tags-container .suggestion-chip", "#suggested-people-container .suggestion-chip", ".face-card"]) {
      const el = ctx.document.querySelector(selector);
      assert.ok(el, selector);
      let clicks = 0;
      el.click = () => { clicks += 1; };
      el.focus();
      assert.ok(key(ctx, el, "Enter").defaultPrevented);
      assert.equal(clicks, 1, `Enter on ${selector} clicks it`);
      key(ctx, el, " ");
      assert.equal(clicks, 2, `Space on ${selector} clicks it`);
    }
  });

  test("Enter on the focused picture opens the zoom, and Escape closes it", async (t) => {
    const ctx = await page(t, photos(2));
    await openFolder(ctx, FOLDER);
    await openPhoto(ctx, 0);
    const image = ctx.document.getElementById("main-image");
    image.focus();
    key(ctx, image, "Enter");
    await flush(ctx.window);
    assert.ok(!ctx.document.getElementById("image-zoom").classList.contains("hidden"), "the zoom is open");
    key(ctx, ctx.document.activeElement || ctx.document.body, "Escape");
    await flush(ctx.window);
    assert.ok(ctx.document.getElementById("image-zoom").classList.contains("hidden"), "Escape closes it");
  });

  test("a clip open: its player is a stop and the hidden picture is not", async (t) => {
    const ctx = await page(t, [...photos(2), CLIP]);
    await openFolder(ctx, FOLDER);
    await openPhoto(ctx, 2);
    const order = names(ctx);
    assert.ok(order.includes("#main-video"), `the player is a stop: ${order}`);
    assert.ok(!order.includes("#main-image"));
    noHiddenOrMouseOnlyStops(ctx);
  });

  test("a dialog opened takes the focus inside it, Tab stays in it, and Escape gives the focus back", async (t) => {
    const ctx = await page(t, photos(2));
    await openFolder(ctx, FOLDER);
    await openPhoto(ctx, 0);
    const opener = ctx.document.getElementById("btn-edit-date-taken");
    opener.focus();
    click(ctx.window, opener);
    await settle(ctx.window);
    const modal = ctx.document.getElementById("date-taken-modal");
    assert.ok(modal.classList.contains("active"));
    const focused = ctx.document.activeElement;
    assert.ok(modal.contains(focused), `the focus is in the dialog: ${nameOf(focused)}`);
    assert.ok(tabStop(focused), "and on a control shown (visibility visible once open)");
    assert.deepEqual(tabOrder(ctx.document).filter((el) => modal.contains(el)).map(nameOf),
      ["#btn-close-date-modal", "#input-date-taken", "#btn-cancel-date-modal", "#btn-save-date-modal"]);
    key(ctx, focused, "Escape");
    await settle(ctx.window);
    assert.ok(!modal.classList.contains("active"));
    assert.equal(ctx.document.activeElement, opener, "the focus is back on the button that opened it");
    assert.deepEqual(tabOrder(ctx.document).filter((el) => modal.contains(el)).map(nameOf), [], "closed, out of the order");

    // The tag editor, built once and kept in the page: opened, its search has the focus; closed, it is out of the order.
    const gear = ctx.document.getElementById("btn-gear");
    gear.focus();
    click(ctx.window, gear);
    click(ctx.window, ctx.document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(ctx.window, 4);
    const editor = ctx.document.getElementById("taxonomy-modal");
    assert.ok(editor.contains(ctx.document.activeElement) && tabStop(ctx.document.activeElement), "the editor holds the focus");
    key(ctx, ctx.document.activeElement, "Escape");
    await settle(ctx.window);
    assert.deepEqual(tabOrder(ctx.document).filter((el) => editor.contains(el)).map(nameOf), []);
  });
});
