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
import { REPO_ROOT, COMMON_DIR, pageModules } from "./harness.mjs";

const LIMIT = 1000;
const PAGES = { tagpup: path.join(REPO_ROOT, "web", "tagpup"), tuner: path.join(REPO_ROOT, "web", "tuner") };
/** The stylesheets that import the one theme (web/common/theme.css), each [its page's folder, its name]. */
const THEMED = [["tagpup", "style.css"], ["tagpup", "mobile.css"], ["tuner", "style.css"]];

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

/** Each top-level function of a page's own modules, by name, as written; `html` the page's file, `leftOut` modules not counted. */
function functionsOf(pageDir, html = "index.html", leftOut = new Set()) {
  const found = new Map();
  for (const module of pageModules(pageDir, COMMON_DIR, html)) {
    if (path.dirname(module.file) !== pageDir) continue; // web/common/ is shared by design
    if (leftOut.has(module.file)) continue;
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

  test("the phone's page writes none of the desktop pages' functions again; a shared one belongs in web/common/", () => {
    // Its own modules only: one it imports from the desktop page (vgrid.js, from cut 2) is the desktop's, not a copy.
    const desktop = new Set(pageModules(PAGES.tagpup).map((m) => m.file));
    const phone = functionsOf(PAGES.tagpup, "mobile.html", desktop);
    assert.ok(phone.size > 0, "no function of the phone's page was read; this checks nothing");
    for (const [page, functions] of [["tagpup", functionsOf(PAGES.tagpup)], ["tuner", functionsOf(PAGES.tuner)]]) {
      const twice = copiesBetween(phone, functions).map(([a, b]) => `${a}: ${phone.get(a).where} and ${b}: ${functions.get(b).where}`);
      assert.deepEqual(twice, [], `copied from ${page}'s page`);
    }
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

  test("the theme's colours and fonts are defined once, in web/common/theme.css, and each page's stylesheet imports it first", () => {
    const css = (...parts) => fs.readFileSync(path.join(REPO_ROOT, "web", ...parts), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const roots = (text) => (text.match(/:root\s*\{/g) || []).length;
    assert.equal(roots(css("common", "theme.css")), 1, "web/common/theme.css defines no tokens");
    for (const [folder, sheet] of THEMED) {
      const text = css(folder, sheet);
      assert.equal(roots(text), 0, `web/${folder}/${sheet} defines tokens of its own`);
      assert.match(text.trimStart(), /^@import url\("common\/theme\.css"\);/, `web/${folder}/${sheet} does not import the theme first`);
    }
  });

  test("every stylesheet closes each block it opens, so a rule after an @media block is not inside it", () => {
    // Cut 2 of the phone redesign appended its covers after cut 1's prefers-reduced-motion block with that block's brace
    // lost: the browser read every rule after it as reduced-motion only, and Browse's People strip fell apart on a phone.
    const open = [];
    for (const dir of ["tagpup", "tuner", "common", "activity"]) {
      for (const name of fs.readdirSync(path.join(REPO_ROOT, "web", dir)).filter((n) => n.endsWith(".css"))) {
        const text = fs.readFileSync(path.join(REPO_ROOT, "web", dir, name), "utf8")
          .replace(/\/\*[\s\S]*?\*\//g, "").replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g, '""');
        let depth = 0;
        for (const ch of text) {
          depth += ch === "{" ? 1 : ch === "}" ? -1 : 0;
          if (depth < 0) break;
        }
        if (depth !== 0) open.push(`web/${dir}/${name}: ${depth > 0 ? `${depth} block(s) left open` : "a brace closes nothing"}`);
      }
    }
    assert.deepEqual(open, []);
  });
});
