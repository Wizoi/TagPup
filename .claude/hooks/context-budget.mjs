#!/usr/bin/env node
/**
 * UserPromptSubmit hook: a context budget for the main session (DECISIONS 2026-10-16, the first retro's action 2).
 *
 * The retro counted the main session's average context at about 500k tokens a call and 55% of all calls above 150k:
 * every turn re-read a context most of which the work no longer needed. When the last call of this session carried
 * more than LIMIT tokens of context (input + cache read + cache write), this adds one line to the prompt's context
 * telling the main session to write the memory handoff and suggest `/clear` to the owner.
 *
 * Reads the hook payload on stdin; its `transcript_path` (a field of every hook's input in Claude Code's hook
 * reference) is the session's .jsonl. Only the tail is read, backwards, until the last assistant record with usage;
 * of that record only `type`, `isSidechain` and `message.usage` are looked at, never message text. A payload without
 * the path, a file that cannot be read, or anything else that goes wrong is a no-op: exit 0, nothing said. A broken
 * guard must never stop a prompt.
 *
 * A subagent has the same budget at a lower limit (retro 2026-10-18, action 3; the worker's context is held to
 * WORKER_LIMIT): the same hook, run after a tool call (PostToolUse) with the subagent's `agent_id`, reads the
 * subagent's own transcript (`<session>/subagents/agent-<id>.jsonl`, whose records are sidechain) and tells the
 * worker to commit what it has and report.
 */
import { closeSync, fstatSync, openSync, readFileSync, readSync } from "node:fs";
import path from "node:path";

export const LIMIT = 300000;
export const WORKER_LIMIT = 150000;
const CHUNK = 256 * 1024;
const MOST = 8 * 1024 * 1024;

/** The context of one transcript line if it is a main-session assistant call with usage, else null. An API error's
 * record carries a usage of zeros (20 records in the transcripts on 2026-10-16, 14 of them API errors): it is not
 * a call, so it is skipped. */
export function contextOf(line, sidechain = false) {
  if (!line.includes('"assistant"') || !line.includes('"usage"')) return null;
  let record;
  try {
    record = JSON.parse(line);
  } catch {
    return null;
  }
  const usage = record?.message?.usage;
  if (record?.type !== "assistant" || Boolean(record.isSidechain) !== sidechain || !usage || typeof usage !== "object") return null;
  const context = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0) +
    (usage.cache_creation_input_tokens || 0);
  return context > 0 || usage.output_tokens > 0 ? context : null;
}

/** The context of the last assistant call in the transcript at `path`, read from its end, or null. */
export function lastContext(file, sidechain = false) {
  const fd = openSync(file, "r");
  try {
    const size = fstatSync(fd).size;
    let end = size;
    let carry = Buffer.alloc(0);
    while (end > 0 && size - end < MOST) {
      const start = Math.max(0, end - CHUNK);
      const chunk = Buffer.alloc(end - start);
      readSync(fd, chunk, 0, chunk.length, start);
      const data = Buffer.concat([chunk, carry]);
      let stop = data.length;
      for (let i = data.length - 1; i >= -1; i--) {
        if (i >= 0 ? data[i] !== 10 : start > 0) continue;
        const found = stop > i + 1 ? contextOf(data.subarray(i + 1, stop).toString("utf8"), sidechain) : null;
        if (found !== null) return found;
        stop = i;
      }
      carry = data.subarray(0, Math.max(0, stop));   // the line the chunk began inside: finished by the next
      end = start;
    }
    return null;
  } finally {
    closeSync(fd);
  }
}

/** The line to add to the prompt's context, or null under the budget. */
export function advice(context) {
  if (context === null || context <= LIMIT) return null;
  return `Context budget: the last call carried ${Math.round(context / 1000)}k tokens of context, over ` +
    `${LIMIT / 1000}k (DECISIONS 2026-10-16, the first retro). Before more work, write the memory handoff and ` +
    "suggest /clear to the owner.";
}

/** The line for a subagent whose last call carried more than WORKER_LIMIT, or null. */
export function workerAdvice(context) {
  if (context === null || context <= WORKER_LIMIT) return null;
  return `Context budget: your last call carried ${Math.round(context / 1000)}k tokens of context, over ` +
    `${WORKER_LIMIT / 1000}k (DECISIONS 2026-10-18, the second retro). Commit what you have now and report: ` +
    "a fresh worker does the rest.";
}

/** The subagent's own transcript: the session's file `<id>.jsonl` has its subagents in `<id>/subagents/`. */
export function subagentTranscript(sessionFile, agentId) {
  if (typeof sessionFile !== "string" || typeof agentId !== "string" || !/^[0-9a-z]+$/i.test(agentId)) return null;
  return path.join(path.dirname(sessionFile), path.basename(sessionFile, ".jsonl"), "subagents",
                   `agent-${agentId}.jsonl`);
}

function main() {
  try {
    const payload = JSON.parse(readFileSync(0, "utf8") || "{}");
    if (typeof payload?.transcript_path !== "string") return;
    let line;
    let event = "UserPromptSubmit";
    if (payload.agent_id) {
      event = "PostToolUse";
      const own = subagentTranscript(payload.transcript_path, payload.agent_id);
      line = own && workerAdvice(lastContext(own, true));
    } else if (payload.hook_event_name === "PostToolUse") {
      return;   // the main session is asked on the owner's prompt, not after every tool call
    } else {
      line = advice(lastContext(payload.transcript_path));
    }
    if (line) {
      process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: event, additionalContext: line } })
        + "\n");
    }
  } catch {
    // a broken guard says nothing and lets the prompt through
  }
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop())) {
  main();
  process.exit(0);
}
