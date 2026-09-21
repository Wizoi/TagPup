/**
 * The selection panel and the grid context menu.
 *
 * Two usability problems this covers. The selection panel used to be hidden whenever
 * the selection emptied, so clearing and re-selecting collapsed and reflowed the whole
 * right-hand column on every click. And the selection toolbar lives in a header that
 * scrolls out of view in a long folder, which put "select none" out of reach at exactly
 * the moment it is wanted.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder } from "./harness.mjs";

const FILES = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"];

async function loadGrid(t) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", FILES.map((filename) => photoRecord({ filename })));

  const ctx = await loadApp("tagpup", {
    t,
    url: "http://localhost:8090/photo_index/",
    server,
  });

  await openFolder(ctx, "D:\\Library\\2020", { settle: 6 });

  ctx.cards = () => [...ctx.document.querySelectorAll(".thumbnail-card")];
  ctx.checkboxes = () =>
    ctx.cards().map((card) => card.querySelector(".thumbnail-checkbox"));
  ctx.selected = () =>
    ctx.cards().filter((c) => c.classList.contains("selected")).length;
  ctx.panel = () => ctx.document.getElementById("folder-selection-sidebar");
  ctx.menu = () => ctx.document.getElementById("grid-context-menu");
  return ctx;
}

function isHidden(el) {
  return !el || el.classList.contains("hidden");
}

/** Right-click, optionally over a specific card. */
function rightClick(window, target) {
  const event = new window.MouseEvent("contextmenu", {
    bubbles: true,
    cancelable: true,
    clientX: 100,
    clientY: 100,
  });
  target.dispatchEvent(event);
  return event;
}

describe("the selection panel stays put", () => {
  test("it is visible with nothing selected", async (t) => {
    const ctx = await loadGrid(t);
    assert.ok(!isHidden(ctx.panel()), "panel was hidden on an empty selection");
  });

  test("it shows an empty hint when nothing is selected", async (t) => {
    const ctx = await loadGrid(t);
    const hint = ctx.document.getElementById("selection-empty-hint");
    assert.ok(!isHidden(hint), "no empty-state hint shown");
  });

  test("it stays visible through select then clear", async (t) => {
    const ctx = await loadGrid(t);
    click(ctx.window, ctx.checkboxes()[0]);
    assert.ok(!isHidden(ctx.panel()));

    ctx.document.getElementById("btn-select-none-thumbnails").click();
    assert.ok(!isHidden(ctx.panel()), "panel collapsed when the selection emptied");
  });

  test("the hint gives way to the summary once something is selected", async (t) => {
    const ctx = await loadGrid(t);
    const hint = ctx.document.getElementById("selection-empty-hint");
    const scroll = ctx.document.querySelector(".selection-summary-scroll");

    click(ctx.window, ctx.checkboxes()[0]);
    assert.ok(isHidden(hint), "hint stayed up with a selection");
    assert.ok(!isHidden(scroll), "summary stayed hidden with a selection");
  });

  test("clearing the selection empties the summary rather than leaving stale chips", async (t) => {
    const ctx = await loadGrid(t);
    click(ctx.window, ctx.checkboxes()[0]);
    ctx.document.getElementById("btn-select-none-thumbnails").click();

    const dateValue = ctx.document.getElementById("selection-date-value").textContent;
    assert.equal(dateValue, "--", `stale summary left behind: ${dateValue}`);
  });
});

