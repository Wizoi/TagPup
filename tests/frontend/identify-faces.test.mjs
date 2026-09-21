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
  const server = serverWith(faces).on("/api/faces/exclude", { success: true, excluded: faces.length });
  const { window, document } = await loadApp("tagtuner", {
    server,
    url: `http://localhost:8080/kr-track/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
    t,
  });
  await new Promise((r) => window.setTimeout(r, 120));
  return { window, document, server };
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

describe("ignoring a cluster", () => {
  // Exclusion already meant "never offer this face to anyone", but reaching it
  // required ticking every face in the group. A cluster of a stranger at a meet
  // can be thirty faces, which is thirty clicks to say one thing.
  const cluster = [
    face(10, 0.95, 0),
    face(11, 0.94, 0),
    face(12, 0.92, 0),
  ];

  function ignoreButton(document) {
    return [...document.querySelectorAll(".matching-group-header button")]
      .find((b) => /Ignore Cluster/.test(b.textContent));
  }

  test("every cluster offers it, beside Assign Cluster", async (t) => {
    const { document } = await openPerson(t, cluster);
    assert.ok(ignoreButton(document), "no way to ignore a cluster");
  });

  test("it excludes every face in the cluster at once", async (t) => {
    const { document, window, server } = await openPerson(t, cluster);
    window.confirm = () => true;
    ignoreButton(document).click();
    await new Promise((r) => window.setTimeout(r, 40));

    const body = server.lastBody("/api/faces/exclude");
    assert.deepEqual(body.face_ids, [10, 11, 12]);
  });

  test("it asks first, and declining changes nothing", async (t) => {
    const { document, window, server } = await openPerson(t, cluster);
    window.confirm = () => false;
    ignoreButton(document).click();
    await new Promise((r) => window.setTimeout(r, 40));

    assert.equal(server.lastBody("/api/faces/exclude"), undefined);
  });

  test("it does not ask twice for the same decision", async (t) => {
    // The bulk path prompts for a reason; asking again after a confirm that
    // already stated the scope is the friction that stops a feature being used.
    const { document, window, server } = await openPerson(t, cluster);
    let prompted = false;
    window.confirm = () => true;
    window.prompt = () => { prompted = true; return "x"; };
    ignoreButton(document).click();
    await new Promise((r) => window.setTimeout(r, 40));

    assert.equal(prompted, false, "a second modal appeared for the same decision");
  });

  test("the reason it records says where it came from", async (t) => {
    const { document, window, server } = await openPerson(t, cluster);
    window.confirm = () => true;
    ignoreButton(document).click();
    await new Promise((r) => window.setTimeout(r, 40));

    assert.match(server.lastBody("/api/faces/exclude").reason, /cluster/);
  });
});

describe("face crop details", () => {
  // The panel is titled Face Crop Details and showed the source photo with a box
  // drawn on it, but never the crop itself -- the thing the matcher compares and
  // the thing you are being asked to recognise.
  const faces = [face(21, 0.95, 0), face(22, 0.94, 0)];

  async function selectFirstFace(t) {
    const { document, window, server } = await openPerson(t, faces);
    const card = document.querySelector("#matching-faces-grid .face-match-item");
    card.click();
    await new Promise((r) => window.setTimeout(r, 60));
    return { document, window, server };
  }

  test("the crop is shown for the selected face", async (t) => {
    const { document } = await selectFirstFace(t);
    const crop = document.getElementById("matching-detail-crop");
    assert.match(crop.getAttribute("src"), /\/api\/face-crop\?id=21/);
  });

  test("the source photo is still shown alongside it", async (t) => {
    const { document } = await selectFirstFace(t);
    assert.match(
      document.getElementById("matching-detail-img").getAttribute("src"),
      /\/api\/photo-file/
    );
  });

  test("the crop's pixel size is reported", async (t) => {
    const { document } = await selectFirstFace(t);
    // The fixture's box is [10, 10, 40, 40].
    assert.match(document.getElementById("matching-detail-crop-size").textContent, /30 x 30 px/);
  });

  test("a crop too small to match well is flagged", async (t) => {
    const { document } = await selectFirstFace(t);
    const size = document.getElementById("matching-detail-crop-size");
    assert.ok(size.classList.contains("is-small"), "a 30px crop was not flagged");
    assert.match(size.title, /less to match on/);
  });

  test("a crop of a workable size is not flagged", async (t) => {
    const big = [{ ...face(30, 0.95, 0), box: [0, 0, 120, 120] }];
    const { document, window } = await openPerson(t, big);
    document.querySelector("#matching-faces-grid .face-match-item").click();
    await new Promise((r) => window.setTimeout(r, 60));

    const size = document.getElementById("matching-detail-crop-size");
    assert.equal(size.textContent, "120 x 120 px");
    assert.ok(!size.classList.contains("is-small"));
  });

  test("selecting another face swaps the crop", async (t) => {
    const { document, window } = await selectFirstFace(t);
    const cards = document.querySelectorAll("#matching-faces-grid .face-match-item");
    cards[1].click();
    await new Promise((r) => window.setTimeout(r, 60));
    assert.match(
      document.getElementById("matching-detail-crop").getAttribute("src"),
      /id=22/
    );
  });
});

describe("saying why a face is offered", () => {
  // A candidate is an unnamed face in a photo whose keywords mention the name, where
  // no face there is linked to it yet. When a photo names two people and neither has
  // a face, both faces appear under both names -- correct, and impossible to work out
  // from a grid of faces with no explanation.
  const shared = [
    { ...face(41, 0.0, -1), photo_path: "D:\\xc\\group.jpg", other_names: ["Miko Zellweg"] },
    { ...face(42, 0.0, -1), photo_path: "D:\\xc\\group.jpg", other_names: ["Miko Zellweg"] },
    { ...face(43, 0.0, -1), photo_path: "D:\\xc\\solo.jpg", other_names: [] },
  ];

  test("the group says who else its photos name", async (t) => {
    const { document } = await openPerson(t, shared);
    const header = document.querySelector(".matching-group-header");
    assert.match(header.textContent, /also names Miko Zellweg/);
  });

  test("several other names are counted rather than listed", async (t) => {
    const many = [
      { ...face(44, 0.0, -1), other_names: ["Miko Zellweg", "Oren Ingram", "Emory Kade"] },
    ];
    const { document } = await openPerson(t, many);
    const note = document.querySelector(".competing-names-note");
    assert.match(note.textContent, /also names 3 others/);
    assert.match(note.title, /Miko Zellweg/);
  });

  test("the faces sharing a photo with another name are marked", async (t) => {
    const { document } = await openPerson(t, shared);
    const marked = [...document.querySelectorAll(".face-match-item")]
      .filter((el) => el.classList.contains("has-competing-names"));
    assert.equal(marked.length, 2, "the two faces from the shared photo are not marked");
  });

  test("a face whose photo names only this person is not marked", async (t) => {
    const { document } = await openPerson(t, shared);
    const items = [...document.querySelectorAll(".face-match-item")];
    const solo = items.find((el) => !el.classList.contains("has-competing-names"));
    assert.ok(solo, "every face was marked, including the unambiguous one");
  });

  test("the mark explains what to do about it", async (t) => {
    const { document } = await openPerson(t, shared);
    const marked = document.querySelector(".face-match-item.has-competing-names");
    assert.match(marked.title, /Miko Zellweg/);
    assert.match(marked.title, /stop being\s+offered/);
  });

  test("nothing is said when no photo names anyone else", async (t) => {
    const clean = [{ ...face(45, 0.0, -1), other_names: [] }];
    const { document } = await openPerson(t, clean);
    assert.equal(document.querySelector(".competing-names-note"), null);
  });
});
