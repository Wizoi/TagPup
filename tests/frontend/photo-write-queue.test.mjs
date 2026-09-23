/**
 * Every write to a photo's metadata goes through one queue.
 *
 * Each writer -- Enter in a field, a suggestion chip, a face card, Apply All, Ctrl+D,
 * a click on a pill, the date-taken dialog -- used to post its own snapshot of the
 * photo's whole tag list, taken when it was clicked, and redraw whatever photo was
 * open when the server answered. So:
 *
 *   - a chip clicked on A, then an arrow key, drew A's tags and A's chips on B, and
 *     clicking one of those chips wrote A's suggestion to B;
 *   - two quick writes to one photo raced, and whichever landed last dropped the
 *     other's change;
 *   - a second Save chained behind the first was not waited for, so the "Save
 *     changes?" question could offer Discard for text that was then written anyway.
 *
 * The server's save-metadata replies are held back here until the test releases
 * them, which is what makes "a reply that lands after the arrow key" reproducible.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click } from "./harness.mjs";

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Imogen Vale", name: "Imogen Vale", parent_id: 1, has_face: 1 },
  { id: 3, tag: "Cross Country", name: "Cross Country", parent_id: null, has_face: 0 },
  { id: 4, tag: "Harbourside", name: "Harbourside", parent_id: null, has_face: 0 },
  { id: 5, tag: "Kentridge", name: "Kentridge", parent_id: null, has_face: 0 },
];

const A = "D:\\p\\a.jpg";
const B = "D:\\p\\b.jpg";

function photos(aTags = []) {
  return [
    { path: A, filename: "a.jpg", tags: [...aTags], people: [], captions: [], title: "" },
    { path: B, filename: "b.jpg", tags: [], people: [], captions: [], title: "" },
  ];
}

const SUGGESTIONS = {
  [A]: { people: [], tags: [{ tag: "Cross Country", score: 0.9 }, { tag: "Harbourside", score: 0.8 }] },
  [B]: { people: [], tags: [{ tag: "Kentridge", score: 0.9 }] },
};

function server(aTags) {
  return new FakeServer()
    .on("/api/folder/scan", photos(aTags))
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/people", ["Imogen Vale"])
    .on("/api/tags", ["Cross Country", "Harbourside", "Kentridge"])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/folder/suggest-status", { status: "completed", suggestions: SUGGESTIONS });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

/**
 * Hold every save-metadata reply until released. The request is still recorded
 * the moment it is sent; only the answer waits.
 */
function holdSaves(window) {
  const inner = window.fetch;
  const held = [];
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : String(input && input.url);
    const reply = inner.call(this, input, init);
    if (!url.includes("save-metadata")) return reply;
    return new Promise((resolve) => held.push(() => resolve(reply)));
  };
  return {
    held,
    /** Answer the oldest held request. */
    releaseOne() {
      const next = held.shift();
      if (next) next();
      return Boolean(next);
    },
  };
}

/** Release held replies, oldest first, until nothing more is sent. */
async function releaseAll(window, gate) {
  for (let i = 0; i < 20; i++) {
    await settle(window, 30);
    if (!gate.releaseOne()) {
      await settle(window, 30);
      if (!gate.held.length) return;
    }
  }
}

async function onPhotoA(t, aTags = []) {
  const s = server(aTags);
  const { window, document } = await loadApp("tagpup", { server: s, t });
  window.alert = () => {};
  await openFolder({ document, window }, "D:\\p");
  await settle(window, 60);
  click(window, document.querySelector(`li[data-path="${CSS_escape(A)}"]`));
  await settle(window, 60);
  assert.equal(activePath(document), A);
  const gate = holdSaves(window);
  return { window, document, server: s, gate };
}

function CSS_escape(v) {
  return v.replace(/\\/g, "\\\\");
}

function activePath(document) {
  const el = document.querySelector(".photo-item-file.active");
  return el && el.getAttribute("data-path");
}

function press(document, key, target, mods = {}) {
  const ev = new document.defaultView.KeyboardEvent("keydown", {
    key, bubbles: true, cancelable: true, ...mods,
  });
  (target || document.activeElement || document).dispatchEvent(ev);
  return ev;
}

function type(window, input, text) {
  input.focus();
  input.value = text;
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
}

const saves = (s) => s.calls.filter((c) => c.url.includes("save-metadata"));
const pills = (document) =>
  [...document.querySelectorAll("#detail-tags .tag-pill, #detail-people .tag-pill")].map((p) => p.textContent);
const chipEls = (document) => [...document.querySelectorAll("#suggestions-section .suggestion-chip")];
const chip = (document, name) => chipEls(document).find((c) => c.textContent.includes(name));

