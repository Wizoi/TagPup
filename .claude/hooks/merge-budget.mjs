#!/usr/bin/env node
/**
 * PreToolUse hook (Bash): refuse a `git merge` that breaks the branch budget or names itself badly
 * (DECISIONS 2026-10-23, the fourth retro's action 3; finding #1806).
 *
 * Two things were found at the retro: a merge added 1,653 lines against the budget of 1,200 with no reason in its
 * message, and a merge's whole subject was the `Worktree: agent-<id>` line. A rule to remember did not hold; this does.
 *
 * Refuses a merge command whose message
 *   - has a subject that is only a `Worktree:` line (the line belongs in the body, under the owner's subject), or
 *   - merges a branch adding more than LIMIT lines (`git diff --numstat`, the merge base against the branch tip, the
 *     count `tools/retro_metrics.py --branch` makes) and has no `Over budget: <reason>` line.
 * A deletion is never over budget: only added lines count.
 *
 * The message is read from the command: every `-m` argument, or the heredoc a `-F -` reads. A merge with no message
 * on the command is judged as one with an empty message. `--abort`, `--continue`, `--quit` and `--ff-only` (no merge
 * commit, so no message) are let through.
 *
 * Reads the hook payload on stdin; exit 2 with a reason on stderr blocks the call. Anything that goes wrong in the
 * hook itself (no git, a ref it cannot resolve, a command it cannot read) allows the call: a broken guard must not
 * stop all shell use.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

export const LIMIT = 1200;
const OVER_BUDGET = /^Over budget: \S/m;
const WORKTREE = /^Worktree: \S/;

/** Shell words of one command line, quotes removed. Enough for `git merge --no-ff -m "x" branch`. */
export function words(line) {
  const found = [];
  const token = /"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+)/g;
  let match;
  while ((match = token.exec(line)) !== null) found.push(match[1] ?? match[2] ?? match[3]);
  return found;
}

/** The merges in a command: `{ ref, message }` for each `git merge` that makes a commit, in order. */
export function merges(command) {
  const lines = (command || "").split(/\r?\n/);
  const found = [];
  for (let i = 0; i < lines.length; i++) {
    const at = lines[i].search(/(^|[\s;&|(])git(\.exe)?\s+(-\S+\s+(\S+\s+)?)*merge(\s|$)/);
    if (at < 0) continue;
    const args = words(lines[i].slice(lines[i].indexOf("merge", at) + "merge".length));
    if (args.some((a) => ["--abort", "--continue", "--quit", "--ff-only"].includes(a))) continue;
    const messages = [];
    let ref = null;
    let fromStdin = false;
    for (let k = 0; k < args.length; k++) {
      const a = args[k];
      if (a === "-m" || a === "--message") messages.push(args[++k] ?? "");
      else if (a.startsWith("--message=")) messages.push(a.slice("--message=".length));
      else if (a === "-F" || a === "--file") fromStdin = args[++k] === "-";
      else if (a.startsWith("-") || a.startsWith("<")) continue;
      else ref = a;
    }
    if (fromStdin) {
      const opener = /<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/.exec(lines[i]);
      if (opener) {
        const body = [];
        for (let j = i + 1; j < lines.length && lines[j].trim() !== opener[2]; j++) body.push(lines[j]);
        messages.push(body.join("\n"));
      }
    }
    if (ref) found.push({ ref, message: messages.join("\n\n") });
  }
  return found;
}

/** The reason to refuse a merge of `added` lines with this message, or null. */
export function reasonToBlock(message, added) {
  const subject = (message || "").split(/\r?\n/).find((l) => l.trim() !== "") ?? "";
  if (WORKTREE.test(subject.trim())) {
    return "The merge subject is only a `Worktree:` line. Write the subject first (what the merge does), then the " +
      "`Worktree: agent-<id>` line in the body.";
  }
  if (added > LIMIT && !OVER_BUDGET.test(message || "")) {
    return `This branch adds ${added} lines, over the budget of ${LIMIT} (CLAUDE.md, "Keeping the project small"). ` +
      "Cut it into merges that each leave the trunk whole, or put a line `Over budget: <reason>` in the merge message " +
      "(`tools/retro_metrics.py --branch REF --allow-over REASON` prints it).";
  }
  return null;
}

/** Lines the branch `ref` adds against its merge base with HEAD, or 0 when git cannot say. */
export function addedLines(ref, cwd) {
  const git = (...args) => execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  const base = git("merge-base", "HEAD", ref).trim();
  return git("diff", "--numstat", "--no-renames", base, ref).split(/\r?\n/).reduce((sum, line) => {
    const added = line.split("\t")[0];
    return sum + (/^\d+$/.test(added) ? Number(added) : 0);
  }, 0);
}

function main() {
  let payload;
  try {
    payload = JSON.parse(readFileSync(0, "utf8") || "{}");
  } catch {
    process.exit(0);
  }
  try {
    for (const { ref, message } of merges(payload?.tool_input?.command)) {
      let added = 0;
      try {
        added = addedLines(ref, payload.cwd || process.cwd());
      } catch {
        // a ref git cannot resolve is git's to refuse
      }
      const reason = reasonToBlock(message, added);
      if (reason) {
        process.stderr.write(reason + "\n");
        process.exit(2);
      }
    }
  } catch {
    // a broken guard says nothing
  }
  process.exit(0);
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop())) {
  main();
}
