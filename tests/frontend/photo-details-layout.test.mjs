/**
 * Image Details, laid out for the person tagging: where the photo is as its folder's name and its own, each a link, with a button
 * that copies the whole path (web/tagpup/photo-path.js); Date taken, Size and the camera as labelled lines of the second column;
 * and the boxes that add a person or a keyword shown by a pencil, closed until it is clicked (web/tagpup/edit-toggles.js).
 */
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadApp, FakeServer, advance, closeAllApps, openFolder, click, PHOTO_WRITES, testClock } from "./harness.mjs";
import { loadViewPage, recordOf } from "./view-page.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const PHOTO = { path: "D:\\Pictures\\Run\\a.jpg", filename: "a.jpg", tags: [], people: [], captions: [], title: "", writes: PHOTO_WRITES, camera: "Tidewater TX R6m2", lens: "" };

function server(records = [PHOTO]) {
  return new FakeServer()
    .on("/api/folder/scan", records)
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

afterEach(() => closeAllApps());

async function openPhoto(t) {
  const { window, document } = await loadApp("tagpup", { server: server(), t });
  await openFolder({ document, window }, "D:\\Pictures\\Run");
  await advance(window, 60);
  click(window, document.querySelector(".photo-item-file"));
  await advance(window, 40);
  return { window, document };
}

test("the camera and lens are a line inside the Date Taken's box", async (t) => {
  const { document } = await openPhoto(t);
  const camera = document.getElementById("detail-camera-item");
  assert.ok(!camera.classList.contains("hidden"));
  assert.equal(camera.closest(".detail-item"), document.getElementById("detail-date-taken").closest(".detail-item"));
});

test("the add boxes of people and keywords are closed until their pencil is clicked", async (t) => {
  const { window, document } = await openPhoto(t);
  for (const [pencil, group, input] of [["btn-edit-people", "add-people-group", "input-add-person"],
                                        ["btn-edit-tags", "add-tags-group", "input-add-tag"]]) {
    const box = document.getElementById(group);
    assert.ok(box.classList.contains("hidden"), `${group} was open`);
    click(window, document.getElementById(pencil));
    assert.ok(!box.classList.contains("hidden"), `${pencil} opened nothing`);
    assert.equal(document.getElementById(pencil).getAttribute("aria-expanded"), "true");
    click(window, document.getElementById(pencil));
    assert.ok(box.classList.contains("hidden"), `${pencil} did not close it`);
    // With a name typed, the pencil keeps the box open: that text is not yet added.
    click(window, document.getElementById(pencil));
    document.getElementById(input).value = "Someone";
    click(window, document.getElementById(pencil));
    assert.ok(!box.classList.contains("hidden"), `${group} closed over a typed name`);
  }
});

async function openWith(t, record, { before } = {}) {
  const srv = server([record]);
  const { window, document } = await loadApp("tagpup", { server: srv, t, before });
  await openFolder({ document, window }, "D:\\Pictures\\Run");
  await advance(window, 60);
  click(window, document.querySelector(".photo-item-file"));
  await advance(window, 40);
  return document;
}

// A folder and a name with the characters an address reads otherwise (& # +), and long enough to be cut.
const LONG_FOLDER = "D:\\Pictures\\Swim & Dive #3 + Relay at the Harbour Pool, the whole afternoon";
const LONG_NAME = "Lane 4 & 5 #2 + the finish of the long relay, taken from the far end.jpg";

test("where the photo is reads as its folder's name and its own, each whole in its tooltip, and the copy button copies the whole path", async (t) => {
  const copied = [];
  const before = (window) => Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async (text) => { copied.push(text); } } });
  const path = `${LONG_FOLDER}\\${LONG_NAME}`;
  const page = await openWith(t, { ...PHOTO, path, filename: LONG_NAME }, { before });
  const folder = page.getElementById("detail-folder"), name = page.getElementById("detail-file-name");
  assert.equal(folder.textContent, "Swim & Dive #3 + Relay at the Harbour Pool, the whole afternoon");
  assert.equal(folder.title, `Back to this folder's grid: ${LONG_FOLDER}`);
  assert.equal(name.textContent, LONG_NAME);
  assert.equal(name.title, `Open in the default app: ${path}`);
  assert.ok(folder.classList.contains("one-line") && name.classList.contains("one-line"), "a long name is cut with an ellipsis");
  click(page.defaultView, page.getElementById("btn-copy-path"));
  await advance(page.defaultView, 40);
  assert.deepEqual(copied, [path]);
  assert.match(page.getElementById("status-text").textContent, /Copied the full path/);
});

