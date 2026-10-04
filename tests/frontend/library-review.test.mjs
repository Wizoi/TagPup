/**
 * The owner's first review of the library views after installing phase 9 (docs/findings.md #668-#675; this file holds the
 * header, the strip, the toolbar, the selection details and Delete -- #671-#673, the navigator's, are tested beside it).
 * Fictional names only: the real library is photographs of real people, many of them minors.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps } from "./harness.mjs";
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
