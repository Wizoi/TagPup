/**
 * People by id on the pages (docs/ARCHITECTURE.md, "People by id, stage 2", part C, phase 6): the library's people held as
 * /api/people?records=1 answers them, and every question about "the same person" asked of their ids -- never of a label or a leaf.
 *
 * The answers here are the server's real shapes (tagpup.store.person_ids.Directory): the owner's real case is one leaf shared by
 * a pet and a friend, so the table has a Sam under Pets and a Sam under Friends, beside a cousin pair and people nobody shares a
 * name with. Names are fictional.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import {
  PeopleDirectory, personFields, personLabelOf, personTitleOf, photoAlreadyHas, sameRecord, sameTagPerson,
} from "../../web/common/vocabulary.js";

const PEOPLE = [
  { id: 31, name: "Rowan Thackeray", tag: "Family/Thackeray/Rowan Thackeray", group: "", shared: false },
  { id: 40, name: "Sam", tag: "Friends/Sam", group: "Friends", shared: true },
  { id: 41, name: "Sam", tag: "Pets/Sam", group: "Pets", shared: true },
  { id: 52, name: "Wren", tag: "Family/Ingersoll/Wren", group: "", shared: false },
];

const isPerson = (tag) => /^(Family|Friends|Pets)\//.test(tag) || ["Sam", "Wren"].includes(tag);

describe("the directory", () => {
  const directory = new PeopleDirectory(PEOPLE);

  test("a tag path names exactly one person, a shared bare name names none", () => {
    assert.equal(directory.ofText("Friends/Sam").id, 40);
    assert.equal(directory.ofText("Pets/Sam").id, 41);
    assert.equal(directory.ofText("friends/sam").id, 40, "without case");
    assert.equal(directory.ofText("Sam"), null, "two people are called Sam");
    assert.equal(directory.ofText("Wren").id, 52, "a bare name one person has");
    assert.equal(directory.ofText("Nobody Atall"), null);
    assert.equal(directory.ofText(""), null);
  });

  test("everyone called a name, the one person called it, and whether it is shared", () => {
    assert.deepEqual(directory.called("sam").map((each) => each.id), [40, 41]);
    assert.equal(directory.only("Sam"), null);
    assert.equal(directory.only("Rowan Thackeray").id, 31);
    assert.equal(directory.shared("Sam"), true);
    assert.equal(directory.shared("Wren"), false);
    assert.equal(directory.ofId(41).tag, "Pets/Sam");
    assert.equal(directory.ofId(9999), null);
    assert.equal(directory.ofId(null), null);
  });

  test("only the people with a tag are held: a name, a failed lookup's object, a record with no id are let go", () => {
    assert.equal(new PeopleDirectory(["Sam", "Wren"]).size, 0);
    assert.equal(new PeopleDirectory({ error: "no library" }).size, 0);
    assert.equal(new PeopleDirectory([{ id: null, name: "Sam", tag: null, group: "", shared: true }]).size, 0);
    assert.equal(new PeopleDirectory(undefined).size, 0);
  });
});

describe("the same person", () => {
  const directory = new PeopleDirectory(PEOPLE);

  test("by id: the friend and the pet called Sam are two", () => {
    assert.equal(sameTagPerson("Friends/Sam", "Pets/Sam", directory), false);
    assert.equal(sameTagPerson("Friends/Sam", "Friends/Sam", directory), true);
    assert.equal(sameTagPerson("Wren", "Family/Ingersoll/Wren", directory), true, "a bare name one person has is that person");
  });

  test("a bare name two people have is neither of them for certain", () => {
    assert.equal(sameTagPerson("Sam", "Friends/Sam", directory), false);
    assert.equal(sameTagPerson("Pets/Sam", "Sam", directory), false);
  });

  test("a text no person has is its leaf, as before, unless the leaf is somebody's", () => {
    assert.equal(sameTagPerson("People/Fenn Ashdown", "Fenn Ashdown", directory), true);
    assert.equal(sameTagPerson("People/Sam", "Friends/Sam", directory), false, "People/Sam is no person's tag; Sam is somebody's");
  });

  test("without the people the old rule holds: by leaf", () => {
    assert.equal(sameTagPerson("Friends/Sam", "Pets/Sam", null), true);
    assert.equal(sameTagPerson("Friends/Sam", "Pets/Sam", new PeopleDirectory([])), true);
  });

  test("two records are one person by id; two with no id by name; never one with and one without", () => {
    assert.equal(sameRecord(PEOPLE[1], { ...PEOPLE[1], group: "other" }), true);
    assert.equal(sameRecord(PEOPLE[1], PEOPLE[2]), false);
    assert.equal(sameRecord({ id: null, name: "Fenn" }, { id: null, name: "fenn" }), true);
    assert.equal(sameRecord({ id: null, name: "Sam" }, PEOPLE[1]), false);
    assert.equal(sameRecord(null, PEOPLE[1]), false);
  });
});

describe("a photo that already has a person", () => {
  const directory = new PeopleDirectory(PEOPLE);

  test("the friend Sam on the photo does not stop the pet Sam being added", () => {
    const photo = { tags: ["Friends/Sam", "Places/Coast"] };
    assert.equal(photoAlreadyHas(photo, "Pets/Sam", isPerson, directory), false);
    assert.equal(photoAlreadyHas(photo, "Friends/Sam", isPerson, directory), true);
  });

  test("the old rule, with no directory, still takes them for one (the leaf)", () => {
    assert.equal(photoAlreadyHas({ tags: ["Friends/Sam"] }, "Pets/Sam", isPerson), true);
  });

  test("a bare name already on the photo is that person only when it is one person's", () => {
    assert.equal(photoAlreadyHas({ tags: ["Wren"] }, "Family/Ingersoll/Wren", isPerson, directory), true);
    assert.equal(photoAlreadyHas({ tags: ["Sam"] }, "Pets/Sam", isPerson, directory), false);
  });
});

describe("what a row is shown as, and how a request names a person", () => {
  test("the label of the nested person when the row has one, else the name it holds", () => {
    assert.equal(personLabelOf({ name: "Sam", person: PEOPLE[1] }), "Sam · Friends");
    assert.equal(personLabelOf({ name: "Sam", person: PEOPLE[2] }), "Sam · Pets");
    assert.equal(personLabelOf({ name: "Sam", person: { id: null, name: "Sam", tag: null, group: "", shared: true } }), "Sam");
    assert.equal(personLabelOf({ name: "Fenn", person: null }), "Fenn");
    assert.equal(personLabelOf({ name: "Fenn" }), "Fenn");
    assert.equal(personLabelOf({ person: PEOPLE[0] }), "Rowan Thackeray");
    assert.equal(personLabelOf({ who: "Fenn" }, "who"), "Fenn");
    assert.equal(personLabelOf(null), "");
    assert.equal(personTitleOf({ name: "Sam", person: PEOPLE[2] }), "Pets/Sam");
    assert.equal(personTitleOf({ name: "Fenn", person: null }), "Fenn");
  });

  test("a request sends the id when there is one, the name only when there is not", () => {
    assert.deepEqual(personFields(PEOPLE[2]), { person_id: 41 });
    assert.deepEqual(personFields({ id: null, name: "Fenn Ashdown" }), { person_name: "Fenn Ashdown" });
    assert.deepEqual(personFields({ name: "Fenn Ashdown" }), { person_name: "Fenn Ashdown" });
    assert.deepEqual(personFields("Fenn Ashdown"), { person_name: "Fenn Ashdown" });
    assert.deepEqual(personFields({ id: 0, name: "Zero" }), { person_id: 0 });
  });
});
