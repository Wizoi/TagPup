/**
 * A view of the library in the grid (web/tagpup/library-view.js, library-source.js; phase 9b-2): the whole
 * library, a year, a keyword, a person or a folder, from the library's order (GET /api/library/ids) and
 * the cards of the photos near the window (GET /api/library/cards), in the same grid, details panel and
 * selection as a folder.
 *
 * jsdom has no layout, so the page is given one, as grid-window.test.mjs does: a 600 px view, four
 * columns of 200 px cards, a 16 px gap. The network is the harness's FakeServer, which can hold a reply
 * back, so a request that is still out when the view moves on is a thing a test can look at. The library
 * is 68,000 photos of invented ids; no name is a real one.
 */
import { test, describe, afterEach, mock } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports,
} from "./harness.mjs";

afterEach(() => closeAllApps());

const N = 68000;
const GRID_TOP = 100;
const STRIDE = 216;
const COLUMNS = 4;
const IDS = Array.from({ length: N }, (_, i) => 5000 + i * 3);   // not 0..N: an id is not an index
const FOLDER = "D:\\Library\\2020";

const cardOf = (id) => ({
  id, name: `IMG_${id}.jpg`, path: `D:\\Library\\Photos\\IMG_${id}.jpg`, taken: "2020:01:01 10:00:00",
  damaged: false, damage: null, thumb: `/api/photo-thumb?id=${id}&v=1600000000.0`,
});
const recordOf = (id) => photoRecord({
  id, filename: `IMG_${id}.jpg`, path: `D:\\Library\\Photos\\IMG_${id}.jpg`, tags: ["Trips/Coast"], people: [],
  taken: "2020:01:01 10:00:00",
});
const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
const frame = (window) => new Promise((resolve) => window.requestAnimationFrame(() => resolve()));

/** The page on a library, a view named by its address, a layout for the grid, and a server that holds what it is told. */
async function load(t, { search = "?view=all", ids = IDS, total, onIds, onCards, onPhoto, scan, layout = true, rootsProblem } = {}) {
  const server = new FakeServer();
  const ctx = { held: [], idsAsked: [], cardsAsked: [], photosAsked: [], server };
  ctx.hold = false;
  const answer = (make) => (url) => {
    const reply = make(url);
    if (!ctx.hold) return reply;
    return new Promise((resolve) => ctx.held.push(() => resolve(reply)));
  };
  server
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index", "other"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/membership", { photos_not_held: 0 })
    .on("/api/folder/damaged", { photos: [] })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/photo/delete", { success: true })
    .on("/api/photos/bulk-tags", { success: true })
    .on("/api/folder/scan", () => (scan ? scan() : [photoRecord({ filename: "a.jpg" }), photoRecord({ filename: "b.jpg" })]));
  if (rootsProblem) {
    server.first("/api/library/", { error: "This computer does not know where this library keeps its photos." },
      { status: 409, headers: { "X-TagPup-Roots-Problem": "1" } });
  }
  server.on("/api/library/ids", answer((url) => {
    ctx.idsAsked.push(url);
    if (onIds) return onIds(url);
    return { source: {}, total: total ?? ids.length, ids, complete: true };
  }));
  server.on("/api/library/cards", answer((url) => {
    const asked = decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").map(Number);
    ctx.cardsAsked.push(asked);
    if (onCards) return onCards(asked, url);
    return { cards: asked.map(cardOf) };
  }));
  server.on("/api/library/photo", (url) => {
    const id = Number((url.match(/id=(\d+)/) || [])[1]);
    ctx.photosAsked.push(id);
    return onPhoto ? onPhoto(id) : { photo: recordOf(id) };
  });
  const loaded = await loadApp("tagpup", { t, url: `http://localhost:8090/photo_index/${search}`, server });
  Object.assign(ctx, loaded);
  ctx.window.alert = () => {};
  ctx.window.confirm = () => true;
  const { state } = pageExports(ctx.window, "web/tagpup/state.js");
  ctx.state = state;
  const scroller = ctx.document.getElementById("folder-view-main");
  const content = ctx.document.getElementById("folder-view-content");
  const here = { scrollTop: 0, viewport: 600, columns: COLUMNS, cardHeight: 200, rowGap: 16, gridTop: GRID_TOP, cardWidth: 150, columnGap: 16 };
  Object.defineProperty(scroller, "scrollTop", { get: () => here.scrollTop, set: (v) => { here.scrollTop = v; }, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { get: () => (content.classList.contains("hidden") ? 0 : here.viewport), configurable: true });
  if (layout) {
    state.grid.setMeasure(() => {
      if (content.classList.contains("hidden")) {
        here.scrollTop = 0;
        return { ...here, viewport: 0 };
      }
      return { ...here };
    });
    state.grid.refresh();
  }
  await flush(ctx.window, 4);
  Object.assign(ctx, {
    here, scroller,
    cards: () => [...ctx.document.querySelectorAll("#thumbnails-grid .thumbnail-card")],
    real: () => ctx.cards().filter((c) => !c.classList.contains("placeholder")),
    holes: () => ctx.cards().filter((c) => c.classList.contains("placeholder")),
    cardById: (id) => ctx.document.querySelector(`#thumbnails-grid [data-id="${id}"]`),
    scrollTo: async (top) => {
      here.scrollTop = top;
      scroller.dispatchEvent(new ctx.window.Event("scroll"));
      await frame(ctx.window);
    },
    scrollToIndex: (i) => ctx.scrollTo(GRID_TOP + Math.floor(i / COLUMNS) * STRIDE),
    strip: () => ctx.document.getElementById("library-strip"),
    stripText: () => ctx.strip().textContent.replace(/\s+/g, " ").trim(),
    label: () => ctx.document.getElementById("selected-thumbnails-count").textContent,
    selectedIds: () => {
      const { sel, ids } = ctx.state.library;
      return sel.mode === "ids" ? ids.filter((id) => sel.ids.has(id)) : ids.filter((id) => !sel.excluded.has(id));
    },
    release: async () => {
      const waiting = ctx.held.splice(0);
      waiting.forEach((go) => go());
      await flush(ctx.window, 6);
    },
    settle: async (ms = 160) => { await wait(ctx.window, ms); await flush(ctx.window, 6); },
    folderCalls: () => server.urls().filter((u) => u.includes("/api/folder/")),
  });
  await ctx.settle();
  return ctx;
}

