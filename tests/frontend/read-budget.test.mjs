/**
 * The PreToolUse hook that has a worker read by part (retro 2026-11-13: whole-file Reads were the largest share of a
 * worker's context, slices of findings.md the largest single reads). The scenario: a worker opens a large module
 * whole, then the findings ledger, and is pointed at tools/code.py and add_findings.py --open; the main session, a
 * slice, a picture, a short file, a file not there and a payload the hook cannot read all go through.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { LIMIT, isLedger, linesOver, normal, reasonToBlock } from "../../.claude/hooks/read-budget.mjs";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const HOOK = path.join(ROOT, ".claude", "hooks", "read-budget.mjs");
const JOURNAL = path.join(ROOT, "tagpup", "store", "journal.py");

const folder = mkdtempSync(path.join(tmpdir(), "read-budget-"));
const file = (name, text) => {
  const at = path.join(folder, name);
  writeFileSync(at, text);
  return at;
};
const lines = (n, end = "\n") => Array.from({ length: n }, (_v, i) => `line ${i}`).join(end) + end;

test("lines are counted to the limit and no further; CRLF counts once, a last line with no newline counts", () => {
  assert.equal(linesOver(file("short.py", lines(LIMIT))), LIMIT);
  assert.equal(linesOver(file("crlf.py", lines(LIMIT, "\r\n"))), LIMIT);
  assert.equal(linesOver(file("tail.py", lines(LIMIT - 1) + "no newline")), LIMIT);
  assert.equal(linesOver(file("long.py", lines(LIMIT * 400))), LIMIT + 1);
  assert.equal(linesOver(file("empty.py", "")), 0);
});

test("the ledgers are known by either slash and any case, and nothing else is", () => {
  assert.equal(isLedger(normal("C:\\src\\TagPup\\docs\\findings.md")), true);
  assert.equal(isLedger(normal("docs/DECISIONS.md", "C:\\src\\wt")), true);
  assert.equal(isLedger(normal("C:/src/TagPup/Docs/Findings.md")), true);
  assert.equal(isLedger(normal("C:/src/TagPup/docs/findings_pending/x.md")), false);
  assert.equal(isLedger(normal("C:/src/TagPup/docs/history/findings_closed_2001-2500.md")), false);
});

test("a worker opening a large module whole is pointed at outline and show; a slice of it goes through", () => {
  const reason = reasonToBlock({ file_path: JOURNAL }, ROOT);
  assert.match(reason, /more than 300 lines/);
  assert.match(reason, /tools\/code\.py outline/);
  assert.match(reason, /tools\/code\.py show .*::NAME/);
  assert.match(reason, /offset and limit/);
  assert.equal(reasonToBlock({ file_path: JOURNAL, offset: 200, limit: 60 }, ROOT), null);
  assert.equal(reasonToBlock({ file_path: JOURNAL, limit: 60 }, ROOT), null);
  assert.equal(reasonToBlock({ file_path: JOURNAL, offset: 1 }, ROOT), null);
  assert.match(reasonToBlock({ file_path: "tagpup\\store\\journal.py" }, ROOT), /tagpup\\store\\journal\.py::NAME/);
});

test("the ledgers are refused whole and by slice, naming --open and grep -n", () => {
  for (const input of [{ file_path: path.join(ROOT, "docs", "findings.md") },
                       { file_path: "docs/findings.md", offset: 1, limit: 40 },
                       { file_path: path.join(ROOT, "docs", "DECISIONS.md"), limit: 10 }]) {
    const reason = reasonToBlock(input, ROOT);
    assert.match(reason, /add_findings\.py --open/);
    assert.match(reason, /grep -n/);
  }
});

test("a short file, a picture, a PDF, a notebook and an input with no path go through, and nothing is counted", () => {
  const never = () => assert.fail("counted a file it need not open");
  assert.equal(reasonToBlock({ file_path: file("short.md", lines(12)) }, folder), null);
  for (const name of ["photo.JPG", "scan.png", "manual.pdf", "notes.ipynb"]) {
    assert.equal(reasonToBlock({ file_path: path.join(folder, name) }, folder, never), null);
  }
  assert.equal(reasonToBlock({}, folder, never), null);
  assert.equal(reasonToBlock(null, folder, never), null);
  assert.equal(reasonToBlock({ file_path: 42 }, folder, never), null);
});

test("as a hook: a worker is refused with exit 2, the main session and a broken payload are let through", () => {
  const big = file("big.js", lines(LIMIT + 1));
  const run = (payload, input) => spawnSync(process.execPath, [HOOK], {
    input: input ?? JSON.stringify(payload), encoding: "utf8",
  });
  const worker = { agent_id: "a904a18", hook_event_name: "PreToolUse", tool_name: "Read", cwd: folder };

  const refused = run({ ...worker, tool_input: { file_path: big } });
  assert.equal(refused.status, 2);
  assert.match(refused.stderr, /tools\/code\.py outline/);
  assert.equal(run({ ...worker, tool_input: { file_path: "big.js" } }).status, 2);
  assert.equal(run({ ...worker, tool_input: { file_path: "docs\\findings.md" } }).status, 2);

  assert.equal(run({ ...worker, tool_input: { file_path: big, offset: 1, limit: 50 } }).status, 0);
  assert.equal(run({ ...worker, agent_id: undefined, tool_input: { file_path: big } }).status, 0);
  assert.equal(run({ ...worker, agent_id: "", tool_input: { file_path: "docs/findings.md" } }).status, 0);
  assert.equal(run({ ...worker, tool_input: { file_path: path.join(folder, "not-there.py") } }).status, 0);
  assert.equal(run({ ...worker, tool_input: { file_path: folder } }).status, 0);
  assert.equal(run({ ...worker, tool_input: "not an object" }).status, 0);
  assert.equal(run({ ...worker, tool_name: "Grep", tool_input: { file_path: big } }).status, 0);
  assert.equal(run(undefined, "not json").status, 0);
  assert.equal(run(undefined, "").status, 0);
  assert.equal(run(null).status, 0);
});

test("the hook is registered on the Read tool, and the tool it names is there", () => {
  const settings = JSON.parse(readFileSync(path.join(ROOT, ".claude", "settings.json"), "utf8"));
  const entry = settings.hooks.PreToolUse.find((e) => e.hooks.some((h) => h.command.includes("read-budget.mjs")));
  assert.ok(entry, "read-budget.mjs is not a PreToolUse hook");
  assert.match("Read", new RegExp(`^(?:${entry.matcher})$`));
  assert.ok(readFileSync(path.join(ROOT, "tools", "code.py"), "utf8").includes("def outline("));
});
