/**
 * The PreToolUse hook that refuses inline scripts with backslash escapes in a heredoc.
 * Each "blocks" case is a command that actually damaged a file.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { reasonToBlock } from "../../.claude/hooks/no-heredoc-escapes.mjs";

const HOOK = path.join(path.dirname(fileURLToPath(import.meta.url)),
                       "..", "..", ".claude", "hooks", "no-heredoc-escapes.mjs");

const blocks = {
  "a newline escape in a python heredoc":
    ".venv/Scripts/python.exe - <<'EOF'\nprint(\"%s\\n\" % x)\nEOF",
  "a doubled backslash in an unquoted heredoc":
    "python <<EOF\np = s.replace(\"\\\\\", \"/\")\nEOF",
  "a unicode escape":
    "cd wt && /c/x/.venv/Scripts/python.exe - <<'PYEOF'\nname = \"M\\u00fcnster\"\nPYEOF",
  "a node heredoc":
    "node - <<'EOF'\nconsole.log('a\\tb')\nEOF",
  "an indented heredoc (<<-)":
    "python3 - <<-EOF\n\tprint('x\\n')\n\tEOF",
};

const allows = {
  "a commit message by heredoc": "git commit -F - <<'EOF'\nfix: a thing\n\nCo-Authored-By: x\nEOF",
  "a python heredoc with no escapes": "python - <<'EOF'\nprint(1 + 1)\nEOF",
  "python -c": "python -c \"print('a\\nb')\"",
  "an ordinary command": "git status --short",
  "a sed with backslashes, no heredoc": "sed -n 's/\\\\/\\//g' file.txt",
  "nothing at all": "",
};

for (const [name, command] of Object.entries(blocks)) {
  test(`blocks ${name}`, () => {
    assert.ok(reasonToBlock(command), command);
  });
}

for (const [name, command] of Object.entries(allows)) {
  test(`allows ${name}`, () => {
    assert.equal(reasonToBlock(command), null, command);
  });
}

test("as a hook: exit 2 and the reason on stderr to block, exit 0 to allow", () => {
  const run = (command) => spawnSync(process.execPath, [HOOK], {
    input: JSON.stringify({ tool_name: "Bash", tool_input: { command } }), encoding: "utf8",
  });
  const blocked = run(blocks["a newline escape in a python heredoc"]);
  assert.equal(blocked.status, 2);
  assert.match(blocked.stderr, /Write tool/);
  assert.equal(run(allows["a commit message by heredoc"]).status, 0);
  assert.equal(spawnSync(process.execPath, [HOOK], { input: "not json", encoding: "utf8" }).status, 0);
});
