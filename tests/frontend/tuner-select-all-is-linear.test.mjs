/**
 * Select All and Select None cost one pass over the grid, not one per selected face.
 *
 * Both walked every card and asked `selectedFaceIds.includes(id)` for each -- an array
 * search inside a loop. On Unknown Faces, about 24,000 cards, that is hundreds of
 * millions of comparisons for a click, on the main thread. The label beside the button
 * did the same on every selection change. They use a Set now.
 *
 * Timing 24,000 cards in jsdom would be slow and flaky, so this reads the code: the
 * handler and the label must not search the selection array per card.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps, pageSource } from "./harness.mjs";

// The page is modules now (web/tuner/); the button and its label are in whichever
// holds them.
const SOURCE = pageSource("tagtuner");

function block(startMarker, length = 1400) {
  const at = SOURCE.indexOf(startMarker);
  assert.ok(at >= 0, `cannot find ${startMarker}`);
  return SOURCE.slice(at, at + length);
}

describe("Select All is one pass over the grid", () => {
  test("the button's handler does not search the selection per card", () => {
    const handler = block("btnMatchingSelectAll.addEventListener('click'");
    assert.ok(!/selectedFaceIds\.includes\(/.test(handler), handler.slice(0, 400));
    assert.ok(!/faceIds\.includes\(/.test(handler));
  });

  test("its label does not either", () => {
    // The few lines that decide between "Select All" and "Select None".
    const at = SOURCE.indexOf("btnMatchingSelectAll.textContent = allSelected");
    assert.ok(at >= 0, "cannot find the label");
    const label = SOURCE.slice(at - 500, at);
    assert.ok(!/selectedFaceIds\.includes\(/.test(label), label);
  });

  test("and it still selects everything, then nothing", async (t) => {
    const faces = [1, 2, 3].map((id) => ({
      id, photo_path: `D:\\xc\\p${id}.jpg`, filename: `p${id}.jpg`, box: [1, 1, 5, 5], prob: 0.99,
      mtime: 0, year: 2026, similarity: 0, cluster_id: -1, cluster_name: "Unclustered",
      other_names: [], suggested_name: null, suggested_similarity: 0, suggestion_strength: null,
    }));
    const server = new FakeServer()
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/unmatched-faces/people", [{ name: "Unknown Faces", count: 3, unit: "face" }])
      .on("/api/unmatched-faces/person-matches", {
        faces, total_count: 3, unclustered_total: 3, unclustered_shown: 3, has_more: false,
      })
      .on("/api/people-with-counts", [])
      .on("/api/people", []);
    const { window, document } = await loadApp("tagtuner", {
      t, server, url: "http://localhost:8080/kr-track/?mode=unmatched-faces&person=Unknown%20Faces",
    });
    await new Promise((r) => window.setTimeout(r, 140));
    const button = document.getElementById("btn-matching-select-all");
    const selected = () => document.querySelectorAll("#matching-faces-grid .face-match-item.selected").length;
    click(window, button);
    await new Promise((r) => window.setTimeout(r, 30));
    assert.equal(selected(), 3);
    assert.equal(button.textContent, "Select None");
    click(window, button);
    await new Promise((r) => window.setTimeout(r, 30));
    assert.equal(selected(), 0);
    closeAllApps();
  });
});
