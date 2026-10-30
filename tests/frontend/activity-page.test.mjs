/**
 * The Activity page (web/activity/; docs/ARCHITECTURE.md, phase 8.5): each section drawn from what the server answers,
 * Now asked again while the page is in view and not while it is hidden, Run now asked first, "Logs for this run" reading
 * that run's lines (Logs unfolded), every request sent under no library, through api.js's form for what covers every
 * library; Scheduled jobs, Snapshots, Server and Logs folded, each with one line, the open ones remembered where the
 * browser may; a server that stops answering asked less and less often, Now's silence said in the top line and each
 * other section's in that section, a drawing's error never taken for the server's; Run now's read of the jobs the poll's.
 *
 * Now, Needs you, Scheduled jobs, Recent activity and the list of Logs answer from fixtures/activity.json: the real
 * routes' replies on libraries seeded through the real writers (tests/test_activity_page_replies.py), one morning the
 * owner opens the page on a scenario -- quiet, long_sync, stuck, morning; a log's lines are answered here.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { loadApp, FakeServer, flush, closeAllApps, click, pageExports } from "./harness.mjs";

afterEach(() => closeAllApps());

const SERVED = JSON.parse(fs.readFileSync(new URL("./fixtures/activity.json", import.meta.url), "utf8"));

const SYNC = {
  watching: true,
  libraries: [
    { name: "harbour", last_in_step: "2026-09-26 09:00:00", review_folders: 2,
      review_url: "http://localhost:8080/harbour/?review=1",
      last_whole: { id: 3, started: "2026-09-26 08:59:00", finished: "2026-09-26 09:00:00", seconds: 60, whole: true,
                    in_step: true, found: {}, changed: { rows: 0 }, change: null, run: "sync:harbour:20260926T085900",
                    logs: ["tagpup_web.log"] },
      last_folder: { id: 4, started: "2026-09-26 09:10:00", finished: "2026-09-26 09:10:02", seconds: 2, whole: false,
                     in_step: false, found: { not_looked_at: 1309, folders_not_looked_at: 1 }, changed: { rows: 0 },
                     change: null, run: "sync:harbour:20260926T091000", logs: ["tagpup_web.log"] },
      roots: [{ path: "D:\\Photos", there: true, watched: true }, { path: "E:\\Archive", there: false, answering: true, watched: false },
              { path: "\\\\nas\\Pictures", there: false, answering: false, away_since: "2026-09-26 08:33:15", watched: false }],
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
  version: "20260926-090000-abc1234", taking_work: true, busy: [], running_since: "2026-09-26 09:00:00",
  pid: 4242, background: ["recurring jobs", "folder watcher"],
};

function records(messages, run = null) {
  return {
    name: "tagpup_web.log", bytes: 20480, start: 100, end: 20480, looked_at: 20380, complete: false, rotated: false,
    records: messages.map((message, i) => ({ time: `2026-09-26 10:00:0${i}`, ms: 0, level: "WARNING", thread: "MainThread",
                                             logger: "tagpup.web", runs: run ? [run] : [], message, offset: 100 + i })),
  };
}

/** A server answering `scenario`'s Now, jobs and timeline; `first` routes answered ahead of them. */
function server(scenario = "morning", overrides = {}) {
  const fake = new FakeServer();
  if (overrides.first) for (const [match, body] of overrides.first) fake.on(match, body);
  const served = SERVED[scenario];
  return fake
    .on("/api/activity/now", served.now)
    .on("/api/activity/jobs/run", { success: true, started: true, run_id: 13, run: "job:breakwater:13" })
    .on("/api/activity/jobs", served.jobs)
    .on("/api/activity/sync", SYNC)
    .on("/api/activity/snapshots", SNAPSHOTS)
    .on("/api/activity/server", SERVER)
    .on("/api/activity/timeline", served.timeline)
    .on("/api/activity/logs/", (url) => records(url.includes("run=") ? ["the run's line"] : ["slow: GET /api/tags took 1.20s"]))
    .on("/api/activity/logs", served.logs);
}

function open(t, fake = server(), every = 5000) {
  return loadApp("activity", { t, server: fake, url: `http://localhost:8090/activity/?every=${every}` });
}

const card = (document, library) => document.querySelector(`#now-body .now-library[data-library="${library}"]`);
const asked = (fake, route) => fake.urls().filter((url) => url === route).length;

