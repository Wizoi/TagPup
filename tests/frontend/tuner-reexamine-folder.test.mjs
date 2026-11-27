/**
 * Re-examine this folder, in TagTuner's Folder Matches list (web/tuner/reexamine.js).
 *
 * The folder's button named faces at once, with no word of how many or whose. Now it
 * asks the server for a dry run, puts what it found to the owner -- how many faces, in
 * how many photos, and whose -- and names them only when they agree. The apply decides
 * again, so when it named a different number the page says so. A second press while
 * one is under way asks nothing.
 *
 * An index whose status offers Re-examine (`reexamine`: some new faces have a likely name, or
 * their matching failed) keeps its sentence on TagTuner's index line, its Re-examine a button
 * that runs Re-examine on that folder, until the next index -- the same folder's rerun too
 * (#3422); TagPup's status line links to TagTuner opened onto it (`?reexamine=<folder>`).
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
    t, url: "http://localhost:8080/kr-track/?mode=folder-match", server: options && options.server || server(options),
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

// The index's closing sentences as tagpup/services/indexing.py says them: some new faces with a likely name, and none.
const LIKELY = "Folder indexed. 3 of the 5 new faces have a likely name: open Re-examine on their folder to confirm them.";
const NONE_LIKELY = "Folder indexed. None of the 4 new faces looks enough like anyone named to be proposed: they wait in Identify Faces.";
const FAILED = "Folder indexed. The photos are indexed, but matching their faces failed: RuntimeError: out of memory. Open "
  + "Re-examine on their folder, or run cluster-faces in the TagPup CLI.";
const AGAIN = "Indexing again: files arrived in it while it was being indexed...";
const photoReads = (s) => s.urls().filter((u) => u.includes("/api/photos")).length;

/** A server whose index of a batch, named by its first folder MEET (#3417), is under way when the page opens on
 * FINISH's entry and has ended with `message` by the first status read, offering Re-examine of `reexamine` (or not). */
function endingIndex(message, reexamine = null) {
  let asked = 0;
  return server()
    .first("/api/folder/index-active", () => (asked++ === 0
      ? { active: [{ folder: FINISH, name: "Finish", percent: 90, message: "Matching" }], queued: [], busy: true, remaining: 1 }
      : { active: [], queued: [], busy: false, remaining: 0 }))
    .first("/api/folder/index-status", { status: "completed", percent: 100, folder: MEET, message,
      ...(reexamine ? { reexamine } : {}) });
}

const settle = (window, ms) => new Promise((r) => window.setTimeout(r, ms));

