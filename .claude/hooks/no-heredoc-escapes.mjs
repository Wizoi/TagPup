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
 * Blocks: any heredoc (<<EOF, <<'EOF', <<-EOF) whose body contains \n \t \r \u \x
 * \0 or \\ -- whether it feeds an interpreter or writes a file (cat > fix.py, tee),
 * since a file written that way is run a moment later. Allows a heredoc that is a
 * git message (git commit -F -), `python -c "..."`, and everything else.
 *
 * Reads the hook payload on stdin; exit 2 with a reason on stderr blocks the call and
 * shows the reason to Claude. Any error in the hook itself allows the call (exit 0):
 * a broken guard must not stop all shell use.
 */
import { readFileSync } from "node:fs";

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

// A heredoc that is a git message (commit -F -, tag -F -) is prose, not code, and
// is the one place this repo uses heredocs on purpose.
const GIT_MESSAGE = /(^|[\s;&|(])git(\.exe)?\s/i;

/** The reason to refuse this command, or null to let it run.
 *
 * Any heredoc, not only one piped into an interpreter: `cat > fix.py <<'EOF'` and
 * `tee fix.py <<EOF` write the same mangled script to a file and run it a moment
 * later, and a review found the interpreter-only rule let exactly that through.
 */
export function reasonToBlock(command) {
  for (const { opener, body } of heredocs(command || "")) {
    if (GIT_MESSAGE.test(opener)) continue;
    if (ESCAPE.test(body)) {
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
