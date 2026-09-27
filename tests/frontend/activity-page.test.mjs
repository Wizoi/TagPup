/**
 * The Activity page (web/activity/; docs/ARCHITECTURE.md, phase 8.5): each section drawn from
 * what the server answers, Now asked again while the page is in view and not while it is
 * hidden, Run now asked first, "Logs for this run" reading that run's lines, and every
 * request sent under no library, through api.js's form for what covers every library.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, closeAllApps, click } from "./harness.mjs";

afterEach(() => closeAllApps());

const NOW = {
  at: "2026-09-26 10:00:03",
  libraries: [
    {
      name: "harbour",
      indexing: {
        running: { run: "index:harbour:20260926T095900-1", started: "2026-09-26 09:59:00", folders: 2,
                   name: "Regatta 2019", percent: 40, message: "Indexing: 40% (4/10)" },
        queued: [{ name: "Harbour Walk", folders: 1 }],
      },
      suggesting: [{ library: "harbour", folder: "Lakes", status: "running", completed: 3, total: 9 }],
      jobs: [],
    },
    { name: "regatta", indexing: { running: null, queued: [] }, suggesting: [],
      jobs: [{ job: "snapshots", run_id: 12, started: "2026-09-26 09:58:00", run: "job:regatta:12" }] },
  ],
  syncing: { library: "harbour", folder: "D:\\Photos\\Regatta", started: "2026-09-26 10:00:01" },
  watching: true,
  server: { version: "20260926-090000-abc1234", taking_work: true, requests: 1, busy: [] },
  supervisor: { state: "moving", why: "to 20260926-100000-def5678", alive: true,
                update_refused: { said: "the installed version is newer than the checkout", since: "2026-09-26 08:00:00" } },
};

function job(name, reason, runs, extra = {}) {
  return { name, period: "daily", reason, per_library: true, about: `What ${name} does.`, last: null,
           running: false, next_due: "2026-09-27 09:00:00", failing: false, runs, ...extra };
}

const JOBS = {
  runs_jobs: true,
  check_every: 600,
  running_here: [],
  libraries: [
    { name: "harbour", jobs: [
      job("snapshots", "safety", [
        { id: 9, started: "2026-09-26 09:00:00", finished: "2026-09-26 09:00:11", seconds: 11, outcome: "failed",
          changed: { errors: 1 }, error: "OSError: the disk is full", run: "job:harbour:9" },
        { id: 7, started: "2026-09-25 09:00:00", finished: "2026-09-25 09:00:12", seconds: 12, outcome: "done",
          changed: { changed: 1 }, error: null, run: "job:harbour:7" },
      ], { failing: true }),
      job("sync", "catch-up", []),
    ] },
    { name: "regatta", jobs: [job("snapshots", "safety", []), job("sync", "catch-up", [])] },
  ],
};

const SYNC = {
  watching: true,
  libraries: [
    { name: "harbour", last_in_step: "2026-09-26 09:00:00", review_folders: 2,
      review_url: "http://localhost:8080/harbour/?review=1",
      last_whole: { id: 3, started: "2026-09-26 08:59:00", finished: "2026-09-26 09:00:00", seconds: 60, whole: true,
                    in_step: true, found: {}, changed: { rows: 0 }, change: null, run: "sync:harbour:20260926T085900" },
      last_folder: null,
      roots: [{ path: "D:\\Photos", there: true, watched: true }, { path: "E:\\Archive", there: false, watched: false }],
      watches: ["D:\\Photos"], watcher: { last_event: "2026-09-26 09:59:00", pending_folders: 0, whole_pending: false,
                                          last_sync: null, not_watched: false } },
  ],
};

const SNAPSHOTS = {
  bytes: 3000000000,
  libraries: [{ name: "harbour", bytes: 3000000000, snapshots: [
    { name: "daily/20260926_090000", kind: "daily", taken: "2026-09-26 09:00:00", age_seconds: 3600, bytes: 1500000000 },
    { name: "weekly/20260920_090000", kind: "weekly", taken: "2026-09-20 09:00:00", age_seconds: 522000, bytes: 1500000000 },
  ] }],
};

const SERVER = {
  version: "20260926-090000-abc1234", supervised: true, taking_work: true, busy: [], running_since: "2026-09-26 09:00:00",
  pid: 4242, background: ["recurring jobs", "folder watcher"],
  supervisor: { state: "running", why: null, alive: true, running_since: "2026-09-26 08:59:00", crashes: 1,
                crash_window_minutes: 10, server_starts: 2, previous_version: "20260925-090000-0123456",
                version: "20260926-090000-abc1234", update_refused: null },
};

const TIMELINE = {
  limit: 50, more: true,
  entries: [
    { kind: "change", library: "regatta", time: "2026-09-26 09:30:00", finished: "2026-09-26 09:30:00", seconds: null,
      what: "change settings", outcome: "applied", counts: { rows: 1, files: 0 }, id: 5, run: null, error: null },
    { kind: "index", library: "harbour", time: "2026-09-26 09:10:00", finished: "2026-09-26 09:20:00", seconds: 600,
      what: "index of Regatta 2019", outcome: "completed", counts: { folders: 1 }, id: null,
      run: "index:harbour:20260926T091000-1", error: null },
    { kind: "job", library: "harbour", time: "2026-09-26 09:00:00", finished: "2026-09-26 09:00:11", seconds: 11,
      what: "snapshots", outcome: "failed", counts: { errors: 1 }, id: 9, run: "job:harbour:9", error: "OSError: full" },
  ],
};

const LOG_FILES = {
  folder: "D:\\TagPup\\data\\logs",
  levels: ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
  logs: [
    { name: "tagpup_web.log", program: "tagpup_web", source: "Web server", bytes: 20480, modified: 1, rotated: [] },
    { name: "supervisor.log", program: "supervisor", source: "Always on (supervisor)", bytes: 2048, modified: 0, rotated: [] },
    { name: "indexer-harbour.log", program: "indexer-harbour", source: "Indexer (harbour)", bytes: 1024, modified: 0,
      rotated: [] },
  ],
};

function records(messages, run = null) {
  return {
    name: "tagpup_web.log", bytes: 20480, start: 100, end: 20480, looked_at: 20380, complete: false, rotated: false,
    records: messages.map((message, i) => ({ time: `2026-09-26 10:00:0${i}`, ms: 0, level: "WARNING", thread: "MainThread",
                                             logger: "tagpup.web", runs: run ? [run] : [], message, offset: 100 + i })),
  };
}

function server(overrides = {}) {
  const fake = new FakeServer();
  if (overrides.first) for (const [match, body] of overrides.first) fake.on(match, body);
  return fake
    .on("/api/activity/now", NOW)
    .on("/api/activity/jobs/run", { success: true, started: true, run_id: 13, run: "job:harbour:13" })
    .on("/api/activity/jobs", JOBS)
    .on("/api/activity/sync", SYNC)
    .on("/api/activity/snapshots", SNAPSHOTS)
    .on("/api/activity/server", SERVER)
    .on("/api/activity/timeline", TIMELINE)
    .on("/api/activity/logs/", (url) => records(url.includes("run=") ? ["the run's line"] : ["slow: GET /api/tags took 1.20s"]))
    .on("/api/activity/logs", LOG_FILES);
}

function open(t, fake = server(), every = 5000) {
  return loadApp("activity", { t, server: fake, url: `http://localhost:8090/activity/?every=${every}` });
}

describe("the Activity page draws each section from what the server says", () => {
  test("Now: indexing, Suggest, a job, the watcher's sync and the always-on process", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 6);
    const now = document.getElementById("now-body");
    assert.match(now.textContent, /Regatta 2019 and 1 more folder\(s\)/);
    assert.equal(now.querySelector("progress").getAttribute("value"), "40");
    assert.match(now.textContent, /1 waiting/);
    assert.match(now.textContent, /Harbour Walk/);
    assert.match(now.textContent, /Lakes/);
    assert.match(now.textContent, /running, 3 of 9/);
    assert.match(now.textContent, /snapshots/);
    assert.match(now.textContent, /update waiting/);
    assert.match(now.textContent, /update refused/);
    assert.match(now.textContent, /D:\\Photos\\Regatta/);
    assert.equal(document.getElementById("updated").textContent, "Updated 10:00:03");
  });

  test("Scheduled jobs: each run with its counts and error, a failure flagged, Run now", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 6);
    const jobs = document.getElementById("jobs-body");
    const snapshots = jobs.querySelector('.job-card[data-job="snapshots"]');
    assert.ok(snapshots, "no card for the snapshots job");
    assert.match(snapshots.textContent, /safety/);
    const harbour = snapshots.querySelector('tr.job[data-library="harbour"]');
    assert.ok(harbour.classList.contains("failing"), "a failed last run is flagged");
    assert.match(harbour.textContent, /failed last time/);
    assert.match(harbour.textContent, /OSError: the disk is full/);
    assert.match(harbour.textContent, /11 s/);
    assert.ok(!snapshots.querySelector('tr.job[data-library="regatta"]').classList.contains("failing"));
    assert.equal(snapshots.querySelectorAll("button.run-now").length, 2);
    // Its last runs, unfolded.
    click(window, harbour.querySelector("button.fold"));
    const unfolded = jobs.querySelectorAll('.job-card[data-job="snapshots"] tr.run');
    assert.equal(unfolded.length, 2);
    assert.match(unfolded[1].textContent, /changed 1/);
  });

  test("Sync & watcher, Snapshots and Always on", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 6);
    const sync = document.getElementById("sync-body");
    assert.match(sync.textContent, /D:\\Photos/);
    assert.match(sync.textContent, /E:\\Archive/);
    assert.match(sync.textContent, /missing/);
    const review = sync.querySelector('a[href="http://localhost:8080/harbour/?review=1"]');
    assert.ok(review, "the folders to review open TagTuner's dialog");
    assert.equal(review.getAttribute("target"), "_blank");
    const snapshots = document.getElementById("snapshots-body");
    assert.equal(snapshots.querySelectorAll("tr.snapshot").length, 2);
    assert.match(snapshots.textContent, /All together: 3\.0 GB/);
    assert.match(snapshots.textContent, /1 h/);
    const always = document.getElementById("server-body");
    assert.match(always.textContent, /20260926-090000-abc1234/);
    assert.match(always.textContent, /1 in the last 10 minutes/);
    assert.match(always.textContent, /from 20260925-090000-0123456 to 20260926-090000-abc1234/);
  });

  test("Recent activity, newest first, and More reads further back", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    const rows = document.querySelectorAll("#timeline-body tr.entry");
    assert.deepEqual([...rows].map((row) => row.dataset.kind), ["change", "index", "job"]);
    assert.match(rows[2].textContent, /OSError: full/);
    const more = document.getElementById("timeline-more");
    assert.equal(more.disabled, false);
    click(window, more);
    await flush(window, 4);
    assert.ok(fake.urls().some((url) => url === "/api/activity/timeline?limit=100"), fake.urls().join("\n"));
  });

  test("every request goes under no library, through api.js", async (t) => {
    const fake = server();
    const { window } = await open(t, fake);
    await flush(window, 6);
    assert.ok(fake.urls().length >= 7);
    for (const url of fake.urls().filter((each) => !each.includes("api/rules"))) {
      assert.ok(url.startsWith("/api/activity/"), `asked under a library, or not the Activity routes: ${url}`);
    }
  });
});

describe("Now is asked while the page is in view, and not while it is hidden", () => {
  test("hidden, it asks nothing; back, it asks at once", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake, 30);
    const asked = () => fake.urls().filter((url) => url === "/api/activity/now").length;
    await new Promise((resolve) => setTimeout(resolve, 200));
    const visible = asked();
    assert.ok(visible >= 3, `asked ${visible} times in 200 ms at every 30 ms`);

    let state = "hidden";
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new window.Event("visibilitychange"));
    await flush(window, 2);
    const hiddenFrom = asked();
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(asked(), hiddenFrom, "the page asked while hidden");

    state = "visible";
    document.dispatchEvent(new window.Event("visibilitychange"));
    await flush(window, 2);
    assert.ok(asked() > hiddenFrom, "the page did not ask again once it was back in view");
  });

  test("a poll waits for the one before it to be answered", async (t) => {
    const fake = server({ first: [["/api/activity/now", () => new Promise(() => {})]] });
    const { window } = await open(t, fake, 20);
    await new Promise((resolve) => setTimeout(resolve, 150));
    await flush(window, 2);
    assert.equal(fake.urls().filter((url) => url === "/api/activity/now").length, 1);
  });
});

describe("Run now is asked first", () => {
  test("the first click asks; Cancel sends nothing; Run sends the job and the library", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    const runNow = () => document.querySelector('.job-card[data-job="sync"] tr.job[data-library="harbour"] button.run-now');
    const posted = () => fake.calls.filter((call) => call.method === "POST");

    click(window, runNow());
    await flush(window, 2);
    const confirm = document.querySelector(".confirm");
    assert.ok(confirm, "Run now did not ask first");
    assert.match(confirm.textContent, /Run sync for harbour now\?/);
    assert.equal(posted().length, 0, "sent before it was confirmed");

    click(window, confirm.querySelector("button.cancel-run"));
    await flush(window, 2);
    assert.equal(document.querySelector(".confirm"), null);
    assert.equal(posted().length, 0);

    click(window, runNow());
    await flush(window, 2);
    click(window, document.querySelector(".confirm button.confirm-run"));
    await flush(window, 6);
    assert.equal(posted().length, 1);
    assert.equal(posted()[0].url, "/api/activity/jobs/run");
    assert.deepEqual(posted()[0].body, { job: "sync", library: "harbour" });
    assert.match(document.getElementById("jobs-body").textContent, /Started \(run 13\)/);
  });

  test("a job not started says why", async (t) => {
    const fake = server({ first: [["/api/activity/jobs/run", { success: false, started: false, why: "a run of it is under way already",
                                                                   error: "Not started: a run of it is under way already." }]] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    click(window, document.querySelector('.job-card[data-job="sync"] tr.job[data-library="regatta"] button.run-now'));
    await flush(window, 2);
    click(window, document.querySelector(".confirm button.confirm-run"));
    await flush(window, 6);
    assert.match(document.getElementById("jobs-body").textContent, /Not started: a run of it is under way already/);
  });
});

describe("Logs", () => {
  test("a tab for each log, the web server's first, warnings and worse by default", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    const tabs = [...document.querySelectorAll("#log-tabs .tab")];
    assert.deepEqual(tabs.map((tab) => tab.dataset.log), ["tagpup_web.log", "supervisor.log", "indexer-harbour.log"]);
    assert.ok(tabs[0].classList.contains("chosen"));
    const read = fake.urls().find((url) => url.startsWith("/api/activity/logs/tagpup_web.log?"));
    assert.ok(read, fake.urls().join("\n"));
    assert.match(read, /level=WARNING/);
    assert.match(document.getElementById("log-records").textContent, /slow: GET \/api\/tags/);
    assert.equal(document.getElementById("log-raw").getAttribute("href"), "/api/activity/logs/tagpup_web.log/raw");
    assert.equal(document.getElementById("log-download").getAttribute("href"), "/api/activity/logs/tagpup_web.log/download");
  });

  test("Logs for this run reads only that run's lines, at every level, in its log", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    const link = document.querySelector('#timeline-body button.run-logs[data-run="index:harbour:20260926T091000-1"]');
    assert.ok(link, "no Logs for this run beside the run of the indexer");
    click(window, link);
    await flush(window, 6);
    const read = fake.urls().filter((url) => url.startsWith("/api/activity/logs/indexer-harbour.log?")).at(-1);
    assert.ok(read, fake.urls().join("\n"));
    assert.match(read, /run=index%3Aharbour%3A20260926T091000-1/);
    assert.match(read, /level=DEBUG/);
    assert.equal(document.getElementById("log-run").classList.contains("hidden"), false);
    assert.match(document.getElementById("log-records").textContent, /the run's line/);
    click(window, document.getElementById("log-run-clear"));
    await flush(window, 6);
    assert.doesNotMatch(fake.urls().at(-1), /run=/);
  });

  test("Follow asks only for what was written since, and Older for what came before", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake, 30);
    await flush(window, 8);
    const follow = document.getElementById("log-follow");
    follow.checked = true;
    follow.dispatchEvent(new window.Event("change"));
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.ok(fake.urls().some((url) => /\/api\/activity\/logs\/tagpup_web\.log\?.*after=20480/.test(url)), fake.urls().join("\n"));
    click(window, document.getElementById("log-older"));
    await flush(window, 4);
    assert.ok(fake.urls().some((url) => /before=100/.test(url)), "Older did not read from where the last read began");
  });
});
