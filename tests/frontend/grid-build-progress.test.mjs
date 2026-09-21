/**
 * Saying how far along a grid is, instead of sitting blank for a minute.
 *
 * Opening Unknown Faces on a real library is the better part of a minute, nearly all
 * of it grouping a hundred thousand candidates. The panel said "Loading faces..." for
 * the whole of it, which is indistinguishable from a hang.
 *
 * There is nothing to stream -- it is one request that takes a minute -- so the request
 * doing the work publishes its progress and the page polls for it on the side while its
 * own fetch is still in flight. These tests drive that against a server that holds the
 * grid back until the test lets it go.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const NAME = "Unknown Faces";

function face(id) {
  return {
    id,
    photo_path: `D:\\meets\\${id}.jpg`,
    filename: `${id}.jpg`,
    box: [10, 10, 40, 40],
    prob: 0.99,
    mtime: 0,
    year: 2025,
    similarity: 0.9,
    person_similarity: 0.9,
    cluster_id: 4,
    cluster_name: "Cluster 4",
    other_names: [],
    suggested_name: null,
    suggested_similarity: 0,
    suggestion_strength: null,
  };
}

/**
 * A server that answers person-matches only when told to, and meanwhile reports
 * build progress -- the shape of the real thing, where one slow request runs while
 * status requests are answered on another thread.
 */
class SlowGridServer extends FakeServer {
  constructor(statuses) {
    super();
    this.statuses = [...statuses];
    this.statusCalls = 0;
    this.release = null;
  }

  install(window) {
    super.install(window);
    const inner = window.fetch;
    const self = this;
    window.fetch = function (input, init) {
      const url = typeof input === "string" ? input : String(input && input.url);

      if (url.includes("/api/unmatched-faces/build-status")) {
        self.statusCalls++;
        const next =
          self.statuses.length > 1 ? self.statuses.shift() : self.statuses[0];
        self.calls.push({ url, method: "GET" });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(next) });
      }

      if (url.includes("/api/unmatched-faces/person-matches")) {
        self.calls.push({ url, method: "GET" });
        const body = {
          faces: [face(1), face(2)],
          total_count: 2,
          unclustered_total: 0,
          unclustered_shown: 0,
          has_more: false,
        };
        // Held until the test releases it, the way a minute-long build holds.
        return new Promise((resolve) => {
          self.release = () =>
            resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
        });
      }

      return inner(input, init);
    };
    return this;
  }
}

function building(percent, message) {
  return { name: NAME, active: true, percent, stage: "grouping", message };
}

async function openGrid(t, statuses) {
  const server = new SlowGridServer(statuses)
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: NAME, count: 2, unit: "face" }])
    .on("/api/people-with-counts", [])
    .on("/api/people", []);

  const { window, document } = await loadApp("tagtuner", {
    server,
    url: `http://localhost:8080/photo_index/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
    t,
  });
  return { window, document, server };
}

const wait = (window, ms) => new Promise((r) => window.setTimeout(r, ms));

describe("the grid says how far along it is", () => {
  afterEach(() => closeAllApps());

  test("a bar and a message appear while the server is still working", async (t) => {
    const { window, document, server } = await openGrid(t, [
      building(12, "Grouping 102,483 faces that look alike"),
      building(48, "Grouping 102,483 faces that look alike"),
    ]);

    await wait(window, 1000);

    const bar = document.getElementById("grid-build-progress");
    assert.ok(bar, "there is no progress element in the page");
    assert.ok(
      !bar.classList.contains("hidden"),
      "the panel was still blank while the server was working",
    );
    assert.ok(
      server.statusCalls > 0,
      "the page never asked the server how far along it was",
    );
    assert.match(
      document.getElementById("grid-build-text").textContent,
      /Grouping 102,483 faces/,
      "the message from the server was not shown",
    );

    server.release();
    await wait(window, 150);
  });

  test("the bar tracks the percentage the server reports", async (t) => {
    const { window, document, server } = await openGrid(t, [
      building(12, "Grouping"),
      building(48, "Grouping"),
      building(90, "Grouping"),
    ]);

    await wait(window, 900);
    const first = document.getElementById("grid-build-bar").style.width;
    await wait(window, 1500);
    const later = document.getElementById("grid-build-bar").style.width;

    assert.equal(first, "12%", `expected the first reading, got ${first}`);
    assert.ok(
      parseInt(later, 10) > parseInt(first, 10),
      `the bar did not advance: ${first} then ${later}`,
    );

    server.release();
    await wait(window, 150);
  });

  test("it goes away when the grid arrives", async (t) => {
    const { window, document, server } = await openGrid(t, [building(30, "Grouping")]);
    await wait(window, 900);
    assert.ok(!document.getElementById("grid-build-progress").classList.contains("hidden"));

    server.release();
    await wait(window, 250);

    assert.ok(
      document.getElementById("grid-build-progress").classList.contains("hidden"),
      "the progress bar was left on screen after the faces arrived",
    );
    assert.equal(
      document.querySelectorAll("#matching-faces-grid [data-face-id]").length,
      2,
      "the faces did not render once the request finished",
    );
  });

  test("it stops asking once the grid has arrived", async (t) => {
    const { window, document, server } = await openGrid(t, [building(30, "Grouping")]);
    await wait(window, 900);

    server.release();
    await wait(window, 200);
    const asked = server.statusCalls;
    await wait(window, 1600);

    assert.equal(
      server.statusCalls,
      asked,
      "the page kept polling for progress after the grid had loaded",
    );
  });

  test("a cached grid never shows a bar", async (t) => {
    // The server says nothing is being built, because nothing is: the answer was
    // cached. Clicking between people should not flash a progress bar.
    const { window, document, server } = await openGrid(t, [
      { name: NAME, active: false, percent: 0 },
    ]);

    server.release();
    await wait(window, 1200);

    assert.ok(
      document.getElementById("grid-build-progress").classList.contains("hidden"),
      "a bar appeared for a grid that was never built",
    );
  });
});