afterEach(() => closeAllApps());

describe("a write to A answered after moving to B", () => {
  test("draws nothing of A on B, and B's chips apply only B's suggestions", async (t) => {
    const { window, document, server: s, gate } = await onPhotoA(t);
    click(window, chip(document, "Cross Country"));
    await settle(window);
    document.activeElement && document.activeElement.blur && document.activeElement.blur();
    press(document, "ArrowDown", document.body);
    await settle(window);

    await releaseAll(window, gate);
    assert.equal(activePath(document), B, "never reached B");

    assert.ok(!pills(document).includes("Cross Country"),
      `A's tags drawn on B: ${pills(document).join(", ")}`);
    const offered = chipEls(document).map((c) => c.textContent).join(" | ");
    assert.ok(!/Harbourside|Cross Country/.test(offered), `A's suggestions offered on B: ${offered}`);

    // Take whatever B offers; none of it may be anything but B's own.
    for (const c of chipEls(document)) {
      click(window, c);
      await releaseAll(window, gate);
    }
    for (const call of saves(s).filter((c) => c.body.path === B)) {
      for (const tag of call.body.tags) {
        assert.equal(tag, "Kentridge", `wrote ${tag} to B`);
      }
    }
    const toA = saves(s).filter((c) => c.body.path === A);
    assert.ok(toA.length >= 1 && toA.every((c) => c.body.tags.includes("Cross Country")),
      "A's own write did not go to A");
  });
});

describe("a chip drawn for one photo", () => {
  test("does nothing once another photo is open", async (t) => {
    // Whatever route leaves one behind, a chip is bound to the photo it was drawn
    // for. It used to read the open photo when clicked.
    const { window, document, server: s, gate } = await onPhotoA(t);
    const stale = chip(document, "Harbourside");
    press(document, "ArrowDown", document.body);
    await settle(window);
    assert.equal(activePath(document), B);

    click(window, stale);
    await releaseAll(window, gate);
    const toB = saves(s).filter((c) => c.body.path === B);
    assert.deepEqual(toB.map((c) => c.body.tags), [], "A's chip wrote to B");
  });
});

describe("two quick writes to one photo", () => {
  test("removing two pills in a row removes both", async (t) => {
    const { window, document, server: s, gate } = await onPhotoA(t, ["Cross Country", "Harbourside"]);
    const pill = (name) => [...document.querySelectorAll("#detail-tags .tag-pill")]
      .find((p) => p.textContent === name);
    click(window, pill("Cross Country"));
    click(window, pill("Harbourside"));
    await releaseAll(window, gate);

    const last = saves(s).at(-1);
    assert.deepEqual(last.body.tags, [], `the last write left: ${last.body.tags}`);
  });

  test("Enter on a typed tag, then a suggestion chip, keeps both", async (t) => {
    const { window, document, server: s, gate } = await onPhotoA(t);
    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Places/Beach");
    press(document, "Enter", tagInput);
    await settle(window);
    click(window, chip(document, "Cross Country"));
    await releaseAll(window, gate);

    const last = saves(s).at(-1);
    assert.ok(last.body.tags.includes("Places/Beach"), `lost the typed tag: ${last.body.tags}`);
    assert.ok(last.body.tags.includes("Cross Country"), `lost the chip: ${last.body.tags}`);
  });
});

describe("leaving during a chained save", () => {
  test("Discard is never offered for text that is then written anyway", async (t) => {
    const { window, document, server: s, gate } = await onPhotoA(t);
    const tagInput = document.getElementById("input-add-tag");
    type(window, tagInput, "Places/Beach");
    press(document, "Enter", tagInput);
    await settle(window);
    type(window, tagInput, "Places/Dunes");
    press(document, "Enter", tagInput);
    tagInput.blur();
    press(document, "ArrowDown", document.body);
    await settle(window);

    let discarded = false;
    for (let i = 0; i < 20; i++) {
      gate.releaseOne();
      await settle(window, 30);
      const ask = document.querySelector(".unsaved-edits-modal.active");
      if (ask) {
        click(window, ask.querySelector('[data-choice="discard"]'));
        discarded = true;
      }
    }

    const wroteDunes = saves(s).some((c) => c.body.path === A && c.body.tags.includes("Places/Dunes"));
    assert.ok(!(discarded && wroteDunes), "Discard was chosen, and the discarded tag was written");
    assert.ok(wroteDunes || discarded, "Enter on the tag neither wrote it nor asked");
    assert.equal(activePath(document), B);
  });
});
