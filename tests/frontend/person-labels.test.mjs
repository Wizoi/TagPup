/**
 * Where two people share a leaf, the page shows the group beside the name, and only then: `Sam · Thackeray`
 * (docs/ARCHITECTURE.md, "People by id, stage 2", "Showing the group"). The server decides who shares and which
 * tail of the path tells them apart (tagpup.core.vocabulary.person_labels, over everyone in the library); the page
 * joins what it is sent, in personLabel (web/common/vocabulary.js), and the full tag is personTitle. This table
 * (tests/fixtures/person_labels.json) is the one tests/test_person_labels.py holds the server to.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { personLabel, personTitle, GROUP_SEPARATOR } from "../../web/common/vocabulary.js";

const TABLE = JSON.parse(readFileSync(new URL("../fixtures/person_labels.json", import.meta.url), "utf-8"));

/** A person as the server sends one: {id, name, tag, group, shared}. */
function sent(tag, expected, id) {
  return { id, name: tag.split("/").pop(), tag, group: expected.group, shared: expected.shared };
}

describe("the table both halves are held to", () => {
  for (const found of TABLE.cases) {
    test(found.why, () => {
      found.people.forEach((tag, number) => {
        const expected = found.expect[tag];
        const person = sent(tag, expected, number + 1);
        assert.equal(personLabel(person), expected.label, tag);
        assert.equal(personTitle(person), expected.title, tag);
      });
    });
  }
});

describe("the string", () => {
  test("a middle dot between the name and the group, as the owner chose", () => {
    assert.equal(GROUP_SEPARATOR, " · ");
    assert.equal(personLabel({ name: "Sam", group: "Thackeray", shared: true }), "Sam · Thackeray");
  });

  test("a group is shown only when the leaf is shared", () => {
    assert.equal(personLabel({ name: "Sam", group: "Thackeray", shared: false }), "Sam");
    assert.equal(personLabel({ name: "Sam", group: "", shared: true }), "Sam", "shared, and the server could not say which");
  });

  test("a person the server sent no fields for is their name; nobody is nothing", () => {
    assert.equal(personLabel({ name: "Sam" }), "Sam");
    assert.equal(personLabel(null), "");
    assert.equal(personLabel(undefined), "");
    assert.equal(personTitle({ name: "Sam" }), "Sam");
    assert.equal(personTitle(null), "");
  });

  test("markup in a name or a group is text, as the string is, for the page to put in with textContent", () => {
    const person = { name: "<b>Sam</b>", group: "<i>x</i>", shared: true };
    assert.equal(personLabel(person), "<b>Sam</b> · <i>x</i>");
  });
});
