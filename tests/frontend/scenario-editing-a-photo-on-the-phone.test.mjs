/**
 * Owner scenario: on the phone page (web/tagpup/mobile.html, docs/plans/phone-viewer.md, Screens 5) the owner opens a
 * photo, taps Edit, and changes its people, keywords, title and Date Taken from the sheet (web/tagpup/mobile-edit.js).
 *
 * - A tag added or taken off is one path's change through /api/photos/write (op `tags`), the photo named by its id,
 *   against what the file holds: no stamp. Renamed on the PC since it was read, the write is refused (changed_on_disk);
 *   the sheet reads the photo again by its id and sends it once more, by the name the file has now.
 * - The sheet is a step of the history, its one record of being open: Back closes it, the text typed kept, and a second
 *   Back leaves the photo; Back to it from another screen opens it again; the page back from the back-forward cache, Close
 *   then Edit tapped at once, a tap on the picture above it: the sheet is always as its step says.
 * - A chip's x waits behind its own Undo; an Undo in time sends nothing; leaving the photo (a swipe, Back, the Libraries
 *   button) or the page hidden (another app, pagehide; keepalive, the sheet left open) sends what is still waiting;
 *   behind a Save still out (itself keepalive), it goes after the Save, never beside it, and names the file by the name
 *   a title gave it.
 * - Two chips of one leaf say their roots.
 * - Each write waits for the one before it: a tag added while the Save is out is sent after it.
 * - The title and Date Taken go through /api/photos/write too (op `set`), with the stamp and base of the file as read; a
 *   title that renames the file renames the card the page holds. A file changed on the PC is never written over: the
 *   sheet says so, reads it again and keeps the text typed.
 * - A dropped connection or a server that cannot answer says a sentence, the sheet open, the text kept; a refusal while
 *   the server is updated is waited out (web/common/api.js).
 * - Close with a title or date typed asks; Back or the Libraries button keeps it, and the photo's Edit shows it again.
 * - A chip taken off stays off on the photo's sheet read again (Edit opened again, a Save refused and read again) while
 *   its removal waits, and the removal lands on the sheet shown: the next Save names the file as the removal left it.
 *
 * Names hold spaces and apostrophes, as the live libraries' do. Every name here is made up. The Undo's five seconds are
 * shortened to UNDO here: the page's timers are what it would wait on.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps, pageExports, PEOPLE_ROUTE, PHOTO_WRITES, CLIP_WRITES, SAVE, lastSave, saveCalls,
  saved, changedOnDisk, taggedAs } from "./harness.mjs";
import { PHONE_PAGE, el, shown, pageState, settle, phoneCard, phoneLayout } from "./phone-page.mjs";

const KEYWORDS = { keywords: [{ tag: "Events", name: "Events", parent: null, count: 20 }], untagged: 0 };
const UNDO = 200;
const PATH = "D:\\Photos\\Wren's Meet\\Wren's Meet 1.jpg";
const WREN = "People/Wren O'Malley";
const JUNIPER = "People/Juniper Okafor";
const COAST = "Trips/Coast";
const COVE = "Trips/Cove Walk";
const TAGS = [WREN, JUNIPER, COAST, COVE, "Events/Harvest", "Places/Harvest", "Events/Relay"];
const PEOPLE = [{ id: 1, name: "Wren O'Malley", tag: WREN }, { id: 2, name: "Juniper Okafor", tag: JUNIPER }];

const vocabulary = (ctx) => pageExports(ctx.window, "web/common/vocabulary.js");

/**
 * The page on a library of 20 photos, the first a file `file` that the fake server writes as the real one does: a tag
 * change through photos/write (op `tags`), the title and date through it too (op `set`), each a new stamp;
 * /api/library/photo reads it.
 * A Save naming another stamp or base than the file's is refused (409, changed_on_disk), as the server refuses it; so is
 * a tag change naming photo 1 at a path it is no longer at (photo_writes._moved), nothing written.
 */
