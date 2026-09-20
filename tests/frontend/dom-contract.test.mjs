/**
 * The apps and their markup must agree.
 *
 * Both apps accumulated references to elements that were never built: TagTuner looked up
 * `notperson-toggle-group`, and TagPup held a `faces-section` handle, for elements absent
 * from their index.html. `getElementById` returns null rather than throwing, and the code
 * around these was written defensively (`if (el) ...`), so a half-removed or
 * never-finished feature leaves no trace at runtime and no failing test. It just sits
 * there looking load-bearing.
 *
 * This is the cheapest possible check: every id the script asks for must exist in the
 * page it ships with. It catches a feature deleted from the markup but not the script,
 * a typo'd id, and a stub left behind when an idea was abandoned.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, APPS } from "./harness.mjs";

/** Ids created by the script itself rather than declared in the markup. */
const DYNAMIC_IDS = new Set([]);

function readApp(dir) {
  return {
    js: fs.readFileSync(path.join(REPO_ROOT, dir, "app.js"), "utf8"),
    html: fs.readFileSync(path.join(REPO_ROOT, dir, "index.html"), "utf8"),
  };
}

function declaredIds(html) {
  return new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
}

function referencedIds(js) {
  return [...js.matchAll(/getElementById\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1]);
}

/** Ids the script creates at runtime, e.g. `el.id = "foo"`. */
function assignedIds(js) {
  return new Set([...js.matchAll(/\.id\s*=\s*['"]([^'"]+)['"]/g)].map((m) => m[1]));
}

for (const [name, app] of Object.entries(APPS)) {
  describe(`${name}: DOM contract`, () => {
    test("every element the script looks up exists in the page", () => {
      const { js, html } = readApp(app.dir);
      const declared = declaredIds(html);
      const created = assignedIds(js);

      const dangling = [...new Set(referencedIds(js))].filter(
        (id) => !declared.has(id) && !created.has(id) && !DYNAMIC_IDS.has(id)
      );

      assert.deepEqual(
        dangling,
        [],
        `${app.dir}/app.js looks up ${dangling.length} element(s) that ${app.dir}/index.html ` +
          `does not define: ${dangling.join(", ")}. Either the markup was removed and the ` +
          `script was not, or the feature was never finished -- delete the dead lookup, or ` +
          `add the element.`
      );
    });

    test("the check is actually looking at something", () => {
      // A regex that stopped matching would make the guard above pass silently.
      const { js, html } = readApp(app.dir);
      assert.ok(referencedIds(js).length > 20, "no getElementById calls parsed");
      assert.ok(declaredIds(html).size > 20, "no ids parsed from the markup");
    });
  });
}

describe("retired concepts stay retired", () => {
  test("no app offers an 'Unmatched' pseudo-person", () => {
    // Nameless faces belong to the Identify Faces queue, which groups them. A flat
    // dump of every unnamed face inside the people list is what this replaced.
    const tuner = fs.readFileSync(
      path.join(REPO_ROOT, "scripts", "tuner_server.py"),
      "utf8"
    );
    assert.ok(
      !/people_counts\.insert\(0,\s*\{"name":\s*"Unmatched"/.test(tuner),
      "the Unmatched pseudo-person is back in the people list"
    );
  });

  test("no code still branches on the retired 'Unmatched' person", () => {
    // These comparisons sat in the Review People path, where activePersonName comes
    // from the people list. Once the pseudo-person was removed from that list none of
    // them could ever be true, so they were dead branches that read as live ones --
    // nine of them, each implying a mode the app no longer has.
    const js = fs.readFileSync(path.join(REPO_ROOT, "gui", "app.js"), "utf8");
    const comparisons = [...js.matchAll(/[!=]==\s*['"]Unmatched['"]/g)];
    assert.equal(
      comparisons.length,
      0,
      `${comparisons.length} comparison(s) against 'Unmatched' are back. Nameless ` +
        `faces are reached through Identify Faces, whose buckets are named ` +
        `'Unknown Faces', 'Ungrouped' and 'Excluded'.`
    );
  });

  test("the 'Non Person' marker is carried onto the excluded column, not discarded", () => {
    // The old migration cleared these to NULL, which returned deliberately rejected
    // faces to the matching pool. They mean what `excluded` means.
    const index = fs.readFileSync(path.join(REPO_ROOT, "scripts", "index.py"), "utf8");
    const migration = index.slice(index.indexOf("Non Person"));
    assert.match(
      migration,
      /excluded = 1/,
      "the Non Person migration no longer preserves the exclusion"
    );
  });
});

describe("sidebar refreshes follow the selected mode", () => {
  test("no handler bypasses the mode dispatcher", () => {
    // fetchPhotos() reads the Tune target and dispatches; fetchPeopleWithCounts()
    // renders the people list unconditionally. Calling the latter after a background
    // job finished put the people list in the sidebar while the dropdown still said
    // "Folder Matches" -- the two disagreeing about what you were looking at.
    const js = fs.readFileSync(path.join(REPO_ROOT, "gui", "app.js"), "utf8");
    const direct = [...js.matchAll(/fetchPeopleWithCounts\(\s*true\s*\)/g)];
    assert.equal(
      direct.length,
      0,
      `${direct.length} call(s) to fetchPeopleWithCounts(true) bypass fetchPhotos(), ` +
        `which is what keeps the sidebar and the Tune target dropdown in agreement.`
    );
  });

  test("the dispatcher still dispatches", () => {
    const js = fs.readFileSync(path.join(REPO_ROOT, "gui", "app.js"), "utf8");
    const body = js.slice(js.indexOf("function fetchPhotos()"));
    assert.match(
      body.slice(0, 900),
      /mode === 'face-matching' \|\| mode === 'unmatched-faces'/,
      "fetchPhotos no longer branches on the mode"
    );
  });
});
