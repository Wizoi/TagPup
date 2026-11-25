/**
 * Manage folders, the dialog both pages open from the gear (web/common/folder-manager.js): add folders -- browse to a
 * parent, tick its subfolders, queue them -- or remove a folder the library holds.
 *
 * The replies are the server's own (tagpup.web.folder_routes), captured from a sandbox library holding four photos in
 * three folders, one of them gone from disk; the sandbox's folder is written D:\Pictures, and the folders above it,
 * which its temporary folder added to the list, are left out.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { advance, loadApp, FakeServer, closeAllApps, click, flush, pageExports, testClock } from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const PICTURES = "D:\\Pictures";
const OLD_MEET = `${PICTURES}\\Old Meet`;
const SEASON = `${PICTURES}\\Season 2024`;
const MEET = `${SEASON}\\Meet 1`;
const NEW_MEET = `${PICTURES}\\New Meet`;

const IDLE = { active: [], queued: [], busy: false, remaining: 0 };
const REPLIES = {
  indexed: { folders: [
    { path: PICTURES, photos: 4, own_photos: 0 },
    { path: OLD_MEET, photos: 1, own_photos: 1 },
    { path: SEASON, photos: 3, own_photos: 1 },
    { path: MEET, photos: 2, own_photos: 2 },
  ] },
  subfolders: {
    parent: PICTURES,
    folders: [
      { path: NEW_MEET, name: "New Meet", images: 1, has_subfolders: false, indexed: 0 },
      { path: SEASON, name: "Season 2024", images: 3, has_subfolders: true, indexed: 3 },
    ],
    own_images: 0, own_indexed: 0, has_subfolders: true,
  },
  indexStart: { success: true, status: "running", unignored: [], queued: [NEW_MEET, SEASON], already_queued: [],
    invalid: [], pending: 2, added: 2 },
  indexStartAgain: { success: true, status: "running", unignored: [], queued: [], already_queued: [SEASON],
    invalid: [], pending: 2, added: 0 },
  removeQueued: { success: false, error: `Not removed: ${SEASON} waits to be indexed in this library. Cancel it, or `
    + "remove the folder when its index has finished." },
  remove: { photos_removed: 1, faces_removed: 0, manual_lost: 0, excluded_lost: 0, ignored: false, success: true },
  removeChildOfAdded: { photos_removed: 2, faces_removed: 0, manual_lost: 0, excluded_lost: 0, ignored: true,
    success: true },
};

/** The paths an /api/folder/on-disk request asks about, in order. */
const askedOf = (url) => new URL(url, "http://localhost").searchParams.getAll("path");

/** /api/folder/on-disk as the server answers it: each path asked, in order, "there" unless `states` says else. */
const onDisk = (states = {}) => (url) => ({ folders: askedOf(url).map((path) => ({ path, state: states[path] || "there" })) });

/** A reply held back until the test answers it. */
function later() {
  let answer;
  const reply = new Promise((resolve) => { answer = resolve; });
  return { reply, answer };
}

function serverFor() {
  return new FakeServer()
    .on("/api/folder/index-active", IDLE)
    .on("/api/folder/indexed", REPLIES.indexed)
    .on("/api/folder/on-disk", onDisk({ [OLD_MEET]: "gone" }))
    .on("/api/browse-folder", { path: PICTURES })
    .on("/api/folder/subfolders", REPLIES.subfolders)
    .on("/api/folder/index-start", REPLIES.indexStart)
    .on("/api/folder/remove", REPLIES.remove);
}

/** Longer than the dialog waits after the filter stops changing before it asks what is on disk. */
const LOOK = 300;

function openFromGear(ctx) {
  click(ctx.window, ctx.document.getElementById("btn-gear"));
  click(ctx.window, ctx.document.querySelector('#gear-menu [data-action="manage-folders"]'));
  return flush(ctx.window, 4);
}

async function openManager(t, { app = "tagtuner", server = serverFor(), url } = {}) {
  const ctx = await loadApp(app, { t, server, url });
  ctx.window.confirm = () => true;
  await openFromGear(ctx);
  return { ...ctx, server };
}

