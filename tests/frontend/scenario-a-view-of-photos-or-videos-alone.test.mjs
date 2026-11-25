/**
 * Scenario: the owner looks at a view's videos alone (Show, in a library view's header: Both, Photos or Videos;
 * web/tagpup/filter-menu.js). They choose Videos in a view of 40 items, 10 of them clips; the view is read again of the clips,
 * the address keeps it, the count says of how many, and the next view they open from the navigator shows clips too. Select all
 * then names the clips alone: a bulk edit or a Delete of a Select all under Videos must never reach a photo, so the request's
 * `source` carries `media` (the data risk). Choices clicked faster than the library answers leave the last one standing; a view
 * the filter empties says why; another library, or the page loaded again, opens on Both, while Back within the page returns
 * each view with its own filter. The menu's keys are those of Sort by beside it (web/tagpup/radio-menu.js).
 *
 * The library is invented; the real one is photographs of real people, many of them minors.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { closeAllApps, click, testClock } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

/** The delete-check reply as tagpup.web.tagpup_routes makes it (tests/test_batch_strip_replies.py). */
const served = JSON.parse(fs.readFileSync(new URL("./fixtures/batches.json", import.meta.url), "utf8"));
const range = (n, from = 1) => Array.from({ length: n }, (_, i) => from + i);
const plain = (value) => JSON.parse(JSON.stringify(value));
const el = (ctx, id) => ctx.document.getElementById(id);
const ALL = range(40);
const CLIPS = ALL.filter((id) => id % 4 === 0);
const PHOTOS = ALL.filter((id) => id % 4 !== 0);

/**
 * GET /api/library/ids as the server answers it (tagpup.services.library_view.ids): the source's ids of the kind `media` names
 * (none: both), `videos` the clips among them, `of` how many the source holds of either kind.
 */
function idsReply(all, clips) {
  const isClip = new Set(clips);
  return (url) => {
    const params = new URL(url, "http://localhost").searchParams;
    const media = params.get("media");
    const ids = media === "video" ? all.filter((id) => isClip.has(id)) : media === "photo" ? all.filter((id) => !isClip.has(id)) : all;
    return {
      source: { kind: params.get("kind"), value: params.get("value"), recursive: params.get("recursive") === "1" },
      order: params.get("order") || "taken", media, total: ids.length, ids, complete: true,
      videos: ids.filter((id) => isClip.has(id)), of: all.length,
    };
  };
}

async function view(t, search = "?view=all", { all = ALL, clips = CLIPS } = {}) {
  const ctx = await loadViewPage(t, {
    search, onIds: idsReply(all, clips), cardExtra: (id) => ({ media: clips.includes(id) ? "video" : "photo" }),
  });
  ctx.questions = [];
  ctx.window.confirm = (question) => { ctx.questions.push(question); return true; };
  return ctx;
}

const button = (ctx) => el(ctx, "btn-filter-media");
const menu = (ctx) => el(ctx, "filter-menu");
const items = (ctx) => [...menu(ctx).querySelectorAll("[role='menuitemradio']")];
const item = (ctx, label) => items(ctx).find((each) => each.textContent === label);
const checked = (ctx) => items(ctx).filter((each) => each.getAttribute("aria-checked") === "true").map((each) => each.textContent);
const isOpen = (ctx) => !menu(ctx).classList.contains("hidden");
const asked = (ctx) => new URL(ctx.idsAsked.at(-1), "http://localhost").searchParams;
const total = (ctx) => el(ctx, "library-strip-total").textContent;
const said = (ctx) => el(ctx, "library-strip-status").textContent;
const press = (ctx, key, target = ctx.document.activeElement) => ctx.key(target, key);
const calls = (ctx, part) => ctx.server.calls.filter((call) => call.url.includes(part));

async function show(ctx, label) {
  click(ctx.window, button(ctx));
  click(ctx.window, item(ctx, label));
  await ctx.settle();
}

