/**
 * The selection follows its photos (found while moving the grid onto a window of cards, 9b-1).
 *
 * - A selected photo whose title is saved from its card may be renamed by the save (a caption
 *   is part of the file name once the photo has been through Smart Rename). The selection kept
 *   the old path, so the next bulk write named a file that was no longer there.
 * - "Extend selection to here" in the right-click menu, with nothing selected yet, handed the
 *   selection code no card and no earlier click: it threw, and selected nothing.
 * Neither needs a layout, so both run on the page as jsdom draws it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2020";
const PHOTOS = ["a.jpg", "b.jpg", "c.jpg"].map((filename) => photoRecord({ filename }));

async function load(t, save) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", save || { success: true })
    .on("/api/folder/scan", () => PHOTOS.map((p) => ({ ...p })));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.alert = () => {};
  await openFolder(ctx, FOLDER);
  ctx.card = (filename) => [...ctx.document.querySelectorAll(".thumbnail-card")]
    .find((c) => c.getAttribute("data-path").endsWith(`\\${filename}`));
  return ctx;
}

describe("the selection follows its photos", () => {
  test("a photo renamed by saving its title stays selected, under its new path", async (t) => {
    const renamed = `${FOLDER}\\Harbour - 01.jpg`;
    const ctx = await load(t, { success: true, new_path: renamed });
    click(ctx.window, ctx.card("a.jpg").querySelector(".thumbnail-checkbox"));
    click(ctx.window, ctx.card("c.jpg").querySelector(".thumbnail-checkbox"));
    ctx.card("a.jpg").querySelector(".thumbnail-filename").dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    const input = ctx.card("a.jpg").querySelector(".thumbnail-filename-input");
    input.value = "Harbour";
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 6);

    const card = ctx.card("Harbour - 01.jpg");
    assert.ok(card, "the card has the new path");
    assert.ok(card.classList.contains("selected"), "and is still marked selected");
    assert.ok(card.querySelector(".thumbnail-checkbox").checked);
    assert.match(ctx.document.getElementById("selected-thumbnails-count").textContent, /2/);
    // A bulk write now names the file as it is called.
    ctx.window.confirm = () => true;
    ctx.document.getElementById("bulk-add-tags-input").value = "Trips/Lighthouse";
    ctx.server.on("/api/photos/bulk-tags", { success: true });
    click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
    await flush(ctx.window, 8);
    const sent = ctx.server.lastBody("/api/photos/bulk-tags");
    assert.deepEqual([...sent.paths].sort(), [renamed, `${FOLDER}\\c.jpg`].sort());
  });

  test("Extend selection to here, with nothing selected, selects that photo", async (t) => {
    const ctx = await load(t);
    ctx.card("b.jpg").dispatchEvent(new ctx.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    ctx.document.querySelector('#grid-context-menu [data-action="select-range"]').click();
    assert.ok(ctx.card("b.jpg").classList.contains("selected"));
    assert.match(ctx.document.getElementById("selected-thumbnails-count").textContent, /1/);
  });
});
