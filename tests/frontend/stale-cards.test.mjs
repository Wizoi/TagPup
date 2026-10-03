/**
 * Cards of photos whose files are not as the library's rows say (web/tagpup/stale.js; phase 9c): a badge on the card
 * -- "changed on disk" or "missing" -- heard by a screen reader; a MISSING photo is shown and cannot be edited (its notice,
 * its write controls inert); a CHANGED photo, opened, was read from its file, and its badge clears. The server side is
 * tests/test_library_cards_stale.py. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click } from "./harness.mjs";
import { loadViewPage, pageErrors, recordOf, GRID_TOP, STRIDE } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const stale = (map) => (id) => (map[id] ? { stale: map[id] } : {});
const badge = (card) => card && card.querySelector(".thumbnail-stale");
const writes = (ctx) => [...ctx.document.querySelectorAll("[data-writes]")];

describe("the badge on a card", () => {
  test("changed and missing are marked on the card, and a good card is not", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", cardExtra: stale({ 12: "changed", 13: "missing" }) });
    assert.equal(badge(ctx.cardById(11)), null);
    assert.equal(badge(ctx.cardById(12)).textContent, "changed on disk");
    assert.equal(badge(ctx.cardById(13)).textContent, "missing");
    assert.ok(ctx.cardById(13).classList.contains("stale-missing"));
    assert.ok(!ctx.cardById(12).classList.contains("stale-missing"));
    assert.ok(ctx.cardById(12).classList.contains("stale"));
  });

  test("a screen reader hears the file name, the date, whether it is selected, and what is wrong", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", cardExtra: stale({ 12: "changed", 13: "missing" }) });
    assert.equal(ctx.cardById(11).getAttribute("role"), "option");
    assert.match(ctx.cardById(11).getAttribute("aria-label"), /^IMG_11\.jpg, .+, not selected$/);
    assert.match(ctx.cardById(12).getAttribute("aria-label"), /, not selected, changed on disk$/);
    assert.match(ctx.cardById(13).getAttribute("aria-label"), /, not selected, file missing$/);
    ctx.cardById(11).click();
    assert.match(ctx.cardById(11).getAttribute("aria-label"), /, selected$/);
    assert.equal(ctx.cardById(11).getAttribute("aria-selected"), "true");
    assert.equal(ctx.cardById(12).getAttribute("aria-selected"), "false");
  });

  test("a picture that cannot be had is a grey card, not a broken-image icon", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", cardExtra: stale({ 13: "missing" }) });
    const img = ctx.cardById(13).querySelector("img");
    img.dispatchEvent(new ctx.window.Event("error"));
    assert.ok(img.classList.contains("failed"));
    assert.equal(img.getAttribute("alt"), "", "the card's label says what it is");
    assert.ok(badge(ctx.cardById(13)), "and its badge still says why");
  });

  test("two hundred stale cards in a batch: the window's are marked, the others have no card at all", async (t) => {
    const ids = Array.from({ length: 200 }, (_, i) => 5000 + i);
    const ctx = await loadViewPage(t, { search: "?view=all", ids, cardExtra: () => ({ stale: "missing" }) });
    const real = ctx.real();
    assert.ok(real.length > 0 && real.length < 60);
    assert.equal(ctx.cards().filter((card) => badge(card)).length, real.length);
    assert.ok(Math.max(...ctx.cardsAsked.map((batch) => batch.length)) <= 200);
  });

  test("a card from a library that did not look (no stale field) is as it was", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all" });
    assert.equal(ctx.cards().filter((card) => badge(card)).length, 0);
    assert.ok(ctx.real().every((card) => !card.classList.contains("stale")));
  });
});

describe("a missing photo: shown, not editable", () => {
  const missingRecord = (id) => recordOf(id, { missing: true });

  async function openMissing(t, extra = {}) {
    const ctx = await loadViewPage(t, {
      search: "?view=all", cardExtra: stale({ 13: "missing" }),
      onPhoto: (id) => ({ photo: id === 13 ? missingRecord(id) : recordOf(id) }), ...extra,
    });
    ctx.cardById(13).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(100);
    return ctx;
  }

  test("opening it shows a read-only notice, and every control that writes is inert", async (t) => {
    const ctx = await openMissing(t);
    const note = ctx.document.getElementById("photo-missing-note");
    assert.ok(!note.classList.contains("hidden"));
    assert.match(note.textContent, /file is not on disk.*cannot be edited/);
    const controls = writes(ctx);
    assert.ok(controls.length >= 6, "rotation, date, title, people, keywords, suggestions");
    assert.ok(controls.every((el) => el.hasAttribute("inert") && el.getAttribute("aria-disabled") === "true"));
    assert.equal(ctx.document.getElementById("main-image").getAttribute("src") || "", "", "no request for a file that is not there");
    assert.ok(ctx.document.getElementById("main-image").classList.contains("hidden"), "and no broken-image icon in its place");
    assert.equal(ctx.document.getElementById("detail-path").textContent, recordOf(13).path);
  });

  test("the next photo is editable again: nothing of the missing one is carried to it", async (t) => {
    const ctx = await openMissing(t);
    ctx.key(ctx.document.body, "ArrowRight");
    await ctx.settle(100);
    assert.equal(ctx.state.library.activeId, 14);
    assert.ok(ctx.document.getElementById("photo-missing-note").classList.contains("hidden"));
    assert.ok(!ctx.document.getElementById("main-image").classList.contains("hidden"));
    assert.ok(writes(ctx).every((el) => !el.hasAttribute("inert") && !el.hasAttribute("aria-disabled")));
  });

  test("every button and field that writes is inside something inert, so a browser neither focuses nor clicks it", async (t) => {
    const ctx = await openMissing(t);
    for (const id of ["btn-rotate-left", "btn-rotate-right", "btn-delete-photo", "btn-save-title", "input-photo-title", "btn-add-person",
      "input-add-person", "btn-add-tag", "input-add-tag", "btn-edit-date-taken", "btn-carry-forward"]) {
      assert.ok(ctx.document.getElementById(id).closest("[inert]"), `${id} is not inert`);
    }
    // (jsdom keeps the attribute and clicks anyway; a browser does not click what is inert. The server refuses a write
    // to a file that is not there as well: tagpup.services.tagging.)
  });
});

describe("a changed photo, opened", () => {
  test("it was read from its file: the badge clears on its card", async (t) => {
    const ctx = await loadViewPage(t, {
      search: "?view=all", cardExtra: stale({ 12: "changed" }),
      onPhoto: (id) => ({ photo: recordOf(id, { tags: ["Trips/Coast", "Added/Elsewhere"] }) }),
    });
    assert.ok(badge(ctx.cardById(12)));
    ctx.cardById(12).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(100);
    assert.deepEqual([...ctx.document.querySelectorAll("#detail-tags .tag-pill, #detail-tags [data-tag]")].length > 0, true);
    assert.equal(ctx.state.library.cards.get(12).stale, undefined);
    ctx.document.getElementById("folder-view-header").click();
    await ctx.settle(100);
    assert.equal(badge(ctx.cardById(12)), null, "the card is current");
    assert.ok(ctx.document.getElementById("photo-missing-note").classList.contains("hidden"));
  });

  test("a view refreshed before sync has brought the row up to date says changed again, which is true", async (t) => {
    const ctx = await loadViewPage(t, { search: "?view=all", cardExtra: stale({ 12: "changed" }) });
    ctx.cardById(12).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(100);
    ctx.document.getElementById("folder-view-header").click();
    await ctx.settle(100);
    ctx.document.getElementById("btn-library-refresh").click();
    await ctx.settle(200);
    assert.ok(badge(ctx.cardById(12)));
  });

  test("a photo that was marked changed and is found missing when it is opened is marked missing", async (t) => {
    const ctx = await loadViewPage(t, {
      search: "?view=all", cardExtra: stale({ 12: "changed" }),
      onPhoto: (id) => ({ photo: recordOf(id, { missing: true }) }),
    });
    ctx.cardById(12).querySelector(".btn-thumbnail-detail").click();
    await ctx.settle(100);
    assert.equal(ctx.state.library.cards.get(12).stale, "missing");
    ctx.document.getElementById("folder-view-header").click();
    await ctx.settle(100);
    assert.equal(badge(ctx.cardById(12)).textContent, "missing");
  });
});
