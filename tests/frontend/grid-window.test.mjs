/**
 * The folder view on the windowed grid (web/tagpup/vgrid.js, grid.js): only the cards on
 * screen exist, and everything a card did still works for the photos that have no card.
 *
 * jsdom has no layout, so the page is given one: the grid's measuring function is replaced by
 * numbers (a 600 px view, four columns of 200 px cards, a 16 px gap), and scrolling is a number
 * the test moves. What is under test is the shipped page; the layout is the only fiction.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports, REPO_ROOT,
} from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2020";
const N = 400;
const STRIDE = 216;
const GRID_TOP = 100;
const name = (i) => `IMG_${String(i).padStart(4, "0")}.jpg`;
const photos = (n = N, folder = FOLDER) =>
  Array.from({ length: n }, (_, i) => photoRecord({ filename: name(i), path: `${folder}\\${name(i)}` }));

const frame = (window) => new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

/** The page, a folder of `n` photos open, and a layout for the grid to measure. */
async function load(t, { n = N, routes = [], onSave } = {}) {
  const server = new FakeServer();
  for (const [match, body] of routes) server.on(match, body);
  server
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", onSave || { success: true })
    .on("/api/photos/bulk-tags", { success: true })
    .on("/api/folder/scan", (url) => {
      const folder = decodeURIComponent((url.match(/path=([^&]+)/) || [])[1] || FOLDER).replace(/\//g, "\\");
      const count = server.counts && server.counts[folder] !== undefined ? server.counts[folder] : n;
      return photos(count, folder);
    });
  server.counts = {};
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.alert = () => {};
  ctx.window.confirm = () => true;
  await openFolder(ctx, FOLDER, { settle: 6 });

  const { state } = pageExports(ctx.window, "web/tagpup/state.js");
  const scroller = ctx.document.getElementById("folder-view-main");
  const content = ctx.document.getElementById("folder-view-content");
  const here = { scrollTop: 0, viewport: 600, columns: 4, cardHeight: 200, rowGap: 16, gridTop: GRID_TOP, cardWidth: 150, columnGap: 16 };
  Object.defineProperty(scroller, "scrollTop", { get: () => here.scrollTop, set: (v) => { here.scrollTop = v; }, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { get: () => (content.classList.contains("hidden") ? 0 : here.viewport), configurable: true });
  state.grid.setMeasure(() => {
    if (content.classList.contains("hidden")) {
      here.scrollTop = 0; // a hidden element forgets where it was scrolled to
      return { ...here, viewport: 0 };
    }
    return { ...here };
  });
  state.grid.refresh();

  Object.assign(ctx, {
    state, here, scroller, server,
    cards: () => [...ctx.document.querySelectorAll("#thumbnails-grid .thumbnail-card")],
    cardOf: (i, folder = FOLDER) => ctx.cards().find((c) => c.getAttribute("data-path") === `${folder}\\${name(i)}`),
    box: (i) => ctx.cardOf(i).querySelector(".thumbnail-checkbox"),
    scrollTo: async (top) => {
      here.scrollTop = top;
      scroller.dispatchEvent(new ctx.window.Event("scroll"));
      await frame(ctx.window);
    },
    scrollToRow: (row) => ctx.scrollTo(GRID_TOP + row * STRIDE),
    label: () => ctx.document.getElementById("selected-thumbnails-count").textContent,
  });
  return ctx;
}

describe("only the cards on screen exist", () => {
  test("a folder of 400 draws a window, whose cards are the first photos", async (t) => {
    const ctx = await load(t);
    const cards = ctx.cards();
    assert.ok(cards.length > 0 && cards.length < 40, `${cards.length} cards`);
    assert.equal(cards[0].getAttribute("data-path"), `${FOLDER}\\${name(0)}`);
    assert.match(ctx.document.getElementById("folder-view-stats").textContent, /400 photos/);
  });

  test("scrolling moves the window; the sidebar list and the stats do not change", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(80);
    assert.ok(ctx.cardOf(320), "the photo at the top of the view has a card");
    assert.equal(ctx.cardOf(0), undefined, "the first photos are no longer in the DOM");
    assert.ok(ctx.cards().length < 40);
    assert.equal(ctx.document.querySelectorAll("#photo-list .photo-item-file").length, N);
  });

  test("pictures are asked for by the cards that stayed in view, not by every card drawn", async (t) => {
    const ctx = await load(t);
    const asked = () => ctx.cards().filter((c) => c.querySelector("img").getAttribute("src")).length;
    assert.equal(asked(), 0, "nothing at once");
    await wait(ctx.window, 160);
    assert.ok(asked() > 0 && asked() < ctx.cards().length, `${asked()} of ${ctx.cards().length}`);
    assert.match(ctx.cardOf(0).querySelector("img").getAttribute("src"), /^\/photo_index\/api\/photo-file\?path=/);
  });
});

describe("selecting photos that have no card", () => {
  test("a Shift-click selects the range between two photos whatever is drawn", async (t) => {
    const ctx = await load(t);
    click(ctx.window, ctx.box(1));
    await ctx.scrollToRow(80);
    assert.equal(ctx.cardOf(1), undefined);
    click(ctx.window, ctx.box(322), { shiftKey: true });
    assert.equal(ctx.state.selectedThumbnails.length, 322, "photos 1 through 322");
    assert.equal(ctx.state.selectedThumbnails[0], `${FOLDER}\\${name(1)}`);
    assert.equal(ctx.state.selectedThumbnails.at(-1), `${FOLDER}\\${name(322)}`);
    assert.match(ctx.label(), /322/);
    assert.ok(ctx.cards().every((c) => c.classList.contains("selected") === (Number(c.dataset.path.slice(-8, -4)) >= 1 && Number(c.dataset.path.slice(-8, -4)) <= 322)));
    await ctx.scrollTo(0);
    assert.ok(ctx.box(5).checked && ctx.cardOf(5).classList.contains("selected"), "a card drawn later shows it");
    assert.equal(ctx.cardOf(0).classList.contains("selected"), false);
  });

  test("Select all, Invert and Select none cover every photo and rebuild no card", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(30);
    const first = ctx.cards()[0];
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    assert.equal(ctx.state.selectedThumbnails.length, N);
    assert.equal(ctx.state.selectedKeys.size, N);
    assert.match(ctx.label(), /400/);
    assert.equal(ctx.cards()[0], first, "the cards on screen were marked, not drawn again");
    assert.ok(ctx.cards().every((c) => c.classList.contains("selected") && c.querySelector(".thumbnail-checkbox").checked));

    click(ctx.window, ctx.box(Number(ctx.cards()[3].dataset.path.slice(-8, -4)))); // one off
    assert.equal(ctx.state.selectedThumbnails.length, N - 1);
    ctx.cards()[0].dispatchEvent(new ctx.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    ctx.document.querySelector('#grid-context-menu [data-action="invert"]').click();
    assert.equal(ctx.state.selectedThumbnails.length, 1, "the one that was off is the only one on");
    assert.equal(ctx.state.selectedKeys.size, 1);

    ctx.document.getElementById("btn-select-none-thumbnails").click();
    assert.deepEqual([...ctx.state.selectedThumbnails], []);
    assert.match(ctx.label(), /: 0/);
  });

  test("a bulk write after Select all receives every path, once", async (t) => {
    const ctx = await load(t);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    ctx.document.getElementById("bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
    await flush(ctx.window, 8);
    const call = ctx.server.calls.find((c) => c.url.includes("/api/photos/bulk-tags"));
    assert.ok(call, "the write was sent");
    assert.equal(call.body.paths.length, N);
    assert.equal(new Set(call.body.paths).size, N);
  });

  test("the context menu names the photo under the cursor, however the card got there", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(60);
    await ctx.scrollToRow(0);
    await ctx.scrollToRow(70);
    const card = ctx.cardOf(282);
    card.dispatchEvent(new ctx.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 10, clientY: 10 }));
    assert.equal(ctx.document.getElementById("grid-context-menu").dataset.path, `${FOLDER}\\${name(282)}`);
  });

  test("Extend selection to here works with nothing selected yet (it threw without a card)", async (t) => {
    const ctx = await load(t);
    const target = ctx.cardOf(2);
    target.dispatchEvent(new ctx.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    ctx.document.querySelector('#grid-context-menu [data-action="select-range"]').click();
    assert.deepEqual([...ctx.state.selectedThumbnails], [`${FOLDER}\\${name(2)}`]);
    assert.ok(ctx.cardOf(2).classList.contains("selected"));
  });
});

