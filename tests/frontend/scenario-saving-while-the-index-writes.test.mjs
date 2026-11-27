/**
 * V33: somebody adds a folder and tags its photos at once, while the index's bake writes its own fields into each new
 * photo's file (docs/plans/working-on-a-new-folder.md, cut B; DECISIONS 2026-11-26). Every save made from the list read
 * before the bake was refused as changed on disk, and the owner was told to save again. Now a refused save is read again
 * (by its id, found by its path for a folder's photo) and sent once more naming the file as it is now and, as its base,
 * what the page read; the server says whether it may be written (the tags and caption as that base), and written, the
 * status line says so. Someone else's tags changed meanwhile is refused the second time too: today's sentence, nothing
 * merged, no third send. A photo the page no longer lists is not sent again; a caption that renames the file follows the
 * rename; two refusals in a row each go once more; a library view's photo, read by its own id, goes the same way. A read
 * again that finds the file gone (`missing`) or that ExifTool could not read (`unreadable`) is not sent again either.
 * Every save of the page's that names a base goes by the one rule (web/common/photo-write.js postOrSendAgain): the
 * panel's, a card's title in the grid, Carry forward, a suggestion taken one or all at once, the Edit Date Taken dialog's
 * (its base names the date the page read, which the server compares with the file's, #3425: another program's date is
 * refused, never written over); the owner opening the next photo while one is read again waits for it. From the refusal
 * until the second send is answered nothing of the save is written: the write line says it waits in this page, and closing
 * the tab asks (the browser's own Leave site?), for every one of those saves; refused the second time it stops waiting.
 * The phone's Save: scenario-editing-a-photo-on-the-phone (V26).
 *
 * The server is a fake of the files as they are now (`disk`, each with its row's id), which a save that wrote changes, and
 * which refuses a save as the real one does (tagging.py save_photo): its stamp not the file's, or its base not the file's
 * tags (as a set) and caption. The scan gives a folder's photos without an id, as photos.scan_folder does. `ctx.replies`
 * answers the next saves instead, for a reply the model does not make (a rename); `ctx.holdReads` holds the reads of a
 * photo again (`ctx.held()` answers it), `ctx.holdSave` the next save (`ctx.heldSave()`).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps, pageExports, SAVE, saveCalls, savedPhoto, saved,
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

/** A date typed in the dialog, as the server writes it ("2021-05-06T07:08" is "2021:05:06 07:08:00"). */
function spelled(date) {
  const text = date.replace("T", " ").replace(/\.000$/, "");
  const whole = text.slice(0, 10).replace(/-/g, ":") + text.slice(10);
  return whole.length === 16 ? `${whole}:00` : whole;
}

/** Would the real server refuse `sent` (savedPhoto) over `file`: gone, or the stamp or the base not the file's? */
function refused(file, sent) {
  if (!file || file.missing) return true;
  const held = new Set(file.tags || []);
  const base = new Set(sent.base.tags);
  return !sent.stamp || sent.stamp.mtime !== file.mtime || sent.stamp.size !== file.size
    || held.size !== base.size || [...base].some((tag) => !held.has(tag)) || (file.title || "") !== sent.base.title
    || (sent.base.date_taken !== undefined && (file.taken || "") !== sent.base.date_taken);
}

/** Does closing the tab now ask (the browser's own Leave site?)? */
function leaving(ctx) {
  const event = new ctx.window.Event("beforeunload", { cancelable: true });
  ctx.window.dispatchEvent(event);
  return event.defaultPrevented;
}

/** The write line's words of what leaving does now (write-queue.js leaveSaid), '' when it says nothing. */
function line(ctx) {
  const said = ctx.document.querySelector("#write-queue .write-queue-leave");
  return said && !said.hidden ? said.textContent : "";
}

