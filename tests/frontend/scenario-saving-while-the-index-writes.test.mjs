/**
 * V32: somebody adds a folder and tags its photos at once, while the index's bake writes its own fields into each new
 * photo's file (docs/plans/working-on-a-new-folder.md, cut B; DECISIONS 2026-11-26). Every save made from the list read
 * before the bake was refused as changed on disk, and the owner was told to save again. Now a refused save is read again
 * (by its id, found by its path for a folder's photo) and, when the tags and caption it writes over are as the page read
 * them, sent once more naming the file as it is now, the status line saying so. Someone else's tags changed meanwhile, or
 * the second send refused too, is today's refusal: the sentence, nothing merged, no third send. A photo the page no
 * longer lists is not sent again; a caption that renames the file follows the rename; two refusals in a row each go once
 * more; a library view's photo, read by its own id, goes the same way.
 *
 * The server is a fake of the files as they are now (`disk`, each with its row's id), which a save that wrote changes, as
 * the real one does: the scan gives a folder's photos without an id, as photos.scan_folder does.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports, SAVE, saveCalls, saved,
  changedOnDisk,
} from "./harness.mjs";

afterEach(() => closeAllApps());

/** Let the page's replies land until `done()` holds (a fake server answers at once), or fail saying what was waited for. */
async function until(ctx, done, what) {
  for (let turn = 0; turn < 40; turn += 1) {
    if (done()) return;
    await flush(ctx.window, 4);
  }
  assert.fail(`gave up waiting for ${typeof what === "function" ? what() : what}`);
}

const FOLDER = "D:\\Library\\2021 Coast";
const A = photoRecord({ filename: "a.jpg", path: `${FOLDER}\\a.jpg`, mtime: 1700000000.5, size: 2048, tags: ["Trips/Coast"] });
const B = photoRecord({ filename: "b.jpg", path: `${FOLDER}\\b.jpg`, mtime: 1700000100.5, size: 4096, tags: ["Activity/Sailing"] });
const IDS = { [A.path]: 31, [B.path]: 32 };

