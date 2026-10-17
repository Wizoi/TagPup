/**
 * A person hidden from autocomplete still exists, and is still not offered while typing.
 *
 * The page asked the autocomplete list whether a name exists, and that list leaves out
 * people hidden from autocomplete -- so naming one of them asked "not currently in the
 * database, create a new person?", and the New Person dialog let a duplicate through. The page
 * reads the people with include_hidden=1, so a hidden person is a person it knows. The list
 * offered while typing is the other read, without include_hidden: once the page held only
 * the first, it offered the hidden person too (docs/findings.md, #1432).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, click, flush, closeAllApps, PEOPLE_ROUTE } from "./harness.mjs";

const HIDDEN = { id: 7, name: "Rowan Thackeray", tag: "People/Rowan Thackeray" };
const SHOWN = { id: 8, name: "Hazel Brookmire", tag: "People/Hazel Brookmire" };

async function open(t) {
  const faces = [{
    id: 1, photo_path: "D:\\xc\\p1.jpg", filename: "p1.jpg", box: [1, 1, 5, 5], prob: 0.99,
    mtime: 0, year: 2026, similarity: 0, cluster_id: -1, cluster_name: "Unclustered",
    other_names: [], suggested_name: null, suggested_similarity: 0, suggestion_strength: null,
  }];
  const server = new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/unmatched-faces/people", [{ name: "Unknown Faces", count: 1, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", {
      faces, total_count: 1, unclustered_total: 1, unclustered_shown: 1, has_more: false,
    })
    .on("/api/people-with-counts", [])
    .on("/api/face-matches-unmatched", { matches: [] })
    .on("/api/taxonomy/tree", [])
    // As tagpup.services.people.records answers: the hidden person only when include_hidden=1 is asked.
    .on(PEOPLE_ROUTE, (url) => (url.includes("include_hidden=1") ? [SHOWN, HIDDEN] : [SHOWN]))
    .on("/api/faces/match-bulk", { success: true, matched_ids: [1] });
  const ctx = await loadApp("tagtuner", {
    t, server, url: "http://localhost:8080/kr-track/?mode=unmatched-faces&person=Unknown%20Faces",
  });
  await new Promise((r) => ctx.window.setTimeout(r, 140));
  return ctx;
}

describe("a hidden person still exists", () => {
  afterEach(() => closeAllApps());

  test("assigning to them asks to assign, not to create", async (t) => {
    const { window, document } = await open(t);
    const asked = [];
    window.confirm = (message) => { asked.push(message); return false; };
    click(window, document.querySelector('#matching-faces-grid [data-face-id="1"]'));
    const input = document.getElementById("input-reassign-name");
    input.value = "Rowan Thackeray";
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    click(window, document.getElementById("btn-reassign-selected"));
    await new Promise((r) => window.setTimeout(r, 30));

    assert.equal(asked.length, 1);
    assert.ok(!/not currently in the database/.test(asked[0]), asked[0]);
  });

  test("they are not offered while typing, and New Person on their name says they exist", async (t) => {
    const { window, document } = await open(t);
    const offered = [...document.querySelectorAll("#people-datalist option")].map((option) => option.value);
    assert.deepEqual(offered, ["Hazel Brookmire"], "the hidden person is not offered");

    click(window, document.querySelector('#matching-faces-grid [data-face-id="1"]'));
    click(window, document.getElementById("btn-new-person"));
    await flush(window, 8);
    const name = document.getElementById("new-person-name");
    name.value = "Rowan Thackeray";
    name.dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.ok(document.getElementById("btn-modal-save").disabled, "no second Rowan Thackeray");
    assert.match(document.getElementById("modal-name-error").textContent,
      /Another person is called Rowan Thackeray \(People\/Rowan Thackeray\)/);
  });
});
