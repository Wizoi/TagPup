/**
 * Background-job progress polling in the TagPup workspace.
 *
 * Both pollers previously had failure modes that presented as a hung UI with no error:
 * checkSuggestionsStatus had no branch for the server's default "idle" status and so
 * polled forever, and a dead worker left the progress bar spinning because the status
 * it was waiting for was never written. These tests drive the real pollers against a
 * scripted server.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, openFolder } from "./harness.mjs";

const FOLDER = "D:\\Library\\2020";

/** A fake server whose status endpoint returns a scripted sequence of replies. */
class ScriptedServer extends FakeServer {
  constructor(statusPath, sequence) {
    super();
    this.statusPath = statusPath;
    this.sequence = [...sequence];
    this.statusCalls = 0;
  }

  install(window) {
    super.install(window);
    const inner = window.fetch;
    const self = this;
    window.fetch = function (input, init) {
      const url = typeof input === "string" ? input : String(input && input.url);
      if (url.includes(self.statusPath)) {
        self.statusCalls++;
        const next =
          self.sequence.length > 1 ? self.sequence.shift() : self.sequence[0];
        self.calls.push({ url, method: "GET" });
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(next),
        });
      }
      return inner(input, init);
    };
    return this;
  }
}

function baseRoutes(server) {
  return server
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg" })])
    .on("/api/folder/suggest-start", { success: true, status: "running" })
    .on("/api/folder/index-start", { success: true, status: "running" });
}

async function loadWithStatus(t, statusPath, sequence, extra = () => {}) {
  const server = baseRoutes(new ScriptedServer(statusPath, sequence));
  extra(server);
  const ctx = await loadApp("tagpup", { t,
    url: "http://localhost:8090/photo_index/",
    server,
  });
  await openFolder(ctx, FOLDER, { settle: 6 });
  return ctx;
}

/** Let real timers run long enough for N poll intervals. */
async function waitMs(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

describe("suggestion progress polling", () => {
  test("an idle folder stops polling instead of spinning forever", async (t) => {
    // Regression: "idle" is what the server returns for a folder that has never been
    // suggested. With no branch for it the poller ran every 1.5s indefinitely.
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [{ status: "idle" }]);

    const afterLoad = ctx.server.statusCalls;
    await waitMs(200);
    const afterWait = ctx.server.statusCalls;

    assert.ok(afterLoad >= 1, "the poller never ran");
    assert.equal(
      afterWait,
      afterLoad,
      `poller kept running on an idle folder (${afterLoad} -> ${afterWait} calls)`
    );
  });

  test("the progress bar stays hidden for an idle folder", async (t) => {
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [{ status: "idle" }]);
    const container = ctx.document.getElementById("suggest-progress-container");
    assert.ok(container.classList.contains("hidden"), "progress bar shown when idle");
  });

  test("a running job reports its progress", async (t) => {
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "running", completed: 25, total: 100, suggestions: {} },
    ]);
    await waitMs(60);

    const text = ctx.document.getElementById("suggest-progress-text").textContent;
    assert.match(text, /25/, `progress text did not report progress: ${text}`);
    const container = ctx.document.getElementById("suggest-progress-container");
    assert.ok(!container.classList.contains("hidden"), "progress bar not shown");
  });

  test("a completed job hides the bar and stops polling", async (t) => {
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "completed", completed: 1, total: 1, suggestions: {} },
    ]);
    await waitMs(120);
    const settled = ctx.server.statusCalls;
    await waitMs(200);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running after completion");
    const container = ctx.document.getElementById("suggest-progress-container");
    assert.ok(container.classList.contains("hidden"), "progress bar left visible");
  });

  test("an errored job stops polling and reports the error", async (t) => {
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "error", message: "boom" },
    ]);
    await waitMs(120);
    const settled = ctx.server.statusCalls;
    await waitMs(200);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running after an error");
    assert.match(
      ctx.document.getElementById("status-text").textContent,
      /error/i,
      "the user was not told the job failed"
    );
  });

  test("an unrecognised status does not spin the poller", async (t) => {
    // Defensive: any status the client does not know must terminate polling rather
    // than retry forever, which is how the original hang presented.
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "something-new" },
    ]);
    await waitMs(120);
    const settled = ctx.server.statusCalls;
    await waitMs(200);

    assert.equal(
      ctx.server.statusCalls,
      settled,
      "an unknown status kept the poller running"
    );
  });

  test("polling follows a job from running to completion", async (t) => {
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "running", completed: 1, total: 3, suggestions: {} },
      { status: "completed", completed: 3, total: 3, suggestions: {} },
    ]);

    // The suggestion poller ticks every 1500ms, so allow one full interval
    // beyond the immediate first query.
    await waitMs(1800);
    const container = ctx.document.getElementById("suggest-progress-container");
    assert.ok(
      container.classList.contains("hidden"),
      "the bar was still visible after the job completed"
    );
  });
});

describe("indexing progress polling", () => {
  /**
   * The index poller is no longer started by a button -- TagPup has no Index button,
   * because adding a folder to a database is TagTuner's job. It starts when the page
   * opens on a folder, so that an index running elsewhere still shows its progress
   * here. That is the path these drive.
   */
  async function openOnAFolder(t, sequence) {
    const server = baseRoutes(new ScriptedServer("/api/folder/index-status", sequence));
    const ctx = await loadApp("tagpup", { t,
      url: "http://localhost:8090/photo_index/?path=" + encodeURIComponent(FOLDER),
      server,
    });
    await flush(ctx.window, 6);
    return ctx;
  }

  test("a folder never indexed reports ready and stops", async (t) => {
    const ctx = await openOnAFolder(t, [
      { status: "completed", percent: 100, message: "Ready" },
    ]);
    await waitMs(200);
    const settled = ctx.server.statusCalls;
    assert.ok(settled > 0, "the index poller never ran");
    await waitMs(300);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running when ready");
  });

  test("a running index reports its message and percentage", async (t) => {
    const ctx = await openOnAFolder(t, [
      { status: "running", percent: 45, message: "Indexing photos: 50%" },
    ]);
    await waitMs(60);

    const container = ctx.document.getElementById("index-progress-container");
    assert.ok(!container.classList.contains("hidden"), "progress bar not shown");
    assert.match(
      ctx.document.getElementById("index-progress-text").textContent,
      /Indexing/,
      "the worker's message was not surfaced"
    );
  });

  test("a failed index stops polling rather than spinning", async (t) => {
    const ctx = await openOnAFolder(t, [
      { status: "failed", percent: 0, message: "Indexing failed with exit code 1." },
    ]);
    await waitMs(200);
    const settled = ctx.server.statusCalls;
    await waitMs(200);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running after failure");
    assert.ok(
      ctx.document.getElementById("index-progress-container").classList.contains("hidden"),
      "the progress bar was left up after a failure"
    );
  });

  test("there is no Index button to press", async (t) => {
    const ctx = await openOnAFolder(t, [{ status: "completed", percent: 100 }]);
    assert.equal(
      ctx.document.getElementById("btn-index-folder"), null,
      "the Index button is back; adding folders belongs to TagTuner"
    );
  });
});
