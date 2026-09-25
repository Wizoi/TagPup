/**
 * How TagPup's page is split into modules (docs/ARCHITECTURE.md, phase 6).
 *
 * The page was one closure: its state was the closure's `let`s, and every function
 * could call every other. As modules, a module cannot assign to a binding it imports,
 * so the state is one object (state.js); and a module cannot import one that imports
 * it, so a call back up the page goes through `upper` (hooks.js), which main.js fills
 * in. A name missing from `upper` fails only when that path runs -- a click, a save
 * -- so these hold it at load.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, APPS, loadApp, FakeServer, pageModules, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const PAGE_DIR = path.join(REPO_ROOT, APPS.tagpup.dir);

/** The page's own modules (not web/common/), each with its name and source. */
function ownModules() {
  return pageModules(PAGE_DIR)
    .filter((m) => path.dirname(m.file) === PAGE_DIR)
    .map((m) => ({ name: path.basename(m.file), source: fs.readFileSync(m.file, "utf8") }));
}

/** A module's code without its comments and quoted strings. */
function codeOf(source) {
  return source
    .replace(/'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g, "''")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:\\])\/\/.*$/gm, "$1");
}

/** Every `upper.name` the page's modules call. */
function upperCalls() {
  const names = new Set();
  for (const { source } of ownModules()) {
    for (const m of codeOf(source).matchAll(/(?<![\w$.])upper\.([\w$]+)/g)) names.add(m[1]);
  }
  return names;
}

describe("TagPup's page as modules", () => {
  test("every call up the page is filled in before the page starts", () => {
    const called = [...upperCalls()].sort();
    assert.ok(called.length > 0, "no module calls through upper; this guard checks nothing");

    const hooks = fs.readFileSync(path.join(PAGE_DIR, "hooks.js"), "utf8");
    const listed = [...codeOf(hooks).matchAll(/^\s+([\w$]+):\s*null,?$/gm)].map((m) => m[1]).sort();
    assert.deepEqual(listed, called, "hooks.js should list exactly the names the modules call through upper");

    // Filled at main.js's top level, which runs before DOMContentLoaded and so before
    // anything the page does.
    const main = codeOf(fs.readFileSync(path.join(PAGE_DIR, "main.js"), "utf8"));
    const fill = main.match(/^Object\.assign\(upper, \{([^}]*)\}\);$/m);
    assert.ok(fill, "main.js no longer fills in upper at its top level");
    const filled = fill[1].split(",").map((s) => s.trim()).filter(Boolean).sort();
    assert.deepEqual(filled, called, "main.js should fill in every name the modules call through upper");
  });

  test("the page loads with every call up the page in place", async (t) => {
    const server = new FakeServer()
      .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
      .on("/api/tags", [])
      .on("/api/people", [])
      .on("/api/taxonomy/tree", []);
    const { consoleErrors } = await loadApp("tagpup", { t, server });
    const uncaught = consoleErrors.map(String).filter((e) => /Uncaught|ReferenceError|TypeError: upper/.test(e));
    assert.deepEqual(uncaught, [], "the page threw while starting");
  });

  test("the page's state is one object: no module keeps a `let` of its own", () => {
    const offenders = [];
    for (const { name, source } of ownModules()) {
      codeOf(source).split(/\r?\n/).forEach((line, i) => {
        if (/^(?:export\s+)?(?:let|var)\s/.test(line)) offenders.push(`${name}:${i + 1}: ${line.trim()}`);
      });
    }
    assert.deepEqual(offenders, [], "a module's `let` cannot be assigned by another module; put it in state.js");
  });

  test("main.js holds the start-up and the wiring, not a feature", () => {
    const main = ownModules().find((m) => m.name === "main.js");
    const declared = [...codeOf(main.source).matchAll(/^\s*(?:async\s+)?function\s+([\w$]+)/gm)].map((m) => m[1]);
    assert.deepEqual(declared, [], "a function in main.js belongs in the module of its feature");
  });
});
