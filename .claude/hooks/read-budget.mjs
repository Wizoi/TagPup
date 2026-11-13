#!/usr/bin/env node
/**
 * PreToolUse hook (Read): a subagent reads by part, not by file (retro 2026-11-13).
 *
 * Measured over 473 subagent runs past 150k: about 96k tokens a run were tool results, and whole-file Reads were the
 * largest share (18.6k a run), then Read slices (14.6k); the worst single reads were slices of docs/findings.md (32k)
 * and docs/DECISIONS.md (19k) and whole modules (journal.py, 18k).
 *
 * For a subagent only (the payload has `agent_id`, as context-budget.mjs reads it), refuses
 *   - a Read of docs/findings.md or docs/DECISIONS.md, whole or a slice: the open findings are
 *     `tools/add_findings.py --open`, and one row or one decision is a `grep -n` away;
 *   - a Read with neither `offset` nor `limit` of a text file over LIMIT lines: `tools/read_code.py outline` and `show`
 *     read it by definition, or Read takes offset+limit.
 * An image, a PDF, a notebook, a file of LIMIT lines or fewer, and any Read by the main session pass. Lines are counted
 * by reading the file in chunks until LIMIT is passed, never the whole of a large file.
 *
 * Reads the hook payload on stdin; exit 2 with a reason on stderr blocks the call. A payload it cannot read, a file
 * that is not there or cannot be opened (the Read tool says so itself), or anything else that goes wrong in the hook
 * lets the call through: a broken guard must never stop a call.
 */
import { closeSync, openSync, readFileSync, readSync } from "node:fs";
import path from "node:path";

export const LIMIT = 300;
const CHUNK = 64 * 1024;
/** Read by the tool as something other than lines of text. */
const NOT_TEXT = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff", ".heic", ".pdf",
                          ".ipynb"]);
const LEDGERS = ["docs/findings.md", "docs/decisions.md"];

/** A path with forward slashes, made absolute against `cwd` when it is relative. */
export function normal(file, cwd) {
  const forward = String(file).replace(/\\/g, "/");
  const absolute = /^([A-Za-z]:)?\//.test(forward) || !cwd ? forward : String(cwd).replace(/\\/g, "/") + "/" + forward;
  return path.posix.normalize(absolute);
}

/** Whether the path is the findings ledger or the decisions log of a checkout (any letter case: Windows). */
export function isLedger(file) {
  const lower = file.toLowerCase();
  return LEDGERS.some((ledger) => lower === ledger || lower.endsWith("/" + ledger));
}

/** The file's line count, or LIMIT + 1 as soon as it has more than LIMIT; a last line with no newline counts. */
export function linesOver(file, most = LIMIT) {
  const fd = openSync(file, "r");
  try {
    const buffer = Buffer.alloc(CHUNK);
    let lines = 0;
    let last = 10;
    for (;;) {
      const read = readSync(fd, buffer, 0, CHUNK, null);
      if (read === 0) break;
      for (let i = 0; i < read; i++) if (buffer[i] === 10) lines++;
      last = buffer[read - 1];
      if (lines > most) return most + 1;
    }
    return lines + (last === 10 ? 0 : 1);
  } finally {
    closeSync(fd);
  }
}

/** The reason to refuse a subagent's Read of `input` (the tool's input), or null; `count(file)` counts the lines of
 * the file as the system names it (a UNC path kept whole). */
export function reasonToBlock(input, cwd, count = linesOver) {
  if (!input || typeof input.file_path !== "string" || !input.file_path) return null;
  const file = normal(input.file_path, cwd);
  if (isLedger(file)) {
    return `${path.posix.basename(file)} is not read by a worker (a slice of findings.md cost 32k tokens, retro ` +
      "2026-11-13). The open findings: `python tools/add_findings.py --open`. One row or one decision: `grep -n` for " +
      "its number or a word (the Grep tool with head_limit), then Read a few lines with offset+limit only if needed.";
  }
  if (input.offset != null || input.limit != null) return null;
  if (NOT_TEXT.has(path.posix.extname(file).toLowerCase())) return null;
  if (count(cwd ? path.resolve(String(cwd), input.file_path) : input.file_path) <= LIMIT) return null;
  return `${input.file_path} has more than ${LIMIT} lines: a whole read is refused for a worker (retro 2026-11-13). ` +
    `Read it by part: \`python tools/read_code.py outline ${input.file_path}\` lists its definitions or headings with their ` +
    `lines; \`python tools/read_code.py show ${input.file_path}::NAME\` prints one function, class or section; or Read ` +
    "with offset and limit.";
}

function main() {
  let payload;
  try {
    payload = JSON.parse(readFileSync(0, "utf8") || "{}");
  } catch {
    process.exit(0);
  }
  try {
    if (!payload?.agent_id) process.exit(0);   // the main session is not guarded
    if (payload.tool_name && payload.tool_name !== "Read") process.exit(0);
    const reason = reasonToBlock(payload.tool_input, payload.cwd);
    if (reason) {
      process.stderr.write(reason + "\n");
      process.exit(2);
    }
  } catch {
    // a broken guard says nothing: a file that is not there is the Read tool's to report
  }
  process.exit(0);
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop())) {
  main();
}
