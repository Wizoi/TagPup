/**
 * "Name faces from tags", from TagTuner's Folder Matches header and gear and from TagPup's Organize folder view
 * (web/common/name-faces.js; docs/findings.md, #789).
 *
 * One dialog for both pages: it starts the job (the plan is read, nothing is written), shows the question the plan
 * makes -- "Name N faces in M photos (X by their tag, Y by looking like the person's confirmed faces)? Z are left for
 * you." -- and only the answer Yes writes. A second box, off, also groups the rest of the faces, and its text says that
 * it works on the whole library and re-derives automatic names. The server is scripted: these tests are about what
 * the person sees and what the page sends.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";
import { OPEN_DIALOG } from "../../web/common/dialog.js";
import { questionText, folderText } from "../../web/common/name-faces.js";

afterEach(() => closeAllApps());

const LIBRARY = "kr-track";

const PLAN = { faces: 10477, by_tag: 5093, by_comparison: 5384, photos: 9624, left: 11924, earlier_apply: false, again: null };

function status(state, extra = {}) {
  return { job: 7, library: LIBRARY, state, phase: state, stage: "reading", label: null, percent: null, done: 0, total: 1,
           started: 1, finished: null, elapsed: 3, cancelling: false, can_cancel: false, message: null, plan: null,
           group: false, applied: null, grouped: null, in_folder: null, folder: false, ask_seconds: null, ...extra };
}

const ASKING = status("asking", { plan: PLAN, ask_seconds: 900, can_cancel: true });
const PLANNING = status("planning", { label: "Comparing faces with the confirmed faces of the people", percent: 40, can_cancel: true });

async function openFrom(appName, t, { start, confirm, cancel, statusAnswer, current } = {}) {
  const server = new FakeServer()
    .on("/api/apps", { this: appName === "tagtuner" ? "tuner" : "tagpup", apps: {} })
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", [])
    .on("/api/name-faces/current", () => ({ success: true, status: current || null }))
    .on("/api/name-faces/start", () => start || { success: true, status: ASKING })
    .on("/api/name-faces/confirm", () => (confirm ? confirm() : { success: true, status: status("applying", { phase: "applying", label: "Writing", can_cancel: true }) }))
    .on("/api/name-faces/cancel", () => cancel || { success: true, status: status("cancelled", { message: "Cancelled: nothing was changed." }) })
    .on("/api/name-faces/status", () => (statusAnswer ? statusAnswer() : { success: true, status: ASKING }));
  const ctx = await loadApp(appName, { t, url: `http://localhost:8080/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  ctx.server = server;
  ctx.button = ctx.document.getElementById("btn-name-faces");
  ctx.modal = () => ctx.document.getElementById("name-faces-modal");
  ctx.open = async () => {
    click(ctx.window, ctx.button);
    await flush(ctx.window, 8);
  };
  ctx.posts = (path) => server.calls.filter((c) => c.url.includes(path) && c.method === "POST").map((c) => c.body);
  ctx.text = (selector) => ctx.modal().querySelector(selector)?.textContent;
  ctx.shown = (id) => ![...ctx.modal().querySelectorAll("button")].find((b) => b.textContent === id)?.classList.contains("hidden");
  ctx.press = async (label) => {
    const found = [...ctx.modal().querySelectorAll("button")].find((b) => b.textContent === label && !b.classList.contains("hidden"));
    assert.ok(found, `no ${label} button is shown`);
    click(ctx.window, found);
    await flush(ctx.window, 8);
  };
  return ctx;
}

describe("the words", () => {
  test("the question is the plan in one sentence, counts only", () => {
    assert.equal(questionText(PLAN), "Name 10,477 faces in 9,624 photos (5,093 by their tag, 5,384 by looking like the person's "
      + "confirmed faces)? 11,924 photos are left for you.");
    assert.equal(questionText({ ...PLAN, faces: 1, photos: 1, by_tag: 1, by_comparison: 0, left: 1 }),
      "Name 1 face in 1 photo (1 by their tag, 0 by looking like the person's confirmed faces)? 1 photo is left for you.");
    assert.match(questionText({ ...PLAN, faces: 0, photos: 0, by_tag: 0, by_comparison: 0 }), /^No face can be named from its tag\./);
  });

  test("what changed in the open folder is said from the counts before and after", () => {
    assert.equal(folderText({ before: { named: 68, unnamed: 2407 }, after: { named: 90, unnamed: 2385 } }),
      "In the open folder: 22 more named; 90 named and 2,385 still unnamed.");
    assert.equal(folderText({ before: { named: 5, unnamed: 1 }, after: { named: 5, unnamed: 1 } }),
      "In the open folder: no face named or unnamed; 5 named and 1 still unnamed.");
    assert.equal(folderText(null), "");
    assert.equal(folderText({ before: null, after: null }), "", "a folder the page could not count says nothing");
  });
});

for (const appName of ["tagpup", "tagtuner"]) {
  describe(`${appName}'s Name faces from tags`, () => {
    test("the button starts the job and the question is shown with a dialog the shortcuts leave alone", async (t) => {
      const ctx = await openFrom(appName, t);
      await ctx.open();
      assert.equal(ctx.posts("/api/name-faces/start").length, 1);
      assert.ok(ctx.document.querySelector(OPEN_DIALOG));
      assert.match(ctx.text(".name-faces-scope"), /whole library, not only the open folder/);
      assert.equal(ctx.text(".name-faces-question"), questionText(PLAN));
      assert.ok(ctx.shown("Yes") && ctx.shown("No"));
      assert.equal(ctx.modal().querySelector("#name-faces-group").checked, false, "grouping is off until it is ticked");
    });

    test("the grouping box says it works on the whole library and re-derives the automatic names", async (t) => {
      const ctx = await openFrom(appName, t);
      await ctx.open();
      const text = ctx.text(".name-faces-group");
      assert.match(text, /every face in the library/);
      assert.match(text, /re-derives the automatic names/);
      assert.doesNotMatch(text, /take away/);
      assert.match(text, /its photo's own tags confirm is kept/);
      assert.match(text, /a name you gave by hand/);
      assert.match(text, /needs no graphics card/);
      assert.match(text, /History cannot undo it/);
      assert.match(text, /History can no longer be counted on to undo the change that writes those names either/);
    });

    test("a page opened while the job works shows its progress at once, and says what is refused meanwhile", async (t) => {
      const working = status("applying", { phase: "applying", label: "Writing the names", can_cancel: true });
      const ctx = await openFrom(appName, t, { current: working, statusAnswer: () => ({ success: true, status: working }) });
      assert.ok(!ctx.modal().classList.contains("hidden"));
      assert.match(ctx.text(".name-faces-step"), /Writing the names/);
      assert.match(ctx.text(".name-faces-body"), /changes to faces, tags and folders in this library are refused/);
      assert.equal(ctx.posts("/api/name-faces/start").length, 0, "attaching starts nothing");
    });

    test("a page opened while a question waits does not open it by itself", async (t) => {
      const ctx = await openFrom(appName, t, { current: ASKING });
      assert.ok(!ctx.modal() || ctx.modal().classList.contains("hidden"));
    });

    test("the question says History undoes the change, until the grouping is ticked", async (t) => {
      const ctx = await openFrom(appName, t);
      await ctx.open();
      assert.equal(ctx.text(".name-faces-again"), "Yes writes them as one change that History can undo.");
      const box = ctx.modal().querySelector("#name-faces-group");
      box.checked = true;
      box.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
      assert.match(ctx.text(".name-faces-again"), /^With the grouping ticked, History can no longer be counted on to undo this change once grouping has run/);
      box.checked = false;
      box.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
      assert.equal(ctx.text(".name-faces-again"), "Yes writes them as one change that History can undo.");
    });

    test("Yes sends the answer once, without the grouping unless it is ticked", async (t) => {
      const ctx = await openFrom(appName, t);
      await ctx.open();
      await ctx.press("Yes");
      assert.deepEqual(ctx.posts("/api/name-faces/confirm"), [{ job: 7, group: false }]);
      assert.match(ctx.text(".name-faces-step"), /Writing/);
      assert.ok(ctx.shown("Cancel"));
    });

    test("with the grouping ticked, Yes asks for it", async (t) => {
      const ctx = await openFrom(appName, t);
      await ctx.open();
      const box = ctx.modal().querySelector("#name-faces-group");
      box.checked = true;
      box.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
      await ctx.press("Yes");
      assert.deepEqual(ctx.posts("/api/name-faces/confirm"), [{ job: 7, group: true }]);
    });

    test("rapid clicks on Yes send one answer", async (t) => {
      const ctx = await openFrom(appName, t);
      await ctx.open();
      const yes = [...ctx.modal().querySelectorAll("button")].find((b) => b.textContent === "Yes");
      click(ctx.window, yes);
      click(ctx.window, yes);
      click(ctx.window, yes);
      await flush(ctx.window, 8);
      assert.equal(ctx.posts("/api/name-faces/confirm").length, 1);
    });

    test("with nothing to name, Yes waits for the grouping to be ticked", async (t) => {
      const empty = status("asking", { plan: { ...PLAN, faces: 0, photos: 0, by_tag: 0, by_comparison: 0 }, ask_seconds: 900 });
      const ctx = await openFrom(appName, t, { start: { success: true, status: empty } });
      await ctx.open();
      const yes = [...ctx.modal().querySelectorAll("button")].find((b) => b.textContent === "Yes");
      assert.equal(yes.disabled, true);
      const box = ctx.modal().querySelector("#name-faces-group");
      box.checked = true;
      box.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
      assert.equal(yes.disabled, false);
    });

    test("a library applied before says so, and Yes is the second time", async (t) => {
      const again = status("asking", { plan: { ...PLAN, earlier_apply: true, again: "faces-from-tags was applied to this library before." }, ask_seconds: 900 });
      const ctx = await openFrom(appName, t, { start: { success: true, status: again } });
      await ctx.open();
      assert.match(ctx.text(".name-faces-again"), /^Applied before\. faces-from-tags was applied to this library before\.$/);
    });

    test("No changes nothing and says so", async (t) => {
      const ctx = await openFrom(appName, t);
      await ctx.open();
      await ctx.press("No");
      assert.deepEqual(ctx.posts("/api/name-faces/cancel"), [{ job: 7 }]);
      assert.equal(ctx.posts("/api/name-faces/confirm").length, 0);
      assert.match(ctx.text(".name-faces-result"), /nothing was changed/);
    });

    test("a plan being read shows its step and a bar, and Cancel stops it", async (t) => {
      const ctx = await openFrom(appName, t, { start: { success: true, status: PLANNING } });
      await ctx.open();
      assert.match(ctx.text(".name-faces-step"), /Comparing faces/);
      assert.equal(ctx.modal().querySelector(".name-faces-bar").getAttribute("aria-valuenow"), "40");
      assert.ok(ctx.shown("Cancel") && !ctx.shown("Yes"));
      await ctx.press("Cancel");
      assert.deepEqual(ctx.posts("/api/name-faces/cancel"), [{ job: 7 }]);
    });

    test("a second click while one runs shows the one that runs, and the refusal", async (t) => {
      const running = { success: false, error: "Name faces from tags already is reading its plan in kr-track.", job: PLANNING };
      const ctx = await openFrom(appName, t, { start: running });
      await ctx.open();
      assert.match(ctx.text(".name-faces-step"), /Comparing faces/);
      assert.match(ctx.text(".name-faces-status-line"), /already is reading its plan/);
    });

    test("a refusal to start is said, with the reason", async (t) => {
      const ctx = await openFrom(appName, t, { start: { success: false, error: "Not now in kr-track: an index run is running or queued." } });
      await ctx.open();
      assert.match(ctx.text(".name-faces-result"), /^Not now in kr-track: an index run/);
      assert.ok(!ctx.shown("Yes"));
    });

    test("a refused Yes keeps the question and says why", async (t) => {
      const refusal = { success: false, error: "Not now in kr-track: Suggest is running." };
      const ctx = await openFrom(appName, t, { confirm: () => refusal });
      await ctx.open();
      await ctx.press("Yes");
      assert.match(ctx.text(".name-faces-status-line"), /Suggest is running/);
      assert.equal(ctx.text(".name-faces-question"), questionText(PLAN), "the question stays: it can be answered again");
    });

    test("a plan the server has let go is not left on screen as a question", async (t) => {
      const gone = status("expired", { message: "The plan was not answered in 15 minutes and was let go." });
      const ctx = await openFrom(appName, t, {
        confirm: () => ({ success: false, error: "The plan was not answered in 15 minutes and was let go." }),
        statusAnswer: () => ({ success: true, status: gone }) });
      await ctx.open();
      await ctx.press("Yes");
      assert.match(ctx.text(".name-faces-result"), /let go/);
      assert.ok(!ctx.shown("Yes"));
    });

    test("the result is counts, and the open folder's change", async (t) => {
      const done = status("done", { applied: { changed: 3, change: 12 }, in_folder: { before: { named: 1, unnamed: 5 }, after: { named: 4, unnamed: 2 } },
                                   message: "3 face names given from the photos' tags (one change in History, which Undo takes back)." });
      const ctx = await openFrom(appName, t, { confirm: () => ({ success: true, status: done }) });
      await ctx.open();
      await ctx.press("Yes");
      assert.match(ctx.text(".name-faces-result"), /3 face names given/);
      assert.equal(ctx.text(".name-faces-folder"), "In the open folder: 3 more named; 4 named and 2 still unnamed.");
    });

    test("closing the dialog leaves the job running and the button shows it again", async (t) => {
      const ctx = await openFrom(appName, t, { start: { success: true, status: PLANNING },
                                              statusAnswer: () => ({ success: true, status: PLANNING }) });
      await ctx.open();
      await ctx.press("Close");
      assert.ok(ctx.modal().classList.contains("hidden"));
      assert.equal(ctx.posts("/api/name-faces/cancel").length, 0, "closing is not cancelling");
      await ctx.open();
      assert.ok(!ctx.modal().classList.contains("hidden"));
      assert.match(ctx.text(".name-faces-step"), /Comparing faces/);
    });

    test("a job that ended while the dialog was closed is shown, not started again", async (t) => {
      const done = status("done", { message: "Nothing needed naming." });
      let ended = false;
      const ctx = await openFrom(appName, t, { start: { success: true, status: PLANNING },
                                              statusAnswer: () => ({ success: true, status: ended ? done : PLANNING }) });
      await ctx.open();
      await ctx.press("Close");
      ended = true;
      await ctx.open();
      assert.equal(ctx.posts("/api/name-faces/start").length, 1);
      assert.match(ctx.text(".name-faces-result"), /Nothing needed naming/);
    });
  });
}

describe("TagTuner's header", () => {
  test("the button is part of Folder Matches and the gear has the same item", async (t) => {
    const ctx = await openFrom("tagtuner", t);
    const { document, window } = ctx;
    const container = document.getElementById("name-faces-container");
    assert.ok(!container.classList.contains("hidden"));
    const mode = document.getElementById("tuner-mode");
    mode.value = "unmatched-faces";
    mode.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.ok(container.classList.contains("hidden"), "it belongs to Folder Matches");
    mode.value = "folder-match";
    mode.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.ok(!container.classList.contains("hidden"));
    click(window, document.getElementById("btn-gear"));
    await flush(window);
    click(window, document.querySelector('[data-action="name-faces"]'));
    await flush(window, 8);
    assert.ok(!ctx.modal().classList.contains("hidden"));
    assert.equal(ctx.posts("/api/name-faces/start").length, 1);
  });

  test("once names were written the list beside the photo is read again", async (t) => {
    const done = status("done", { applied: { changed: 3, change: 12 }, message: "3 face names given." });
    const ctx = await openFrom("tagtuner", t, { confirm: () => ({ success: true, status: done }) });
    await ctx.open();
    const before = ctx.server.urls().filter((u) => u.includes("/api/photos")).length;
    await ctx.press("Yes");
    await flush(ctx.window, 8);
    assert.ok(ctx.server.urls().filter((u) => u.includes("/api/photos")).length > before);
  });
});

describe("TagPup's folder view", () => {
  test("the button sits in the folder view's header with its reach in the title", async (t) => {
    const ctx = await openFrom("tagpup", t);
    assert.ok(ctx.button.closest(".folder-view-actions"));
    assert.match(ctx.button.title, /whole library, not only this folder/);
  });
});