const modal = (document) => document.getElementById("folder-manager-modal");
const part = (document, n) => modal(document).querySelectorAll(".folder-manager-part")[n];
const addRows = (document) => [...part(document, 0).querySelectorAll(".folder-manager-row")];
const removeRows = (document) => [...part(document, 1).querySelectorAll(".folder-manager-row")];
const rowOf = (rows, words) => rows.find((r) => r.querySelector(".folder-manager-name").textContent.startsWith(words));
const button = (document, words) => [...modal(document).querySelectorAll("button")].find((b) => b.textContent === words);
const said = (document) => modal(document).querySelector(".folder-manager-status").textContent;
const posted = (server, route) => server.calls.filter((c) => c.method === "POST" && c.url.includes(route));
const looks = (server) => server.urls().filter((u) => u.includes("/api/folder/on-disk"));

function closeDialog(ctx) {
  click(ctx.window, button(ctx.document, "Close"));
}

async function browse(ctx) {
  click(ctx.window, button(ctx.document, "Browse..."));
  await flush(ctx.window, 4);
}

async function choose(ctx, path) {
  click(ctx.window, rowOf(removeRows(ctx.document), path).querySelector("input"));
  await flush(ctx.window, 2);
}

afterEach(() => closeAllApps());

describe("the library's folders, to remove one", () => {
  test("each says its photos; a folder above them says they are in folders under it", async (t) => {
    const { document } = await openManager(t);
    const rows = removeRows(document);
    assert.deepEqual(rows.map((r) => r.querySelector(".folder-manager-name").textContent),
      [PICTURES, OLD_MEET, SEASON, MEET]);
    assert.match(rowOf(rows, PICTURES).textContent, /4 photo\(s\) in folders under it/);
    assert.match(rowOf(rows, SEASON).textContent, /3 photo\(s\)$/);
  });

  test("whether each is on disk is asked of the rows shown, a moment after: gone, there, a share not answering", async (t) => {
    const server = serverFor().first("/api/folder/on-disk", onDisk({ [OLD_MEET]: "gone", [SEASON]: "unanswered" }));
    const { window, document } = await openManager(t, { server });
    assert.deepEqual(looks(server), [], "asked before the dialog had settled");
    await advance(window, LOOK);
    assert.equal(looks(server).length, 1);
    assert.deepEqual(askedOf(looks(server)[0]), [PICTURES, OLD_MEET, SEASON, MEET]);

    const rows = removeRows(document);
    assert.match(rowOf(rows, OLD_MEET).textContent, /— not on disk/);
    assert.ok(rowOf(rows, OLD_MEET).classList.contains("not-on-disk"));
    // A share that did not answer in time is not a folder gone: no removal is suggested by it.
    assert.match(rowOf(rows, SEASON).textContent, /its share is not answering/);
    assert.doesNotMatch(rowOf(rows, SEASON).textContent, /not on disk/);
    assert.ok(!rowOf(rows, SEASON).classList.contains("not-on-disk"));
    assert.doesNotMatch(rowOf(rows, MEET).textContent, /disk|answering/);
  });

  test("the filter asks once it stops changing, only of the rows it shows not yet answered, at most 100", async (t) => {
    // A library of 2,660 folders on a share took 22 s to look at whole: the dialog looks only at what it shows.
    const many = Array.from({ length: 150 }, (_, i) => `${PICTURES}\\Meet ${String(i + 1).padStart(3, "0")}`);
    const server = serverFor().first("/api/folder/indexed", { folders: many.map((path) => ({ path, photos: 1, own_photos: 1 })) });
    const { window, document } = await openManager(t, { server });
    assert.equal(removeRows(document).length, 100);
    assert.match(part(document, 1).textContent, /50 more: type in the filter to find one/);
    await advance(window, LOOK);
    assert.deepEqual(askedOf(looks(server)[0]), many.slice(0, 100));

    const filter = part(document, 1).querySelector(".folder-manager-filter");
    for (const typed of ["Meet 1", "Meet 14"]) {
      filter.value = typed;
      filter.dispatchEvent(new window.Event("input", { bubbles: true }));
      await advance(window, 50);
    }
    await advance(window, LOOK);
    assert.equal(looks(server).length, 2, "a look for each keystroke");
    assert.deepEqual(askedOf(looks(server)[1]), many.slice(139, 149));
    assert.equal(removeRows(document).length, 10);

    // Back to a wider filter: the rows answered already are not asked again.
    filter.value = "Meet 1";
    filter.dispatchEvent(new window.Event("input", { bubbles: true }));
    await advance(window, LOOK);
    assert.equal(looks(server).length, 3);
    assert.deepEqual(askedOf(looks(server)[2]), [...many.slice(100, 139), many[149]]);
  });
});

