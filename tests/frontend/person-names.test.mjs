/**
 * A person is shown by their name; the full tag is where they are filed (docs/ARCHITECTURE.md, "People by id, stage 2"). Two
 * people called alike are told apart by the tag in the title and by the "which one?" question (person-choice.js), not by a group
 * joined to the name (owner, 2026-10-10: the leaf is never identity, and a shared leaf is fixed by hand).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { personLabelOf, personTitle } from "../../web/common/vocabulary.js";

test("a person is shown by their name and titled by their tag", () => {
  const person = { id: 41, name: "Sam", tag: "Pets/Sam" };
  assert.equal(personLabelOf({ name: "Sam", person }), "Sam");
  assert.equal(personTitle(person), "Pets/Sam");
});

test("a person with no tag is titled by the name; nobody is nothing", () => {
  assert.equal(personLabelOf({ name: "Sam" }), "Sam");
  assert.equal(personTitle({ name: "Sam" }), "Sam");
  assert.equal(personLabelOf(null), "");
  assert.equal(personTitle(undefined), "");
});

test("markup in a name is text, for the page to put in with textContent", () => {
  assert.equal(personLabelOf({ name: "<b>Sam</b>" }), "<b>Sam</b>");
});
