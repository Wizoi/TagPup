/**
 * The Selection Details panel in a narrow window (docs/findings.md, #720).
 *
 * The panel (340 px) and the sidebar (360) kept their widths at every window width, leaving the grid 296 px at 1,000
 * and 40 px at 720, where the page scrolled sideways. Under NARROW px the panel is collapsed behind a button
 * (aria-expanded, aria-controls) and opened lies over the grid. The widths themselves are measured in a real
 * Chromium by scripts/measure_narrow_window.py; here, what jsdom can hold: the button and its attributes, the choice
 * kept in this browser (and a browser that keeps nothing), and the stylesheet's rules, read as text.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadApp, FakeServer, photoRecord, click, openFolder, REPO_ROOT } from "./harness.mjs";

const KEY = "tagpup.detailsPanel";
const FILES = ["a.jpg", "b.jpg"];

async function load(t, { before } = {}) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", FILES.map((filename) => photoRecord({ filename })));
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server, before });
  await openFolder(ctx, "D:\\Library\\2020", { settle: 6 });
  ctx.button = () => ctx.document.getElementById("btn-details-toggle");
  ctx.panel = () => ctx.document.getElementById("folder-selection-sidebar");
  return ctx;
}

describe("the details button", () => {
  test("is a button that says the panel is closed and names it", async (t) => {
    const ctx = await load(t);
    const button = ctx.button();
    assert.equal(button.tagName, "BUTTON");
    assert.equal(button.getAttribute("type"), "button");
    assert.equal(button.getAttribute("aria-controls"), "folder-selection-sidebar");
    assert.equal(button.getAttribute("aria-expanded"), "false");
    assert.ok(!ctx.panel().classList.contains("details-open"));
  });

  test("opens and closes the panel, and this browser remembers which", async (t) => {
    const ctx = await load(t);
    click(ctx.window, ctx.button());
    assert.equal(ctx.button().getAttribute("aria-expanded"), "true");
    assert.ok(ctx.panel().classList.contains("details-open"));
    assert.equal(ctx.window.localStorage.getItem(KEY), "open");
    click(ctx.window, ctx.button());
    assert.equal(ctx.button().getAttribute("aria-expanded"), "false");
    assert.ok(!ctx.panel().classList.contains("details-open"));
    assert.equal(ctx.window.localStorage.getItem(KEY), "closed");
  });

  test("the panel has a button of its own to close it, for the opened panel covers Details; the focus follows", async (t) => {
    const ctx = await load(t);
    const close = ctx.document.getElementById("btn-details-close");
    assert.equal(close.tagName, "BUTTON");
    assert.ok(ctx.panel().contains(close));
    assert.equal(close.getAttribute("aria-label"), "Hide Selection Details");
    click(ctx.window, ctx.button());
    assert.equal(ctx.document.activeElement, close, "the focus went into the opened panel");
    click(ctx.window, close);
    assert.equal(ctx.button().getAttribute("aria-expanded"), "false");
    assert.ok(!ctx.panel().classList.contains("details-open"));
    assert.equal(ctx.window.localStorage.getItem(KEY), "closed");
    assert.equal(ctx.document.activeElement, ctx.button(), "and back to Details as it closed");
  });

  test("a page opened after it was left open starts open", async (t) => {
    const ctx = await load(t, { before: (window) => window.localStorage.setItem(KEY, "open") });
    assert.equal(ctx.button().getAttribute("aria-expanded"), "true");
    assert.ok(ctx.panel().classList.contains("details-open"));
  });

  test("a browser that keeps nothing still has the button, and it works", async (t) => {
    const ctx = await load(t, {
      before: (window) => {
        const real = window.localStorage;
        const off = (key) => String(key).startsWith(KEY);
        const stub = {
          getItem: (key) => { if (off(key)) throw new Error("storage is off"); return real.getItem(key); },
          setItem: (key, value) => { if (off(key)) throw new Error("storage is off"); real.setItem(key, value); },
          removeItem: (key) => real.removeItem(key),
        };
        Object.defineProperty(window, "localStorage", { get: () => stub, configurable: true });
      },
    });
    assert.equal(ctx.button().getAttribute("aria-expanded"), "false");
    click(ctx.window, ctx.button());
    assert.equal(ctx.button().getAttribute("aria-expanded"), "true");
  });
});

describe("the stylesheet", () => {
  const css = fs.readFileSync(path.join(REPO_ROOT, "web", "tagpup", "style.css"), "utf8");
  const NARROW = 1100;

  /** The rules of the one @media (max-width: NARROW - 1) block that names the panel, as text. */
  function narrowBlock() {
    const start = css.indexOf(`@media (max-width: ${NARROW - 1}px)`);
    assert.ok(start >= 0, `a media rule for windows under ${NARROW} px`);
    let depth = 0;
    for (let at = css.indexOf("{", start); at < css.length; at++) {
      if (css[at] === "{") depth++;
      if (css[at] === "}" && --depth === 0) return css.slice(start, at + 1);
    }
    throw new Error("the media rule does not end");
  }

  test("below the breakpoint the panel is out of the row and the button shows; open, it lies over the grid", () => {
    const block = narrowBlock();
    assert.match(block, /\.folder-view-sidebar\s*\{[^}]*display:\s*none/, "collapsed: not in the row");
    assert.match(block, /\.folder-view-sidebar\.details-open\s*\{[^}]*position:\s*absolute/, "opened: over the grid, not beside it");
    assert.match(block, /\.details-toggle,[^{]*\{[^}]*display:\s*inline-flex/, "the button shows");
    assert.match(block, /\.details-open \.details-close/, "the panel's own close button shows only while it is opened");
  });

  test("on a wide window the button is not shown", () => {
    assert.match(css, /\.details-toggle,\s*\.details-close\s*\{[^}]*display:\s*none/);
  });

  test("the grid's cards keep their 150 px minimum", () => {
    assert.match(css, /\.thumbnails-grid\s*\{[^}]*minmax\(150px,\s*1fr\)/);
  });
});
