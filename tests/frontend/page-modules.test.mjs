/**
 * How the harness loads a page's modules (harness.mjs, pageModules).
 *
 * jsdom cannot load ES modules, so the harness puts a page's modules in the order the
 * browser runs them and evaluates them as one script, import lines dropped and
 * `export` words taken off (docs/ARCHITECTURE.md, phase 6). What that cannot honour --
 * a name declared twice, a renamed import, an import of something not exported -- it
 * refuses, rather than load a page that a browser would load differently.
 */
import { test, describe, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { REPO_ROOT, pageModules, pageScript, entryModule } from "./harness.mjs";

const made = [];
after(() => made.forEach((folder) => fs.rmSync(folder, { recursive: true, force: true })));

/** A page folder and a common folder holding `files` ({"page/x.js": text, "common/y.js": text}). */
function site(files, html = '<html><body><script type="module" src="main.js"></script></body></html>') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tagpup_modules_"));
  made.push(root);
  const page = path.join(root, "page");
  const common = path.join(root, "common");
  fs.mkdirSync(page);
  fs.mkdirSync(common);
  fs.writeFileSync(path.join(page, "index.html"), html);
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(root, name), text);
  return { page, common };
}

const names = (modules) => modules.map((m) => path.basename(path.dirname(m.file)) + "/" + path.basename(m.file));

