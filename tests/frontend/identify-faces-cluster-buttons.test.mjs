/**
 * A cluster's buttons act on the faces still in it, not the faces it was drawn with.
 *
 * Assigning or excluding faces takes their cards out of the grid in place rather than
 * rebuilding it. The cluster's buttons used to be wired to the list of faces the group
 * was first drawn with, so after some of its faces had been named or excluded:
 *
 *  - Ignore Cluster excluded every face it had ever held, and excluding clears the
 *    name -- the faces just assigned by hand lost their names, and Undo could not
 *    bring them back.
 *  - "Assign N" and Assign Cluster named faces that had already been excluded.
 *
 * And the Undo offered for either action appeared in the same tick as the request,
 * so a batch the server refused still said "Assigned 5" and offered to unmatch faces
 * that had never been assigned.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const NAME = "Unknown Faces";
const GUESS = "Imogen Vale";

function face(id, clusterId, photo) {
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
    cluster_name: clusterId === -1 ? "Unclustered" : `Cluster ${clusterId}`,
    other_names: [],
    suggested_name: clusterId === 5 ? GUESS : null,
    suggested_similarity: clusterId === 5 ? 0.93 : 0,
    suggestion_strength: clusterId === 5 ? "likely" : null,
  };
}

function facesOfSize(n) {
  const faces = [];
  for (let i = 1; i <= n; i++) faces.push(face(i, 5, `p${i}.jpg`));
  faces.push(face(20, 9, "q1.jpg"), face(21, 9, "q2.jpg"), face(22, 9, "q3.jpg"));
  return faces;
}

async function openGrid(t, { clusterSize = 6, matchReply, matchStatus = 200,
  excludeReply, excludeStatus = 200 } = {}) {
  const faces = facesOfSize(clusterSize);
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: NAME, count: faces.length, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", {
      faces,
      total_count: faces.length,
      unclustered_total: 0,
      unclustered_shown: 0,
      has_more: false,
    })
    .on("/api/faces/exclude", excludeReply || { success: true }, { status: excludeStatus })
    .on("/api/faces/match-bulk", matchReply || { success: true }, { status: matchStatus })
    .on("/api/faces/unmatch-bulk", { success: true })
    .on("/api/faces/restore", { success: true })
    .on("/api/people-with-counts", [])
    .on("/api/people", [GUESS]);

  const { window, document } = await loadApp("tagtuner", {
    server,
    url: `http://localhost:8080/photo_index/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
    t,
  });
  window.confirm = () => true;
  window.alert = () => {};
  await wait(window, 150);
  return { window, document, server };
}

const wait = (window, ms) => new Promise((r) => window.setTimeout(r, ms));
const clickOn = (window, el, opts = {}) =>
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, ...opts }));
const cardFor = (document, id) =>
  document.querySelector(`#matching-faces-grid [data-face-id="${id}"]`);

/** The section headed `label`, looked up while the grid is fresh. */
function clusterSection(document, label) {
  const section = [...document.querySelectorAll("#matching-faces-grid .matching-group-section")]
    .find((s) => ((s.querySelector(".matching-group-title") || {}).textContent || "")
      .startsWith(label));
  assert.ok(section, `no section headed ${label}`);
  return section;
}

