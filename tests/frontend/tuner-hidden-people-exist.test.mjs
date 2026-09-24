/**
 * Assigning a face to someone hidden from autocomplete does not offer to create them.
 *
 * The page asked the autocomplete list whether a name exists, and that list leaves out
 * people hidden from autocomplete -- so naming one of them asked "not currently in the
 * database, create a new person?", and the New Person dialog let a duplicate through.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps } from "./harness.mjs";

describe("a hidden person still exists", () => {
  afterEach(() => closeAllApps());

  test("assigning to them asks to assign, not to create", async (t) => {
    const faces = [{
      id: 1, photo_path: "D:\\xc\\p1.jpg", filename: "p1.jpg", box: [1, 1, 5, 5], prob: 0.99,
      mtime: 0, year: 2026, similarity: 0, cluster_id: -1, cluster_name: "Unclustered",
      other_names: [], suggested_name: null, suggested_similarity: 0, suggestion_strength: null,
    }];
    const server = new FakeServer()
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/unmatched-faces/people", [{ name: "Unknown Faces", count: 1, unit: "face" }])
      .on("/api/unmatched-faces/person-matches", {
        faces, total_count: 1, unclustered_total: 1, unclustered_shown: 1, has_more: false,
      })
      .on("/api/people-with-counts", [])
      .on("/api/people?include_hidden=1", ["Rowan Thackeray"])
      .on("/api/people", [])
      .on("/api/faces/match-bulk", { success: true, matched_ids: [1] });
    const { window, document } = await loadApp("tagtuner", {
      t, server, url: "http://localhost:8080/kr-track/?mode=unmatched-faces&person=Unknown%20Faces",
    });
    await new Promise((r) => window.setTimeout(r, 140));

    const asked = [];
    window.confirm = (message) => { asked.push(message); return false; };
    click(window, document.querySelector('#matching-faces-grid [data-face-id="1"]'));
    const input = document.getElementById("input-reassign-name");
    input.value = "Rowan Thackeray";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    click(window, document.getElementById("btn-reassign-selected"));
    await new Promise((r) => window.setTimeout(r, 30));

    assert.equal(asked.length, 1);
    assert.ok(!/not currently in the database/.test(asked[0]), asked[0]);
  });
});