describe("TagTuner's elements.js holds what more than one feature reaches (#160)", () => {
  // Its header says so. tagViewContent stayed there, used by sidebar.js alone, with the
  // old page's comment on why it was declared early -- a hazard of one closure's `const`
  // that modules do not have.
  const tuner = path.join(REPO_ROOT, "web", "tuner");
  const elements = path.join(tuner, "elements.js");

  test("every element it exports is imported by two modules or more", () => {
    const source = fs.readFileSync(elements, "utf8");
    const exported = [...source.matchAll(/^export const (\w+)/gm)].map((m) => m[1]);
    assert.ok(exported.length > 5, "found too few exports to be reading the right file");
    const importers = new Map(exported.map((name) => [name, []]));
    for (const module of pageModules(tuner)) {
      if (path.resolve(module.file) === path.resolve(elements)) continue;
      const text = fs.readFileSync(module.file, "utf8");
      const block = text.match(/import\s*\{([^}]*)\}\s*from\s*['"]\.\/elements\.js['"]/);
      if (!block) continue;
      for (const name of block[1].split(",").map((s) => s.trim()).filter(Boolean)) {
        if (importers.has(name)) importers.get(name).push(path.basename(module.file));
      }
    }
    const single = [...importers].filter(([, users]) => users.length < 2).map(([n, u]) => `${n}: ${u.join(", ") || "none"}`);
    assert.deepEqual(single, []);
  });

  test("it keeps no comment about a closure's declaration order", () => {
    assert.doesNotMatch(fs.readFileSync(elements, "utf8"), /before its line had run/);
  });
});

describe("the order a page's modules run in", () => {
  test("each module's imports first, in the order it names them, each once", () => {
    const { page, common } = site({
      "page/main.js": 'import { b } from "./b.js";\nimport { api } from "./common/api.js";\nexport const main = b + api;\n',
      "page/b.js": 'import { api } from "./common/api.js";\nexport const b = api;\n',
      "common/api.js": 'import { key } from "./paths.js";\nexport const api = key;\n',
      "common/paths.js": "export const key = 1;\n",
    });
    assert.deepEqual(names(pageModules(page, common)), ["common/paths.js", "common/api.js", "page/b.js", "page/main.js"]);
  });

  test("the script has no import lines and no export words, and is strict", () => {
    const { page, common } = site({
      "page/main.js": 'import {\n  helper,\n  other\n} from "./common/tools.js";\ndocument.title = helper() + other;\n',
      "common/tools.js": "export function helper() { return 'x'; }\nexport const other = 'y';\n",
    });
    const script = pageScript(page, common);
    assert.ok(script.startsWith('"use strict";'));
    assert.doesNotMatch(script, /^\s*import\b/m);
    assert.doesNotMatch(script, /^\s*export\b/m);
    assert.match(script, /^function helper\(\)/m);
    assert.match(script, /^const other = 'y';/m);
    assert.ok(script.indexOf("function helper") < script.indexOf("document.title"));
    // Each module in a function of its own, handed its imports.
    assert.match(script, /const \{ helper, other \} = __module0;/);
  });

  test("the entry is the module index.html starts", () => {
    assert.equal(entryModule('<script type="module" src="main.js"></script>'), "main.js");
    assert.throws(() => entryModule('<script src="app.js"></script>'), /one <script type="module"/);
    assert.throws(() => entryModule('<script type="module" src="a.js"></script><script src="b.js"></script>'));
  });
});

describe("each module has a scope of its own, as in a browser", () => {
  test("two modules may declare the same name, and each sees its own", () => {
    const { page, common } = site({
      "page/main.js": 'import { pathKey } from "./common/paths.js";\nfunction samePath() { return "page"; }\ndocument.title = pathKey() + samePath();\n',
      "common/paths.js": 'export function pathKey() { return samePath(); }\nexport function samePath() { return "common"; }\n',
    });
    const title = { value: "" };
    new Function("document", pageScript(page, common))({ set title(v) { title.value = v; } });
    assert.equal(title.value, "commonpage");
  });

  test("a module's own parameter or local is not a use of another module's name", () => {
    const { page, common } = site({
      "page/main.js": 'import { has } from "./common/vocabulary.js";\nexport function isPersonTag(t) { return has(t, (x) => x); }\n',
      "common/vocabulary.js": "export function has(tag, isPersonTag) { const leaf = isPersonTag(tag); return leaf; }\n",
    });
    assert.equal(pageModules(page, common).length, 2);
  });
});

describe("what the page loader cannot take is refused", () => {

  test("an import of something the module does not export", () => {
    const { page, common } = site({
      "page/main.js": 'import { leafOf } from "./common/vocabulary.js";\n',
      "common/vocabulary.js": "function leafOf() {}\n",
    });
    assert.throws(() => pageModules(page, common), /does not export it/);
  });

  test("a renamed, default or namespace import", () => {
    for (const line of ['import { a as b } from "./common/x.js";', 'import x from "./common/x.js";',
                        'import * as x from "./common/x.js";']) {
      const { page, common } = site({ "page/main.js": line + "\n", "common/x.js": "export const a = 1;\n" });
      assert.throws(() => pageModules(page, common), /takes only named imports|takes no renamed import/, line);
    }
  });

  test("an export that is not a declaration", () => {
    const { page, common } = site({
      "page/main.js": 'import { a } from "./common/x.js";\n',
      "common/x.js": "const a = 1;\nexport { a };\n",
    });
    assert.throws(() => pageModules(page, common), /only exported declarations/);
  });

  test("an import that is not relative, or reaches outside what the server serves", () => {
    for (const line of ['import { a } from "jsdom";', 'import { a } from "./common/deeper/x.js";']) {
      const { page, common } = site({ "page/main.js": line + "\n" });
      assert.throws(() => pageModules(page, common), /only relative imports|not a module the server serves/, line);
    }
  });

  test("a name used from another module without importing it", () => {
    // A ReferenceError in a browser, and in the tests only on a path a test runs.
    const { page, common } = site({
      "page/main.js": 'import { api } from "./common/api.js";\nconst address = api.url(libraryIn("/x/"));\n',
      "common/api.js": "export function libraryIn(p) { return p; }\nexport const api = { url: (p) => p };\n",
    });
    assert.throws(() => pageModules(page, common), /uses libraryIn from .*api\.js without importing it/);
    // A name the other module does not export is a ReferenceError in a browser too.
    const hidden = site({
      "page/main.js": 'import { api } from "./common/api.js";\nconst address = trim("/x/");\n',
      "common/api.js": "function trim(p) { return p; }\nexport const api = {};\n",
    });
    assert.throws(() => pageModules(hidden.page, hidden.common), /uses trim from .*api\.js without importing it/);
    // Named in a comment or a string, it is not used.
    const quiet = site({
      "page/main.js": 'import { api } from "./common/api.js";\n// libraryIn is api.js\'s\nconst label = "libraryIn";\n',
      "common/api.js": "export function libraryIn(p) { return p; }\nexport const api = {};\n",
    });
    assert.equal(pageModules(quiet.page, quiet.common).length, 2);
  });

  test("two modules importing each other", () => {
    const { page, common } = site({
      "page/main.js": 'import { a } from "./a.js";\n',
      "page/a.js": 'import { b } from "./b.js";\nexport const a = 1;\n',
      "page/b.js": 'import { a } from "./a.js";\nexport const b = 1;\n',
    });
    assert.throws(() => pageModules(page, common), /import each other/);
  });
});
