/**
 * The owner's first review of the library views after installing phase 9 (docs/findings.md #668-#675; this file holds the
 * header, the strip, the toolbar, the selection details and Delete -- #671-#673, the navigator's, are tested beside it).
 * Fictional names only: the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const el = (ctx, id) => ctx.document.getElementById(id);

describe("#668: the button beside Library is Organize", () => {
  test("it says Organize, and its tooltip says what Organize is for: one folder on disk", async (t) => {
    const ctx = await loadViewPage(t);
    const button = el(ctx, "sidebar-tab-folder");
    assert.equal(button.textContent.trim(), "Organize");
    assert.match(button.title, /one folder on disk/);
    assert.match(button.title, /Smart Rename/);
    const switchText = el(ctx, "sidebar-switch").textContent.replace(/\s+/g, " ").trim();
    assert.equal(switchText, "Library Organize", "no button is called Folder: that is the library's Folders tab");
  });
});

describe("#669: Smart Rename and the time shift are Organize's, not a library view's", () => {
  const FOLDER = "D:\\Library\\2020\\Event 01";
  const scan = () => [1, 2].map((n) => photoRecord({ filename: `IMG_000${n}.jpg`, path: `${FOLDER}\\IMG_000${n}.jpg`, tags: [], people: [] }));

  test("every kind of view: neither button is shown, and neither panel", async (t) => {
    for (const search of ["?view=all", "?view=folder&value=D%3A%5CLibrary&recursive=1", "?view=keyword&value=Trips", "?view=person&value=Wren%20Halloway", "?view=year&value=2020", "?view=month&value=2020-06"]) {
      const ctx = await loadViewPage(t, { search });
      for (const id of ["btn-toggle-rename", "btn-toggle-timeshift"]) assert.ok(el(ctx, id).classList.contains("hidden"), `${id} in ${search}`);
      assert.ok(el(ctx, "rename-panel").classList.contains("hidden"));
      assert.ok(el(ctx, "timeshift-panel").classList.contains("hidden"));
      assert.equal(el(ctx, "timeshift-direction"), null, "the view's own direction field is gone");
    }
  });

  test("a folder in Organize keeps both, with the camera, and they come back when a view closes onto a folder", async (t) => {
    const ctx = await loadViewPage(t, { search: "", scan });
    await openFolder(ctx, FOLDER);
    await ctx.settle(60);
    for (const id of ["btn-toggle-rename", "btn-toggle-timeshift"]) {
      assert.ok(!el(ctx, id).classList.contains("hidden"), id);
      assert.equal(el(ctx, id).disabled, false, id);
    }
    ctx.module("library-view.js").openLibraryView({ kind: "all" });
    await ctx.settle();
    assert.ok(el(ctx, "btn-toggle-timeshift").classList.contains("hidden"));
    ctx.module("library-view.js").closeLibraryView({ folder: FOLDER });
    await ctx.settle(100);
    assert.ok(!el(ctx, "btn-toggle-timeshift").classList.contains("hidden"));
    assert.ok(!el(ctx, "btn-toggle-rename").classList.contains("hidden"));
    click(ctx.window, el(ctx, "btn-toggle-timeshift"));
    assert.ok(!el(ctx, "timeshift-panel").classList.contains("hidden"));
    assert.ok(el(ctx, "timeshift-camera-select").options.length > 0, "the camera is offered in Organize");
  });
});