describe("a title typed in a card", () => {
  const edit = (ctx, i, text) => {
    ctx.cardOf(i).querySelector(".thumbnail-filename").dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    const input = ctx.cardOf(i).querySelector(".thumbnail-filename-input");
    input.value = text;
    return input;
  };

  test("is not taken from under the person typing when the card scrolls away", async (t) => {
    const ctx = await load(t);
    const input = edit(ctx, 0, "Harbour at dusk");
    assert.equal(ctx.document.activeElement, input);
    await ctx.scrollToRow(80);
    assert.equal(input.isConnected, true, "the editor is still in the page");
    assert.equal(ctx.document.activeElement, input, "and has the focus");
    assert.equal(ctx.server.calls.filter((c) => c.url.includes("save-metadata")).length, 0, "scrolling saved nothing");
    ctx.cards().forEach((c) => assert.ok(c.getAttribute("data-path")));
    await ctx.scrollToRow(0);
    assert.equal(ctx.cardOf(0).querySelector(".thumbnail-filename-input"), input, "the same editor, in its place");
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 6);
    const saved = ctx.server.calls.find((c) => c.url.includes("save-metadata"));
    assert.equal(saved.body.path, `${FOLDER}\\${name(0)}`);
    assert.equal(saved.body.title, "Harbour at dusk");
    assert.equal(ctx.cardOf(0).querySelector(".thumbnail-filename").textContent, "Harbour at dusk");
    assert.equal(ctx.cardOf(0).querySelector(".thumbnail-filename-input"), null);
  });

  test("a photo renamed by its save keeps its selection and its card under the new path", async (t) => {
    const renamed = `${FOLDER}\\Harbour - 01.jpg`;
    const ctx = await load(t, { onSave: { success: true, new_path: renamed } });
    click(ctx.window, ctx.box(0));
    click(ctx.window, ctx.box(2));
    const input = edit(ctx, 0, "Harbour");
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 6);
    assert.ok(ctx.cards().some((c) => c.getAttribute("data-path") === renamed), "the card has the new path");
    assert.ok(!ctx.cards().some((c) => c.getAttribute("data-path") === `${FOLDER}\\${name(0)}`));
    assert.deepEqual([...ctx.state.selectedThumbnails], [renamed, `${FOLDER}\\${name(2)}`], "still selected, under the new name");
    assert.equal(ctx.state.selectedKeys.size, 2);
    const card = ctx.cards().find((c) => c.getAttribute("data-path") === renamed);
    assert.ok(card.classList.contains("selected"));
  });
});