/** Until `done()` holds, failing saying `what` after `ms`: a page that never gets there fails, never hangs the run. */
async function settled(done, what, ms = 5000) {
  const end = Date.now() + ms;
  while (!done()) {
    if (Date.now() > end) assert.fail(`gave up waiting: ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

/** Until the page has asked `route` `times` more times, and drawn the answer; failing, never hanging, if it does not. */
async function polled(window, fake, route, times = 2) {
  const from = asked(fake, route);
  await settled(() => asked(fake, route) >= from + times, `${route} asked ${times} more times`);
  await flush(window, 4);
}

describe("Now: a card a library, its runs as a tree, in the server's words", () => {
  test("the watcher's whole sync is one line with the library's sync row, at its folder, this process's index beneath it", async (t) => {
    const { document, window } = await open(t, server("long_sync"));
    await flush(window, 8);
    const harbour = card(document, "harbour");
    const top = [...harbour.querySelectorAll(":scope > ul.run-tree > li.run")];
    assert.deepEqual(top.map((li) => li.dataset.label), ["sync (folder watcher)"], "one line for the sync, not two");
    assert.match(top[0].textContent, /walking the folders again/);
    assert.match(top[0].textContent, /812 folders, 20,417 photo files/);
    assert.match(top[0].textContent, /at D:\\TagPup\\Pictures\\harbour\\2019\\Regatta/);
    assert.match(top[0].textContent, /running 14 min, last progress 5 s ago/);
    // This process's own index (its queue's) nests by its row's parent, as every run does, its phase in the server's words.
    const beneath = [...top[0].querySelectorAll(":scope > ul.run-tree > li.run")];
    assert.deepEqual(beneath.map((li) => li.dataset.label), ["index of Regatta 2019"], "the index it queued is under it");
    assert.match(beneath[0].querySelector(".phase").textContent, /^reading metadata$/);
    assert.equal(beneath[0].querySelector("progress").getAttribute("aria-valuetext"), "15%, reading metadata");
    assert.match(beneath[0].textContent, /running 4 min, last progress 3 s ago/);
    // The library with nothing running says so, and what finished in it last.
    assert.match(card(document, "lighthouse").textContent, /^lighthouseNothing is running\. The last, .+, finished .+ ago\.$/);
    assert.equal(document.getElementById("updated").textContent, "Updated 15:27:20");
  });

  test("the watcher's line joins the row the server says is its, not any sync row", async (t) => {
    // The server names the watcher's run (`source: "watcher"`); a sync row it does not name is a run of its own.
    const long = SERVED.long_sync.now;
    const harbour = long.libraries[0];
    const jobs = harbour.jobs.map((row) => ({ ...row, source: "row" }));
    const apart = { ...long, libraries: [{ ...harbour, jobs }, ...long.libraries.slice(1)] };
    const { document, window } = await open(t, server("long_sync", { first: [["/api/activity/now", apart]] }));
    await flush(window, 8);
    const top = [...card(document, "harbour").querySelectorAll(":scope > ul.run-tree > li.run")];
    assert.deepEqual(top.map((li) => li.dataset.label), ["sync (folder watcher)", "sync"]);
  });

  test("a run is stuck only when its last progress is over five minutes old; one that said none is not", async (t) => {
    const stuck = SERVED.stuck.now;
    const [harbourNow, lighthouseNow] = stuck.libraries;
    const running = { ...harbourNow.indexing.running, progressed: null, said: null };
    const silent = { ...stuck, libraries: [{ ...harbourNow, indexing: { ...harbourNow.indexing, running } }, lighthouseNow] };
    const { document, window } = await open(t, server("stuck", { first: [["/api/activity/now", silent]] }));
    await flush(window, 8);
    const lighthouse = card(document, "lighthouse");
    assert.match(lighthouse.textContent, /stuck/);
    assert.match(lighthouse.textContent, /writing the tags into the files/);
    assert.match(lighthouse.textContent, /2 of 9 folders done, 14 written, 40 already current/);
    assert.match(lighthouse.textContent, /no progress for 12 min; the share may be away/);
    const harbour = card(document, "harbour");
    assert.equal(harbour.querySelector(".run-line.stuck"), null);
    assert.match(harbour.querySelector('li.run[data-label="index of Regatta 2019"]').textContent, /no progress said yet/);
  });

  test("this process's own index: its queue's words and bar, the folder waiting behind it and Suggest beside", async (t) => {
    const long = SERVED.long_sync.now;
    const harbour = { ...long.libraries[0],
      suggesting: [{ library: "harbour", folder: "Lakes", status: "running", completed: 3, total: 9 }] };
    const { document, window } = await open(t, server("long_sync", { first: [["/api/activity/now", { ...long, libraries: [harbour] }]] }));
    await flush(window, 8);
    const sync = card(document, "harbour").querySelector('li.run[data-label="sync (folder watcher)"]');
    const index = sync.querySelector('li.run[data-label="index of Regatta 2019"]');
    assert.ok(index, card(document, "harbour").textContent);
    assert.equal(index.querySelector("progress").getAttribute("value"), "15");
    assert.match(index.textContent, /Reading metadata: 15% \(120\/800\)/, "the queue's own message: the graphics card's wait is said there");
    assert.match(card(document, "harbour").textContent, /1 waiting.*Harbour Walk/);
    assert.match(card(document, "harbour").textContent, /Lakes.*running, 3 of 9/);
  });

  test("a library with nothing running says what finished in it last, whatever the timeline of every library holds", async (t) => {
    const cut = { entries: [], more: true, limit: 50 };
    const { document, window } = await open(t, server("morning", { first: [["/api/activity/timeline", cut]] }));
    await flush(window, 8);
    assert.match(card(document, "jetty").textContent, /Nothing is running\. The last, sync, finished 2 h ago\./);
    // A server before last_finished: nothing said of the last, never a guess.
    const jobs = { ...SERVED.morning.jobs, libraries: SERVED.morning.jobs.libraries.map(({ last_finished, ...rest }) => rest) };
    const older = await open(t, server("morning", { first: [["/api/activity/jobs", jobs]] }));
    await flush(older.window, 8);
    assert.equal(card(older.document, "jetty").textContent, "jettyNothing is running.");
  });

  test("batches of photo files, running and waiting, with Cancel for this server's own", async (t) => {
    const quiet = SERVED.quiet.now;
    const batches = [
      { ticket: 3, id: 41, operation: "bulk tags", library: "harbour", photos: 60, done: 25, conflict: 1, planned: 34,
        started: 1, state: "running", position: 0, here: true },
      { ticket: 4, id: null, operation: "bulk people", library: "harbour", photos: 40, done: 0, conflict: 0, planned: 0,
        started: null, state: "queued", position: 1, here: true },
      { ticket: null, id: 42, operation: "bulk tags", library: "harbour", photos: 30, done: 2, conflict: 0, planned: 28,
        started: null, state: "running", position: 0, here: false },
    ];
    const now = { ...quiet, libraries: [{ ...quiet.libraries[0], batches }, quiet.libraries[1]] };
    const fake = server("quiet", { first: [["/api/activity/now", now], ["/api/activity/batches/cancel", { success: true }]] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    const harbour = card(document, "harbour");
    assert.match(harbour.textContent, /bulk tags.*25 of 60 photos written, 1 changed outside/);
    assert.match(harbour.textContent, /bulk people.*40 photos, 1 ahead; a restart drops it before anything is written/);
    assert.match(harbour.textContent, /2 of 30 photos written \(another program runs it\)/);
    const buttons = harbour.querySelectorAll("button.cancel-batch");
    assert.equal(buttons.length, 2, "another program's batch cannot be cancelled here");
    click(window, buttons[0]);
    await flush(window, 4);
    const posted = fake.calls.filter((call) => call.method === "POST");
    assert.equal(posted.length, 1);
    assert.equal(posted[0].url, "/api/activity/batches/cancel");
    assert.deepEqual(posted[0].body, { library: "harbour", ticket: 3 });
  });

  test("the line read aloud changes with a phase or a ten of percent, never with each poll", async (t) => {
    const long = SERVED.long_sync.now;
    let answer = long;
    const fake = server("long_sync", { first: [["/api/activity/now", () => answer]] });
    const { document, window } = await open(t, fake, 20);
    await flush(window, 8);
    const line = document.getElementById("now-summary");
    assert.equal(line.getAttribute("aria-live"), "polite");
    // Only the line is read aloud: the cards, drawn again at every poll, are not.
    assert.equal(document.getElementById("now-body").getAttribute("aria-live"), null);
    assert.equal(line.textContent,
                 "harbour: sync (folder watcher), walking the folders again. harbour: index of Regatta 2019, reading metadata, 10%.");
    const said = line.firstChild;

    // Three seconds on, more folders walked: the same words, left as they were.
    const [harbourNow, lighthouseNow] = long.libraries;
    const at = (sync, index) => ({ ...long, at: "2026-09-21 15:27:23", libraries: [{ ...harbourNow,
      jobs: [{ ...harbourNow.jobs[0], progress: { ...harbourNow.jobs[0].progress, ...sync } }],
      indexing: { ...harbourNow.indexing, running: { ...harbourNow.indexing.running, ...index } } }, lighthouseNow] });
    answer = at({ counts: { folders: 830, files: 20500 } }, {});
    await polled(window, fake, "/api/activity/now");
    assert.match(document.getElementById("now-body").textContent, /830 folders/);
    assert.equal(line.firstChild, said, "the same words were written again, and read out again");

    // The walk over: said.
    answer = at({ said: "reading what changed" }, { percent: 25 });
    await polled(window, fake, "/api/activity/now");
    assert.equal(line.textContent,
                 "harbour: sync (folder watcher), reading what changed. harbour: index of Regatta 2019, reading metadata, 20%.");

    answer = SERVED.quiet.now;
    await polled(window, fake, "/api/activity/now");
    assert.equal(line.textContent, "Nothing is running.");
  });
});

describe("Needs you: what happened, what it means, what to do, how often", () => {
  test("the share dropped overnight and the stuck index ended, each as the server words it; an unreadable library on its card", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 8);
    const needs = document.getElementById("needs-body");
    assert.equal(needs.getAttribute("aria-live"), "polite");
    const blocks = [...needs.querySelectorAll(".need")];
    assert.deepEqual(blocks.map((each) => `${each.dataset.kind} ${each.dataset.library} ${each.dataset.job}`),
                     ["share breakwater sync", "error lighthouse index"]);
    const [share, error] = blocks;
    const [group] = SERVED.morning.jobs.needs_you;
    const item = group.items[0];
    for (const said of [item.sentence, group.means, item.next_step, `once, at ${item.last}`]) {
      assert.ok(share.textContent.includes(said), `${said} not in ${share.textContent}`);
    }
    assert.deepEqual([...share.querySelectorAll("dt")].map((dt) => dt.textContent),
                     ["What happened", "What it means", "What to do", "How often"]);
    assert.equal(share.querySelectorAll("button.run-now").length, 1);
    assert.equal(share.querySelector("button.run-now").textContent, "Run it now");
    assert.equal(error.querySelector("button.run-now"), null, "an index has no Run it now");
    assert.match(error.textContent, /The indexer stopped answering; its process was ended\./);
    // Now: the library that is no library says why on its card; the others are drawn, nothing running in them.
    assert.match(card(document, "pier").textContent, /Could not read this library: file is not a database/);
    assert.doesNotMatch(card(document, "pier").textContent, /Nothing is running/, "it cannot know");
    assert.match(card(document, "jetty").textContent, /Nothing is running\. The last, sync, finished 2 h ago\./);
  });

  test("drawn again only when what it shows changes; nothing left, it says so", async (t) => {
    let jobs = SERVED.morning.jobs;
    const fake = server("morning", { first: [["/api/activity/jobs/run", { success: true, started: true, run_id: 13 }],
                                              ["/api/activity/jobs", () => jobs]] });
    const { document, window } = await open(t, fake, 20);
    await flush(window, 8);
    const needs = document.getElementById("needs-body");
    const drawn = needs.firstChild;
    await polled(window, fake, "/api/activity/jobs");
    assert.equal(needs.firstChild, drawn, "a poll that read the same drew it again");
    jobs = { ...jobs, needs_you: [] };
    await polled(window, fake, "/api/activity/jobs");
    assert.equal(needs.textContent, "Nothing needs you.");
  });
});

describe("the other sections", () => {
  test("Scheduled jobs: each run with its counts and error, a failure flagged, its runs unfolded", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 6);
    const jobs = document.getElementById("jobs-body");
    const sync = jobs.querySelector('.job-card[data-job="sync"]');
    assert.match(sync.textContent, /catch-up/);
    const breakwater = sync.querySelector('tr.job[data-library="breakwater"]');
    assert.ok(breakwater.classList.contains("failing"), "a failed last run is flagged");
    assert.match(breakwater.textContent, /failed last time/);
    assert.match(breakwater.textContent, /The network share C: is not answering just now/);
    assert.ok(!sync.querySelector('tr.job[data-library="jetty"]').classList.contains("failing"),
              "an abandoned run is not a failure");
    assert.equal(sync.querySelectorAll("button.run-now").length, 4);
    click(window, sync.querySelector('tr.job[data-library="harbour"] button.fold'));
    const unfolded = jobs.querySelectorAll('.job-card[data-job="sync"] tr.run');
    assert.equal(unfolded.length, 2);
    assert.match(unfolded[0].textContent, /done/);
    // A sync job's run names its sync's record, which pairs the two in What happened: an id, never said as a count.
    assert.doesNotMatch(jobs.textContent, /record \d/, "a record's id is no count");
  });

  test("Sync & watcher, Snapshots and the server", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 6);
    const sync = document.getElementById("sync-body");
    assert.match(sync.textContent, /D:\\Photos/);
    assert.match(sync.textContent, /E:\\Archive/);
    assert.match(sync.textContent, /missing/);
    // A root on a share asleep is not answering, never missing.
    const asleep = [...sync.querySelectorAll("ul.roots li")].find(li => /nas/.test(li.textContent));
    assert.match(asleep.textContent, /share not answering since 08:33/, "since when, to the minute");
    assert.doesNotMatch(asleep.textContent, /missing/);
    // A sync that could not look at a folder on a share asleep says so, and is not in step.
    assert.match(sync.textContent, /1 folder not looked at \(share not answering or not listable\)/);
    assert.match(sync.textContent, /not in step/);
    const review = sync.querySelector('a[href="http://localhost:8080/harbour/?review=1"]');
    assert.ok(review, "the folders to review open TagTuner's dialog");
    assert.equal(review.getAttribute("target"), "_blank");
    const snapshots = document.getElementById("snapshots-body");
    assert.equal(snapshots.querySelectorAll("tr.snapshot").length, 2);
    assert.match(snapshots.textContent, /All together: 3\.0 GB/);
    assert.match(snapshots.textContent, /1 h/);
    const always = document.getElementById("server-body");
    assert.match(always.textContent, /20260926-090000-abc1234/);
  });

  test("What happened the morning after: a tree of what ran, each line how it ended and why; an unreadable library said; More", async (t) => {
    const timeline = { ...SERVED.morning.timeline, more: true };
    const fake = server("morning", { first: [["/api/activity/timeline", timeline]] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    const body = document.getElementById("timeline-body");
    assert.equal(body.querySelectorAll("li.entry").length, timeline.entries.length + 1, "every entry, and the unreadable library");
    const top = [...body.querySelectorAll(":scope > ul.run-tree > li.entry")];
    // The library that is no library says so first; it was only logged before.
    assert.ok(top[0].classList.contains("unreadable"));
    assert.match(top[0].textContent, /pier.*Could not read what was done in this library: file is not a database/);
    // The daily sync is one line, never its job's row and its record's both; the index its job queued is beneath it.
    assert.deepEqual(top.slice(1, 8).map((li) => `${li.dataset.kind} ${li.dataset.library}`),
                     ["job jetty", "sync breakwater", "sync jetty", "index harbour", "sync harbour", "index lighthouse", "sync harbour"]);
    // The watcher's long sync, ended as the sync job ends it: its run and its record one line, its index beneath it.
    const beneath = [...top[5].querySelectorAll(":scope > ul.run-tree > li.entry")];
    assert.deepEqual(beneath.map((li) => li.dataset.kind), ["index"], "the index the sync job queued, under it");
    assert.ok(beneath[0].querySelector('button.run-logs[data-run="index:harbour:20260921T152320-4242-1"]'));
    // How each ended, in a word and why: never colour alone.
    const failed = top[2].querySelector(":scope > .run-line");
    assert.equal(failed.querySelector(".badge").textContent, "failed");
    assert.match(failed.querySelector(".error").textContent, /^The network share C: is not answering just now/);
    assert.match(top[1].querySelector(".error").textContent, /the process that started it stopped before it could record its end/);
    assert.match(top[6].querySelector(".error").textContent, /The indexer stopped answering; its process was ended\./);
    assert.equal(top[3].querySelector(".badge").textContent, "in step");
    assert.equal(top[3].querySelector(".error"), null);
    assert.equal(top[5].querySelector(":scope > .run-line .badge").textContent, "in step");
    assert.match(top[5].querySelector(":scope > .run-line").textContent, /sync of the whole library/);
    assert.match(top[5].querySelector(":scope > .run-line .took").textContent, /^took \d+ (s|min)$/);
    assert.doesNotMatch(body.textContent, /record \d/, "a record's id is no count");
    const more = document.getElementById("timeline-more");
    assert.equal(more.disabled, false);
    click(window, more);
    await flush(window, 4);
    assert.ok(fake.urls().some((url) => url === "/api/activity/timeline?limit=100"), fake.urls().join("\n"));
  });

  test("What happened nests a folder sync's index by its syncs, never under a journal change of the same id; an older server's reply is drawn", async (t) => {
    // A page left open across an update: the reply of a server before `unreadable` and before a sync was paired.
    const entry = (fields) => ({ library: "harbour", time: "2026-09-26 09:10:00", finished: "2026-09-26 09:10:05", seconds: 5,
                                 outcome: "completed", counts: {}, error: null, logs: [], ...fields });
    const timeline = { limit: 50, more: false, entries: [
      entry({ kind: "index", what: "index of 1 folder(s)", id: 7, parent: null, syncs: ["sync:harbour:20260926T091000"],
              run: "index:harbour:20260926T091001-4242-1" }),
      entry({ kind: "index", what: "index of 2 folder(s)", id: 8, parent: 4, syncs: [], run: "index:harbour:20260926T090500-4242-2" }),
      entry({ kind: "sync", what: "sync of one folder", outcome: "in step", id: 6, run: "sync:harbour:20260926T091000" }),
      entry({ kind: "change", what: "bulk tags", outcome: "applied", id: 4, run: null, seconds: null }),
    ] };
    const fake = server("quiet", { first: [["/api/activity/timeline", timeline]] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    const top = [...document.querySelectorAll("#timeline-body > ul.run-tree > li.entry")];
    assert.deepEqual(top.map((li) => li.dataset.kind), ["index", "sync", "change"]);
    assert.deepEqual([...top[1].querySelectorAll("li.entry")].map((li) => li.dataset.kind), ["index"]);
    assert.equal(top[2].querySelector("li.entry"), null, "an index nested under a journal change that shares its id");
    assert.equal(top[2].querySelector(".took"), null, "a change says no time it took");
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

  test("it has no File access section and asks nothing of the deleted probes (a server without the route gives 404)", async (t) => {
    const fake = server();
    const { window } = await open(t, fake);
    await flush(window, 6);
    assert.equal(window.document.getElementById("file-access"), null);
    assert.equal(window.document.querySelector('a[href="#file-access"]'), null);
    assert.deepEqual(fake.urls().filter((url) => url.includes("file-access")), []);
  });
});

describe("Scheduled jobs, Snapshots, Server and Logs fold, and a folded one says what needs the owner", () => {
  const FOLDS = ["jobs", "snapshots", "server", "logs"];
  const opened = (document) => FOLDS.filter((name) => document.querySelector(`details.fold[data-fold="${name}"]`).open);
  const line = (document, name) => document.getElementById(`${name}-folded`).textContent;
  const summary = (document, name) => document.querySelector(`details.fold[data-fold="${name}"] > summary`);

  test("the first visit opens them folded, each with its one line from what the page already read", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 8);
    assert.deepEqual(opened(document), []);
    assert.match(line(document, "jobs"), /^next due .+ \(2026-09-22 01:13:20\); 1 failed last time$/);
    assert.match(line(document, "server"), /^Version 20260926-090000-abc1234; up \d/);
    assert.match(line(document, "snapshots"), /^Last taken 2026-09-26 09:00:00 \(.+\); 3\.0 GB all together$/);
    assert.equal(line(document, "logs"), "2 system logs; 3 runs' logs kept");
    // Now, Needs you, Sync and Recent activity never fold.
    for (const id of ["now", "needs", "sync", "timeline"]) assert.equal(document.querySelector(`#${id} details`), null, id);
  });

  test("a section opened stays open on the next visit; one folded again stays folded", async (t) => {
    const first = await open(t);
    await flush(first.window, 6);
    click(first.window, summary(first.document, "server"));
    click(first.window, summary(first.document, "jobs"));
    await flush(first.window, 2);
    assert.deepEqual(opened(first.document), ["jobs", "server"]);
    click(first.window, summary(first.document, "jobs"));
    await flush(first.window, 2);
    const kept = first.window.localStorage.getItem("tagpup_activity_open");
    assert.equal(kept, JSON.stringify(["server"]));

    const again = await loadApp("activity", { t, server: server(), url: "http://localhost:8090/activity/?every=5000",
                                              before: (window) => window.localStorage.setItem("tagpup_activity_open", kept) });
    await flush(again.window, 6);
    assert.deepEqual(opened(again.document), ["server"]);
  });

  test("a browser that will not remember: every section folded, each opens and folds all the same", async (t) => {
    const refuse = (window) => Object.defineProperty(window, "localStorage", { configurable: true,
      get: () => { throw new window.DOMException("The operation is insecure.", "SecurityError"); } });
    const { document, window, consoleErrors } = await loadApp("activity", { t, server: server(),
      url: "http://localhost:8090/activity/?every=5000", before: refuse });
    await flush(window, 8);
    assert.deepEqual(opened(document), []);
    assert.equal(line(document, "logs"), "2 system logs; 3 runs' logs kept");
    click(window, summary(document, "snapshots"));
    await flush(window, 2);
    assert.deepEqual(opened(document), ["snapshots"]);
    assert.equal(document.querySelectorAll("#snapshots-body tr.snapshot").length, 2);
    assert.deepEqual(consoleErrors, []);
  });

  test("a nav link to a folded section, and Logs for this run, unfold it", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 8);
    click(window, document.querySelector('nav a[href="#jobs"]'));
    click(window, document.querySelector('nav a[href="#now"]'));   // a section that never folds: nothing to unfold
    await flush(window, 2);
    assert.deepEqual(opened(document), ["jobs"]);
    click(window, document.querySelector("#timeline-body button.run-logs"));
    await flush(window, 6);
    assert.deepEqual(opened(document), ["jobs", "logs"]);
    assert.match(document.getElementById("log-records").textContent, /the run's line/);
  });
});

