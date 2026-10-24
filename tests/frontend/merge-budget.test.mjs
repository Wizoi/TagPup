/**
 * The PreToolUse hook that refuses a `git merge` over the branch budget without a reason, and one whose subject is
 * only a `Worktree:` line (finding #1806). The scenarios are the two merges the fourth retro found.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync, execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { addedLines, merges, reasonToBlock } from "../../.claude/hooks/merge-budget.mjs";

const HOOK = path.join(path.dirname(fileURLToPath(import.meta.url)),
                       "..", "..", ".claude", "hooks", "merge-budget.mjs");

test("a merge by -m: the ref and every message", () => {
  assert.deepEqual(merges('git merge --no-ff feature -m "Merge feature: a thing" -m "Over budget: why"'),
                   [{ ref: "feature", message: "Merge feature: a thing\n\nOver budget: why" }]);
});

test("a merge by heredoc message, after a cd", () => {
  const found = merges("cd /x && git merge --no-ff feature -F - <<'EOF'\nMerge: a thing\n\nWorktree: agent-1\nEOF");
  assert.deepEqual(found, [{ ref: "feature", message: "Merge: a thing\n\nWorktree: agent-1" }]);
});

test("not merges: abort, fast-forward only, a commit message that says merge", () => {
  assert.deepEqual(merges("git merge --abort"), []);
  assert.deepEqual(merges("git merge --ff-only feature"), []);
  assert.deepEqual(merges('git commit -m "git merge later"'), []);
  assert.deepEqual(merges("git status"), []);
});

test("a subject that is only a Worktree line is refused, however small the branch", () => {
  assert.match(reasonToBlock("Worktree: agent-a2681305d6457fbea", 10), /Worktree/);
  assert.equal(reasonToBlock("Merge: a thing\n\nWorktree: agent-a2681305d6457fbea", 10), null);
});

test("over the budget needs an Over budget line; at the budget or under does not", () => {
  assert.match(reasonToBlock("Merge: a thing", 1653), /1653 lines/);
  assert.equal(reasonToBlock("Merge: a thing\n\nOver budget: one rename across 40 files", 1653), null);
  assert.equal(reasonToBlock("Merge: a thing", 1200), null);
  assert.match(reasonToBlock("Over budget:", 1653), /budget/);
});

test("as a hook, in a repository: added lines count, deleted lines do not", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "merge-budget-"));
  const git = (...args) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@example.com", ...args],
                                        { cwd: dir, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  git("init", "-q", "-b", "main");
  writeFileSync(path.join(dir, "old.txt"), "x\n".repeat(3000));
  git("add", "."); git("commit", "-q", "-m", "base");
  git("checkout", "-q", "-b", "big");
  writeFileSync(path.join(dir, "new.txt"), "y\n".repeat(1500));
  git("add", "."); git("commit", "-q", "-m", "big");
  git("checkout", "-q", "-b", "shrink", "main");
  writeFileSync(path.join(dir, "old.txt"), "x\n");
  git("add", "."); git("commit", "-q", "-m", "shrink");
  git("checkout", "-q", "main");
  assert.equal(addedLines("big", dir), 1500);
  assert.equal(addedLines("shrink", dir), 0);

  const run = (command) => spawnSync(process.execPath, [HOOK], {
    input: JSON.stringify({ tool_name: "Bash", cwd: dir, tool_input: { command } }), encoding: "utf8",
  });
  const blocked = run('git merge --no-ff big -m "Merge big"');
  assert.equal(blocked.status, 2);
  assert.match(blocked.stderr, /Over budget/);
  assert.equal(run('git merge --no-ff big -m "Merge big" -m "Over budget: a generated table"').status, 0);
  assert.equal(run('git merge --no-ff shrink -m "Merge shrink"').status, 0);
  assert.equal(run('git merge --no-ff shrink -m "Worktree: agent-1"').status, 2);
  assert.equal(run("git merge --no-ff nonesuch -m x").status, 0);
  assert.equal(spawnSync(process.execPath, [HOOK], { input: "not json", encoding: "utf8" }).status, 0);
});
