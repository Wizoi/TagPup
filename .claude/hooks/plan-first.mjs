#!/usr/bin/env node
/**
 * PreToolUse hook (Agent): refuse a worker launch for a feature across two domains that cites no plan
 * (DECISIONS 2026-11-13, the retro amended: "the root is the plan and the owner, not the merge"; finding #2925).
 *
 * Convert (2026-11-12) was started as two worker halves the same morning, one building a script and one a service
 * for the same mechanism; the merge-budget guard caught it only at the merge. A feature that touches two domains now
 * has a short plan first (docs/plans/README.md says what it holds), and each worker launched for it cites the plan.
 *
 * Refuses launching a `tagpup-worker` or `tagpup-worker-small` whose prompt has two or more `Domain:` lines, or names
 * the integration owner (its charter `tagpup-owner-integration`, or a `Domain: Integration` line), unless the prompt
 * has a line `Plan: docs/plans/<name>.md` naming a plan that exists (the template README.md is not a plan). The plan
 * is looked for under the project (CLAUDE_PROJECT_DIR) and under the session's working folder.
 *
 * Every other launch (one domain, no domain, an owner, a reviewer) is let through. Reads the hook payload on stdin;
 * exit 2 with a reason on stderr blocks the call. Anything that goes wrong in the hook itself allows the call: a
 * broken guard must not stop all delegation.
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

export const WORKERS = new Set(["tagpup-worker", "tagpup-worker-small"]);
const DOMAIN_LINE = /^[ \t>*-]*Domain:/gim;
const INTEGRATION = /tagpup-owner-integration|^[ \t>*-]*Domain:\s*Integration\b/im;
const PLAN_LINE = /^[ \t]*Plan:[ \t]*(docs\/plans\/[A-Za-z0-9_.-]+\.md)[ \t]*$/gm;
const TEMPLATE = "docs/plans/README.md";

/** Whether a brief is for a feature across two domains: two `Domain:` lines, or the integration owner named. */
export function crossesDomains(prompt) {
  const text = prompt || "";
  return (text.match(DOMAIN_LINE) || []).length >= 2 || INTEGRATION.test(text);
}

/** The plans a brief cites, as written: every `Plan: docs/plans/<name>.md` line but the template. */
export function citedPlans(prompt) {
  return [...(prompt || "").matchAll(PLAN_LINE)].map((m) => m[1]).filter((p) => p.toLowerCase() !== TEMPLATE.toLowerCase());
}

/** The reason to refuse launching `agentType` with `prompt`, or null; `exists(rel)` says whether a cited plan is there. */
export function reasonToBlock(agentType, prompt, exists) {
  if (!WORKERS.has(agentType) || !crossesDomains(prompt)) return null;
  const plans = citedPlans(prompt);
  if (plans.some((p) => exists(p))) return null;
  const named = plans.length ? ` (${plans.join(", ")} is not there)` : "";
  return `This brief names two domains or the integration owner, and cites no plan${named} (DECISIONS 2026-11-13; ` +
    "#2925). Write the plan first, by docs/plans/README.md: the mechanisms, the one place each lives and its owner, the " +
    "steady-state writer, what is a one-off and which service it calls. Then put a line `Plan: docs/plans/<name>.md` " +
    "in the brief of every worker for the feature.";
}

/** A check that a plan (a path relative to the project) is a file under one of `roots`. */
export function existsUnder(roots) {
  return (rel) => roots.filter(Boolean).some((root) => existsSync(path.join(root, ...rel.split("/"))));
}

function main() {
  let payload;
  try {
    payload = JSON.parse(readFileSync(0, "utf8") || "{}");
  } catch {
    process.exit(0);
  }
  try {
    const input = payload?.tool_input || {};
    const exists = existsUnder([process.env.CLAUDE_PROJECT_DIR, payload.cwd]);
    const reason = reasonToBlock(input.subagent_type, input.prompt, exists);
    if (reason) {
      process.stderr.write(reason + "\n");
      process.exit(2);
    }
  } catch {
    // a broken guard says nothing
  }
  process.exit(0);
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop())) {
  main();
}
