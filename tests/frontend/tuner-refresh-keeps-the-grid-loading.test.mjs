/**
 * An exclude, a restore or a finished index does not cancel the grid being loaded.
 *
 * Each refreshed the sidebar through fetchPhotos, which also aborts the panel's
 * request. Exclude some faces, click another person before the exclude returns, and
 * that person's grid was cancelled when it did: the abort was swallowed and the panel
 * sat on "Loading faces..." for good.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps } from "./harness.mjs";

function face(id) {
  return {
    id, photo_path: `D:\\xc\\photo${id}.jpg`, filename: `photo${id}.jpg`,
    box: [10, 10, 40, 40], prob: 0.99, mtime: 0, year: 2026, similarity: 0.0,
    cluster_id: -1, cluster_name: "Unclustered", other_names: [],
    suggested_name: null, suggested_similarity: 0, suggestion_strength: null,
  };
}

function grid(faces) {
  return {
    faces, total_count: faces.length, unclustered_total: faces.length,
    unclustered_shown: faces.length, has_more: false, page: 1, limit: -1,
  };
}

function gate() {
  let open;
  const shut = new Promise((r) => { open = r; });
  return { shut, open };
}

describe("a refresh does not cancel the grid being loaded", () => {
  afterEach(() => closeAllApps());

  test("an exclude returning while another grid loads leaves that grid to arrive", async (t) => {
    const excludeGate = gate();
    const kiraGate = gate();
    const server = new FakeServer()
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/unmatched-faces/people", [
        { name: "Unknown Faces", count: 2, unit: "face" },
        { name: "Kira Bao", count: 1, unit: "face" },
      ])
      .on("/api/unmatched-faces/person-matches", (url) =>
        decodeURIComponent(url).includes("Kira Bao")
          ? kiraGate.shut.then(() => grid([face(9)]))
          : grid([face(1), face(2)]))
      .on("/api/faces/exclude", () => excludeGate.shut.then(() => ({ success: true, excluded: 1 })))
      .on("/api/people-with-counts", [])
      .on("/api/people", []);
    const { window, document } = await loadApp("tagtuner", {
      t, server,
      url: "http://localhost:8080/kr-track/?mode=unmatched-faces&person=Unknown%20Faces",
    });
    await new Promise((r) => window.setTimeout(r, 140));
    window.confirm = () => true;

    click(window, document.querySelector('#matching-faces-grid [data-face-id="1"]'));
    click(window, document.getElementById("btn-exclude-selected"));
    await new Promise((r) => window.setTimeout(r, 60));
    const choice = document.querySelector("#exclude-reason-choices button");
    if (choice) click(window, choice);
    await new Promise((r) => window.setTimeout(r, 40));

    // Another person, before the exclude has answered.
    const kira = [...document.querySelectorAll("#photo-list li")].find((li) => li.textContent.includes("Kira Bao"));
    click(window, kira);
    await new Promise((r) => window.setTimeout(r, 40));

    excludeGate.open();
    await new Promise((r) => window.setTimeout(r, 60));
    kiraGate.open();
    await new Promise((r) => window.setTimeout(r, 160));

    assert.ok(document.querySelector('#matching-faces-grid [data-face-id="9"]'),
      "the grid being loaded never arrived");
    // Once. The refresh cancelled the request and the sidebar asked for it again from
    // the start -- on a grid that takes the server fifty seconds to build, all of it.
    const asked = server.calls.filter((c) =>
      c.url.includes("person-matches") && decodeURIComponent(c.url).includes("Kira Bao"));
    assert.equal(asked.length, 1, "the grid being loaded was cancelled and started again");
  });
});
