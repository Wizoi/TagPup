/**
 * The Activity page's link to a library's folders to review opens TagTuner on it with
 * `?review=1`, and TagTuner opens its Folders to review dialog as it starts
 * (web/tuner/review.js). Opened without it, the dialog waits for the gear or the notice.
 * And both gears link to the Activity page, in a new tab, under no library.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const LIBRARY = "kr-track";

function tunerServer() {
  return new FakeServer()
    .on("/api/apps", { this: "tuner", apps: {} })
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", [])
    .on("/api/sync/review", { library: LIBRARY, count: 1, photos: 3, folders: [{ path: "D:/Pictures/Scans", photos: 3 }] })
    .on("/api/sync", { library: LIBRARY, last_in_step: null, last_run: { found: { review_folders: 1 } } });
}

describe("TagTuner opened from the Activity page", () => {
  test("with ?review=1 it opens Folders to review as it starts", async (t) => {
    const server = tunerServer();
    const { document, window } = await loadApp("tagtuner", { t, server, url: `http://localhost:8080/${LIBRARY}/?review=1` });
    await flush(window, 8);
    const modal = document.getElementById("review-modal");
    assert.ok(modal && !modal.classList.contains("hidden"), "the dialog did not open");
    assert.ok(server.urls().includes(`/${LIBRARY}/api/sync/review`), server.urls().join("\n"));
  });

  test("without it, the dialog waits to be asked", async (t) => {
    const server = tunerServer();
    const { document, window } = await loadApp("tagtuner", { t, server, url: `http://localhost:8080/${LIBRARY}/` });
    await flush(window, 8);
    const modal = document.getElementById("review-modal");
    assert.ok(!modal || modal.classList.contains("hidden"));
    assert.ok(!server.urls().includes(`/${LIBRARY}/api/sync/review`));
  });
});

describe("both gears link to the Activity page", () => {
  for (const app of ["tagpup", "tagtuner"]) {
    test(`${app}'s gear opens it in a new tab, under no library`, async (t) => {
      const server = tunerServer().on("/api/tags", []);
      const { document } = await loadApp(app, { t, server });
      const link = [...document.querySelectorAll('#gear-menu [role="menuitem"]')]
        .find((item) => item.textContent.trim() === "Activity...");
      assert.ok(link, "no Activity... in the gear");
      assert.equal(link.getAttribute("href"), "/activity/");
      assert.equal(link.getAttribute("target"), "_blank");
    });
  }
});
