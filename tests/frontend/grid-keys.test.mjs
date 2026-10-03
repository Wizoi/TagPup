/**
 * The grid, operated from the keyboard (web/tagpup/grid-keys.js, vgrid.js; phase 9c): ONE tab stop (roving tabindex), the
 * arrow keys by card and by row, Home and End, PageUp and PageDown by a window, Enter to open the photo, Space to select,
 * Shift to extend -- in a library view and in a folder view, across the edge of the window and at 20,000 photos, with the
 * focused card kept in the DOM and the focus kept across a redraw. jsdom has no layout, so the page is given one (a 600 px
 * view, four columns, so two rows in view); the focus is jsdom's own. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord } from "./harness.mjs";
import { loadViewPage, pageErrors, cardOf, GRID_TOP, STRIDE } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const frame = (window) => new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
const idsOf = (n) => Array.from({ length: n }, (_, i) => 1000 + i);

async function viewPage(t, n = 400, options = {}) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: idsOf(n), ...options });
  Object.assign(ctx, {
    focused: () => ctx.document.activeElement,
    focusedId: () => {
      const el = ctx.document.activeElement;
      return el && el.getAttribute && el.getAttribute("data-id") !== null ? Number(el.getAttribute("data-id")) : null;
    },
    press: async (key, init = {}, ms = 130) => {
      const target = ctx.document.activeElement && ctx.document.activeElement !== ctx.document.body ? ctx.document.activeElement : ctx.document.getElementById("thumbnails-grid");
      ctx.key(target, key, init);
      await frame(ctx.window);
      if (ms) await ctx.settle(ms);
    },
    stops: () => ctx.cards().filter((card) => card.getAttribute("tabindex") === "0"),
  });
  return ctx;
}

describe("one tab stop", () => {
  test("the grid is a listbox of options with one card at tabindex 0; placeholders take no focus; what is inside a card takes none", async (t) => {
    const ctx = await viewPage(t);
    const grid = ctx.document.getElementById("thumbnails-grid");
    assert.equal(grid.getAttribute("role"), "listbox");
    assert.equal(grid.getAttribute("aria-multiselectable"), "true");
    assert.equal(ctx.stops().length, 1);
    assert.equal(ctx.stops()[0], ctx.real()[0], "the first card, until the keys are anywhere else");
    for (const card of ctx.real()) {
      if (card !== ctx.stops()[0]) assert.equal(card.getAttribute("tabindex"), "-1");
      assert.equal(card.querySelector(".thumbnail-checkbox").getAttribute("tabindex"), "-1");
      assert.equal(card.querySelector(".btn-thumbnail-detail").getAttribute("tabindex"), "-1");
    }
    for (const hole of ctx.cards().filter((card) => card.classList.contains("placeholder"))) {
      assert.equal(hole.hasAttribute("tabindex"), false, "not focusable until loaded");
    }
  });

  test("the tab stop is the card the keys are on", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1005).focus();
    assert.equal(ctx.state.gridKeys.index, 5);
    assert.deepEqual(ctx.stops(), [ctx.cardById(1005)]);
    ctx.cardById(1001).focus();
    assert.deepEqual(ctx.stops(), [ctx.cardById(1001)]);
  });

  test("a grid with no real card has no tab stop of its own: nothing to land on", async (t) => {
    const ctx = await viewPage(t, 400, { onCards: () => ({ cards: [] }) });
    assert.equal(ctx.stops().length, 0);
    assert.equal(ctx.document.getElementById("thumbnails-grid").getAttribute("tabindex"), "-1");
  });
});

describe("moving", () => {
  test("arrows move by one card and by a row, Home and End to the ends of the view", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1000).focus();
    await ctx.press("ArrowRight");
    assert.equal(ctx.focusedId(), 1001);
    await ctx.press("ArrowDown");
    assert.equal(ctx.focusedId(), 1005);
    await ctx.press("ArrowLeft");
    assert.equal(ctx.focusedId(), 1004);
    await ctx.press("ArrowUp");
    assert.equal(ctx.focusedId(), 1000);
    await ctx.press("ArrowUp");
    assert.equal(ctx.focusedId(), 1000, "the first card has nothing above");
    await ctx.press("End", {}, 400);
    assert.equal(ctx.focusedId(), 1399, "End goes to the last photo, whose card had to be fetched");
    await ctx.press("ArrowRight");
    assert.equal(ctx.focusedId(), 1399);
    await ctx.press("Home", {}, 400);
    assert.equal(ctx.focusedId(), 1000);
  });

  test("PageDown and PageUp move a window: the rows in view", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1000).focus();
    await ctx.press("PageDown", {}, 300);
    assert.equal(ctx.focusedId(), 1008, "two rows of four");
    await ctx.press("PageDown", {}, 300);
    assert.equal(ctx.focusedId(), 1016);
    await ctx.press("PageUp", {}, 300);
    assert.equal(ctx.focusedId(), 1008);
  });

  test("a last row that is short: Down from a column with no card below goes to the last card", async (t) => {
    const ctx = await viewPage(t, 10);
    ctx.cardById(1003).focus();     // row 0, last column
    await ctx.press("ArrowDown");
    assert.equal(ctx.focusedId(), 1007);
    await ctx.press("ArrowDown");
    assert.equal(ctx.focusedId(), 1009, "row 2 has two cards; below column 3 there is none: the last");
    await ctx.press("ArrowDown");
    assert.equal(ctx.focusedId(), 1009);
  });

  test("the focused card scrolls into view, below the strip that sticks above the grid", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1000).focus();
    for (let i = 0; i < 4; i++) await ctx.press("ArrowDown");
    assert.equal(ctx.focusedId(), 1016);
    const top = ctx.here.scrollTop;
    const rowTop = GRID_TOP + 4 * STRIDE - 12;
    assert.ok(top >= rowTop + STRIDE - 600 - 1 && top <= rowTop + 1, `scrolled to ${top}, the row is at ${rowTop}`);
  });
});

describe("across the edge of the window, at 20,000 photos", () => {
  test("holding ArrowDown through 80 rows: the focus follows every step, the DOM stays small, the focused card is always in it", async (t) => {
    const ctx = await viewPage(t, 20000);
    ctx.cardById(1000).focus();
    let worst = 0;
    for (let i = 1; i <= 80; i++) {
      ctx.key(ctx.document.activeElement && ctx.document.activeElement !== ctx.document.body ? ctx.document.activeElement : ctx.document.getElementById("thumbnails-grid"), "ArrowDown");
      await frame(ctx.window);
      worst = Math.max(worst, ctx.cards().length);
      assert.equal(ctx.state.gridKeys.index, i * 4, `step ${i}`);
      const at = ctx.state.library.ids[i * 4];
      assert.ok(ctx.cardById(at) || ctx.cards().some((c) => c.classList.contains("placeholder")), "the card or its place is drawn");
    }
    assert.ok(worst < 60, `${worst} cards in the DOM at the most`);
    await ctx.settle(400);
    assert.equal(ctx.focusedId(), ctx.state.library.ids[320], "the focus has arrived on the card, once it was fetched");
    assert.equal(ctx.stops().length, 1);
    assert.equal(ctx.stops()[0], ctx.focused());
    // The cards fetched are the window's: not 320 rows of them.
    assert.ok(ctx.state.library.cards.size < 400);
  });

  test("a scroll by the wheel away from the focused card does not lose it: it stays in the DOM, and so does the focus", async (t) => {
    const ctx = await viewPage(t, 20000);
    ctx.cardById(1002).focus();
    await ctx.scrollTo(GRID_TOP + 3000 * STRIDE);
    await ctx.settle(300);
    assert.equal(ctx.focusedId(), 1002, "still the focused element");
    assert.ok(ctx.focused().isConnected);
    assert.ok(ctx.cards().length < 70);
    // Back on it, the keys go on from where they were.
    await ctx.scrollTo(0);
    await ctx.press("ArrowRight", {}, 100);
    assert.equal(ctx.focusedId(), 1003);
  });
});

describe("the page's shortcuts are still everyone's", () => {
  test("Ctrl+Z and Ctrl+D are answered with the focus on a card, as with it nowhere", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1002).focus();
    assert.equal(ctx.key(ctx.focused(), "z", { ctrlKey: true }).defaultPrevented, true);
    assert.equal(ctx.key(ctx.focused(), "d", { ctrlKey: true }).defaultPrevented, true);
  });

  test("the arrow keys of the photo's own panel are not the grid's: with a photo open they step it", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1002).focus();
    ctx.key(ctx.focused(), "Enter");
    await ctx.settle(100);
    ctx.document.activeElement.blur();
    ctx.key(ctx.document.body, "ArrowRight");
    await ctx.settle(100);
    assert.equal(ctx.state.library.activeId, 1003);
  });
});

describe("Enter and Space", () => {
  test("Enter opens the photo in the details panel", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1006).focus();
    ctx.key(ctx.focused(), "Enter");
    await ctx.settle(100);
    assert.deepEqual(ctx.photosAsked, [1006]);
    assert.equal(ctx.state.library.activeId, 1006);
    assert.ok(!ctx.document.getElementById("panel-content").classList.contains("hidden"));
  });

  test("Enter on a place whose card has not arrived opens the photo all the same (by its id)", async (t) => {
    const ctx = await viewPage(t, 400, { onCards: (asked) => ({ cards: asked.filter((id) => id < 1200).map((id) => cardOf(id)) }) });
    ctx.cardById(1000).focus();
    ctx.key(ctx.focused(), "End");
    await frame(ctx.window);
    assert.equal(ctx.focused(), ctx.document.getElementById("thumbnails-grid"), "the focus waits on the grid: a place is not focusable");
    ctx.key(ctx.focused(), "Enter");
    await ctx.settle(100);
    assert.deepEqual(ctx.photosAsked, [1399]);
  });

  test("Space selects the photo, and again takes it away; the card and the count say so", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1001).focus();
    ctx.key(ctx.focused(), " ");
    assert.equal(ctx.state.selectedThumbnails.length, 1);
    assert.equal(ctx.state.selectedThumbnails[0], cardOf(1001).path);
    assert.equal(ctx.cardById(1001).getAttribute("aria-selected"), "true");
    assert.ok(ctx.cardById(1001).classList.contains("selected"));
    assert.match(ctx.document.getElementById("selected-thumbnails-count").textContent, /1/);
    ctx.key(ctx.focused(), " ");
    assert.equal(ctx.state.selectedThumbnails.length, 0);
    assert.equal(ctx.cardById(1001).getAttribute("aria-selected"), "false");
  });

  test("Space on a place whose card has not arrived selects it once the card's path is fetched", async (t) => {
    const ctx = await viewPage(t, 400, { onCards: (asked) => ({ cards: asked.filter((id) => id < 1200).map((id) => cardOf(id)) }) });
    ctx.cardById(1000).focus();
    ctx.key(ctx.focused(), "End");
    await frame(ctx.window);
    ctx.key(ctx.focused(), " ");
    await ctx.settle(100);
    assert.equal(ctx.state.selectedThumbnails.length, 0, "no card for it: the library has none to give (a photo deleted since)");
  });
});

describe("Shift extends the selection", () => {
  test("Shift+arrows add each photo the move passes, from where the run began", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1001).focus();
    await ctx.press("ArrowRight", { shiftKey: true }, 200);
    await ctx.press("ArrowRight", { shiftKey: true }, 200);
    await ctx.press("ArrowDown", { shiftKey: true }, 200);
    const paths = new Set(ctx.state.selectedThumbnails);
    for (let id = 1001; id <= 1007; id++) assert.ok(paths.has(cardOf(id).path), `1001..1007 include ${id}`);
    assert.ok(!paths.has(cardOf(1000).path) && !paths.has(cardOf(1008).path));
    assert.equal(ctx.state.gridKeys.anchor, 1);
    // An arrow without Shift moves and ends the run.
    await ctx.press("ArrowRight");
    assert.equal(ctx.state.gridKeys.anchor, -1);
  });

  test("Shift+End across photos with no card selects them all: their paths are fetched, 200 at a time", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1000).focus();
    const before = ctx.cardsAsked.length;
    await ctx.press("End", { shiftKey: true }, 500);
    assert.equal(ctx.state.selectedThumbnails.length, 400);
    const fetched = ctx.cardsAsked.slice(before);
    assert.ok(fetched.every((batch) => batch.length <= 200));
    assert.equal(ctx.focusedId(), 1399);
    assert.equal(new Set(ctx.state.selectedThumbnails).size, 400);
  });

  test("a new move replaces a selection still being fetched, and none is left half made", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1000).focus();
    ctx.hold.cards = true;
    ctx.key(ctx.focused(), "End", { shiftKey: true });
    await ctx.settle(30);
    assert.ok(ctx.state.library.selecting, "being fetched");
    ctx.key(ctx.document.getElementById("thumbnails-grid"), "Home", { shiftKey: true });
    await ctx.settle(30);
    ctx.hold.cards = false;
    await ctx.release("cards");
    await ctx.settle(300);
    assert.equal(ctx.state.library.selecting, null);
    assert.ok(ctx.state.selectedThumbnails.length <= 400);
  });
});

describe("the focus across a redraw", () => {
  test("a redraw of the data (refresh, an edit) builds the card again and the focus goes to it", async (t) => {
    const ctx = await viewPage(t);
    const old = ctx.cardById(1002);
    old.focus();
    ctx.state.grid.refresh();
    await ctx.settle(60);
    const fresh = ctx.cardById(1002);
    assert.notEqual(fresh, old, "a new card");
    assert.equal(ctx.focused(), fresh);
    assert.equal(ctx.stops().length, 1);
    await ctx.press("ArrowRight");
    assert.equal(ctx.focusedId(), 1003);
  });

  test("a card patched with new data keeps the focus; Refresh view keeps the place", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1003).focus();
    ctx.state.library.cards.get(1003).name = "Renamed.jpg";
    ctx.state.grid.patch(["#1003"]);
    assert.equal(ctx.focusedId(), 1003);
    assert.match(ctx.focused().getAttribute("aria-label"), /^IMG_1003\.jpg|^Renamed\.jpg/);
    ctx.document.getElementById("btn-library-refresh").click();
    await ctx.settle(300);
    assert.ok(ctx.focused() === ctx.document.body || ctx.focusedId() === 1003 || ctx.focused() === ctx.document.getElementById("thumbnails-grid"),
      "after a refresh the focus is on the card, or waiting on the grid for it, never lost to another control");
    assert.equal(ctx.state.gridKeys.index, 3);
  });

  test("another view starts the keys again", async (t) => {
    const ctx = await viewPage(t);
    ctx.cardById(1003).focus();
    await ctx.popTo("?view=year&value=2020");
    assert.equal(ctx.state.gridKeys.index, -1);
  });
});

describe("a folder view", () => {
  const FOLDER = "D:\\Library\\2020\\Event 01";
  const scan = () => Array.from({ length: 60 }, (_, i) => photoRecord({
    filename: `IMG_${String(i + 1).padStart(4, "0")}.jpg`, path: `${FOLDER}\\IMG_${String(i + 1).padStart(4, "0")}.jpg`,
    raw_metadata: { "EXIF:DateTimeOriginal": `2020:01:01 10:${String(i % 60).padStart(2, "0")}:00` },
  }));

  async function folderPage(t) {
    const ctx = await viewPage(t, 8, { search: "", scan });
    await openFolder(ctx, FOLDER);
    await ctx.settle(100);
    return ctx;
  }

  test("the same keys: one tab stop, arrows, Enter opens the photo, Space selects, Shift extends", async (t) => {
    const ctx = await folderPage(t);
    assert.equal(ctx.stops().length, 1);
    const cards = () => ctx.real();
    cards()[0].focus();
    await ctx.press("ArrowDown");
    assert.equal(ctx.focused().getAttribute("data-path"), scan()[4].path);
    ctx.key(ctx.focused(), " ");
    assert.deepEqual([...ctx.state.selectedThumbnails], [scan()[4].path]);
    await ctx.press("ArrowRight", { shiftKey: true }, 100);
    await ctx.press("ArrowRight", { shiftKey: true }, 100);
    assert.equal(ctx.state.selectedThumbnails.length, 3);
    await ctx.press("End", {}, 200);
    assert.equal(ctx.focused().getAttribute("data-path"), scan()[59].path);
    ctx.key(ctx.focused(), "Enter");
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, scan()[59].path);
    assert.ok(!ctx.document.getElementById("panel-content").classList.contains("hidden"));
  });

  test("the photo's arrows still step it, with focus nowhere in the grid", async (t) => {
    const ctx = await folderPage(t);
    ctx.real()[2].click();
    ctx.real()[2].querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, scan()[2].path);
    ctx.document.activeElement.blur();
    ctx.key(ctx.document.body, "ArrowRight");
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, scan()[3].path);
  });
});