describe("adding folders", () => {
  test("a folder wholly in the library says so and is not ticked; a new one is", async (t) => {
    const ctx = await openManager(t);
    await browse(ctx);
    const rows = addRows(ctx.document);
    const season = rowOf(rows, "Season 2024");
    assert.match(season.textContent, /3 image\(s\) — already in the library/);
    assert.ok(season.classList.contains("already-indexed"));
    assert.equal(season.querySelector("input").checked, false);
    assert.equal(rowOf(rows, "New Meet").querySelector("input").checked, true);
    assert.equal(part(ctx.document, 0).querySelector(".folder-manager-selection").textContent, "1 folder(s), 1 image(s)");
    assert.equal(button(ctx.document, "Add").disabled, false);
  });

  test("the picker's own row: a folder whose own photos are all in the library reads so, unticked", async (t) => {
    const server = serverFor().first("/api/folder/subfolders",
      { parent: MEET, folders: [], own_images: 2, own_indexed: 2, has_subfolders: false });
    const ctx = await openManager(t, { server });
    await browse(ctx);
    const [own] = addRows(ctx.document);
    assert.match(own.textContent, /Meet 1 {2}\(this folder itself\)/);
    assert.match(own.textContent, /2 image\(s\) — already in the library/);
    assert.equal(own.querySelector("input").checked, false);
    assert.equal(button(ctx.document, "Add").disabled, true);
  });

  test("the ticked folders go in one request, and the dialog says what the server queued", async (t) => {
    const ctx = await openManager(t);
    await browse(ctx);
    click(ctx.window, button(ctx.document, "All"));
    click(ctx.window, button(ctx.document, "Add 2 folders"));
    await flush(ctx.window, 4);
    assert.deepEqual(posted(ctx.server, "/api/folder/index-start").map((c) => c.body), [{ folder_paths: [NEW_MEET, SEASON] }]);
    assert.equal(said(ctx.document), "2 folder(s) added and queued to be indexed.");
  });

  test("a folder already queued or being indexed is said so", async (t) => {
    const server = serverFor().first("/api/folder/index-start", REPLIES.indexStartAgain);
    const ctx = await openManager(t, { server });
    await browse(ctx);
    click(ctx.window, button(ctx.document, "Add"));
    await flush(ctx.window, 4);
    assert.equal(said(ctx.document), "0 folder(s) added and queued to be indexed. 1 already queued or being indexed.");
  });

  test("rapid clicks: a second Add while the first is on its way sends nothing", async (t) => {
    const held = later();
    const server = serverFor().first("/api/folder/index-start", () => held.reply);
    const ctx = await openManager(t, { server });
    await browse(ctx);
    const add = button(ctx.document, "Add");
    click(ctx.window, add);
    click(ctx.window, add);
    await flush(ctx.window, 2);
    assert.equal(posted(server, "/api/folder/index-start").length, 1);
    assert.equal(add.disabled, true, "Add is pressable while the first is on its way");
    held.answer(REPLIES.indexStart);
    await flush(ctx.window, 4);
    assert.equal(posted(server, "/api/folder/index-start").length, 1);
    assert.match(said(ctx.document), /added and queued/);
  });
});

