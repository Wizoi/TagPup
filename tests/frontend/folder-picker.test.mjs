/**
 * Choosing folders to index.
 *
 * The native folder dialog returns a single path and cannot multi-select, so adding a
 * season of shoots meant opening it once per folder and waiting for each to finish
 * before the next could be started -- the server answered a second folder with 409.
 * The picker browses to a parent, lists its subfolders with image counts, and queues
 * the ticked ones in one request.
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const PARENT = "D:\\Training\\Pictures\\Cross Country";

function subfolderReply(folders, ownImages = 0) {
  return {
    parent: PARENT,
    own_images: ownImages,
    has_subfolders: folders.length > 0,
    folders: folders.map((f) => ({
      path: `${PARENT}\\${f.name}`,
      name: f.name,
      images: f.images,
      indexed: f.indexed || 0,
      has_subfolders: false,
    })),
  };
}

/** Open the picker and load a parent's subfolders into it. */
async function openPickerWith(t, folders, ownImages = 0) {
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/browse-folder", { path: PARENT })
    .on("/api/folder/subfolders", subfolderReply(folders, ownImages))
    .on("/api/folder/index-start", { success: true, queued: [], already_queued: [], pending: 0 });

  const { window, document } = await loadApp("tagtuner", { server, t });
  document.getElementById("btn-add-folder").click();
  document.getElementById("btn-folder-picker-browse").click();
  await new Promise((r) => window.setTimeout(r, 20));
  return { window, document, server };
}

function rows(document) {
  return [...document.querySelectorAll("#folder-picker-list .folder-picker-row")];
}

function checkboxes(document) {
  return rows(document).map((r) => r.querySelector("input[type=checkbox]"));
}

afterEach(() => closeAllApps());

describe("opening the picker", () => {
  test("the Add folders button opens it", async (t) => {
    const server = new FakeServer().on("/api/folder/index-active", {
      active: [], queued: [], busy: false, remaining: 0,
    });
    const { document } = await loadApp("tagtuner", { server, t });
    const modal = document.getElementById("folder-picker-modal");
    assert.ok(modal.classList.contains("hidden"), "picker was open before it was asked for");
    document.getElementById("btn-add-folder").click();
    assert.ok(!modal.classList.contains("hidden"), "the button did not open the picker");
  });

  test("it opens empty, with nothing queued by accident", async (t) => {
    const server = new FakeServer().on("/api/folder/index-active", {
      active: [], queued: [], busy: false, remaining: 0,
    });
    const { document } = await loadApp("tagtuner", { server, t });
    document.getElementById("btn-add-folder").click();
    assert.equal(rows(document).length, 0);
    assert.ok(document.getElementById("btn-folder-picker-queue").disabled);
  });

  test("Cancel closes it without queueing anything", async (t) => {
    const { document, server } = await openPickerWith(t, [{ name: "a", images: 3 }]);
    document.getElementById("btn-folder-picker-cancel").click();
    assert.ok(document.getElementById("folder-picker-modal").classList.contains("hidden"));
    assert.equal(server.urls().filter((u) => u.includes("index-start")).length, 0);
  });
});

describe("listing a parent's subfolders", () => {
  test("every subfolder becomes a row", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "2024-10 Twilight", images: 47 },
      { name: "2024-11 Westside", images: 37 },
      { name: "2025-09 Seaside", images: 57 },
    ]);
    assert.equal(rows(document).length, 3);
  });

  test("each row says how many images the folder holds", async (t) => {
    const { document } = await openPickerWith(t, [{ name: "shoot", images: 47 }]);
    assert.match(rows(document)[0].textContent, /47 image/);
  });

  test("a folder already in the index says so", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "done", images: 20, indexed: 20 },
    ]);
    assert.match(rows(document)[0].textContent, /already indexed/);
  });

  test("a partly indexed folder reports both numbers", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "partial", images: 20, indexed: 8 },
    ]);
    assert.match(rows(document)[0].textContent, /20 image\(s\), 8 already indexed/);
  });

  test("the parent itself is offered when it holds images directly", async (t) => {
    const { document } = await openPickerWith(t, [{ name: "sub", images: 2 }], 9);
    assert.match(rows(document)[0].textContent, /this folder itself/);
  });

  test("a parent of only folders is not offered as a row of its own", async (t) => {
    const { document } = await openPickerWith(t, [{ name: "sub", images: 2 }], 0);
    assert.ok(!rows(document).some((r) => /this folder itself/.test(r.textContent)));
  });

  test("a leaf folder with no subfolders offers itself", async (t) => {
    const { document } = await openPickerWith(t, [], 12);
    assert.equal(rows(document).length, 1);
    assert.match(rows(document)[0].textContent, /this folder itself/);
  });
});