describe("Show: a view of photos alone, or videos alone", () => {
  test("choosing Videos asks again with media=video; the address keeps it, the count says of how many, the button says so", async (t) => {
    const ctx = await view(t);
    assert.equal(button(ctx).getAttribute("aria-label"), "Show: Both");
    assert.ok(!button(ctx).classList.contains("active"));
    assert.equal(asked(ctx).get("media"), null, "Both asks for no kind");
    await show(ctx, "Videos");
    assert.equal(asked(ctx).get("media"), "video");
    assert.equal(ctx.window.location.search, "?view=all&media=video");
    assert.deepEqual(ctx.state.library.ids, CLIPS);
    assert.equal(total(ctx), "10 videos (of 40)");
    assert.equal(button(ctx).getAttribute("aria-label"), "Show: Videos");
    assert.ok(button(ctx).classList.contains("active"), "lit while it shows one kind alone");
    click(ctx.window, button(ctx));
    assert.deepEqual(checked(ctx), ["Videos"]);
    assert.equal(ctx.document.activeElement, item(ctx, "Videos"), "the menu opens on the choice");
  });

  test("the next view opened from the navigator keeps the filter", async (t) => {
    const ctx = await view(t);
    await show(ctx, "Videos");
    await ctx.openTab("keywords");
    click(ctx.window, ctx.rowByLabel("keywords", "Events"));
    await ctx.settle();
    assert.equal(asked(ctx).get("kind"), "keyword");
    assert.equal(asked(ctx).get("media"), "video");
    assert.equal(ctx.window.location.search, "?view=keyword&value=Events&media=video");
    assert.equal(button(ctx).getAttribute("aria-label"), "Show: Videos");
  });

  test("Select all under Videos: the bulk edit and the delete check name the clips alone, never the photos beside them", async (t) => {
    const ctx = await view(t);
    await show(ctx, "Videos");
    ctx.bulk.edit = { total: 10, changed: 10 };
    el(ctx, "btn-select-all-thumbnails").click();
    el(ctx, "bulk-add-tags-input").value = "Trips/Lighthouse";
    click(ctx.window, el(ctx, "btn-bulk-add-tags"));
    await ctx.settle(80);
    const clipsAlone = { kind: "all", value: null, recursive: false, media: "video" };
    const [edit] = calls(ctx, "/api/library/bulk-edit");
    assert.deepEqual(plain(edit.body.selection), { source: clipsAlone, excluded: [] }, "the tags go on the clips alone");
    ctx.server.on("/api/library/selection/delete-check", { ...served.delete_check, total: 10 });
    ctx.bulk.edit = { op: "delete", total: 10, changed: 10 };
    click(ctx.window, el(ctx, "btn-delete-selection"));
    await ctx.settle(80);
    const [check] = calls(ctx, "/selection/delete-check");
    assert.deepEqual(plain(check.body), { selection: { source: clipsAlone, excluded: [] } }, "the delete is checked of the clips alone");
    const remove = calls(ctx, "/api/library/bulk-edit").at(-1);
    assert.deepEqual(plain(remove.body), { op: "delete", selection: { source: clipsAlone, excluded: [] }, params: { token: served.delete_check.token } },
      "and deletes the clips alone");
  });

  test("rapid choices while the library has not answered: only the last one stands", async (t) => {
    const ctx = await view(t);
    ctx.hold.ids = true;
    await show(ctx, "Videos");
    await show(ctx, "Both");
    await show(ctx, "Photos");
    await ctx.release("ids");
    await ctx.settle();
    assert.equal(ctx.state.library.media, "photo");
    assert.equal(ctx.state.library.status, "ready");
    assert.deepEqual(ctx.state.library.ids, PHOTOS, "the clips' answer, come late, is not the view");
    assert.equal(ctx.window.location.search, "?view=all&media=photo");
    assert.equal(total(ctx), "30 photos (of 40)");
    assert.equal(button(ctx).getAttribute("aria-label"), "Show: Photos");
  });

  test("a view the filter empties says why, and the count says of how many it holds", async (t) => {
    const ctx = await view(t, "?view=all", { all: range(1204), clips: [] });
    await show(ctx, "Videos");
    assert.equal(ctx.state.library.status, "empty");
    assert.equal(said(ctx), "No videos here: the 1,204 in this view are all photos. Choose Both to see them.");
    assert.equal(total(ctx), "0 videos (of 1,204)");
    const one = await view(t, "?view=all", { all: [7], clips: [7] });
    await show(one, "Photos");
    assert.equal(said(one), "No photos here: the one in this view is a video. Choose Both to see it.");
    assert.equal(total(one), "0 photos (of 1)");
  });

  test("going to another library takes the filter out of the address", async () => {
    const went = [];
    const saved = { window: globalThis.window, localStorage: globalThis.localStorage };
    globalThis.window = { location: { search: "?view=keyword&value=Trips&media=video&path=D%3A%5CX", set href(v) { went.push(v); } } };
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    try {
      const { goToLibrary } = await import("../../web/common/library.js");
      goToLibrary("other");
    } finally {
      globalThis.window = saved.window;
      globalThis.localStorage = saved.localStorage;
      if (saved.window === undefined) delete globalThis.window;
      if (saved.localStorage === undefined) delete globalThis.localStorage;
    }
    assert.deepEqual(went, ["/other/?path=D%3A%5CX"]);
  });

  test("a page loaded with media=video opens on Both and drops it from the address; Back within the page returns each view's own", async (t) => {
    const ctx = await view(t, "?view=all&media=video");
    assert.equal(asked(ctx).get("media"), null, "the page loaded asks for both");
    assert.equal(ctx.window.location.search, "?view=all");
    assert.equal(button(ctx).getAttribute("aria-label"), "Show: Both");
    assert.deepEqual(ctx.state.library.ids, ALL);
    await show(ctx, "Videos");
    ctx.module("library-view.js").openLibraryView({ kind: "keyword", value: "Events" });
    await ctx.settle();
    assert.equal(ctx.window.location.search, "?view=keyword&value=Events&media=video");
    await show(ctx, "Both");
    assert.equal(ctx.window.location.search, "?view=keyword&value=Events");
    for (const [search, media, label] of [
      ["?view=keyword&value=Events&media=video", "video", "Show: Videos"],
      ["?view=all&media=video", "video", "Show: Videos"],
      ["?view=all", null, "Show: Both"],
    ]) {
      ctx.window.history.back();
      await ctx.settle(300);
      assert.equal(ctx.window.location.search, search);
      assert.equal(asked(ctx).get("media"), media, `${search} is read as it was shown`);
      assert.equal(button(ctx).getAttribute("aria-label"), label);
    }
    ctx.window.history.forward();
    await ctx.settle(300);
    assert.equal(ctx.window.location.search, "?view=all&media=video", "Forward too");
    assert.deepEqual(ctx.state.library.ids, CLIPS);
  });

  test("a folder of 1,500 under Videos is still a large folder: no walk of the disk for its 10 clips until Refresh view", async (t) => {
    const folder = "D:\\Library\\2020\\Event 01";
    const all = range(1500, 1000);
    const ctx = await view(t, `?view=folder&value=${encodeURIComponent(folder)}&recursive=1`, { all, clips: all.slice(0, 10) });
    await show(ctx, "Videos");
    assert.equal(total(ctx), "10 videos (of 1,500)");
    assert.equal(ctx.membershipAsked.length, 0, "the folder's size, whatever Show shows");
    assert.ok(el(ctx, "moves-banner").classList.contains("hidden"), "nothing said until it is asked");
  });

  test("the keys: ArrowDown opens on the choice, arrows wrap, Enter and Space choose, Escape and Tab close, the photo is not stepped", async (t) => {
    const ctx = await view(t);
    const before = ctx.idsAsked.length;
    const photos = ctx.photosAsked.length;
    button(ctx).focus();
    press(ctx, "ArrowDown");
    assert.ok(isOpen(ctx));
    assert.equal(button(ctx).getAttribute("aria-expanded"), "true");
    assert.equal(ctx.document.activeElement, item(ctx, "Both"));
    press(ctx, "ArrowUp");
    assert.equal(ctx.document.activeElement, item(ctx, "Videos"), "up from the first wraps to the last");
    press(ctx, "ArrowDown");
    assert.equal(ctx.document.activeElement, item(ctx, "Both"), "down from the last wraps to the first");
    press(ctx, "End");
    assert.equal(ctx.document.activeElement, item(ctx, "Videos"));
    press(ctx, "Home");
    press(ctx, "ArrowRight");
    press(ctx, "ArrowLeft");
    assert.equal(ctx.document.activeElement, item(ctx, "Both"), "one column: left and right go nowhere");
    press(ctx, "Escape");
    assert.ok(!isOpen(ctx));
    assert.equal(ctx.document.activeElement, button(ctx));
    await ctx.settle(20);
    assert.equal(ctx.idsAsked.length, before, "Escape chooses nothing");
    assert.equal(ctx.photosAsked.length, photos, "the arrow keys in the menu do not step the open photo");
    press(ctx, "ArrowUp");
    assert.equal(ctx.document.activeElement, item(ctx, "Both"), "ArrowUp opens on the choice too");
    press(ctx, "ArrowDown");
    const enter = press(ctx, "Enter");
    assert.ok(enter.defaultPrevented);
    await ctx.settle();
    assert.equal(asked(ctx).get("media"), "photo");
    assert.ok(!isOpen(ctx));
    assert.equal(ctx.document.activeElement, button(ctx));
    press(ctx, "ArrowDown");
    assert.equal(ctx.document.activeElement, item(ctx, "Photos"));
    press(ctx, "Enter");
    await ctx.settle();
    assert.equal(asked(ctx).get("media"), "photo", "the choice already made asks for nothing");
    assert.equal(ctx.idsAsked.length, before + 1);
    press(ctx, "ArrowDown");
    press(ctx, "ArrowDown");
    press(ctx, " ");
    await ctx.settle();
    assert.equal(asked(ctx).get("media"), "video", "Space chooses");
    press(ctx, "ArrowDown");
    press(ctx, "Tab");
    assert.ok(!isOpen(ctx), "Tab closes it");
    click(ctx.window, button(ctx));
    el(ctx, "thumbnails-grid").dispatchEvent(new ctx.window.MouseEvent("mousedown", { bubbles: true }));
    assert.ok(!isOpen(ctx), "a click outside closes it");
    assert.equal(button(ctx).getAttribute("aria-expanded"), "false");
    click(ctx.window, button(ctx));
    await ctx.popTo("?view=all");
    assert.ok(!isOpen(ctx), "drawn for the view that has gone");
    assert.equal(ctx.document.activeElement, button(ctx));
  });
});
