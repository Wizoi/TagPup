/**
 * Owner scenario: on the phone page (web/tagpup/mobile.html, docs/plans/phone-redesign.md cut 1) the owner moves
 * between its screens by the bar of tabs at the foot of the screen: Library (Browse), Photos (the Grid), Filter (cut 3's
 * sheet over the Grid, web/tagpup/mobile-filter.js) and Libraries (web/tagpup/mobile-tabs.js).
 *
 * - A page on a library opens on Library, its tab lit; a page on no library shows the list of libraries and no bar.
 * - A press is a step of history (mobile-screens.js openScreen): Back goes to the screen the tab was pressed on, the lit
 *   tab following, down to the page's first step, as before the bar. Rapid presses add a step for each change of tab and
 *   none for the tab already shown.
 * - Photos opens the Grid on the view it showed last, or on the whole library before any.
 * - Filter from Browse opens the Grid on the whole library with the Filter sheet over it, two steps: Back closes the sheet,
 *   a second Back leaves the Grid. What the sheet does is the filtering scenario's (scenario-filtering-on-the-phone).
 * - The Photo screen has no bar (the picture on its own canvas); Back to the Grid shows it again.
 * - A phone turned on its side keeps the bar and the screen: nothing is opened or read again.
 *
 * Another library chosen from the Libraries tab, and a server updated while the page is open, are the opening scenario's
 * (scenario-opening-the-phone-page.test.mjs, pressed through the tab).
 *
 * Every name is made up; the real library is photographs of real people, many of them minors.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps, testClock } from "./harness.mjs";
import { PHONE_PAGE, el, shown, settle, phoneCard, phoneLayout, tab } from "./phone-page.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const KEYWORDS = { keywords: [{ tag: "Events", name: "Events", parent: null, count: 3 }], untagged: 0 };

const lit = (ctx) => [...ctx.document.querySelectorAll("#tabs [data-tab]")]
  .filter((t) => t.getAttribute("aria-current") === "page").map((t) => t.dataset.tab);
const barShown = (ctx) => !el(ctx, "tabs").classList.contains("hidden");
const idsAsked = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/library/ids"));

async function press(ctx, element) {
  click(ctx.window, element);
  await settle(ctx);
}

async function back(ctx) {
  ctx.window.history.back();
  await settle(ctx);
}

/** The page on the library alpha, opened at `url`: a keyword of three photos, the whole library of six. */
async function phone(t, url = PHONE_PAGE) {
  const server = new FakeServer()
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/library/navigator?section=keywords", KEYWORDS)
    .on("/api/library/ids", (asked) => {
      const ids = /kind=all/.test(asked) ? [1, 2, 3, 4, 5, 6] : [1, 2, 3];
      return { ids, total: ids.length, of: ids.length, videos: [] };
    })
    .on("/api/library/cards", (asked) => {
      const ids = decodeURIComponent((asked.match(/ids=([^&]+)/) || [])[1] || "").split(",").map(Number);
      return { cards: ids.map((id) => phoneCard(id)) };
    });
  const ctx = await loadApp("phone", { t, server, url });
  phoneLayout(ctx);
  return ctx;
}

afterEach(() => closeAllApps());

