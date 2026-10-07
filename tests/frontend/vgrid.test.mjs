/**
 * The windowed grid (web/tagpup/vgrid.js), on its own: a source of records, a card builder, and
 * numbers for the layout, which jsdom does not have. What is held here is what 9b-2's library
 * source will lean on: only the rows in view (and two either side) are in the DOM, the padding
 * stands for the rest, a card that stays is the same element, a picture is asked for only by a
 * card that stays in view, and the place in the list survives a change of data or of size.
 */
import { test, describe, beforeEach, afterEach, mock } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { createVGrid } from "../../web/tagpup/vgrid.js";

// The clock is the test's (node:test's mock timers: setTimeout, setInterval -- jsdom's frames -- and Date). What is
// asserted is how long a card stayed in view, and a busy machine made a "fast scroll" of real frames slow enough for
// cards flown past to ask (#721). Time passes here only when a test says so.
const windows = [];
beforeEach(() => {
  mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"], now: 1_000_000 });
});
afterEach(() => {
  while (windows.length) windows.pop().close();
  mock.timers.reset();
});

/** One frame: jsdom draws every 1000/60 ms. */
const frame = async (win) => {
  const drawn = new Promise((resolve) => win.requestAnimationFrame(() => resolve()));
  mock.timers.tick(17);
  await drawn;
};
const wait = async (ms) => {
  mock.timers.tick(ms);
  await Promise.resolve();
};