describe("the place in the list", () => {
  test("an edit that draws the grid again keeps the scroll position and the window", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(50);
    const before = ctx.cards().map((c) => c.dataset.path);
    ctx.cardOf(200).querySelector(".thumbnail-filename").dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    const input = ctx.cardOf(200).querySelector(".thumbnail-filename-input");
    input.value = "Kentridge";
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await flush(ctx.window, 6);
    assert.equal(ctx.here.scrollTop, GRID_TOP + 50 * STRIDE);
    assert.deepEqual(ctx.cards().map((c) => c.dataset.path), before);
    assert.equal(ctx.cardOf(200).querySelector(".thumbnail-filename").textContent, "Kentridge");
  });

  test("typing in the filter starts the list again from the top", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(80);
    const search = ctx.document.getElementById("photo-search");
    search.value = "IMG_01";
    search.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    await wait(ctx.window, 220);
    assert.equal(ctx.here.scrollTop, 0);
    assert.equal(ctx.cards()[0].getAttribute("data-path"), `${FOLDER}\\${name(100)}`);
    search.value = "no such photo";
    search.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    await wait(ctx.window, 220);
    assert.equal(ctx.cards().length, 0);
    assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /No photos found matching filter\./);
  });

  test("opening a photo and coming back finds the grid where it was, with its selection", async (t) => {
    const ctx = await load(t);
    click(ctx.window, ctx.box(3));
    await ctx.scrollToRow(40);
    ctx.cardOf(164).querySelector(".btn-thumbnail-detail").dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    await flush(ctx.window, 6);
    assert.ok(ctx.document.getElementById("folder-view-content").classList.contains("hidden"), "the photo is open");
    // A hidden element forgets its scroll offset, and the resize observer reports its size gone.
    ctx.here.scrollTop = 0;
    ctx.state.grid.render();
    ctx.document.getElementById("folder-view-header").dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    await flush(ctx.window, 6);
    assert.equal(ctx.here.scrollTop, GRID_TOP + 40 * STRIDE);
    assert.ok(ctx.cardOf(164), "the photo that was opened is on screen");
    assert.ok(ctx.cards().length < 40);
    assert.deepEqual([...ctx.state.selectedThumbnails], [`${FOLDER}\\${name(3)}`]);
    await ctx.scrollTo(0);
    assert.ok(ctx.cardOf(3).classList.contains("selected"));
  });

  test("another folder opens at the top; the same folder scanned again keeps its place", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(60);
    ctx.document.getElementById("btn-refresh-list").click();
    await flush(ctx.window, 8);
    assert.equal(ctx.here.scrollTop, GRID_TOP + 60 * STRIDE, "a rescan of the folder keeps the place");
    assert.ok(ctx.cardOf(240));
    await openFolder(ctx, "D:\\Library\\2021", { settle: 8 });
    assert.equal(ctx.here.scrollTop, 0);
    assert.equal(ctx.cards()[0].getAttribute("data-path"), `D:\\Library\\2021\\${name(0)}`);
  });

  test("a folder that comes back with fewer photos, while scrolled past them, shows the end", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(90);
    ctx.server.counts[FOLDER] = 30;
    ctx.document.getElementById("btn-refresh-list").click();
    await flush(ctx.window, 8);
    assert.ok(ctx.cardOf(29), "the last photo has its card");
    assert.ok(ctx.cards().length > 0 && ctx.cards().length <= 30);
  });

  test("two folders opened one after the other leave the second's cards, from the top", async (t) => {
    const ctx = await load(t);
    await ctx.scrollToRow(60);
    ctx.document.getElementById("folder-path-input").value = "D:\\Library\\2022";
    ctx.document.getElementById("folder-path-input").dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    ctx.document.getElementById("folder-path-input").value = "D:\\Library\\2023";
    ctx.document.getElementById("folder-path-input").dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 10);
    assert.ok(ctx.cards().every((c) => c.dataset.path.startsWith("D:\\Library\\2023\\")), "only the last folder's cards");
    assert.equal(ctx.here.scrollTop, 0);
  });
});