/** The save's read again is out: closing the tab asks, the line says it waits in the page; then the read is answered. */
async function asksWhileReadAgain(ctx) {
  await until(ctx, () => ctx.held, "the read again");
  assert.equal(line(ctx), "1 photo waiting in this page: stay until it is sent");
  assert.equal(leaving(ctx), true, "closing the tab while the refused save was read again asked nothing: it was lost");
  ctx.holdReads = false;
  ctx.held();
}

/** The page on FOLDER, its server's files in `ctx.disk` (path -> record with its id); `ctx.replies` answers the next saves. */
async function page(t, { view = false, open = true, taken = null } = {}) {
  const ctx = { replies: [], held: null, alerts: [] };
  const disk = new Map([A, B].map((record) => [record.path, { ...record, id: IDS[record.path], ...(taken && record === A ? { taken } : {}) }]));
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
      if (!ctx.holdSave) return answerSave(url, body);
      ctx.holdSave = false;
      return new Promise((resolve) => { ctx.heldSave = () => resolve(answerSave(url, body)); });
    });
  function answerSave(url, body) {
      const answer = ctx.replies.length ? ctx.replies.shift()
        : refused(disk.get(savedPhoto(body).path), savedPhoto(body)) ? changedOnDisk() : saved();
      const reply = answer(url, body);
      if (reply.success) {
        // The file holds what the save wrote, at the stamp the reply gives.
        Object.entries(reply.written).forEach(([path, own]) => {
          const file = disk.get(path);
          disk.delete(path);
          const dated = savedPhoto(body).date_taken;
          if (dated) own.taken = spelled(dated);   // as the server spells it (fields.date_taken_value)
          disk.set(own.new_path, { ...file, path: own.new_path, filename: own.new_path.split("\\").pop(), tags: own.tags,
            title: own.base.title, mtime: own.mtime, size: own.size, ...(dated ? { taken: own.taken } : {}) });
        });
      }
      return reply;
  }
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
  ctx.open = async (path) => {
    ctx.document.querySelector(`#photo-list li[data-path="${path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 6);
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
    if (open) await ctx.open(A.path);
  }
  return ctx;
}

describe("V33: a save refused because the index wrote to the file", () => {
  test("the bake wrote its own fields only: the save is read again and sent once more, and the status line says so", async (t) => {
    const ctx = await page(t);
    const scans = ctx.scans();
    ctx.bake(A.path);
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

  test("someone changed the tags meanwhile: the server refuses the second send too, today's sentence, no third", async (t) => {
    const ctx = await page(t);
    const file = ctx.disk.get(A.path);
    ctx.disk.set(A.path, { ...file, tags: ["Trips/Coast", "Activity/Sailing"], mtime: file.mtime + 300 });
    const scans = ctx.scans();
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => /changed on disk since you opened it/.test(ctx.status()), () => `the sentence (${ctx.status()})`);
    assert.ok(ctx.scans() > scans, "the folder was read again to show the file");
    await flush(ctx.window, 12);
    const saves = ctx.saves();
    assert.equal(saves.length, 2, "sent once more, and not a third time");
    assert.deepEqual(saves[1].base, { tags: ["Trips/Coast"], title: "" }, "the second named what the page read, not the file's tags now");
    assert.deepEqual(saves[1].stamp, { mtime: file.mtime + 300, size: file.size }, "and the file's stamp now");
    assert.ok(ctx.alerts.some((message) => /changed on disk/.test(message)), ctx.alerts.join("|"));
    assert.deepEqual(ctx.record(A.path).tags, ["Trips/Coast", "Activity/Sailing"], "nothing merged: the record is the file's");
    assert.equal(ctx.record(A.path).mtime, file.mtime + 300);
    assert.deepEqual(ctx.disk.get(A.path).tags, ["Trips/Coast", "Activity/Sailing"], "nothing written over them");
  });

  test("the bake writes again between the read and the second send: refused too, no third, today's sentence", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.holdReads = true;
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.held, "the read again");
    ctx.holdReads = false;
    ctx.held();
    ctx.bake(A.path);    // after the read, before the second send lands
    await until(ctx, () => /changed on disk since you opened it/.test(ctx.status()), () => `the sentence (${ctx.status()})`);
    await flush(ctx.window, 12);
    assert.equal(ctx.saves().length, 2);
    assert.ok(ctx.alerts.some((message) => /changed on disk/.test(message)));
  });

  test("the page no longer lists the photo when the read lands: nothing is sent again", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
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
    ctx.replies.push(changedOnDisk(), saved({ new_path: renamed }));    // the model makes no rename: these two say it
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
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.saves().length === 2 && /^Saved/.test(ctx.status()), "the first save");
    ctx.bake(A.path);   // the bake's next step writes the file again
    ctx.addTag("Trips/Harbour");
    await until(ctx, () => ctx.saves().length === 4 && /^Saved/.test(ctx.status()), () => `the second save (${ctx.status()})`);
    const saves = ctx.saves();
    assert.deepEqual(saves[2].base.tags, ["Trips/Coast", "Trips/Lakes"], "the second named what the first wrote");
    assert.deepEqual(saves[3].stamp, { mtime: ctx.disk.get(A.path).mtime - 1, size: ctx.disk.get(A.path).size },
      "and was sent again naming the file as the bake left it");
    assert.deepEqual(saves[3].tags, ["Trips/Coast", "Trips/Lakes", "Trips/Harbour"]);
    assert.deepEqual(ctx.alerts, []);
  });

  for (const [what, mark] of [["gone from the disk", { missing: true }], ["that ExifTool could not read", { unreadable: true }]]) {
    test(`a read again finding the file ${what}: not sent again, today's sentence`, async (t) => {
      const ctx = await page(t);
      const file = ctx.disk.get(A.path);
      ctx.disk.set(A.path, { ...file, ...mark, mtime: file.mtime + 300 });
      ctx.addTag("Trips/Lakes");
      await until(ctx, () => /changed on disk since you opened it/.test(ctx.status()), () => `the sentence (${ctx.status()})`);
      await flush(ctx.window, 12);
      assert.equal(ctx.saves().length, 1, "sent again");
      assert.ok(ctx.alerts.some((message) => /changed on disk/.test(message)), ctx.alerts.join("|"));
      assert.ok(!ctx.alerts.some((message) => /could not be read just now/.test(message)), ctx.alerts.join("|"));
    });
  }

  test("the owner opens the next photo while the save is read again: the move waits, it is saved, then the next opens", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.holdReads = true;
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.held, "the read again");
    ctx.document.querySelector(`#photo-list li[data-path="${B.path.replace(/\\/g, "\\\\")}"]`).click();
    await flush(ctx.window, 6);
    assert.equal(ctx.state.activePhotoPath, A.path, "the move waits for the save");
    ctx.holdReads = false;
    ctx.held();
    await until(ctx, () => ctx.state.activePhotoPath === B.path, () => `the next photo (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.deepEqual(ctx.disk.get(A.path).tags, ["Trips/Coast", "Trips/Lakes"], "written");
    assert.deepEqual(Array.from(ctx.record(A.path).tags), ["Trips/Coast", "Trips/Lakes"]);
    assert.deepEqual(ctx.alerts, []);
  });

  test("a library view's photo is read again by its own id and sent once more", async (t) => {
    const ctx = await page(t, { view: true });
    ctx.bake(A.path);
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => /^Saved/.test(ctx.status()), () => `the save (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.equal(ctx.finds(), 0, "a view's photo has its id: nothing to find");
    assert.deepEqual(ctx.saves()[1].stamp, { mtime: A.mtime + 300, size: A.size + 512 });
    assert.match(ctx.status(), /after reading its file again/);
  });
});

