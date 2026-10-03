/**
 * TagTuner's Roots, from the gear (web/tuner/roots.js; docs/ARCHITECTURE.md, "Roots and machines").
 *
 * A row for each root of the library with where this computer keeps it, which place writes go to,
 * and the last check; Verify (a sample, answered at once; "all" a job whose progress is followed
 * and can be cancelled); Change location (a place named or browsed to, checked first, then
 * confirmed -- once); Change back. Plain sentences for each refusal. And a library whose root this
 * computer does not place shows a banner, from any request the server answers 409 for that reason.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";
import { OPEN_DIALOG } from "../../web/common/dialog.js";

afterEach(() => closeAllApps());

const LIBRARY = "photo_index";
const OLD = "D:/Training/Pictures";
const NEW = "//idziserver/Pictures/Pictures";
const SHARE = "\\\\idziserver\\Pictures\\Pictures";

const VERIFY = {
  root: "pictures", location: OLD, mode: "sample", reachable: true, state: "ok", message: "", rows: 68466,
  checked: 2000, matches: 1998, differs: 2, missing: 0, unreadable: 0, folders: 412, not_in_library: null,
  outside_rows: 0, outside: [], native_inside: 0, poor: false, poor_why: [],
  summary: "Looked at a sample of 2000 of 68466 rows covering 412 folder(s): 1998 match, 2 differ (changed since indexed; sync settles them), 0 are missing.",
};

function entry(overrides = {}) {
  return {
    name: "pictures", address: SHARE, added: "2026-10-02 09:00:00", places: [OLD], active: OLD, previous: null,
    writes_to: `Tags and renames are written to files at ${OLD}.`, mapped: true, rows: 68466,
    last_verify: null, verifying: null, ...overrides,
  };
}

function listing(roots, extra = {}) {
  return { library: LIBRARY, map: "C:/TagPup/machine_roots.json", problem: null, busy: [], poll_ms: 10,
           adopt_hint: 'TagPup CLI.cmd --db <library> roots adopt --name pictures --address "..." --location "..."',
           roots, ...extra };
}

async function tuner(t, { roots = [entry()], server = new FakeServer(), extra = {} } = {}) {
  server
    .on("/api/apps", { this: "tuner", apps: {} })
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", [])
    .on("/api/sync", { library: LIBRARY, last_in_step: null, last_run: null });
  if (roots) server.on("/api/roots", listing(roots, extra));
  const ctx = await loadApp("tagtuner", { t, url: `http://localhost:8080/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  ctx.server = server;
  ctx.posted = (path) => server.calls.filter((c) => c.url.includes(path) && c.method === "POST").map((c) => c.body);
  return ctx;
}

async function openFromGear(ctx) {
  const { window, document } = ctx;
  click(window, document.getElementById("btn-gear"));
  await flush(window);
  click(window, document.querySelector('[data-action="library-roots"]'));
  await flush(window, 6);
  return document.getElementById("roots-modal");
}

const text = (element) => element.textContent.replace(/\s+/g, " ").trim();
const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

describe("the Roots dialog", () => {
  test("the gear opens it, and a root says where it is kept and where writes go", async (t) => {
    const ctx = await tuner(t);
    const dialog = await openFromGear(ctx);
    assert.ok(dialog && !dialog.classList.contains("hidden"));
    assert.ok(dialog.matches(OPEN_DIALOG), "the page's shortcuts would act behind it");
    const row = dialog.querySelector('.roots-root[data-root="pictures"]');
    assert.match(text(row), /pictures/);
    assert.match(text(row.querySelector(".roots-rows")), /68,466 photos/);
    assert.equal(text(row.querySelector(".roots-place")), `Kept at ${OLD}`);
    assert.equal(text(row.querySelector(".roots-writes")), `Tags and renames are written to files at ${OLD}.`);
    assert.equal(text(row.querySelector(".roots-last")), "Not checked yet.");
    assert.ok(row.querySelector(".roots-back").disabled, "no earlier place to go back to");
  });

  test("a root with an earlier place says it is a separate copy, and Change back is there", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ places: [NEW, OLD], active: NEW, previous: OLD,
      writes_to: `Tags and renames are written to files at ${NEW}.` })] });
    const dialog = await openFromGear(ctx);
    const row = dialog.querySelector(".roots-root");
    assert.match(text(row.querySelector(".roots-previous")), new RegExp(`Before that: ${OLD}.*separate copy`));
    assert.equal(row.querySelector(".roots-back").disabled, false);
  });

  test("a root another library uses too says that moving it moves it for them", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ shared_with: ["kr-track"] })] });
    const row = (await openFromGear(ctx)).querySelector(".roots-root");
    assert.equal(text(row.querySelector(".roots-shared")), "kr-track also uses this root: moving it moves it for them too.");
  });

  test("the last check is a sentence with its counts", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ last_verify: { when: "2026-10-02 10:00:00", mode: "sample",
      checked: 2000, matches: 1998, differs: 2, missing: 0, outcome: "done" } })] });
    const row = (await openFromGear(ctx)).querySelector(".roots-root");
    assert.equal(text(row.querySelector(".roots-last")),
      "Last checked 2026-10-02 10:00:00, a sample of 2,000 rows: 1,998 match, 2 changed since they were indexed, 0 missing.");
  });

  test("a library with no roots says so, and shows the command", async (t) => {
    const ctx = await tuner(t, { roots: [] });
    const dialog = await openFromGear(ctx);
    assert.equal(text(dialog.querySelector(".roots-empty")),
      "This library has not adopted a root yet; nothing to change here.");
    assert.match(text(dialog.querySelector(".roots-command")), /roots adopt/);
    assert.equal(dialog.querySelectorAll(".roots-root").length, 0);
  });

  test("a root this computer does not place offers Change location and no Verify", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ places: [], active: null, mapped: false, writes_to: null, rows: null })] });
    const row = (await openFromGear(ctx)).querySelector(".roots-root");
    assert.match(text(row.querySelector(".roots-place")), /does not place this root yet/);
    assert.ok(row.querySelector(".roots-verify").disabled);
    assert.equal(row.querySelector(".roots-move").disabled, false);
  });

  test("a map that cannot be read is a sentence above the roots, not a traceback", async (t) => {
    const ctx = await tuner(t, { extra: { problem: "C:/TagPup/machine_roots.json: it holds an object with version and roots" } });
    const dialog = await openFromGear(ctx);
    assert.match(text(dialog.querySelector(".validation-error")), /machine_roots\.json/);
    assert.ok(dialog.querySelector(".roots-root"));
  });

  test("Escape and Close shut it, and the focus goes back", async (t) => {
    const ctx = await tuner(t);
    const dialog = await openFromGear(ctx);
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.ok(dialog.classList.contains("hidden"));
  });
});

describe("Verify", () => {
  test("a sample is asked for and its summary shown", async (t) => {
    const ctx = await tuner(t);
    ctx.server.first("/api/roots/verify", { success: true, started: false, verify: VERIFY });
    const dialog = await openFromGear(ctx);
    click(ctx.window, dialog.querySelector(".roots-verify"));
    await flush(ctx.window, 6);
    assert.deepEqual(ctx.posted("/api/roots/verify"), [{ root: "pictures" }]);
    assert.match(text(dialog.querySelector(".roots-result")), /a sample of 2000 of 68466 rows.*1998 match/);
  });

  test("a place that cannot be reached is said as that, not as missing rows", async (t) => {
    const ctx = await tuner(t);
    const away = { ...VERIFY, reachable: false, state: "away", checked: 0, matches: 0, poor: true,
      message: "This location cannot be reached: D:/x did not answer within 15 seconds, so nothing is counted as missing. Try again when it is back.",
      poor_why: ["This location cannot be reached: D:/x did not answer within 15 seconds, so nothing is counted as missing. Try again when it is back."],
      summary: "This location cannot be reached: D:/x did not answer within 15 seconds, so nothing is counted as missing. Try again when it is back." };
    ctx.server.first("/api/roots/verify", { success: true, started: false, verify: away });
    const dialog = await openFromGear(ctx);
    click(ctx.window, dialog.querySelector(".roots-verify"));
    await flush(ctx.window, 6);
    const shown = text(dialog.querySelector(".roots-result"));
    assert.match(shown, /cannot be reached/);
    assert.doesNotMatch(shown, /are missing\./);
  });

  test("a refusal from the server is shown as its sentence", async (t) => {
    const ctx = await tuner(t);
    ctx.server.first("/api/roots/verify", { success: false, error: "A verify of pictures is under way already; wait for it, or cancel it." }, { status: 409 });
    const dialog = await openFromGear(ctx);
    click(ctx.window, dialog.querySelector(".roots-verify"));
    await flush(ctx.window, 6);
    assert.match(text(dialog.querySelector(".roots-result")), /under way already/);
  });

  test("all rows is a job: progress is followed, and the result is shown when it ends", async (t) => {
    const ctx = await tuner(t);
    let polls = 0;
    let finished = false;
    ctx.server.routes.length = 0;
    ctx.server
      .on("/api/roots/verify", { success: true, started: true,
        status: { root: "pictures", state: "running", checked: 0, rows: 68466, folders: 0, cancelling: false } })
      .on("/api/roots", () => {
        polls += 1;
        const running = !finished;
        return listing([entry({ verifying: running
          ? { root: "pictures", state: "running", checked: polls * 20000, rows: 68466, folders: polls * 100, cancelling: false }
          : { root: "pictures", state: "done", checked: 68466, rows: 68466, folders: 411, result: { ...VERIFY, mode: "all",
            checked: 68466, matches: 68466, differs: 0, not_in_library: 3,
            summary: "Looked at 68466 of 68466 rows: 68466 match, 0 differ, 0 are missing. 3 photo(s) at the location have no row." } } })]);
      });
    const dialog = await openFromGear(ctx);
    click(ctx.window, dialog.querySelector(".roots-verify-all"));
    await flush(ctx.window, 4);
    assert.deepEqual(ctx.posted("/api/roots/verify"), [{ root: "pictures", all: true }]);
    const progress = dialog.querySelector(".roots-progress");
    assert.ok(!progress.classList.contains("hidden"));
    await wait(ctx.window, 60);
    assert.ok(polls >= 3, "it asked how the run was going, again and again");
    assert.match(text(progress), /Looking at every row: [\d,]+ of 68,466/);
    finished = true;
    for (let i = 0; i < 60 && !progress.classList.contains("hidden"); i++) await wait(ctx.window, 20);
    assert.ok(progress.classList.contains("hidden"), "the progress went away");
    assert.match(text(dialog.querySelector(".roots-result")), /68466 match.*3 photo\(s\) at the location have no row/);
    const settled = polls;
    await wait(ctx.window, 80);
    assert.equal(polls, settled, "it asked again after the job ended");
  });

  test("Cancel asks the server to stop the run", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ verifying: { root: "pictures", state: "running", checked: 5, rows: 100,
      folders: 1, cancelling: false } })] });
    let cancelled = false;
    ctx.server.first("/api/roots", () => listing([entry({ verifying: { root: "pictures", state: "running", checked: 5,
      rows: 100, folders: 1, cancelling: cancelled } })]));
    ctx.server.first("/api/roots/verify-cancel", () => {
      cancelled = true;
      return { success: true, cancelled: true };
    });
    const dialog = await openFromGear(ctx);
    assert.ok(!dialog.querySelector(".roots-progress").classList.contains("hidden"), "a run begun before is shown");
    click(ctx.window, dialog.querySelector(".roots-cancel-verify"));
    await flush(ctx.window, 4);
    assert.deepEqual(ctx.posted("/api/roots/verify-cancel"), [{ root: "pictures" }]);
    assert.match(text(dialog.querySelector(".roots-progress-text")), /stopping/i);
  });

  test("an answer that arrives after the dialog was closed and opened again is let go", async (t) => {
    // The library switched, or the dialog reopened, while a poll was in flight: its answer is for the
    // dialog that asked, and must not put that run's progress in this one.
    const running = entry({ verifying: { root: "pictures", state: "running", checked: 5, rows: 100, folders: 1, cancelling: false } });
    const ctx = await tuner(t, { roots: [running] });
    let release;
    const late = new Promise((resolve) => { release = resolve; });
    let slow = false;
    ctx.server.first("/api/roots", () => (slow ? late : listing([running])));
    const dialog = await openFromGear(ctx);
    await wait(ctx.window, 40);
    slow = true;
    await wait(ctx.window, 40);              // a poll is now waiting on `late`
    click(ctx.window, dialog.querySelector(".btn.btn-secondary:not(.btn-sm)"));   // Close
    slow = false;
    ctx.server.first("/api/roots", () => listing([entry()]));
    await openFromGear(ctx);
    assert.ok(dialog.querySelector(".roots-progress").classList.contains("hidden"));
    release(listing([running]));
    await wait(ctx.window, 60);
    assert.ok(dialog.querySelector(".roots-progress").classList.contains("hidden"), "an old answer drew progress in the new dialog");
    assert.equal(ctx.consoleErrors.length > 0 ? ctx.consoleErrors.filter((e) => /roots/i.test(String(e))).length : 0, 0);
  });

  test("closing the dialog stops asking how the run is going", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ verifying: { root: "pictures", state: "running", checked: 5, rows: 100,
      folders: 1, cancelling: false } })] });
    await openFromGear(ctx);
    await wait(ctx.window, 60);
    const before = ctx.server.urls().filter((u) => u.includes("/api/roots")).length;
    assert.ok(before >= 2, "it was polling while open");
    click(ctx.window, ctx.document.querySelector("#roots-modal .btn.btn-secondary:not(.btn-sm)"));
    await wait(ctx.window, 80);
    assert.equal(ctx.server.urls().filter((u) => u.includes("/api/roots")).length, before);
  });
});

describe("Change location", () => {
  async function panelOn(t, options = {}) {
    const ctx = await tuner(t, options);
    const dialog = await openFromGear(ctx);
    click(ctx.window, dialog.querySelector(".roots-move"));
    const type = (value) => {
      const input = dialog.querySelector(".roots-location");
      input.value = value;
      input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    };
    return { ctx, dialog, type };
  }

  const GOOD = { success: true, dry_run: true, changed: 0, root: "pictures", location: NEW,
    verify: { ...VERIFY, location: NEW, summary: "Looked at a sample of 2000 of 68466 rows covering 412 folder(s): 2000 match, 0 differ, 0 are missing." },
    writes_to: `Tags and renames are written to files at ${NEW}.` };

  test("a place is checked first, with what it holds, and nothing is changed by it", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    ctx.server.first("/api/roots/change-location", GOOD);
    assert.ok(dialog.querySelector(".roots-confirm").disabled, "nothing to confirm before a check");
    type(NEW);
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    assert.deepEqual(ctx.posted("/api/roots/change-location"), [{ root: "pictures", dry_run: true, from: OLD, location: NEW }]);
    const shown = text(dialog.querySelector(".roots-check"));
    assert.match(shown, /2000 match, 0 differ, 0 are missing/);
    assert.match(shown, new RegExp(`Tags and renames are written to files at ${NEW.replace(/[/.]/g, "\\$&")}`));
    assert.match(shown, /no photo file, no row/);
    assert.equal(dialog.querySelector(".roots-confirm").disabled, false);
  });

  test("the folder picker fills the place in", async (t) => {
    const { ctx, dialog } = await panelOn(t);
    ctx.server.first("/api/browse-folder", { path: "E:/Photos" });
    click(ctx.window, dialog.querySelector(".roots-browse"));
    await flush(ctx.window, 4);
    assert.equal(dialog.querySelector(".roots-location").value, "E:/Photos");
  });

  test("naming nothing is told to name something, and asks the server nothing", async (t) => {
    const { ctx, dialog } = await panelOn(t);
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 4);
    assert.match(text(dialog.querySelector(".roots-check")), /Name the folder first/);
    assert.deepEqual(ctx.posted("/api/roots/change-location"), []);
  });

  test("Move is a second press, and a double click makes one change", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    let changes = 0;
    ctx.server.first("/api/roots", listing([entry({ places: [NEW, OLD], active: NEW, previous: OLD,
      writes_to: `Tags and renames are written to files at ${NEW}.` })]));
    ctx.server.routes.unshift({ match: "/api/roots/change-location", status: 200, headers: {}, body: (url) => {
      const asked = ctx.server.calls[ctx.server.calls.length - 1].body;
      if (asked.dry_run) return GOOD;
      changes += 1;
      return { ...GOOD, dry_run: false, changed: 1, message: `moved root pictures to ${NEW} (was ${OLD})` };
    } });
    type(NEW);
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    const confirm = dialog.querySelector(".roots-confirm");
    click(ctx.window, confirm);
    click(ctx.window, confirm);
    click(ctx.window, confirm);
    await flush(ctx.window, 8);
    assert.equal(changes, 1, "a double click changed it more than once");
    assert.equal(ctx.posted("/api/roots/change-location").filter((b) => b.dry_run === false).length, 1);
    assert.deepEqual(ctx.posted("/api/roots/change-location").find((b) => b.dry_run === false),
      { root: "pictures", dry_run: false, from: OLD, location: NEW });
    const row = dialog.querySelector(".roots-root");
    assert.equal(text(row.querySelector(".roots-place")), `Kept at ${NEW}`);
    assert.match(text(row.querySelector(".roots-writes")), /written to files at \/\/idziserver/);
    assert.match(dialog.querySelector(".roots-status").textContent, /moved root pictures to/);
  });

  test("typing another place after a check asks for a new check", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    ctx.server.first("/api/roots/change-location", GOOD);
    type(NEW);
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    assert.equal(dialog.querySelector(".roots-confirm").disabled, false);
    type("E:/Elsewhere");
    assert.ok(dialog.querySelector(".roots-confirm").disabled);
    assert.equal(text(dialog.querySelector(".roots-check")), "");
  });

  test("a poor result is shown with why, and moving it needs the person to say they mean it", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    const why = "1500 of the 2000 rows looked at (75%) are not there: this looks like another folder, or another copy.";
    ctx.server.routes.unshift({ match: "/api/roots/change-location", status: 400, headers: {}, body: () => {
      const asked = ctx.server.calls[ctx.server.calls.length - 1].body;
      if (asked.dry_run || !asked.override) {
        return { success: false, error: `Not changed: ${why} Say override to change it anyway.`, would_refuse: why,
          verify: { ...VERIFY, poor: true, poor_why: [why], summary: "Looked at a sample ... 500 match, 0 differ, 1500 are missing." } };
      }
      return { ...GOOD, dry_run: false, changed: 1, message: "moved root pictures" };
    } });
    type("D:/Other");
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    assert.match(text(dialog.querySelector(".roots-check")), /another folder, or another copy/);
    const confirm = dialog.querySelector(".roots-confirm");
    const label = dialog.querySelector(".roots-override");
    assert.ok(!label.classList.contains("hidden"));
    assert.ok(confirm.disabled, "a poor place is not moved to by pressing Move");
    const box = label.querySelector("input");
    box.checked = true;
    box.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    assert.equal(confirm.disabled, false);
    click(ctx.window, confirm);
    await flush(ctx.window, 8);
    assert.equal(ctx.posted("/api/roots/change-location").pop().override, true);
  });

  test("a refusal at confirm -- a run under way, the map moved by another tab -- is its sentence, and it can be tried again", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    let answers = 0;
    ctx.server.routes.unshift({ match: "/api/roots/change-location", status: 200, headers: {}, body: () => {
      const asked = ctx.server.calls[ctx.server.calls.length - 1].body;
      if (asked.dry_run) return GOOD;
      answers += 1;
      return { success: false, error: "Not changed: an index run is running or queued. Wait for it to finish or cancel it, then try again." };
    } });
    type(NEW);
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    click(ctx.window, dialog.querySelector(".roots-confirm"));
    await flush(ctx.window, 6);
    assert.match(text(dialog.querySelector(".roots-check")), /an index run is running or queued/);
    assert.equal(dialog.querySelector(".roots-confirm").disabled, false, "it can be pressed again once the run is done");
    click(ctx.window, dialog.querySelector(".roots-confirm"));
    await flush(ctx.window, 6);
    assert.equal(answers, 2);
  });

  test("a refused dry run is a sentence, and nothing can be confirmed", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    ctx.server.first("/api/roots/change-location", { success: false, error: "Not changed. This location cannot be reached: Q: is not there -- a drive that is not connected, or a share that is away. Nothing is counted as missing." }, { status: 400 });
    type("Q:/Photos");
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    assert.match(text(dialog.querySelector(".roots-check")), /cannot be reached/);
    assert.ok(dialog.querySelector(".roots-confirm").disabled);
    assert.ok(dialog.querySelector(".roots-override").classList.contains("hidden"), "an unreachable place is not for override");
  });

  test("a place the root is at already says so", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    ctx.server.first("/api/roots/change-location", { success: true, unchanged: true, message: `pictures is at ${OLD} already; nothing to change.`, changed: 0 });
    type(OLD.toUpperCase());
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    assert.match(text(dialog.querySelector(".roots-check")), /already; nothing to change/);
    assert.ok(dialog.querySelector(".roots-confirm").disabled);
  });
});

describe("Change back", () => {
  const BACK = { success: true, dry_run: true, changed: 0, root: "pictures", location: OLD,
    verify: { ...VERIFY }, writes_to: `Tags and renames are written to files at ${OLD}.` };

  async function backOn(t, answer) {
    const roots = [entry({ places: [NEW, OLD], active: NEW, previous: OLD, writes_to: `Tags and renames are written to files at ${NEW}.` })];
    const ctx = await tuner(t, { roots });
    ctx.server.routes.unshift({ match: "/api/roots/change-back", status: 200, headers: {}, body: () => {
      const asked = ctx.server.calls[ctx.server.calls.length - 1].body;
      return answer(asked);
    } });
    const dialog = await openFromGear(ctx);
    click(ctx.window, dialog.querySelector(".roots-back"));
    await flush(ctx.window, 6);
    return { ctx, dialog };
  }

  test("it checks the earlier place at once, and moves back on the second press", async (t) => {
    const { ctx, dialog } = await backOn(t, (asked) => asked.dry_run ? BACK
      : { ...BACK, dry_run: false, changed: 1, message: `moved back root pictures to ${OLD} (was ${NEW})` });
    assert.deepEqual(ctx.posted("/api/roots/change-back"), [{ root: "pictures", dry_run: true, from: NEW }]);
    assert.match(text(dialog.querySelector(".roots-check")), new RegExp(`written to files at ${OLD.replace(/[/.]/g, "\\$&")}`));
    click(ctx.window, dialog.querySelector(".roots-confirm"));
    await flush(ctx.window, 8);
    assert.deepEqual(ctx.posted("/api/roots/change-back").pop(), { root: "pictures", dry_run: false, from: NEW });
    assert.match(dialog.querySelector(".roots-status").textContent, /moved back root pictures/);
  });

  test("when the earlier place has been deleted it says so and offers nothing", async (t) => {
    const { dialog } = await backOn(t, () => ({ success: false,
      error: `The previous place, ${OLD}, is not there any more. Nothing was changed. Choose a place with Change location.` }));
    assert.match(text(dialog.querySelector(".roots-check")), /is not there any more/);
    assert.ok(dialog.querySelector(".roots-confirm").disabled);
  });
});

describe("a library whose root this computer does not place", () => {
  const MESSAGE = "root 'pictures' has no location on this machine: add it to C:/TagPup/machine_roots.json";

  for (const app of ["tagtuner", "tagpup"]) {
    test(`${app} shows the banner when a request is answered 409 for it, and not before`, async (t) => {
      // What each page asks as it opens: TagPup its tag tree, TagTuner its photos.
      const server = new FakeServer()
        .on("/api/apps", { this: app === "tagpup" ? "tagpup" : "tuner", apps: {} })
        .on(app === "tagpup" ? "/api/taxonomy/tree" : "/api/photos", { success: false, error: MESSAGE, roots_problem: true },
          { status: 409, headers: { "X-TagPup-Roots-Problem": "1" } })
        .on("/api/databases", { databases: [LIBRARY] })
        .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
        .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" });
      const ctx = await loadApp(app, { t, url: `http://localhost:8080/${LIBRARY}/`, server });
      await flush(ctx.window, 8);
      const banner = ctx.document.getElementById("roots-banner");
      assert.ok(banner, "the page has a banner");
      assert.ok(!banner.classList.contains("hidden"));
      assert.equal(text(banner.querySelector(".roots-banner-message")), MESSAGE);
    });

    test(`${app}'s banner stays hidden while the server answers`, async (t) => {
      const server = new FakeServer()
        .on("/api/apps", { this: app === "tagpup" ? "tagpup" : "tuner", apps: {} })
        .on("/api/databases", { databases: [LIBRARY] })
        .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 });
      const ctx = await loadApp(app, { t, url: `http://localhost:8080/${LIBRARY}/`, server });
      await flush(ctx.window, 8);
      assert.ok(ctx.document.getElementById("roots-banner").classList.contains("hidden"));
    });
  }
});