describe("Now is asked while the page is in view, and not while it is hidden", () => {
  test("hidden, it asks nothing; back, it asks at once", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake, 30);
    const now = () => asked(fake, "/api/activity/now");
    await new Promise((resolve) => setTimeout(resolve, 200));
    const visible = now();
    assert.ok(visible >= 3, `asked ${visible} times in 200 ms at every 30 ms`);

    let state = "hidden";
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new window.Event("visibilitychange"));
    await flush(window, 2);
    const hiddenFrom = now();
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(now(), hiddenFrom, "the page asked while hidden");

    state = "visible";
    document.dispatchEvent(new window.Event("visibilitychange"));
    await flush(window, 2);
    assert.ok(now() > hiddenFrom, "the page did not ask again once it was back in view");
  });

  test("a poll waits for the one before it to be answered", async (t) => {
    const fake = server("morning", { first: [["/api/activity/now", () => new Promise(() => {})]] });
    const { window } = await open(t, fake, 20);
    await new Promise((resolve) => setTimeout(resolve, 150));
    await flush(window, 2);
    assert.equal(asked(fake, "/api/activity/now"), 1);
  });
});

describe("a server that stops answering: the page asks less and less often, says so, and comes back at once", () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  test("answers saying success: false: each wait twice the last, the top line saying when it asks again; one answer puts it back", async (t) => {
    let refuse = false;
    const fake = server("morning", { first: [["/api/activity/now",
      () => (refuse ? { success: false, error: "database is locked" } : SERVED.morning.now)]] });
    const { document, window } = await open(t, fake, 20);
    await wait(100);
    const updated = document.getElementById("updated");
    assert.match(updated.textContent, /^Updated /);

    refuse = true;
    const from = asked(fake, "/api/activity/now");
    await wait(500);
    const whileDown = asked(fake, "/api/activity/now") - from;
    // Every 20 ms that is about 25 asks; waiting 40, 80, 160, 320 ms it is five at most.
    assert.ok(whileDown >= 2 && whileDown <= 6, `asked ${whileDown} times in 500 ms of no answer`);
    assert.match(updated.textContent, /^The server did not answer; asking again at \d\d:\d\d:\d\d\.$/);

    refuse = false;
    await polled(window, fake, "/api/activity/now", 1);
    assert.match(updated.textContent, /^Updated /, "the line still says the server is not answering");
    const back = asked(fake, "/api/activity/now");
    await wait(200);
    assert.ok(asked(fake, "/api/activity/now") - back >= 4, "one answer did not put it back to every 20 ms");
  });

  test("no answer at all (api.js asks once more after 1.5 s): the top line says so, and the next answer puts it back", async (t) => {
    let down = false;
    const fake = server("morning", { first: [["/api/activity/now",
      () => (down ? Promise.reject(new TypeError("Failed to fetch")) : SERVED.morning.now)]] });
    const { document, window } = await open(t, fake, 20);
    await wait(60);
    down = true;
    const updated = document.getElementById("updated");
    await settled(() => /did not answer/.test(updated.textContent), "the top line to say the server did not answer");
    down = false;
    await polled(window, fake, "/api/activity/now", 1);
    assert.match(updated.textContent, /^Updated /);
  });
});

