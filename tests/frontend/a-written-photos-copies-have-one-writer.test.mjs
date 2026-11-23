/**
 * A photo written is held by the page in many copies: the open folder's records (`state.folderPhotos`), the scan kept of
 * its own folder and of every folder above it (a scan is recursive), and the scans another tab of this library kept. Each
 * `wrong` of the page's write coherence (about 23 rows, #3054..#3339) was a writer that changed one copy and not another;
 * the one place that changes every copy after a write is web/tagpup/cache.js (photosWritten, recordsWritten, photosGone,
 * photoDeleted; #3324, docs/plans/photos-written.md). This guard fails when a page module changes a copy itself (#3343):
 *
 * - a kept scan: only cache.js writes or forgets an entry of local storage under a folder's key -- every other module's
 *   setItem / removeItem names a constant key (an UPPER_CASE name or a quoted string), never `tagpup_cache_`, and reads a
 *   kept scan only through `localStorage.getItem(folderCacheKey(...))`;
 * - the open folder kept whole: saveToLocalStorageCache is called outside cache.js only where the open list or its
 *   suggestions came whole from the server (a scan read, Suggest's progress), never after a write of photos;
 * - the open list: no module but cache.js patches `state.folderPhotos` in place (splice, push, sort, an index or its
 *   length set, or set again from itself); taking a list the server gave whole, or emptying it, is not a patch;
 * - a record's stamp or Date Taken (`mtime`, `base`, `taken`, fields only a write's reply gives; `order` too, but a sort
 *   order and a request's `order` share its name): a function that sets one hands the record on to cache.js
 *   (recordsWritten, photosWritten) or forgets what holds it (photosGone, photoDeleted) in the same function, so every
 *   other copy takes the same.
 *
 * The phone's page (mobile-*.js) keeps no scan and no folder list: its records are its screens' own (mobile-state.js).
 * A library view's cards are library-source.js's, drawn again from the records after an edit (DRAWN_FROM_THE_RECORDS).
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The repository's web/, found from this file (the source text alone is read: no page is loaded).
const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "web");
const CACHE_JS = path.join("tagpup", "cache.js");

/** Where the open folder's scan is kept again whole, outside cache.js: {module: calls}. A new one is a write's: it goes. */
const SAVES_THE_OPEN_SCAN = {
  "tagpup/folder.js": 1,        // a scan read (folder.js takeScan)
  "tagpup/suggestions.js": 3,   // Suggest's progress and end, and a looking Suggest let go: the suggestions, not photos
};

/** The functions that copy the records into a view's own cards, which no write reaches directly: {module: [function]}. */
const DRAWN_FROM_THE_RECORDS = { "tagpup/library-source.js": ["applyEditedRecords"] };

/** Every page module's source but cache.js's, {module ("tagpup/photo.js"): text}; or the modules under `root`. */
function pageSources(root = WEB) {
  const found = {};
  for (const entry of fs.readdirSync(root, { recursive: true })) {
    const name = String(entry).split(path.sep).join("/");
    if (!name.endsWith(".js") || name.includes("node_modules") || name === CACHE_JS.split(path.sep).join("/")) continue;
    found[name] = fs.readFileSync(path.join(root, entry), "utf8");
  }
  return found;
}

