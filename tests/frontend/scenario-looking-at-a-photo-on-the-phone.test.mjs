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
 * - Under the picture, its facts (docs/plans/phone-redesign.md, PHOTO): the Date Taken, the title (three lines, then
 *   "more"), the people and keywords as chips and Edit; pulled up or its handle tapped, the rest. A photo with no date,
 *   no keywords, a 500-character title, 40 keywords, a clip, a record that could not be read; a swipe is refused while
 *   the sheet is up and goes on once it is down; rapid swipes read the facts one at a time; a phone on its side keeps
 *   the picture's height, the facts down to their handle or beside it.
 *
 * Names hold spaces and apostrophes, as the live libraries' do. Every name here is made up.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadApp, FakeServer, advance, flush, click, closeAllApps, pageExports, PEOPLE_ROUTE, answer, testClock } from "./harness.mjs";
import { PHONE_PAGE, el, shown, pageState, settle, phoneCard, phoneLayout } from "./phone-page.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const HERE = path.dirname(fileURLToPath(import.meta.url));
const KEYWORDS = { keywords: [{ tag: "Events", name: "Events", parent: null, count: 2000 }], untagged: 0 };
const WREN = "People/Wren O'Malley";
const ODA = "People/Oda Fairweather";
const PEOPLE = [{ id: 1, name: "Wren O'Malley", tag: WREN }, { id: 2, name: "Oda Fairweather", tag: ODA }];

/** The record /api/library/photo sends for `card` (tagpup.services.photos.page_record): `extra` overrides any of it. */
function phoneRecord(card, extra = {}) {
  return { id: card.id, path: card.path, filename: card.name, tags: [], people: [], title: "", mtime: card.mtime,
    size: 204800, year: 2024, taken: card.taken, raw_metadata: {}, camera: "", lens: "", media: card.media, writes: [], ...extra };
}

const cards = (ctx) => [...el(ctx, "grid-cards").querySelectorAll(".phone-card[data-id]")];
const idsAsked = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/library/ids"));

/** Wait for `done()`, at most 10 s. */
async function until(ctx, done, what) {
  const end = Date.now() + 10000;
  while (!done()) {
    if (Date.now() > end) throw new Error(`waited 10 s for ${what}`);
    await advance(ctx.window, 5);
  }
  await flush(ctx.window, 6);
}

/**
 * The page on a library whose keyword holds `count` photos, the Grid given a phone's layout (jsdom has none): 3
 * columns, 600 px of view. `card(id)` answers each card, null for a photo the library no longer has; a read of cards
 * sent while the Photo screen shows is held until released when `holdOne`.
 */
async function phone(t, { count = 2000, card = (id) => phoneCard(id), holdOne = false, record = () => ({}) } = {}) {
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
    })
    .on("/api/library/photo", (url) => {
      const id = Number((url.match(/id=(\d+)/) || [])[1]);
      return card(id) ? { photo: phoneRecord(card(id), record(id)) } : answer(404, { error: `There is no photo ${id} in this library.` });
    })
    .on("/api/tags", [WREN, ODA])
    .on(PEOPLE_ROUTE, PEOPLE);
  const ctx = await loadApp("phone", { t, server, url: PHONE_PAGE });
  page = ctx;
  const { here } = phoneLayout(ctx);
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

const factsUp = (ctx) => el(ctx, "photo-facts").classList.contains("open");
const chipsIn = (ctx, id) => [...el(ctx, id).querySelectorAll(".photo-chip")].map((chip) => chip.textContent);
const noneIn = (ctx, id) => (el(ctx, id).querySelector(".photo-none") || { textContent: "" }).textContent;
const factSaid = (ctx, id) => (el(ctx, id).parentElement.classList.contains("hidden") ? "" : el(ctx, id).textContent);
const factsProblem = (ctx) => (el(ctx, "photo-facts-problem").classList.contains("hidden") ? "" : el(ctx, "photo-facts-problem").textContent);
const photoReads = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/library/photo?")).length;

/** A finger on the facts sheet's handle, `dy` pixels up (negative) or down, and the click the browser sends after it. */
function pull(ctx, dy) {
  const handle = el(ctx, "photo-facts-handle");
  const pointer = (type, y) => {
    const event = new ctx.window.MouseEvent(type, { bubbles: true, clientX: 200, clientY: y, button: 0 });
    Object.defineProperty(event, "pointerId", { value: 9 });
    Object.defineProperty(event, "pointerType", { value: "touch" });
    handle.dispatchEvent(event);
  };
  pointer("pointerdown", 500);
  pointer("pointerup", 500 + dy);
  click(ctx.window, handle);
}

