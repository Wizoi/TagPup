/**
 * Owner scenario: on the phone page (web/tagpup/mobile.html, docs/plans/phone-viewer.md, Screens 4) the owner taps a
 * thumbnail of a view and looks at the photo large, swiping left and right through the view, and goes Back to the grid.
 *
 * - The picture is the library's /api/photo-file at 1024 px, versioned by the card's `mtime`; a clip plays from
 *   /api/video-file with its poster (web/common/file-addresses.js, the desktop's spelling too). A tap opens the file
 *   whole in the shared zoom.
 * - A swipe is the shared one (web/common/swipe.js): each lands one photo on, in the step the photo was opened in, so
 *   rapid swipes are as many photos and no extra Back; the first and the last photo go no further.
 * - A photo whose card the grid has not asked yet is read with the cards around it, kept in the view as the grid's own:
 *   80 swipes past the grid's window are a few reads, not 80 (#3151); a reply for a photo swiped past is dropped.
 * - A photo deleted on the PC while it is looked at (a card window's empty reply drops it from the view): a swipe
 *   still steps on, from where it was.
 * - Back after swipes is the grid, its ids not asked again, the card swiped to in view.
 * - A file that is gone, a picture the PC could not read, a card that could not be read, a clip that will not play: a
 *   sentence, and the swipe goes on.
 * - A picture that failed while the server was being updated is shown again once api.js has asked for it again, and
 *   its sentence goes.
 *
 * Names hold spaces and apostrophes, as the live libraries' do. Every name here is made up.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps, pageExports } from "./harness.mjs";

const PAGE = "http://localhost:8090/alpha/mobile.html";
const KEYWORDS = { keywords: [{ tag: "Events", name: "Events", parent: null, count: 2000 }], untagged: 0 };

const el = (ctx, id) => ctx.document.getElementById(id);
const shown = (ctx) =>
  [...ctx.document.querySelectorAll("[data-screen]")].filter((s) => !s.classList.contains("hidden")).map((s) => s.dataset.screen);
const pageState = (ctx) => pageExports(ctx.window, "web/tagpup/mobile-state.js").state;
const cards = (ctx) => [...el(ctx, "grid-cards").querySelectorAll(".phone-card[data-id]")];
const idsAsked = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/library/ids"));
const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function settle(ctx, ms = 50) {
  await wait(ctx.window, ms);
  await flush(ctx.window, 6);
}

/** Wait for `done()`, at most 10 s. */
async function until(ctx, done, what) {
  const end = Date.now() + 10000;
  while (!done()) {
    if (Date.now() > end) throw new Error(`waited 10 s for ${what}`);
    await wait(ctx.window, 5);
  }
  await flush(ctx.window, 6);
}

/** The card the library sends: its path, its stamp (`mtime`, also in its thumbnail's address), `media`, as tagpup.services.library_view. */
function cardOf(id, extra = {}) {
  const mtime = extra.mtime ?? 1718000000.5;
  return {
    id, name: `Rowan's Meet ${id}.jpg`, path: `D:\\Photos\\Rowan's Meet\\Rowan's Meet ${id}.jpg`, taken: "2024:06:01 10:00:00",
    thumb: `/api/photo-thumb?id=${id}&v=${mtime}`, mtime, media: "photo", ...extra,
  };
}

/**
 * The page on a library whose keyword holds `count` photos, the Grid given a phone's layout (jsdom has none): 3
 * columns, 600 px of view. `card(id)` answers each card, null for a photo the library no longer has; a read of cards
 * sent while the Photo screen shows is held until released when `holdOne`.
 */