/** Each line of `text` with its number, comments taken off (a sentence naming a rule is not a breach of it). */
function codeLines(text) {
  return text.split(/\r?\n/).map((line, at) => [at + 1, line.replace(/\/\/.*$/, "").replace(/^\s*\*.*$/, "").replace(/\/\*.*?\*\//g, "")]);
}

/**
 * Every function of a module (declared, arrow, or a class's method): [name, first line, its own text], its own text being
 * its body less the bodies of the functions inside it, so a stamp set in a handler is judged by that handler alone.
 */
function ownFunctions(text) {
  const code = codeLines(text).map(([, line]) => line).join("\n");
  const found = [];
  const stack = [];
  for (let at = 0; at < code.length; at++) {
    if (code[at] === "{") {
      const before = code.slice(0, at);
      let name = null;
      if (/=>\s*$/.test(before)) {
        name = /(\w+)\s*=\s*(?:async\s*)?(?:\([^()]*\)|\w+)\s*=>\s*$/.exec(before)?.[1] || "an arrow function";
      } else if (/\)\s*$/.test(before)) {
        let depth = 0;
        let open = before.length - 1;
        for (; open >= 0; open--) {
          if (before[open] === ")") depth++;
          else if (before[open] === "(" && --depth === 0) break;
        }
        const head = /(?:\bfunction\s*\*?\s*(\w*)|(?:^|[\n;{}])\s*(?:async\s+|static\s+)*(\w+))\s*$/.exec(before.slice(0, open));
        const word = head && (head[1] || head[2]);
        if (head && !/^(if|for|while|switch|catch|with)$/.test(word || "")) name = word || "an anonymous function";
      }
      stack.push({ name, start: at, first: code.slice(0, at).split("\n").length, inner: [], parent: stack.findLast((each) => each.name) });
    } else if (code[at] === "}" && stack.length) {
      const top = stack.pop();
      if (!top.name) continue;
      let own = code.slice(top.start, at + 1);
      for (const [from, to] of top.inner.reverse()) own = own.slice(0, from - top.start) + own.slice(to - top.start);
      top.own = own;
      found.push(top);
      if (top.parent) top.parent.inner.push([top.start, at + 1]);
    }
  }
  // A callback handed to photosWritten, or a helper of a function that hands on, is covered by that function's own call.
  const coveredAbove = (each) => { for (let up = each.parent; up; up = up.parent) if (HANDS_IT_ON.test(up.own || "")) return true; return false; };
  return found.map((each) => [each.name, each.first, each.own, coveredAbove(each)]);
}

const KEY_ARGUMENT = /localStorage\s*\.\s*(setItem|removeItem)\s*\(\s*([^,)]*)/g;
const CONSTANT_KEY = /^([A-Z][A-Z0-9_]*|'[^']*'|"[^"]*")$/;
const PATCHES_THE_LIST = [
  /state\s*\.\s*folderPhotos\s*\.\s*(splice|push|unshift|pop|shift|sort|reverse|fill|copyWithin)\s*\(/,
  /state\s*\.\s*folderPhotos\s*\[[^\]]*\]\s*=(?!=)/,
  /state\s*\.\s*folderPhotos\s*\.\s*length\s*=(?!=)/,
  /state\s*\.\s*folderPhotos\s*=(?!=)[^;]*folderPhotos/,
];
// A stamp is set by assigning a field, by Object.assign of one, or by web/common/photo-write.js takeStamp / takeWritten.
const SETS_A_STAMP = new RegExp("\\b\\w+\\s*\\.\\s*(mtime|base|taken)\\s*=(?!=)|\\b(takeStamp|takeWritten)\\s*\\("
  + "|Object\\s*\\.\\s*assign\\s*\\([^;]*\\b(mtime|base|taken)\\s*:");
const HANDS_IT_ON = /\b(recordsWritten|photosWritten|photosGone|photoDeleted)\s*\(/;

/** What breaks the rules above in `sources` ({module: text}): one sentence each, naming the module and line. */
function copiesWrittenOutsideCache(sources) {
  const found = [];
  for (const [module, text] of Object.entries(sources)) {
    const lines = codeLines(text);
    for (const [number, line] of lines) {
      for (const [, verb, key] of line.matchAll(KEY_ARGUMENT)) {
        if (!CONSTANT_KEY.test(key.trim())) {
          found.push(`${module}:${number} ${verb}s a key of local storage it computes (${key.trim()}): a kept scan is cache.js's`);
        }
      }
      if (/tagpup_cache_/.test(line)) found.push(`${module}:${number} spells a kept scan's key: cache.js folderCacheKey owns it`);
      if (/folderCacheKey\s*\(/.test(line) && !/localStorage\s*\.\s*getItem\s*\(\s*folderCacheKey\s*\(/.test(line)) {
        found.push(`${module}:${number} uses folderCacheKey other than to read a kept scan: writing one is cache.js's`);
      }
      if (PATCHES_THE_LIST.some((pattern) => pattern.test(line))) {
        found.push(`${module}:${number} patches state.folderPhotos itself: cache.js photosWritten, recordsWritten or photoDeleted`);
      }
    }
    const saves = lines.filter(([, line]) => /\bsaveToLocalStorageCache\s*\(/.test(line)
      && !/import\s*\{/.test(line)).length;
    if (saves !== (SAVES_THE_OPEN_SCAN[module] || 0)) {
      found.push(`${module} keeps the open folder's scan whole ${saves} time(s), not ${SAVES_THE_OPEN_SCAN[module] || 0}: `
        + "after a write, cache.js does (photosWritten); a list read whole is listed in SAVES_THE_OPEN_SCAN");
    }
    // The phone's modules keep no copies; web/common/photo-write.js is where takeStamp sets the stamp (it is not a page's copy).
    if (/^tagpup\/(?!mobile)/.test(module) && !module.endsWith("photo-write.js")) {
      for (const [name, first, body, covered] of ownFunctions(text)) {
        if (SETS_A_STAMP.test(body) && !HANDS_IT_ON.test(body) && !covered &&!(DRAWN_FROM_THE_RECORDS[module] || []).includes(name)) {
          found.push(`${module}:${first} ${name} sets a record's stamp or Date Taken and hands it to no cache.js call `
            + "(recordsWritten, photosWritten): the other copies of the photo keep the old one");
        }
      }
    }
  }
  return found;
}

test("no page module changes a copy of a written photo but through cache.js", () => {
  assert.deepEqual(copiesWrittenOutsideCache(pageSources()), []);
});

test("the guard finds a module that patches a kept scan, the open list or a record's stamp itself", () => {
  // A writer of the shape every one of those rows had: it takes the reply into the open record and the scans it knows of.
  const violating = {
    "tagpup/a-writer.js": [
      "import { folderCacheKey, saveToLocalStorageCache } from './cache.js';",
      "export function tookReply(photo, data, folder) {",
      "    takeStamp(photo, data);",
      "    const key = folderCacheKey(folder);",
      "    const kept = JSON.parse(localStorage.getItem(key));",
      "    localStorage.setItem(key, JSON.stringify(kept));",
      "    state.folderPhotos.splice(0, 1);",
      "    state.folderPhotos = state.folderPhotos.filter(p => p !== photo);",
      "    saveToLocalStorageCache();",
      "}",
    ].join("\n"),
  };
  const found = copiesWrittenOutsideCache(violating);
  for (const said of ["computes (key)", "uses folderCacheKey", "patches state.folderPhotos", "whole 1 time(s), not 0",
    "tookReply sets a record's stamp"]) {
    assert.ok(found.some((each) => each.includes(said)), `the guard misses: ${said}\n${found.join("\n")}`);
  }
  assert.equal(found.filter((each) => each.includes("patches state.folderPhotos")).length, 2);
});

test("the guard judges a stamp set in a handler or an Object.assign by the function it is in", () => {
  const sources = {
    "tagpup/a-setup.js": [
      "export const setup = (button) => {",
      "    button.onclick = (event) => {",
      "        Object.assign(event.record, { taken: 1, order: 2 });",
      "    };",
      "    button.ondblclick = () => { photosWritten([], () => {}); };",
      "};",
    ].join("\n"),
    "tagpup/a-kept.js": [
      "export function setup(list) {",
      "    list.forEach((record) => { takeWritten(record, null, []); });",
      "    photosWritten([], () => {});",
      "}",
    ].join("\n"),
  };
  const found = copiesWrittenOutsideCache(sources);
  assert.equal(found.length, 1, found.join("\n"));
  assert.ok(found[0].startsWith("tagpup/a-setup.js:2 onclick sets"), found[0]);
});