async function phone(t, file = {}) {
  const server = new FakeServer();
  const held = Object.assign({ path: PATH, tags: [WREN, COAST], title: "Finish line", taken: "2024:06:01 10:00:00",
    mtime: 1718000000.5, size: 204800, writes: PHOTO_WRITES, media: "photo" }, file);
  const record = () => ({ id: 1, ...held, filename: held.path.split("\\").pop(), tags: held.tags.slice(), people: [], raw_metadata: {} });
  server
    .on("/api/databases", { databases: ["alpha", "beta"] })
    .on("/api/library/navigator?section=keywords", KEYWORDS)
    .on("/api/library/ids", { ids: Array.from({ length: 20 }, (_, i) => i + 1), total: 20, of: 20, videos: [] })
    .on("/api/library/cards", (url) => ({
      cards: decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").map((id) => phoneCard(Number(id), {}, "Wren's Meet")) }))
    .on("/api/library/photo", () => ({ photo: record() }))
    .on("/api/tags", TAGS)
    .on(PEOPLE_ROUTE, PEOPLE)
    .on("/api/photos/write", (url, sent) => {
      const [[path, change]] = Object.entries(sent.changes);
      if (change.id === 1 && path !== held.path) return taggedAs("renamed_since")(url, sent);
      held.tags = held.tags.filter((tag) => !(change.remove || []).includes(tag))
        .concat((change.add || []).filter((tag) => !held.tags.includes(tag)));
      held.mtime += 1;
      return taggedAs("written", () => ({ tags: held.tags.slice(), mtime: held.mtime, size: held.size }))(url, sent);
    })
    .on(SAVE, (url, sent) => {
      const body = lastSave(server);
      if (body.stamp.mtime !== held.mtime || JSON.stringify(body.base.tags) !== JSON.stringify(held.tags)) {
        return changedOnDisk("The file changed on disk.")();
      }
      held.title = body.title;
      if (body.date_taken) held.taken = body.date_taken.replace(/-/g, ":").replace("T", " ");
      held.mtime += 1;
      return saved({ mtime: held.mtime, size: held.size, taken: held.taken, base: { tags: held.tags.slice(), title: held.title } })(url, sent);
    });
  const ctx = await loadApp("phone", { t, server, url: PHONE_PAGE });
  phoneLayout(ctx);
  // What each request asked of the browser: a write sent as the page goes must outlive it (keepalive).
  const sent = [];
  const fetch = ctx.window.fetch;
  ctx.window.fetch = (input, init) => {
    sent.push({ url: String(input), keepalive: Boolean(init && init.keepalive), body: init && init.body ? JSON.parse(init.body) : undefined });
    return fetch(input, init);
  };
  // The Undo's five seconds, shortened: every other timer as the page set it.
  const setTimer = ctx.window.setTimeout.bind(ctx.window);
  ctx.window.setTimeout = (job, ms, ...rest) => setTimer(job, ms === 5000 ? UNDO : ms, ...rest);
  return Object.assign(ctx, { held, sent });
}

/** Keywords, Events, Show these photos, a tap on the first card, and Edit. */
async function openSheet(ctx) {
  click(ctx.window, ctx.document.querySelector('[data-section="keywords"]'));
  await settle(ctx);
  click(ctx.window, [...el(ctx, "browse-rows").querySelectorAll(".browse-item")].find((r) => r.textContent.includes("Events")));
  await settle(ctx);
  click(ctx.window, el(ctx, "browse-show"));
  await settle(ctx, 80);
  await openPhotoEdit(ctx);
}

async function openPhotoEdit(ctx) {
  click(ctx.window, [...el(ctx, "grid-cards").querySelectorAll(".phone-card[data-id]")].find((c) => c.dataset.id === "1"));
  await settle(ctx);
  click(ctx.window, el(ctx, "photo-edit"));
  await settle(ctx);
}

const chips = (ctx, id) => [...el(ctx, id).querySelectorAll(".edit-chip")].map((chip) => chip.firstChild.textContent);
const chipX = (ctx, tag) => [...ctx.document.querySelectorAll(".edit-chip-x")].find((x) => x.dataset.tag === tag);
const toasts = (ctx) => [...el(ctx, "edit-toasts").querySelectorAll(".edit-toast")];
const said = (ctx) => (el(ctx, "edit-problem").classList.contains("hidden") ? "" : el(ctx, "edit-problem").textContent);
const sheetOpen = (ctx) => !el(ctx, "edit-sheet").classList.contains("hidden");
const calls = (ctx, route) => ctx.server.calls.filter((c) => c.url.includes(route));
const isTagWrite = (c) => c.url.includes("/api/photos/write") && !SAVE(c.url, c.body);
const tagWrites = (ctx) => ctx.server.calls.filter(isTagWrite).map((c) => c.body.changes);

function type(ctx, id, value) {
  el(ctx, id).value = value;
  el(ctx, id).dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
}

async function pick(ctx, text, tag) {
  type(ctx, "edit-add", text);
  const match = [...el(ctx, "edit-matches").querySelectorAll(".edit-match")].find((m) => m.dataset.tag === tag);
  assert.ok(match, `${tag} is offered for "${text}"`);
  click(ctx.window, match);
  await settle(ctx);
}

/** A route answered later: `release(reply)` sends the reply, a route's function of (url, body) (saved) or its body. */
function holdNext(ctx, route) {
  const hold = {};
  ctx.server.first(route, (url, body) => {
    ctx.server.routes.shift();
    return new Promise((resolve) => { hold.release = (reply) => resolve(typeof reply === "function" ? reply(url, body) : reply); });
  });
  return hold;
}

afterEach(() => closeAllApps());

describe("editing a photo on the phone", () => {
  test("a person and a keyword added and taken off: one path each through photos/write, no stamp", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    const { leafOf } = vocabulary(ctx);
    assert.deepEqual(chips(ctx, "edit-people"), [leafOf(WREN)]);
    assert.deepEqual(chips(ctx, "edit-keywords"), [leafOf(COAST)]);
    type(ctx, "edit-add", "wren");
    assert.equal(el(ctx, "edit-matches").children.length, 0, "a person the photo holds is not offered again");

    await pick(ctx, "jun", JUNIPER);
    await pick(ctx, "cove", COVE);
    assert.equal(el(ctx, "edit-add").value, "", "the add box is empty for the next");
    click(ctx.window, chipX(ctx, COAST));
    click(ctx.window, chipX(ctx, WREN));
    await settle(ctx, UNDO + 80);

    assert.deepEqual(tagWrites(ctx), [{ [PATH]: { id: 1, add: [JUNIPER] } }, { [PATH]: { id: 1, add: [COVE] } },
      { [PATH]: { id: 1, remove: [COAST] } }, { [PATH]: { id: 1, remove: [WREN] } }], "one path's change each, nothing of a stamp or base");
    assert.deepEqual(ctx.held.tags, [JUNIPER, COVE]);
    assert.deepEqual(chips(ctx, "edit-people"), [leafOf(JUNIPER)]);
    assert.deepEqual(chips(ctx, "edit-keywords"), [leafOf(COVE)]);
    assert.equal(said(ctx), "");
  });

  test("rapid removals each have their own Undo; an Undo in time sends nothing", async (t) => {
    const ctx = await phone(t, { tags: [WREN, COAST, "Events/Relay", COVE] });
    await openSheet(ctx);
    for (const tag of [COAST, "Events/Relay", COVE]) click(ctx.window, chipX(ctx, tag));
    await flush(ctx.window);
    assert.equal(toasts(ctx).length, 3, "a line with its Undo for each");
    assert.deepEqual(chips(ctx, "edit-keywords"), [], "each chip goes at its tap");

    click(ctx.window, toasts(ctx)[1].querySelector(".edit-undo"));
    await flush(ctx.window);
    assert.deepEqual(chips(ctx, "edit-keywords"), ["Relay"], "the chip undone is back");
    assert.equal(toasts(ctx).length, 2);
    assert.equal(tagWrites(ctx).length, 0, "nothing is sent while the Undo can still be tapped");

    await settle(ctx, UNDO + 80);
    assert.deepEqual(tagWrites(ctx), [{ [PATH]: { id: 1, remove: [COAST] } }, { [PATH]: { id: 1, remove: [COVE] } }]);
    assert.deepEqual(ctx.held.tags, [WREN, "Events/Relay"], "the tag undone was never taken off");
    assert.equal(toasts(ctx).length, 0);
  });

  for (const [how, leave, stays = false] of [
    ["a swipe to the next photo", async (ctx) => {
      click(ctx.window, el(ctx, "edit-close"));       // the sheet open, a swipe is not taken
      await settle(ctx);
      const stage = el(ctx, "photo-stage");
      for (const [type, x] of [["pointerdown", 200], ["pointerup", 80]]) {
        const event = new ctx.window.MouseEvent(type, { bubbles: true, clientX: x, clientY: 300, button: 0 });
        Object.defineProperty(event, "pointerId", { value: 7 });
        Object.defineProperty(event, "pointerType", { value: "touch" });
        stage.dispatchEvent(event);
      }
    }],
    ["Back out of the photo (the sheet, then the photo)", async (ctx) => {
      ctx.window.history.back();
      await settle(ctx);
      ctx.window.history.back();
    }],
    ["the Libraries button", (ctx) => click(ctx.window, el(ctx, "bar-library"))],
    ["the page going", (ctx) => ctx.window.dispatchEvent(new ctx.window.Event("pagehide")), true],
    ["another app shown (Android may then discard the tab, its timers frozen)", (ctx) => {
      Object.defineProperty(ctx.document, "visibilityState", { value: "hidden", configurable: true });
      ctx.document.dispatchEvent(new ctx.window.Event("visibilitychange"));
    }, true],
  ]) {
    test(`a removal still behind its Undo is sent on ${how}, not dropped`, async (t) => {
      const ctx = await phone(t);
      await openSheet(ctx);
      click(ctx.window, chipX(ctx, COAST));
      await leave(ctx);
      await settle(ctx);
      assert.deepEqual(tagWrites(ctx), [{ [PATH]: { id: 1, remove: [COAST] } }], "sent at once, not after the Undo's time");
      assert.ok(ctx.sent.find((s) => s.url.includes("/api/photos/write")).keepalive, "a write that outlives the page");
      assert.equal(sheetOpen(ctx), stays, stays ? "the sheet as its step left it, for the page coming back" : "the sheet shut");
      assert.equal(toasts(ctx).length, 0, "its Undo is gone with it");
      assert.deepEqual(chips(ctx, "edit-keywords"), [], "the chip stays off");
    });
  }

  test("two chips of one leaf say their roots", async (t) => {
    const ctx = await phone(t, { tags: [WREN, "Events/Harvest", "Places/Harvest", COAST] });
    await openSheet(ctx);
    const { leafOf, rootOf } = vocabulary(ctx);
    assert.deepEqual(chips(ctx, "edit-keywords").sort(), [leafOf(COAST),
      `${leafOf("Events/Harvest")} (${rootOf("Events/Harvest")})`, `${leafOf("Places/Harvest")} (${rootOf("Places/Harvest")})`].sort());
  });

  test("a tag added while the Save is out waits for it", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    const save = holdNext(ctx, SAVE);
    type(ctx, "edit-title", "Wren's Last Lap");
    click(ctx.window, el(ctx, "edit-save"));
    await pick(ctx, "cove", COVE);
    assert.equal(tagWrites(ctx).length, 0, "not sent while the Save is out");

    save.release(saved({ mtime: 1718000009, size: 204800, base: { tags: [WREN, COAST], title: "Wren's Last Lap" } }));
    await settle(ctx);
    assert.deepEqual(tagWrites(ctx), [{ [PATH]: { id: 1, add: [COVE] } }]);
    assert.ok(ctx.server.calls.findIndex(isTagWrite) > ctx.server.calls.findIndex((c) => SAVE(c.url, c.body)));
    assert.deepEqual(chips(ctx, "edit-keywords"), [leaf(ctx, COAST), leaf(ctx, COVE)]);
  });

  test("the title and Date Taken are saved with the stamp and base read; a title that renames the file renames its card", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    const renamed = "D:\\Photos\\Wren's Meet\\Wren's Last Lap.jpg";
    ctx.server.first(SAVE, saved({ new_path: renamed, mtime: 1718000077, size: 204900,
      taken: "2024:06:02 09:30:00", base: { tags: [WREN, COAST], title: "Wren's Last Lap" } }));
    assert.equal(el(ctx, "edit-title").value, "Finish line");
    assert.equal(el(ctx, "edit-date").value, "2024-06-01");
    type(ctx, "edit-title", "Wren's Last Lap");
    type(ctx, "edit-date", "2024-06-02");
    type(ctx, "edit-time", "09:30");                   // a time input may leave the seconds out
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);

    assert.deepEqual(saveCalls(ctx.server).map((c) => c.body), [{ path: PATH, id: 1, title: "Wren's Last Lap",
      tags: [WREN, COAST], stamp: { mtime: 1718000000.5, size: 204800 }, base: { tags: [WREN, COAST], title: "Finish line" },
      date_taken: "2024-06-02T09:30:00" }]);
    assert.equal(said(ctx), "Saved.");
    const card = pageState(ctx).photos.cards.get(1);
    assert.equal(card.path, renamed, "the grid's card names the file as it is now");
    assert.equal(card.name, "Wren's Last Lap.jpg");
    assert.equal(card.mtime, 1718000077, "and its stamp");
    assert.equal(pageState(ctx).photo.path, renamed, "the Photo screen's card too");
    assert.equal(el(ctx, "edit-time").value, "09:30:00");
  });

  test("a file changed on the PC since it was read is not written over: the sheet says so, reads it again, keeps the text", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    ctx.server.first(SAVE, () => {
      ctx.server.routes.shift();
      Object.assign(ctx.held, { title: "Set on the PC", mtime: 1718000300 });
      return changedOnDisk("The file changed on disk.")();
    });
    type(ctx, "edit-title", "Wren's Last Lap");
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);

    assert.match(said(ctx), /changed on the PC since it was read/);
    assert.equal(saveCalls(ctx.server).length, 1, "nothing sent again by itself");
    assert.equal(calls(ctx, "/api/library/photo").length, 2, "the photo read again");
    assert.equal(el(ctx, "edit-title").value, "Wren's Last Lap", "the text typed kept");
    assert.ok(sheetOpen(ctx));

    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    const again = saveCalls(ctx.server)[1].body;
    assert.deepEqual([again.stamp, again.base.title], [{ mtime: 1718000300, size: 204800 }, "Set on the PC"],
      "the Save the owner sends again names the file as read again");
    assert.equal(ctx.held.title, "Wren's Last Lap");
  });

  test("a chip taken off, Close, and Edit again within its Undo: the chip stays off, and the next Save lands", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    click(ctx.window, chipX(ctx, COAST));
    click(ctx.window, el(ctx, "edit-close"));
    await settle(ctx);
    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    assert.deepEqual(chips(ctx, "edit-keywords"), [], "its removal still waits: the chip is not shown back");

    await settle(ctx, UNDO + 80);
    assert.deepEqual(ctx.held.tags, [WREN]);
    assert.deepEqual(chips(ctx, "edit-keywords"), [], "the sheet shows what the file holds");
    type(ctx, "edit-title", "Wren's Last Lap");
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    assert.equal(said(ctx), "Saved.", "the phone's own removal does not make its Save look changed on the PC");
    assert.equal(ctx.held.title, "Wren's Last Lap");
  });

  test("a Save refused as changed on the PC while a removal waits: the photo read again keeps the chip off, the next Save lands", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    click(ctx.window, chipX(ctx, COAST));
    Object.assign(ctx.held, { title: "Set on the PC", mtime: 1718000300 });
    type(ctx, "edit-title", "Wren's Last Lap");
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    assert.match(said(ctx), /changed on the PC since it was read/);
    assert.deepEqual(chips(ctx, "edit-keywords"), [], "read again, the chip taken off is still off");

    await settle(ctx, UNDO + 80);
    assert.deepEqual(ctx.held.tags, [WREN]);
    assert.deepEqual(chips(ctx, "edit-keywords"), []);
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    assert.equal(said(ctx), "Saved.");
    assert.equal(ctx.held.title, "Wren's Last Lap");
  });

  test("a dropped connection or a server that cannot answer: a sentence, the sheet open, the text kept", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    type(ctx, "edit-title", "Wren's Last Lap");
    ctx.server.first(SAVE, () => {
      ctx.server.routes.shift();
      return Promise.reject(new ctx.window.TypeError("Failed to fetch"));
    });
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    assert.equal(said(ctx), "Not saved: no connection. Try again.");
    assert.equal(saveCalls(ctx.server).length, 1, "a write is not sent again by itself");

    ctx.server.first(SAVE, () => {
      ctx.server.routes.shift();
      return { success: false, error: "Could not reach the library's folder just now. Try again." };
    }, { status: 503 });
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    assert.equal(said(ctx), "Not saved: Could not reach the library's folder just now. Try again.");
    assert.ok(sheetOpen(ctx));
    assert.equal(el(ctx, "edit-title").value, "Wren's Last Lap");
    assert.equal(ctx.held.title, "Finish line");
  });

  test("a Save turned away while the server is updated is waited out and lands once", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    ctx.server.first(SAVE, () => {
      ctx.server.routes.shift();
      return { success: false, error: "Updating" };
    }, { status: 503, headers: { "X-TagPup-Updating": "1", "Retry-After": "0" } });
    type(ctx, "edit-title", "Wren's Last Lap");
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx, 60);
    assert.equal(said(ctx), "Saved.");
    assert.equal(saveCalls(ctx.server).length, 2, "sent again once the update was through");
    assert.equal(ctx.held.title, "Wren's Last Lap");
  });

  test("a clip's Date Taken is offered and saved", async (t) => {
    const clip = "D:\\Photos\\Wren's Meet\\Wren's Meet 1.mp4";
    const ctx = await phone(t, { path: clip, media: "video", writes: CLIP_WRITES });
    await openSheet(ctx);
    assert.equal(el(ctx, "edit-when").classList.contains("hidden"), false);
    type(ctx, "edit-date", "2024-06-03");
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    assert.equal(saveCalls(ctx.server)[0].body.date_taken, "2024-06-03T10:00:00");
    assert.equal(saveCalls(ctx.server)[0].body.path, clip);
  });

  test("an add that fails says why and puts the text typed back", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    ctx.server.first("/api/photos/write", { error: "The library is busy: try again." }, { status: 500 });
    await pick(ctx, "cove", COVE);
    assert.equal(said(ctx), "Not added: The library is busy: try again.");
    assert.equal(el(ctx, "edit-add").value, "cove");
    assert.deepEqual(chips(ctx, "edit-keywords"), [leaf(ctx, COAST)]);
  });

  test("Close with a title typed asks first; Close with nothing typed does not", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    const asked = [];
    let answer = false;
    ctx.window.confirm = (text) => { asked.push(text); return answer; };
    type(ctx, "edit-title", "Wren's Last Lap");
    click(ctx.window, el(ctx, "edit-close"));
    assert.equal(asked.length, 1);
    assert.ok(sheetOpen(ctx), "kept open when the owner says no");
    answer = true;
    click(ctx.window, el(ctx, "edit-close"));
    await settle(ctx);
    assert.equal(sheetOpen(ctx), false);

    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    assert.equal(el(ctx, "edit-title").value, "Finish line", "a draft let go on Close is not shown again");
    click(ctx.window, el(ctx, "edit-close"));
    await settle(ctx);
    assert.equal(asked.length, 2, "nothing typed: no question");
    assert.equal(sheetOpen(ctx), false);
  });

  test("Close tapped twice, then Close and Edit at once: one Back each, the sheet as its step says, Back from the photo the grid", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    click(ctx.window, el(ctx, "edit-close"));
    click(ctx.window, el(ctx, "edit-close"));          // a second tap before the first one's Back lands
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["photo"], "one step back: the photo, not the grid");
    assert.equal(sheetOpen(ctx), false);

    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    click(ctx.window, el(ctx, "edit-close"));
    click(ctx.window, el(ctx, "photo-edit"));          // before Close's Back lands: no second sheet step
    await settle(ctx);
    assert.equal(sheetOpen(ctx), Boolean(ctx.window.history.state.sheet), "the sheet is as its step says");
    if (sheetOpen(ctx)) {
      ctx.window.history.back();
      await settle(ctx);
    }
    ctx.window.history.back();
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"], "one Back from the photo leaves it: no stale sheet step under it");
  });

  test("the page back from the back-forward cache: the sheet open as its step left it; Back shuts it, the next leaves the photo", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    type(ctx, "edit-title", "Wren's Last Lap");
    for (const name of ["pagehide", "pageshow"]) {    // another address opened in the tab, then the phone's Back
      const event = new ctx.window.Event(name);
      Object.defineProperty(event, "persisted", { value: true });
      ctx.window.dispatchEvent(event);
    }
    await settle(ctx);
    assert.ok(sheetOpen(ctx), "the sheet open, as the step shown says");
    assert.equal(el(ctx, "edit-title").value, "Wren's Last Lap", "the text typed still there");
    ctx.window.history.back();
    await settle(ctx);
    assert.equal(sheetOpen(ctx), false);
    assert.deepEqual(shown(ctx), ["photo"], "the first Back shuts the sheet");
    ctx.window.history.back();
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
  });

  test("a tap on the picture above the open sheet opens no zoom: Back shuts the sheet, the next leaves the photo", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    click(ctx.window, el(ctx, "photo-picture"));
    await settle(ctx);
    assert.ok(el(ctx, "image-zoom").classList.contains("hidden"), "no zoom over the sheet, for Back to leave behind");
    ctx.window.history.back();
    await settle(ctx);
    assert.equal(sheetOpen(ctx), false);
    assert.deepEqual(shown(ctx), ["photo"]);
    assert.ok(el(ctx, "image-zoom").classList.contains("hidden"));
    click(ctx.window, el(ctx, "photo-picture"));
    await settle(ctx);
    assert.equal(el(ctx, "image-zoom").classList.contains("hidden"), false, "the sheet shut, a tap zooms");
    ctx.window.history.back();
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
  });

  test("Back with the sheet open closes the sheet, not the photo, the text typed kept; the next Back leaves the photo", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    click(ctx.window, el(ctx, "edit-close"));          // Close lets its step go: no Back is spent on a sheet shut
    await settle(ctx);
    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    const asked = [];
    ctx.window.confirm = (text) => { asked.push(text); return false; };
    type(ctx, "edit-title", "Wren's Last Lap");

    ctx.window.history.back();
    await settle(ctx);
    assert.equal(sheetOpen(ctx), false);
    assert.deepEqual(shown(ctx), ["photo"], "the photo stays");
    assert.equal(asked.length, 0, "nothing asked: the text typed is kept");
    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    assert.equal(el(ctx, "edit-title").value, "Wren's Last Lap");

    click(ctx.window, el(ctx, "bar-back"));           // the bar's Back as the phone's
    await settle(ctx);
    assert.equal(sheetOpen(ctx), false);
    assert.deepEqual(shown(ctx), ["photo"]);
    ctx.window.history.back();
    await settle(ctx);
    assert.deepEqual(shown(ctx), ["grid"]);
    assert.equal(saveCalls(ctx.server).length, 0);
  });

  test("a draft kept survives the sheet opened and shut again while the photo is still being read (#3168)", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    type(ctx, "edit-title", "Wren's Last Lap");
    ctx.window.history.back();                         // the sheet shut, the draft kept
    await settle(ctx);

    const reading = holdNext(ctx, "/api/library/photo");
    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    assert.ok(sheetOpen(ctx));
    ctx.window.history.back();                         // shut while still reading: nothing typed, nothing read
    await settle(ctx);
    assert.equal(sheetOpen(ctx), false);

    click(ctx.window, el(ctx, "photo-edit"));
    await settle(ctx);
    assert.equal(el(ctx, "edit-title").value, "Wren's Last Lap", "the draft is still there");
    assert.equal(typeof reading.release, "function");
  });

  for (const [how, leave] of [
    ["Back out of the photo (the sheet, then the photo)", async (ctx) => {
      ctx.window.history.back();
      await settle(ctx);
      ctx.window.history.back();
      await settle(ctx);
    }],
    ["the Libraries button", async (ctx) => {
      click(ctx.window, el(ctx, "bar-library"));
      await settle(ctx);
      assert.deepEqual(shown(ctx), ["library"]);
      ctx.window.history.back();
      await settle(ctx);
      assert.ok(sheetOpen(ctx), "Back to the photo's step opens its sheet again");
      assert.equal(el(ctx, "edit-title").value, "Wren's Last Lap");
      for (let step = 0; step < 2; step += 1) {
        ctx.window.history.back();
        await settle(ctx);
      }
    }],
  ]) {
    test(`a title and date typed are kept through ${how}: the photo's Edit shows them again`, async (t) => {
      const ctx = await phone(t);
      await openSheet(ctx);
      type(ctx, "edit-title", "Wren's Last Lap");
      type(ctx, "edit-date", "2024-06-02");
      await leave(ctx);
      assert.deepEqual(shown(ctx), ["grid"]);
      const goes = new ctx.window.Event("beforeunload", { cancelable: true });
      ctx.window.dispatchEvent(goes);
      assert.ok(goes.defaultPrevented, "the page going asks first while a draft is kept");

      await openPhotoEdit(ctx);
      assert.equal(el(ctx, "edit-title").value, "Wren's Last Lap");
      assert.equal(el(ctx, "edit-date").value, "2024-06-02");
      assert.match(said(ctx), /not saved yet/i);
      assert.equal(saveCalls(ctx.server).length, 0, "nothing was saved for the owner");
      click(ctx.window, el(ctx, "edit-save"));
      await settle(ctx);
      assert.equal(ctx.held.title, "Wren's Last Lap");
      ctx.window.history.back();                       // the sheet shut, the photo still shown
      await settle(ctx);
      click(ctx.window, el(ctx, "photo-edit"));
      await settle(ctx);
      assert.equal(said(ctx), "", "a draft saved is not offered again");
    });
  }

  for (const [how, wait] of [["behind its Undo", 0], ["in its turn behind the Save", UNDO + 80]]) {
    test(`the page going with a removal ${how} and a Save still out: the Save goes first, the removal after it`, async (t) => {
      const ctx = await phone(t);
      await openSheet(ctx);
      const save = holdNext(ctx, SAVE);
      type(ctx, "edit-title", "Wren's Last Lap");
      click(ctx.window, el(ctx, "edit-save"));
      click(ctx.window, chipX(ctx, COAST));
      await settle(ctx, wait);
      ctx.window.dispatchEvent(new ctx.window.Event("pagehide"));
      await settle(ctx);
      assert.equal(tagWrites(ctx).length, 0, "not beside the Save, which it would make look changed on the PC");
      assert.ok(ctx.sent.find((s) => SAVE(s.url, s.body)).keepalive, "the Save outlives the page");

      const renamed = "D:\\Photos\\Wren's Meet\\Wren's Last Lap.jpg";
      ctx.held.path = renamed;                         // the Save's title renamed the file, as the server does
      save.release(saved({ new_path: renamed, mtime: 1718000009, size: 204800,
        base: { tags: [WREN, COAST], title: "Wren's Last Lap" } }));
      await settle(ctx);
      assert.deepEqual(tagWrites(ctx), [{ [renamed]: { id: 1, remove: [COAST] } }], "after the Save, by the file's new name");
      assert.ok(ctx.sent.find(isTagWrite).keepalive);
    });
  }

  test("a chip taken off a photo renamed on the PC since it was read: refused, read again by its id, sent once more by its new name", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    const renamed = "D:\\Photos\\Wren's Meet\\Wren at the Finish.jpg";
    ctx.held.path = renamed;                           // renamed on the PC: the sheet's record still names the old file
    const reads = calls(ctx, "/api/library/photo?id=1").length;
    click(ctx.window, chipX(ctx, COAST));
    await settle(ctx, UNDO + 80);
    assert.deepEqual(tagWrites(ctx), [{ [PATH]: { id: 1, remove: [COAST] } }, { [renamed]: { id: 1, remove: [COAST] } }],
      "refused by the old name, sent once more by the new");
    assert.equal(calls(ctx, "/api/library/photo?id=1").length, reads + 1, "read again by its id, once");
    assert.deepEqual(ctx.held.tags, [WREN]);
    assert.deepEqual(chips(ctx, "edit-keywords"), [], "the chip stays off");
    assert.equal(toasts(ctx).length, 0, "nothing failed");

    type(ctx, "edit-title", "Wren at the Finish");
    click(ctx.window, el(ctx, "edit-save"));
    await settle(ctx);
    assert.equal(lastSave(ctx.server).path, renamed, "the next Save names the file where it is");
    assert.equal(ctx.held.title, "Wren at the Finish");
  });

  test("a chip taken off a photo since deleted from the library: refused, read again, and said; the chip comes back", async (t) => {
    const ctx = await phone(t);
    await openSheet(ctx);
    ctx.held.path = "D:\\Photos\\Wren's Meet\\gone.jpg";
    ctx.server.first("/api/library/photo?id=1", { success: false, error: "No photo 1 in this library." }, { status: 404 });
    click(ctx.window, chipX(ctx, COAST));
    await settle(ctx, UNDO + 80);
    assert.equal(tagWrites(ctx).length, 1, "not sent again");
    assert.deepEqual(chips(ctx, "edit-keywords"), [leaf(ctx, COAST)], "the chip is back");
    assert.match(toasts(ctx).map((line) => line.textContent).join(" "), /Not taken off Coast: this photo is no longer in the library/);
  });
});

function leaf(ctx, tag) {
  return vocabulary(ctx).leafOf(tag);
}