async function phone(t, { count = 2000, card = (id) => cardOf(id), holdOne = false } = {}) {
  const held = [];
  let page = null;
  const onPhoto = () => Boolean(page) && !page.document.querySelector('[data-screen="photo"]').classList.contains("hidden");
  const server = new FakeServer()
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/library/navigator?section=keywords", KEYWORDS)
    .on("/api/library/ids", () => {
      const ids = Array.from({ length: count }, (_, i) => i + 1);
      return { ids, total: count, of: count, videos: ids.filter((id) => (card(id) || {}).media === "video") };
    })
    .on("/api/library/cards", (url) => {
      const ids = decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").map(Number);
      const body = { cards: ids.map(card).filter(Boolean) };
      return holdOne && onPhoto() ? new Promise((resolve) => held.push({ id: ids[0], ids, go: () => resolve(body) })) : body;
    });
  const ctx = await loadApp("phone", { t, server, url: PAGE });
  page = ctx;
  const section = ctx.document.querySelector('[data-screen="grid"]');
  const here = { scrollTop: 0, viewport: 600, columns: 3, cardHeight: 130, rowGap: 4, gridTop: 120, cardWidth: 120, columnGap: 4 };
  Object.defineProperty(section, "scrollTop", { get: () => here.scrollTop, set: (v) => { here.scrollTop = v; }, configurable: true });
  Object.defineProperty(section, "clientHeight", { get: () => (section.classList.contains("hidden") ? 0 : here.viewport), configurable: true });
  pageState(ctx).grid.setMeasure(() => {
    if (section.classList.contains("hidden")) {
      here.scrollTop = 0;       // the browser forgets a hidden element's scroll offset
      return { ...here, viewport: 0 };
    }
    return { ...here };
  });
  return Object.assign(ctx, { here, held });
}

/** Keywords, Events, Show these photos, and a tap on the card of `id` (in the first window). */
async function openPhoto(ctx, id = 1) {
  click(ctx.window, ctx.document.querySelector('[data-section="keywords"]'));
  await settle(ctx);
  click(ctx.window, [...el(ctx, "browse-rows").querySelectorAll(".browse-item")].find((r) => r.textContent.includes("Events")));
  await settle(ctx);
  click(ctx.window, el(ctx, "browse-show"));
  await settle(ctx, 100);
  click(ctx.window, cards(ctx).find((c) => Number(c.dataset.id) === id));
  await settle(ctx);
}

/** A finger across the photo: `dx` pixels, right to left when negative. */
function swipe(ctx, dx) {
  const stage = el(ctx, "photo-stage");
  const pointer = (type, x) => {
    const event = new ctx.window.MouseEvent(type, { bubbles: true, clientX: x, clientY: 300, button: 0 });
    Object.defineProperty(event, "pointerId", { value: 7 });
    Object.defineProperty(event, "pointerType", { value: "touch" });
    stage.dispatchEvent(event);
  };
  pointer("pointerdown", 200);
  pointer("pointerup", 200 + dx);
}
const next = (ctx) => swipe(ctx, -120);
const previous = (ctx) => swipe(ctx, 120);

const picture = (ctx) => el(ctx, "photo-picture");
const said = (ctx) => (el(ctx, "photo-problem").classList.contains("hidden") ? "" : el(ctx, "photo-problem").textContent);

afterEach(() => closeAllApps());