describe("Re-examine from an index's closing sentence", () => {
  afterEach(() => closeAllApps());

  test("TagTuner's index line keeps the sentence, and its Re-examine runs on the index's folder", async (t) => {
    const ctx = await open(t, { server: endingIndex(LIKELY, MEET) });
    await settle(ctx.window, 700);
    const line = ctx.document.getElementById("index-progress-text");
    assert.ok(!ctx.document.getElementById("index-progress-container").classList.contains("hidden"),
      "the closing sentence went with the bar");
    assert.equal(line.textContent, LIKELY);
    const button = line.querySelector(".btn-index-reexamine");
    assert.ok(button, "the sentence's Re-examine is not something to press");
    const reads = photoReads(ctx.server);
    button.click();
    await flush(ctx.window, 8);
    const sent = posts(ctx.server);
    assert.deepEqual(sent.map((c) => [c.body.folder_path, c.body.dry_run]), [[MEET, true], [MEET, false]],
      "not the batch's folder, its first (#3417)");
    assert.equal(ctx.questions.length, 1);
    assert.ok(photoReads(ctx.server) > reads, "the list was not read again after faces were named");
  });

  test("while the index runs its sentence is words: its bake still writes the folder's files", async (t) => {
    const s = server()
      .first("/api/folder/index-active", { active: [{ folder: MEET, name: "Meet", percent: 45, message: LIKELY }],
        queued: [], busy: true, remaining: 1 })
      .first("/api/folder/index-status", { status: "running", percent: 45, folder: MEET, message: LIKELY });
    const ctx = await open(t, { server: s });
    await settle(ctx.window, 50);
    assert.equal(ctx.document.getElementById("index-progress-text").textContent, LIKELY);
    assert.equal(ctx.document.querySelector(".btn-index-reexamine"), null);
  });

  test("no new face with a likely name: nothing to press, and the bar goes as it did", async (t) => {
    const ctx = await open(t, { server: endingIndex(NONE_LIKELY) });
    await settle(ctx.window, 700);
    assert.ok(ctx.document.getElementById("index-progress-container").classList.contains("hidden"));
    assert.equal(ctx.document.querySelector(".btn-index-reexamine"), null);
    assert.equal(posts(ctx.server).length, 0);
  });

  test("the matching of the new faces failed: its Re-examine is a button too (#3420)", async (t) => {
    const ctx = await open(t, { server: endingIndex(FAILED, MEET) });
    await settle(ctx.window, 700);
    const line = ctx.document.getElementById("index-progress-text");
    assert.equal(line.textContent, FAILED);
    assert.ok(line.querySelector(".btn-index-reexamine"), "the failure's Re-examine is not something to press");
  });

  test("the folder indexed again as it ends (files arrived meanwhile): the ended sentence goes, the rerun is followed (#3422)",
    async (t) => {
      // The server's queue (tagpup/jobs/indexing.py _queue_what_was_missed): MEET's index ends offering Re-examine, and
      // MEET is at once indexed again; then that rerun ends, its own new faces with no likely name.
      let stage = "first";
      const running = (message, percent) => ({ active: [{ folder: MEET, name: "Meet", percent, message }], queued: [],
        busy: true, remaining: 1 });
      const s = server()
        .first("/api/folder/index-active", () => (stage === "first" ? running("Matching", 45)
          : stage === "again" ? running(AGAIN, 5) : { active: [], queued: [], busy: false, remaining: 0 }))
        .first("/api/folder/index-status", () => {
          if (stage === "first") {
            stage = "again";
            return { status: "completed", percent: 100, folder: MEET, message: LIKELY, reexamine: MEET };
          }
          return stage === "again" ? { status: "running", percent: 5, folder: MEET, message: AGAIN }
            : { status: "completed", percent: 100, folder: MEET, message: NONE_LIKELY };
        });
      const ctx = await open(t, { server: s });
      await settle(ctx.window, 700);
      const line = ctx.document.getElementById("index-progress-text");
      assert.equal(line.textContent, AGAIN, "the ended index's sentence stayed while the folder is indexed again");
      assert.equal(ctx.document.querySelector(".btn-index-reexamine"), null, "a button left live during the rerun");
      assert.equal(ctx.document.getElementById("index-progress-bar").style.width, "5%");
      stage = "done";
      await settle(ctx.window, 1600);
      assert.ok(ctx.document.getElementById("index-progress-container").classList.contains("hidden"),
        "the rerun's end, offering nothing, left the bar");
      assert.equal(ctx.document.querySelector(".btn-index-reexamine"), null);
    });

  test("the words without the status's field: nothing to press, the field is the offer (#3423)", async (t) => {
    const ctx = await open(t, { server: endingIndex(LIKELY) });
    await settle(ctx.window, 700);
    assert.ok(ctx.document.getElementById("index-progress-container").classList.contains("hidden"));
    assert.equal(ctx.document.querySelector(".btn-index-reexamine"), null);
  });

  function openedOnto(t, folder, answer) {
    const ctx = { questions: [], alerts: [] };
    return loadApp("tagtuner", {
      t, url: `http://localhost:8080/kr-track/?mode=folder-match&reexamine=${encodeURIComponent(folder)}`, server: server(),
      before: (window) => {
        window.confirm = (text) => { ctx.questions.push(text); return answer; };
        window.alert = (text) => { ctx.alerts.push(text); };
      },
    }).then(async (app) => {
      await flush(app.window, 8);
      return Object.assign(app, ctx);
    });
  }

  test("TagTuner opened from TagPup's link asks about that folder, names on Yes, reads the list again", async (t) => {
    const ctx = await openedOnto(t, MEET, true);
    const sent = posts(ctx.server);
    assert.deepEqual(sent.map((c) => [c.body.folder_path, c.body.dry_run]), [[MEET, true], [MEET, false]]);
    assert.match(ctx.questions[0], /^Name 3 faces in 3 photos/);
    assert.equal(photoReads(ctx.server), 2, "the list was not read again after faces were named");
    assert.equal(ctx.window.location.search, "?mode=folder-match", "a reload would ask again");
  });

  test("TagTuner opened from the link and the question declined: nothing is written", async (t) => {
    const ctx = await openedOnto(t, MEET, false);
    assert.deepEqual(posts(ctx.server).map((c) => c.body.dry_run), [true]);
    assert.equal(ctx.questions.length, 1);
    assert.equal(photoReads(ctx.server), 1);
  });
});

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

  test("an excluded face opened in the strip is neither unmatched nor, after Re-examine, matched", async (t) => {
    // docs/findings.md, #655: the strip counted it as unmatched, and Re-examine's
    // arithmetic then moved it into the matched badge.
    const ctx = await open(t);
    // The photo as the server has it: one of its two waiting faces named once the apply is in.
    const applied = () => posts(ctx.server).some((c) => c.body && c.body.dry_run === false);
    ctx.server.first("/api/photo-details", () => ({
      path: "D:\\Meet\\120.jpg", filename: "120.jpg", people: [], tags: [], caption: null, year: "2026",
      faces: [
        { id: 1, box: [0, 0, 10, 10], name: applied() ? "Rowan Thackeray" : null, max_similarity: 0.9,
          excluded: false },
        { id: 2, box: [0, 0, 10, 10], name: null, max_similarity: 0.9, excluded: false },
        { id: 3, box: [0, 0, 10, 10], name: null, max_similarity: 0.1, excluded: true },
      ],
    }));
    const li = [...ctx.document.querySelectorAll(".folder-photo-item")].find((x) => x.photo.path === "D:\\Meet\\120.jpg");
    li.click();
    await flush(ctx.window, 6);
    assert.equal(li.photo.badgeEl.textContent, "2 unmatched", "the excluded face was counted as unmatched");
    buttonOf(ctx.document, MEET).button.click();
    await flush(ctx.window, 8);
    assert.equal(li.photo.badgeEl.textContent, "1 unmatched");
    assert.equal(li.photo.badgeMatchedEl.textContent, "1 matched", "the excluded face was counted as matched");
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

  test("faces the dry run already left for a rename are not the reason for a difference", async (t) => {
    // docs/findings.md, #657: the total was taken for what changed since the question.
    const ctx = await open(t, { plan: { ...PLAN, renamed: 1 },
                                done: { ...DONE, matched_count: 2, faces: 2, renamed: 1,
                                        people: { "Rowan Thackeray": 2 } } });
    buttonOf(ctx.document, MEET).button.click();
    await flush(ctx.window, 6);
    assert.equal(ctx.alerts.length, 1);
    assert.doesNotMatch(ctx.alerts[0], /renamed meanwhile/);
    assert.match(ctx.alerts[0], /since it asked/);
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
      { success: false, error: "Names are being given from tags; this waits until it finishes." }, { status: 409 });
    const ctx = await loadApp("tagtuner", { t, url: "http://localhost:8080/kr-track/?mode=folder-match", server: s });
    const alerts = [];
    ctx.window.alert = (text) => alerts.push(text);
    ctx.window.confirm = () => true;
    await flush(ctx.window);
    const { button } = buttonOf(ctx.document, MEET);
    button.click();
    await flush(ctx.window, 6);
    assert.equal(alerts.length, 1);
    assert.match(alerts[0], /Names are being given from tags/);
    assert.equal(button.disabled, false);
  });
});
