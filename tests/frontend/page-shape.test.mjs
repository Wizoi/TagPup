/**
 * The shape phase 6 left the pages in, held (docs/ARCHITECTURE.md, phase 6).
 *
 * Each page was one app.js of about 5,000 lines, and thirteen helpers were written in
 * both, drifting apart one fix at a time. Now each page is modules of a size a reader
 * can hold, and a helper both pages need lives once, in web/common/.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, pageModules } from "./harness.mjs";

const LIMIT = 1000;
const PAGES = { tagpup: path.join(REPO_ROOT, "web", "tagpup"), tuner: path.join(REPO_ROOT, "web", "tuner") };

/** A function's text without its name and spelling of export, async or spacing: what is left is what it does. */
export function bodyOf(text, name) {
  return text.replace(/^export\s+/, "").replace(new RegExp("\\b" + name.replace(/\$/g, "\\$") + "\\b", "g"), "NAME")
    .replace(/\s+/g, " ").trim();
}

/** Copies between two pages' functions, by what they do and not what they are called (#289): [[nameA, nameB]]. */
export function copiesBetween(tagpup, tuner) {
  const byBody = new Map();
  for (const [name, f] of tuner) byBody.set(bodyOf(f.text, name), name);
  return [...tagpup].filter(([name, f]) => byBody.has(bodyOf(f.text, name)))
    .map(([name]) => [name, byBody.get(bodyOf(tagpup.get(name).text, name))]);
}

/** Pairs that read alike by what they are, not by copying: [TagPup's name, TagTuner's name] and why. */
const KNOWN_PAIRS = [
  ["startNamingFacesInFolder", "startNamingFaces", "each page's one call of the shared dialog (web/common/name-faces.js)"],
];

/** Each top-level function of a page's own modules, by name, as written. */
function functionsOf(pageDir) {
  const found = new Map();
  for (const module of pageModules(pageDir)) {
    if (path.dirname(module.file) !== pageDir) continue; // web/common/ is shared by design
    const lines = fs.readFileSync(module.file, "utf8").split("\n");
    for (let i = 0; i < lines.length; i++) {
      const start = lines[i].match(/^(?:export\s+)?(?:async\s+)?function\s*\*?\s*([\w$]+)/);
      if (!start) continue;
      let end = i;
      while (end < lines.length && !/^}/.test(lines[end])) end++;
      const text = lines.slice(i, end + 1).join("\n").replace(/^export\s+/, "").replace(/\s+/g, " ").trim();
      found.set(start[1], { text, where: path.relative(REPO_ROOT, module.file) });
    }
  }
  return found;
}

describe("the pages' shape", () => {
  test("no page module is over about a thousand lines", () => {
    const over = [];
    for (const dir of [...Object.values(PAGES), path.join(REPO_ROOT, "web", "common"), path.join(REPO_ROOT, "web", "activity")]) {
      for (const name of fs.readdirSync(dir).filter((n) => n.endsWith(".js"))) {
        const lines = fs.readFileSync(path.join(dir, name), "utf8").split("\n").length;
        if (lines > LIMIT) over.push(`${path.relative(REPO_ROOT, path.join(dir, name))}: ${lines}`);
      }
    }
    assert.deepEqual(over, [], "split these by feature");
  });

  test("no function is written in both pages; a shared one belongs in web/common/", () => {
    const tagpup = functionsOf(PAGES.tagpup);
    const tuner = functionsOf(PAGES.tuner);
    const known = (a, b) => KNOWN_PAIRS.some(([x, y]) => x === a && y === b);
    const twice = copiesBetween(tagpup, tuner).filter(([a, b]) => !known(a, b)).map(([a, b]) => `${a}: ${tagpup.get(a).where} and ${b}: ${tuner.get(b).where}`);
    assert.deepEqual(twice, []);
  });

  test("the check finds a copy, reformatted or under another name", () => {
    // Worthless if it matches nothing. #146: TagPup's baseName and TagTuner's basename were one function twice.
    const make = (name, text) => new Map([[name, { text, where: name }]]);
    const a = make("baseName", "function baseName(t) {\n  return t.split('/').pop();\n}");
    const b = make("basename", "export function basename(t) {\n    return t.split('/').pop();\n}");
    assert.deepEqual(copiesBetween(a, b), [["baseName", "basename"]]);
    const other = make("basename", "function basename(t) {\n  return t.split('\\\\').pop();\n}");
    assert.deepEqual(copiesBetween(a, other), [], "a different function is not a copy");
  });

  test("a function that calls itself is still the same function under another name", () => {
    const make = (name) => new Map([[name, { text: `function ${name}(n) { return n ? ${name}(n - 1) : 0; }`, where: name }]]);
    assert.deepEqual(copiesBetween(make("countDown"), make("down")), [["countDown", "down"]]);
  });
});