describe("removing a folder", () => {
  test("the owner is asked first, with the folder, its photos and what goes; No removes nothing", async (t) => {
    const ctx = await openManager(t);
    const asked = [];
    ctx.window.confirm = (text) => { asked.push(text); return false; };
    await choose(ctx, OLD_MEET);
    click(ctx.window, button(ctx.document, "Remove..."));
    await flush(ctx.window, 4);
    assert.equal(asked.length, 1);
    assert.match(asked[0], /Remove the 1 photo\(s\) under/);
    assert.ok(asked[0].includes(OLD_MEET) && asked[0].includes("The photo files stay where they are."));
    assert.deepEqual(posted(ctx.server, "/api/folder/remove"), []);
  });

  test("rapid clicks: one removal, and the list read again once it is done", async (t) => {
    const held = later();
    const server = serverFor().first("/api/folder/remove", () => held.reply);
    const ctx = await openManager(t, { server });
    await choose(ctx, OLD_MEET);
    const remove = button(ctx.document, "Remove...");
    click(ctx.window, remove);
    click(ctx.window, remove);
    await flush(ctx.window, 2);
    assert.deepEqual(posted(server, "/api/folder/remove").map((c) => c.body), [{ folder_path: OLD_MEET }]);
    assert.equal(remove.disabled, true);
    const reads = server.urls().filter((u) => u.includes("/api/folder/indexed")).length;
    held.answer(REPLIES.remove);
    await flush(ctx.window, 4);
    assert.equal(said(ctx.document), "Removed 1 photo(s) and 0 face(s).");
    assert.equal(server.urls().filter((u) => u.includes("/api/folder/indexed")).length, reads + 1);
    assert.equal(remove.disabled, true, "nothing is chosen after the removal");
  });

  test("a folder waiting to be indexed: the server's 409 sentence is shown as it is", async (t) => {
    const server = serverFor().first("/api/folder/remove", REPLIES.removeQueued, { status: 409 });
    const ctx = await openManager(t, { server });
    await choose(ctx, MEET);
    click(ctx.window, button(ctx.document, "Remove..."));
    await flush(ctx.window, 4);
    assert.equal(said(ctx.document), REPLIES.removeQueued.error);
    assert.equal(button(ctx.document, "Remove...").disabled, false, "the choice is lost: it cannot be tried again");
  });

  test("a folder under one added with its subfolders: the dialog says it is now ignored", async (t) => {
    const server = serverFor().first("/api/folder/remove", REPLIES.removeChildOfAdded);
    const ctx = await openManager(t, { server });
    await choose(ctx, MEET);
    click(ctx.window, button(ctx.document, "Remove..."));
    await flush(ctx.window, 4);
    assert.match(said(ctx.document), /^Removed 2 photo\(s\) and 0 face\(s\)\. A folder above it was added with its subfolders/);
    assert.match(said(ctx.document), /ignored folders/);
  });
});

describe("a reply after the dialog closed or opened again", () => {
  test("the first opening's list of folders is let go", async (t) => {
    const held = later();
    let reads = 0;
    const server = serverFor().first("/api/folder/indexed", () => {
      reads += 1;
      return reads === 1 ? held.reply : { folders: [{ path: OLD_MEET, photos: 1, own_photos: 1 }] };
    });
    const ctx = await openManager(t, { server });
    closeDialog(ctx);
    await openFromGear(ctx);
    held.answer(REPLIES.indexed);
    await flush(ctx.window, 4);
    assert.deepEqual(removeRows(ctx.document).map((r) => r.querySelector(".folder-manager-name").textContent), [OLD_MEET]);
  });

  test("a folder browsed before a close is not shown after the reopen", async (t) => {
    const held = later();
    const server = serverFor().first("/api/folder/subfolders", () => held.reply);
    const ctx = await openManager(t, { server });
    await browse(ctx);
    closeDialog(ctx);
    await openFromGear(ctx);
    held.answer(REPLIES.subfolders);
    await flush(ctx.window, 4);
    assert.deepEqual(addRows(ctx.document), []);
    assert.match(part(ctx.document, 0).textContent, /Browse to the folder that holds the ones to add/);
  });

  test("folders added before a reopen: the new opening says nothing of it, the page follows the queue", async (t) => {
    const held = later();
    const server = serverFor().first("/api/folder/index-start", () => held.reply);
    const ctx = await openManager(t, { server });
    await browse(ctx);
    click(ctx.window, button(ctx.document, "Add"));
    closeDialog(ctx);
    await openFromGear(ctx);
    const progress = ctx.document.getElementById("index-progress-container");
    assert.ok(progress.classList.contains("hidden"), "fixture: nothing is indexing yet");
    held.answer(REPLIES.indexStart);
    await flush(ctx.window, 4);
    assert.equal(said(ctx.document), "");
    assert.ok(!progress.classList.contains("hidden"), "the page was not told the folders were queued");
  });
});

describe("both libraries", () => {
  for (const app of ["tagpup", "tagtuner"]) {
    test(`${app}: every request of the dialog goes to the page's library`, async (t) => {
      const ctx = await openManager(t, { app, url: "http://localhost:8090/kr-track/" });
      await browse(ctx);
      click(ctx.window, button(ctx.document, "Add"));
      await choose(ctx, OLD_MEET);
      click(ctx.window, button(ctx.document, "Remove..."));
      await advance(ctx.window, LOOK);
      const asked = ctx.server.urls().filter((u) => /\/api\/(folder\/(indexed|on-disk|subfolders|index-start|remove)|browse-folder)/.test(u));
      for (const route of ["indexed", "on-disk", "subfolders", "index-start", "remove", "browse-folder"]) {
        assert.ok(asked.some((u) => u.includes(route)), `fixture: ${route} was not asked`);
      }
      assert.deepEqual(asked.filter((u) => !u.startsWith("/kr-track/api/")), []);
    });
  }
});