describe("moving between the tabs of the phone page", () => {
  test("a page on a library opens on Library: the bar of four, Library lit; Filter opens its sheet over the Grid", async (t) => {
    const ctx = await phone(t);
    const sheetOpen = () => !el(ctx, "filter-sheet").classList.contains("hidden");

    assert.deepEqual(shown(ctx), ["browse"]);
    assert.ok(barShown(ctx));
    assert.deepEqual([...ctx.document.querySelectorAll("#tabs [data-tab]")].map((b) => b.textContent),
      ["Library", "Photos", "Filter", "Libraries"]);
    assert.deepEqual(lit(ctx), ["browse"]);
    assert.ok(!tab(ctx, "filter").disabled);
    assert.equal(el(ctx, "bar-library"), null, "the list of libraries is a tab, not a button in the top bar");
    assert.ok(!sheetOpen());

    const steps = ctx.window.history.length;
    click(ctx.window, tab(ctx, "filter"));
    await press(ctx, tab(ctx, "filter"));
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.ok(sheetOpen(), "the sheet over the Grid");
    assert.equal(el(ctx, "grid-heading").textContent, "The whole library");
    assert.equal(ctx.window.history.length, steps + 2, "the Grid and the sheet over it, two steps however fast the presses");

    await back(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.ok(!sheetOpen(), "Back closes the sheet first");
    await back(ctx);
    assert.deepEqual([shown(ctx), lit(ctx)], [["browse"], ["browse"]], "a second Back leaves the Grid");
  });

  test("a page on no library shows the list of libraries and no bar", async (t) => {
    const ctx = await phone(t, "http://localhost:8090/mobile.html");

    assert.deepEqual(shown(ctx), ["library"]);
    assert.ok(!barShown(ctx), "nothing to browse until a library is chosen");
  });

  test("each tab is one step however fast it is pressed, and Back walks the tabs back to the page's first step", async (t) => {
    const ctx = await phone(t);
    const first = ctx.window.history.length;

    for (let i = 0; i < 3; i += 1) click(ctx.window, tab(ctx, "grid"));
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.deepEqual(lit(ctx), ["grid"]);
    assert.equal(ctx.window.history.length, first + 1, "three presses of Photos, one step");
    assert.equal(el(ctx, "grid-heading").textContent, "The whole library", "before any view, the whole library");
    assert.equal(el(ctx, "grid-count").textContent, "6 photos");
    assert.equal(idsAsked(ctx).length, 1, "read once");

    click(ctx.window, tab(ctx, "library"));
    click(ctx.window, tab(ctx, "library"));
    await settle(ctx);
    click(ctx.window, tab(ctx, "browse"));
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["browse"]);
    assert.deepEqual(lit(ctx), ["browse"]);
    assert.equal(ctx.window.history.length, first + 3, "a step for each change of tab, none for one pressed again");

    await back(ctx);
    assert.deepEqual([shown(ctx), lit(ctx)], [["library"], ["library"]]);
    await back(ctx);
    assert.deepEqual([shown(ctx), lit(ctx)], [["grid"], ["grid"]]);
    assert.equal(el(ctx, "grid-count").textContent, "6 photos");
    await back(ctx);
    assert.deepEqual([shown(ctx), lit(ctx)], [["browse"], ["browse"]]);
    assert.ok(el(ctx, "bar-back").classList.contains("hidden"), "the top of Browse has no Back of its own");
    await back(ctx);
    assert.deepEqual([shown(ctx), lit(ctx)], [["library"], ["library"]], "the page's first step, the list under Browse");
    assert.ok(el(ctx, "bar-back").classList.contains("hidden"), "from here Back leaves the page, as before the bar");
  });

  test("Library from a level of Browse opens its top as a step; pressed again, nothing", async (t) => {
    const ctx = await phone(t);
    await press(ctx, ctx.document.querySelector('[data-section="keywords"]'));
    assert.ok(!el(ctx, "browse-level").classList.contains("hidden"));
    assert.deepEqual(lit(ctx), ["browse"]);
    const steps = ctx.window.history.length;

    await press(ctx, tab(ctx, "browse"));
    await press(ctx, tab(ctx, "browse"));
    assert.ok(el(ctx, "browse-level").classList.contains("hidden"), "the top of Browse");
    assert.equal(ctx.window.history.length, steps + 1);

    await back(ctx);
    assert.ok(!el(ctx, "browse-level").classList.contains("hidden"), "Back is the level it was pressed on");
  });

  test("Photos opens the Grid on the view it showed last", async (t) => {
    const ctx = await phone(t);
    await press(ctx, ctx.document.querySelector('[data-section="keywords"]'));
    await press(ctx, [...el(ctx, "browse-rows").querySelectorAll(".browse-item")].find((r) => r.textContent.includes("Events")));
    await press(ctx, el(ctx, "browse-show"));
    assert.deepEqual([shown(ctx), lit(ctx)], [["grid"], ["grid"]]);
    const heading = el(ctx, "grid-heading").textContent;
    assert.notEqual(heading, "The whole library");

    await press(ctx, tab(ctx, "browse"));
    await press(ctx, tab(ctx, "grid"));
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(el(ctx, "grid-heading").textContent, heading, "the keyword's photos, not the whole library");
    assert.equal(el(ctx, "grid-count").textContent, "3 photos");
    assert.ok(idsAsked(ctx).every((u) => !/kind=all/.test(u)));
  });

  test("the Photo screen has no bar; Back to the Grid shows it again", async (t) => {
    const ctx = await phone(t);
    await press(ctx, tab(ctx, "grid"));
    await press(ctx, el(ctx, "grid-cards").querySelector(".phone-card[data-id]"));
    assert.deepEqual(shown(ctx), ["photo"]);
    assert.ok(!barShown(ctx), "the picture on its own canvas");

    await back(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.ok(barShown(ctx));
    assert.deepEqual(lit(ctx), ["grid"]);
  });

  test("a phone turned on its side keeps the bar and the screen, nothing opened or read again", async (t) => {
    const ctx = await phone(t);
    await press(ctx, tab(ctx, "grid"));
    const steps = ctx.window.history.length;
    const asked = idsAsked(ctx).length;

    ctx.window.dispatchEvent(new ctx.window.Event("orientationchange"));
    ctx.window.dispatchEvent(new ctx.window.Event("resize"));
    await settle(ctx, 200);

    assert.deepEqual([shown(ctx), lit(ctx)], [["grid"], ["grid"]]);
    assert.ok(barShown(ctx));
    assert.equal(ctx.window.history.length, steps);
    assert.equal(idsAsked(ctx).length, asked);
    assert.ok(el(ctx, "grid-cards").querySelector(".phone-card[data-id]"), "the cards still drawn");
  });
});
