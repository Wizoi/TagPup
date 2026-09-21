/**
 * Choosing a folder opens it, and says which folder you are in.
 *
 * There was a Scan Folder button beside the path box. Choosing a folder and then
 * telling the app to go and look at it are not two decisions -- nobody picks a folder
 * they did not want opened -- so the button was a step that existed only because the
 * code needed a trigger. Browse already scanned on its own, which made the button
 * inconsistent as well as redundant.
 *
 * The other half is knowing where you are. The path box holds an absolute path that
 * is usually too long to read in a sidebar, so the folder's own name is shown under
 * it and put in the window title, which is what you see when the window is not focused.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, openFolder } from "./harness.mjs";

const FOLDER = "D:/Training/Pictures/Cross Country/2026-09-16 - KR XC at North SeaTac";

function server() {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/autocomplete-folder", [])
    .on("/api/folder/scan", [
      photoRecord({ filename: "a.jpg" }),
      photoRecord({ filename: "b.jpg" }),
    ]);
}

const load = (t) =>
  loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server: server() });

const scans = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/folder/scan")).length;

describe("choosing a folder opens it", () => {
  test("committing a typed path scans without a further click", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.equal(scans(ctx), 1, "choosing a folder did not open it");
    assert.equal(ctx.document.querySelectorAll("li[data-path]").length, 2);
  });

  test("Enter opens it without waiting for focus to leave the box", async (t) => {
    const ctx = await load(t);
    const input = ctx.document.getElementById("folder-path-input");
    input.value = FOLDER;
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", {
      key: "Enter", bubbles: true, cancelable: true,
    }));
    await flush(ctx.window, 6);
    assert.equal(scans(ctx), 1, "Enter did not open the folder");
  });

  test("the Scan Folder button is gone", async (t) => {
    const ctx = await load(t);
    assert.equal(
      ctx.document.getElementById("btn-scan-folder"), null,
      "the button is back; choosing a folder should be the whole gesture"
    );
  });

  test("an empty box does nothing rather than erroring", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, "");
    assert.equal(scans(ctx), 0);
  });

  test("leaving the box on the folder already open does not rescan it", async (t) => {
    // `change` fires on blur as well as on picking a suggestion, so without a guard
    // every click away from the box would re-scan the folder on screen.
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.equal(scans(ctx), 1);
    await openFolder(ctx, FOLDER);
    assert.equal(scans(ctx), 1, "the open folder was scanned again for nothing");
  });

  test("a trailing slash is the same folder", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    await openFolder(ctx, FOLDER + "/");
    assert.equal(scans(ctx), 1, "a trailing slash made it look like a different folder");
  });

  test("choosing a different folder does open it", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    await openFolder(ctx, "D:/Training/Pictures/Cross Country/2026-09-12 - Lake Wilderness");
    assert.equal(scans(ctx), 2, "a different folder was not opened");
  });
});

describe("saying which folder you are in", () => {
  test("the folder's own name is shown, not the whole path", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    const label = ctx.document.getElementById("current-folder-name");
    assert.equal(label.textContent, "2026-09-16 - KR XC at North SeaTac");
    assert.ok(!label.classList.contains("hidden"), "the folder name stayed hidden");
  });

  test("the full path is still there to hover for", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.equal(ctx.document.getElementById("current-folder-name").title, FOLDER);
  });

  test("the window title names the folder", async (t) => {
    // What you see in the taskbar with several folders open at once.
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.match(ctx.document.title, /2026-09-16 - KR XC at North SeaTac/);
  });

  test("nothing is shown before a folder is open", async (t) => {
    const ctx = await load(t);
    const label = ctx.document.getElementById("current-folder-name");
    assert.ok(label.classList.contains("hidden"), "an empty folder name was shown");
  });

  test("the folder view row names the folder too", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    const header = ctx.document.getElementById("folder-view-header");
    assert.match(header.textContent, /2026-09-16 - KR XC at North SeaTac/);
  });
});

describe("the heavier work is labelled as heavier", () => {
  test("Suggest Tags now says what it starts", async (t) => {
    // "Suggest Tags" reads like a menu item; it runs CLIP over every photo in the
    // folder and takes minutes. The name should set that expectation.
    const ctx = await load(t);
    const button = ctx.document.getElementById("btn-suggest-tags");
    assert.match(button.textContent, /Start Photo Analysis/);
  });
});

describe("the untagged-only checkbox is gone", () => {
  test("it is no longer in the sidebar", async (t) => {
    const ctx = await load(t);
    assert.equal(ctx.document.getElementById("filter-untagged-only"), null);
  });

  test("the file list still works without it", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.equal(ctx.document.querySelectorAll("li[data-path]").length, 2);
  });
});

describe("the dog park cannot change under an open folder", () => {
  // Switching reloads the page carrying the same ?path=, so the same folder comes back
  // attached to a different index, different suggestions and different people, with
  // nothing marking the change. That is a quiet way to tag a folder into the wrong
  // library, so the picker locks while a folder is open.
  test("the picker is free before a folder is opened", async (t) => {
    const ctx = await load(t);
    assert.equal(ctx.document.getElementById("db-select").disabled, false);
    assert.ok(ctx.document.getElementById("btn-change-db").classList.contains("hidden"));
  });

  test("it locks once a folder is open", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.equal(
      ctx.document.getElementById("db-select").disabled, true,
      "the dog park could still be switched under an open folder"
    );
  });

  test("a Change button appears instead", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.ok(!ctx.document.getElementById("btn-change-db").classList.contains("hidden"));
  });

  test("Change closes the folder and frees the picker", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    ctx.window.confirm = () => true;

    ctx.document.getElementById("btn-change-db").click();
    await flush(ctx.window, 4);

    assert.equal(ctx.document.getElementById("db-select").disabled, false);
    assert.equal(ctx.document.getElementById("folder-path-input").value, "");
    assert.ok(
      !ctx.window.location.search.includes("path="),
      "the folder stayed in the URL, so it would reopen in the new dog park"
    );
  });

  test("declining Change leaves everything as it was", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    ctx.window.confirm = () => false;

    ctx.document.getElementById("btn-change-db").click();
    await flush(ctx.window, 4);

    assert.equal(ctx.document.getElementById("db-select").disabled, true);
    assert.equal(ctx.document.getElementById("folder-path-input").value, FOLDER);
  });

  test("the picker is labelled in the app's own language", async (t) => {
    const ctx = await load(t);
    assert.match(ctx.document.body.textContent, /Dog Park/);
  });
});
