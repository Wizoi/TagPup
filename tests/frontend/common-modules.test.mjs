/**
 * What both pages share is written once, in web/common/, and both import it.
 *
 * Thirteen helpers were written in both pages -- pathKey, samePath, leafOf, samePerson,
 * the tag and name checks, the picker and the library the browser remembers -- and the
 * tests holding them to one rule cut them out of each page's text. Now each is a
 * module's, imported by both. The harness refuses a name declared by two of a page's
 * modules at their top; what it cannot see is a copy inside a page's closure, which
 * would quietly shadow the import. That is what this looks for.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, APPS, COMMON_DIR, pageModules } from "./harness.mjs";
import { leafOf, rootOf, samePerson, photoAlreadyHas } from "../../web/common/vocabulary.js";

const COMMON = fs.readdirSync(COMMON_DIR).filter((name) => name.endsWith(".js"));

/** Every name a shared module exports, with the module. */
function exportedNames() {
  const names = [];
  for (const file of COMMON) {
    const source = fs.readFileSync(path.join(COMMON_DIR, file), "utf8");
    for (const m of source.matchAll(/^export\s+(?:(?:async\s+)?function\s*\*?\s*|class\s+|(?:const|let|var)\s+)([\w$]+)/gm)) {
      names.push({ name: m[1], file });
    }
  }
  return names;
}

describe("each shared helper is written once", () => {
  test("the shared modules export what the pages share", () => {
    const names = exportedNames().map((e) => e.name);
    for (const name of ["api", "pathKey", "samePath", "leafOf", "rootOf", "samePerson", "photoAlreadyHas",
                        "tagProblem", "nameProblem", "textProblem", "rememberedLibrary", "rememberLibrary",
                        "goToLibrary", "initDatabaseSelector"]) {
      assert.ok(names.includes(name), `${name} is not exported by web/common/`);
    }
  });

  for (const app of Object.keys(APPS)) {
    test(`${app}: no module of the page declares its own copy`, () => {
      const offenders = [];
      for (const module of pageModules(path.join(REPO_ROOT, APPS[app].dir))) {
        if (module.file.startsWith(COMMON_DIR)) continue;
        const lines = fs.readFileSync(module.file, "utf8").split(/\r?\n/);
        for (const { name, file } of exportedNames()) {
          const declared = new RegExp(`^\\s*(?:(?:async\\s+)?function\\s*\\*?\\s*|class\\s+|(?:const|let|var)\\s+)${name.replace(/\$/g, "\\$")}\\b`);
          lines.forEach((line, i) => {
            if (declared.test(line)) offenders.push(`${path.basename(module.file)}:${i + 1}: ${name} (web/common/${file})`);
          });
        }
      }
      assert.deepEqual(offenders, [], "these are web/common/'s; import them instead");
    });

    test(`${app}: the page loads the shared modules it needs`, () => {
      const loaded = pageModules(path.join(REPO_ROOT, APPS[app].dir)).map((m) => path.basename(m.file));
      for (const file of ["api.js", "library.js", "paths.js", "vocabulary.js"]) {
        assert.ok(loaded.includes(file), `${app} does not load web/common/${file}`);
      }
    });
  }
});

describe("web/common/vocabulary.js: a person's two shapes", () => {
  test("the leaf is who they are; the root is where they are filed", () => {
    assert.equal(leafOf("People/Rowan Thackeray"), "Rowan Thackeray");
    assert.equal(leafOf(" Rowan Thackeray "), "Rowan Thackeray");
    assert.equal(leafOf(null), "");
    assert.equal(rootOf("People/Family/Rowan Thackeray"), "People");
    assert.ok(samePerson("People/Rowan Thackeray", "rowan thackeray"));
    assert.ok(!samePerson("", ""));
  });

  test("a photo already has a person under another spelling, by the page's rule", () => {
    // Which tags are people is the page's to say (the tag tree's face roots).
    const isPersonTag = (tag) => tag.startsWith("People/") || tag === "Rowan Thackeray";
    const photo = { tags: ["People/Rowan Thackeray", "Activity/Rowing"] };
    assert.ok(photoAlreadyHas(photo, "Rowan Thackeray", isPersonTag));
    assert.ok(photoAlreadyHas(photo, "Activity/Rowing", isPersonTag));
    assert.ok(!photoAlreadyHas(photo, "Rowing", isPersonTag), "a keyword's leaf is not the keyword");
    assert.ok(!photoAlreadyHas(null, "People/Rowan Thackeray", isPersonTag));
  });
});