describe("an update hands the server over: each poll says its own silence, and only its own", () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const status = (document, section) => document.getElementById(`${section}-status`).textContent;

  test("every route away, then back: Now's answer puts the top line back while a slower poll, still waiting, says so in its section", async (t) => {
    let down = false;
    const gated = (body) => () => (down ? { success: false, error: "the server is moving onto a new version" } : body);
    const fake = new FakeServer()
      .on("/api/activity/now", gated(SERVED.morning.now))
      .on("/api/activity/jobs", gated(SERVED.morning.jobs))
      .on("/api/activity/sync", gated(SYNC))
      .on("/api/activity/snapshots", gated(SNAPSHOTS))
      .on("/api/activity/server", gated(SERVER))
      .on("/api/activity/timeline", gated(SERVED.morning.timeline))
      .on("/api/activity/logs", SERVED.morning.logs);
    // Every 25 ms: the snapshots every second, a failed ask of them waiting two.
    const { document, window } = await open(t, fake, 25);
    await wait(150);
    const updated = document.getElementById("updated");
    assert.match(updated.textContent, /^Updated /);

    down = true;
    await settled(() => asked(fake, "/api/activity/snapshots") >= 2 && /did not answer/.test(updated.textContent),
                  "the snapshots asked while the server is away", 6000);
    down = false;
    await polled(window, fake, "/api/activity/now", 2);
    assert.match(updated.textContent, /^Updated /, "Now answers again, and the top line still says the server did not answer");
    assert.match(status(document, "snapshots"), /^Snapshots could not be refreshed; asking again at \d\d:\d\d:\d\d\.$/,
                 "the snapshots wait to be asked again, and their section does not say so");
    await settled(() => status(document, "snapshots") === "", "the snapshots' next answer to clear their line", 8000);
    assert.match(updated.textContent, /^Updated /);
  });

  test("one route failing is said in its own section, by name, and nowhere else", async (t) => {
    let refuse = true;
    const fake = server("morning", { first: [["/api/activity/timeline",
      () => (refuse ? { success: false, error: "database is locked" } : SERVED.morning.timeline)]] });
    const { document, window } = await open(t, fake, 20);
    await settled(() => /could not be refreshed/.test(status(document, "timeline")), "the timeline's section to say it failed");
    assert.match(status(document, "timeline"), /^Recent activity could not be refreshed; asking again at /);
    assert.match(document.getElementById("updated").textContent, /^Updated /, "one route failing said the whole server did not answer");
    for (const section of ["jobs", "sync", "snapshots", "server", "logs"]) assert.equal(status(document, section), "", section);
    refuse = false;
    await settled(() => status(document, "timeline") === "", "the timeline's next answer to clear its line");
    await flush(window, 2);
  });

  test("a section that fails to draw is the page's error, not the server's: logged, and asked again on time", async (t) => {
    const fake = server("morning", { first: [["/api/activity/server", { ...SERVER, background: 5 }]] });
    const { document, window, consoleErrors } = await open(t, fake, 20);
    await wait(100);
    const from = asked(fake, "/api/activity/server");
    await wait(1000);
    await flush(window, 2);
    // Every 200 ms: about five; backing off (400, 800 ms) it would be two.
    assert.ok(asked(fake, "/api/activity/server") - from >= 4, `asked ${asked(fake, "/api/activity/server") - from} times in 1 s`);
    assert.equal(status(document, "server"), "");
    assert.match(document.getElementById("updated").textContent, /^Updated /);
    assert.ok(consoleErrors.some((line) => /Could not draw/.test(String(line))), consoleErrors.join("\n"));
  });

  test("a server away for long: Now is asked once a minute at the longest, never less often", async (t) => {
    let refuse = false;
    const fake = server("morning", { first: [["/api/activity/now",
      () => (refuse ? { success: false, error: "database is locked" } : SERVED.morning.now)]] });
    // Now every 15 s: failing, it waits 30 s, then 60 s, then 60 s again.
    const { document, window } = await open(t, fake, 15000);
    await flush(window, 6);
    refuse = true;
    const { state } = pageExports(window, "web/activity/state.js");
    const { soon } = pageExports(window, "web/activity/poll.js");
    const now = state.pollers.find((each) => each.name === "now");
    const waits = [];
    for (let i = 0; i < 4; i += 1) {
      await soon("now");
      await flush(window, 2);
      waits.push(Math.round((now.nextAt - Date.now()) / 1000));
    }
    assert.deepEqual(waits, [30, 60, 60, 60]);
    assert.match(document.getElementById("updated").textContent, /^The server did not answer; asking again at /);
  });
});