describe("choosing which to index", () => {
  test("folders with images start ticked", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "a", images: 5 },
      { name: "b", images: 3 },
    ]);
    assert.deepEqual(checkboxes(document).map((c) => c.checked), [true, true]);
  });

  test("a folder already fully indexed does not start ticked", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "new", images: 5 },
      { name: "done", images: 9, indexed: 9 },
    ]);
    assert.deepEqual(checkboxes(document).map((c) => c.checked), [true, false]);
  });

  test("an empty folder does not start ticked", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "empty", images: 0 },
      { name: "full", images: 4 },
    ]);
    assert.deepEqual(checkboxes(document).map((c) => c.checked), [false, true]);
  });

  test("Select none clears everything", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "a", images: 5 },
      { name: "b", images: 3 },
    ]);
    document.getElementById("btn-folder-select-none").click();
    assert.ok(checkboxes(document).every((c) => !c.checked));
    assert.ok(document.getElementById("btn-folder-picker-queue").disabled);
  });

  test("Select all takes every folder that has images", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "a", images: 5 },
      { name: "empty", images: 0 },
      { name: "done", images: 9, indexed: 9 },
    ]);
    document.getElementById("btn-folder-select-none").click();
    document.getElementById("btn-folder-select-all").click();
    assert.deepEqual(checkboxes(document).map((c) => c.checked), [true, false, true]);
  });

  test("unticking a row removes it from the count", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "a", images: 5 },
      { name: "b", images: 3 },
    ]);
    const box = checkboxes(document)[0];
    box.checked = false;
    box.dispatchEvent(new document.defaultView.Event("change"));
    assert.match(document.getElementById("folder-picker-selection").textContent, /1 folder/);
  });

  test("the footer totals the folders and their images", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "a", images: 5 },
      { name: "b", images: 3 },
    ]);
    assert.match(
      document.getElementById("folder-picker-selection").textContent,
      /2 folder\(s\), 8 image\(s\)/
    );
  });

  test("hiding already-indexed folders removes their rows", async (t) => {
    const { document } = await openPickerWith(t, [
      { name: "new", images: 5 },
      { name: "done", images: 9, indexed: 9 },
    ]);
    const toggle = document.getElementById("folder-picker-hide-indexed");
    toggle.checked = true;
    toggle.dispatchEvent(new document.defaultView.Event("change"));
    assert.equal(rows(document).length, 1);
    assert.match(rows(document)[0].textContent, /new/);
  });
});

describe("queueing the selection", () => {
  test("every ticked folder goes in one request", async (t) => {
    const { document, server, window } = await openPickerWith(t, [
      { name: "a", images: 5 },
      { name: "b", images: 3 },
      { name: "c", images: 4 },
    ]);
    document.getElementById("btn-folder-picker-queue").click();
    await new Promise((r) => window.setTimeout(r, 20));

    const starts = server.calls.filter((c) => c.url.includes("index-start"));
    assert.equal(starts.length, 1, `expected one request, got ${starts.length}`);
    assert.deepEqual(starts[0].body.folder_paths, [
      `${PARENT}\\a`, `${PARENT}\\b`, `${PARENT}\\c`,
    ]);
  });

  test("unticked folders are left out", async (t) => {
    const { document, server, window } = await openPickerWith(t, [
      { name: "a", images: 5 },
      { name: "b", images: 3 },
    ]);
    const box = checkboxes(document)[1];
    box.checked = false;
    box.dispatchEvent(new document.defaultView.Event("change"));
    document.getElementById("btn-folder-picker-queue").click();
    await new Promise((r) => window.setTimeout(r, 20));

    assert.deepEqual(server.lastBody("index-start").folder_paths, [`${PARENT}\\a`]);
  });

  test("the picker closes once the folders are queued", async (t) => {
    const { document, window } = await openPickerWith(t, [{ name: "a", images: 5 }]);
    document.getElementById("btn-folder-picker-queue").click();
    await new Promise((r) => window.setTimeout(r, 20));
    assert.ok(document.getElementById("folder-picker-modal").classList.contains("hidden"));
  });

  test("the queue button stays disabled while nothing is ticked", async (t) => {
    const { document } = await openPickerWith(t, [{ name: "a", images: 5 }]);
    document.getElementById("btn-folder-select-none").click();
    assert.ok(document.getElementById("btn-folder-picker-queue").disabled);
  });
});

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

  test("Add folders stays usable while something is indexing", async (t) => {
    // Folders queue rather than collide, so disabling the button would now be a
    // lie -- and a disabled control with no explanation is what read as broken.
    const server = new FakeServer().on("/api/folder/index-active", {
      active: [{ folder: "D:\\x\\running", name: "running", percent: 10, message: "working" }],
      queued: [],
      busy: true,
      remaining: 1,
    });
    const { document, window } = await loadApp("tagtuner", { server, t });
    await new Promise((r) => window.setTimeout(r, 20));

    assert.equal(document.getElementById("btn-add-folder").disabled, false);
  });
});
