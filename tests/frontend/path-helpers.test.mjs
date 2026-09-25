/**
 * One place decides when two paths are the same path.
 *
 * The server now sends every photo and folder path in one spelling: the native
 * absolute path, exactly as the database holds it. Server paths are therefore
 * compared with `===` and never rewritten in the browser. The only paths that need
 * normalising are the ones that did not come from the server -- what somebody typed
 * into the folder box, the ?photo= in a URL, what the native Browse dialog returned
 * (forward slashes) -- and those go through `pathKey` / `samePath`.
 *
 * Before this, each page had its own idea of "the same path": one lowercased and
 * turned backslashes into forward slashes, one also dropped a trailing separator,
 * the folder cache did neither, and the job-folder checks used `!==`. Each was right
 * for the case in front of it and wrong for some other one. These tests hold the
 * rule in the same shape as the tag vocabulary: the conversion lives in one helper,
 * web/common/paths.js's pathKey, which both pages import, and a separator conversion
 * anywhere else in either page's modules fails here.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, APPS, loadApp, FakeServer, photoRecord, openFolder, pageModules } from "./harness.mjs";
import { baseName, pathKey, samePath } from "../../web/common/paths.js";

const PATHS_JS = path.join(REPO_ROOT, "web", "common", "paths.js");

/** The helpers allowed to convert separators, by the name they are declared with. */
const HELPERS = ["pathKey"];

/**
 * A line carrying this marker converts a *tag's* separators, not a path's. Keywords
 * are hierarchies written with "/" and older ones arrived with "\\"; the regex below
 * cannot tell `tag.replace(/\\/g, '/')` from `path.replace(/\\/g, '/')`, so the tag
 * sites say what they are. Put it on the same line, so it cannot drift away.
 */
const TAG_MARKER = "tag-hierarchy:";

