/**
 * Owner scenario: on the phone page (web/tagpup/mobile.html, docs/plans/phone-viewer.md, Screens 3) the owner opens a
 * keyword's photos and looks through them as thumbnails, three to a row, scrolling down a view of tens of thousands.
 *
 * - Only the cards of the window in view are asked (GET /api/library/cards, at most 200 ids a request), the next window
 *   as it is scrolled to: the desktop page's own card windows (library-source.js), through the phone's host
 *   (web/tagpup/mobile-grid.js). The real photo_index holds 59,967 photos at the top (counted read-only 2026-11-18).
 * - The order and Photos/Clips are two choices in the head of the Filter sheet over the grid (mobile-filter.js; the
 *   sheet over a keyword's photos is over the same photos as a filter, that keyword marked any of, a search asked in a
 *   POST): a change reads the view again from the top, a reply for the order left is dropped, and the step in history
 *   keeps the choice.
 * - A tap opens the photo (the Photo screen, web/tagpup/mobile-photo.js): one step for
 *   two quick taps, and Back is the grid where it was left, its ids not asked again.
 * - The ids or a window's cards that could not be read say "Could not load photos" with a Try again that works; an empty
 *   view says why.
 *
 * Names hold spaces and apostrophes, as the live libraries' do. Every name here is made up.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps } from "./harness.mjs";
import { PHONE_PAGE, el, shown, pageState, settle, phoneLayout, tab } from "./phone-page.mjs";

const KEYWORDS = { keywords: [{ tag: "Events", name: "Events", parent: null, count: 40000 }], untagged: 0 };
const VIEW = 40000;

const cards = (ctx) => [...el(ctx, "grid-cards").querySelectorAll(".phone-card[data-id]")];
const names = (ctx) => cards(ctx).map((c) => c.querySelector(".phone-card-name").textContent);
const idsAsked = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/library/ids"));

async function tap(ctx, element) {
  click(ctx.window, element);
  await settle(ctx);
}

async function back(ctx) {
  ctx.window.history.back();
  await settle(ctx);
}

/** A view's ids, by the order and the media the request names (in its address, or a search's in its body). */
function idsOf(url, body, { count = VIEW, videos = [] } = {}) {
  const descending = /order=name-desc/.test(url) || (body && body.order) === "name-desc";
  const ids = Array.from({ length: count }, (_, i) => (descending ? 100000 + i : i + 1));
  if (/media=video/.test(url) || (body && body.media) === "video") return { ids: videos, total: videos.length, of: count, videos };
  return { ids, total: ids.length, of: count, videos };
}

/**
 * The page on a library whose keyword holds `count` photos, opened at Browse, the Grid's section given a phone's layout
 * (jsdom has none): 3 columns, 600 px of view. `cardsReply(ids)` answers the cards; `hold` keeps each reply until released.
 */
async function phone(t, { count = VIEW, videos = [], cardsReply, hold = false } = {}) {
  const asked = [];
  const held = [];
  const server = new FakeServer()
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/library/navigator?section=keywords", KEYWORDS)
    .on("/api/library/ids", (url, body) => idsOf(url, body, { count, videos }))
    .on("/api/library/cards", (url) => {
      const ids = decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").map(Number);
      asked.push(ids);
      const body = cardsReply ? cardsReply(ids) : {
        cards: ids.map((id) => ({ id, name: `Rowan's Meet ${id}.jpg`, thumb: `/api/photo-thumb?id=${id}&v=1`, media: "photo" })),
      };
      return hold ? new Promise((resolve) => held.push(() => resolve(body))) : body;
    });
  const ctx = await loadApp("phone", { t, server, url: PHONE_PAGE });
  const { here, section } = phoneLayout(ctx);
  return Object.assign(ctx, {
    asked, here, section,
    release: async () => {
      held.splice(0).forEach((go) => go());
      await settle(ctx);
    },
    scrollTo: async (top) => {
      here.scrollTop = top;
      section.dispatchEvent(new ctx.window.Event("scroll"));
      await settle(ctx, 400);    // the window stands still, its cards are asked (library-source.js FETCH_DELAY_MS)
    },
  });
}

/** Keywords, Events, Show these photos: the Grid on the keyword and everything under it. */
async function openEvents(ctx) {
  await tap(ctx, ctx.document.querySelector('[data-section="keywords"]'));
  await tap(ctx, [...el(ctx, "browse-rows").querySelectorAll(".browse-item")].find((r) => r.textContent.includes("Events")));
  await tap(ctx, el(ctx, "browse-show"));
  await settle(ctx, 100);
}

