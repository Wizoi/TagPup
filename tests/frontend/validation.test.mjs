/**
 * The pages check what may be set by the rules the server publishes, and keep no copy.
 *
 * web/common/validate.js applies what /api/rules answers (tagpup/core/validation.py).
 * The same cases run here and in tests/test_validation.py, against the rules the
 * server publishes -- tests/validation_rules.json, which that test holds to the server
 * and which every FakeServer answers /api/rules with (harness.mjs) -- so the two
 * languages cannot give different answers.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  REPO_ROOT, PUBLISHED_RULES, loadApp, FakeServer, flush, click, closeAllApps, photoRecord, openFolder,
} from "./harness.mjs";
import { ruleProblem as problem, useRules, rulesVersion } from "../../web/common/validate.js";
import { tagProblem, nameProblem, textProblem } from "../../web/common/vocabulary.js";

const CASES = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "tests", "validation_cases.json"), "utf8"));

/** A case's value: {"repeat": text, "times": n} is the text n times over. */
const valueOf = (value) => (value && typeof value === "object" && !Array.isArray(value)
  ? value.repeat.repeat(value.times) : value);

afterEach(() => closeAllApps());

describe("web/common/validate.js", () => {
  test("before the rules arrive it refuses nothing: the server still does", () => {
    // Node shares this module between the tests; this one runs first.
    assert.equal(rulesVersion(), null);
    assert.equal(problem("tag", "Places|Harbour"), null);
  });

  test("answers every case as the server does", () => {
    useRules(PUBLISHED_RULES);
    for (const [kind, value, expected] of CASES.cases) {
      assert.equal(problem(kind, valueOf(value)), expected, `${kind}: ${JSON.stringify(value)}`);
    }
  });

  test("the page's helpers ask it", () => {
    useRules(PUBLISHED_RULES);
    assert.match(tagProblem("Places//Harbour"), /empty level/);
    assert.match(nameProblem("Excluded"), /one of TagTuner's lists/);
    assert.match(textProblem("Relays\u0007"), /control character/);
    assert.equal(textProblem("Harbour Day"), null);
    assert.equal(textProblem("\u00e9".repeat(1000)), null, "2,000 bytes is the most, and allowed");
    assert.match(textProblem("\u00e9".repeat(1000) + "x"), /longer than 2,000 bytes/);
    // Beyond the first plane a character is four bytes, as Python counts it.
    assert.match(textProblem("\ud83d\udc36".repeat(500) + "x"), /longer than 2,000 bytes/);
    assert.equal(textProblem("\ud83d\udc36".repeat(500)), null);
  });

  test("a kind it has no rules for is an error, not an allowance", () => {
    useRules(PUBLISHED_RULES);
    assert.throws(() => problem("tags", "Places/Harbour"), /No rules for tags/);
  });
});

/** Every page module and shared module the server serves. */
function pageFiles() {
  const files = [];
  for (const dir of ["web/common", "web/tagpup", "web/tuner"]) {
    for (const name of fs.readdirSync(path.join(REPO_ROOT, dir))) {
      if (name.endsWith(".js")) files.push(path.join(REPO_ROOT, dir, name));
    }
  }
  return files;
}

/** Every message a rule can give, nested ones too; the text before any {value}. */
function ruleMessages(rules) {
  const found = [];
  for (const rule of rules) {
    if (rule.message) found.push(rule.message.split("{value}")[0].replace(/^'$/, ""));
    if (rule.each) found.push(...ruleMessages(rule.each));
  }
  return found;
}

describe("no page keeps a copy of a rule", () => {
  test("no rule's message is written in a page's source", () => {
    const messages = new Set(Object.values(PUBLISHED_RULES.kinds).flatMap((k) => ruleMessages(k.rules)));
    const copies = [];
    for (const file of pageFiles()) {
      const source = fs.readFileSync(file, "utf8");
      for (const message of messages) {
        if (message.length > 12 && source.includes(message)) {
          copies.push(`${path.relative(REPO_ROOT, file)}: ${message}`);
        }
      }
    }
    assert.deepEqual(copies, []);
  });

  test("nor the library name's pattern", () => {
    for (const file of pageFiles()) {
      assert.ok(!/\[a-zA-Z0-9_\\-\]/.test(fs.readFileSync(file, "utf8")), path.relative(REPO_ROOT, file));
    }
  });
});

describe("each page fetches the rules once", () => {
  for (const app of ["tagpup", "tagtuner"]) {
    test(app, async (t) => {
      const ctx = await loadApp(app, { t, server: new FakeServer() });
      await flush(ctx.window, 6);
      const asked = ctx.server.urls().filter((u) => u.includes("api/rules"));
      assert.deepEqual(asked, ["/photo_index/api/rules"]);
    });
  }
});

describe("a new library's name is held to the server's rule, reserved names too", () => {
  async function create(t, typed) {
    const server = new FakeServer()
      .on("/api/databases/create", { success: true })
      .on("/api/databases", { databases: ["photo_index"] });
    const ctx = await loadApp("tagtuner", {
      t, server, url: "http://localhost:8091/",
      before: (window) => {
        window.prompt = () => typed;
      },
    });
    const alerts = [];
    ctx.window.alert = (message) => alerts.push(message);
    await flush(ctx.window, 6);
    click(ctx.window, ctx.document.getElementById("btn-create-db"));
    await flush(ctx.window, 6);
    return { alerts, sent: server.calls.filter((c) => c.url.includes("databases/create")) };
  }

  test("a route's name is refused before it is sent", async (t) => {
    // The page's own pattern allowed it; only the server knew /api/ was taken.
    const { alerts, sent } = await create(t, "api");
    assert.deepEqual(sent, []);
    assert.deepEqual(alerts, ["'api' is the name of one of the app's own pages; choose another"]);
  });

  test("so is a character a name may not hold", async (t) => {
    const { alerts, sent } = await create(t, "kr track");
    assert.deepEqual(sent, []);
    assert.deepEqual(alerts, ["A library's name can hold only letters, numbers, underscores and hyphens."]);
  });

  test("a plain name is sent", async (t) => {
    const { alerts, sent } = await create(t, "harbour_2026");
    assert.deepEqual(alerts, []);
    assert.deepEqual(sent.map((c) => c.body), [{ db_name: "harbour_2026" }]);
  });
});

describe("TagPup: a caption is checked before it is saved", () => {
  test("one holding a control character is not sent", async (t) => {
    const server = new FakeServer()
      .on("/api/tags", [])
      .on("/api/people", [])
      .on("/api/taxonomy/tree", [])
      .on("/api/databases", { databases: ["photo_index"] })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
      .on("/api/folder/scan", [photoRecord({ filename: "a.jpg" })])
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/photo/save-metadata", { success: true });
    const ctx = await loadApp("tagpup", { t, server });
    const alerts = [];
    ctx.window.alert = (message) => alerts.push(message);
    await openFolder(ctx, "D:/Library/2020", { settle: 6 });
    ctx.document.querySelector("li[data-path]").click();
    await flush(ctx.window, 8);

    const title = ctx.document.getElementById("input-photo-title");
    title.value = "Relays\u0007";
    title.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await flush(ctx.window, 8);

    assert.equal(server.lastBody("/api/photo/save-metadata"), undefined, "the caption was sent");
    assert.equal(alerts.length, 1);
    assert.match(alerts[0], /A caption cannot contain a control character/);
  });
});