/** Anything that rewrites one separator into another. */
const CONVERSIONS = [
  /\.replace(All)?\(\s*\/\\\\\/g?\s*,/, //  .replace(/\\/g, ...)
  /\.replace(All)?\(\s*\/\\\/\/g?\s*,/, //  .replace(/\//g, ...)
  /\.replace(All)?\(\s*\/\[[^\]]*(\\\\|\\\/|\/)[^\]]*\]\+?\/g\s*,/, //  .replace(/[\\/]+/g, ...)
  /\.replace(All)?\(\s*(['"])(\\\\|\/)\2\s*,/, //  .replace('\\', ...) / .replace('/', ...)
  /\.split\(\s*(['"])\\\\\1\s*\)/, //  .split('\\')
];

function functionSource(source, name) {
  const lines = source.split(/\r?\n/);
  const start = lines.findIndex((l) => new RegExp(`^\\s*(?:export\\s+)?function\\s+${name}\\s*\\(`).test(l));
  assert.ok(start >= 0, `function ${name} is gone`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((l, i) => i > start && l === `${indent}}`);
  assert.ok(end > start, `could not find the end of ${name}`);
  return lines.slice(start, end + 1).join("\n");
}

function inHelper(lines, lineIndex) {
  for (let i = lineIndex; i >= 0 && i > lineIndex - 12; i--) {
    const declared = lines[i].match(/^\s*(?:export\s+)?function\s+(\w+)\s*\(/);
    if (declared) return HELPERS.includes(declared[1]);
  }
  return false;
}

/** Separator conversions outside pathKey in the page's modules, the shared ones included. */
function offendersIn(app) {
  const offenders = [];
  for (const module of pageModules(path.join(REPO_ROOT, APPS[app].dir))) {
    const lines = fs.readFileSync(module.file, "utf8").split(/\r?\n/);
    lines.forEach((line, i) => {
      const trimmed = line.trimStart();
      if (trimmed.startsWith("//") || trimmed.startsWith("*")) return;
      if (!CONVERSIONS.some((re) => re.test(line))) return;
      if (line.includes(TAG_MARKER)) return;
      if (inHelper(lines, i)) return;
      offenders.push(`${path.relative(REPO_ROOT, module.file)}:${i + 1}: ${line.trim()}`);
    });
  }
  return offenders;
}

describe("web/common/paths.js: pathKey and samePath", () => {
    test("slash direction, case and a trailing separator do not make a new path", () => {
      assert.ok(samePath("D:/Pictures/Run/", "d:\\pictures\\run"));
      assert.equal(pathKey("D:/Pictures/Run/"), "d:\\pictures\\run");
    });

    test("what the Browse dialog returns matches what the server sends", () => {
      assert.ok(samePath("D:/Training/Meet 3", "D:\\Training\\Meet 3"));
    });

    test("a folder is not the same as one whose name starts with it", () => {
      assert.ok(!samePath("D:\\Run 2", "D:\\Run"));
      assert.ok(!samePath("D:\\Run\\a.jpg", "D:\\Run"));
    });

    test("nothing is the same as nothing", () => {
      // An unset folder must never match: the job-folder check relies on it.
      assert.ok(!samePath("", ""));
      assert.ok(!samePath(null, null));
      assert.ok(!samePath("D:\\Run", null));
      assert.equal(pathKey(null), "");
    });

    test("surrounding whitespace from a typed path is ignored", () => {
      assert.ok(samePath("  D:\\Run  ", "d:/run"));
    });
});

for (const app of Object.keys(APPS)) {
  describe(`${app}: separators are converted in one place`, () => {
    test("nothing outside pathKey converts a path's separators", () => {
      const offenders = offendersIn(app);
      assert.deepEqual(
        offenders,
        [],
        "these rewrite path separators by hand instead of going through pathKey / " +
          "samePath (server paths need no rewriting; tag code marks itself with " +
          `"${TAG_MARKER}"):\n` + offenders.join("\n")
      );
    });

    test("the page's modules include the one that converts", () => {
      // The check above is worthless if the page does not load paths.js.
      const files = pageModules(path.join(REPO_ROOT, APPS[app].dir)).map((m) => m.file);
      assert.ok(files.includes(PATHS_JS), "the page does not import web/common/paths.js");
    });
  });
}

describe("web/common/paths.js: baseName, the one last segment (#146)", () => {
  // TagPup's baseName and TagTuner's basename differed at the edges: for null one
  // gave '' and the other "null"; for "/" one gave '' and the other "/". A path with
  // no last segment has none: '' for all of them, and the caller says what to show.
  test("a photo's file name, either separator", () => {
    assert.equal(baseName("D:\\Run\\a.jpg"), "a.jpg");
    assert.equal(baseName("D:/Run/a.jpg"), "a.jpg");
    assert.equal(baseName("a.jpg"), "a.jpg");
  });

  test("a folder's name, with or without a trailing separator", () => {
    assert.equal(baseName("D:\\Run\\2019\\"), "2019");
    assert.equal(baseName("D:/Run/2019/"), "2019");
  });

  test("a drive root is its drive", () => {
    assert.equal(baseName("D:\\"), "D:");
  });

  test("nothing, and a bare separator, have no last segment", () => {
    for (const p of [null, undefined, "", "/", "\\", "//"]) assert.equal(baseName(p), "", String(p));
  });
});

describe("one baseName for both pages", () => {
  for (const app of Object.keys(APPS)) {
    test(`${app} declares no basename of its own`, () => {
      const own = [];
      for (const module of pageModules(path.join(REPO_ROOT, APPS[app].dir))) {
        if (path.resolve(module.file) === path.resolve(PATHS_JS)) continue;
        const source = fs.readFileSync(module.file, "utf8");
        if (/function\s+base_?name\s*\(/i.test(source)) own.push(path.relative(REPO_ROOT, module.file));
      }
      assert.deepEqual(own, []);
    });
  }
});

describe("the pages agree on what the same path is", () => {
  test("the guard is not checking a rule nobody follows", () => {
    // pathKey itself converts, so the patterns must see it -- if they did not, the
    // check above would pass on anything.
    const source = functionSource(fs.readFileSync(PATHS_JS, "utf8"), "pathKey");
    assert.ok(
      source.split("\n").some((l) => CONVERSIONS.some((re) => re.test(l))),
      "the conversion patterns no longer match pathKey's own conversion"
    );
  });
});

describe("the conversion patterns catch what they are meant to", () => {
  const caught = (line) => CONVERSIONS.some((re) => re.test(line));
  test("the shapes that used to be scattered through the pages", () => {
    assert.ok(caught("return p1.replace(/\\\\/g, '/').toLowerCase()"));
    assert.ok(caught("x.replace(/\\//g, '\\\\')"));
    assert.ok(caught("x.replace(/[\\\\/]+/g, '/')"));
    assert.ok(caught("x.replaceAll('\\\\', '/')"));
    assert.ok(caught("x.split('\\\\').join('/')"));
  });

  test("but not stripping a trailing separator or taking the last segment", () => {
    assert.ok(!caught("String(p).replace(/[\\\\/]+$/, '').split(/[\\\\/]/).pop()"));
  });
});

// --------------------------------------------------------------- in the page --

const FOLDER = "D:/Training/Pictures/Meet 3";

function server() {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/autocomplete-folder", [])
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg" })]);
}

const load = (t) =>
  loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server: server() });
const scans = (ctx) => ctx.server.urls().filter((u) => u.includes("/api/folder/scan")).length;

describe("TagPup: a folder typed two ways is one folder", () => {
  test("re-typing the open folder in another spelling does not rescan it", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    await openFolder(ctx, "d:\\training\\pictures\\meet 3\\");
    assert.equal(scans(ctx), 1, "a different spelling of the open folder was scanned again");
  });

  test("the folder cache is shared between spellings", async (t) => {
    const ctx = await load(t);
    await openFolder(ctx, FOLDER);
    await openFolder(ctx, "D:/Training/Pictures/Meet 4");
    assert.equal(scans(ctx), 2);

    // Back to the first folder, spelled the way the server would spell it.
    await openFolder(ctx, "D:\\Training\\Pictures\\Meet 3");
    assert.equal(scans(ctx), 2, "the cached scan was missed because of the spelling");
  });

  test("a cache entry stored under the typed path is still read once", async (t) => {
    // Entries written before pathKey were keyed by the path as typed. Dropping them
    // would cost one rescan per folder; reading them costs one fallback lookup.
    const ctx = await load(t);
    ctx.window.localStorage.setItem(
      `tagpup_cache_${FOLDER}`,
      JSON.stringify({ timestamp: Date.now(), photos: [photoRecord({ filename: "a.jpg" })] })
    );
    await openFolder(ctx, FOLDER);
    assert.equal(scans(ctx), 0, "the existing cache entry was ignored");
  });
});