describe("Run now's read of the jobs is the jobs poll's, never a second request beside it", () => {
  test("two jobs run in quick succession while the jobs are being read: one more read, after the one in flight", async (t) => {
    let quick = true;
    let release;
    let gate = Promise.resolve();
    let inFlight = 0;
    let most = 0;
    const readAt = [];
    const jobsRead = () => {
      readAt.push(Date.now());
      inFlight += 1;
      most = Math.max(most, inFlight);
      return (quick ? Promise.resolve() : gate).then(() => {
        inFlight -= 1;
        return SERVED.morning.jobs;
      });
    };
    const fake = server("morning", { first: [["/api/activity/jobs/run", { success: true, started: true, run_id: 13 }],
                                              ["/api/activity/jobs", jobsRead]] });
    // Every 10 ms: the jobs are asked every 100.
    const { document, window } = await open(t, fake, 10);
    await flush(window, 6);
    const reads = () => asked(fake, "/api/activity/jobs");

    // The next poll of the jobs hangs on a slow server.
    quick = false;
    gate = new Promise((resolve) => { release = resolve; });
    const before = reads();
    await settled(() => reads() > before, "the next poll of the jobs");
    assert.equal(inFlight, 1);
    const hanging = reads();

    const runFor = async (library) => {
      click(window, document.querySelector(`.job-card[data-job="sync"] tr.job[data-library="${library}"] button.run-now`));
      await flush(window, 1);
      click(window, document.querySelector("#jobs-body .confirm button.confirm-run"));
      await flush(window, 4);
    };
    await runFor("harbour");
    await runFor("jetty");
    assert.deepEqual(fake.calls.filter((call) => call.method === "POST").map((call) => call.body.library), ["harbour", "jetty"]);
    assert.equal(reads(), hanging, "Run now read the jobs beside the poll in flight");

    quick = true;
    const released = Date.now();
    release();
    await flush(window, 6);
    assert.equal(most, 1, "two reads of the jobs at once");
    assert.ok(reads() > hanging, "the jobs were not read again after the runs");
    assert.ok(readAt[hanging] - released < 100, "the read after the runs waited for the next poll");
    assert.match(document.getElementById("jobs-body").textContent, /Started \(run 13\)/);
  });
});