test("a browser that will not let the page copy says so, rather than seeming to have copied", async (t) => {
  const before = (window) => Object.defineProperty(window.navigator, "clipboard", { value: undefined });
  const page = await openWith(t, PHOTO, { before });
  click(page.defaultView, page.getElementById("btn-copy-path"));
  await advance(page.defaultView, 40);
  assert.equal(page.getElementById("status-text").textContent, "This browser will not let the page copy; select the path in the panel instead.");
});

test("a copy the browser refuses says the same sentence, never the browser's own error", async (t) => {
  const before = (window) => Object.defineProperty(window.navigator, "clipboard", { value: { writeText: async () => { throw new Error("NotAllowedError: Document is not focused"); } } });
  const page = await openWith(t, PHOTO, { before });
  click(page.defaultView, page.getElementById("btn-copy-path"));
  await advance(page.defaultView, 40);
  assert.equal(page.getElementById("status-text").textContent, "This browser will not let the page copy; select the path in the panel instead.");
});

test("a clip's name is a label with its whole path, not a button promising to open it", async (t) => {
  const path = "D:\\Pictures\\Run\\a.mp4";
  const page = await openWith(t, { ...PHOTO, path, filename: "a.mp4", media: "video", size: 3 * 1024 * 1024, camera: "" });
  const name = page.getElementById("detail-file-name");
  assert.equal(name.title, path);
  assert.equal(name.disabled, true);
});

test("the folder's name opens it in the library this page is on, and back to its grid when that is the view the photo came from", async (t) => {
  // The page on the second of two libraries that hold one folder, a folder whose name an address reads otherwise: the view is
  // asked of this library, for that folder.
  const folder = "D:\\Library\\Swim & Dive #3 + Relay";
  const ctx = await loadViewPage(t, {
    search: "?view=all", library: "kr-track",
    onPhoto: (id) => ({ photo: recordOf(id, { path: `${folder}\\IMG_${id}.jpg`, filename: `IMG_${id}.jpg` }) }),
  });
  ctx.cardById(13).querySelector(".btn-thumbnail-detail").click();
  await ctx.settle(100);
  const asked = ctx.server.urls().length;
  click(ctx.window, ctx.document.getElementById("detail-folder"));
  await ctx.settle(100);
  const views = ctx.server.urls().slice(asked).filter((url) => url.includes("/api/library/ids"));
  assert.ok(views.length >= 1 && views.every((url) => url.startsWith("/kr-track/api/library/ids")), `${views}`);
  const params = new URL(views[0], "http://localhost").searchParams;
  assert.equal(params.get("kind"), "folder");
  assert.equal(params.get("folder"), folder);
  assert.equal(new URLSearchParams(ctx.window.location.search).get("value"), folder, "the address names the folder");
  assert.equal(ctx.document.getElementById("detail-folder").title, `Show this folder in the library: ${folder}`);
  assert.ok(ctx.document.getElementById("panel-content").classList.contains("hidden"), "the folder's grid is shown");

  // A photo of that folder's view: its folder's name goes back to the grid it came from, asking the library nothing.
  ctx.cardById(14).querySelector(".btn-thumbnail-detail").click();
  await ctx.settle(100);
  assert.ok(!ctx.document.getElementById("panel-content").classList.contains("hidden"));
  const before = ctx.server.urls().length;
  click(ctx.window, ctx.document.getElementById("detail-folder"));
  await ctx.settle(100);
  assert.ok(ctx.document.getElementById("panel-content").classList.contains("hidden"), "the grid was not shown again");
  assert.equal(ctx.server.urls().slice(before).filter((url) => url.includes("/api/library/ids")).length, 0);
});

