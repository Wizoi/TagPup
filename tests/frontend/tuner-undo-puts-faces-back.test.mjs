/**
 * Undo puts the faces back where they were, without rebuilding the grid.
 *
 * Ignoring a cluster or assigning it in one click takes its cards out of the grid in
 * place. Undo then rebuilt the whole grid -- ten seconds on Unknown Faces, the very
 * cost the in-place removal was written to avoid -- from a list the undone faces had
 * already been dropped from, so they did not come back at all until the person was
 * opened again. And undoing an assign told the server "this is nobody", a decision
 * nobody had made.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const NAME = "Unknown Faces";

function face(id, clusterId, photo, suggested = null) {
  return {
    id,
    photo_path: `D:\\meets\\${photo}`,
    filename: photo,
    box: [10, 10, 40, 40],
    prob: 0.99,
    mtime: 0,
    year: 2025,
    similarity: 0.9,
    person_similarity: 0.9,
    cluster_id: clusterId,
    cluster_name: `Cluster ${clusterId}`,
    other_names: [],
    suggested_name: suggested,
    suggested_similarity: suggested ? 0.93 : 0,
    suggestion_strength: suggested ? "likely" : null,
  };
}

const FACES = [
  face(1, 5, "a.jpg", "Rowan Thackeray"),
  face(2, 5, "b.jpg"),
  face(3, 5, "c.jpg"),
  face(10, 9, "d.jpg"),
  face(11, 9, "e.jpg"),
];

async function openGrid(t) {
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: NAME, count: FACES.length, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", {
      faces: FACES, total_count: FACES.length, unclustered_total: 0,
      unclustered_shown: 0, has_more: false,
    })
    .on("/api/faces/exclude", { success: true, excluded: 3 })
    .on("/api/faces/restore", { success: true, restored: 3 })
    .on("/api/faces/match-bulk", { success: true, matched_ids: [1, 2, 3] })
    .on("/api/faces/unmatch-bulk", { success: true })
    .on("/api/people-with-counts", [])
    .on("/api/people", ["Rowan Thackeray"]);
  const { window, document } = await loadApp("tagtuner", {
    server,
    url: `http://localhost:8080/photo_index/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
    t,
  });
  await new Promise((r) => window.setTimeout(r, 150));
  return { window, document, server };
}

const settle = (window, ms = 150) => new Promise((r) => window.setTimeout(r, ms));
const card = (document, id) => document.querySelector(`#matching-faces-grid [data-face-id="${id}"]`);
const ids = (document) =>
  [...document.querySelectorAll("#matching-faces-grid [data-face-id]")].map((c) => Number(c.dataset.faceId));
const press = (window, el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

function section(document, title) {
  return [...document.querySelectorAll("#matching-faces-grid .matching-group-section")].find((s) =>
    ((s.querySelector(".matching-group-title") || {}).textContent || "").startsWith(title));
}

async function ignore(window, document) {
  const button = [...section(document, "Cluster 5").querySelectorAll("button")]
    .find((b) => b.textContent.includes("Ignore Cluster"));
  press(window, button);
  await settle(window, 120);
  const ok = document.getElementById("btn-ignore-confirm-ok");
  const modal = document.getElementById("ignore-confirm-modal");
  if (modal && !modal.classList.contains("hidden")) press(window, ok);
  await settle(window);
}

describe("Undo puts the faces back", () => {
  afterEach(() => closeAllApps());

  test("undoing an ignore: the cards return, and nothing else is rebuilt", async (t) => {
    const { window, document, server } = await openGrid(t);
    const bystander = card(document, 10);
    await ignore(window, document);
    assert.deepEqual(ids(document), [10, 11], "fixture: the cluster was ignored");

    press(window, document.getElementById("btn-assign-undo"));
    await settle(window);

    assert.deepEqual(ids(document).sort((a, b) => a - b), [1, 2, 3, 10, 11],
      "the undone faces did not come back");
    assert.equal(card(document, 10), bystander, "the grid was rebuilt around the undo");
    assert.deepEqual(server.lastBody("/api/faces/restore").face_ids, [1, 2, 3]);
    assert.match(section(document, "Cluster 5").textContent, /3 faces/);
  });

  test("undoing an assign: the cards return, and the faces are unreviewed, not nobody", async (t) => {
    const { window, document, server } = await openGrid(t);
    const bystander = card(document, 10);
    const accept = section(document, "Cluster 5").querySelector(".cluster-suggestion-assign");
    assert.ok(accept, "fixture: the cluster offers its guess");
    press(window, accept);
    await settle(window);
    assert.deepEqual(ids(document), [10, 11], "fixture: the cluster was assigned");

    press(window, document.getElementById("btn-assign-undo"));
    await settle(window);

    assert.deepEqual(ids(document).sort((a, b) => a - b), [1, 2, 3, 10, 11]);
    assert.equal(card(document, 10), bystander);
    const sent = server.lastBody("/api/faces/unmatch-bulk");
    assert.deepEqual(sent.face_ids, [1, 2, 3]);
    assert.equal(sent.undo, true, "the undo told the server these faces are nobody");
  });
});
