#!/usr/bin/env node
/**
 * PreToolUse hook (Bash): refuse an inline script fed through a heredoc when its body
 * contains a backslash escape.
 *
 * CLAUDE.md: "Write patch scripts with a file tool, not a shell heredoc. Backslashes
 * and \u escapes are mangled in transit." It cost time three times in one day: a
 * "\n" in a Python string became a real line break and broke the file (83 lint
 * errors), a "\\" became "\" and a regex matched nothing. A rule to remember did not
 * hold; this does.
 *
 * Blocks: a heredoc (<<EOF, <<'EOF', <<-EOF) whose command line runs an interpreter
 * (python, python.exe, py, node, perl, ruby) and whose body contains \n \t \r \u \x
 * \0 or \\. Allows everything else -- including commit messages fed by heredoc, and
 * `python -c "..."`, which never went through a heredoc.
 *
 * Reads the hook payload on stdin; exit 2 with a reason on stderr blocks the call and
 * shows the reason to Claude. Any error in the hook itself allows the call (exit 0):
 * a broken guard must not stop all shell use.
 */
import { readFileSync } from "node:fs";

// Preceded by a path separator too: this repo runs .venv/Scripts/python.exe.
const INTERPRETER = /(^|[\s;&|(/\\])(python3?|python(\.exe)?|py(\.exe)?|node(\.exe)?|perl|ruby)(\s|$)/i;
const ESCAPE = /\\[ntrux0\\]/;
const HEREDOC = /<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/g;

/** Every heredoc in a command: the line that opens it, and its body. */
export function heredocs(command) {
  const found = [];
  const lines = command.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    HEREDOC.lastIndex = 0;
    let match;
    while ((match = HEREDOC.exec(lines[i])) !== null) {
      const delimiter = match[2];
      const body = [];
      let j = i + 1;
      while (j < lines.length && lines[j].trim() !== delimiter) body.push(lines[j++]);
      found.push({ opener: lines[i], body: body.join("\n") });
    }
  }
  return found;
}

/** The reason to refuse this command, or null to let it run. */
export function reasonToBlock(command) {
  for (const { opener, body } of heredocs(command || "")) {
    if (INTERPRETER.test(opener) && ESCAPE.test(body)) {
      return "Backslash escapes in a heredoc are mangled in transit (CLAUDE.md: write " +
        "patch scripts with a file tool, not a shell heredoc). Write the script with " +
        "the Write tool and run the file.";
    }
  }
  return null;
}

function main() {
  let payload;
  try {
    payload = JSON.parse(readFileSync(0, "utf8") || "{}");
  } catch {
    process.exit(0);
  }
  const reason = reasonToBlock(payload?.tool_input?.command);
  if (reason) {
    process.stderr.write(reason + "\n");
    process.exit(2);
  }
  process.exit(0);
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop())) {
  main();
}