describe("opening a view from the address", () => {
  test("the whole library: the order is asked for once, the strip says what is open, no list is built", async (t) => {
    const ctx = await load(t);
    assert.equal(ctx.idsAsked.length, 1);
    assert.match(ctx.idsAsked[0], /\/photo_index\/api\/library\/ids\?kind=all$/);
    assert.match(ctx.stripText(), /The whole library/);
    assert.match(ctx.stripText(), /68,?000 photos/);
    assert.ok(!ctx.strip().classList.contains("hidden"));
    assert.equal(ctx.document.querySelectorAll("#photo-list .photo-item-file").length, 0, "no row for each of 68,000 photos");
    assert.equal(ctx.state.library.ids.length, N);
    assert.match(ctx.document.getElementById("folder-view-stats").textContent, /68,?000 photos/);
  });

  test("only the window has cards: not one request for the other 67,900", async (t) => {
    const ctx = await load(t);
    assert.ok(ctx.real().length > 0 && ctx.cards().length < 60, `${ctx.cards().length} cards in the DOM`);
    const asked = ctx.cardsAsked.flat();
    // (The page is first drawn with no layout, as jsdom has none: its first 120 records, before the layout is given.)
    assert.ok(asked.length > 0 && asked.length < 250, `${asked.length} cards asked for`);
    assert.ok(asked.every((id) => IDS.indexOf(id) < 250), "the first photos of the order, nothing else");
    assert.ok(ctx.state.library.cards.size < 250);
    const first = ctx.real()[0];
    assert.equal(first.getAttribute("data-id"), String(IDS[0]));
    assert.equal(first.getAttribute("data-path"), cardOf(IDS[0]).path);
  });

  test("a card shows the photo's name and date, and its thumbnail is asked for only once the card stays in view", async (t) => {
    const ctx = await load(t);
    const card = ctx.cardById(IDS[0]);
    assert.equal(card.querySelector(".thumbnail-filename").textContent, `IMG_${IDS[0]}`);
    assert.match(card.querySelector(".thumbnail-date").textContent, /2020|01\/01/);
    const img = card.querySelector("img");
    assert.equal(img.dataset.src, `/photo_index/api/photo-thumb?id=${IDS[0]}&v=1600000000.0`);
    await ctx.settle(200);
    assert.ok(ctx.cardById(IDS[0]).querySelector("img").getAttribute("src"), "asked for after 120 ms in view");
    const far = ctx.cards().find((c) => c.getAttribute("data-id") === String(IDS[40]));
    if (far) assert.ok(!far.querySelector("img").getAttribute("src"), "a card in the buffer waits");
  });

  test("the folder's machinery is idle: nothing of /api/folder/ is asked, nothing is cached", async (t) => {
    const ctx = await load(t);
    assert.deepEqual(ctx.folderCalls(), []);
    assert.equal(ctx.state.scannedFolder, null);
    const kept = Object.keys(ctx.window.localStorage);
    assert.deepEqual(kept.filter((key) => key.startsWith("tagpup_cache_")), [], "no scan of a folder is kept for a view");
    assert.equal(ctx.document.getElementById("add-folder-modal").classList.contains("active"), false);
    assert.equal(ctx.document.getElementById("btn-suggest-tags").disabled, true);
    assert.ok(ctx.document.getElementById("btn-toggle-rename").classList.contains("hidden"), "Smart Rename is Organize's (#669)");
    assert.ok(ctx.document.getElementById("btn-toggle-timeshift").classList.contains("hidden"), "and so is the time shift (#669)");
    assert.equal(ctx.document.getElementById("photo-search").disabled, true);
    assert.match(ctx.document.getElementById("photo-search").title, /Library pane’s search box/);
  });

  test("a ?view wins over the remembered ?path", async (t) => {
    const ctx = await load(t, { search: "?path=D%3A%5CLibrary%5C2020&view=year&value=2020", total: 3, ids: [1, 2, 3] });
    assert.deepEqual(ctx.folderCalls(), []);
    assert.match(ctx.idsAsked[0], /kind=year&value=2020/);
    assert.match(ctx.stripText(), /Photos of 2020/);
  });

  test("each kind asks as the route is spelled; a folder by `folder`", async (t) => {
    const cases = [
      ["?view=month&value=2024-06", /kind=month&value=2024-06$/, /June 2024/],
      ["?view=keyword&value=Trips%2FCoast", /kind=keyword&value=Trips%2FCoast$/, /Keyword Trips\/Coast/],
      ["?view=person&value=Wren%20Halloway", /kind=person&value=Wren%20Halloway$/, /Wren Halloway/],
      ["?view=folder&value=D%3A%5CLibrary%5C2020&recursive=1", /kind=folder&folder=D%3A%5CLibrary%5C2020&recursive=1$/, /subfolders/],
      ["?view=folder&value=D%3A%5CLibrary%5C2020", /kind=folder&folder=D%3A%5CLibrary%5C2020&recursive=0$/, /Folder D:/],
    ];
    for (const [search, ask, say] of cases) {
      const ctx = await load(t, { search, ids: [7, 8], total: 2 });
      assert.match(ctx.idsAsked[0], ask, search);
      assert.match(ctx.stripText(), say, search);
    }
  });
});

