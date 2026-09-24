/**
 * A face's badge from one grid does not follow it into the next.
 *
 * Each card records its face's best guess, and the name box, the "Assign to their
 * matches" button and the by-badge split all read those records. They were only ever
 * removed with the card, never when a new grid was drawn, so a face shown with a badge
 * in one grid and without one in the next still filled the name box from the badge
 * nobody could see.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps } from "./harness.mjs";

function face(id, suggestion) {
  return {
    id, photo_path: `D:\\xc\\photo${id}.jpg`, filename: `photo${id}.jpg`,
    box: [10, 10, 40, 40], prob: 0.99, mtime: 0, year: 2026, similarity: 0.0,
    cluster_id: -1, cluster_name: "Unclustered", other_names: [],
    suggested_name: suggestion, suggested_similarity: suggestion ? 0.93 : 0,
    suggestion_strength: suggestion ? "likely" : null,
  };
}

function grid(faces) {
  return {
    faces, total_count: faces.length, unclustered_total: faces.length,
    unclustered_shown: faces.length, has_more: false, page: 1, limit: -1,
  };
}

describe("suggestions belong to the grid they were drawn with", () => {
  afterEach(() => closeAllApps());

  test("a badge from the last grid does not fill the name box in this one", async (t) => {
    const server = new FakeServer()
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/unmatched-faces/people", [
        { name: "Unknown Faces", count: 1, unit: "face" },
        { name: "Kira Bao", count: 1, unit: "face" },
      ])
      .on("/api/unmatched-faces/person-matches", (url) =>
        decodeURIComponent(url).includes("Kira Bao") ? grid([face(1, null)]) : grid([face(1, "Rory Olwen")]))
      .on("/api/people-with-counts", [])
      .on("/api/people", ["Rory Olwen", "Kira Bao"]);
    const { window, document } = await loadApp("tagtuner", {
      t, server,
      url: "http://localhost:8080/kr-track/?mode=unmatched-faces&person=Unknown%20Faces",
    });
    await new Promise((r) => window.setTimeout(r, 140));
    assert.ok(document.querySelector("#matching-faces-grid .face-match-item"), "fixture: first grid");

    const kira = [...document.querySelectorAll("#photo-list li")].find((li) => li.textContent.includes("Kira Bao"));
    assert.ok(kira, "fixture: the sidebar lists the second grid");
    click(window, kira);
    await new Promise((r) => window.setTimeout(r, 160));

    const input = document.getElementById("input-reassign-name");
    input.value = "";
    const card = document.querySelector('#matching-faces-grid [data-face-id="1"]');
    assert.ok(card, "fixture: the face is in the second grid");
    click(window, card);
    await new Promise((r) => window.setTimeout(r, 40));
    assert.notEqual(input.value, "Rory Olwen", "a badge from the previous grid filled the box");
  });
});
