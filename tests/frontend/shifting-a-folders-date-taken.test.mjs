/**
 * The owner moves a camera's Date Taken in a folder by some minutes (Shift Date Taken, web/tagpup/rename.js applyTimeShift).
 *
 * The page chooses the photos (the camera's, never a clip) and sends them by path in one request to the one entry point of
 * photo writes (POST /api/photos/write, op `shift`; plan one-write-path, cut 3b). What the owner lives through: the folder
 * put back in the server's order with each photo's new date as the server spells it, a second click finding the minutes
 * reset; a second shift clicked before the first one's reply, sent after it; a share that stops answering part-way (what
 * landed is shown, the rest said); a refusal before any write (names being given from tags) that changes nothing. Every
 * scan this browser keeps that holds the photos takes the reply too (scenario-a-write-reaches-every-scan-kept). The
 * replies are the real entry point's (fixtures/shift_replies.json, harness.mjs shiftedAs).
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, closeAllApps, openFolder, click, flush, photoRecord, pageExports, SHIFT, shiftedAs,
  SHIFT_REPLIES, SAVE, SAVE_REPLIES, savedAs,
} from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Pictures\\Regatta";
const OTHER = "D:\\Pictures\\Harbour";
const CAMERA = "Tidewater TX R6m2";
// A record as a scan's reply gives it: `order`, the server's key of its place in the folder (tagpup.core.dates
// taken_sort_key: its Date Taken, the undated after by file time).
const at = (folder, filename, own) => {
  const record = photoRecord({ path: `${folder}\\${filename}`, filename, camera: CAMERA, ...own });
  return { order: record.taken ?? `mtime_${record.mtime}`, ...record };
};
// A photo's new Date Taken as a shift's reply gives it (`written[path]`): its `order` is the date.
const dated = (taken) => ({ taken, order: taken });

// As the server orders a folder: by Date Taken, the undated after.
const CLIP = at(FOLDER, "MVI_0004.mp4", { media: "video", taken: "2020:06:01 09:00:00" });
const A = at(FOLDER, "IMG_0001.jpg", { taken: "2020:06:01 10:00:00" });
const B = at(FOLDER, "IMG_0002.jpg", { taken: "2020:06:01 10:20:00" });
const C = at(FOLDER, "IMG_0003.jpg", { camera: "Lanternfly Pixel 8 Pro", taken: "2020:06:01 10:40:00" });
const UNDATED = at(FOLDER, "IMG_0005.jpg", { camera: "Lanternfly Pixel 8 Pro" });
const HARBOUR = [0, 1, 2].map((n) => at(OTHER, `IMG_100${n}.jpg`, { taken: "2020:06:01 10:00:00" }));

function server(shift) {
  return new FakeServer()
    .first(SHIFT, shift)
    .first(SAVE, savedAs(SAVE_REPLIES.dated, { new_path: C.path }))
    .on("/api/folder/scan", (url) => (url.includes(encodeURIComponent(OTHER)) ? HARBOUR : [CLIP, A, B, C, UNDATED])
      .map((record) => ({ ...record })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

async function opened(t, shift, folder = FOLDER) {
  const fake = server(shift);
  const ctx = await loadApp("tagpup", { server: fake, t });
  ctx.fake = fake;
  ctx.alerts = [];
  ctx.window.alert = (text) => ctx.alerts.push(text);
  ctx.window.confirm = () => true;
  await openFolder(ctx, folder);
  await flush(ctx.window, 10);
  const $ = (id) => ctx.document.getElementById(id);
  ctx.$ = $;
  ctx.shifts = () => fake.calls.filter((c) => SHIFT(c.url, c.body)).map((c) => c.body);
  ctx.order = () => [...ctx.document.querySelectorAll(".photo-item-file")].map((el) => el.dataset.path.split("\\").pop());
  ctx.said = () => $("status-text").textContent;
  ctx.shift = async (camera, minutes = "30") => {
    click(ctx.window, $("btn-toggle-timeshift"));
    $("timeshift-camera-select").value = camera;
    $("timeshift-minutes-input").value = minutes;
    const apply = $("btn-apply-timeshift");
    apply.disabled = false;
    click(ctx.window, apply);
    await flush(ctx.window, 10);
  };
  return ctx;
}

test("a camera's photos are sent by path in one request, and the folder takes their new dates in the server's order", async (t) => {
  // B moved past C: the server's folder order puts it after C, and so does the page.
  const ctx = await opened(t, shiftedAs("shifted", [dated("2020:06:01 10:30:00"), dated("2020:06:01 10:50:00")]));
  click(ctx.window, [...ctx.document.querySelectorAll(".photo-item-file")].find((el) => el.dataset.path.endsWith("IMG_0002.jpg")));
  await flush(ctx.window, 6);
  assert.deepEqual(ctx.order(), ["MVI_0004.mp4", "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg", "IMG_0005.jpg"]);

  await ctx.shift(CAMERA);
  assert.deepEqual(ctx.shifts(), [{ op: "shift", params: { minutes: 30 }, changes: { [A.path]: { id: null }, [B.path]: { id: null } } }],
    "the camera's photos and not its clip, by path, in one request");
  assert.equal(ctx.$("timeshift-minutes-input").value, "0", "the minutes are reset as it is sent");
  assert.deepEqual(ctx.order(), ["MVI_0004.mp4", "IMG_0001.jpg", "IMG_0003.jpg", "IMG_0002.jpg", "IMG_0005.jpg"],
    "in Date Taken order again, the undated last");
  assert.match(ctx.$("detail-date-taken").textContent, /10:50/, "the open photo shows its new Date Taken");
  assert.match(ctx.said(), /^Time shift applied to 2 photos$/);

  // A second click straight after: the minutes are 0, nothing more is sent.
  click(ctx.window, ctx.$("btn-apply-timeshift"));
  await flush(ctx.window, 6);
  assert.equal(ctx.shifts().length, 1);
});

test("a share that stops answering part-way: the photo that landed takes its stamp and keeps its date, and the status says how many did not", async (t) => {
  // The share stopped after the first photo's write landed, so the read back after the write did not reach it: the reply
  // names its stamp and no Date Taken. It keeps the date it showed, never "Unknown" put last (#3300).
  const ctx = await opened(t, shiftedAs("stopped"), OTHER);
  await ctx.shift(CAMERA);
  assert.equal(Object.keys(ctx.shifts()[0].changes).length, 3);
  const { state } = pageExports(ctx.window, "web/tagpup/state.js");
  assert.deepEqual([...state.folderPhotos].map((p) => [p.filename, p.taken, p.mtime]), [
    ["IMG_1000.jpg", "2020:06:01 10:00:00", 1], ["IMG_1001.jpg", "2020:06:01 10:00:00", HARBOUR[1].mtime],
    ["IMG_1002.jpg", "2020:06:01 10:00:00", HARBOUR[2].mtime]], "the written one takes its stamp, and no date not read");
  assert.deepEqual(ctx.order(), ["IMG_1000.jpg", "IMG_1001.jpg", "IMG_1002.jpg"]);
  assert.match(ctx.said(), /^Time shift applied to 1 of 3 photo\(s\); 2 could not be written: ExifTool stopped answering after 1 of 3/);
  assert.deepEqual(ctx.alerts, []);
});

test("a shift refused before any write (names being given from tags) changes nothing and says it failed", async (t) => {
  const why = SHIFT_REPLIES.refused.reply.error;
  const ctx = await opened(t, shiftedAs("refused"));
  const before = ctx.order();
  await ctx.shift(CAMERA);
  assert.deepEqual(ctx.order(), before);
  assert.equal(ctx.said(), "Time shift failed");
  assert.deepEqual(ctx.alerts, [`Error shifting time: ${why}`]);
});

test("an envelope that wrote nothing and says why is the failure it says, not a count of photos applied", async (t) => {
  // The share stopped before the first photo's write: the envelope (400) names no photo written. As a refusal before any
  // write, it is "Time shift failed" and its reason in the alert (#3302). The envelope is the real entry point's (#3307).
  const why = SHIFT_REPLIES.stopped_first.reply.error;
  const ctx = await opened(t, shiftedAs("stopped_first"));
  const before = ctx.order();
  await ctx.shift(CAMERA);
  assert.deepEqual(ctx.order(), before);
  assert.equal(ctx.said(), "Time shift failed");
  assert.deepEqual(ctx.alerts, [`Error shifting time: ${why}`]);
});

test("a second shift clicked before the first one's reply is sent after it is taken, never beside it", async (t) => {
  // The owner clicks Apply again while the first shift is out: the second waits in the page's one line of writes (a reply
  // taken out of order would give a photo the stamp the earlier write left), and the status says it waits.
  const held = [];
  const ctx = await opened(t, (url, body) => new Promise((resolve) => {
    const minutes = body.params.minutes;
    const own = minutes === 30
      ? [dated("2020:06:01 10:30:00"), dated("2020:06:01 10:50:00")]
      : [dated("2020:06:01 10:45:00"), dated("2020:06:01 11:05:00")];
    held.push(() => resolve(shiftedAs("shifted", own)(url, body)));
  }));
  await ctx.shift(CAMERA);
  await ctx.shift(CAMERA, "15");
  assert.equal(ctx.shifts().length, 1, "the second is not sent while the first is out");
  assert.match(ctx.said(), /^Shift of 2 photo\(s\) waits for the writes before it/);

  held[0]();
  await flush(ctx.window, 10);
  assert.deepEqual(ctx.shifts().map((body) => body.params.minutes), [30, 15], "sent once the first reply is taken");
  assert.deepEqual(Object.keys(ctx.shifts()[1].changes), [A.path, B.path]);

  held[1]();
  await flush(ctx.window, 10);
  const { state } = pageExports(ctx.window, "web/tagpup/state.js");
  assert.deepEqual([...state.folderPhotos].filter((p) => p.camera === CAMERA && p.media !== "video").map((p) => [p.filename, p.taken]),
    [["IMG_0001.jpg", "2020:06:01 10:45:00"], ["IMG_0002.jpg", "2020:06:01 11:05:00"]], "the last reply's dates, taken last");
  assert.match(ctx.said(), /^Time shift applied to 2 photos$/);
  assert.deepEqual(ctx.alerts, []);
});

test("photos holding no Date Taken are said as such, not as photos that could not be written (#3305)", async (t) => {
  const skipped = (n) => Array.from({ length: n }, (_, i) => ({ path: `PHOTO_${i}`, why: "holds no Date Taken to move" }));
  const ctx = await opened(t, shiftedAs("shifted", [], { attempted: 4, changed: 2, skipped: skipped(2) }));
  await ctx.shift(CAMERA);
  assert.equal(ctx.said(), "Time shift applied to 2 photos; 2 had no Date Taken to move.");

  const one = await opened(t, shiftedAs("shifted", [], { attempted: 2, changed: 1, skipped: skipped(1) }));
  await one.shift(CAMERA);
  assert.equal(one.said(), "Time shift applied to 1 photo; 1 had no Date Taken to move.");
});

test("a photo whose Date Taken was saved sits at its new date's place when a shift then re-sorts the folder", async (t) => {
  // The save's reply gives the photo's new `order` with its date; a shift of the other camera's photos puts the folder back
  // in order, and the photo saved is not among those it moves, so it must carry the order the save gave it.
  const ctx = await opened(t, shiftedAs("shifted", [dated("2020:06:01 10:30:00"), dated("2020:06:01 10:50:00")]));
  click(ctx.window, [...ctx.document.querySelectorAll(".photo-item-file")].find((el) => el.dataset.path.endsWith("IMG_0003.jpg")));
  await flush(ctx.window, 6);
  click(ctx.window, ctx.$("btn-edit-date-taken"));
  ctx.$("input-date-taken").value = "2019-03-02T09:15:00";
  click(ctx.window, ctx.$("btn-save-date-modal"));
  await flush(ctx.window, 10);
  const { state } = pageExports(ctx.window, "web/tagpup/state.js");
  assert.equal(state.folderPhotos.find((p) => p.path === C.path).order, SAVE_REPLIES.dated.written.PHOTO.order);

  await ctx.shift(CAMERA);
  assert.deepEqual(ctx.order(), ["IMG_0003.jpg", "MVI_0004.mp4", "IMG_0001.jpg", "IMG_0002.jpg", "IMG_0005.jpg"],
    "the photo saved is first, at its 2019 date, not where its old date put it");
});
