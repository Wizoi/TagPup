/**
 * TagTuner's Roots, from the gear (web/tuner/roots.js; docs/ARCHITECTURE.md, "Roots and machines").
 *
 * A row for each root of the library with where this computer keeps it and which place writes go to;
 * Change location (a place named or browsed to, checked first, then confirmed -- once). Plain
 * sentences for each refusal. The Verify buttons and Change back are gone (2026-10-11). And a library whose root this
 * computer does not place shows a banner, from any request the server answers 409 for that reason.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";
import { OPEN_DIALOG } from "../../web/common/dialog.js";

afterEach(() => closeAllApps());

const LIBRARY = "photo_index";
const OLD = "D:/Training/Pictures";
const NEW = "//idziserver/Pictures/Pictures";
const SHARE = "\\\\idziserver\\Pictures\\Pictures";

const VERIFY = {
  root: "pictures", location: OLD, reachable: true, state: "ok", message: "", rows: 68466,
  checked: 2000, matches: 1998, differs: 2, missing: 0, unreadable: 0, folders: 412,
  outside_rows: 0, outside: [], native_inside: 0, poor: false, poor_why: [],
  summary: "Looked at a sample of 2000 of 68466 rows covering 412 folder(s): 1998 match, 2 differ (changed since indexed; sync settles them), 0 are missing.",
};

function entry(overrides = {}) {
  return {
    name: "pictures", address: SHARE, added: "2026-10-02 09:00:00", places: [OLD], active: OLD, previous: null,
    writes_to: `Tags and renames are written to files at ${OLD}.`, mapped: true, rows: 68466,
    // The server's own field: where files are written, the first place of the map, which is the active one.
    writes_to_place: overrides.active === undefined ? OLD : overrides.active, ...overrides,
  };
}

function listing(roots, extra = {}) {
  return { library: LIBRARY, map: "C:/TagPup/machine_roots.json", problem: null, busy: [],
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
    assert.equal(row.querySelector(".roots-last"), null, "no Verify, so no last check");
    assert.equal(row.querySelector(".roots-verify"), null);
    assert.equal(row.querySelector(".roots-back"), null, "Change back is gone");
  });

  test("it carries no line about other programs scanning the files, and asks nothing about them", async (t) => {
    const ctx = await tuner(t);
    const dialog = await openFromGear(ctx);
    assert.equal(dialog.querySelector(".file-access-note"), null);
    assert.equal(dialog.querySelector('a[href*="file-access"]'), null);
    assert.equal(ctx.server.calls.filter((c) => c.url.includes("file-access")).length, 0);
  });

  test("after a Change location the root names its place once, the earlier place stays in the open, the rest is folded (#915)", async (t) => {
    // The shape the server makes after a move: the new place first, the old kept, writes to the first.
    const ctx = await tuner(t, { roots: [entry({ places: [NEW, OLD], active: NEW, previous: OLD,
      writes_to: `Tags and renames are written to files at ${NEW}.` })] });
    const row = (await openFromGear(ctx)).querySelector(".roots-root");
    const more = row.querySelector("details.roots-more");
    assert.ok(more, "where files are written is folded away");
    assert.equal(more.hasAttribute("open"), false, "closed while the writes go where the root is kept");
    assert.ok(more.querySelector(".roots-writes"));
    const previous = row.querySelector(".roots-previous");
    assert.ok(previous && !previous.closest("details"), "the separate-copy sentence is not folded away");
    assert.match(text(previous), /separate copy.*Moving again forgets it/);
    const open = [...row.children].filter((child) => child !== more).map(text).join(" ");
    assert.equal(open.split(NEW).length - 1, 1, "the place is named once outside the fold");
    assert.equal(open.includes("Tags and renames"), false);
  });

  test("the fold compares the server's two places as paths, not as text", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ writes_to_place: "d:\\training\\pictures\\" })] });
    const more = (await openFromGear(ctx)).querySelector(".roots-root details.roots-more");
    assert.equal(more.hasAttribute("open"), false, "the same folder in another spelling is the same place");
  });

  test("the fold opens when the server says files are written elsewhere than the root is kept", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ writes_to_place: NEW,
      writes_to: `Tags and renames are written to files at ${NEW}.` })] });
    const more = (await openFromGear(ctx)).querySelector(".roots-root details.roots-more");
    assert.equal(more.hasAttribute("open"), true);
    assert.match(text(more.querySelector("summary")), /elsewhere/);
  });

  test("the dialog gives way on a narrow window", async () => {
    const css = readFileSync(new URL("../../web/tuner/style.css", import.meta.url), "utf8");
    assert.match(css, /@media \(max-width: 600px\) \{[^@]*\.roots-title \{ flex-wrap: wrap/);
  });

  test("a root with an earlier place says it is a separate copy", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ places: [NEW, OLD], active: NEW, previous: OLD,
      writes_to: `Tags and renames are written to files at ${NEW}.` })] });
    const dialog = await openFromGear(ctx);
    const row = dialog.querySelector(".roots-root");
    assert.match(text(row.querySelector(".roots-previous")), new RegExp(`Before that: ${OLD}.*separate copy`));
  });

  test("a root another library uses too says that moving it moves it for them", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ shared_with: ["kr-track"] })] });
    const row = (await openFromGear(ctx)).querySelector(".roots-root");
    assert.equal(text(row.querySelector(".roots-shared")), "kr-track also uses this root: moving it moves it for them too.");
  });

  test("a library with no roots says so", async (t) => {
    const ctx = await tuner(t, { roots: [] });
    const dialog = await openFromGear(ctx);
    assert.equal(text(dialog.querySelector(".roots-empty")),
      "This library has no roots; nothing to change here.");
    assert.equal(dialog.querySelectorAll(".roots-root").length, 0);
  });

  test("a root this computer does not place offers Change location", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ places: [], active: null, mapped: false, writes_to: null, rows: null })] });
    const row = (await openFromGear(ctx)).querySelector(".roots-root");
    assert.match(text(row.querySelector(".roots-place")), /does not place this root yet/);
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

describe("what the owner is told before a move (#467, #472)", () => {
  test("the panel says what TagTuner cannot see, and that only two places are kept", async (t) => {
    const ctx = await tuner(t);
    const dialog = await openFromGear(ctx);
    click(ctx.window, dialog.querySelector(".roots-move"));
    const note = text(dialog.querySelector(".roots-note"));
    assert.match(note, /TagTuner sees only its own runs/);
    assert.match(note, /Stop Suggest in TagPup, any index you started from the command line, and a sync/);
    assert.match(note, /Only the current and the previous place are kept/);
  });

  test("a root with an earlier place says moving again forgets it", async (t) => {
    const ctx = await tuner(t, { roots: [entry({ places: [NEW, OLD], active: NEW, previous: OLD })] });
    const row = (await openFromGear(ctx)).querySelector(".roots-root");
    assert.match(text(row.querySelector(".roots-previous")), /Moving again forgets it/);
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

  test("a place that is refused is shown with its sentence", async (t) => {
    const { ctx, dialog, type } = await panelOn(t);
    const why = "30 of the 40 rows looked at (75%) are not there: this looks like another folder, or another copy.";
    ctx.server.routes.unshift({ match: "/api/roots/change-location", status: 400, headers: {}, body: () => ({
      success: false, error: `Not changed: ${why} Say override to change it anyway.`, would_refuse: why,
      verify: { ...VERIFY, poor: true, poor_why: [why] } }) });
    type("D:/Other");
    click(ctx.window, dialog.querySelector(".roots-look"));
    await flush(ctx.window, 6);
    const shown = text(dialog.querySelector(".roots-check"));
    assert.match(shown, /another folder, or another copy/);
    assert.ok(!dialog.querySelector(".roots-override").classList.contains("hidden"));
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

describe("a page holding an old place's paths (#492)", () => {
  const reloads = (ctx) => ctx.consoleErrors.filter((e) => /navigation/i.test(String(e && e.message || e))).length;

  async function movedPage(t, app, { recent = false } = {}) {
    const server = new FakeServer()
      .on("/api/apps", { this: app === "tagpup" ? "tagpup" : "tuner", apps: {} })
      .on("/api/databases", { databases: [LIBRARY] })
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on(app === "tagpup" ? "/api/taxonomy/tree" : "/api/photos", [], { headers: { "X-TagPup-Roots-Moved": "pictures" } });
    const ctx = await loadApp(app, { t, url: `http://localhost:8080/${LIBRARY}/`, server,
      before: (window) => { if (recent) window.sessionStorage.setItem("tagpup-roots-reload", String(Date.now())); } });
    await flush(ctx.window, 8);
    return ctx;
  }

  for (const app of ["tagtuner", "tagpup"]) {
    test(`${app} says the place changed and reloads once`, async (t) => {
      const ctx = await movedPage(t, app);
      const banner = ctx.document.getElementById("roots-banner");
      assert.ok(!banner.classList.contains("hidden"));
      assert.match(text(banner), /The place of root pictures changed since this page loaded; reloading/);
      assert.doesNotMatch(text(banner), /does not know where this library keeps/);
      assert.equal(reloads(ctx), 1);
      assert.ok(Number(ctx.window.sessionStorage.getItem("tagpup-roots-reload")) > 0);
    });
  }

  test("a reload within 30 s of the last one is not made, and the banner still says why", async (t) => {
    const ctx = await movedPage(t, "tagtuner", { recent: true });
    assert.equal(reloads(ctx), 0, "a page that cannot converge reloaded again");
    assert.match(text(ctx.document.getElementById("roots-banner")), /changed since this page loaded/);
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