test("a photo opened in Organize: its folder's name goes back to the Organize grid, asking the library nothing", async (t) => {
  const srv = server([PHOTO]);
  const { window, document } = await loadApp("tagpup", { server: srv, t });
  await openFolder({ document, window }, "D:\\Pictures\\Run");
  await advance(window, 60);
  click(window, document.querySelector(".photo-item-file"));
  await advance(window, 40);
  const folder = document.getElementById("detail-folder");
  assert.equal(folder.title, "Back to this folder's grid: D:\\Pictures\\Run");
  assert.ok(!document.getElementById("panel-content").classList.contains("hidden"), "the photo is open");
  const asked = srv.urls().length;
  click(window, folder);
  await advance(window, 100);
  assert.ok(document.getElementById("panel-content").classList.contains("hidden"), "the photo closed");
  assert.ok(!document.getElementById("folder-view-content").classList.contains("hidden"), "the Organize grid is shown");
  assert.equal(srv.urls().slice(asked).filter((url) => url.includes("/api/library/ids")).length, 0);
});

test("the Image Details box is as wide as the picture's box and the faces' below it", async (t) => {
  // The row once held the suggestions beside the details (2fr 1fr); they moved to the analysis row and the details kept two thirds
  // of the width. jsdom lays nothing out: the row's rule is read, and the row is seen to hold the details alone.
  const css = readFileSync(new URL("../../web/tagpup/style.css", import.meta.url), "utf8");
  const rules = [...css.matchAll(/\.details-and-suggestions-row(?::has\([^)]*\))?\s*\{([^}]*)\}/g)].map((m) => m[1]);
  const tracks = rules.map((rule) => (rule.match(/grid-template-columns:\s*([^;]*);/) || [, "1fr"])[1].trim().split(/\s+/).length);
  assert.ok(rules.length >= 1 && tracks.every((n) => n === 1), `the details take part of the width: ${tracks}`);
  const page = await openWith(t, PHOTO);
  const row = page.querySelector(".details-and-suggestions-row");
  assert.deepEqual([...row.children].map((child) => child.className), ["details-section"]);
});

test("a photo's and a clip's size read on the Size line under Date taken, the camera under both only when the file names one", async (t) => {
  for (const [record, size, camera] of [
    [{ ...PHOTO, size: 820 * 1024 }, "820 KB", "Tidewater TX R6m2"],
    [{ ...PHOTO, path: "D:\\Pictures\\Run\\a.mp4", filename: "a.mp4", media: "video", size: 3 * 1024 * 1024, camera: "" }, "3.0 MB", null],
  ]) {
    const page = await openWith(t, record);
    const lines = [...page.querySelectorAll(".detail-fact")].filter((line) => !line.classList.contains("hidden"));
    assert.deepEqual(lines.map((line) => line.querySelector(".detail-fact-label").textContent),
      camera ? ["Date taken:", "Size:", "Camera:"] : ["Date taken:", "Size:"], record.filename);
    assert.equal(page.getElementById("detail-size").textContent, size);
    assert.equal(new Set(lines.map((line) => line.closest(".detail-item"))).size, 1, "the lines are one column's");
    if (camera) assert.equal(page.getElementById("detail-camera").textContent, camera);
    assert.equal(page.getElementById("detail-file-name").closest(".detail-item").textContent.includes(size), false, "the size still floats under the path");
    closeAllApps();
  }
});

test("the Date Taken reads whole, as the photo stores it: no zone moves it, and a photo with none says so", async (t) => {
  // Midnight, the date the owner checks; 2:30 AM on the day the clocks go forward, which a Date would read as 3:30; a zone after the
  // seconds, as 106 of the owner's photos carry; and none, as 74 have.
  for (const [taken, shown] of [["2021:03:27 00:00:00", "3/27/21 12:00 AM"], ["2021:03:14 02:30:00", "3/14/21 2:30 AM"],
                                ["2019:11:02 17:45:09-07:00", "11/2/19 5:45 PM"], [null, "Unknown"]]) {
    const page = await openWith(t, { ...PHOTO, taken });
    assert.equal(page.getElementById("detail-date-taken").textContent, shown, `${taken}`);
    closeAllApps();
  }
});
