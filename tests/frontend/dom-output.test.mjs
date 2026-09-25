/**
 * The pages show the library's text as text, never as markup.
 *
 * Tag names, people's names, captions and paths come from the photo files, written by
 * other programs. Written into innerHTML, one holding "<" is an element or a script and
 * one holding a quote breaks the attribute it is in; TagPup's placement question did
 * exactly that with the typed tag and every tag path it offered. web/common/dom.js
 * builds elements from text, and this fails a page module that writes anything but a
 * fixed string into innerHTML, outerHTML or insertAdjacentHTML. `innerHTML = ''` and a
 * string with nothing put into it are allowed: they carry no value.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, loadApp, FakeServer, photoRecord, flush, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

/** A string literal starting at `i`: its end, and whether a value is put into it. */
function literalAt(source, i) {
  const quote = source[i];
  if (!`'"\``.includes(quote)) return null;
  for (let j = i + 1; j < source.length; j++) {
    if (source[j] === "\\") { j++; continue; }
    if (quote === "`" && source[j] === "$" && source[j + 1] === "{") return { end: j, interpolated: true };
    if (source[j] === quote) return { end: j + 1, interpolated: false };
    if (quote !== "`" && source[j] === "\n") return null;
  }
  return null;
}

/** Does a fixed string stand alone at `i` -- nothing added to it, nothing chosen between? */
function fixedStringAt(source, i, closers) {
  const literal = literalAt(source, i);
  if (!literal || literal.interpolated) return false;
  const rest = source.slice(literal.end).match(/^\s*(.)/s);
  return !rest || closers.includes(rest[1]);
}

/** Each write of a value into markup in `source`: [line, what was written]. */
export function markupWrites(source) {
  const found = [];
  const lineOf = (index) => source.slice(0, index).split("\n").length;
  for (const match of source.matchAll(/\.(innerHTML|outerHTML)\s*(\+?=)(?!=)\s*/g)) {
    const at = match.index + match[0].length;
    if (match[2] === "+=" || !fixedStringAt(source, at, ";,)}\n")) {
      found.push([lineOf(match.index), source.slice(match.index, at + 40).split("\n")[0]]);
    }
  }
  for (const match of source.matchAll(/\.insertAdjacentHTML\s*\(\s*/g)) {
    const first = literalAt(source, match.index + match[0].length);
    const comma = first && source.slice(first.end).match(/^\s*,\s*/);
    const second = comma ? first.end + comma[0].length : -1;
    if (!comma || !fixedStringAt(source, second, ")")) {
      found.push([lineOf(match.index), source.slice(match.index, match.index + 60).split("\n")[0]]);
    }
  }
  return found;
}

/** Every module the server serves the pages. */
function pageModules() {
  const files = [];
  for (const dir of ["web/common", "web/tagpup", "web/tuner"]) {
    for (const name of fs.readdirSync(path.join(REPO_ROOT, dir))) {
      if (name.endsWith(".js")) files.push(path.join(REPO_ROOT, dir, name));
    }
  }
  return files;
}

describe("no page writes a value into markup", () => {
  test("the guard sees what it is for", () => {
    const allowed = [
      "el.innerHTML = '';",
      "el.innerHTML = \"\";",
      "el.innerHTML = '<b>Loading</b>';",
      "el.innerHTML = `<b>Loading</b>`;\n",
      "el.innerHTML = `\n  <div class=\"x\">fixed</div>\n`;",
      "el.insertAdjacentHTML('beforeend', '<hr>');",
      "const saved = el.innerHTML;",
      "if (el.innerHTML === '') go();",
    ];
    const refused = [
      "el.innerHTML = name;",
      "el.innerHTML = `<b>${name}</b>`;",
      "el.innerHTML = '<b>' + name + '</b>';",
      "el.innerHTML = cold ? '<b>a</b>' : '<b>b</b>';",
      "el.innerHTML += '<hr>';",
      "el.outerHTML = markup;",
      "el.insertAdjacentHTML('beforeend', markup);",
      "el.insertAdjacentHTML(where, '<hr>');",
      "el.innerHTML =\n    '<p>' + more;",
    ];
    for (const source of allowed) assert.deepEqual(markupWrites(source), [], source);
    for (const source of refused) assert.equal(markupWrites(source).length, 1, source);
  });

  test("in any module the pages load", () => {
    const writes = [];
    for (const file of pageModules()) {
      for (const [line, text] of markupWrites(fs.readFileSync(file, "utf8"))) {
        writes.push(`${path.relative(REPO_ROOT, file)}:${line}: ${text}`);
      }
    }
    assert.deepEqual(writes, [], "build the elements with web/common/dom.js");
  });
});

describe("TagPup's placement question shows the tag as typed", () => {
  test("a tag holding markup and quotes is text, and each place is offered as it is spelled", async (t) => {
    const roots = [
      { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
      { id: 2, tag: "Activity", name: "Activity", parent_id: null, has_face: 0 },
      { id: 3, tag: 'Kids "Club"', name: 'Kids "Club"', parent_id: null, has_face: 0 },
    ];
    const server = new FakeServer()
      .on("/api/tags", [])
      .on("/api/people", [])
      .on("/api/taxonomy/tree", roots)
      .on("/api/taxonomy/create", { success: true })
      .on("/api/databases", { databases: ["photo_index"] })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
      .on("/api/folder/scan", [photoRecord({ filename: "a.jpg" })])
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 });
    const ctx = await loadApp("tagpup", { t, server });
    await openFolder(ctx, "D:/Library/2020", { settle: 6 });
    ctx.document.querySelector("li[data-path]").click();
    await flush(ctx.window, 8);

    const input = ctx.document.getElementById("input-add-tag");
    input.value = 'Relay <img src="x"> team';
    input.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await flush(ctx.window, 8);

    const modal = ctx.document.querySelector(".modal-overlay.active");
    assert.ok(modal, "the placement question did not open");
    assert.equal(modal.querySelector("img"), null, "the typed tag became an element");
    assert.match(modal.querySelector(".modal-body p").textContent, /"Relay <img src="x"> team" is new/);
    const offered = [...modal.querySelectorAll('input[name="placement-opt"]')].map((r) => r.value);
    assert.deepEqual(offered, ["Activity", 'Kids "Club"', "__new_root__"]);
    assert.equal(modal.querySelector('input[name="placement-opt"]:checked').value, "Activity");
    assert.equal(modal.querySelector(".modal-header h2").textContent, "Resolve New Tag");
  });
});