describe("an address, or a library, that gives no view", () => {
  test("a kind that is none, a missing or oversized value, a bad year: a sentence, no request, no error", async (t) => {
    for (const search of ["?view=bogus", "?view=keyword", "?view=year&value=abc", "?view=month&value=2024-13",
      `?view=person&value=${"x".repeat(5000)}`]) {
      const ctx = await load(t, { search });
      assert.equal(ctx.idsAsked.length, 0, search);
      assert.ok(ctx.stripText().length > 20, search);
      assert.equal(ctx.cards().filter((c) => !c.classList.contains("placeholder")).length, 0);
      assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /does not know|without saying|too long|written as/, search);
      assert.deepEqual(ctx.consoleErrors, [], search);
    }
  });

  test("a keyword with no node is an empty view that says so", async (t) => {
    const ctx = await load(t, { search: "?view=keyword&value=No%2FSuch", ids: [], total: 0 });
    assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /No photo carries the keyword/);
    assert.match(ctx.stripText(), /0 photos/);
  });

  test("a library behind (the route's 409 sentence) is shown, not a trace", async (t) => {
    const ctx = await load(t);
    ctx.server.first("/api/library/ids", { error: "kr-track has not been brought up to date for browsing." }, { status: 409 });
    ctx.window.history.replaceState({}, "", "/photo_index/?view=year&value=2021");
    ctx.window.dispatchEvent(new ctx.window.PopStateEvent("popstate", { state: {} }));
    await ctx.settle();
    assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /has not been brought up to date/);
    assert.match(ctx.stripText(), /has not been brought up to date/);
  });

  test("a root this computer does not place: the banner, and the view says why it is empty", async (t) => {
    const ctx = await load(t, { rootsProblem: true });
    const banner = ctx.document.getElementById("roots-banner");
    assert.ok(!banner.classList.contains("hidden"), "the existing banner is shown");
    assert.match(ctx.document.getElementById("thumbnails-grid").textContent, /does not know where this library keeps/);
  });

  test("a source of 0 photos and a source of 1", async (t) => {
    const none = await load(t, { search: "?view=year&value=1999", ids: [], total: 0 });
    assert.match(none.document.getElementById("thumbnails-grid").textContent, /The library holds no photo of the year 1999/);
    assert.equal(none.cards().length, 0, "no card, only the sentence");
    const one = await load(t, { search: "?view=person&value=Wren%20Halloway", ids: [42], total: 1 });
    assert.equal(one.real().length, 1);
    assert.match(one.stripText(), /1 photo\b/);
    await one.scrollTo(GRID_TOP + 5000);
    assert.equal(one.real().length, 1, "the end of one is its one");
  });
});

