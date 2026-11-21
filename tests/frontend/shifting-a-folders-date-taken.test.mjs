/**
 * The owner moves a camera's Date Taken in a folder by some minutes (Shift Date Taken, web/tagpup/rename.js applyTimeShift).
 *
 * The page chooses the photos (the camera's, never a clip) and sends them by path in one request to the one entry point of
 * photo writes (POST /api/photos/write, op `shift`; plan one-write-path, cut 3b). What the owner lives through: the folder
 * put back in Date Taken order with each photo's new date as the server spells it, a second click finding the minutes
 * reset; a share that stops answering part-way (what landed is shown, the rest said); a refusal before any write (names
 * being given from tags) that changes nothing; a folder left before the reply came, which the reply does not touch. The
 * replies are the real entry point's (fixtures/shift_replies.json, harness.mjs shiftedAs).
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  loadApp, FakeServer, closeAllApps, openFolder, click, flush, photoRecord, answer, pageExports, SHIFT, shiftedAs,
} from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Pictures\\Regatta";
const OTHER = "D:\\Pictures\\Harbour";
const CAMERA = "Tidewater TX R6m2";
const at = (folder, filename, own) => photoRecord({ path: `${folder}\\${filename}`, filename, camera: CAMERA, ...own });

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

test("a camera's photos are sent by path in one request, and the folder takes their new dates in Date Taken order", async (t) => {
  // B moved past C: the server's folder order would put it after C, and so does the page.
  const ctx = await opened(t, shiftedAs("shifted", [{ taken: "2020:06:01 10:30:00" }, { taken: "2020:06:01 10:50:00" }]));
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
  assert.match(ctx.said(), /^Time shift applied to 2 photo\(s\)$/);

  // A second click straight after: the minutes are 0, nothing more is sent.
  click(ctx.window, ctx.$("btn-apply-timeshift"));
  await flush(ctx.window, 6);
  assert.equal(ctx.shifts().length, 1);
});

test("a share that stops answering part-way: the photo that landed takes its date, and the status says how many did not", async (t) => {
  const ctx = await opened(t, shiftedAs("stopped"), OTHER);
  await ctx.shift(CAMERA);
  assert.equal(Object.keys(ctx.shifts()[0].changes).length, 3);
  assert.deepEqual(ctx.order(), ["IMG_1001.jpg", "IMG_1002.jpg", "IMG_1000.jpg"], "the first, written, is 30 minutes later now");
  assert.match(ctx.said(), /^Time shift applied to 1 of 3 photo\(s\); 2 could not be written: ExifTool stopped answering after 1 of 3/);
  assert.deepEqual(ctx.alerts, []);
});

test("a shift refused before any write (names being given from tags) changes nothing and says it failed", async (t) => {
  const why = "Names are being given from tags; this waits until it finishes.";
  const ctx = await opened(t, () => answer(409, { success: false, error: why }));
  const before = ctx.order();
  await ctx.shift(CAMERA);
  assert.deepEqual(ctx.order(), before);
  assert.equal(ctx.said(), "Time shift failed");
  assert.deepEqual(ctx.alerts, [`Error shifting time: ${why}`]);
});

test("a folder left before the shift's reply came is not touched by it", async (t) => {
  let reply;
  const ctx = await opened(t, (url, body) => new Promise((resolve) => {
    reply = () => resolve(shiftedAs("shifted", [{ taken: "2020:06:01 11:30:00" }, { taken: "2020:06:01 09:30:00" }])(url, body));
  }));
  await ctx.shift(CAMERA);
  await openFolder(ctx, OTHER);
  await flush(ctx.window, 10);
  assert.deepEqual(ctx.order(), ["IMG_1000.jpg", "IMG_1001.jpg", "IMG_1002.jpg"]);
  // Then a library view, listing the two photos shifted, by name the other way: its records and order are its own.
  const { state } = pageExports(ctx.window, "web/tagpup/state.js");
  state.library = { name: "photo_index" };
  state.folderPhotos = [{ ...B }, { ...A }];
  reply();
  await flush(ctx.window, 10);
  assert.deepEqual(state.folderPhotos.map((p) => [p.filename, p.taken, p.mtime]),
    [["IMG_0002.jpg", B.taken, B.mtime], ["IMG_0001.jpg", A.taken, A.mtime]], "neither patched nor put in another order");
  assert.match(ctx.said(), /^Time shift applied to 2 photo\(s\)$/, "what was written is still said");
});
