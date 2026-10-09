/**
 * Identify Faces with a name two people have (identity by id, part B): the pages still send names, so a read of the name opens
 * the candidates of both and a write the server cannot take says WHY, with the people it found, not "failed". Shapes are the
 * server's (tests/test_two_people_one_leaf_on_a_photo.py holds the server side). Names are made up.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const NAME = "Sam";
const SAM_T = { id: 10, name: NAME, tag: "Family/Thackeray/Sam", group: "Thackeray", shared: true };
const SAM_I = { id: 12, name: NAME, tag: "Family/Ingersoll/Sam", group: "Ingersoll", shared: true };
const REFUSAL = "More than one person is called Sam: Family/Ingersoll/Sam, Family/Thackeray/Sam. Choose one of them "
  + "(a page sends their person_id).";

function face(id, clusterId) {
  return {
    id, photo_path: `D:\\xc\\photo${id}.jpg`, filename: `photo${id}.jpg`, box: [10, 10, 40, 40], prob: 0.99, mtime: 0,
    year: 2025, similarity: 0.95, band: "likely", cluster_id: clusterId, cluster_name: `Cluster ${clusterId + 1}`,
    suggested_name: "Sam", suggested_similarity: 0.93, suggestion_strength: "likely",
  };
}

async function open(t) {
  const faces = [face(1, 0), face(2, 0), face(3, 0), face(4, 0)];
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [
      { name: NAME, count: 2, person_id: SAM_T.id, person: SAM_T },
      { name: NAME, count: 2, person_id: SAM_I.id, person: SAM_I },
    ])
    .on("/api/unmatched-faces/person-matches", {
      faces, total_count: 4, unclustered_total: 0, unclustered_shown: 0, has_more: false, page: 1, limit: -1,
    })
    .on("/api/faces/match-bulk", { error: REFUSAL }, { status: 400 })
    .on("/api/people-with-counts", [])
    .on("/api/people", []);
  const ctx = await loadApp("tagtuner", {
    server, t, url: `http://localhost:8080/kr-track/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
  });
  await new Promise((r) => ctx.window.setTimeout(r, 120));
  return { ...ctx, server };
}

test("both people's candidates open under the name, and a write refused for the shared name says which people", async (t) => {
  const { window, document, server } = await open(t);
  assert.equal(document.querySelectorAll("#matching-faces-grid .face-match-item").length, 4, "the union is on the page");
  const said = [];
  window.alert = (text) => said.push(String(text));
  document.querySelector(".cluster-suggestion-assign").click();
  await new Promise((r) => window.setTimeout(r, 60));
  assert.equal(server.lastBody("/api/faces/match-bulk").person_name, NAME, "the page sends the name it has");
  assert.ok(said.some((text) => text.includes("Family/Ingersoll/Sam") && text.includes("Family/Thackeray/Sam")),
    "the refusal names both people: " + JSON.stringify(said));
  assert.ok(said.every((text) => !/^Bulk matching failed$/.test(text)), "not the generic sentence");
});