describe("the order and the cards, scrolled", () => {
  test("the scroll bar dragged to the end: placeholders first, then the last photos' cards", async (t) => {
    const ctx = await load(t);
    const rows = Math.ceil(N / COLUMNS);
    await ctx.scrollTo(GRID_TOP + (rows - 3) * STRIDE);
    assert.ok(ctx.holes().length > 0, "placeholders where the cards are not here yet");
    assert.ok(ctx.holes().every((c) => !c.getAttribute("data-path")), "nothing to click on a placeholder");
    await ctx.settle();
    const last = ctx.cardById(IDS[N - 1]);
    assert.ok(last, "the last photo has a card");
    assert.equal(last.getAttribute("data-path"), cardOf(IDS[N - 1]).path);
    assert.equal(ctx.holes().length, 0);
  });

  test("a fast scroll through the middle asks for nothing it has left behind", async (t) => {
    const ctx = await load(t);
    ctx.cardsAsked.length = 0;
    // Thirty windows, a frame (17 ms) apiece, on the test's clock (node:test's mock timers: the page's setTimeout and
    // jsdom's frames). On the real one a frame took as long as the machine was busy, and under load the scroll paused
    // long enough to be answered (#721).
    mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    try {
      const turn = () => new Promise((resolve) => setImmediate(resolve));
      for (let i = 1; i <= 30; i++) {
        ctx.here.scrollTop = GRID_TOP + Math.floor((2000 * i) / COLUMNS) * STRIDE;
        ctx.scroller.dispatchEvent(new ctx.window.Event("scroll"));
        mock.timers.tick(17);
        await turn();
      }
      for (let n = 0; n < 40; n++) {   // then it stands still
        mock.timers.tick(20);
        await turn();
      }
    } finally {
      mock.timers.reset();
    }
    await flush(ctx.window, 6);
    const asked = ctx.cardsAsked.flat();
    assert.ok(ctx.cardsAsked.length <= 3, `${ctx.cardsAsked.length} requests for thirty windows`);
    assert.ok(asked.every((id) => IDS.indexOf(id) >= 59000), "none for a window it flew past");
    const last = IDS.indexOf(Math.max(...asked));
    assert.ok(last >= 59000 && last <= 61000, "and for the one it stopped at");
    assert.ok(ctx.cardById(IDS[60000]), "which is drawn");
  });

  test("a batch for a window that was left is cancelled and never painted", async (t) => {
    const ctx = await load(t);
    ctx.hold = true;
    await ctx.scrollToIndex(20000);
    await ctx.settle();
    const firstAsk = ctx.cardsAsked.at(-1);
    assert.ok(firstAsk && IDS.indexOf(firstAsk[0]) >= 19000, "asked for the window it was at");
    await ctx.scrollToIndex(50000);
    await ctx.settle();
    ctx.hold = false;
    await ctx.release();
    await ctx.settle();
    assert.equal(ctx.cardById(IDS[20000]), null, "the old window's cards were not painted");
    assert.ok(ctx.cardById(IDS[50000]), "the new window's were");
    assert.equal(ctx.state.library.cards.has(IDS[20000]), false, "nor kept");
  });

  test("the cards held are bounded: the far ones go", async (t) => {
    const ctx = await load(t);
    for (let i = 1; i <= 45; i++) {
      await ctx.scrollToIndex(1000 * i);
      await ctx.settle(130);
    }
    assert.ok(ctx.state.library.cards.size <= 2000, `${ctx.state.library.cards.size} cards held`);
    assert.ok(ctx.state.library.cards.size > 20);
    assert.equal(ctx.state.library.cards.has(IDS[0]), false, "the first window was let go long ago");
  });

  test("the ids are one array, kept as they are while cards come and go", async (t) => {
    const ctx = await load(t);
    const ids = ctx.state.library.ids;
    await ctx.scrollToIndex(30000);
    await ctx.settle();
    assert.equal(ctx.state.library.ids, ids);
    assert.equal(ids.length, N);
  });

  test("a batch that fails leaves placeholders, is tried once more, and then gives up", async (t) => {
    const ctx = await load(t);
    ctx.server.first("/api/library/cards", { error: "the library is busy" }, { status: 500 });
    await ctx.scrollToIndex(10000);
    await ctx.settle();
    const afterFirst = ctx.server.urls().filter((u) => u.includes("/api/library/cards")).length;
    await ctx.scrollToIndex(10000);
    await ctx.settle();
    const afterSecond = ctx.server.urls().filter((u) => u.includes("/api/library/cards")).length;
    await ctx.scrollToIndex(10000);
    await ctx.settle();
    await ctx.scrollToIndex(10001);
    await ctx.settle();
    const afterThird = ctx.server.urls().filter((u) => u.includes("/api/library/cards")).length;
    assert.ok(afterSecond > afterFirst, "the next scroll asks again, once");
    assert.equal(afterThird, afterSecond, "and then not at all: no endless loop");
    assert.ok(ctx.holes().length > 0, "the placeholders stay");
    assert.ok(ctx.document.querySelector(".thumbnail-card.placeholder.failed"), "and say they gave up");
    assert.match(ctx.stripText(), /could not be loaded/i);
    assert.deepEqual(ctx.consoleErrors.filter((e) => String(e).includes("Uncaught")), []);
  });

  test("a photo the library no longer has when its card is asked for is dropped, quietly", async (t) => {
    const gone = IDS[2];
    const ctx = await load(t, { onCards: (asked) => ({ cards: asked.filter((id) => id !== gone).map(cardOf) }) });
    assert.equal(ctx.state.library.ids.includes(gone), false);
    assert.equal(ctx.state.library.total, N - 1);
    assert.equal(ctx.cardById(gone), null);
    assert.match(ctx.stripText(), /67,?999 photos/);
    assert.equal(ctx.real().length > 5, true);
  });

  test("a right-click on a placeholder names no photo, and Open does nothing", async (t) => {
    const ctx = await load(t);
    ctx.hold = true;
    await ctx.scrollToIndex(40000);
    const hole = ctx.holes()[0];
    assert.ok(hole);
    hole.dispatchEvent(new ctx.window.MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    ctx.document.querySelector('#grid-context-menu [data-action="open"]').click();
    ctx.document.querySelector('#grid-context-menu [data-action="select-range"]').click();
    await ctx.settle();
    assert.equal(ctx.photosAsked.length, 0);
    assert.equal(ctx.state.activePhotoPath, null);
    assert.equal(ctx.selectedIds().length, 0);
    ctx.hold = false;
    await ctx.release();
  });

  test("damaged and incomplete photos are marked from the card, with no folder list", async (t) => {
    const ctx = await load(t, {
      onCards: (asked) => ({ cards: asked.map((id) => ({
        ...cardOf(id), damaged: id === IDS[0] || id === IDS[1], damage: id === IDS[0] ? "truncated" : id === IDS[1] ? "incomplete" : null,
      })) }),
    });
    const broken = ctx.cardById(IDS[0]);
    assert.ok(broken.classList.contains("damaged"));
    assert.equal(broken.querySelector("img"), null, "nothing to ask for");
    assert.match(broken.textContent, /Can't be read/);
    const incomplete = ctx.cardById(IDS[1]);
    assert.ok(incomplete.classList.contains("damaged"));
    assert.ok(incomplete.querySelector("img"), "an incomplete copy still has its picture");
    assert.match(incomplete.textContent, /Incomplete/);
    assert.equal(ctx.cardById(IDS[2]).classList.contains("damaged"), false);
  });
});

describe("selecting in a view", () => {
  test("a click selects the photo by its id; a Shift-click across cards that are not here selects the range at once, with no request", async (t) => {
    const ctx = await load(t);
    click(ctx.window, ctx.cardById(IDS[1]).querySelector(".thumbnail-checkbox"));
    assert.deepEqual(ctx.selectedIds(), [IDS[1]]);
    assert.equal(ctx.state.selectedThumbnails.length, 0, "a library view's selection is ids, never paths");
    await ctx.scrollToIndex(1500);
    await ctx.settle();
    const asked = ctx.cardsAsked.length;
    click(ctx.window, ctx.cardById(IDS[1500]).querySelector(".thumbnail-checkbox"), { shiftKey: true });
    assert.equal(ctx.cardsAsked.length, asked, "not one request for the 1,500 photos between");
    assert.equal(ctx.selectedIds().length, 1500, "photos 1 through 1500");
    assert.equal(ctx.selectedIds()[0], IDS[1]);
    assert.match(ctx.label(), /1,500/);
    assert.ok(ctx.cardById(IDS[1500]).classList.contains("selected"));
    assert.equal(ctx.state.library.cards.size <= 2000, true);
  });

  test("Select all of 68,000 is the view's source with nothing excluded: no question, no request, no ids", async (t) => {
    const ctx = await load(t);
    let asked = 0;
    ctx.window.confirm = () => { asked++; return false; };
    const requests = ctx.server.calls.length;
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    assert.equal(asked, 0);
    assert.equal(ctx.server.calls.length, requests, "nothing was asked of the server");
    assert.equal(ctx.state.library.sel.mode, "source");
    assert.equal(ctx.state.library.sel.excluded.size, 0);
    assert.equal(ctx.state.library.sel.ids.size, 0);
    assert.equal(ctx.state.selectedThumbnails.length, 0);
    assert.match(ctx.label(), /68,000/);
    assert.ok(ctx.real().every((card) => card.classList.contains("selected")));
    ctx.document.getElementById("btn-select-none-thumbnails").click();
    assert.match(ctx.label(), /Selected: 0$/);
    assert.ok(ctx.real().every((card) => !card.classList.contains("selected")));
  });

  test("a small selection: all, then none", async (t) => {
    const ctx = await load(t, { search: "?view=year&value=2020", ids: IDS.slice(0, 400), total: 400 });
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    assert.equal(ctx.selectedIds().length, 400);
    ctx.document.getElementById("btn-select-none-thumbnails").click();
    assert.equal(ctx.selectedIds().length, 0);
  });

  test("the selection panel keeps Smart Rename off (a name is per folder)", async (t) => {
    const ctx = await load(t);
    click(ctx.window, ctx.cardById(IDS[0]).querySelector(".thumbnail-checkbox"));
    assert.equal(ctx.document.getElementById("btn-apply-rename").disabled, true);
    assert.equal(ctx.document.getElementById("btn-toggle-rename").disabled, true);
  });

  test("a selection survives Refresh view", async (t) => {
    const ctx = await load(t, { search: "?view=year&value=2020", ids: IDS.slice(0, 300), total: 300 });
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    click(ctx.window, ctx.document.getElementById("btn-library-refresh"));
    await ctx.settle();
    assert.equal(ctx.selectedIds().length, 300);
    assert.ok(ctx.cardById(IDS[0]).classList.contains("selected"));
  });
});

describe("the details panel on a library photo", () => {
  test("opening a card reads the photo whole, shows it with its place in the view, and editing works by path", async (t) => {
    const ctx = await load(t);
    ctx.cardById(IDS[2]).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    assert.deepEqual(ctx.photosAsked, [IDS[2]]);
    assert.equal(ctx.state.activePhotoPath, cardOf(IDS[2]).path);
    assert.equal(ctx.document.getElementById("detail-path").textContent, cardOf(IDS[2]).path);
    assert.equal(ctx.document.getElementById("photo-position").textContent, "3 of 68,000");
    assert.match(ctx.document.getElementById("detail-tags").textContent, /Trips\/Coast/);
    // a title saved: written by path, the card follows in place
    ctx.document.getElementById("input-photo-title").value = "Harbour";
    click(ctx.window, ctx.document.getElementById("btn-save-title"));
    await ctx.settle(60);
    const sent = ctx.server.lastBody("/api/photo/save-metadata");
    assert.equal(sent.path, cardOf(IDS[2]).path);
    assert.equal(sent.title, "Harbour");
    assert.deepEqual(sent.tags, ["Trips/Coast"], "the tags it holds, not a card's nothing");
    ctx.document.getElementById("folder-view-header").click();
    await ctx.settle(60);
    assert.equal(ctx.cardById(IDS[2]).querySelector(".thumbnail-filename").textContent, "Harbour");
    assert.equal(ctx.state.library.ids[2], IDS[2], "its place in the order is not recomputed");
  });

  test("the arrow keys follow the view's order through the ids, fetching each photo", async (t) => {
    const ctx = await load(t);
    ctx.cardById(IDS[10]).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    const key = (name) => ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }));
    key("ArrowRight");
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, cardOf(IDS[11]).path);
    assert.equal(ctx.document.getElementById("photo-position").textContent, "12 of 68,000");
    key("ArrowLeft");
    key("ArrowLeft");
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, cardOf(IDS[9]).path);
    assert.deepEqual(ctx.photosAsked.slice(0, 2), [IDS[10], IDS[11]]);
  });

  test("the first photo has no photo before it", async (t) => {
    const ctx = await load(t);
    ctx.cardById(IDS[0]).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true }));
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, cardOf(IDS[0]).path);
    assert.equal(ctx.document.getElementById("photo-position").textContent, "1 of 68,000");
  });

  test("a deleted photo's card goes, the total drops, and the next one opens", async (t) => {
    const ctx = await load(t);
    ctx.cardById(IDS[3]).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    ctx.document.getElementById("btn-delete-photo").click();
    await ctx.settle(80);
    assert.equal(ctx.server.lastBody("/api/photo/delete").path, cardOf(IDS[3]).path);
    assert.equal(ctx.state.library.ids.includes(IDS[3]), false);
    assert.equal(ctx.state.library.total, N - 1);
    assert.equal(ctx.state.activePhotoPath, cardOf(IDS[4]).path, "the one after it in the order");
    assert.match(ctx.document.getElementById("photo-position").textContent, /4 of 67,?999/);
  });

  test("a photo opened that the library no longer has is dropped, quietly", async (t) => {
    const ctx = await load(t, { onPhoto: () => { throw new Error("unused"); } });
    ctx.server.first("/api/library/photo", { error: "There is no photo 5 in this library." }, { status: 404 });
    ctx.cardById(IDS[5]).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(80);
    assert.equal(ctx.state.activePhotoPath, null);
    assert.equal(ctx.state.library.ids.includes(IDS[5]), false);
    assert.equal(ctx.state.library.total, N - 1);
  });

  test("unsaved edits are asked about before another photo opens", async (t) => {
    const ctx = await load(t);
    ctx.cardById(IDS[0]).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    ctx.document.getElementById("input-photo-title").value = "Unsaved";
    ctx.document.getElementById("input-photo-title").dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }));
    await ctx.settle(60);
    assert.ok(ctx.document.querySelector(".unsaved-edits-modal"), "Save, Discard or Cancel");
    assert.equal(ctx.state.activePhotoPath, cardOf(IDS[0]).path);
  });

  test("the panel stays open on its photo while the view refreshes", async (t) => {
    const ctx = await load(t);
    ctx.cardById(IDS[2]).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(60);
    ctx.document.getElementById("btn-refresh-list").click();
    await ctx.settle(60);
    assert.equal(ctx.state.activePhotoPath, cardOf(IDS[2]).path);
    assert.equal(ctx.document.getElementById("photo-position").textContent, "3 of 68,000");
  });
});

