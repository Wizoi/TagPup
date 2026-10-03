/**
 * A save names the file it was built from, a refusal reads the photo again, and a bulk write names itself
 * (findings #533, #535, #536; docs/ARCHITECTURE.md, phase 9b-2).
 *
 * The panel sends a photo's WHOLE tag list. If another program changed the file since the page read it, that list
 * would write over what the file now holds, so every save sends the stamp (modified time, size) of the record it
 * was built from and the server refuses (409, changed_on_disk) when the file differs. What the page then does:
 * reads the photo again, says so, and writes nothing -- no silent overwrite, no silent merge.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2020";
const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function folderServer(extra = {}) {
  const records = [
    photoRecord({ filename: "a.jpg", path: `${FOLDER}\\a.jpg`, mtime: 1700000000.5, size: 2048, tags: ["Trips/Coast"] }),
    photoRecord({ filename: "b.jpg", path: `${FOLDER}\\b.jpg`, mtime: 1700000100.5, size: 4096, tags: ["Activity/Sailing"] }),
  ];
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/scan", () => server.scanned.map((r) => ({ ...r })));
  server.scanned = records;
  Object.entries(extra).forEach(([match, body]) => server.on(match, body));
  return server;
}

async function openPhoto(ctx, name) {
  ctx.document.querySelector(`#photo-list li[data-path="${FOLDER.replace(/\\/g, "\\\\")}\\\\${name}"]`).click();
  await flush(ctx.window, 6);
}

async function folderPage(t, server) {
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.alert = (message) => { ctx.alerts.push(message); };
  ctx.window.confirm = (message) => { ctx.confirms.push(message); return ctx.answer; };
  ctx.alerts = [];
  ctx.confirms = [];
  ctx.answer = true;
  await openFolder(ctx, FOLDER);
  return ctx;
}

describe("a save names the stamp of the record it was built from", () => {
  test("adding a tag sends the photo's modified time and size", async (t) => {
    const server = folderServer({ "/api/photo/save-metadata": { success: true, mtime: 1700000500.5, size: 2100 } });
    const ctx = await folderPage(t, server);
    await openPhoto(ctx, "a.jpg");
    ctx.document.getElementById("input-add-tag").value = "Trips/Lakes";
    click(ctx.window, ctx.document.getElementById("btn-add-tag"));
    await flush(ctx.window, 8);
    assert.deepEqual(server.lastBody("/api/photo/save-metadata").stamp, { mtime: 1700000500.5 - 500, size: 2048 });
  });

  test("after a save the record has the file's new stamp, and the next save names that", async (t) => {
    const server = folderServer({ "/api/photo/save-metadata": { success: true, mtime: 1700000500.5, size: 2100 } });
    const ctx = await folderPage(t, server);
    await openPhoto(ctx, "a.jpg");
    ctx.document.getElementById("input-add-tag").value = "Trips/Lakes";
    click(ctx.window, ctx.document.getElementById("btn-add-tag"));
    await flush(ctx.window, 8);
    ctx.document.getElementById("input-add-tag").value = "Trips/Harbour";
    click(ctx.window, ctx.document.getElementById("btn-add-tag"));
    await flush(ctx.window, 8);
    assert.deepEqual(server.lastBody("/api/photo/save-metadata").stamp, { mtime: 1700000500.5, size: 2100 });
  });
});

describe("a refusal because the file changed", () => {
  test("a folder's photo is read again (the folder is scanned afresh), the owner is told, and nothing is merged", async (t) => {
    const server = folderServer({
      "/api/photo/save-metadata": { success: false, error: "This photo changed on disk since you opened it: reload it first.", changed_on_disk: true },
    });
    const ctx = await folderPage(t, server);
    await openPhoto(ctx, "a.jpg");
    const scans = server.urls().filter((u) => u.includes("/api/folder/scan")).length;
    // another program added a keyword: the next scan shows it
    server.scanned = [{ ...server.scanned[0], mtime: 1700009000.5, size: 3000, tags: ["Trips/Coast", "Trips/Lakes"] }, server.scanned[1]];
    ctx.document.getElementById("input-add-tag").value = "Activity/Sailing";
    click(ctx.window, ctx.document.getElementById("btn-add-tag"));
    await flush(ctx.window, 12);
    assert.ok(server.urls().filter((u) => u.includes("/api/folder/scan")).length > scans, "the folder was read again");
    assert.match(ctx.document.getElementById("status-text").textContent, /changed on disk/);
    const { state } = pageExports(ctx.window, "web/tagpup/state.js");
    const record = state.folderPhotos.find((p) => p.path === `${FOLDER}\\a.jpg`);
    assert.deepEqual(record.tags, ["Trips/Coast", "Trips/Lakes"], "the record is the file's now; the typed tag was not written into it");
    assert.equal(record.mtime, 1700009000.5);
    assert.ok(ctx.alerts.some((message) => /changed on disk/.test(message)), "and the owner was told in words");
  });
});

describe("in a view of the library", () => {
  const ID = 4242;
  const record = (extra = {}) => photoRecord({
    id: ID, filename: "v.jpg", path: "D:\\Library\\Photos\\v.jpg", tags: ["Trips/Coast"], mtime: 1700000000.5, size: 2048,
    taken: "2020:01:01 10:00:00", damaged: false, damage: null, ...extra,
  });

  async function view(t, { photo, saved } = {}) {
    const server = folderServer();
    const state = { photo: photo || record() };
    server
      .on("/api/library/ids", { total: 1, ids: [ID], complete: true })
      .on("/api/library/cards", { cards: [{ id: ID, name: "v.jpg", path: state.photo.path, taken: "2020:01:01 10:00:00",
        damaged: Boolean(state.photo.damaged), damage: state.photo.damage, thumb: `/api/photo-thumb?id=${ID}&v=1700000000.5` }] })
      .on("/api/library/photo", () => ({ photo: state.photo }))
      .on("/api/photo/save-metadata", saved || { success: true, mtime: 1700000600.5, size: 2200 });
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/?view=all", server });
    ctx.window.alert = (message) => { ctx.alerts.push(message); };
    ctx.alerts = [];
    ctx.state = state;
    ctx.server = server;
    await wait(ctx.window, 200);
    await flush(ctx.window, 6);
    return ctx;
  }

  test("a refusal reads the photo again from the library and shows what the file holds", async (t) => {
    const ctx = await view(t, { saved: { success: false, error: "This photo changed on disk since you opened it: reload it first.", changed_on_disk: true } });
    ctx.document.querySelector(".btn-thumbnail-detail").click();
    await wait(ctx.window, 80);
    assert.equal(ctx.server.urls().filter((u) => u.includes("/api/library/photo")).length, 1);
    ctx.state.photo = record({ tags: ["Trips/Coast", "Trips/Lakes"], mtime: 1700009000.5, size: 3000 });   // changed under the page
    ctx.document.getElementById("input-add-tag").value = "Activity/Sailing";
    click(ctx.window, ctx.document.getElementById("btn-add-tag"));
    await wait(ctx.window, 120);
    assert.equal(ctx.server.urls().filter((u) => u.includes("/api/library/photo")).length, 2, "read again");
    assert.match(ctx.document.getElementById("status-text").textContent, /changed on disk/);
    assert.match(ctx.document.getElementById("detail-tags").textContent, /Trips\/Lakes/, "the panel shows the file's tags now");
    assert.equal(ctx.server.lastBody("/api/photo/save-metadata").stamp.size, 2048, "the refused save named the stamp it was built from");
  });

  test("a recorded-unreadable photo opened from a view shows why and asks for no picture", async (t) => {
    const ctx = await view(t, { photo: record({ damaged: true, damage: "truncated" }) });
    ctx.document.querySelector(".btn-thumbnail-detail").click();
    await wait(ctx.window, 80);
    const note = ctx.document.getElementById("photo-damaged-note");
    assert.ok(!note.classList.contains("hidden"), "the note is shown");
    assert.match(note.textContent, /can't be read/);
    assert.equal(ctx.document.getElementById("main-image").getAttribute("src") || "", "");
    assert.equal(ctx.server.urls().filter((u) => u.includes("/api/photo-file")).length, 0);
  });
});

describe("a bulk write from a selection names itself", () => {
  async function selectionOf(t, count) {
    const records = Array.from({ length: count }, (_, i) => photoRecord({ filename: `IMG_${i}.jpg`, path: `${FOLDER}\\IMG_${i}.jpg` }));
    const server = folderServer({ "/api/photos/bulk-tags": { success: true, written: {}, stamps: {} } });
    server.scanned = records;
    const ctx = await folderPage(t, server);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    await flush(ctx.window, 4);
    ctx.document.getElementById("bulk-add-tags-input").value = "Trips/Lighthouse";
    return ctx;
  }
  const bulkCalls = (ctx) => ctx.server.calls.filter((c) => c.url.includes("/api/photos/bulk-tags")).length;

  test("a selection of 300 asks 'Add Trips/Lighthouse to 300 photos?' and writes only on yes", async (t) => {
    const ctx = await selectionOf(t, 300);
    ctx.answer = false;
    click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
    await flush(ctx.window, 8);
    assert.ok(ctx.confirms.some((m) => m === "Add Trips/Lighthouse to 300 photos?"), ctx.confirms.join("|"));
    assert.equal(bulkCalls(ctx), 0);
    ctx.answer = true;
    click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
    await flush(ctx.window, 8);
    assert.equal(bulkCalls(ctx), 1);
  });

  test("a selection of 200 or fewer is not asked about", async (t) => {
    const ctx = await selectionOf(t, 200);
    click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
    await flush(ctx.window, 8);
    assert.deepEqual(ctx.confirms.filter((m) => /Lighthouse/.test(m)), []);
    assert.equal(bulkCalls(ctx), 1);
  });

  test("a selection over 5,000 is refused with the sentence and nothing is sent", async (t) => {
    const ctx = await selectionOf(t, 5001);
    click(ctx.window, ctx.document.getElementById("btn-bulk-add-tags"));
    await flush(ctx.window, 8);
    assert.ok(ctx.alerts.some((m) => /Narrow the selection: bulk edits over 5000 photos arrive with the editing stage/.test(m)));
    assert.equal(bulkCalls(ctx), 0);
  });
});

describe("a late reply about the folder after a view opened", () => {
  test("an index-status reply shows no progress over the view", async (t) => {
    const server = folderServer();
    let release;
    server.first("/api/folder/index-status", () => new Promise((resolve) => {
      release = () => resolve({ status: "running", percent: 40, message: "Indexing..." });
    }));
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/?path=D%3A%5CLibrary%5C2020", server });
    server.on("/api/library/ids", { total: 0, ids: [], complete: true });
    pageExports(ctx.window, "web/tagpup/library-view.js").openLibraryView({ kind: "all" });
    await flush(ctx.window, 6);
    release();
    await flush(ctx.window, 6);
    assert.ok(ctx.document.getElementById("index-progress-container").classList.contains("hidden"));
  });
});