describe("a change of size at a place in the list", () => {
  test("the same photos stay at the top of the view", async (t) => {
    const ctx = await load(t);
    await ctx.scrollTo(GRID_TOP + 80 * STRIDE + 54); // a quarter of the way into the row of photo 320
    ctx.here.columns = 2;
    ctx.here.cardHeight = 380;
    ctx.here.rowGap = 20;
    click(ctx.window, ctx.document.getElementById("btn-size-large"));
    const row = 160 + 54 / STRIDE;
    assert.ok(Math.abs(ctx.here.scrollTop - (GRID_TOP + row * 400)) < 1e-6, String(ctx.here.scrollTop));
    assert.ok(ctx.cardOf(320));
    assert.ok(ctx.document.getElementById("thumbnails-grid").classList.contains("size-larger"));
  });
});

describe("marks that arrive after the cards were built", () => {
  test("a damaged photo scrolled into view has its mark and no picture to ask for", async (t) => {
    const bad = `${FOLDER}\\${name(300)}`;
    const ctx = await load(t, {
      routes: [["/api/folder/damaged", {
        folder: FOLDER,
        photos: [{ path: bad, name: name(300), folder: FOLDER, kind: "truncated", indexed: false, reason: "cut short",
          detail: "", zero_tail: 0, size: 9, mtime: 1, found: "2026-09-28 10:00:00" }],
      }]],
    });
    await wait(ctx.window, 60);
    await ctx.scrollToRow(75);
    const card = ctx.cardOf(300);
    assert.ok(card.classList.contains("damaged"));
    assert.equal(card.querySelector("img"), null);
    assert.match(card.querySelector(".thumbnail-damaged-placeholder").textContent, /Can't be read/);
  });
});

describe("the selection has one owner", () => {
  test("nothing but selected.js assigns, pushes to or splices state.selectedThumbnails", () => {
    const dir = path.join(REPO_ROOT, "web", "tagpup");
    const writes = /selectedThumbnails\s*(=[^=]|\.(push|splice|pop|shift|unshift|sort|reverse)\(|\.length\s*=[^=])|selectedKeys\.(add|delete|clear)/;
    const offenders = [];
    for (const file of fs.readdirSync(dir).filter((f) => f.endsWith(".js") && f !== "selected.js" && f !== "state.js")) {
      fs.readFileSync(path.join(dir, file), "utf8").split(/\r?\n/).forEach((line, i) => {
        if (writes.test(line.replace(/\/\/.*$/, ""))) offenders.push(`${file}:${i + 1}: ${line.trim()}`);
      });
    }
    assert.deepEqual(offenders, [], "the Set and the array would drift apart");
  });
});