describe("a view that changes under the page", () => {
  test("photos added elsewhere leave the total as it was until Refresh view; then the place is kept", async (t) => {
    let ids = IDS.slice();
    const ctx = await load(t, { onIds: () => ({ source: {}, total: ids.length, ids, complete: true }) });
    await ctx.scrollToIndex(30000);
    await ctx.settle();
    const near = IDS[30000];
    ids = [1, 2, 3, ...IDS];            // another tab, or the indexer, added three photos at the front
    await ctx.settle();
    assert.match(ctx.stripText(), /68,?000 photos/, "outdated, and shown as it was");
    click(ctx.window, ctx.document.getElementById("btn-library-refresh"));
    await ctx.settle(500);
    assert.match(ctx.stripText(), /68,?003 photos/);
    assert.equal(ctx.state.library.ids.length, N + 3);
    assert.ok(ctx.cardById(near), "the same photo is still in view");
    assert.ok(Math.abs(ctx.here.scrollTop - (GRID_TOP + Math.floor(30003 / COLUMNS) * STRIDE)) < STRIDE * 3, "near where it was");
  });

  test("a refresh that cannot read the order leaves the view as it was and says so", async (t) => {
    const ctx = await load(t);
    ctx.server.first("/api/library/ids", { error: "the library is busy" }, { status: 503 });
    click(ctx.window, ctx.document.getElementById("btn-library-refresh"));
    await ctx.settle();
    assert.equal(ctx.state.library.ids.length, N);
    assert.match(ctx.stripText(), /Could not refresh the view: the library is busy/);
    assert.ok(ctx.real().length > 0);
  });

  test("two quick changes of view: the first's order and cards are not painted into the second", async (t) => {
    const ctx = await load(t, { search: "", ids: [], total: 0, layout: true });
    const opened = pageExports(ctx.window, "web/tagpup/library-view.js");
    const firstIds = IDS.slice(0, 200);
    const secondIds = IDS.slice(1000, 1200);
    const replies = [];
    ctx.server.first("/api/library/ids", (url) => new Promise((resolve) => {
      const ids = url.includes("kind=year") ? firstIds : secondIds;
      replies.push(() => resolve({ total: 200, ids, complete: true }));
    }));
    opened.openLibraryView({ kind: "year", value: "2020" });
    opened.openLibraryView({ kind: "month", value: "2021-05" });
    await flush(ctx.window, 3);
    assert.equal(replies.length, 2);
    replies[1]();                    // the second's order arrives first,
    await ctx.settle();
    replies[0]();                    // and the first's after it
    await ctx.settle();
    assert.deepEqual(ctx.state.library.ids, secondIds);
    assert.match(ctx.stripText(), /May 2021/);
    assert.ok(ctx.cardById(IDS[1000]));
    assert.equal(ctx.cardById(IDS[0]), null);
    assert.ok(ctx.cardsAsked.flat().every((id) => IDS.indexOf(id) >= 1000), "no card of the first view was asked for or kept");
  });
});

