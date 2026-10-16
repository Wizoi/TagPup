/**
 * The UserPromptSubmit hook that tells a long main session to hand over and /clear: the owner types a prompt in a
 * session whose last call carried over 300k of context and the main session is told; under it, or with anything
 * wrong with the payload or the file, nothing is said and the prompt goes through. No text of the transcript reaches
 * its output.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { LIMIT, lastContext } from "../../.claude/hooks/context-budget.mjs";

const HOOK = path.join(path.dirname(fileURLToPath(import.meta.url)),
                       "..", "..", ".claude", "hooks", "context-budget.mjs");
const CANARY = "CANARY-Odile-Brannagh-4410";

const call = (input, read, write, extra = {}) => ({
  type: "assistant", isSidechain: false, sessionId: "s-1",
  message: { role: "assistant", model: "claude-test", content: [{ type: "text", text: CANARY }],
             usage: { input_tokens: input, output_tokens: 50, cache_read_input_tokens: read,
                      cache_creation_input_tokens: write } },
  ...extra,
});
const said = { type: "user", message: { role: "user", content: CANARY + " ünïcode" } };
const apiError = { type: "assistant", isApiErrorMessage: true,
                   message: { model: "<synthetic>", content: [{ type: "text", text: CANARY }],
                              usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0,
                                       cache_creation_input_tokens: 0 } } };

function inAHome(records, check) {
  const home = mkdtempSync(path.join(tmpdir(), "context-budget-"));
  try {
    const file = path.join(home, "s-1.jsonl");
    writeFileSync(file, records.map((r) => (typeof r === "string" ? r : JSON.stringify(r))).join("\n") + "\n");
    return check(file);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

const hook = (payload) => spawnSync(process.execPath, [HOOK], {
  input: typeof payload === "string" ? payload : JSON.stringify(payload), encoding: "utf8",
});

test("a prompt after a call over the budget tells the main session to hand over and /clear", () => {
  inAHome([said, call(2, 310000, 1000), said], (file) => {
    const done = hook({ hook_event_name: "UserPromptSubmit", transcript_path: file, prompt: CANARY });
    assert.equal(done.status, 0);
    const out = JSON.parse(done.stdout);
    assert.equal(out.hookSpecificOutput.hookEventName, "UserPromptSubmit");
    assert.match(out.hookSpecificOutput.additionalContext, /311k.*memory handoff.*\/clear/);
    assert.ok(!done.stdout.includes(CANARY) && !done.stderr.includes(CANARY));
  });
});

test("the last call counts: a session back under the budget, or never over it, is told nothing", () => {
  inAHome([call(2, 900000, 0), said, call(2, 120000, 5000), said], (file) => {
    assert.equal(hook({ transcript_path: file }).stdout, "");
  });
  inAHome([call(LIMIT, 0, 0)], (file) => assert.equal(hook({ transcript_path: file }).stdout, ""));
});

test("an API error's record of zeros is not a call: the one before it counts", () => {
  inAHome([call(2, 400000, 0), apiError], (file) => assert.equal(lastContext(file), 400002));
});

test("a subagent's record is not the main session's", () => {
  inAHome([call(2, 100000, 0), call(2, 500000, 0, { isSidechain: true })],
          (file) => assert.equal(lastContext(file), 100002));
});

test("the last call is found behind a megabyte of tool output, read from the end", () => {
  const output = { type: "user", message: { content: CANARY.repeat(40000) } };   // about 1 MB, over four chunks
  inAHome([call(2, 350000, 0), output, "not json " + CANARY], (file) => assert.equal(lastContext(file), 350002));
  inAHome([said], (file) => assert.equal(lastContext(file), null));
});

test("anything wrong is a no-op: exit 0 and nothing said", () => {
  for (const payload of ["not json", "", { prompt: "no transcript_path" }, { transcript_path: 7 },
                         { transcript_path: path.join(tmpdir(), "no-such-transcript.jsonl") }]) {
    const done = hook(payload);
    assert.equal(done.status, 0, JSON.stringify(payload));
    assert.equal(done.stdout, "", JSON.stringify(payload));
  }
});
