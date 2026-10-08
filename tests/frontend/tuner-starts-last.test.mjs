/**
 * TagTuner's main.js wires the page, then starts it at the very end (CLAUDE.md; findings #287).
 *
 * Its start-up handler called fetchPhotos(), fetchKnownPeople() and restoreIndexingState()
 * before wireSidebar() ... wireTunerGear(): the first requests ran without the listeners
 * and the calls up the page that the features add. Only TagPup's main.js was guarded
 * (tag-vocabulary.test.mjs).
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./harness.mjs";

const LINES = fs.readFileSync(path.join(REPO_ROOT, "web", "tuner", "main.js"), "utf8").split(/\r?\n/);
const indexOf = (re) => LINES.findIndex((l) => re.test(l));
const lastIndexOf = (re) => LINES.length - 1 - [...LINES].reverse().findIndex((l) => re.test(l));

describe("TagTuner starts itself last", () => {
  const STARTS = [/^ {4}fetchPhotos\(\);/, /^ {4}fetchKnownPeople\(\);/, /^ {4}restoreIndexingState\(\);/];

  test("the start-up calls are still where this guard expects them", () => {
    for (const re of STARTS) assert.ok(indexOf(re) > 0, `${re} moved or changed shape; update this guard`);
  });

  test("they come after every wire call", () => {
    const lastWire = lastIndexOf(/^ {4}wire\w+\(/);
    assert.ok(lastWire > 0, "no wire call found; update this guard");
    for (const re of STARTS) {
      assert.ok(indexOf(re) > lastWire, `main.js:${indexOf(re) + 1}: ${re} runs before main.js:${lastWire + 1}, a wire call`);
    }
  });

  test("nothing but the closing of the handler follows the first of them", () => {
    const first = Math.min(...STARTS.map(indexOf));
    const rest = LINES.slice(first)
      .filter((l) => l.trim() && !l.trim().startsWith("//") && l.trim() !== "});")
      .filter((l) => !STARTS.some((re) => re.test(l)));
    assert.deepEqual(rest, [], "code runs after the page has started itself");
  });
});