describe("the way there and back", () => {
  test("the gear opens the whole library and pushes an address; Back goes to the folder", async (t) => {
    const ctx = await load(t, { search: "?path=D%3A%5CLibrary%5C2020&nothing=1" , ids: [], total: 0 });
    // a folder was open from the address
    await openFolder(ctx, FOLDER);
    await ctx.settle();
    assert.equal(ctx.state.scannedFolder, FOLDER);
    const folderCalls = ctx.folderCalls().length;
    const entries = ctx.window.history.length;
    ctx.document.getElementById("btn-gear").click();
    const item = ctx.document.querySelector('#gear-menu [data-action="browse-library"]');
    item.click();
    await ctx.settle();
    assert.equal(ctx.window.history.length, entries + 1, "a place to go Back to");
    assert.match(ctx.window.location.search, /view=all/);
    assert.equal(ctx.state.library.kind, "all");
    assert.equal(ctx.state.scannedFolder, null);
    assert.equal(ctx.folderCalls().length, folderCalls, "the folder is not asked about while the view is open");
    ctx.window.history.back();
    await ctx.settle(250);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, FOLDER, "the folder is open again");
    assert.doesNotMatch(ctx.window.location.search, /view=/);
    assert.ok(ctx.strip().classList.contains("hidden"));
    assert.equal(ctx.document.getElementById("photo-search").disabled, false);
    assert.ok(ctx.cards().length >= 2, "its photos are drawn");
  });

  test("'Library view of this folder' opens the folder and its subfolders; the item is off with no folder", async (t) => {
    const ctx = await load(t, { search: "", ids: [], total: 0 });
    ctx.document.getElementById("btn-gear").click();
    const item = ctx.document.querySelector('#gear-menu [data-action="library-of-folder"]');
    assert.equal(item.getAttribute("aria-disabled"), "true");
    ctx.document.getElementById("btn-gear").click();
    await openFolder(ctx, FOLDER);
    ctx.document.getElementById("btn-gear").click();
    assert.equal(item.getAttribute("aria-disabled"), null);
    item.click();
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "folder");
    assert.equal(ctx.state.library.recursive, true);
    assert.equal(ctx.state.library.value, FOLDER);
    assert.match(ctx.idsAsked.at(-1), /kind=folder&folder=D%3A%5CLibrary%5C2020&recursive=1$/);
  });

  test("Back returns to the folder that was open, and the grid draws it (the strip's own link went with #670)", async (t) => {
    const ctx = await load(t, { search: "", ids: [], total: 0 });
    await openFolder(ctx, FOLDER);
    pageExports(ctx.window, "web/tagpup/library-view.js").openLibraryView({ kind: "all" });
    await ctx.settle();
    assert.equal(ctx.state.library.kind, "all");
    ctx.window.history.back();
    await ctx.settle(300);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, FOLDER);
    assert.equal(ctx.cards().length, 2);
    assert.ok(ctx.document.getElementById("photo-list").querySelectorAll(".photo-item-file").length === 2, "the list is back");
  });

  test("between two views, Back returns to the first at the place it was left", async (t) => {
    const ctx = await load(t);
    const opened = pageExports(ctx.window, "web/tagpup/library-view.js");
    await ctx.scrollToIndex(20000);
    await ctx.settle();
    const top = ctx.here.scrollTop;
    opened.openLibraryView({ kind: "year", value: "2019" });
    await ctx.settle();
    assert.match(ctx.window.location.search, /view=year&value=2019/);
    ctx.window.history.back();
    await ctx.settle(300);
    assert.match(ctx.window.location.search, /view=all/);
    assert.equal(ctx.state.library.kind, "all");
    assert.equal(ctx.here.scrollTop, top);
    assert.ok(ctx.cardById(IDS[20000]));
  });

  test("typing a folder leaves the view: the folder opens, the strip goes", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    assert.equal(ctx.state.library, null);
    assert.equal(ctx.state.scannedFolder, FOLDER);
    assert.ok(ctx.strip().classList.contains("hidden"));
    assert.doesNotMatch(ctx.window.location.search, /view=/);
  });

  test("a scan still out when a view opens does not land in the view", async (t) => {
    const ctx = await load(t, { search: "", ids: [], total: 0 });
    const gate = {};
    ctx.server.first("/api/folder/scan", () => new Promise((resolve) => { gate.go = () => resolve([photoRecord({ filename: "late.jpg" })]); }));
    const box = ctx.document.getElementById("folder-path-input");
    box.value = FOLDER;
    box.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 3);
    ctx.server.first("/api/library/ids", { total: 3, ids: [1, 2, 3], complete: true });
    pageExports(ctx.window, "web/tagpup/library-view.js").openLibraryView({ kind: "all" });
    await ctx.settle();
    gate.go();
    await ctx.settle();
    assert.equal(ctx.state.scannedFolder, null);
    assert.equal(ctx.state.folderPhotos.length, 0);
    assert.equal(ctx.state.library.kind, "all");
  });
});

describe("another library", () => {
  test("choosing one goes to its address without the view, its order too (#697)", async () => {
    const calls = [];
    globalThis.window = { location: { search: "?view=keyword&value=Trips&recursive=1&order=name-desc&path=D%3A%5CX", set href(v) { calls.push(v); } } };
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    try {
      const { goToLibrary } = await import("../../web/common/library.js");
      goToLibrary("other");
      globalThis.window.location.search = "?view=all";
      goToLibrary("other");
    } finally {
      delete globalThis.window;
      delete globalThis.localStorage;
    }
    assert.deepEqual(calls, ["/other/?path=D%3A%5CX", "/other/"]);
  });
});
