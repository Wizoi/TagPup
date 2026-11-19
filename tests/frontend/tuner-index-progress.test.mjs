/**
 * TagTuner's header bar for the index: what is running, how far it got, and what waits behind it.
 *
 * Indexing runs in the server, not in the tab, so the page asks what is running when it loads and follows the queue
 * as each folder finishes (web/tuner/indexing.js). Folders are queued from Manage folders, the dialog both pages share
 * (tests/frontend/folder-manager.test.mjs).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

describe("reloading the page mid-index", () => {
  // Indexing runs in a thread on the server, not in the tab, so a reload neither
  // restarts nor stops it. What the page has to do is ask what is already running,
  // because a freshly loaded page knows nothing about a job that began before it --
  // without that it showed an idle, enabled button over a busy server.

  function busyServer() {
    return new FakeServer()
      .on("/api/folder/index-active", {
        active: [{ folder: "D:\\x\\running", name: "running", percent: 36, message: "Generating embeddings: 36% (24/67)" }],
        queued: [{ folder: "D:\\x\\next", name: "next" }],
        busy: true,
        remaining: 2,
      })
      .on("/api/folder/index-status", { status: "running", percent: 36, message: "Generating embeddings: 36% (24/67)" });
  }

  test("a fresh page picks the running job back up", async (t) => {
    const { document, window } = await loadApp("tagtuner", { server: busyServer(), t });
    await new Promise((r) => window.setTimeout(r, 30));
    assert.ok(
      !document.getElementById("index-progress-container").classList.contains("hidden"),
      "the progress bar was not restored"
    );
    assert.match(document.getElementById("index-progress-text").textContent, /36%/);
  });

  test("the progress bar resumes at the percentage it had reached", async (t) => {
    const { document, window } = await loadApp("tagtuner", { server: busyServer(), t });
    await new Promise((r) => window.setTimeout(r, 30));
    assert.equal(document.getElementById("index-progress-bar").style.width, "36%");
  });

  test("the queue behind it is restored too", async (t) => {
    const { document, window } = await loadApp("tagtuner", { server: busyServer(), t });
    await new Promise((r) => window.setTimeout(r, 30));
    assert.match(document.getElementById("index-queue-summary").textContent, /1 more waiting/);
  });

  test("it keeps polling, so the restored bar is live and not a snapshot", async (t) => {
    const s = busyServer();
    const { window } = await loadApp("tagtuner", { server: s, t });
    await new Promise((r) => window.setTimeout(r, 1200));
    const polls = s.urls().filter((u) => u.includes("index-status")).length;
    assert.ok(polls >= 2, `only ${polls} status poll(s) after reload`);
  });

  test("an idle server leaves the page idle", async (t) => {
    const s = new FakeServer().on("/api/folder/index-active", {
      active: [], queued: [], busy: false, remaining: 0,
    });
    const { document, window } = await loadApp("tagtuner", { server: s, t });
    await new Promise((r) => window.setTimeout(r, 30));
    assert.ok(document.getElementById("index-progress-container").classList.contains("hidden"));
  });
});

describe("showing what is waiting", () => {
  test("the running folder is named and the rest are counted", async (t) => {
    const server = new FakeServer().on("/api/folder/index-active", {
      active: [{ folder: "D:\\x\\running", name: "running", percent: 10, message: "working" }],
      queued: [{ folder: "D:\\x\\next", name: "next" }],
      busy: true,
      remaining: 2,
    });
    const { document, window } = await loadApp("tagtuner", { server, t });
    await new Promise((r) => window.setTimeout(r, 20));

    const summary = document.getElementById("index-queue-summary");
    assert.ok(!summary.classList.contains("hidden"), "the queue was not shown");
    assert.match(summary.textContent, /running/, "the folder being worked on is not named");
    assert.match(summary.textContent, /1 more waiting/);
  });

  test("an empty queue shows nothing and offers no cancel", async (t) => {
    const server = new FakeServer().on("/api/folder/index-active", {
      active: [], queued: [], busy: false, remaining: 0,
    });
    const { document, window } = await loadApp("tagtuner", { server, t });
    await new Promise((r) => window.setTimeout(r, 20));

    assert.ok(document.getElementById("index-queue-summary").classList.contains("hidden"));
    assert.ok(document.getElementById("btn-cancel-queue").classList.contains("hidden"));
  });

  test("a long queue is a count, not a list of names", async (t) => {
    // Listing them made a line too long to read, which ran off the edge of the bar
    // and buried the one number anybody wants.
    const queued = ["2024-09-22 - KR XC Seaside 3 Course Challenge",
                    "2024-10-05 - KR XC Twilight Invitational",
                    "2024-11-02 - KR XC WIAA District Westside Classic",
                    "d", "e"].map((n) => ({ folder: `D:\\x\\${n}`, name: n }));
    const server = new FakeServer().on("/api/folder/index-active", {
      active: [], queued, busy: true, remaining: 5,
    });
    const { document, window } = await loadApp("tagtuner", { server, t });
    await new Promise((r) => window.setTimeout(r, 20));

    const summary = document.getElementById("index-queue-summary");
    assert.match(summary.textContent, /5 more waiting/);
    assert.ok(
      !summary.textContent.includes("Seaside"),
      `the names are back in the line: ${summary.textContent}`
    );
    assert.ok(summary.title.includes("Seaside"), "the names are not available on hover");
  });

  test("Cancel forgets what waits, and the bar follows the queue the server then has", async (t) => {
    // index-active and index-cancel as the server answers them (captured in a sandbox; its folder written D:\Pictures).
    let cancelled = false;
    const server = new FakeServer()
      .on("/api/folder/index-active", () => (cancelled
        ? { active: [], queued: [], busy: false, remaining: 0 }
        : { active: [], queued: [{ folder: "D:\\Pictures\\New Meet", name: "New Meet" },
          { folder: "D:\\Pictures\\Season 2024", name: "Season 2024" }], busy: true, remaining: 2 }))
      .on("/api/folder/index-cancel", () => {
        cancelled = true;
        return { success: true, cancelled: ["D:\\Pictures\\New Meet", "D:\\Pictures\\Season 2024"],
          rerun_dropped: [], stopping: [], pending: 0 };
      });
    const { document, window } = await loadApp("tagtuner", { server, t });
    await new Promise((r) => window.setTimeout(r, 20));
    const summary = document.getElementById("index-queue-summary");
    assert.match(summary.textContent, /2 more waiting/);

    window.confirm = () => true;
    document.getElementById("btn-cancel-queue").click();
    await new Promise((r) => window.setTimeout(r, 500));
    assert.deepEqual(server.calls.filter((c) => c.url.includes("index-cancel")).map((c) => c.body), [{ all: true }]);
    assert.ok(summary.classList.contains("hidden"), "the forgotten folders are still shown waiting");
    assert.ok(document.getElementById("btn-cancel-queue").classList.contains("hidden"));
  });

  test("a disagreement between the two status reads does not spin", async (t) => {
    // index-active and index-status are two reads of state that moves between them,
    // so the first can still name a folder the second has already called finished.
    // Following that answer restarts the poll, which finishes again immediately --
    // a spin, not a wait. This pins the folder as active and terminal at once.
    const server = new FakeServer()
      .on("/api/folder/index-active", {
        active: [{ folder: "D:\\x\\ghost", name: "ghost", percent: 100, message: "done" }],
        queued: [],
        busy: true,
        remaining: 1,
      })
      .on("/api/folder/index-status", { status: "completed", percent: 100, message: "done" });

    const { window, server: s } = await loadApp("tagtuner", { server, t });
    await new Promise((r) => window.setTimeout(r, 300));
    const calls = s.urls().filter((u) => u.includes("index-active")).length;
    assert.ok(
      calls < 10,
      `index-active was called ${calls} times in 300ms -- the follower is spinning`
    );
  });
});
