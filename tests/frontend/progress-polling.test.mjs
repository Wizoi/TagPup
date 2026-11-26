/**
 * Background-job progress polling in the TagPup workspace.
 *
 * Both pollers previously had failure modes that presented as a hung UI with no error:
 * checkSuggestionsStatus had no branch for the server's default "idle" status and so
 * polled forever, and a dead worker left the progress bar spinning because the status
 * it was waiting for was never written. These tests drive the real pollers against a
 * scripted server.
 */
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { advance, loadApp, FakeServer, photoRecord, flush, openFolder, testClock } from "./harness.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

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

/**
 * Long enough for two polls of either poller (Suggest's every 1.5 s, the index's every 1 s: web/tagpup/suggestions.js), so
 * a poller that did not stop is seen asking again. On the test's clock it costs no real time.
 */
const TWO_POLLS = 3200;

describe("suggestion progress polling", () => {
  test("an idle folder stops polling instead of spinning forever", async (t) => {
    // Regression: "idle" is what the server returns for a folder that has never been
    // suggested. With no branch for it the poller ran every 1.5s indefinitely.
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [{ status: "idle" }]);

    const afterLoad = ctx.server.statusCalls;
    await advance(ctx.window, TWO_POLLS);
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
    await advance(ctx.window, 60);

    const text = ctx.document.getElementById("suggest-progress-text").textContent;
    assert.match(text, /25/, `progress text did not report progress: ${text}`);
    const container = ctx.document.getElementById("suggest-progress-container");
    assert.ok(!container.classList.contains("hidden"), "progress bar not shown");
  });

  test("a completed job hides the bar and stops polling", async (t) => {
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "completed", completed: 1, total: 1, suggestions: {} },
    ]);
    await advance(ctx.window, 120);
    const settled = ctx.server.statusCalls;
    await advance(ctx.window, TWO_POLLS);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running after completion");
    const container = ctx.document.getElementById("suggest-progress-container");
    assert.ok(container.classList.contains("hidden"), "progress bar left visible");
  });

  test("an errored job stops polling and reports the error", async (t) => {
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "error", message: "boom" },
    ]);
    await advance(ctx.window, 120);
    const settled = ctx.server.statusCalls;
    await advance(ctx.window, TWO_POLLS);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running after an error");
    assert.match(
      ctx.document.getElementById("status-text").textContent,
      /boom/,
      "the user was not told why the job failed"
    );
  });

  test("an unrecognised status does not spin the poller", async (t) => {
    // Defensive: any status the client does not know must terminate polling rather
    // than retry forever, which is how the original hang presented.
    const ctx = await loadWithStatus(t, "/api/folder/suggest-status", [
      { status: "something-new" },
    ]);
    await advance(ctx.window, 120);
    const settled = ctx.server.statusCalls;
    await advance(ctx.window, TWO_POLLS);

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
    await advance(ctx.window, 1800);
    const container = ctx.document.getElementById("suggest-progress-container");
    assert.ok(
      container.classList.contains("hidden"),
      "the bar was still visible after the job completed"
    );
  });
});

describe("indexing progress polling", () => {
  /**
   * The index poller is no longer started by a button -- TagPup has no Index button, because adding a folder to a
   * database is TagTuner's job. The page follows the library's queue (GET /api/folder/index-active: web/tagpup/suggestions.js
   * checkIndexingStatus) from its load, whoever queued the folders, so an index running elsewhere still shows its progress
   * here; that is the path these drive. What an index's end reads again, and the owner's scenario around it, is
   * scenario-working-on-a-new-folder.test.mjs (V32).
   */
  const IDLE = { active: [], queued: [], busy: false, remaining: 0 };
  const running = (percent, message) => ({ active: [{ folder: FOLDER, name: "2020", percent, message }], queued: [],
    busy: true, remaining: 1 });

  async function openOnAFolder(t, sequence, ended = { status: "completed", percent: 100, message: "Folder indexed." }) {
    const server = baseRoutes(new ScriptedServer("/api/folder/index-active", sequence))
      .on("/api/folder/index-status", ended)
      .on("/api/folder/suggest-status", { status: "idle" });
    const ctx = await loadApp("tagpup", { t,
      url: "http://localhost:8090/photo_index/?path=" + encodeURIComponent(FOLDER),
      server,
    });
    await flush(ctx.window, 6);
    return ctx;
  }

  test("nothing queued: the queue is asked, and then no more", async (t) => {
    const ctx = await openOnAFolder(t, [IDLE]);
    await advance(ctx.window, 200);
    const settled = ctx.server.statusCalls;
    assert.ok(settled > 0, "the index poller never ran");
    await advance(ctx.window, TWO_POLLS);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running with nothing queued");
  });

  test("a running index reports its message and percentage", async (t) => {
    const ctx = await openOnAFolder(t, [running(45, "Indexing photos: 50%")]);
    await advance(ctx.window, 60);

    const container = ctx.document.getElementById("index-progress-container");
    assert.ok(!container.classList.contains("hidden"), "progress bar not shown");
    assert.match(ctx.document.getElementById("index-progress-text").textContent, /Indexing photos: 50%/,
      "the worker's message was not surfaced");
    assert.equal(ctx.document.getElementById("index-progress-bar").style.width, "45%");
  });

  test("a failed index stops polling rather than spinning, and says so", async (t) => {
    const ctx = await openOnAFolder(t, [running(10, "Indexing photos: 10%"), IDLE],
      { status: "failed", percent: 0, message: "Indexing failed with exit code 1." });
    await advance(ctx.window, 1200);
    const settled = ctx.server.statusCalls;
    await advance(ctx.window, TWO_POLLS);

    assert.equal(ctx.server.statusCalls, settled, "poller kept running after failure");
    assert.ok(
      ctx.document.getElementById("index-progress-container").classList.contains("hidden"),
      "the progress bar was left up after a failure"
    );
    assert.match(ctx.document.getElementById("status-text").textContent, /2020: Indexing failed with exit code 1\./);
  });

  test("there is no Index button to press", async (t) => {
    const ctx = await openOnAFolder(t, [{ status: "completed", percent: 100 }]);
    assert.equal(
      ctx.document.getElementById("btn-index-folder"), null,
      "the Index button is back; adding folders belongs to TagTuner"
    );
  });
});