/** A scroller and a grid in jsdom, a layout we control, and a vgrid over `records`. */
function setup({ records, layout = {}, options = {}, drawTime = 0 } = {}) {
  const dom = new JSDOM('<div id="s"><div id="g"></div></div>', { pretendToBeVisual: true });
  const win = dom.window;
  windows.push(win);
  const scroller = win.document.getElementById("s");
  const grid = win.document.getElementById("g");
  const here = { scrollTop: 0, viewport: 600, columns: 4, cardHeight: 200, rowGap: 16, gridTop: 100, cardWidth: 150, columnGap: 16, ...layout };
  Object.defineProperty(scroller, "scrollTop", { get: () => here.scrollTop, set: (v) => { here.scrollTop = v; }, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { get: () => here.viewport, configurable: true });
  const data = { list: records };
  const source = {
    count: () => data.list.length,
    recordAt: (i) => data.list[i],
  };
  const built = [];
  const view = createVGrid({
    container: grid,
    scroller,
    source,
    measure: () => ({ ...here }),
    buildCard: (record, index) => {
      built.push(record.id);
      if (drawTime) mock.timers.setTime(Date.now() + drawTime);   // a card that takes this long to build
      const card = win.document.createElement("div");
      card.className = "card";
      card.dataset.id = record.id;
      const img = win.document.createElement("img");
      img.dataset.src = `/pic/${record.id}`;
      card.appendChild(img);
      return card;
    },
    cardKey: (record) => record.id,
    imageDelay: 30,
    ...options,
  });
  const ids = () => [...grid.children].map((c) => c.dataset.id);
  const px = (name) => parseFloat(grid.style.getPropertyValue(name));
  const scrollTo = async (top) => {
    here.scrollTop = top;
    scroller.dispatchEvent(new win.Event("scroll"));
    await frame(win);
  };
  return { win, scroller, grid, here, data, view, built, ids, px, scrollTo };
}

const records = (n, prefix = "p") => Array.from({ length: n }, (_, i) => ({ id: `${prefix}${i}` }));

describe("the window", () => {
  test("20,000 records: a few dozen cards, and the padding stands for the rest", () => {
    const t = setup({ records: records(20000) });
    t.view.refresh();
    const stride = 216;
    assert.equal(t.ids().length, 20); // rows 0..4 of 4 cards: the 3 in view and 2 buffer rows
    assert.deepEqual(t.ids().slice(0, 2), ["p0", "p1"]);
    assert.equal(t.px("--vgrid-before"), 0);
    // The rows drawn, the padding before and after, are the height of all 5,000 rows.
    const rows = Math.ceil(t.ids().length / 4);
    assert.equal(t.px("--vgrid-before") + rows * stride - 16 + t.px("--vgrid-after"), 5000 * stride - 16);
  });

  test("scrolling moves the window, keeps the DOM bounded, and keeps the total height", async () => {
    const t = setup({ records: records(20000) });
    t.view.refresh();
    let peak = 0;
    for (const top of [0, 5000, 40000, 400000, 1079000, 1079000 + 600]) {
      await t.scrollTo(top);
      peak = Math.max(peak, t.grid.children.length);
      const seen = Math.floor((top - 100) / 216) * 4;
      const first = Number(t.ids()[0].slice(1));
      assert.ok(first <= Math.max(0, seen) && first >= seen - 2 * 4 - 4, `window at ${top}: first card p${first}, row ${seen / 4}`);
      const rows = Math.ceil(t.ids().length / 4);
      assert.equal(t.px("--vgrid-before") + rows * 216 - 16 + t.px("--vgrid-after"), 5000 * 216 - 16, `height at ${top}`);
    }
    assert.ok(peak <= 150, `${peak} cards in the DOM`);
    assert.equal(t.ids().at(-1), "p19999", "the last card is reachable");
  });

  test("a card that stays in the window is the same element; one that left is gone", async () => {
    const t = setup({ records: records(400) });
    t.view.refresh();
    const stay = t.grid.querySelector('[data-id="p12"]');
    const goes = t.grid.querySelector('[data-id="p0"]');
    await t.scrollTo(216 * 4);
    assert.equal(t.grid.querySelector('[data-id="p12"]'), stay);
    assert.equal(goes.isConnected, false);
    const before = t.built.length;
    await t.scrollTo(216 * 4 + 1);
    assert.equal(t.built.length, before, "a one-pixel scroll builds nothing");
  });

  test("the DOM is in index order whichever way it was scrolled to", async () => {
    const t = setup({ records: records(400) });
    t.view.refresh();
    for (const top of [4000, 100, 9000, 3000]) {
      await t.scrollTo(top);
      const numbers = t.ids().map((id) => Number(id.slice(1)));
      assert.deepEqual(numbers, [...numbers].sort((a, b) => a - b));
      assert.equal(new Set(numbers).size, numbers.length);
    }
  });

  test("an empty source shows the empty element; one photo shows one card", () => {
    const again = setup({ records: [], options: {} });
    const note = again.win.document.createElement("p");
    note.textContent = "No photos found matching filter.";
    again.view.destroy();
    again.view = createVGrid({
      container: again.grid, scroller: again.scroller, measure: () => ({ ...again.here }),
      source: { count: () => again.data.list.length, recordAt: (i) => again.data.list[i] },
      buildCard: (record) => { const d = again.win.document.createElement("div"); d.dataset.id = record.id; return d; },
      cardKey: (r) => r.id, empty: () => note,
    });
    again.view.refresh();
    assert.equal(again.grid.textContent, "No photos found matching filter.");
    again.data.list = [{ id: "only" }];
    again.view.refresh();
    assert.deepEqual(again.ids(), ["only"]);
    assert.equal(again.px("--vgrid-after"), 0);
    again.data.list = [];
    again.view.refresh();
    assert.equal(again.grid.textContent, "No photos found matching filter.");
  });

  test("N shrinking while scrolled past the new end shows the end", async () => {
    const t = setup({ records: records(2000) });
    t.view.refresh();
    await t.scrollTo(100000);
    t.data.list = records(40); // 10 rows: 2,160px
    t.view.refresh();
    assert.equal(t.ids().at(-1), "p39");
    assert.ok(t.ids().length > 0);
    assert.equal(t.px("--vgrid-after"), 0);
  });

  test("without a layout (jsdom, a hidden tab) the first 120 records are drawn, whole, with their pictures", () => {
    const t = setup({ records: records(500), layout: { viewport: 0 } });
    t.view.refresh();
    assert.equal(t.ids().length, 120);
    assert.equal(t.px("--vgrid-before"), 0);
    assert.equal(t.px("--vgrid-after"), 0);
    assert.ok([...t.grid.querySelectorAll("img")].every((img) => img.getAttribute("src") === `/pic/${img.closest(".card").dataset.id}`));
  });
});

describe("refresh and reset", () => {
  test("refresh draws the window again from the data and keeps the place", async () => {
    const t = setup({ records: records(400) });
    t.view.refresh();
    await t.scrollTo(216 * 20);
    const first = t.ids()[0];
    const old = t.grid.firstChild;
    t.data.list[Number(first.slice(1))].changed = true;
    t.view.refresh();
    assert.equal(t.ids()[0], first, "same place");
    assert.equal(t.here.scrollTop, 216 * 20, "the scroll position was not touched");
    assert.notEqual(t.grid.firstChild, old, "the cards were built again from the records");
  });

  test("reset starts at the top", async () => {
    const t = setup({ records: records(400) });
    t.view.refresh();
    await t.scrollTo(216 * 20);
    t.view.reset();
    assert.equal(t.here.scrollTop, 0);
    assert.equal(t.ids()[0], "p0");
  });

  test("a record that changed key is a new card; the old one is gone", () => {
    const t = setup({ records: records(10) });
    t.view.refresh();
    t.data.list[3] = { id: "renamed" };
    t.view.refresh();
    assert.equal(t.grid.querySelector('[data-id="p3"]'), null);
    assert.equal(t.view.cardFor("renamed").dataset.id, "renamed");
    assert.equal(t.view.indexOfKey("renamed"), 3);
    assert.equal(t.view.indexOfKey("p3"), -1);
  });

  test("two records of one key are both shown", () => {
    const t = setup({ records: [{ id: "x" }, { id: "x" }, { id: "y" }] });
    t.view.refresh();
    assert.equal(t.grid.children.length, 3);
  });
});

describe("a card with an editor open", () => {
  const busy = (card) => card.classList.contains("editing");

  test("is kept, out of the window, where it cannot be seen, and is not moved when it returns", async () => {
    const t = setup({ records: records(400), options: { isBusy: busy } });
    t.view.refresh();
    const card = t.view.cardFor("p1");
    card.classList.add("editing");
    await t.scrollTo(216 * 30);
    assert.equal(card.isConnected, true, "an editor is not taken from under the person typing");
    assert.equal(card.style.position, "absolute");
    assert.equal(t.grid.firstChild, card, "above the window, so first");
    assert.ok(t.grid.children.length < 40);
    // Refreshing while it is out of the window does not rebuild it.
    t.view.refresh();
    assert.equal(t.view.cardFor("p1"), card);
    // Back in the window: the same element, in place, no longer parked.
    await t.scrollTo(0);
    assert.equal(t.view.cardFor("p1"), card);
    assert.equal(card.style.position, "");
    assert.equal(t.ids()[1], "p1");
    assert.equal(card.parentNode, t.grid);
  });

  test("a card below the window is last; one whose record is gone is released", async () => {
    const t = setup({ records: records(400), options: { isBusy: busy } });
    t.view.refresh();
    await t.scrollTo(216 * 30);
    const far = t.view.cardFor("p125");
    far.classList.add("editing");
    await t.scrollTo(0);
    assert.equal(t.grid.lastChild, far);
    assert.equal(far.style.position, "absolute");
    t.data.list = t.data.list.filter((r) => r.id !== "p125");
    t.view.refresh();
    assert.equal(far.isConnected, false);
  });

  test("a refresh keeps the card being typed in, and rebuilds the rest", () => {
    const t = setup({ records: records(10), options: { isBusy: busy } });
    t.view.refresh();
    const typing = t.view.cardFor("p2");
    const other = t.view.cardFor("p3");
    typing.classList.add("editing");
    t.view.refresh();
    assert.equal(t.view.cardFor("p2"), typing);
    assert.notEqual(t.view.cardFor("p3"), other);
  });
});

describe("pictures", () => {
  test("a card asks for its picture only after it has stayed in view", async () => {
    const t = setup({ records: records(400) });
    t.view.refresh();
    const src = () => [...t.grid.querySelectorAll("img")].filter((i) => i.getAttribute("src")).length;
    assert.equal(src(), 0, "nothing is asked for at once");
    await wait(80);
    assert.ok(src() > 0, "the cards in view asked");
    assert.ok(src() <= 4 * 5, "and only those in view and a row either side");
  });

  test("a slow draw is not the view standing still: nothing is asked for until it has stood after the draw", async () => {
    // 20 cards of 10 ms: the draw takes 200 ms, past the delay (30 ms). Timed from before the draw, the row below the
    // view asked at once (#721: what failed under load).
    const t = setup({ records: records(400), drawTime: 10 });
    t.view.refresh();
    assert.equal(t.grid.querySelectorAll("img[src]").length, 0, "nothing at once");
    await wait(29);
    assert.equal(t.grid.querySelectorAll("img[src]").length, 0, "nor before the delay is up");
    await wait(1);
    assert.ok(t.grid.querySelectorAll("img[src]").length > 0, "then they ask");
  });

  test("cards flown past never ask; the ones recycled out cancel what they asked", async () => {
    const t = setup({ records: records(2000) });
    t.view.refresh();
    const asked = new Set();
    const watch = () => t.grid.querySelectorAll("img[src]").forEach((i) => asked.add(i.dataset.src));
    await wait(60);
    watch();
    const firstScreen = asked.size;
    assert.ok(firstScreen > 0);
    const gone = [...t.grid.querySelectorAll("img")];
    // A fast scroll: a new place every frame, none stays for 30 ms.
    for (let i = 1; i <= 20; i++) {
      await t.scrollTo(i * 4000);
      watch();
    }
    assert.equal(asked.size, firstScreen, "nothing flown past asked for its picture");
    assert.ok(gone.every((i) => !i.getAttribute("src")), "what the first screen was still fetching is cancelled");
    await wait(80);
    watch();
    assert.ok(asked.size > firstScreen, "where the scroll stopped, they ask");
  });

  test("a card the view left since the last frame does not ask (a slow frame must not look like a pause)", async () => {
    const t = setup({ records: records(2000) });
    t.view.refresh();
    t.here.scrollTop = 100000; // scrolled; the scroll event, and the frame that draws it, are late
    await wait(80);
    assert.equal(t.grid.querySelectorAll("img[src]").length, 0);
  });

  test("the row either side of the view waits for the view to stand still", async () => {
    const t = setup({ records: records(2000) });
    t.view.refresh();
    await wait(60);
    const asked = (i) => t.view.cardFor(`p${i}`).querySelector("img").getAttribute("src");
    // View: rows 0-2 (photos 0-11); the margin is row 3 (12-15). Both ask once the view has stood.
    assert.ok(asked(0) && asked(14));
    // Scroll by a row; the new margin row (16-19) is not asked for until the view stands again.
    await t.scrollTo(100 + 216);
    assert.equal(asked(18), null);
    await wait(60);
    assert.ok(asked(18));
  });

  test("a card drawn again by refresh shows the picture it had at once; one scrolled back to waits like any", async () => {
    const t = setup({ records: records(400) });
    t.view.refresh();
    await wait(60);
    for (const img of t.grid.querySelectorAll("img[src]")) img.dispatchEvent(new t.win.Event("load"));
    t.view.refresh();
    assert.equal(t.view.cardFor("p0").querySelector("img").getAttribute("src"), "/pic/p0", "no flash of grey");
    await t.scrollTo(216 * 40);
    await t.scrollTo(0);
    assert.equal(t.view.cardFor("p0").querySelector("img").getAttribute("src"), null, "scrolled back to: it waits to stay");
    await wait(60);
    assert.equal(t.view.cardFor("p0").querySelector("img").getAttribute("src"), "/pic/p0");
  });

  test("a picture that failed to load is not asked for again by the same card", async () => {
    const t = setup({ records: records(10) });
    t.view.refresh();
    await wait(60);
    const img = t.view.cardFor("p0").querySelector("img");
    img.dispatchEvent(new t.win.Event("error"));
    img.removeAttribute("src");
    await t.scrollTo(1);
    await wait(60);
    assert.equal(img.getAttribute("src"), null);
  });
});

describe("a change of layout", () => {
  test("a size change keeps the same photo near the top", async () => {
    const t = setup({ records: records(2000) });
    t.view.refresh();
    await t.scrollTo(100 + 216 * 100 + 50); // row 100, 50px into it: photos 400..403
    assert.equal(t.view.indexOfKey("p400"), 400);
    // Large: two columns, taller cards.
    Object.assign(t.here, { columns: 2, cardHeight: 380, rowGap: 20 });
    t.view.relayout();
    const row = Math.floor(400 / 2);
    assert.ok(Math.abs(t.here.scrollTop - (100 + (row + 50 / 216) * 400)) < 1e-6, String(t.here.scrollTop));
    assert.ok(t.ids().includes("p400"));
    assert.equal(t.px("--vgrid-before") + Math.ceil(t.ids().length / 2) * 400 - 20 + t.px("--vgrid-after"), 1000 * 400 - 20);
  });

  test("a narrower grid (the sidebar dragged, the browser zoomed) is read again", async () => {
    const observers = [];
    const dom = new JSDOM('<div id="s"><div id="g"></div></div>', { pretendToBeVisual: true });
    windows.push(dom.window);
    dom.window.ResizeObserver = class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe() {}
      disconnect() {}
    };
    const scroller = dom.window.document.getElementById("s");
    const grid = dom.window.document.getElementById("g");
    const here = { scrollTop: 0, viewport: 600, columns: 6, cardHeight: 200, rowGap: 16, gridTop: 100, cardWidth: 150, columnGap: 16 };
    Object.defineProperty(scroller, "scrollTop", { get: () => here.scrollTop, set: (v) => { here.scrollTop = v; }, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { get: () => here.viewport, configurable: true });
    const list = records(3000);
    const view = createVGrid({
      container: grid, scroller, source: { count: () => list.length, recordAt: (i) => list[i] },
      measure: () => ({ ...here }),
      buildCard: (record) => { const d = dom.window.document.createElement("div"); d.dataset.id = record.id; return d; },
      cardKey: (r) => r.id,
    });
    view.refresh();
    here.scrollTop = 100 + 216 * 50; // row 50 of 6 columns: photo 300
    scroller.dispatchEvent(new dom.window.Event("scroll"));
    view.render();
    const [observer] = observers;
    observer.callback([{ target: grid, contentRect: { width: 900 } }]); // first reading
    Object.assign(here, { columns: 4, cardHeight: 230 });
    observer.callback([{ target: grid, contentRect: { width: 640 } }]);
    assert.equal(here.scrollTop, 100 + (75) * 246, "photo 300 is row 75 of 4 columns");
    assert.ok([...grid.children].some((c) => c.dataset.id === "p300"));
  });
});

describe("hidden, then shown again", () => {
  test("the grid comes back at the place it was left", async () => {
    const t = setup({ records: records(2000) });
    t.view.refresh();
    await t.scrollTo(216 * 200);
    // Hidden (display: none): no height, and the browser forgot the scroll position.
    t.here.viewport = 0;
    t.here.scrollTop = 0;
    const drawn = t.ids();
    t.view.refresh();
    assert.deepEqual(t.ids(), drawn, "while hidden the window stays as it was drawn");
    t.here.viewport = 600;
    t.view.refresh();
    assert.equal(t.here.scrollTop, 216 * 200);
    assert.ok(t.ids().includes("p800"));
    assert.ok(t.grid.children.length < 40);
  });
});

describe("what the source offers", () => {
  test("indexOfKey asks the source when it can, and scans when it cannot", () => {
    const t = setup({ records: records(50) });
    assert.equal(t.view.indexOfKey("p33"), 33);
    assert.equal(t.view.indexOfKey("nope"), -1);
    t.view.setSource({ count: () => 2, recordAt: (i) => ({ id: `q${i}` }), indexOfKey: (k) => (k === "q1" ? 7 : -1) });
    assert.equal(t.view.indexOfKey("q1"), 7);
  });

  test("scrollToIndex brings a record into view, and eachCard reaches the cards drawn", async () => {
    const t = setup({ records: records(2000) });
    t.view.refresh();
    t.view.scrollToIndex(1000);
    assert.ok(t.view.cardFor("p1000"));
    assert.equal(t.here.scrollTop, 100 + 250 * 216 + 216 - 600, "the row's bottom edge at the view's");
    t.view.scrollToIndex(1000, "start");
    assert.equal(t.here.scrollTop, 100 + 250 * 216);
    let seen = 0;
    t.view.eachCard((card, record, index) => {
      seen++;
      assert.equal(record.id, `p${index}`);
    });
    assert.equal(seen, t.grid.children.length);
  });
});

describe("what the keys of a page lean on (phase 9c)", () => {
  /** A card that can hold the focus. */
  const focusable = (win, record) => {
    const card = win.document.createElement("div");
    card.className = "card";
    card.dataset.id = record.id;
    card.tabIndex = -1;
    return card;
  };

  test("afterDraw is called after every draw, with the cards in the DOM", async () => {
    const seen = [];
    const t = setup({ records: records(400), options: { afterDraw: () => seen.push(t.grid.children.length) } });
    t.view.refresh();
    const first = seen.length;
    assert.ok(first >= 1 && seen.at(-1) > 0);
    await t.scrollTo(216 * 8);
    assert.ok(seen.length > first, "and after a scroll's draw");
  });

  test("the card with the focus is not taken from under it: out of the window it is kept, out of sight, and the focus stays", async () => {
    const holder = {};
    const t = setup({ records: records(400), options: { buildCard: (record) => focusable(holder.win, record) } });
    holder.win = t.win;
    t.view.refresh();
    const card = t.grid.querySelector('[data-id="p2"]');
    card.focus();
    assert.equal(t.win.document.activeElement, card);
    await t.scrollTo(216 * 40);
    assert.equal(card.isConnected, true, "kept");
    assert.equal(t.win.document.activeElement, card, "and still focused");
    assert.equal(card.style.position, "absolute", "laid out of the way, as a card being typed in is");
    assert.ok(t.grid.children.length < 40);
    // Focus moved elsewhere: the card is let go at the next draw.
    t.grid.querySelector('[data-id="p160"]').focus();
    await t.scrollTo(216 * 40 + 1);
    assert.equal(card.isConnected, false);
  });

  test("a card rebuilt for changed data gives the focus to the one built in its place", () => {
    const holder = {};
    const t = setup({ records: records(100), options: { buildCard: (record) => focusable(holder.win, record) } });
    holder.win = t.win;
    t.view.refresh();
    const old = t.grid.querySelector('[data-id="p5"]');
    old.focus();
    t.view.refresh();
    const fresh = t.grid.querySelector('[data-id="p5"]');
    assert.notEqual(fresh, old);
    assert.equal(t.win.document.activeElement, fresh);
  });

  test("topInset: a card scrolled to is put below what sticks over the top of the scroller", () => {
    const t = setup({ records: records(2000), options: { topInset: () => 60 } });
    t.view.refresh();
    t.view.scrollToIndex(1000, "start");
    assert.equal(t.here.scrollTop, 100 + 250 * 216 - 60);
    t.view.scrollToIndex(0, "start");
    assert.equal(t.here.scrollTop, 40, "the first row, with the inset above it");
    t.view.scrollToIndex(1000);
    assert.equal(t.here.scrollTop, 100 + 250 * 216 - 600 + 216, "the row's bottom edge at the view's, as without an inset");
  });

  test("geometry: the columns, and the rows in view; none before anything is drawn", () => {
    const t = setup({ records: records(100) });
    assert.equal(t.view.geometry(), null);
    t.view.refresh();
    assert.deepEqual(t.view.geometry(), { columns: 4, rows: 2 });
  });
});
