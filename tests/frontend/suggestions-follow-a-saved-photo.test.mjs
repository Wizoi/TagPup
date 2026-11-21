/**
 * A photo's suggestions follow it through a save (finding #2415). The page holds them by the photo's path
 * (state.folderSuggestions); a save that renames the photo (its caption is part of its file name) moved the photo and
 * left its suggestions under the old name, so the box and the AI badge vanished until Refresh asked the server again
 * (the server keeps them by the photo's id and was right all along). A save that fails changes nothing.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, openFolder, closeAllApps, PHOTO_WRITES, SAVE, saved, notSaved } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2020";
const A = `${FOLDER}\\a.jpg`;
const B = `${FOLDER}\\b.jpg`;
const RENAMED = `${FOLDER}\\Harbour - 01.jpg`;
const record = (path) => ({ path, filename: path.split("\\").pop(), tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, mtime: 1, size: 1 });
const OFFER = { tags: [{ tag: "Harbour", score: 0.9 }], people: [], title: "" };

async function load(t, save) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "completed", suggestions: { [A]: OFFER, [B]: OFFER } })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on(SAVE, save)
    .on("/api/folder/scan", () => [record(A), record(B)]);
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.alert = () => {};
  await openFolder(ctx, FOLDER);
  await flush(ctx.window, 8);
  ctx.card = (filename) => [...ctx.document.querySelectorAll(".thumbnail-card")]
    .find((c) => c.getAttribute("data-path").endsWith(`\\${filename}`));
  return ctx;
}

const shown = (ctx) => !ctx.document.getElementById("suggestions-section").classList.contains("hidden");
const badge = (ctx, filename) => !![...ctx.document.querySelectorAll("li[data-path]")]
  .find((li) => li.getAttribute("data-path").endsWith(`\\${filename}`))?.querySelector(".photo-item-has-sugg");

async function saveCaption(ctx, text) {
  const input = ctx.document.getElementById("input-photo-title");
  input.value = text;
  input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
  ctx.document.getElementById("btn-save-details").click();
  await flush(ctx.window, 10);
}

describe("a saved photo keeps its suggestions", () => {
  test("a save that renames the open photo keeps its box, and the other photo's badge", async (t) => {
    const ctx = await load(t, saved({ new_path: RENAMED }));
    click(ctx.window, ctx.document.querySelector(`li[data-path="${A.replace(/\\/g, "\\\\")}"]`));
    await flush(ctx.window, 10);
    assert.ok(shown(ctx), "the box is shown before the save");
    assert.ok(badge(ctx, "b.jpg"), "b has its badge before the save");
    await saveCaption(ctx, "Harbour");
    assert.ok(ctx.card("Harbour - 01.jpg"), "the photo has its new name");
    assert.ok(shown(ctx), "the box is still shown under the new name");
    assert.ok(badge(ctx, "Harbour - 01.jpg"), "its row still has the AI badge");
    assert.ok(badge(ctx, "b.jpg"), "the other photo's badge is untouched");
  });

  test("a rename from the card's own title keeps the badge", async (t) => {
    const ctx = await load(t, saved({ new_path: RENAMED }));
    ctx.card("a.jpg").querySelector(".thumbnail-filename").dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    const input = ctx.card("a.jpg").querySelector(".thumbnail-filename-input");
    input.value = "Harbour";
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 8);
    assert.ok(badge(ctx, "Harbour - 01.jpg"), "the renamed row still has the AI badge");
  });

  test("Smart Rename keeps the renamed photo's suggestions, whole or stopped part-way", async (t) => {
    for (const stopped of [false, true]) {
      const ctx = await load(t, saved());
      const updated = [{ ...record(RENAMED) }, record(B)];
      // The server spells the old path as it likes: matched by key, not by exact text.
      const updated_paths = { [A.toLowerCase()]: RENAMED, [B]: B };
      if (stopped) ctx.server.on("/api/folder/rename-photos", { success: false, error: "stopped", updated_paths, updated_photos: updated }, { status: 500 });
      else ctx.server.on("/api/folder/rename-photos", { success: true, updated_paths, updated_photos: updated });
      ctx.window.confirm = () => true;
      click(ctx.window, ctx.card("a.jpg").querySelector(".thumbnail-checkbox"));
      ctx.document.getElementById("rename-grouping-input").value = "Harbour";
      const apply = ctx.document.getElementById("btn-apply-rename");
      apply.disabled = false;
      click(ctx.window, apply);
      await flush(ctx.window, 10);
      assert.ok(ctx.server.calls.some((c) => c.url.includes("rename-photos")), "the rename was sent");
      assert.ok(badge(ctx, "Harbour - 01.jpg"), `the renamed row keeps its badge (stopped: ${stopped})`);
      assert.ok(badge(ctx, "b.jpg"));
      closeAllApps();
    }
  });

  test("a save that fails changes no suggestion", async (t) => {
    const ctx = await load(t, notSaved("disk full"));
    click(ctx.window, ctx.document.querySelector(`li[data-path="${A.replace(/\\/g, "\\\\")}"]`));
    await flush(ctx.window, 10);
    await saveCaption(ctx, "Harbour");
    assert.ok(shown(ctx));
    assert.ok(badge(ctx, "a.jpg"));
  });
});
