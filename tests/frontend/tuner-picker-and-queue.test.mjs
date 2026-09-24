/**
 * Two things about bringing folders into the index from TagTuner.
 *
 * The picker's row for the folder itself said nothing was indexed -- the page wrote a
 * 0 there -- so an indexed leaf folder was always offered as new and preselected.
 *
 * And a folder queued again after it had finished in this session was lost track of:
 * the page remembers the folder it last saw finish, so as not to be fooled by a reply
 * still calling it running, and took the new job for the old one -- no progress bar,
 * no poll, and Remove Folder enabled while it indexed.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const PARENT = "D:\\Training\\Pictures\\Cross Country";
const FOLDER = `${PARENT}\\Meet`;

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));
const rows = (document) => [...document.querySelectorAll("#folder-picker-list .folder-picker-row")];

describe("the folder picker's own row", () => {
  afterEach(() => closeAllApps());

  test("a folder whose own photos are indexed says so", async (t) => {
    const server = new FakeServer()
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/browse-folder", { path: PARENT })
      .on("/api/folder/subfolders", {
        parent: PARENT, own_images: 9, own_indexed: 9, has_subfolders: false, folders: [],
      });
    const { window, document } = await loadApp("tagtuner", { server, t });
    document.getElementById("btn-add-folder").click();
    document.getElementById("btn-folder-picker-browse").click();
    await settle(window, 20);
    const own = rows(document).find((r) => /this folder itself/.test(r.textContent));
    assert.ok(own, "fixture: the folder itself is offered");
    assert.match(own.textContent, /already indexed/);
  });
});

describe("a folder queued again after it finished", () => {
  afterEach(() => closeAllApps());

  test("is followed like any other job", async (t) => {
    // First the folder is running, then it finishes; later it is queued again.
    let phase = "first run";
    const server = new FakeServer()
      .on("/api/folder/index-active", () =>
        phase === "done" ? { active: [], queued: [], busy: false, remaining: 0 }
          : { active: [{ folder: FOLDER, name: "Meet", percent: 10, message: "Indexing" }],
              queued: [], busy: true, remaining: 1 })
      .on("/api/folder/index-status", () => {
        if (phase === "first run") {
          phase = "done";
          return { status: "completed", percent: 100, message: "done" };
        }
        return { status: "running", percent: 20, message: "Indexing again" };
      })
      .on("/api/browse-folder", { path: PARENT })
      .on("/api/folder/subfolders", {
        parent: PARENT, own_images: 0, has_subfolders: true,
        folders: [{ path: FOLDER, name: "Meet", images: 5, indexed: 2, has_subfolders: false }],
      })
      .on("/api/folder/index-start", () => {
        phase = "second run";
        return { success: true, queued: [FOLDER], already_queued: [], pending: 1 };
      })
      .on("/api/people-with-counts", [])
      .on("/api/photos", []);
    const { window, document } = await loadApp("tagtuner", { server, t });
    await settle(window, 1600);
    assert.equal(phase, "done", "fixture: the first run finished");

    document.getElementById("btn-add-folder").click();
    document.getElementById("btn-folder-picker-browse").click();
    await settle(window, 30);
    document.getElementById("btn-folder-picker-queue").click();
    await settle(window, 700);

    assert.ok(!document.getElementById("index-progress-container").classList.contains("hidden"),
      "the second run of the folder has no progress bar");
    assert.equal(document.getElementById("btn-remove-folder").disabled, true,
      "Remove Folder is enabled while the folder indexes");
  });
});
