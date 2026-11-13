/**
 * The PreToolUse hook that refuses a worker launch for a feature across two domains citing no plan (finding #2925,
 * DECISIONS 2026-11-13). The scenario is the one the retro found: Convert briefed as two worker halves the same
 * morning, a Files half and a Media half, with no plan naming the one place of each mechanism.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { WORKERS, citedPlans, crossesDomains, reasonToBlock } from "../../.claude/hooks/plan-first.mjs";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const HOOK = path.join(ROOT, ".claude", "hooks", "plan-first.mjs");

const HALF = "Domain: Files (load .claude/agents/tagpup-owner-files.md).\nDomain: Media.\nBuild the convert service.";
const ONE = "Domain: People (load .claude/agents/tagpup-owner-people.md).\nFix the face count on the Name page.";

test("a brief across two domains, or naming the integration owner, crosses; one domain or none does not", () => {
  assert.equal(crossesDomains(HALF), true);
  assert.equal(crossesDomains("Domain: Integration (its charter).\nDomain: Files.\nA hook and a route."), true);
  assert.equal(crossesDomains("Read .claude/agents/tagpup-owner-integration.md first.\nDomain: Media."), true);
  assert.equal(crossesDomains("Domain: Integration shell (one hook).\nA hook."), false);
  assert.equal(crossesDomains("Read .claude/agents/tagpup-owner-integration.md first."), false);
  assert.equal(crossesDomains("Domain: Files and Media."), true);
  assert.equal(crossesDomains("Domain: Files, Media (load the charters)."), true);
  assert.equal(crossesDomains("Domain: Files.\nDomain: Files (again)."), false);
  assert.equal(crossesDomains(ONE), false);
  assert.equal(crossesDomains("Rename a test file."), false);
  assert.equal(crossesDomains("Domain: Data.\nThe brief says two `Domain:` lines need a plan."), false);
});

test("the plans a brief cites: one per Plan line, the template is not a plan", () => {
  assert.deepEqual(citedPlans("Plan: docs/plans/convert.md\nPlan: docs/plans/clip-saves.md"),
                   ["docs/plans/convert.md", "docs/plans/clip-saves.md"]);
  assert.deepEqual(citedPlans("Plan: docs/plans/README.md"), []);
  assert.deepEqual(citedPlans("See the plan: docs/plans/convert.md, and Plan: docs/plans/../x.md"), []);
});

test("a worker half with no plan is refused; with a plan that is there it starts; a missing plan is named", () => {
  const there = (rel) => rel === "docs/plans/convert.md";
  assert.match(reasonToBlock("tagpup-worker", HALF, there), /cites no plan/);
  assert.match(reasonToBlock("tagpup-worker-small", HALF, there), /Plan: docs\/plans\/<name>\.md/);
  assert.equal(reasonToBlock("tagpup-worker", HALF + "\nPlan: docs/plans/convert.md", there), null);
  assert.match(reasonToBlock("tagpup-worker", HALF + "\nPlan: docs/plans/convrt.md", there), /convrt\.md is not there/);
  assert.match(reasonToBlock("tagpup-worker", HALF + "\nPlan: docs/plans/README.md", () => true), /cites no plan/);
});

test("one domain, an owner, a reviewer or another agent are let through", () => {
  const none = () => false;
  assert.equal(reasonToBlock("tagpup-worker", ONE, none), null);
  assert.equal(reasonToBlock("tagpup-owner-files", HALF, none), null);
  assert.equal(reasonToBlock("tagpup-reviewer", HALF, none), null);
  assert.equal(reasonToBlock(undefined, HALF, none), null);
});

test("as a hook: refused with exit 2 until the plan is written, and a broken payload allows", () => {
  const project = mkdtempSync(path.join(tmpdir(), "plan-first-"));
  const git = (...args) => spawnSync("git", ["-c", "user.name=t", "-c", "user.email=t@example.com", ...args],
                                     { cwd: project, encoding: "utf8" });
  git("init", "-q");
  const env = { ...process.env };
  delete env.CLAUDE_PROJECT_DIR;
  const run = (toolInput, input) => spawnSync(process.execPath, [HOOK], {
    input: input ?? JSON.stringify({ tool_name: "Agent", cwd: project, tool_input: toolInput }), encoding: "utf8", env,
  });
  const launch = { subagent_type: "tagpup-worker", description: "Convert, the Files half",
                   prompt: HALF + "\nPlan: docs/plans/convert.md" };
  const refused = run(launch);
  assert.equal(refused.status, 2);
  assert.match(refused.stderr, /docs\/plans\/convert\.md is not there/);

  mkdirSync(path.join(project, "docs", "plans"), { recursive: true });
  writeFileSync(path.join(project, "docs", "plans", "convert.md"), "# Convert\n");
  const uncommitted = run(launch);
  assert.equal(uncommitted.status, 2);
  assert.match(uncommitted.stderr, /not committed/);
  git("add", "docs/plans/convert.md");
  git("commit", "-q", "-m", "plan");
  assert.equal(run(launch).status, 0);
  assert.equal(run({ ...launch, prompt: HALF }).status, 2);
  assert.equal(run({ subagent_type: "tagpup-worker", prompt: ONE }).status, 0);
  assert.equal(run({ subagent_type: "tagpup-worker", prompt: 42 }).status, 0);
  assert.equal(run(null).status, 0);
  assert.equal(run(undefined, "not json").status, 0);
});

test("the hook is registered on the Agent tool, and the agents it names are the project's", () => {
  for (const name of [...WORKERS, "tagpup-owner-integration"]) {
    assert.ok(existsSync(path.join(ROOT, ".claude", "agents", name + ".md")), `${name} is not an agent: the hook guards nothing`);
  }
  const settings = JSON.parse(readFileSync(path.join(ROOT, ".claude", "settings.json"), "utf8"));
  const entry = settings.hooks.PreToolUse.find((e) => e.hooks.some((h) => h.command.includes("plan-first.mjs")));
  assert.ok(entry, "plan-first.mjs is not a PreToolUse hook");
  assert.match("Agent", new RegExp(`^(?:${entry.matcher})$`));
});
