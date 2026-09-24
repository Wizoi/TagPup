/**
 * The Assign button says who the faces will go to, and that is who they go to.
 *
 * The label named the person the selected faces' badges agreed on; the click sent
 * whatever the name box held. Type one name over three faces badged with another, and
 * the button read "Assign 3 to <badge>" while assigning them to the typed name.
 * One badged face among unbadged ones counted as agreement and filled the box for all
 * of them. And a group's "Looks like ..." label put its name in the box as if typed,
 * where it stayed for every selection after.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, closeAllApps } from "./harness.mjs";

const NAME = "Unknown Faces";

function face(id, suggestion, clusterId = -1) {
  return {
    id,
    photo_path: `D:\\xc\\photo${id}.jpg`,
    filename: `photo${id}.jpg`,
    box: [10, 10, 40, 40],
    prob: 0.99,
    mtime: 0,
    year: 2026,
    similarity: 0.0,
    cluster_id: clusterId,
    cluster_name: clusterId === -1 ? "Unclustered" : `Cluster ${clusterId}`,
    other_names: [],
    suggested_name: suggestion,
    suggested_similarity: suggestion ? 0.93 : 0,
    suggestion_strength: suggestion ? "likely" : null,
  };
}

async function open(t, faces) {
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: NAME, count: faces.length, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", {
      faces, total_count: faces.length, unclustered_total: faces.length,
      unclustered_shown: faces.length, has_more: false, page: 1, limit: -1,
    })
    .on("/api/people-with-counts", [])
    .on("/api/people", ["Rory Olwen", "Kira Bao"]);
  const { window, document } = await loadApp("tagtuner", {
    t, server,
    url: `http://localhost:8080/kr-track/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
  });
  await new Promise((r) => window.setTimeout(r, 140));
  return { window, document };
}

const settle = (window) => new Promise((r) => window.setTimeout(r, 40));
const cards = (document) => [...document.querySelectorAll("#matching-faces-grid .face-match-item")];

async function selectAll(window, document) {
  cards(document).forEach((c) => click(window, c, { ctrlKey: true }));
  await settle(window);
}

describe("the Assign button tells the truth", () => {
  afterEach(() => closeAllApps());

  test("a typed name is the name on the button", async (t) => {
    const { window, document } = await open(t, [face(1, "Rory Olwen"), face(2, "Rory Olwen")]);
    await selectAll(window, document);
    const input = document.getElementById("input-reassign-name");
    input.value = "Kira Bao";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    await settle(window);
    assert.equal(document.getElementById("btn-reassign-selected").textContent, "Assign 2 to Kira Bao");
  });

  test("one badged face does not speak for the unbadged ones", async (t) => {
    const { window, document } = await open(t, [face(1, "Rory Olwen"), face(2, null)]);
    await selectAll(window, document);
    assert.equal(document.getElementById("input-reassign-name").value, "",
      "one badge filled the box for a face it says nothing about");
  });

  test("a group's guess selects the group it is a guess for", async (t) => {
    const { window, document } = await open(t, [face(1, "Rory Olwen", 4), face(2, null, 4), face(3, null, 7), face(4, null, 7)]);
    const label = document.querySelector(".cluster-suggestion-label");
    assert.ok(label, "fixture: the group offers its guess");
    click(window, label);
    await settle(window);
    const selected = [...document.querySelectorAll("#matching-faces-grid .face-match-item.selected")]
      .map((c) => Number(c.dataset.faceId)).sort();
    assert.deepEqual(selected, [1, 2]);
    assert.equal(document.getElementById("btn-reassign-selected").textContent, "Assign 2 to Rory Olwen");

    // Another selection: the guess was for that group, and goes.
    click(window, cards(document).find((c) => c.dataset.faceId === "3"));
    await settle(window);
    assert.equal(document.getElementById("input-reassign-name").value, "");
  });
});
