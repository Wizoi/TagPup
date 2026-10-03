/**
 * The order tags are shown in (web/common/vocabulary.js): one table, tests/fixtures/tag_order.json, which
 * tagpup.core.vocabulary.tag_sort_key is held to as well (tests/test_tag_order.py), so the page and the server
 * cannot drift. Plus what the table cannot say: a ranked list keeps its rank and breaks ties alphabetically,
 * and the order does not follow the machine's language.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { compareTagNames, sortedTags } from "../../web/common/vocabulary.js";

const TABLE = JSON.parse(readFileSync(new URL("../fixtures/tag_order.json", import.meta.url), "utf-8"));

for (const each of TABLE.cases) {
  test(`sortedTags: ${each.name}`, () => {
    assert.deepEqual(sortedTags(each.given), each.shown);
    assert.deepEqual(sortedTags([...each.given].reverse()), each.shown);
    assert.deepEqual(sortedTags(each.shown), each.shown);
  });

  test(`compareTagNames agrees with sortedTags: ${each.name}`, () => {
    assert.deepEqual([...each.given].sort(compareTagNames), each.shown);
  });
}

test("sortedTags returns a copy and leaves the list it was given as it was", () => {
  const given = ["b", "a"];
  const shown = sortedTags(given);
  assert.deepEqual(given, ["b", "a"]);
  assert.notEqual(shown, given);
});

test("sortedTags orders objects by the text the key names", () => {
  const items = [{ tag: "Zoo", n: 1 }, { tag: "apple", n: 2 }];
  assert.deepEqual(sortedTags(items, (item) => item.tag).map((item) => item.n), [2, 1]);
});

test("a ranked list keeps its rank, and the alphabet breaks its ties", () => {
  const items = [
    { name: "zed", score: 0.5 }, { name: "Amy", score: 0.5 }, { name: "bob", score: 0.9 },
    { name: "Cat", score: 0.1 },
  ];
  const shown = sortedTags(items, (item) => item.name, { rank: (item) => item.score });
  assert.deepEqual(shown.map((item) => item.name), ["bob", "Amy", "zed", "Cat"]);
});

test("a missing tag is the empty one", () => {
  assert.deepEqual(sortedTags([null, "a", undefined]), [null, undefined, "a"]);
  assert.equal(compareTagNames(undefined, ""), 0);
});
