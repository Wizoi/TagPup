/**
 * Opening or closing a folder replaces the address of the place the page is at; it does not
 * make or lose a place, so the place keeps its position (findings #851).
 *
 * folder.js replaced the history state with {} in three places, which dropped the position
 * (history-entries.js) of a place made before the folder opened: a Back or Forward cancelled
 * for unsaved edits then could not find its way back to it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, openFolder, closeAllApps, pageExports } from "./harness.mjs";

const FOLDER = "D:/Library/2020";

function server() {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/autocomplete-folder", [])
    .on("/api/folder/scan", () => [photoRecord({ filename: "a.jpg" }), photoRecord({ filename: "b.jpg" })]);
}

afterEach(() => closeAllApps());

describe("a folder opened from a place of the history", () => {
  test("keeps the place's position, and no longer calls it a photo's", async (t) => {
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server: server() });
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    const load = state.entries.load;
    ctx.window.history.replaceState({ photo: 7, searchBack: 2, entryLoad: load, entryPos: 3 }, "");
    state.entries.at = 3;

    await openFolder(ctx, FOLDER);

    const here = ctx.window.history.state;
    assert.equal(here && here.entryLoad, load, "the place lost the load that made it");
    assert.equal(here && here.entryPos, 3, "the place lost its position");
    assert.equal(here && here.photo, undefined, "the place still says a photo is open over it");
    assert.ok(ctx.window.location.search.includes("path="), "the address does not name the folder");
  });

  test("the page's first place keeps the position it was given on load", async (t) => {
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server: server() });
    await openFolder(ctx, FOLDER);
    const here = ctx.window.history.state || {};
    assert.equal(here.entryPos, 0);
  });
});