describe("V33: closing the tab while a refused save is read again and sent once more", () => {
  test("from the refusal until the second send is answered it waits in this page and closing the tab asks", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.holdReads = true;
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.held, "the read again");
    assert.equal(line(ctx), "1 photo waiting in this page: stay until it is sent");
    assert.equal(leaving(ctx), true, "closing the tab while the refused save was read again asked nothing: it was lost");
    assert.equal(ctx.document.querySelector(".write-queue-entry .write-queue-state").textContent, "writing, its file read again");
    ctx.holdSave = true;
    ctx.holdReads = false;
    ctx.held();
    await until(ctx, () => ctx.heldSave, "the second send");
    assert.equal(leaving(ctx), true, "the second send not answered yet: it may be refused again");
    ctx.heldSave();
    await until(ctx, () => /^Saved/.test(ctx.status()), () => `the save (${ctx.status()})`);
    assert.equal(line(ctx), "");
    assert.equal(leaving(ctx), false, "a save written still asked");
  });

  test("refused the second time: it stops waiting, the refusal said; only the text given back asks, and Escape lets it go", async (t) => {
    const ctx = await page(t);
    const file = ctx.disk.get(A.path);
    ctx.disk.set(A.path, { ...file, tags: ["Trips/Coast", "Activity/Sailing"], mtime: file.mtime + 300 });
    ctx.holdReads = true;
    ctx.addTag("Trips/Lakes");
    await asksWhileReadAgain(ctx);
    await until(ctx, () => ctx.alerts.length, "the refusal said");
    await flush(ctx.window, 12);
    assert.equal(ctx.saves().length, 2, "no third send");
    assert.equal(ctx.document.querySelector(".write-queue-entry").dataset.status, "failed");
    assert.equal(line(ctx), "", "a refused save still said it waited in the page");
    const box = ctx.document.getElementById("input-add-tag");
    assert.equal(box.value, "Trips/Lakes", "the text typed was given back");
    assert.equal(leaving(ctx), true, "the text given back is unsaved: closing asks for it");
    box.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    assert.equal(leaving(ctx), false, "closing the tab asked forever");
  });

  test("rapid saves while one is read again: they wait behind it, one photo waiting, each written once", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.holdReads = true;
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.held, "the read again");
    ctx.addTag("Trips/Harbour");
    ctx.addTag("Trips/Bay");
    await flush(ctx.window, 6);
    assert.equal(ctx.saves().length, 1, "a save behind the one read again was sent beside it");
    assert.equal(line(ctx), "1 photo waiting in this page: stay until it is sent");
    assert.equal(leaving(ctx), true);
    ctx.holdReads = false;
    ctx.held();
    await until(ctx, () => ctx.saves().length === 4 && !line(ctx), () => `the saves (${ctx.saves().length}, ${line(ctx)})`);
    assert.deepEqual(ctx.disk.get(A.path).tags, ["Trips/Coast", "Trips/Lakes", "Trips/Harbour", "Trips/Bay"]);
    assert.equal(leaving(ctx), false);
    assert.deepEqual(ctx.alerts, []);
  });

  test("another folder opened while it is read again: the folder waits for the save, then opens", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    ctx.holdReads = true;
    ctx.addTag("Trips/Lakes");
    await until(ctx, () => ctx.held, "the read again");
    await openFolder(ctx, "D:\\Library\\2022 Hills");
    assert.equal(ctx.state.scannedFolder, FOLDER, "the folder opened over a save not written");
    ctx.holdReads = false;
    ctx.held();
    await until(ctx, () => ctx.state.scannedFolder === "D:\\Library\\2022 Hills", () => `the folder (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.deepEqual(ctx.disk.get(A.path).tags, ["Trips/Coast", "Trips/Lakes"], "written");
    assert.equal(leaving(ctx), false);
  });

  test("the Edit Date Taken dialog's save the same (#3425)", async (t) => {
    const ctx = await page(t, { taken: "2020:01:02 03:04:05" });
    ctx.bake(A.path);
    ctx.holdReads = true;
    ctx.document.getElementById("btn-edit-date-taken").click();
    ctx.document.getElementById("input-date-taken").value = "2021-05-06T07:08:09";
    click(ctx.window, ctx.document.getElementById("btn-save-date-modal"));
    await asksWhileReadAgain(ctx);
    await until(ctx, () => /^Saved the date taken/.test(ctx.status()), () => `the save (${ctx.status()})`);
    assert.equal(leaving(ctx), false);
  });
});

describe("V33: a Date Taken saved from the dialog goes by the same rule (#3425)", () => {
  /** The owner types `date` in the Edit Date Taken dialog of the open photo and saves. */
  function saveDate(ctx, date) {
    ctx.document.getElementById("btn-edit-date-taken").click();
    ctx.document.getElementById("input-date-taken").value = date;
    click(ctx.window, ctx.document.getElementById("btn-save-date-modal"));
  }

  test("the bake wrote its own fields only: the date is sent once more naming the date the page read", async (t) => {
    const ctx = await page(t, { taken: "2020:01:02 03:04:05" });
    ctx.bake(A.path);
    saveDate(ctx, "2021-05-06T07:08:09");
    await until(ctx, () => /^Saved the date taken/.test(ctx.status()), () => `the save (${ctx.status()})`);
    const [first, second] = ctx.saves();
    assert.equal(ctx.saves().length, 2);
    assert.equal(first.base.date_taken, "2020:01:02 03:04:05", "the first names the date the page read");
    assert.equal(second.base.date_taken, "2020:01:02 03:04:05", "and so does the second, not the file's read again");
    assert.equal(second.date_taken, first.date_taken, "the same save");
    assert.deepEqual(second.stamp, { mtime: A.mtime + 300, size: A.size + 512 });
    assert.match(ctx.status(), /^Saved the date taken after reading its file again/);
    assert.equal(ctx.disk.get(A.path).taken, "2021:05:06 07:08:09");
    assert.equal(ctx.record(A.path).taken, "2021:05:06 07:08:09");
    assert.deepEqual(ctx.alerts, []);
  });

  test("a photo the page read without a date: its base names none, an empty one", async (t) => {
    const ctx = await page(t);
    ctx.bake(A.path);
    saveDate(ctx, "2021-05-06T07:08:09");
    await until(ctx, () => /^Saved the date taken/.test(ctx.status()), () => `the save (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.equal(ctx.saves()[1].base.date_taken, "");
  });

  test("another program dated the file meanwhile: the second send is refused too, nothing is written over its date", async (t) => {
    const ctx = await page(t, { taken: "2020:01:02 03:04:05" });
    const file = ctx.disk.get(A.path);
    ctx.disk.set(A.path, { ...file, taken: "2022:02:02 02:02:02", mtime: file.mtime + 300 });
    saveDate(ctx, "2021-05-06T07:08:09");
    await until(ctx, () => /changed on disk since you opened it/.test(ctx.status()), () => `the sentence (${ctx.status()})`);
    await flush(ctx.window, 12);
    assert.equal(ctx.saves().length, 2, "sent once more, and not a third time");
    assert.equal(ctx.saves()[1].base.date_taken, "2020:01:02 03:04:05");
    assert.ok(ctx.alerts.some((message) => /changed on disk/.test(message)), ctx.alerts.join("|"));
    assert.equal(ctx.disk.get(A.path).taken, "2022:02:02 02:02:02", "nothing written over it");
  });

  test("two dates saved in a row: each goes once, the second naming the first's date", async (t) => {
    const ctx = await page(t, { taken: "2020:01:02 03:04:05" });
    saveDate(ctx, "2021-05-06T07:08:09");
    await until(ctx, () => /^Saved the date taken/.test(ctx.status()), () => `the first (${ctx.status()})`);
    saveDate(ctx, "2022-06-07T08:09:10");
    await until(ctx, () => ctx.saves().length === 2, "the second send");
    await flush(ctx.window, 12);
    assert.equal(ctx.saves().length, 2, "no resend: the file is as the first save left it");
    assert.equal(ctx.saves()[1].base.date_taken, "2021:05:06 07:08:09");
    assert.equal(ctx.disk.get(A.path).taken, "2022:06:07 08:09:10");
  });

  test("the photo gone when it is read again: the refusal as before, not sent again", async (t) => {
    const ctx = await page(t, { taken: "2020:01:02 03:04:05" });
    ctx.bake(A.path);
    ctx.disk.set(A.path, { ...ctx.disk.get(A.path), missing: true });
    saveDate(ctx, "2021-05-06T07:08:09");
    await until(ctx, () => ctx.alerts.length, "the refusal said");
    await flush(ctx.window, 12);
    assert.equal(ctx.saves().length, 1);
  });
});

