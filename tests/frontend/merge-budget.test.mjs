/**
 * The PreToolUse hook that refuses a `git merge` over the branch budget without a reason, and one whose subject is
 * only a `Worktree:` line (finding #1806). The scenarios are the two merges the fourth retro found, and the two of the
 * 2026-11-13 retro (#2923): a one-time program and the service only it calls merged over the budget with an
 * `Over budget:` line, and a one-off deleted while rows still needed it (#2871).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync, execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { addedLines, merges, oneOffs, reasonToBlock } from "../../.claude/hooks/merge-budget.mjs";

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

/**
 * A repository of its own whose `main` holds `files`; `branch(name, files, gone)` commits a branch off `main` that
 * writes `files` and removes `gone`; `run(command)` runs the hook in it.
 */
function repository(files) {
  const dir = mkdtempSync(path.join(tmpdir(), "merge-budget-"));
  const git = (...args) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@example.com", ...args],
                                        { cwd: dir, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  const write = (written) => {
    for (const [name, text] of Object.entries(written)) {
      mkdirSync(path.dirname(path.join(dir, name)), { recursive: true });
      writeFileSync(path.join(dir, name), text);
    }
  };
  git("init", "-q", "-b", "main");
  write(files);
  git("add", "-A"); git("commit", "-q", "-m", "base");
  const branch = (name, written = {}, gone = []) => {
    git("checkout", "-q", "-b", name, "main");
    write(written);
    for (const file of gone) git("rm", "-q", file);
    git("add", "-A"); git("commit", "-q", "-m", name);
    git("checkout", "-q", "main");
  };
  const run = (command) => spawnSync(process.execPath, [HOOK], {
    input: JSON.stringify({ tool_name: "Bash", cwd: dir, tool_input: { command } }), encoding: "utf8",
  });
  return { dir, branch, run };
}

const PROGRAM = (body) => `${body}\n\ndef main():\n    pass\n\n\nif __name__ == "__main__":\n    main()\n`;
const ONE_OFF = PROGRAM('"""Fix the rows once: a dry run by default, --apply to write."""');
const FILLER = "y\n".repeat(1500);

test("a one-time program and the service only it calls, over the budget: refused, an Over budget line or not", () => {
  const { dir, branch, run } = repository({ "tagpup/services/clips.py": "def panel():\n    pass\n" });
  branch("convert", {
    "scripts/convert_once.py": PROGRAM("from tagpup.services import convert_old\nfrom tagpup.services import clips"),
    "tagpup/services/convert_old.py": "from tagpup.services import clips\n",
    "tests/test_converted_once.py": "from tagpup.services import convert_old\n",
    "docs/table.txt": FILLER,
  });
  assert.deepEqual(oneOffs("convert", dir),
                   { added: ["scripts/convert_once.py", "tagpup/services/convert_old.py"], deleted: [] });
  const refused = run('git merge --no-ff convert -m "Merge: convert once" -m "Over budget: a one-time script"');
  assert.equal(refused.status, 2);
  assert.match(refused.stderr, /scripts\/convert_once\.py, tagpup\/services\/convert_old\.py/);
  assert.match(refused.stderr, /No `Over budget:` line/);
  assert.equal(run('git merge --no-ff convert -m "Merge: convert once"').status, 2);
});

test("a service only an existing script calls, over the budget: refused; one a page calls is the budget's", () => {
  const { dir, branch, run } = repository({ "scripts/fill_rows.py": ONE_OFF });
  branch("service", {
    "scripts/fill_rows.py": ONE_OFF.replace('"""\n', '"""\nfrom tagpup.services import row_fill\n'),
    "tagpup/services/row_fill.py": "def fill():\n    pass\n",
    "docs/table.txt": FILLER,
  });
  assert.deepEqual(oneOffs("service", dir), { added: ["tagpup/services/row_fill.py"], deleted: [] });
  assert.equal(run('git merge --no-ff service -m "Merge: fill" -m "Over budget: a table"').status, 2);

  branch("feature", {
    "tagpup/services/row_fill.py": "def fill():\n    pass\n",
    "tagpup/web/routes.py": "from tagpup.services import row_fill\n",
    "scripts/_shared.py": "ROOT = '.'\n",
    "docs/table.txt": FILLER,
  });
  assert.deepEqual(oneOffs("feature", dir), { added: [], deleted: [] });
  assert.equal(run('git merge --no-ff feature -m "Merge: fill" -m "Over budget: a generated table"').status, 0);
  assert.equal(run('git merge --no-ff feature -m "Merge: fill"').status, 2);
});

test("a one-time program within the budget merges as before", () => {
  const { branch, run } = repository({ "README": "x\n" });
  branch("small", {
    "scripts/date_once.py": PROGRAM("from tagpup.services import dating"),
    "tagpup/services/dating.py": "def date():\n    pass\n",
  });
  assert.equal(run('git merge --no-ff small -m "Merge: date once"').status, 0);
});

test("a one-off is deleted only with its dry run's 0 left in the merge message; a measuring script freely", () => {
  const { dir, branch, run } = repository({
    "scripts/move_tags_once.py": ONE_OFF,
    "scripts/fill_rows.py": ONE_OFF,
    "scripts/measure_speed.py": PROGRAM('"""Time a page."""'),
  });
  branch("gone", {}, ["scripts/move_tags_once.py", "scripts/fill_rows.py"]);
  assert.deepEqual(oneOffs("gone", dir),
                   { added: [], deleted: ["scripts/fill_rows.py", "scripts/move_tags_once.py"] });
  const refused = run('git merge --no-ff gone -m "Merge: delete the finished one-offs, the owner ran them"');
  assert.equal(refused.status, 2);
  assert.match(refused.stderr, /`fill_rows: dry run 0 left`, `move_tags_once: dry run 0 left`/);
  assert.equal(run('git merge --no-ff gone -m "Merge: delete two" -m "fill_rows: dry run 0 left on each library"')
    .status, 2);
  assert.equal(run('git merge --no-ff gone -m "Merge: delete two" -m "move_tags_once: dry run 10 left"' +
                   ' -m "fill_rows: dry run 0 left"').status, 2);
  assert.equal(run('git merge --no-ff gone -m "Merge: delete two" -m "fill_rows: dry run 0 left"' +
                   ' -m "move_tags_once: dry run 0 left on all three libraries"').status, 0);

  branch("measure", {}, ["scripts/measure_speed.py"]);
  assert.deepEqual(oneOffs("measure", dir), { added: [], deleted: [] });
  assert.equal(run('git merge --no-ff measure -m "Merge: the measuring script goes"').status, 0);
});

test("the reason names the one-offs; with none, the budget alone decides", () => {
  const oneOff = { added: ["scripts/convert_once.py"], deleted: [] };
  assert.match(reasonToBlock("Merge\n\nOver budget: a script", 1523, oneOff), /1523 lines.*scripts\/convert_once\.py/);
  assert.equal(reasonToBlock("Merge", 1200, oneOff), null);
  assert.equal(reasonToBlock("Merge\n\nOver budget: a table", 1523, { added: [], deleted: [] }), null);
  assert.match(reasonToBlock("Merge", 10, { added: [], deleted: ["scripts/x_once.py"] }), /x_once: dry run 0 left/);
  assert.equal(reasonToBlock("Merge\n\nx_once: 0 left", 10, { added: [], deleted: ["scripts/x_once.py"] }), null);
  assert.match(reasonToBlock("Merge\n\nmax_once: 0 left", 10, { added: [], deleted: ["scripts/x_once.py"] }), /x_once/);
});
