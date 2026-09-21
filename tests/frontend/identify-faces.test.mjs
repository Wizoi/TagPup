/**
 * Identify Faces: what the panel does with the candidates it is given.
 *
 * Reported from the running app: the sidebar said "2 photos" for a name and the
 * panel showed nothing at all.
 *
 * The server returns every candidate for a name, including the ones DBSCAN could
 * not group -- deliberately, flagged `cluster_id: -1` with `similarity: 0.0` and
 * ranked last, because a face that forms no cluster is still a face somebody may
 * recognise. The page bucketed candidates into >= 0.9 and >= 0.8 and let everything
 * else fall off the end of the chain, which is all of them when nothing clustered.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const NAME = "Lena Zorina";

/** A face as the server sends it. */
function face(id, similarity, clusterId) {
  return {
    id,
    photo_path: `D:\\xc\\photo${id % 2}.jpg`,
    filename: `photo${id % 2}.jpg`,
    box: [10, 10, 40, 40],
    prob: 0.99,
    mtime: 0,
    year: 2025,
    similarity,
    cluster_id: clusterId,
    cluster_name: clusterId === -1 ? "Unclustered" : `Cluster ${clusterId + 1}`,
  };
}

function serverWith(faces, count = 2) {
  return new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: NAME, count }])
    .on("/api/unmatched-faces/person-matches", {
      faces,
      total_count: faces.length,
      unclustered_total: faces.filter((f) => f.cluster_id === -1).length,
      unclustered_shown: faces.filter((f) => f.cluster_id === -1).length,
      has_more: false,
      page: 1,
      limit: -1,
    })
    .on("/api/people-with-counts", [])
    .on("/api/people", []);
}

async function openPerson(t, faces) {
  const { window, document } = await loadApp("tagtuner", {
    server: serverWith(faces),
    url: `http://localhost:8080/kr-track/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
    t,
  });
  await new Promise((r) => window.setTimeout(r, 120));
  return { window, document };
}

function cards(document) {
  return [...document.querySelectorAll("#matching-faces-grid .face-match-item")];
}

afterEach(() => closeAllApps());

describe("candidates that clustered with nothing", () => {
  // The reported case: 15 faces across 2 photos, every one at similarity 0.0.
  const allUngrouped = Array.from({ length: 15 }, (_, i) => face(200 + i, 0.0, -1));

  test("they are shown rather than silently dropped", async (t) => {
    const { document } = await openPerson(t, allUngrouped);
    assert.ok(
      cards(document).length > 0,
      "the sidebar counted candidates the panel then refused to render"
    );
  });

  test("the Ungrouped tab appears and carries the count", async (t) => {
    const { document } = await openPerson(t, allUngrouped);
    const tab = document.getElementById("tab-low-conf");
    assert.ok(!tab.classList.contains("hidden"), "the tab stayed hidden");
    assert.match(tab.textContent, /Ungrouped \(15\)/);
  });

  test("the panel opens on the tab that has them", async (t) => {
    // Opening on an empty Likely tab is indistinguishable from having no
    // candidates at all, which is exactly how this was reported.
    const { document } = await openPerson(t, allUngrouped);
    assert.ok(
      document.getElementById("tab-low-conf").classList.contains("active"),
      "opened on a tab with nothing in it"
    );
  });

  test("the count line says how many are shown", async (t) => {
    const { document } = await openPerson(t, allUngrouped);
    assert.match(document.getElementById("matching-person-count").textContent, /15/);
  });
});

describe("candidates that did cluster", () => {
  const mixed = [
    face(1, 0.95, 0),
    face(2, 0.93, 0),
    face(3, 0.84, 1),
    face(4, 0.0, -1),
  ];

  test("the confident ones still open first", async (t) => {
    const { document } = await openPerson(t, mixed);
    assert.ok(document.getElementById("tab-matches").classList.contains("active"));
    assert.equal(cards(document).length, 2);
  });

  test("each tab counts only its own", async (t) => {
    const { document } = await openPerson(t, mixed);
    assert.match(document.getElementById("tab-matches").textContent, /Likely \(2\)/);
    assert.match(document.getElementById("tab-outliers").textContent, /Possible \(1\)/);
    assert.match(document.getElementById("tab-low-conf").textContent, /Ungrouped \(1\)/);
  });

  test("every candidate the server sent lands in exactly one tab", async (t) => {
    // The bug was a chain of else-ifs with no final else, so anything below 0.8
    // belonged nowhere. This pins the total rather than any one bucket.
    const { document } = await openPerson(t, mixed);
    const counts = ["tab-matches", "tab-outliers", "tab-low-conf"]
      .map((id) => Number((document.getElementById(id).textContent.match(/\((\d+)\)/) || [])[1] || 0));
    assert.equal(counts.reduce((a, b) => a + b, 0), mixed.length);
  });

  test("switching to Ungrouped shows the ungrouped one", async (t) => {
    const { document, window } = await openPerson(t, mixed);
    document.getElementById("tab-low-conf").click();
    await new Promise((r) => window.setTimeout(r, 30));
    assert.equal(cards(document).length, 1);
  });
});

describe("a name with no candidates at all", () => {
  test("the Ungrouped tab stays hidden", async (t) => {
    const { document } = await openPerson(t, []);
    assert.ok(document.getElementById("tab-low-conf").classList.contains("hidden"));
  });

  test("it says so rather than showing an empty grid", async (t) => {
    const { document } = await openPerson(t, []);
    assert.match(
      document.getElementById("matching-faces-grid").textContent,
      /No likely matches|No candidates/
    );
  });
});
