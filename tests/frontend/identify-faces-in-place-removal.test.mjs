/**
 * Ignoring a cluster must not rebuild the grid around it.
 *
 * Reported from the running app: about ten seconds to ignore a cluster of five, on a
 * grid of roughly twenty-four thousand faces. None of it was the server -- the exclude
 * request itself is a fraction of a second.
 *
 * Assigning and ignoring both removed the handful of cards involved and then called
 * renderPersonFaces, which clears every `img.src` on screen, empties the container and
 * builds every card again. For five faces leaving, twenty-four thousand cards were
 * destroyed and recreated.
 *
 * Nothing about the cards that stayed changed, so nothing about them needs rebuilding.
 * These tests pin that: the surviving card elements are the same objects afterwards,
 * and the headings and counts still tell the truth.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const NAME = "Unknown Faces";

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
    suggested_name: null,
    suggested_similarity: 0,
    suggestion_strength: null,
  };
}

//: Two groups. The first is ignored; the second must come through untouched.
const FACES = [
  face(1, 5, "a.jpg"),
  face(2, 5, "b.jpg"),
  face(3, 5, "c.jpg"),
  face(10, 9, "d.jpg"),
  face(11, 9, "e.jpg"),
  face(12, 9, "f.jpg"),
  face(13, 9, "g.jpg"),
];

async function openGrid(t) {
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: NAME, count: FACES.length, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", {
      faces: FACES,
      total_count: FACES.length,
      unclustered_total: 0,
      unclustered_shown: 0,
      has_more: false,
    })
    .on("/api/faces/exclude", { success: true, excluded: 3 })
    .on("/api/faces/match-bulk", { success: true })
    .on("/api/people-with-counts", [])
    .on("/api/people", []);

  const { window, document } = await loadApp("tagtuner", {
    server,
    url: `http://localhost:8080/photo_index/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
    t,
  });
  await new Promise((r) => window.setTimeout(r, 150));
  return { window, document, server };
}

const cards = (document) => [...document.querySelectorAll("#matching-faces-grid [data-face-id]")];
const cardFor = (document, id) =>
  document.querySelector(`#matching-faces-grid [data-face-id="${id}"]`);
const sections = (document) =>
  [...document.querySelectorAll("#matching-faces-grid .matching-group-section")];

function headingTexts(document) {
  return sections(document).map((s) =>
    (s.querySelector(".matching-group-title") || {}).textContent || "");
}

async function ignoreCluster(window, document, clusterLabel) {
  const section = sections(document).find((s) =>
    (s.querySelector(".matching-group-title") || {}).textContent.startsWith(clusterLabel));
  assert.ok(section, `no section headed ${clusterLabel}`);
  const button = [...section.querySelectorAll("button")].find((b) =>
    b.textContent.includes("Ignore Cluster"));
  assert.ok(button, "the cluster has no Ignore Cluster button");
  button.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => window.setTimeout(r, 120));

  // Ignoring a whole cluster asks first.
  const confirm = document.getElementById("ignore-confirm-modal");
  if (confirm && !confirm.classList.contains("hidden")) {
    document
      .getElementById("btn-ignore-confirm-ok")
      .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 120));
  }

  // The reason picker, when it appears.
  const modal = document.getElementById("exclude-reason-modal");
  if (modal && !modal.classList.contains("hidden")) {
    const choice = modal.querySelector("#exclude-reason-choices button");
    if (choice) choice.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 150));
  }
  return section;
}

describe("ignoring a cluster leaves the rest of the grid alone", () => {
  afterEach(() => closeAllApps());

  test("the cards that stay are the same elements, not rebuilt copies", async (t) => {
    const { window, document } = await openGrid(t);
    assert.equal(cards(document).length, 7, "grid did not render as expected");

    // Hold on to the actual element objects of the group that is NOT being touched.
    const survivorsBefore = [10, 11, 12, 13].map((id) => cardFor(document, id));
    survivorsBefore.forEach((el, i) => assert.ok(el, `face ${[10, 11, 12, 13][i]} missing`));

    await ignoreCluster(window, document, "Cluster 5");

    const survivorsAfter = [10, 11, 12, 13].map((id) => cardFor(document, id));
    survivorsAfter.forEach((el, i) => {
      assert.ok(el, "a face from an untouched cluster disappeared");
      assert.strictEqual(
        el,
        survivorsBefore[i],
        "the grid was rebuilt: this card is a different element than before, which on "
          + "a real library means recreating tens of thousands of them to remove three",
      );
    });
  });

  test("the ignored faces are gone", async (t) => {
    const { window, document } = await openGrid(t);
    await ignoreCluster(window, document, "Cluster 5");

    assert.deepEqual(
      cards(document).map((c) => Number(c.dataset.faceId)).sort((a, b) => a - b),
      [10, 11, 12, 13],
    );
  });

  test("the emptied group's heading goes with it", async (t) => {
    const { window, document } = await openGrid(t);
    assert.equal(sections(document).length, 2);

    await ignoreCluster(window, document, "Cluster 5");

    const headings = headingTexts(document);
    assert.equal(headings.length, 1, `expected one group left, got: ${headings}`);
    assert.ok(
      headings[0].startsWith("Cluster 9"),
      `the wrong group survived: ${headings[0]}`,
    );
  });

  test("a group that loses only some of its faces has its heading recounted", async (t) => {
    const { window, document } = await openGrid(t);

    // Select one face of Cluster 9 and exclude just that one.
    const card = cardFor(document, 13);
    card.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 60));

    const excludeBtn = document.getElementById("btn-exclude-selected");
    assert.ok(excludeBtn, "no exclude button");
    excludeBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 120));
    const modal = document.getElementById("exclude-reason-modal");
    if (modal && !modal.classList.contains("hidden")) {
      const choice = modal.querySelector("#exclude-reason-choices button");
      if (choice) choice.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
      await new Promise((r) => window.setTimeout(r, 150));
    }

    const heading = headingTexts(document).find((h) => h.startsWith("Cluster 9"));
    assert.ok(heading, "Cluster 9 vanished when it should have shrunk");
    assert.ok(
      heading.includes("3 faces"),
      `the heading still claims the old size: ${heading}`,
    );
    assert.ok(
      heading.includes("3 photos"),
      `the photo count was not recounted: ${heading}`,
    );
  });

  test("the panel's face count follows the cards", async (t) => {
    const { window, document } = await openGrid(t);
    const count = document.getElementById("matching-person-count");
    assert.ok(/7/.test(count.textContent), `expected 7 to start: ${count.textContent}`);

    await ignoreCluster(window, document, "Cluster 5");

    assert.ok(
      /\b4\b/.test(count.textContent),
      `the heading count did not follow the cards: ${count.textContent}`,
    );
  });
});
