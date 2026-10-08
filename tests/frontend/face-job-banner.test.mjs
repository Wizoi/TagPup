/**
 * #907: a bulk assignment of faces that stopped part-way is offered when a library is opened (web/common/face-job-banner.js),
 * with Resume and Let go; both apps wire it in main.js. The server's job is tagpup.jobs.face_assignments.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, click } from "./harness.mjs";

afterEach(() => closeAllApps());

const STOPPED = { job: 7, op: "name", state: "abandoned", total: 60, done: 25, resumable: true, cancelling: false };

function serverWith(current) {
  return new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/faces/job/current", { success: true, job: current })
    .on("/api/faces/job/resume", { success: true, job: { ...STOPPED, state: "running", done: 25 } })
    .on("/api/faces/job/status", { success: true, job: { ...STOPPED, state: "done", done: 60 } })
    .on("/api/faces/job/cancel", { success: true, job: null })
    .on("/api/unmatched-faces/people", [])
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", []);
}

const banner = (document) => document.getElementById("face-job-banner");
const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

describe("a stopped assignment of faces", () => {
  test("is offered with how many faces remain, and Resume carries it on until it is done", async (t) => {
    const server = serverWith(STOPPED);
    const { window, document } = await loadApp("tagtuner", { server, url: "http://localhost:8080/photo_index/", t });
    await wait(window, 150);
    assert.ok(banner(document), "a stopped job was not shown");
    assert.match(banner(document).textContent, /35 of 60 faces of an assignment remain/);
    const resume = [...banner(document).querySelectorAll("button")].find((b) => b.textContent === "Resume");
    click(window, resume);
    await wait(window, 150);
    assert.equal(server.lastBody("/api/faces/job/resume").job, 7);
    await wait(window, 2300);
    assert.equal(banner(document), null, "the banner stayed after the job was done");
  });

  test("can be let go", async (t) => {
    const server = serverWith(STOPPED);
    const { window, document } = await loadApp("tagtuner", { server, url: "http://localhost:8080/photo_index/", t });
    await wait(window, 150);
    click(window, [...banner(document).querySelectorAll("button")].find((b) => b.textContent === "Let go"));
    await wait(window, 150);
    assert.deepEqual(server.lastBody("/api/faces/job/cancel"), { job: 7, let_go: true });
    assert.equal(banner(document), null);
  });

  test("an undone one is not resumable: the banner says so and offers only Let go", async (t) => {
    const server = serverWith({ ...STOPPED, undone: true, resumable: false });
    const { window, document } = await loadApp("tagtuner", { server, url: "http://localhost:8080/photo_index/", t });
    await wait(window, 150);
    assert.match(banner(document).textContent, /was undone/);
    assert.deepEqual([...banner(document).querySelectorAll("button")].map((b) => b.textContent), ["Let go"]);
  });

  test("nothing is shown when there is none", async (t) => {
    const { window, document } = await loadApp("tagtuner", { server: serverWith(null), url: "http://localhost:8080/photo_index/", t });
    await wait(window, 150);
    assert.equal(banner(document), null);
  });
});
