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
    for (const dir of [...Object.values(PAGES), path.join(REPO_ROOT, "web", "common")]) {
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
    const twice = [...tagpup].filter(([name, f]) => tuner.has(name) && tuner.get(name).text === f.text)
      .map(([name, f]) => `${name}: ${f.where} and ${tuner.get(name).where}`);
    assert.deepEqual(twice, []);
  });

  test("the check finds a copy", () => {
    // Worthless if it matches nothing: the same function, reformatted, is a copy.
    const a = "function leaf(t) {\n  return t.split('/').pop();\n}";
    const b = "export function leaf(t) {\n    return t.split('/').pop();\n}";
    const norm = (s) => s.replace(/^export\s+/, "").replace(/\s+/g, " ").trim();
    assert.equal(norm(a), norm(b));
  });
});