describe("V33: the page's other saves go by the same rule", () => {
  const sentAgain = / after reading its file again \(indexing, or another program, had written to it\)$/;

  test("a card's title typed in the folder's grid", async (t) => {
    const ctx = await page(t, { open: false });
    ctx.bake(A.path);
    ctx.holdReads = true;
    const card = [...ctx.document.querySelectorAll("[data-path]")]
      .find((each) => each.getAttribute("data-path") === A.path && each.querySelector(".thumbnail-filename"));
    card.querySelector(".thumbnail-filename").dispatchEvent(new ctx.window.MouseEvent("click", { bubbles: true }));
    const input = card.querySelector(".thumbnail-filename-input");
    input.value = "Harbour";
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await asksWhileReadAgain(ctx);   // not the open photo's: another library chosen now goes, and the browser asks
    await until(ctx, () => /^Saved/.test(ctx.status()), () => `the save (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.deepEqual(ctx.saves()[1].stamp, { mtime: A.mtime + 300, size: A.size + 512 });
    assert.match(ctx.status(), new RegExp("^Saved the title" + sentAgain.source));
    assert.equal(ctx.disk.get(A.path).title, "Harbour");
    assert.deepEqual(ctx.alerts, []);
  });

  test("Carry forward", async (t) => {
    const ctx = await page(t);
    await ctx.open(B.path);
    ctx.bake(B.path);
    ctx.holdReads = true;
    ctx.document.getElementById("btn-carry-forward").click();
    await asksWhileReadAgain(ctx);
    await until(ctx, () => /^Copied/.test(ctx.status()), () => `the carry (${ctx.status()})`);
    assert.equal(ctx.saves().length, 2);
    assert.match(ctx.status(), new RegExp("^Copied 1 tag\\(s\\) from a\\.jpg" + sentAgain.source));
    assert.deepEqual(ctx.disk.get(B.path).tags, ["Activity/Sailing", "Trips/Coast"]);
    assert.deepEqual(ctx.alerts, []);
  });

  for (const [how, take] of [
    ["a suggestion taken", (ctx) => pageExports(ctx.window, "web/tagpup/suggestions.js").applySuggestedTagsDirect(["Trips/Lakes"], false)],
    ["the photo's suggestions taken all at once", (ctx) => {
      ctx.state.folderSuggestions[A.path] = { tags: [{ tag: "Trips/Lakes" }], people: [] };
      return pageExports(ctx.window, "web/tagpup/suggestions.js").applyAllSingleSuggestions();
    }],
  ]) {
    test(how, async (t) => {
      const ctx = await page(t);
      ctx.bake(A.path);
      ctx.holdReads = true;
      take(ctx);
      await asksWhileReadAgain(ctx);
      await until(ctx, () => /^Saved/.test(ctx.status()), () => `the save (${ctx.status()})`);
      assert.equal(ctx.saves().length, 2);
      assert.match(ctx.status(), new RegExp("^Saved" + sentAgain.source));
      assert.deepEqual(ctx.disk.get(A.path).tags, ["Trips/Coast", "Trips/Lakes"]);
      assert.deepEqual(ctx.alerts, []);
    });
  }
});