afterEach(() => closeAllApps());

describe("looking at a photo on the phone", () => {
  test("rapid swipes go as many photos on in the one step; Back after them is the grid with the card swiped to in view", async (t) => {
    const ctx = await phone(t);
    await openPhoto(ctx, 1);
    assert.deepEqual(shown(ctx), ["photo"]);
    assert.equal(el(ctx, "photo-name").textContent, "Rowan's Meet 1.jpg");
    assert.equal(el(ctx, "photo-place").textContent, "1 of 2,000");
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
    assert.equal(el(ctx, "photo-place").textContent, "80 of 2,000");
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
    assert.equal(el(ctx, "photo-place").textContent, "3 of 3");
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
    const ctx = await phone(t, { card: (id) => (deleted && id === 250 ? null : phoneCard(id)) });
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
    assert.equal(el(ctx, "photo-place").textContent, "250 of 1,999");
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
    const card = (id) => (id === 2 ? phoneCard(id, { stale: "missing" })
      : id === 4 ? phoneCard(id, { name: "Wren's Kite.mp4", media: "video" }) : phoneCard(id));
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
    assert.equal(el(ctx, "photo-place").textContent, "300 of 300");
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

describe("the facts under a photo on the phone", () => {
  test("the Date Taken, the title, the people and keywords and Edit always; pulled up, the rest; let down again", async (t) => {
    const ctx = await phone(t, {
      count: 5,
      card: (id) => phoneCard(id, { taken: "2019:06:15 15:42:00" }),
      record: () => ({ title: "Beach day with the cousins", tags: ["Trips/Coast", WREN, "Events/Harvest", "Places/Harvest"],
        camera: "Pixel 8", size: 2411724 }),
    });
    await openPhoto(ctx, 2);
    assert.equal(el(ctx, "photo-when").textContent, "Sat, June 15, 2019, 3:42 PM");
    assert.equal(el(ctx, "photo-title").textContent, "Beach day with the cousins");
    assert.deepEqual(chipsIn(ctx, "photo-people"), ["Wren O'Malley"], "a person is told from a keyword by the library's people");
    assert.deepEqual(chipsIn(ctx, "photo-keywords"), ["Harvest (Events)", "Harvest (Places)", "Coast"],
      "two keywords of one leaf say their roots, as the Edit sheet's chips do");
    assert.equal(factsProblem(ctx), "");
    assert.ok(!factsUp(ctx) && el(ctx, "photo-more").classList.contains("hidden"), "the rest waits for a pull");
    assert.equal(el(ctx, "photo-facts-handle").getAttribute("aria-expanded"), "false");

    click(ctx.window, el(ctx, "photo-facts-handle"));               // a tap on the handle
    assert.ok(factsUp(ctx) && !el(ctx, "photo-more").classList.contains("hidden"));
    assert.equal(el(ctx, "photo-facts-handle").getAttribute("aria-expanded"), "true");
    assert.equal(factSaid(ctx, "photo-name"), "Rowan's Meet 2.jpg");
    assert.equal(factSaid(ctx, "photo-folder"), "D:\\Photos\\Rowan's Meet");
    assert.equal(factSaid(ctx, "photo-size"), "2.3 MB");
    assert.equal(factSaid(ctx, "photo-camera"), "Pixel 8");
    assert.equal(factSaid(ctx, "photo-lens"), "", "a fact the photo does not hold is no row");
    assert.equal(factSaid(ctx, "photo-kind"), "", "a photo says no kind; a clip does");

    pull(ctx, 120);                                                  // pulled down: the click after the pull does not undo it
    assert.ok(!factsUp(ctx));
    pull(ctx, -120);
    assert.ok(factsUp(ctx));

    click(ctx.window, el(ctx, "photo-edit"));                        // the Edit sheet, as before
    await settle(ctx);
    assert.ok(!el(ctx, "edit-sheet").classList.contains("hidden"));
    ctx.window.history.back();
    await settle(ctx);
    ctx.window.history.back();                                       // the grid, and the facts let down
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.ok(!factsUp(ctx));
  });

  test("a photo with no date, no people and no keywords says so; a record that could not be read says so, its date the card's", async (t) => {
    const ctx = await phone(t, {
      count: 5,
      card: (id) => phoneCard(id, id === 3 ? {} : { taken: null }),
      record: (id) => (id === 1 ? { tags: [ODA] } : {}),
    });
    ctx.server.first("/api/library/photo?id=3", { error: "The library is busy." }, { status: 500 });
    await openPhoto(ctx, 1);
    assert.equal(el(ctx, "photo-when").textContent, "Unknown");
    assert.ok(el(ctx, "photo-when").classList.contains("photo-none"));
    assert.deepEqual(chipsIn(ctx, "photo-people"), ["Oda Fairweather"]);
    assert.equal(noneIn(ctx, "photo-keywords"), "No keywords");
    assert.deepEqual(chipsIn(ctx, "photo-keywords"), []);
    assert.equal(el(ctx, "photo-title").textContent, "");

    next(ctx);
    await settle(ctx);
    assert.equal(noneIn(ctx, "photo-people"), "No people");
    assert.equal(noneIn(ctx, "photo-keywords"), "No keywords");

    next(ctx);                                                       // photo 3: its record cannot be read
    await settle(ctx);
    assert.equal(el(ctx, "photo-when").textContent, "Sat, June 1, 2024, 10:00 AM", "the card's date, at once");
    assert.match(factsProblem(ctx), /^Could not read the title and tags of this photo/);
    assert.deepEqual([noneIn(ctx, "photo-people"), noneIn(ctx, "photo-keywords")], ["", ""], "no chips claimed of a record not read");

    next(ctx);
    await settle(ctx);
    assert.equal(factsProblem(ctx), "", "the next photo says nothing of the last");
  });

  test("a photo deleted on the PC, tapped on a grid not yet told: one sentence, that it is no longer in the library", async (t) => {
    const gone = new Set();
    const ctx = await phone(t, { count: 3, card: (id) => (gone.has(id) ? null : phoneCard(id)) });
    await openPhoto(ctx, 1);
    ctx.window.history.back();
    await settle(ctx);
    gone.add(2);                                                     // deleted on the PC: the grid still holds its card
    click(ctx.window, cards(ctx).find((c) => Number(c.dataset.id) === 2));
    await settle(ctx);
    assert.equal(said(ctx), "This photo is no longer in the library.");
    assert.equal(factsProblem(ctx), "", "not also that its title and tags could not be read");
    assert.deepEqual([noneIn(ctx, "photo-people"), noneIn(ctx, "photo-keywords")], ["", ""], "no chips claimed of a photo gone");

    next(ctx);                                                       // the swipe goes on
    await settle(ctx);
    assert.deepEqual([said(ctx), factsProblem(ctx)], ["", ""]);
  });

  test("a 500-character title is clamped to three lines with more, which pulls the sheet up to it whole", async (t) => {
    const long = "Cousins at the coast before the storm, ".repeat(13).slice(0, 500);
    const ctx = await phone(t, { count: 3, record: () => ({ title: long }) });
    await openPhoto(ctx, 1);
    const title = el(ctx, "photo-title");
    assert.equal(title.textContent, long, "the title whole: the sheet's CSS clamps it");
    assert.ok(el(ctx, "photo-title-more").classList.contains("hidden"), "jsdom lays out nothing: nothing overflows");

    // As a phone lays it out: three lines of 72 px shown of 400.
    Object.defineProperty(title, "scrollHeight", { get: () => 400, configurable: true });
    Object.defineProperty(title, "clientHeight", { get: () => (factsUp(ctx) ? 400 : 72), configurable: true });
    ctx.window.dispatchEvent(new ctx.window.Event("resize"));        // a phone turned measures it again
    assert.ok(!el(ctx, "photo-title-more").classList.contains("hidden"), "more under a title longer than three lines");

    click(ctx.window, el(ctx, "photo-title-more"));
    assert.ok(factsUp(ctx));
    assert.ok(el(ctx, "photo-title-more").classList.contains("hidden"), "the title whole, no more to ask");
    const css = fs.readFileSync(path.join(HERE, "../../web/tagpup/mobile.css"), "utf8");
    assert.match(css, /\.photo-facts:not\(\.open\) \.photo-title \{[^}]*-webkit-line-clamp: 3;/);
  });

  test("40 keywords are 40 chips that wrap; with the sheet down, two rows of them", async (t) => {
    const many = Array.from({ length: 40 }, (_, i) => `Trips/Stop ${String(i + 1).padStart(2, "0")}`);
    const ctx = await phone(t, { count: 3, record: () => ({ tags: [WREN, ...many] }) });
    await openPhoto(ctx, 1);
    assert.equal(chipsIn(ctx, "photo-keywords").length, 40);
    assert.equal(chipsIn(ctx, "photo-keywords")[39], "Stop 40");
    assert.ok(!el(ctx, "photo-edit").closest(".hidden"), "Edit still there over them");
    const css = fs.readFileSync(path.join(HERE, "../../web/tagpup/mobile.css"), "utf8");
    const rule = (selector) => (css.match(new RegExp(`(^|\\n)${selector.replace(/[.():]/g, "\\$&")} \\{([^}]*)\\}`)) || [])[2] || "";
    assert.match(rule(".photo-chips"), /flex-wrap: wrap;/);
    assert.match(rule(".photo-facts:not(.open) .photo-chips"), /max-height: 4\.25rem;/);
    assert.match(rule(".photo-facts:not(.open) .photo-chips"), /overflow: hidden;/);
  });

  test("a photo at midnight and one at noon: 12:05 AM and 12:30 PM, the clock spelled once for the phone and the PC", async (t) => {
    const ctx = await phone(t, {
      count: 3,
      card: (id) => phoneCard(id, { taken: id === 1 ? "2020:02:29 00:05:00" : "2020:03:01 12:30:00" }),
    });
    await openPhoto(ctx, 1);
    assert.equal(el(ctx, "photo-when").textContent, "Sat, February 29, 2020, 12:05 AM");
    next(ctx);
    await settle(ctx);
    assert.equal(el(ctx, "photo-when").textContent, "Sun, March 1, 2020, 12:30 PM");
    const format = fs.readFileSync(path.join(HERE, "../../web/tagpup/format.js"), "utf8");
    assert.equal(format.split("% 12 || 12").length - 1, 1, "one clockSaid: the phone's date, the PC's and the grid's");
  });

  test("a crowded photo on a 360 x 640 phone: Edit beside the date, in reach with the sheet down however many chips", async (t) => {
    // A stand-in for the look in a real browser, which jsdom cannot lay out: the sheet down shows 40% of 640 px (256 px),
    // and its handle, a three-line title and two rows each of people and keywords already take more than that, so Edit
    // stands on the date's line, the first under the handle, never under the chips the sheet cuts off.
    const people = Array.from({ length: 6 }, (_, i) => `People/Wren O'Malley ${i + 1}`);
    const ctx = await phone(t, {
      count: 3,
      record: () => ({ title: "Cousins at the coast before the storm, ".repeat(4), tags: [...people, "Trips/Coast", "Trips/Cove Walk", "Events/Relay", "Events/Harvest"] }),
    });
    await openPhoto(ctx, 1);
    const head = el(ctx, "photo-when").parentElement;
    assert.equal(el(ctx, "photo-edit").parentElement, head, "Edit on the date's line");
    assert.ok(head.classList.contains("photo-head"));
    assert.equal(head.previousElementSibling, el(ctx, "photo-facts-handle"), "the first line under the handle");
    const css = fs.readFileSync(path.join(HERE, "../../web/tagpup/mobile.css"), "utf8");
    const rule = (selector) => (css.match(new RegExp(`(^|\\n)${selector.replace(/[.():]/g, "\\$&")} \\{([^}]*)\\}`)) || [])[2] || "";
    assert.match(rule(".photo-head"), /display: flex;/);
    assert.match(rule(".photo-head .photo-edit"), /flex: none;/, "Edit keeps its size; the date wraps beside it");
    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    assert.ok(!el(ctx, "edit-sheet").classList.contains("hidden"));
  });

  test("a clip's facts: its date, its kind, and Edit", async (t) => {
    const ctx = await phone(t, {
      count: 3,
      card: (id) => phoneCard(id, id === 2 ? { name: "Wren's Kite.mp4", media: "video", taken: "2023:12:31 23:59:00" } : {}),
      record: (id) => (id === 2 ? { title: "Kite at dusk", tags: [WREN] } : {}),
    });
    await openPhoto(ctx, 2);
    assert.ok(!el(ctx, "photo-clip").classList.contains("hidden"));
    assert.equal(el(ctx, "photo-when").textContent, "Sun, December 31, 2023, 11:59 PM");
    assert.equal(el(ctx, "photo-title").textContent, "Kite at dusk");
    assert.deepEqual(chipsIn(ctx, "photo-people"), ["Wren O'Malley"]);
    click(ctx.window, el(ctx, "photo-facts-handle"));
    assert.equal(factSaid(ctx, "photo-kind"), "Clip");
    assert.equal(factSaid(ctx, "photo-name"), "Wren's Kite.mp4");
    assert.ok(!el(ctx, "photo-edit").closest(".hidden"));
  });

  test("a swipe is refused while the sheet is up and goes on once it is down; rapid swipes read the facts one at a time", async (t) => {
    const ctx = await phone(t, { count: 200, record: (id) => ({ title: `Lap ${id}` }) });
    await openPhoto(ctx, 2);
    assert.equal(el(ctx, "photo-title").textContent, "Lap 2");
    click(ctx.window, el(ctx, "photo-facts-handle"));
    next(ctx);
    await settle(ctx);
    assert.deepEqual({ ...pageState(ctx).place }, { id: 2, at: 1 }, "up, the sheet holds the photo");
    assert.equal(el(ctx, "photo-title").textContent, "Lap 2");

    pull(ctx, 120);
    next(ctx);
    await settle(ctx);
    assert.deepEqual({ ...pageState(ctx).place }, { id: 3, at: 2 });
    assert.equal(el(ctx, "photo-title").textContent, "Lap 3", "the facts follow the photo");

    const reads = photoReads(ctx);
    for (let i = 0; i < 60; i += 1) next(ctx);
    await settle(ctx);
    assert.ok(photoReads(ctx) - reads <= 3, `60 swipes read the facts of a few, not each (${photoReads(ctx) - reads})`);
    assert.equal(el(ctx, "photo-title").textContent, "Lap 63", "the facts of the photo landed on");
  });

  test("Back while the facts are read, the same card again: the reply that came after Back is let go, the photo read again", async (t) => {
    let title = "Before the PC's edit";
    const ctx = await phone(t, { count: 3, record: () => ({ title }) });
    const hold = {};
    ctx.server.first("/api/library/photo?id=1", () => {
      ctx.server.routes.shift();
      const reply = { photo: phoneRecord(phoneCard(1), { title }) };
      return new Promise((resolve) => { hold.release = () => resolve(reply); });
    });
    await openPhoto(ctx, 1);
    assert.equal(el(ctx, "photo-title").textContent, "", "still reading");
    ctx.window.history.back();
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    title = "After the PC's edit";                                   // edited on the PC meanwhile
    hold.release();
    await settle(ctx);

    click(ctx.window, cards(ctx).find((c) => Number(c.dataset.id) === 1));
    await settle(ctx);
    assert.equal(el(ctx, "photo-title").textContent, "After the PC's edit", "read again, not the reply that landed on the grid");
    assert.equal(photoReads(ctx), 2);
  });

  test("a phone on its side: the facts down to their handle, pulled up beside the picture, the title measured again", async (t) => {
    const css = fs.readFileSync(path.join(HERE, "../../web/tagpup/mobile.css"), "utf8");
    const sideways = css.split("@media (orientation: landscape) and (max-height: 500px)").slice(1)
      .map((block) => block.split("\n}\n")[0]).find((block) => block.includes(".photo-facts")) || "";
    assert.match(sideways, /--facts-peek: var\(--tap\);/, "the picture keeps all but the handle's height");
    assert.match(sideways, /\.photo-facts:not\(\.open\) > :not\(\.photo-facts-handle\) \{\s*display: none;/);
    assert.match(sideways, /\.photo-facts\.open \{[^}]*width: min\(24rem, 45vw\);/);
    assert.match(sideways, /:has\(\.photo-facts\.open\) \.photo-stage \{\s*margin-right: min\(24rem, 45vw\);/);

    const ctx = await phone(t, { count: 3, record: () => ({ title: "Turned" }) });
    await openPhoto(ctx, 1);
    click(ctx.window, el(ctx, "photo-facts-handle"));
    const steps = ctx.window.history.length;
    ctx.window.dispatchEvent(new ctx.window.Event("orientationchange"));
    ctx.window.dispatchEvent(new ctx.window.Event("resize"));
    await settle(ctx);
    assert.ok(factsUp(ctx), "turned, the sheet stays as it was");
    assert.deepEqual([shown(ctx), ctx.window.history.length], [["photo"], steps]);
    assert.equal(el(ctx, "photo-title").textContent, "Turned");
  });
});