/** The page on FOLDER, its server's files in `ctx.disk` (path -> record with its id); `ctx.replies` answers the next saves. */
async function page(t, { view = false } = {}) {
  const ctx = { replies: [], held: null, alerts: [] };
  const disk = new Map([A, B].map((record) => [record.path, { ...record, id: IDS[record.path] }]));
  const byId = (id) => [...disk.values()].find((file) => file.id === id);
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/scan", () => [...disk.values()].map(({ id, ...record }) => ({ ...record })))
    .on("/api/library/find", (url) => {
      const file = disk.get(decodeURIComponent(url.match(/path=([^&]+)/)[1]));
      return { id: file ? file.id : null };
    })
    .on("/api/library/photo", (url) => {
      const read = () => ({ photo: { ...byId(Number(url.match(/id=(\d+)/)[1])) } });
      if (!ctx.holdReads) return read();
      return new Promise((resolve) => { ctx.held = () => resolve(read()); });
    })
    .on(SAVE, (url, body) => {
      const reply = (ctx.replies.length ? ctx.replies.shift() : saved())(url, body);
      if (reply.success) {
        // The file holds what the save wrote, at the stamp the reply gives.
        Object.entries(reply.written).forEach(([path, own]) => {
          const file = disk.get(path);
          disk.delete(path);
          disk.set(own.new_path, { ...file, path: own.new_path, filename: own.new_path.split("\\").pop(), tags: own.tags,
            title: own.base.title, mtime: own.mtime, size: own.size });
        });
      }
      return reply;
    });
  if (view) {
    server
      .on("/api/library/ids", { total: 1, ids: [IDS[A.path]], complete: true })
      .on("/api/library/cards", { cards: [{ id: IDS[A.path], name: "a.jpg", path: A.path, taken: null,
        thumb: `/api/photo-thumb?id=${IDS[A.path]}&v=${A.mtime}` }] });
  }
  const loaded = await loadApp("tagpup", { t, url: `http://localhost:8090/photo_index/${view ? "?view=all" : ""}`, server });
  Object.assign(ctx, loaded, { server, disk });
  ctx.window.alert = (message) => { ctx.alerts.push(message); };
  ctx.window.console.error = () => {};
  ctx.state = pageExports(ctx.window, "web/tagpup/state.js").state;
  ctx.status = () => ctx.document.getElementById("status-text").textContent;
  ctx.saves = () => saveCalls(server).map((call) => call.body);
  ctx.scans = () => server.urls().filter((url) => url.includes("/api/folder/scan")).length;
  ctx.finds = () => server.urls().filter((url) => url.includes("/api/library/find")).length;
  ctx.record = (path) => ctx.state.folderPhotos.find((photo) => photo.path === path);
  // The index's bake writes its own fields into the file: its stamp moves, its tags and caption stay.
  ctx.bake = (path) => {
    const file = disk.get(path);
    disk.set(path, { ...file, mtime: file.mtime + 300, size: file.size + 512 });
  };
  ctx.addTag = (tag) => {
    ctx.document.getElementById("input-add-tag").value = tag;
    click(ctx.window, ctx.document.getElementById("btn-add-tag"));
  };
  if (view) {
    await until(ctx, () => ctx.document.querySelector(".btn-thumbnail-detail"), "the view's card");
    ctx.document.querySelector(".btn-thumbnail-detail").click();
    await until(ctx, () => ctx.state.activePhotoPath === A.path, "the view's photo open");
  } else {
    await openFolder(ctx, FOLDER);
    ctx.document.querySelector(`#photo-list li[data-path="${A.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 6);
  }
  return ctx;
}

describe("V32: a save refused because the index wrote to the file", () => {
  test("the bake wrote its own fields only: the save is read again and sent once more, and the status line says so", async (t) => {
    const ctx = await page(t);
    const scans = ctx.scans();
    ctx.bake(A.path);
    ctx.replies.push(changedOnDisk());
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => /^Saved/.test(ctx.status()), () => `the save (${ctx.status()})`);
    const [first, second] = ctx.saves();
    assert.equal(ctx.saves().length, 2);
    assert.deepEqual(first.stamp, { mtime: A.mtime, size: A.size }, "the first named the file as the list read it");
    const now = { mtime: A.mtime + 300, size: A.size + 512 };
    assert.deepEqual(second.stamp, now, "the second names the file as it is now");
    assert.deepEqual(second.base, { tags: ["Trips/Coast"], title: "" });
    assert.deepEqual(second.tags, first.tags, "the same save");
    assert.match(ctx.status(), /^Saved a\.jpg after reading its file again \(indexing, or another program, had written to it\)$/);
    assert.deepEqual(ctx.alerts, []);
    assert.equal(ctx.scans(), scans, "the folder was not read again: the save's reply is the record's");
    const record = ctx.record(A.path);
    assert.deepEqual(Array.from(record.tags), ["Trips/Coast", "Trips/Lakes"]);
    assert.equal(record.mtime, ctx.disk.get(A.path).mtime, "the record names the file as the second send left it");
    assert.match(ctx.document.getElementById("detail-tags").textContent, /Trips\/Lakes/);
  });

  test("someone changed the tags meanwhile: nothing is sent again, today's sentence, the file's tags shown", async (t) => {
    const ctx = await page(t);
    const file = ctx.disk.get(A.path);
    ctx.disk.set(A.path, { ...file, tags: ["Trips/Coast", "Activity/Sailing"], mtime: file.mtime + 300 });
    ctx.replies.push(changedOnDisk());
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => /changed on disk since you opened it/.test(ctx.status()), () => `the sentence (${ctx.status()})`);
    assert.equal(ctx.saves().length, 1, "sent again over someone else's tags");
    assert.ok(ctx.alerts.some((message) => /changed on disk/.test(message)), ctx.alerts.join("|"));
    assert.deepEqual(ctx.record(A.path).tags, ["Trips/Coast", "Activity/Sailing"], "nothing merged: the record is the file's");
  });

  test("the second send is refused too: no third, today's sentence", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.replies.push(changedOnDisk(), changedOnDisk());
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => /changed on disk since you opened it/.test(ctx.status()), () => `the sentence (${ctx.status()})`);
    await flush(ctx.window, 12);
    assert.equal(ctx.saves().length, 2);
    assert.ok(ctx.alerts.some((message) => /changed on disk/.test(message)));
  });

  test("the page no longer lists the photo when the read lands: nothing is sent again", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.replies.push(changedOnDisk());
    ctx.holdReads = true;
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.held, "the read again");
    // Another folder is put on screen meanwhile (the move let go by Escape, V27): its scan lands.
    const other = photoRecord({ filename: "c.jpg", path: "D:\\Library\\2022 Hills\\c.jpg" });
    pageExports(ctx.window, "web/tagpup/folder.js").showScannedFolder("D:\\Library\\2022 Hills", [other]);
    ctx.holdReads = false;
    ctx.held();
    await until(ctx, () => ctx.alerts.length, "the refusal said");
    await flush(ctx.window, 12);
    assert.equal(ctx.saves().length, 1, "a photo the page no longer lists was sent again");
  });

  test("a caption that renames the file: the second send's rename moves the record, the open photo and the selection", async (t) => {
    const renamed = `${FOLDER}\\2021-05-01 Harbour run.jpg`;
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.replies.push(changedOnDisk(), saved({ new_path: renamed }));
    ctx.document.getElementById("input-photo-title").value = "Harbour run";
    click(ctx.window, ctx.document.getElementById("btn-save-title"));
    await until(ctx, () => /^Saved/.test(ctx.status()), () => `the save (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.equal(ctx.saves()[1].title, "Harbour run");
    assert.match(ctx.status(), /^Saved 2021-05-01 Harbour run\.jpg after reading its file again/);
    assert.equal(ctx.state.activePhotoPath, renamed);
    assert.deepEqual(Array.from(ctx.state.folderPhotos, (photo) => photo.path), [renamed, B.path], "one record, at its new name");
    assert.equal(ctx.record(renamed).title, "Harbour run");
  });

  test("two refused saves of one photo in a row: each is read again and sent once more", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.replies.push(changedOnDisk());
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.saves().length === 2 && /^Saved/.test(ctx.status()), "the first save");
    ctx.bake(A.path);   // the bake's next step writes the file again
    ctx.replies.push(changedOnDisk());
    ctx.addTag("Trips/Harbour");
    await until(ctx, () => ctx.saves().length === 4 && /^Saved/.test(ctx.status()), () => `the second save (${ctx.status()})`);
    const saves = ctx.saves();
    assert.deepEqual(saves[2].base.tags, ["Trips/Coast", "Trips/Lakes"], "the second named what the first wrote");
    assert.deepEqual(saves[3].stamp, { mtime: ctx.disk.get(A.path).mtime - 1, size: ctx.disk.get(A.path).size },
      "and was sent again naming the file as the bake left it");
    assert.deepEqual(saves[3].tags, ["Trips/Coast", "Trips/Lakes", "Trips/Harbour"]);
    assert.deepEqual(ctx.alerts, []);
  });

  test("a library view's photo is read again by its own id and sent once more", async (t) => {
    const ctx = await page(t, { view: true });
    ctx.bake(A.path);
    ctx.replies.push(changedOnDisk());
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => /^Saved/.test(ctx.status()), () => `the save (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.equal(ctx.finds(), 0, "a view's photo has its id: nothing to find");
    assert.deepEqual(ctx.saves()[1].stamp, { mtime: A.mtime + 300, size: A.size + 512 });
    assert.match(ctx.status(), /after reading its file again/);
  });
});