describe("a folder queued again after it finished", () => {
  test("is followed like any other job", async (t) => {
    // The page remembers the folder it last saw finish, so as not to be fooled by a reply still calling it running;
    // a folder queued again had been taken for the old job: no progress bar, no poll.
    let phase = "first run";
    const server = serverFor()
      .first("/api/folder/index-active", () => (phase === "done" ? IDLE
        : { active: [{ folder: NEW_MEET, name: "New Meet", percent: 10, message: "Indexing" }], queued: [], busy: true,
          remaining: 1 }))
      .first("/api/folder/index-status", () => {
        if (phase === "first run") {
          phase = "done";
          return { status: "completed", percent: 100, message: "done" };
        }
        return { status: "running", percent: 20, message: "Indexing again" };
      })
      .first("/api/folder/index-start", () => {
        phase = "second run";
        return { ...REPLIES.indexStart, queued: [NEW_MEET], pending: 1, added: 1 };
      });
    const ctx = await loadApp("tagtuner", { server, t });
    await advance(ctx.window, 1600);
    assert.equal(phase, "done", "fixture: the first run finished");

    await openFromGear(ctx);
    await browse(ctx);
    click(ctx.window, button(ctx.document, "Add"));
    await advance(ctx.window, 700);
    assert.ok(!ctx.document.getElementById("index-progress-container").classList.contains("hidden"),
      "the second run of the folder has no progress bar");
    assert.equal(ctx.document.getElementById("index-progress-text").textContent, "Indexing again");
  });
});

describe("review of cut 2", () => {
  test("the folder open in TagPup, added from the gear: the page follows its index and scans it when done", async (t) => {
    const server = serverFor().on("/api/folder/membership", { held: false });
    const ctx = await openManager(t, { app: "tagpup", server });
    pageExports(ctx.window, "web/tagpup/state.js").state.scannedFolder = `${NEW_MEET}\\Heat 1`;   // under a folder the dialog queues
    await browse(ctx);
    click(ctx.window, button(ctx.document, "Add"));
    await flush(ctx.window, 4);
    assert.ok(server.urls().some((u) => u.includes("/api/folder/index-status")), "index-status was asked of the open folder");
  });

  test("a look that fails says so in the dialog, not only in the console", async (t) => {
    const server = serverFor().first("/api/folder/on-disk", { error: "share gone" }, { status: 500 });
    const { window, document } = await openManager(t, { server });
    await advance(window, LOOK);
    assert.match(said(document), /Could not look at the folders/);
  });

  test("closed during a removal and reopened: the reopened list does not hold the removed folder", async (t) => {
    const held = later();
    const server = serverFor().first("/api/folder/remove", () => held.reply);
    const ctx = await openManager(t, { server });
    await choose(ctx, OLD_MEET);
    click(ctx.window, button(ctx.document, "Remove..."));
    await flush(ctx.window, 2);
    closeDialog(ctx);
    await openFromGear(ctx);
    assert.ok(rowOf(removeRows(ctx.document), OLD_MEET), "fixture: the reopened list was read before the removal");
    server.first("/api/folder/indexed", { folders: REPLIES.indexed.folders.filter((f) => f.path !== OLD_MEET) });
    held.answer(REPLIES.remove);
    await flush(ctx.window, 6);
    assert.equal(rowOf(removeRows(ctx.document), OLD_MEET), undefined);
  });

  test("keystrokes while a look is on its way do not ask the same folders again", async (t) => {
    const held = later();
    const server = serverFor().first("/api/folder/on-disk", () => held.reply);
    const { window, document } = await openManager(t, { server });
    await advance(window, LOOK);
    const filter = part(document, 1).querySelector(".folder-manager-filter");
    filter.value = "Season";
    filter.dispatchEvent(new window.Event("input", { bubbles: true }));
    await advance(window, LOOK);
    assert.equal(looks(server).length, 1, "the folders still being looked at are not asked again");
    held.answer({ folders: askedOf(looks(server)[0]).map((path) => ({ path, state: "there" })) });
    await flush(window, 4);
  });
});
