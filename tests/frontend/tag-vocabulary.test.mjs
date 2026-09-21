/**
 * One place decides how this app talks about a tag.
 *
 * A person has two shapes and they are not interchangeable: their identity is a leaf
 * ("Hazel Brookmire"), which is what the faces table, the suggester and photo.people
 * speak in, and their tag is a path ("People/Hazel Brookmire"), which is what the
 * keywords must hold and what the server matches on, exactly.
 *
 * Every bug in this area has been a site converting between the two by hand and
 * getting it slightly wrong, and each one looked like a different bug:
 *
 *   - clicking a recognised face added the person a second time, bare, because the
 *     duplicate check compared a leaf against a path;
 *   - the x on a selection chip removed nothing, because it sent the leaf to a server
 *     that removes by exact match -- and rewrote every selected file to achieve it;
 *   - a suggested "Activity/Cross Country" was written as "Cross Country", because the
 *     line that reduced people to leaves reduced keywords too.
 *
 * Fixing them one at a time is how a whole evening goes. These tests are the
 * structural fix, in the shape that already worked for the database: the conversions
 * live in one block, and a raw `.split('/')` anywhere else in the file fails here.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, loadApp, FakeServer, flush } from "./harness.mjs";

const APP_JS = path.join(REPO_ROOT, "gui_tagpup", "app.js");
const SOURCE = fs.readFileSync(APP_JS, "utf8");
const LINES = SOURCE.split(/\r?\n/);

/** The helpers that are allowed to split a tag, by the name they are declared with. */
const VOCABULARY = ["leafOf", "rootOf", "ancestorsOf"];

function inVocabularyBlock(lineIndex) {
  // Walk back to the nearest `function <name>(` and see whether it is one of ours.
  for (let i = lineIndex; i >= 0 && i > lineIndex - 12; i--) {
    const declared = LINES[i].match(/^\s*function\s+(\w+)\s*\(/);
    if (declared) return VOCABULARY.includes(declared[1]);
  }
  return false;
}

describe("the tag vocabulary is used, not reinvented", () => {
  test("nothing outside the helpers splits a tag by hand", () => {
    const offenders = [];
    LINES.forEach((line, i) => {
      if (!line.includes("split('/')") && !line.includes('split("/")')) return;
      // Reading the database name out of the page URL is a different thing entirely.
      if (line.includes("window.location.pathname")) return;
      if (line.trimStart().startsWith("//")) return;
      if (inVocabularyBlock(i)) return;
      offenders.push(`app.js:${i + 1}: ${line.trim()}`);
    });

    assert.deepEqual(
      offenders,
      [],
      "these convert between a person's name and their tag by hand instead of " +
        "using leafOf / rootOf / ancestorsOf / samePerson / photoAlreadyHas:\n" +
        offenders.join("\n")
    );
  });

  test("the helpers themselves are allowed to", () => {
    // The check above is worthless if it matches nothing anywhere.
    assert.ok(
      SOURCE.includes("function leafOf"),
      "leafOf is gone; the guard above is now checking a rule nobody follows"
    );
    assert.ok(/function leafOf[\s\S]{0,200}split\('\/'\)/.test(SOURCE));
  });

  test("every helper the guard exempts actually exists", () => {
    for (const name of VOCABULARY) {
      assert.ok(
        SOURCE.includes(`function ${name}`),
        `the guard exempts ${name}, which does not exist -- a stale exemption is a hole`
      );
    }
  });
});

describe("the app starts itself last", () => {
  /**
   * This one is worth the trouble, because its failures name the wrong line.
   *
   * Opening the ?path= folder on load calls into most of the app. Run from the middle
   * of the closure, it reaches `let` bindings declared further down, and a `let`
   * reached early does not read as undefined -- it throws, taking the rest of the
   * closure's body with it. Every binding below that point is then permanently
   * uninitialised, so the error a user sees comes from some later, unrelated line.
   *
   * That is exactly what happened: checkIndexingStatus touched indexProgressTimer and
   * threw, so facesRequestToken was never initialised either, and the visible failure
   * was "Error scanning folder: Cannot access 'facesRequestToken' before
   * initialization" -- two removes from the line at fault. Which is why the rule is
   * positional rather than a hunt for which bindings are safe: nothing runs the app
   * until everything is defined.
   */
  const anchor = LINES.findIndex((l) =>
    /^ {4}const params = new URLSearchParams\(window\.location\.search\);/.test(l)
  );
  // Declared in the startup block itself, so below the anchor by definition.
  const OWN = new Set(["params", "initialPath"]);

  test("the startup block is still where this guard expects it", () => {
    assert.ok(anchor > 0, "the startup block moved or changed shape; update this guard");
    assert.ok(
      LINES.slice(anchor).some((l) => l.includes("checkIndexingStatus(initialPath)")),
      "startup no longer picks up a running index; update this guard"
    );
  });

  test("no closure-level state is declared after it", () => {
    const offenders = [];
    LINES.forEach((line, i) => {
      if (i <= anchor) return;
      const declared = line.match(/^ {4}(?:let|const)\s+(\w+)\s*=/);
      if (declared && !OWN.has(declared[1])) {
        offenders.push(`app.js:${i + 1}: ${declared[1]}`);
      }
    });

    assert.deepEqual(
      offenders,
      [],
      "declared after the app starts itself, so startup can reach them before this " +
        "line has run: " + offenders.join(", ")
    );
  });

  test("nothing but the closing brace follows it", () => {
    const end = LINES.findIndex((l, i) => i > anchor && /^ {4}\}$/.test(l));
    assert.ok(end > anchor, "could not find the end of the startup block");

    const after = LINES.slice(end + 1)
      .map((l, i) => [l.trim(), end + i + 2])
      .filter(([text]) => text && text !== "}" && text !== "});" && !text.startsWith("//"));

    assert.deepEqual(
      after.map(([text, line]) => `app.js:${line}: ${text}`),
      [],
      "code runs after the app has started itself; move the startup block back to " +
        "the very end of the closure"
    );
  });
});
