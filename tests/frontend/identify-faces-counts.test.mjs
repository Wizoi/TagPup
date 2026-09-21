/**
 * Identify Faces counts faces, and a mixed selection is assigned by its badges.
 *
 * Two things reported from the running app.
 *
 * The sidebar used three units in one list: photos for people and the Ungrouped
 * bucket, faces for Excluded, photos for Unknown Faces. So "710 photos" sat beside
 * "4,739 faces" in the panel, and "1,554 photos" beside "1,554 ungrouped faces" --
 * true numbers reading as contradictions. A face is the unit of work here: one photo
 * of a start line holds thirty, and clearing it is thirty decisions.
 *
 * And selecting several faces that each resemble a different person left one box
 * asking for one name. The badges already say who each face is, so the button uses
 * them rather than asking for something that cannot be answered.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click } from "./harness.mjs";

const NAME = "Unknown Faces";

function face(id, suggestion, similarity) {
  return {
    id,
    photo_path: `D:\\xc\\photo${id}.jpg`,
    filename: `photo${id}.jpg`,
    box: [10, 10, 40, 40],
    prob: 0.99,
    mtime: 0,
    year: 2026,
    similarity: 0.0,
    cluster_id: -1,
    cluster_name: "Unclustered",
    suggested_name: suggestion,
    suggested_similarity: similarity,
    suggestion_strength: similarity >= 0.85 ? "likely" : "possible",
  };
}

function build(people, faces) {
  return new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", people)
    .on("/api/unmatched-faces/person-matches", {
      faces,
      total_count: faces.length,
      unclustered_total: faces.length,
      unclustered_shown: faces.length,
      has_more: false,
      page: 1,
      limit: -1,
    })
    .on("/api/people-with-counts", [])
    .on("/api/people", ["Rory Olwen", "Kira Bao"])
    .on("/api/faces/match-bulk", { success: true, matched: 1 });
}

async function open(t, people, faces) {
  const { window, document, server } = await loadApp("tagtuner", {
    t,
    server: build(people, faces),
    url: `http://localhost:8080/kr-track/?mode=unmatched-faces&person=${encodeURIComponent(NAME)}`,
  });
  await new Promise((r) => window.setTimeout(r, 140));
  return { window, document, server };
}

const badges = (document) =>
  [...document.querySelectorAll("#photo-list .photo-badge")].map((b) => b.textContent);

describe("the sidebar counts what you act on", () => {
  test("a bucket of faces says faces", async (t) => {
    const { document } = await open(t,
      [{ name: NAME, count: 4739, unit: "face", photos: 710 }], []);
    assert.ok(badges(document).some((b) => /4,739 faces/.test(b)),
      `sidebar said: ${JSON.stringify(badges(document))}`);
  });

  test("it does not say photos for a face count", async (t) => {
    // The reported contradiction: 710 photos in the sidebar, 4,739 faces in the panel.
    const { document } = await open(t,
      [{ name: NAME, count: 4739, unit: "face", photos: 710 }], []);
    assert.ok(!badges(document).some((b) => /photos/.test(b)));
  });

  test("the photo count is still there on hover", async (t) => {
    const { document } = await open(t,
      [{ name: NAME, count: 4739, unit: "face", photos: 710 }], []);
    const badge = document.querySelector("#photo-list .photo-badge");
    assert.match(badge.title, /4,739 faces across 710 photos/);
  });

  test("Excluded is counted the same way", async (t) => {
    const { document } = await open(t, [
      { name: NAME, count: 10, unit: "face", photos: 3 },
      { name: "Excluded", count: 1554, unit: "face" },
    ], []);
    assert.ok(badges(document).some((b) => /1,554 faces/.test(b)));
  });

  test("a count of one is not pluralised", async (t) => {
    const { document } = await open(t, [{ name: NAME, count: 1, unit: "face" }], []);
    assert.ok(badges(document).some((b) => b.trim() === "1 face"));
  });
});

describe("assigning a mixed selection", () => {
  const mixed = [face(1, "Rory Olwen", 0.96), face(2, "Kira Bao", 0.93)];

  async function selectBoth(t) {
    const ctx = await open(t, [{ name: NAME, count: 2, unit: "face" }], mixed);
    const cards = [...ctx.document.querySelectorAll("#matching-faces-grid .face-match-item")];
    assert.equal(cards.length, 2, "the fixture did not render two faces");
    // Ctrl-click, because a plain click now selects only what it lands on -- the
    // way a file list behaves.
    cards.forEach((card) => click(ctx.window, card, { ctrlKey: true }));
    await new Promise((r) => ctx.window.setTimeout(r, 40));
    return ctx;
  }

  test("the button offers to use each face's own match", async (t) => {
    const { document } = await selectBoth(t);
    const button = document.getElementById("btn-reassign-selected");
    assert.match(button.textContent, /Assign 2 to their matches/);
    assert.equal(button.disabled, false, "the button stayed disabled with a selection");
  });

  test("the box says a single name cannot describe the selection", async (t) => {
    const { document } = await selectBoth(t);
    const input = document.getElementById("input-reassign-name");
    assert.match(input.placeholder, /Multiple \(2 people\)/);
  });

  test("each face goes to the person its badge names", async (t) => {
    const ctx = await selectBoth(t);
    ctx.window.confirm = () => true;
    ctx.document.getElementById("btn-reassign-selected").click();
    await new Promise((r) => ctx.window.setTimeout(r, 80));

    const sent = ctx.server.calls
      .filter((c) => c.url.includes("/api/faces/match-bulk") && c.body)
      .map((c) => [c.body.person_name, c.body.face_ids]);
    const names = sent.map(([name]) => name).sort();
    assert.deepEqual(names, ["Kira Bao", "Rory Olwen"],
      `assigned: ${JSON.stringify(sent)}`);
  });

  test("declining sends nothing", async (t) => {
    const ctx = await selectBoth(t);
    ctx.window.confirm = () => false;
    ctx.document.getElementById("btn-reassign-selected").click();
    await new Promise((r) => ctx.window.setTimeout(r, 60));
    assert.equal(
      ctx.server.calls.filter((c) => c.url.includes("/api/faces/match-bulk")).length, 0);
  });

  test("a typed name still wins over the badges", async (t) => {
    // The box is not taken away: sometimes the machine is wrong about all of them.
    const ctx = await selectBoth(t);
    const input = ctx.document.getElementById("input-reassign-name");
    input.value = "Rory Olwen";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    await new Promise((r) => ctx.window.setTimeout(r, 40));

    const button = ctx.document.getElementById("btn-reassign-selected");
    assert.equal(button.textContent, "Assign Selected");
    assert.match(input.placeholder, /Assign to name/);
  });

  test("a selection that agrees is not treated as mixed", async (t) => {
    const agreeing = [face(1, "Rory Olwen", 0.96), face(2, "Rory Olwen", 0.9)];
    const ctx = await open(t, [{ name: NAME, count: 2, unit: "face" }], agreeing);
    const cards = [...ctx.document.querySelectorAll("#matching-faces-grid .face-match-item")];
    cards.forEach((card) => click(ctx.window, card, { ctrlKey: true }));
    await new Promise((r) => ctx.window.setTimeout(r, 40));

    const button = ctx.document.getElementById("btn-reassign-selected");
    assert.equal(button.textContent, "Assign Selected",
      "one person's faces do not need the by-badge path");
  });
});

describe("picking faces the way a file list does", () => {
  // Every click used to toggle. Fine for two faces, unusable for two hundred: picking
  // a run meant two hundred clicks, and one stray click in the middle silently took a
  // face back out of a selection that was about to be assigned.
  const many = [
    face(1, "Rory Olwen", 0.96),
    face(2, "Kira Bao", 0.93),
    face(3, "Rory Olwen", 0.9),
    face(4, "Kira Bao", 0.88),
    face(5, "Rory Olwen", 0.86),
  ];

  async function grid(t) {
    const ctx = await open(t, [{ name: NAME, count: many.length, unit: "face" }], many);
    ctx.cards = () =>
      [...ctx.document.querySelectorAll("#matching-faces-grid .face-match-item")];
    ctx.chosen = () =>
      ctx.cards().filter((c) => c.classList.contains("selected"))
        .map((c) => Number(c.dataset.faceId));
    return ctx;
  }

  const settle = (ctx) => new Promise((r) => ctx.window.setTimeout(r, 30));

  test("a plain click selects only what it lands on", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[0]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[2]);
    await settle(ctx);
    assert.deepEqual(ctx.chosen(), [3]);
  });

  test("clicking the only selected face clears it", async (t) => {
    // A way back to nothing without reaching for the keyboard.
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[1]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[1]);
    await settle(ctx);
    assert.deepEqual(ctx.chosen(), []);
  });

  test("ctrl-click adds one at a time", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[0]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[3], { ctrlKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen().sort(), [1, 4]);
  });

  test("ctrl-click takes one back out", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[0]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[1], { ctrlKey: true });
    await settle(ctx);
    click(ctx.window, ctx.cards()[0], { ctrlKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen(), [2]);
  });

  test("cmd-click does the same, for a Mac", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[0]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[2], { metaKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen().sort(), [1, 3]);
  });

  test("shift-click takes everything between", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[1]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[3], { shiftKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen(), [2, 3, 4]);
  });

  test("a range works backwards too", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[3]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[1], { shiftKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen(), [2, 3, 4]);
  });

  test("a second shift-click re-measures from the same anchor", async (t) => {
    // Rather than growing forever, so overshooting a range is one click to fix.
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[0]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[4], { shiftKey: true });
    await settle(ctx);
    click(ctx.window, ctx.cards()[1], { shiftKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen(), [1, 2]);
  });

  test("ctrl-shift-click adds a range to what is already chosen", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[0]);
    await settle(ctx);
    click(ctx.window, ctx.cards()[3], { ctrlKey: true });
    await settle(ctx);
    click(ctx.window, ctx.cards()[4], { ctrlKey: true, shiftKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen().sort(), [1, 4, 5]);
  });

  test("shift with nothing chosen yet just selects that face", async (t) => {
    const ctx = await grid(t);
    click(ctx.window, ctx.cards()[2], { shiftKey: true });
    await settle(ctx);
    assert.deepEqual(ctx.chosen(), [3]);
  });

  test("the hint says which modifiers do what", async (t) => {
    const ctx = await grid(t);
    assert.match(ctx.document.body.textContent, /Ctrl-click to add/);
    assert.match(ctx.document.body.textContent, /Shift-click for a range/);
  });
});