describe("Run now is asked first, and answered where it was clicked", () => {
  const posted = (fake) => fake.calls.filter((call) => call.method === "POST");
  const inJobs = (document, library) =>
    document.querySelector(`.job-card[data-job="sync"] tr.job[data-library="${library}"] button.run-now`);

  test("the first click asks; Cancel sends nothing; Run sends the job and the library", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 6);

    click(window, inJobs(document, "harbour"));
    await flush(window, 2);
    const confirm = document.querySelector(".confirm");
    assert.ok(confirm, "Run now did not ask first");
    assert.match(confirm.textContent, /Run sync for harbour now\?/);
    assert.equal(posted(fake).length, 0, "sent before it was confirmed");

    click(window, confirm.querySelector("button.cancel-run"));
    await flush(window, 2);
    assert.equal(document.querySelector(".confirm"), null);
    assert.equal(posted(fake).length, 0);

    click(window, inJobs(document, "harbour"));
    await flush(window, 2);
    click(window, document.querySelector(".confirm button.confirm-run"));
    await flush(window, 6);
    assert.equal(posted(fake).length, 1);
    assert.equal(posted(fake)[0].url, "/api/activity/jobs/run");
    assert.deepEqual(posted(fake)[0].body, { job: "sync", library: "harbour" });
    assert.match(document.getElementById("jobs-body").textContent, /Started \(run 13\)/);
    assert.doesNotMatch(document.getElementById("needs-body").textContent, /Started/);
  });

  test("Run it now in Needs you asks there and answers there, not in Scheduled jobs", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    const needs = document.getElementById("needs-body");
    click(window, needs.querySelector('.need[data-library="breakwater"] button.run-now'));
    await flush(window, 2);
    assert.ok(needs.querySelector(".confirm"), "Run it now did not ask first");
    assert.equal(document.querySelector("#jobs-body .confirm"), null, "asked in both places");
    click(window, needs.querySelector(".confirm button.confirm-run"));
    await flush(window, 6);
    assert.deepEqual(posted(fake).map((call) => call.body), [{ job: "sync", library: "breakwater" }]);
    assert.match(needs.textContent, /Started \(run 13\)/);
    assert.doesNotMatch(document.getElementById("jobs-body").textContent, /Started/);
  });

  test("a second click while the first is on its way sends nothing, and the first answer stays", async (t) => {
    let answer;
    const answered = new Promise((resolve) => { answer = resolve; });
    const fake = server("morning", { first: [["/api/activity/jobs/run", () => answered]] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);

    click(window, inJobs(document, "harbour"));
    await flush(window, 2);
    const confirm = document.querySelector(".confirm button.confirm-run");
    click(window, confirm);
    click(window, confirm);   // a double click: the button is gone from the page, not its listener
    await flush(window, 2);
    assert.equal(posted(fake).length, 1, "Run now was sent twice");
    assert.equal(inJobs(document, "harbour").disabled, true, "Run now is offered again while its run is being asked for");
    click(window, inJobs(document, "harbour"));
    await flush(window, 2);
    assert.equal(document.querySelector(".confirm"), null, "a disabled Run now asked again");

    answer({ success: true, started: true, run_id: 13, run: "job:harbour:13" });
    await flush(window, 8);
    assert.equal(posted(fake).length, 1);
    assert.match(document.getElementById("jobs-body").textContent, /Started \(run 13\)/);
    assert.equal(inJobs(document, "harbour").disabled, false);
  });

  test("a job not started says why", async (t) => {
    const fake = server("morning", { first: [["/api/activity/jobs/run", { success: false, started: false, why: "a run of it is under way already",
                                                                           error: "Not started: a run of it is under way already." }]] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    click(window, inJobs(document, "jetty"));
    await flush(window, 2);
    click(window, document.querySelector(".confirm button.confirm-run"));
    await flush(window, 6);
    assert.match(document.getElementById("jobs-body").textContent, /Not started: a run of it is under way already/);
  });
});

describe("Unload models now", () => {
  const LOADED = { loaded: ["CLIP", "faces"], in_use: false, last_used_seconds: 120, release_after_minutes: 5,
                   text: "Loaded: CLIP and faces. Last used 2 minutes ago. Let go after 5 minutes unused." };
  const NONE = { loaded: [], in_use: false, last_used_seconds: null, release_after_minutes: 5,
                 text: "Not loaded; loads when Suggest or indexing needs it." };
  const button = (document) => document.getElementById("unload-models");

  test("the line says what is loaded and the button unloads, asking the server once", async (t) => {
    let models = LOADED;
    const fake = server("quiet", { first: [
      ["/api/activity/server", () => ({ ...SERVER, models })],
      ["/api/activity/models/unload", () => { models = NONE; return { success: true, unloaded: ["CLIP", "faces"],
                                                                      message: "Unloaded CLIP and faces; the graphics card is free.", models: NONE }; }],
    ] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    const always = document.getElementById("server-body");
    assert.match(always.textContent, /Loaded: CLIP and faces\. Last used 2 minutes ago/);
    assert.equal(button(document).disabled, false);
    click(window, button(document));
    click(window, button(document));
    await flush(window, 8);
    assert.equal(fake.calls.filter((call) => call.method === "POST").length, 1);
    assert.match(always.textContent, /Unloaded CLIP and faces; the graphics card is free/);
    assert.match(always.textContent, /Not loaded; loads when Suggest or indexing needs it/);
    assert.equal(button(document).disabled, true, "offered with nothing loaded");
  });

  test("a refusal under a running Suggest is shown as the server said it", async (t) => {
    const fake = server("quiet", { first: [
      ["/api/activity/server", { ...SERVER, models: { ...LOADED, in_use: true } }],
      ["/api/activity/models/unload", { success: false, error: "In use by Suggest Regatta (harbour): they are let go when it ends.", unloaded: [] }],
    ] });
    const { document, window } = await open(t, fake);
    await flush(window, 6);
    click(window, button(document));
    await flush(window, 8);
    assert.match(document.getElementById("server-body").textContent, /In use by Suggest Regatta \(harbour\): they are let go when it ends\./);
    assert.equal(button(document).disabled, false);
  });
});

describe("Logs", () => {
  const INDEX_RUN = "index:harbour:20260921T152320-4242-1";
  const INDEX_LOG = "indexer-harbour-20260921T152320-4242-1.log";

  const RUN_LOGS = ["run_tests-20260921-150500.log", "indexer-lighthouse-20260921T151320-4242-2.log", INDEX_LOG];
  const logsIn = (document, id) => [...document.querySelectorAll(`#${id} button.log-file`)].map((button) => button.dataset.log);
  const choose = (window, id, value) => {
    const select = window.document.getElementById(id);
    select.value = value;
    select.dispatchEvent(new window.Event("change"));
  };

  test("the programs' own logs apart from the runs', each run's said in words, the web server's shown first", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    assert.deepEqual(logsIn(document, "log-system"), ["tagpup_web.log", "tagpup_mcp.log"]);
    assert.deepEqual(logsIn(document, "log-runs"), RUN_LOGS);
    const shown = document.querySelector('#log-system button[data-log="tagpup_web.log"]');
    assert.equal(shown.getAttribute("aria-pressed"), "true");
    assert.ok(shown.classList.contains("chosen"));
    // What each run's log is, in words: what ran, the library, when, its size; its file's name is only its title.
    const lighthouse = document.querySelector(`#log-runs button[data-log="${RUN_LOGS[1]}"]`);
    assert.match(lighthouse.textContent, /^Indexer \(lighthouse, 09-21 15:13\)last written \d{4}-\d\d-\d\d \d\d:\d\d, \d+ bytes$/);
    assert.equal(lighthouse.title, RUN_LOGS[1]);
    assert.match(document.querySelector(`#log-runs button[data-log="${RUN_LOGS[0]}"]`).textContent, /^Test run/);
    assert.equal(document.getElementById("log-kept").textContent, "The last 5 runs' logs of each library and kind are kept.");
    const options = (id) => [...document.getElementById(id).options].map((option) => [option.value, option.textContent]);
    assert.deepEqual(options("log-library"), [["", "All"], ["harbour", "harbour"], ["lighthouse", "lighthouse"]]);
    assert.deepEqual(options("log-kind"), [["", "All"], ["indexer", "Indexer runs"], ["run_tests", "Test runs"]]);
    const read = fake.urls().find((url) => url.startsWith("/api/activity/logs/tagpup_web.log?"));
    assert.ok(read, fake.urls().join("\n"));
    assert.match(read, /level=INFO/);
    assert.match(document.getElementById("log-records").textContent, /slow: GET \/api\/tags/);
    assert.equal(document.getElementById("log-raw").getAttribute("href"), "/api/activity/logs/tagpup_web.log/raw");
    assert.equal(document.getElementById("log-download").getAttribute("href"), "/api/activity/logs/tagpup_web.log/download");
  });

  test("the runs' logs filtered by library and kind from the one listing, a filter matching nothing saying so", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    const asked = fake.urls().length;
    choose(window, "log-library", "lighthouse");
    assert.deepEqual(logsIn(document, "log-runs"), [RUN_LOGS[1]]);
    choose(window, "log-kind", "run_tests");
    assert.deepEqual(logsIn(document, "log-runs"), []);
    assert.equal(document.getElementById("log-runs").textContent, "No test runs' logs of lighthouse are kept.");
    choose(window, "log-library", "");
    assert.deepEqual(logsIn(document, "log-runs"), [RUN_LOGS[0]]);
    // The programs' own logs and the log shown stay as they were; a filter asks the server nothing.
    assert.deepEqual(logsIn(document, "log-system"), ["tagpup_web.log", "tagpup_mcp.log"]);
    assert.equal(document.querySelector("#log-system button.chosen").dataset.log, "tagpup_web.log");
    await flush(window, 2);
    assert.deepEqual(fake.urls().slice(asked).filter((url) => url.includes("/api/activity/logs")), []);
  });

  test("a run's log removed between the listing and the read says why where its lines would be", async (t) => {
    const removed = { success: false, removed: true,
                      error: "The log of this run was removed: the last 5 runs' logs of each library and kind are kept." };
    const fake = server().first(`/api/activity/logs/${RUN_LOGS[1]}?`, removed, { status: 404 });
    const { document, window } = await open(t, fake, 20);
    await flush(window, 8);
    click(window, document.querySelector('details.fold[data-fold="logs"] > summary'));
    const follow = document.getElementById("log-follow");
    follow.checked = true;
    follow.dispatchEvent(new window.Event("change"));
    click(window, document.querySelector(`#log-runs button[data-log="${RUN_LOGS[1]}"]`));
    await flush(window, 6);
    assert.equal(document.getElementById("log-records").textContent, removed.error);
    assert.equal(document.getElementById("log-older").disabled, true);
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.equal(fake.urls().filter((url) => url.includes(`${RUN_LOGS[1]}?`) && url.includes("after=")).length, 0,
                 "a log that is not there was followed");
    click(window, document.querySelector('#log-system button[data-log="tagpup_web.log"]'));
    await flush(window, 6);
    assert.match(document.getElementById("log-records").textContent, /slow: GET \/api\/tags/);
  });

  test("Logs for this run of a run that wrote no log says so, in the server's words", async (t) => {
    const waited = "indexer-harbour-20260921T154720-4242-3.log";
    const fake = server().first(`/api/activity/logs/${waited}?`, SERVED.morning.missing_log, { status: 404 });
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    click(window, document.querySelector(`#timeline-body button.run-logs[data-log="${waited}"]`));
    await flush(window, 8);
    assert.equal(document.getElementById("log-records").textContent, "This run has written no log of its own (yet).");
    assert.equal(document.getElementById("log-run").classList.contains("hidden"), false);
  });

  test("a data folder with no logs yet says so, in each list and in the folded line", async (t) => {
    const { document, window } = await open(t, server("quiet"));
    await flush(window, 8);
    assert.equal(document.getElementById("logs-folded").textContent, "No logs yet.");
    assert.equal(document.getElementById("log-system").textContent, "No program has written a log yet.");
    assert.equal(document.getElementById("log-runs").textContent, "No run has written a log yet.");
    assert.equal(document.getElementById("log-records").textContent, "Choose a log.");
  });

  test("Logs for this run reads only that run's lines, INFO and worse, in its log", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    const link = document.querySelector(`#timeline-body button.run-logs[data-run="${INDEX_RUN}"][data-log="${INDEX_LOG}"]`);
    assert.ok(link, "no Logs for this run beside the run of the indexer");
    click(window, link);
    await flush(window, 6);
    const read = fake.urls().filter((url) => url.startsWith(`/api/activity/logs/${INDEX_LOG}?`)).at(-1);
    assert.ok(read, fake.urls().join("\n"));
    assert.match(read, /run=index%3Aharbour%3A20260921T152320-4242-1/);
    assert.match(read, /level=INFO/);
    assert.equal(document.getElementById("log-run").classList.contains("hidden"), false);
    assert.match(document.getElementById("log-records").textContent, /the run's line/);
    assert.equal(document.querySelector("#log-runs button.chosen").dataset.log, INDEX_LOG, "the run's log is the one shown");
    click(window, document.getElementById("log-run-clear"));
    await flush(window, 6);
    assert.doesNotMatch(fake.urls().at(-1), /run=/);
  });

  test("a run's log is the one the server names, never one the page spells", async (t) => {
    // The indexer's log is named from the run's tag, where a library called "Harbour Photos"
    // is Harbour_Photos (tagpup.logs.run_log); a page spelling it from the library's name
    // asked for a file there is not.
    const named = "indexer-Harbour_Photos-20260926T091000-4242-1.log";
    const timeline = { limit: 50, more: false, entries: [
      { kind: "index", library: "Harbour Photos", time: "2026-09-26 09:10:00", finished: "2026-09-26 09:20:00",
        seconds: 600, what: "index of Regatta 2019", outcome: "completed", counts: { folders: 1 }, id: null,
        run: "index:Harbour_Photos:20260926T091000-4242-1", error: null, logs: [named, "tagpup_web.log"] },
    ] };
    const fake = server("quiet", { first: [["/api/activity/timeline", timeline]] });
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    const links = [...document.querySelectorAll("#timeline-body button.run-logs")];
    assert.equal(links.length, 2, "a link for each log the run wrote in");
    click(window, links[0]);
    await flush(window, 6);
    const read = fake.urls().filter((url) => url.includes("/api/activity/logs/") && url.includes("run=")).at(-1);
    assert.ok(read.startsWith(`/api/activity/logs/${named}?`), read);
    click(window, links[1]);
    await flush(window, 6);
    const second = fake.urls().filter((url) => url.includes("/api/activity/logs/") && url.includes("run=")).at(-1);
    assert.ok(second.startsWith("/api/activity/logs/tagpup_web.log?"), second);
  });

  test("Follow asks only for what was written since, and Older for what came before", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake, 30);
    await flush(window, 8);
    click(window, document.querySelector('details.fold[data-fold="logs"] > summary'));
    const follow = document.getElementById("log-follow");
    follow.checked = true;
    follow.dispatchEvent(new window.Event("change"));
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.ok(fake.urls().some((url) => /\/api\/activity\/logs\/tagpup_web\.log\?.*after=20480/.test(url)), fake.urls().join("\n"));
    click(window, document.getElementById("log-older"));
    await flush(window, 4);
    assert.ok(fake.urls().some((url) => /before=100/.test(url)), "Older did not read from where the last read began");
  });

  test("Follow draws what was written since, with nothing logged as broken", async (t) => {
    const fake = server("morning", { first: [["/api/activity/logs/", (url) => (url.includes("after=")
      ? { ...records(["a line written since"]), start: 20480, end: 20600 }
      : records(["the first line"]))]] });
    const { document, window, consoleErrors } = await open(t, fake, 30);
    await flush(window, 8);
    click(window, document.querySelector('details.fold[data-fold="logs"] > summary'));
    const follow = document.getElementById("log-follow");
    follow.checked = true;
    follow.dispatchEvent(new window.Event("change"));
    await settled(() => /a line written since/.test(document.body.textContent), "the followed line to be drawn");
    assert.deepEqual(consoleErrors, [], "Follow's answer was thrown away");
  });

  test("Follow reads nothing while Logs is folded, and follows again once it is opened", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake, 20);
    await flush(window, 8);
    const followed = () => fake.urls().filter((url) => /\/api\/activity\/logs\/tagpup_web\.log\?.*after=/.test(url)).length;
    const follow = document.getElementById("log-follow");
    follow.checked = true;
    follow.dispatchEvent(new window.Event("change"));
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(followed(), 0, "a folded Logs was read every few seconds");
    const logs = document.querySelector('details.fold[data-fold="logs"] > summary');
    click(window, logs);
    await settled(() => followed() > 0, "Follow to read once Logs is opened");
    click(window, logs);
    await flush(window, 4);
    const folded = followed();
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(followed(), folded, "folded again, Logs was still read");
  });
});