function buttonIn(section, text) {
  return [...section.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

/** Select these faces and assign them by hand, with Assign Selected. */
async function assignByHand(window, document, ids) {
  ids.forEach((id, i) => clickOn(window, cardFor(document, id), { ctrlKey: i > 0 }));
  await wait(window, 30);
  document.getElementById("input-reassign-name").value = GUESS;
  clickOn(window, document.getElementById("btn-reassign-selected"));
  await wait(window, 150);
}

/** Select these faces and exclude them, with Exclude Selected. */
async function excludeByHand(window, document, ids) {
  ids.forEach((id, i) => clickOn(window, cardFor(document, id), { ctrlKey: i > 0 }));
  await wait(window, 30);
  clickOn(window, document.getElementById("btn-exclude-selected"));
  await wait(window, 100);
  const modal = document.getElementById("exclude-reason-modal");
  if (modal && !modal.classList.contains("hidden")) {
    const choice = modal.querySelector("#exclude-reason-choices button");
    if (choice) clickOn(window, choice);
    await wait(window, 150);
  }
}

async function pressIgnoreCluster(window, document, section) {
  clickOn(window, buttonIn(section, "Ignore Cluster"));
  await wait(window, 60);
  const confirm = document.getElementById("ignore-confirm-modal");
  if (confirm && !confirm.classList.contains("hidden")) {
    clickOn(window, document.getElementById("btn-ignore-confirm-ok"));
    await wait(window, 150);
  }
}

const idsIn = (body) => (body && body.face_ids ? [...body.face_ids].sort((a, b) => a - b) : null);
const undoBar = (document) => document.getElementById("assign-undo-bar");

describe("cluster buttons act on the faces still in the cluster", () => {
  afterEach(() => closeAllApps());

  test("Ignore Cluster after naming some of it excludes only the faces left", async (t) => {
    const { window, document, server } = await openGrid(t, { clusterSize: 6 });
    const section = clusterSection(document, "Cluster 5");
    await assignByHand(window, document, [1, 2, 3, 4]);
    assert.deepEqual(idsIn(server.lastBody("/api/faces/match-bulk")), [1, 2, 3, 4]);

    await pressIgnoreCluster(window, document, section);

    assert.deepEqual(
      idsIn(server.lastBody("/api/faces/exclude")),
      [5, 6],
      "Ignore Cluster excluded faces that had just been named, which wipes their names",
    );
  });

  test("Assign N after excluding one does not name the excluded face", async (t) => {
    const { window, document, server } = await openGrid(t, { clusterSize: 6 });
    const section = clusterSection(document, "Cluster 5");
    await excludeByHand(window, document, [6]);

    const accept = section.querySelector(".cluster-suggestion-assign");
    assert.ok(accept, "the cluster lost its one-click assign");
    assert.ok(accept.textContent.includes("Assign 5"),
      `the button still counts the excluded face: ${accept.textContent}`);

    clickOn(window, accept);
    await wait(window, 150);
    assert.deepEqual(idsIn(server.lastBody("/api/faces/match-bulk")), [1, 2, 3, 4, 5]);
  });

  test("Assign Cluster after excluding one does not name the excluded face", async (t) => {
    const { window, document, server } = await openGrid(t, { clusterSize: 6 });
    const section = clusterSection(document, "Cluster 5");
    await excludeByHand(window, document, [2]);

    clickOn(window, buttonIn(section, "Assign Cluster"));
    await wait(window, 80);
    const input = [...document.querySelectorAll("body > div input")]
      .find((i) => i.placeholder === "Search or enter name...");
    assert.ok(input, "the name popup did not open");
    const title = input.parentElement.querySelector("h3");
    assert.ok(title.textContent.includes("Assign 5 "), `popup counts: ${title.textContent}`);
    input.value = GUESS;
    clickOn(window, buttonIn(input.parentElement, "Assign"));
    await wait(window, 150);

    assert.deepEqual(idsIn(server.lastBody("/api/faces/match-bulk")), [1, 3, 4, 5, 6]);
  });

  test("a cluster left with one face is no longer offered as a cluster", async (t) => {
    const { window, document } = await openGrid(t, { clusterSize: 5 });
    const section = clusterSection(document, "Cluster 5");
    await assignByHand(window, document, [1, 2, 3, 4]);

    assert.ok(section.isConnected, "the survivor's group vanished with its last card");
    assert.ok(cardFor(document, 5), "the survivor's card went missing");
    assert.equal(buttonIn(section, "Ignore Cluster"), undefined,
      "one face is not a cluster, yet Ignore Cluster is still offered for it");
    assert.equal(buttonIn(section, "Assign Cluster"), undefined);
    assert.equal(section.querySelector(".cluster-suggestion-assign"), null);
    const heading = section.querySelector(".matching-group-title").textContent;
    assert.ok(heading.startsWith("Unclustered"), `heading: ${heading}`);
  });

  test("a cluster emptied entirely disappears", async (t) => {
    const { window, document } = await openGrid(t, { clusterSize: 3 });
    const section = clusterSection(document, "Cluster 5");
    await assignByHand(window, document, [1, 2, 3]);
    assert.equal(section.isConnected, false);
  });
});

describe("Undo is offered for what the server confirmed", () => {
  afterEach(() => closeAllApps());

  test("a refused assignment offers no Undo and gives the button back", async (t) => {
    const { window, document } = await openGrid(t, {
      clusterSize: 4,
      matchStatus: 400,
      matchReply: { success: false, error: "Cannot match: already tagged." },
    });
    const section = clusterSection(document, "Cluster 5");
    const accept = section.querySelector(".cluster-suggestion-assign");
    clickOn(window, accept);
    await wait(window, 150);

    assert.ok(undoBar(document).classList.contains("hidden"),
      `Undo offered for a batch the server refused: ${document.getElementById("assign-undo-text").textContent}`);
    assert.equal(accept.disabled, false, "the button stays dead after a failure");
    assert.ok(accept.textContent.includes("Assign 4"), `label: ${accept.textContent}`);
  });

  test("Undo is offered once the server has confirmed the assignment", async (t) => {
    const { window, document } = await openGrid(t, { clusterSize: 4 });
    const section = clusterSection(document, "Cluster 5");
    clickOn(window, section.querySelector(".cluster-suggestion-assign"));
    await wait(window, 150);
    assert.ok(!undoBar(document).classList.contains("hidden"));
    assert.ok(document.getElementById("assign-undo-text").textContent.includes("Assigned 4"));
  });

  test("Undo covers only the faces the server says it assigned", async (t) => {
    const { window, document, server } = await openGrid(t, {
      clusterSize: 4,
      matchReply: { success: true, matched: 3, matched_ids: [1, 2, 3], skipped_excluded: [4] },
    });
    const section = clusterSection(document, "Cluster 5");
    clickOn(window, section.querySelector(".cluster-suggestion-assign"));
    await wait(window, 150);

    assert.ok(document.getElementById("assign-undo-text").textContent.includes("Assigned 3"),
      document.getElementById("assign-undo-text").textContent);
    clickOn(window, document.getElementById("btn-assign-undo"));
    await wait(window, 100);
    assert.deepEqual(idsIn(server.lastBody("/api/faces/unmatch-bulk")), [1, 2, 3]);
  });

  test("a refused Ignore Cluster offers no Undo", async (t) => {
    const { window, document } = await openGrid(t, {
      clusterSize: 4,
      excludeStatus: 500,
      excludeReply: { error: "database is locked" },
    });
    const section = clusterSection(document, "Cluster 5");
    await pressIgnoreCluster(window, document, section);

    assert.ok(undoBar(document).classList.contains("hidden"),
      "Undo offered for an Ignore the server refused");
    assert.ok(cardFor(document, 1), "the faces left the grid although nothing was excluded");
  });
});
