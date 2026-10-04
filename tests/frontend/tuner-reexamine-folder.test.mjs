/**
 * Re-examine this folder, in TagTuner's Folder Matches list (web/tuner/reexamine.js).
 *
 * The folder's button named faces at once, with no word of how many or whose. Now it
 * asks the server for a dry run, puts what it found to the owner -- how many faces, in
 * how many photos, and whose -- and names them only when they agree. The apply decides
 * again, so when it named a different number the page says so. A second press while
 * one is under way asks nothing.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, flush } from "./harness.mjs";

const MEET = "D:\\Meet";
const FINISH = "D:\\Meet\\Finish";
const PHOTOS = [
  { path: "D:\\Meet\\120.jpg", filename: "120.jpg", unmatched_count: 2, matched_count: 0,
    mtime: 3, year: "2026", folder: MEET },
  { path: "D:\\Meet\\121.jpg", filename: "121.jpg", unmatched_count: 1, matched_count: 0,
    mtime: 2, year: "2026", folder: MEET },
  { path: "D:\\Meet\\Finish\\7.jpg", filename: "7.jpg", unmatched_count: 1, matched_count: 0,
    mtime: 1, year: "2025", folder: FINISH },
];

const PLAN = { success: true, dry_run: true, matched_count: 0, faces: 3, photos: 3, renamed: 0,
               people: { "Rowan Thackeray": 2, "Wren Halloway": 1 } };
const DONE = { success: true, dry_run: false, matched_count: 3, faces: 3, photos: 3, renamed: 0,
               people: { "Rowan Thackeray": 2, "Wren Halloway": 1 },
               photos_named: PHOTOS.map((p) => p.path), remaining_counts: { "D:\\Meet\\120.jpg": 1 } };

const posts = (s) => s.calls.filter((c) => c.url.includes("/api/folder/automatch"));

function server({ plan = PLAN, done = DONE, hold = null } = {}) {
  const s = new FakeServer()
    .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    // A list of its own each time: the page keeps the records it is sent and counts on them.
    .on("/api/photos", () => PHOTOS.map((p) => ({ ...p })));
  s.on("/api/folder/automatch", () => {
    const body = s.lastBody("/api/folder/automatch");
    const answer = body.dry_run ? plan : done;
    return hold ? hold.then(() => answer) : answer;
  });
  return s;
}

async function open(t, options) {
  const ctx = await loadApp("tagtuner", {
    t, url: "http://localhost:8080/kr-track/?mode=folder-match", server: server(options),
  });
  ctx.questions = [];
  ctx.alerts = [];
  ctx.answer = true;
  ctx.window.confirm = (text) => { ctx.questions.push(text); return ctx.answer; };
  ctx.window.alert = (text) => { ctx.alerts.push(text); };
  await flush(ctx.window);
  return ctx;
}

function buttonOf(document, folder) {
  const header = [...document.querySelectorAll(".folder-header")]
    .find((h) => h.querySelector(".folder-title").title === folder);
  return { header, button: header.querySelector(".btn-folder-automatch") };
}

describe("Re-examine this folder", () => {
  afterEach(() => closeAllApps());

  test("asks with a dry run's counts, then names, and the list shows it", async (t) => {
    const ctx = await open(t);
    const { button } = buttonOf(ctx.document, MEET);
    assert.match(button.textContent, /Re-examine/);
    button.click();
    await flush(ctx.window, 6);

    const sent = posts(ctx.server);
    assert.equal(sent.length, 2, "a dry run, then the apply");
    assert.equal(sent[0].body.dry_run, true);
    assert.equal(sent[0].body.folder_path, MEET);
    assert.equal(sent[1].body.dry_run, false);
    assert.equal(ctx.questions.length, 1);
    assert.match(ctx.questions[0], /^Name 3 faces in 3 photos/);
    assert.match(ctx.questions[0], /Rowan Thackeray 2, Wren Halloway 1/);
    assert.deepEqual(ctx.alerts, [], "the apply did what it was asked, so nothing more is said");

    const byPath = Object.fromEntries([...ctx.document.querySelectorAll(".folder-photo-item")]
      .map((li) => [li.photo.path, li]));
    assert.equal(byPath["D:\\Meet\\120.jpg"].photo.badgeEl.textContent, "1 unmatched");
    assert.equal(byPath["D:\\Meet\\120.jpg"].photo.badgeMatchedEl.textContent, "1 matched");
    assert.equal(byPath["D:\\Meet\\121.jpg"].style.display, "none", "a finished photo leaves the list");
    // The folder under it is a group of its own, under another year: it follows too.
    assert.equal(byPath["D:\\Meet\\Finish\\7.jpg"].style.display, "none");
    assert.equal(buttonOf(ctx.document, MEET).header.querySelector(".folder-count").textContent, " (1)");
    assert.equal(buttonOf(ctx.document, FINISH).header.querySelector(".folder-count").textContent, " (0)");
    assert.equal(button.disabled, false);
    assert.match(button.textContent, /Re-examine/);
  });

  test("answering no names nobody", async (t) => {
    const ctx = await open(t);
    ctx.answer = false;
    buttonOf(ctx.document, MEET).button.click();
    await flush(ctx.window, 6);
    assert.equal(posts(ctx.server).length, 1, "only the dry run was sent");
    assert.equal(ctx.document.querySelector(".folder-photo-item").photo.badgeEl.textContent, "2 unmatched");
  });

  test("with nothing to name it says so and asks nothing", async (t) => {
    const ctx = await open(t, { plan: { ...PLAN, faces: 0, photos: 0, people: {} } });
    buttonOf(ctx.document, MEET).button.click();
    await flush(ctx.window, 6);
    assert.equal(posts(ctx.server).length, 1);
    assert.deepEqual(ctx.questions, []);
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /No face/);
  });

  test("when the apply named a different number, the page says so", async (t) => {
    const ctx = await open(t, { done: { ...DONE, matched_count: 2, faces: 2, renamed: 1,
                                        people: { "Rowan Thackeray": 2 } } });
    buttonOf(ctx.document, MEET).button.click();
    await flush(ctx.window, 6);
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /Named 2 faces in 3 photos; it asked about 3 faces/);
    assert.match(ctx.alerts[0], /Wren Halloway 1 to 0/);
    assert.match(ctx.alerts[0], /1 face was left because their person was renamed/);
    assert.doesNotMatch(ctx.alerts[0], /since it asked/, "the renamed share was the whole difference");
  });

  test("the same number of faces, but other people's, is said too", async (t) => {
    const ctx = await open(t, { done: { ...DONE, people: { "Rowan Thackeray": 1, "Wren Halloway": 1,
                                                           "Kit Morrow": 1 } } });
    buttonOf(ctx.document, MEET).button.click();
    await flush(ctx.window, 6);
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /Kit Morrow 0 to 1, Rowan Thackeray 2 to 1/);
    assert.match(ctx.alerts[0], /since it asked/);
  });

  test("a renamed share takes only its own part of the difference", async (t) => {
    const ctx = await open(t, { done: { ...DONE, matched_count: 1, faces: 1, renamed: 1,
                                        people: { "Wren Halloway": 1 } } });
    buttonOf(ctx.document, MEET).button.click();
    await flush(ctx.window, 6);
    assert.match(ctx.alerts[0], /1 face was left because their person was renamed/);
    assert.match(ctx.alerts[0], /since it asked/, "two faces fewer, one of them renamed: the other is said");
  });

  test("a second press while the first is answered sends nothing", async (t) => {
    let release;
    const hold = new Promise((r) => { release = r; });
    const ctx = await open(t, { hold });
    const meet = buttonOf(ctx.document, MEET).button;
    const finish = buttonOf(ctx.document, FINISH).button;
    meet.click();
    meet.click();
    finish.click();
    await flush(ctx.window, 3);
    assert.equal(posts(ctx.server).length, 1, "a second dry run was asked for");
    release();
    await flush(ctx.window, 6);
    assert.equal(posts(ctx.server).length, 2, "one dry run and one apply");
    assert.equal(ctx.questions.length, 1);
  });

  test("a refusal from the server is shown, and the button comes back", async (t) => {
    const s = server();
    s.first("/api/folder/automatch",
      { success: false, error: "Server is currently clustering faces. Please try again later." }, { status: 409 });
    const ctx = await loadApp("tagtuner", { t, url: "http://localhost:8080/kr-track/?mode=folder-match", server: s });
    const alerts = [];
    ctx.window.alert = (text) => alerts.push(text);
    ctx.window.confirm = () => true;
    await flush(ctx.window);
    const { button } = buttonOf(ctx.document, MEET);
    button.click();
    await flush(ctx.window, 6);
    assert.equal(alerts.length, 1);
    assert.match(alerts[0], /clustering/);
    assert.equal(button.disabled, false);
  });
});
