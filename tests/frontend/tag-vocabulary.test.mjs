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

describe("a variable is declared before anything can reach it", () => {
  test("state used during startup is declared above the startup code", () => {
    // scanFolder() runs during init when the page carries a ?path=, roughly 900 lines
    // above where facesRequestToken used to be declared with `let`. Reaching a `let`
    // before its declaration has run throws "Cannot access X before initialization",
    // and inside scanFolder's promise chain that surfaced as "Error scanning folder"
    // -- leaving the folder unopenable and looking nothing like its cause.
    const startupLine = LINES.findIndex((l) => /^\s*scanFolder\(false\);/.test(l));
    assert.ok(startupLine > 0, "startup no longer scans on a ?path=; update this test");

    // Once the app starts itself, any closure-level binding declared further down is
    // only safe if nothing above it reads it -- because a function defined above can
    // be *called* from startup long before the body reaches the declaration. That is
    // the invariant that broke, so it is checked directly: a binding declared after
    // startup must not be named on any earlier line.
    const offenders = [];
    LINES.forEach((line, i) => {
      if (i <= startupLine) return;
      const declared = line.match(/^ {4}(?:let|const)\s+(\w+)\s*=/);
      if (!declared) return;
      const name = declared[1];
      const pattern = new RegExp(`\\b${name}\\b`);
      const usedAbove = LINES.slice(0, i).some(
        (earlier) => pattern.test(earlier) && !/^\s*(\/\/|\*|\/\*)/.test(earlier)
      );
      if (usedAbove) offenders.push(`app.js:${i + 1}: ${name}`);
    });

    assert.deepEqual(
      offenders,
      [],
      "read above the line that declares them, while the app starts itself further " +
        "up -- reaching one of these before its declaration runs throws 'Cannot " +
        "access X before initialization'. Declare them with the other state at the " +
        "top:\n" + offenders.join("\n")
    );
  });
});

describe("the vocabulary behaves", () => {
  // Driven through the real app so these test the shipped helpers, not a copy.
  async function app(t) {
    const ctx = await loadApp("tagpup", {
      t,
      server: new FakeServer()
        .on("/api/tags", [])
        .on("/api/people", [])
        .on("/api/taxonomy/tree", [])
        .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" }),
    });
    await flush(ctx.window, 2);
    return ctx;
  }

  test("the app starts without a startup error", async (t) => {
    const ctx = await app(t);
    const bad = ctx.consoleErrors.filter((e) =>
      String(e).includes("before initialization")
    );
    assert.deepEqual(bad, [], `startup threw: ${bad.join("; ")}`);
  });
});