describe("looking at a photo on the phone", () => {
  test("rapid swipes go as many photos on in the one step; Back after them is the grid with the card swiped to in view", async (t) => {
    const ctx = await phone(t);
    await openPhoto(ctx, 1);
    assert.deepEqual(shown(ctx), ["photo"]);
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 1.jpg");
    assert.equal(el(ctx, "photo-at").textContent, "1 of 2,000");
    assert.equal(picture(ctx).getAttribute("src"),
      `/alpha/api/photo-file?path=${encodeURIComponent("D:\\Photos\\Rowan's Meet\\Rowan's Meet 1.jpg")}&size=1024&v=1718000000.5`,
      "the library's picture at 1024, its stamp the card's");
    const steps = ctx.window.history.length;
    const ids = idsAsked(ctx).length;
    const cardReads = () => ctx.server.urls().filter((u) => u.includes("/api/library/cards")).length;
    const reads = cardReads();

    for (let i = 0; i < 80; i += 1) next(ctx);
    previous(ctx);
    await settle(ctx);
    assert.ok(cardReads() - reads <= 3, `80 swipes read the cards around them a few times, not each (${cardReads() - reads})`);
    assert.ok(pageState(ctx).photos.cards.has(79), "a card read for a swipe is kept in the view, the grid's own");

    assert.deepEqual({ ...pageState(ctx).place }, { id: 80, at: 79 });
    assert.deepEqual({ ...ctx.window.history.state.place }, { id: 80, at: 79 }, "the step holds the photo swiped to");
    assert.equal(ctx.window.history.length, steps, "swipes add no step to go Back through");
    assert.equal(el(ctx, "photo-at").textContent, "80 of 2,000");
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 80.jpg", "a card the grid had not asked is read by id");
    assert.match(picture(ctx).getAttribute("src"), /Meet%2080\.jpg&size=1024/);

    ctx.window.history.back();
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(idsAsked(ctx).length, ids, "the view held is shown, not asked again");
    const top = ctx.here.scrollTop;
    const row = Math.floor(79 / 3);
    assert.ok(120 + row * 134 >= top && 120 + (row + 1) * 134 <= top + 600, `the card swiped to is in view (scroll ${top})`);
    assert.ok(cards(ctx).some((c) => Number(c.dataset.id) === 80));
  });

  test("the photo tapped is the view's open photo, as on the desktop; Back keeps it so, the grid where it was", async (t) => {
    const ctx = await phone(t);
    await openPhoto(ctx, 4);
    assert.equal(pageState(ctx).photos.activeId, 4, "the tap names the photo open, not only a swipe");
    const top = ctx.here.scrollTop;

    ctx.window.history.back();
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(pageState(ctx).photos.activeId, 4, "still the photo last open: Back does not forget it");
    assert.equal(ctx.here.scrollTop, top, "its card in view already: the scroll is kept");
  });

  test("the first photo goes no further back and the last no further on", async (t) => {
    const ctx = await phone(t, { count: 3 });
    await openPhoto(ctx, 1);
    previous(ctx);
    await settle(ctx);
    assert.deepEqual({ ...pageState(ctx).place }, { id: 1, at: 0 });

    next(ctx);
    next(ctx);
    next(ctx);
    await settle(ctx);
    assert.deepEqual({ ...pageState(ctx).place }, { id: 3, at: 2 });
    assert.equal(el(ctx, "photo-at").textContent, "3 of 3");
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 3.jpg");
  });

  test("a swipe to a photo still being read waits for its card, and a reply for a photo swiped past is dropped", async (t) => {
    const ctx = await phone(t, { holdOne: true });
    await openPhoto(ctx, 1);
    for (let i = 0; i < 99; i += 1) next(ctx);       // photo 100: past the grid's first window
    await settle(ctx);
    assert.equal(el(ctx, "photo-name").textContent, "", "no name of another photo while its card is read");
    assert.ok(picture(ctx).classList.contains("hidden"), "no picture of another photo either");
    next(ctx);                                       // photo 101, before 100's card has come
    await settle(ctx);
    assert.ok(ctx.held.at(-1).ids.includes(100) && ctx.held.at(-1).ids.includes(101), "one read out names both: none sent again");
    assert.ok(ctx.held.length <= 4, `the photos swiped through are read a window at a time (${ctx.held.length})`);

    ctx.held.at(-1).go();
    await settle(ctx);
    ctx.held.slice(0, -1).forEach((each) => each.go());    // the photos swiped past answer last
    await settle(ctx);
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 101.jpg");
    assert.match(picture(ctx).getAttribute("src"), /Meet%20101\.jpg/);
  });

  test("a photo deleted on the PC while looked at, dropped from the view by a card window's reply: the swipe steps on", async (t) => {
    let deleted = false;
    const ctx = await phone(t, { card: (id) => (deleted && id === 250 ? null : cardOf(id)) });
    await openPhoto(ctx, 1);
    deleted = true;                                  // on the PC, before the phone reads its card
    for (let i = 0; i < 249; i += 1) next(ctx);
    await settle(ctx);
    assert.equal(said(ctx), "This photo is no longer in the library.");

    ctx.window.history.back();                       // the grid asks the window around it: its card is not there
    await settle(ctx, 400);
    assert.ok(!pageState(ctx).photos.ids.includes(250), "the view dropped it");
    ctx.window.history.forward();
    await settle(ctx);
    assert.deepEqual({ ...pageState(ctx).place }, { id: 250, at: 249 });
    assert.equal(said(ctx), "This photo is no longer in the library.");

    next(ctx);
    await settle(ctx);
    assert.deepEqual({ ...pageState(ctx).place }, { id: 251, at: 249 }, "on, to the photo after it, now where it was");
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 251.jpg");
    assert.equal(el(ctx, "photo-at").textContent, "250 of 1,999");
  });

  test("a tap opens the file whole in the zoom; a swipe that ends on the picture does not", async (t) => {
    const ctx = await phone(t, { count: 5 });
    await openPhoto(ctx, 2);
    next(ctx);
    click(ctx.window, picture(ctx));                 // the click the browser sends after the swipe
    await settle(ctx);
    assert.ok(el(ctx, "image-zoom").classList.contains("hidden"), "a swipe is not a tap");

    click(ctx.window, picture(ctx));
    await settle(ctx);
    assert.ok(!el(ctx, "image-zoom").classList.contains("hidden"));
    assert.equal(el(ctx, "image-zoom-img").getAttribute("src"),
      `/alpha/api/photo-file?path=${encodeURIComponent("D:\\Photos\\Rowan's Meet\\Rowan's Meet 3.jpg")}&v=1718000000.5`, "the file whole");

    ctx.window.history.back();                       // the phone's Back
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.ok(el(ctx, "image-zoom").classList.contains("hidden"), "the zoom goes with the photo");
  });

  test("a missing file, a picture the PC could not read, a card that could not be read and a clip each say so; the swipe goes on", async (t) => {
    const card = (id) => (id === 2 ? cardOf(id, { stale: "missing" })
      : id === 4 ? cardOf(id, { name: "Wren's Kite.mp4", media: "video" }) : cardOf(id));
    const ctx = await phone(t, { count: 300, card });
    // A read naming photo 300 fails: the last photo's card, and those read with it, cannot be read.
    ctx.server.first(/\/api\/library\/cards\?ids=(?:\d+,)*300(?:,|$)/, { error: "The library is busy." }, { status: 500 });
    await openPhoto(ctx, 1);

    next(ctx);
    await settle(ctx);
    assert.match(said(ctx), /not where the library last saw it/);
    assert.ok(picture(ctx).classList.contains("hidden"));

    next(ctx);
    await settle(ctx);
    assert.equal(said(ctx), "", "the next photo says nothing of the last");
    picture(ctx).dispatchEvent(new ctx.window.Event("error"));    // the share did not answer
    assert.match(said(ctx), /^Could not load this photo/);

    next(ctx);
    await settle(ctx);
    const clip = el(ctx, "photo-clip");
    assert.ok(!clip.classList.contains("hidden") && picture(ctx).classList.contains("hidden"), "a clip plays, no picture");
    assert.equal(clip.getAttribute("src"), "/alpha/api/video-file?id=4&v=1718000000.5");
    assert.equal(clip.getAttribute("poster"), "/alpha/api/photo-thumb?id=4&v=1718000000.5");
    clip.dispatchEvent(new ctx.window.Event("error"));
    assert.equal(said(ctx), "This clip will not play here. Play it on the PC.");

    next(ctx);
    await settle(ctx);
    assert.ok(clip.classList.contains("hidden") && !clip.getAttribute("src"), "the clip swiped past is let go");
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 5.jpg");

    for (let i = 0; i < 295; i += 1) next(ctx);      // the last photo, whose card cannot be read
    await settle(ctx);
    assert.equal(el(ctx, "photo-at").textContent, "300 of 300");
    assert.match(said(ctx), /^Could not load this photo\./);
    ctx.server.routes.shift();                       // the library answers again: a swipe reads anew
    previous(ctx);
    await settle(ctx);
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 299.jpg");
  });

  test("a picture that failed while the server was updated is shown again once it is asked again, its sentence gone", async (t) => {
    const ctx = await phone(t, { count: 5 });
    await openPhoto(ctx, 2);
    pageExports(ctx.window, "web/common/api.js").imagesAfterAnUpdate.watch(ctx.document, { every: 5 });
    ctx.server.first("/api/server", {}, { status: 503, headers: { "X-TagPup-Updating": "1" } });
    const src = picture(ctx).getAttribute("src");

    picture(ctx).dispatchEvent(new ctx.window.Event("error"));    // the server went away for its update
    assert.ok(picture(ctx).classList.contains("hidden"));
    assert.match(said(ctx), /^Could not load this photo/);
    assert.doesNotMatch(said(ctx), /could not read/, "an update is not a file the PC could not read");
    await until(ctx, () => ctx.server.urls().some((u) => u.includes("/api/server")), "the server to be asked");
    ctx.server.routes.shift();                                     // and is back
    await until(ctx, () => picture(ctx).getAttribute("src") !== src, "the picture to be asked again");
    assert.ok(picture(ctx).getAttribute("src").startsWith(`${src}&_again=`));

    picture(ctx).dispatchEvent(new ctx.window.Event("load"));
    assert.ok(!picture(ctx).classList.contains("hidden"), "the picture is shown again");
    assert.equal(said(ctx), "", "and what its failure said goes");
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 2.jpg");
  });
});