describe("grid context menu", () => {
  test("right-clicking the grid opens it", async (t) => {
    const ctx = await loadGrid(t);
    assert.ok(isHidden(ctx.menu()), "menu was open before any right-click");

    rightClick(ctx.window, ctx.cards()[0]);
    assert.ok(!isHidden(ctx.menu()), "right-click did not open the menu");
  });

  test("it reports how much is selected", async (t) => {
    const ctx = await loadGrid(t);
    click(ctx.window, ctx.checkboxes()[0]);
    click(ctx.window, ctx.checkboxes()[1]);
    rightClick(ctx.window, ctx.cards()[0]);

    const label = ctx.document.getElementById("context-menu-count").textContent;
    assert.match(label, /2 of 4/, `unexpected count label: ${label}`);
  });

  test("select none clears the selection", async (t) => {
    const ctx = await loadGrid(t);
    click(ctx.window, ctx.checkboxes()[0]);
    click(ctx.window, ctx.checkboxes()[1]);
    assert.equal(ctx.selected(), 2);

    rightClick(ctx.window, ctx.cards()[0]);
    ctx.menu().querySelector('[data-action="select-none"]').click();
    assert.equal(ctx.selected(), 0, "select none did not clear the selection");
  });

  test("select all takes every photo", async (t) => {
    const ctx = await loadGrid(t);
    rightClick(ctx.window, ctx.cards()[0]);
    ctx.menu().querySelector('[data-action="select-all"]').click();
    assert.equal(ctx.selected(), FILES.length);
  });

  test("invert flips the selection", async (t) => {
    const ctx = await loadGrid(t);
    click(ctx.window, ctx.checkboxes()[0]);
    rightClick(ctx.window, ctx.cards()[0]);
    ctx.menu().querySelector('[data-action="invert"]').click();

    assert.equal(ctx.selected(), FILES.length - 1, "invert did not flip the selection");
    assert.ok(
      !ctx.cards()[0].classList.contains("selected"),
      "the previously selected card stayed selected"
    );
  });

  test("choosing an action closes the menu", async (t) => {
    const ctx = await loadGrid(t);
    rightClick(ctx.window, ctx.cards()[0]);
    ctx.menu().querySelector('[data-action="select-all"]').click();
    assert.ok(isHidden(ctx.menu()), "menu stayed open after an action");
  });

  test("Escape closes the menu", async (t) => {
    const ctx = await loadGrid(t);
    rightClick(ctx.window, ctx.cards()[0]);
    ctx.document.dispatchEvent(
      new ctx.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })
    );
    assert.ok(isHidden(ctx.menu()), "Escape did not close the menu");
  });

  test("clicking elsewhere closes the menu", async (t) => {
    const ctx = await loadGrid(t);
    rightClick(ctx.window, ctx.cards()[0]);
    click(ctx.window, ctx.document.body);
    assert.ok(isHidden(ctx.menu()), "clicking away did not close the menu");
  });

  test("selection-dependent actions are disabled with nothing selected", async (t) => {
    const ctx = await loadGrid(t);
    rightClick(ctx.window, ctx.cards()[0]);
    const gated = [...ctx.menu().querySelectorAll("[data-requires-selection]")];
    for (const item of gated) {
      assert.ok(
        item.classList.contains("disabled"),
        `${item.getAttribute("data-action")} was enabled with an empty selection`
      );
    }
  });

  test("opening a photo from the menu shows the details panel", async (t) => {
    const ctx = await loadGrid(t);
    rightClick(ctx.window, ctx.cards()[1]);
    ctx.menu().querySelector('[data-action="open"]').click();
    await flush(ctx.window, 3);

    const panel = ctx.document.getElementById("panel-content");
    assert.ok(!isHidden(panel), "details panel did not open");
  });

  test("Show in File Explorer asks the server for the right photo", async (t) => {
    const ctx = await loadGrid(t);
    rightClick(ctx.window, ctx.cards()[2]);
    ctx.menu().querySelector('[data-action="explorer"]').click();
    await flush(ctx.window, 3);

    const body = ctx.server.lastBody("/api/photo/open-explorer");
    assert.ok(body, "no explorer request was made");
    assert.match(body.path, /c\.jpg$/, `wrong photo: ${body.path}`);
  });

  test("right-clicking empty grid space still offers the folder-wide actions", async (t) => {
    const ctx = await loadGrid(t);
    const grid = ctx.document.getElementById("thumbnails-grid");
    rightClick(ctx.window, grid);

    assert.ok(!isHidden(ctx.menu()));
    ctx.menu().querySelector('[data-action="select-all"]').click();
    assert.equal(ctx.selected(), FILES.length);
  });
});