/** The Filter sheet opened over the Grid by the Filter chip above it: the order and Photos/Clips are in its head. */
async function openSheet(ctx) {
  await tap(ctx, el(ctx, "grid-chips").querySelector(".grid-chip-filter"));
  await settle(ctx, 100);
  assert.ok(!el(ctx, "filter-sheet").classList.contains("hidden"), "the choices are in the sheet's head");
}

/** The last ids request, as the server reads it: a search's in its body. */
const lastIds = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/library/ids")).at(-1);

/** A choice of the Filter sheet's head made (the sheet opened first when it is not). */
async function choose(ctx, id, value) {
  if (el(ctx, "filter-sheet").classList.contains("hidden")) await openSheet(ctx);
  el(ctx, id).value = value;
  el(ctx, id).dispatchEvent(new ctx.window.Event("change"));
  await settle(ctx, 100);
}

afterEach(() => closeAllApps());

describe("looking through a view's photos on the phone", () => {
  test("a keyword of 40,000 photos asks only the cards of the window in view, then the next window as it is scrolled to", async (t) => {
    const ctx = await phone(t);
    await openEvents(ctx);

    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(el(ctx, "grid-count").textContent, "40,000 photos");
    assert.equal(ctx.asked.length, 1, "one request for the first window");
    assert.ok(ctx.asked[0].length <= 200 && Math.max(...ctx.asked[0]) <= 60, `asked ${ctx.asked[0].length}, up to id ${Math.max(...ctx.asked[0])}`);
    assert.ok(cards(ctx).length > 0 && cards(ctx).length < 60, "only the window's cards are drawn");
    assert.equal(names(ctx)[0], "Rowan's Meet 1.jpg");
    assert.ok(cards(ctx)[0].querySelector("img").dataset.src.includes("/alpha/api/photo-thumb?id=1&v=1"), "the library's thumbnail, by id and stamp");

    await ctx.scrollTo(20000);      // row 148: index about 447
    assert.equal(ctx.asked.length, 2, "the window scrolled to is asked");
    const second = ctx.asked[1];
    assert.ok(Math.min(...second) > 380 && Math.max(...second) < 520, `asked ids ${Math.min(...second)}-${Math.max(...second)}`);
    assert.ok(ctx.asked.flat().length < 200, "never the view's 40,000");
  });

  test("changing the order reads the view again from the top and drops the cards answered for the order left", async (t) => {
    const ctx = await phone(t, { count: 500, hold: true });
    await openEvents(ctx);
    assert.equal(ctx.asked.length, 1, "the first window's cards are under way");

    await openSheet(ctx);
    const before = idsAsked(ctx).length;
    await choose(ctx, "grid-order", "name-desc");
    assert.equal(idsAsked(ctx).length, before + 1);
    assert.equal(lastIds(ctx).body.order, "name-desc");
    const place = pageState(ctx).place;
    assert.deepEqual([place.kind, place.order, place.media], ["search", "name-desc", "both"]);
    assert.deepEqual(JSON.parse(JSON.stringify(place.value.all_of)),
      [{ kind: "any_of", value: [{ kind: "keyword", value: "Events", recursive: false }], recursive: false }], "Events marked any of");
    assert.equal(ctx.window.history.state.place.order, "name-desc", "the step in history keeps the order");
    assert.equal(ctx.here.scrollTop, 0, "from the top");
    await ctx.release();     // every window's replies, the order left's first

    assert.equal(names(ctx)[0], "Rowan's Meet 100000.jpg");
    assert.ok(!pageState(ctx).photos.cards.has(1), "no card of the order left is held");
    assert.ok(names(ctx).every((n) => /Meet 1\d{5}\.jpg$/.test(n)), "only the new order's cards are drawn");
  });

  test("Photos/Clips: the clips alone are asked, the count says of how many, and a clip shows its play mark; a stale photo its badge", async (t) => {
    const ctx = await phone(t, {
      count: 5, videos: [3, 5],
      cardsReply: (ids) => ({
        cards: ids.map((id) => ({ id, name: `Clip ${id}.mp4`, thumb: `/api/photo-thumb?id=${id}&v=1`, media: "video", stale: id === 5 ? "missing" : null })),
      }),
    });
    await openEvents(ctx);
    await choose(ctx, "grid-media", "video");

    assert.equal(lastIds(ctx).body.media, "video");
    assert.equal(el(ctx, "grid-count").textContent, "2 videos (of 5)");
    assert.deepEqual(names(ctx), ["Clip 3.mp4", "Clip 5.mp4"]);
    assert.ok(cards(ctx)[0].querySelector(".phone-card-play"), "a clip says it is one");
    assert.equal(cards(ctx)[1].querySelector(".phone-card-stale").textContent, "missing");
    assert.ok(cards(ctx)[1].classList.contains("stale-missing"));
    assert.equal(el(ctx, "grid-media").value, "video");
  });

  test("a tap opens the photo, two quick taps one step; Back is the grid where it was left, its ids not asked again", async (t) => {
    const ctx = await phone(t, { count: 2000 });
    await openEvents(ctx);
    await ctx.scrollTo(3000);
    // A card in view (the grid draws some above it too): a finger reaches only those.
    const card = cards(ctx).find((c) => c.querySelector("img") && 120 + Math.floor(Number(c.dataset.at) / 3) * 134 >= 3000);
    const id = Number(card.dataset.id);
    const steps = ctx.window.history.length;
    const ids = idsAsked(ctx).length;

    click(ctx.window, card);
    click(ctx.window, card);
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["photo"]);
    assert.equal(ctx.window.history.length, steps + 1, "two quick taps are one step");
    assert.equal(el(ctx, "photo-name").textContent, `Rowan's Meet ${id}.jpg`);
    assert.equal(pageState(ctx).place.id, id);

    await back(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(idsAsked(ctx).length, ids, "the view held is shown, not asked again");
    assert.equal(ctx.here.scrollTop, 3000, "where it was left");
    assert.ok(cards(ctx).some((c) => Number(c.dataset.id) === id));
  });

  test("the list of libraries opened over the grid and Back: the grid is where it was left, its ids not asked again (#3145)", async (t) => {
    const ctx = await phone(t, { count: 2000 });
    await openEvents(ctx);
    await ctx.scrollTo(3000);
    const ids = idsAsked(ctx).length;

    await tap(ctx, tab(ctx, "library"));
    assert.deepEqual(shown(ctx), ["library"]);
    await back(ctx);

    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(idsAsked(ctx).length, ids, "the view held is shown, not asked again");
    assert.equal(ctx.here.scrollTop, 3000, "where it was left");
  });

  test("a photo opened and Back, the grid scrolled far away, then the list of libraries and Back: the grid stays where it was scrolled to (#3145)", async (t) => {
    const ctx = await phone(t, { count: 2000 });
    await openEvents(ctx);
    await ctx.scrollTo(3000);
    const card = cards(ctx).find((c) => c.querySelector("img") && 120 + Math.floor(Number(c.dataset.at) / 3) * 134 >= 3000);
    click(ctx.window, card);
    await settle(ctx);
    await back(ctx);
    await ctx.scrollTo(9000);

    await tap(ctx, tab(ctx, "library"));
    await back(ctx);

    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(ctx.here.scrollTop, 9000, "not jumped back to the photo last open");
  });

  test("ids that could not be read say so with Try again, and Try again shows the photos", async (t) => {
    const ctx = await phone(t, { count: 3 });
    ctx.server.first("/api/library/ids", { error: "The library is busy." }, { status: 500 });
    await openEvents(ctx);

    assert.ok(!el(ctx, "grid-problem").classList.contains("hidden"));
    assert.match(el(ctx, "grid-problem-text").textContent, /^Could not load photos\./);
    assert.equal(cards(ctx).length, 0);

    ctx.server.routes.shift();
    await tap(ctx, el(ctx, "grid-retry"));
    await settle(ctx, 100);
    assert.ok(el(ctx, "grid-problem").classList.contains("hidden"));
    assert.equal(el(ctx, "grid-count").textContent, "3 photos");
    assert.deepEqual(names(ctx), ["Rowan's Meet 1.jpg", "Rowan's Meet 2.jpg", "Rowan's Meet 3.jpg"]);
  });

  test("cards that could not be read are tried once more, then say so with Try again, and Try again draws them", async (t) => {
    const ctx = await phone(t, { count: 3 });
    ctx.server.first("/api/library/cards", { error: "The library is busy." }, { status: 500 });
    await openEvents(ctx);
    await settle(ctx, 2300);   // the one more try (library-source.js RETRY_MS)

    assert.ok(!el(ctx, "grid-problem").classList.contains("hidden"), "never a grid of empty cards saying nothing");
    assert.equal(el(ctx, "grid-problem-text").textContent, "Could not load photos.");
    assert.ok(el(ctx, "grid-cards").querySelector(".phone-card.placeholder .phone-card-note"), "each card says it could not load");

    ctx.server.routes.shift();
    await tap(ctx, el(ctx, "grid-retry"));
    await settle(ctx, 100);
    assert.ok(el(ctx, "grid-problem").classList.contains("hidden"));
    assert.equal(names(ctx).length, 3);
  });

  test("an empty view says why and asks for no cards", async (t) => {
    const ctx = await phone(t, { count: 0 });
    await openEvents(ctx);

    assert.equal(el(ctx, "grid-count").textContent, "No photo carries the keyword “Events”, or the library's tag tree has no such keyword.");
    assert.equal(ctx.asked.length, 0);
    assert.equal(cards(ctx).length, 0);
  });
});
