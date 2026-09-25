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
 * live in a few named helpers -- web/common/vocabulary.js's, and TagPup's own
 * normalizeTag and ancestorsOf -- and a raw `.split('/')` anywhere else in a page's
 * modules fails here.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, APPS, loadApp, FakeServer, flush, pageModules } from "./harness.mjs";

const APP_JS = path.join(REPO_ROOT, "web", "tagpup", "main.js");
const SOURCE = fs.readFileSync(APP_JS, "utf8");
const LINES = SOURCE.split(/\r?\n/);
const VOCABULARY_JS = fs.readFileSync(path.join(REPO_ROOT, "web", "common", "vocabulary.js"), "utf8");

/** The helpers that are allowed to split a tag, by the name they are declared with. */
const VOCABULARY = ["leafOf", "rootOf", "ancestorsOf", "normalizeTag"];

/** Helpers that split something that is not a tag: api.js reading the library out of the URL. */
const NOT_TAGS = ["libraryIn"];

/** Each module of a page, its lines, as the browser loads them. */
function modulesOf(app) {
  return pageModules(path.join(REPO_ROOT, APPS[app].dir)).map((m) => ({
    name: path.relative(REPO_ROOT, m.file).split(path.sep).join("/"),
    lines: fs.readFileSync(m.file, "utf8").split(/\r?\n/),
  }));
}

function declaredAbove(lines, lineIndex) {
  // Walk back to the nearest `function <name>(` and see whose it is.
  for (let i = lineIndex; i >= 0 && i > lineIndex - 12; i--) {
    const declared = lines[i].match(/^\s*(?:export\s+)?function\s+(\w+)\s*\(/);
    if (declared) return declared[1];
  }
  return null;
}

function handSplits(app) {
  const offenders = [];
  for (const { name, lines } of modulesOf(app)) {
    lines.forEach((line, i) => {
      if (!line.includes("split('/')") && !line.includes('split("/")')) return;
      if (/^\s*(\/\/|\/?\*)/.test(line)) return;   // a comment says what it likes
      const owner = declaredAbove(lines, i);
      if (VOCABULARY.includes(owner) || NOT_TAGS.includes(owner)) return;
      offenders.push(`${name}:${i + 1}: ${line.trim()}`);
    });
  }
  return offenders;
}

describe("the tag vocabulary is used, not reinvented", () => {
  test("nothing outside the helpers splits a tag by hand", () => {
    const offenders = handSplits("tagpup");
    assert.deepEqual(
      offenders,
      [],
      "these convert between a person's name and their tag by hand instead of " +
        "using leafOf / rootOf / ancestorsOf / samePerson / photoAlreadyHas:\n" +
        offenders.join("\n")
    );
  });

  test("TagTuner's page keeps the same rule, with the same helpers", () => {
    // It converted a keyword to a name by hand in the photo panel, and the rule
    // above only read TagPup's page.
    const offenders = handSplits("tagtuner");
    assert.deepEqual(offenders, [], "split by hand instead of leafOf:\n" + offenders.join("\n"));
  });

  test("the helpers themselves are allowed to", () => {
    // The check above is worthless if it matches nothing anywhere.
    assert.ok(
      VOCABULARY_JS.includes("export function leafOf"),
      "leafOf is gone; the guard above is now checking a rule nobody follows"
    );
    assert.ok(/function leafOf[\s\S]{0,200}split\('\/'\)/.test(VOCABULARY_JS));
  });

  test("every helper the guard exempts actually exists", () => {
    const everything = [...modulesOf("tagpup"), ...modulesOf("tagtuner")]
      .map((m) => m.lines.join("\n")).join("\n");
    for (const name of [...VOCABULARY, ...NOT_TAGS]) {
      assert.ok(
        new RegExp(`function ${name}\\(`).test(everything),
        `the guard exempts ${name}, which does not exist -- a stale exemption is a hole`
      );
    }
  });
});

describe("the app starts itself last", () => {
  /**
   * CLAUDE.md: a page's main.js only wires it, and starts it at the very end.
   *
   * Opening the ?path= folder on load calls into most of the page -- through the
   * listeners main.js adds and the calls up the page it fills in (hooks.js). Started
   * before those lines, it runs without them. This guard began as one against a
   * closure `let` reached before its line had run; main.js now declares no state
   * (tests/frontend/page-state.test.mjs), so that bug cannot happen here, and what
   * remains is the rule's position: the start-up block, then nothing (#164).
   */
  const anchor = LINES.findIndex((l) =>
    /^ {4}const params = new URLSearchParams\(window\.location\.search\);/.test(l)
  );

  test("the startup block is still where this guard expects it", () => {
    assert.ok(anchor > 0, "the startup block moved or changed shape; update this guard");
    assert.ok(
      LINES.slice(anchor).some((l) => l.includes("checkIndexingStatus(initialPath)")),
      "startup no longer picks up a running index; update this guard"
    );
  });

  test("nothing but the closing brace follows it", () => {
    const end = LINES.findIndex((l, i) => i > anchor && /^ {4}\}$/.test(l));
    assert.ok(end > anchor, "could not find the end of the startup block");

    const after = LINES.slice(end + 1)
      .map((l, i) => [l.trim(), end + i + 2])
      .filter(([text]) => text && text !== "}" && text !== "});" && !text.startsWith("//"));

    assert.deepEqual(
      after.map(([text, line]) => `main.js:${line}: ${text}`),
      [],
      "code runs after the app has started itself; move the startup block back to " +
        "the very end of the closure"
    );
  });
});
