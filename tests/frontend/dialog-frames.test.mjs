/**
 * Each dialog the pages share frames itself (review of pass/journal, item 4).
 *
 * The shared dialogs are TagTuner `.modal`s (web/common/dialog.js reads that class), and
 * only TagTuner's page styles `.modal`: TagPup's dialogs are `.modal-overlay`s. The
 * settings dialog, built as div.modal > .modal-content, opened in TagPup with no overlay,
 * no panel and no padding, at the end of the page. Each shared dialog's own stylesheet
 * gives it its frame, and both pages link it, so it looks the same in both apps.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./harness.mjs";

const COMMON = path.join(REPO_ROOT, "web", "common");
const PAGES = ["tagpup", "tuner"];

/** Each shared dialog: its module, and the class its frame goes by ("settings-modal"). */
function sharedDialogs() {
  const found = [];
  for (const file of fs.readdirSync(COMMON).filter((f) => f.endsWith(".js"))) {
    const source = fs.readFileSync(path.join(COMMON, file), "utf8");
    for (const match of source.matchAll(/className: 'modal ([\w-]+-modal)\b/g)) {
      found.push({ module: file, frame: match[1], css: file.replace(/\.js$/, ".css") });
    }
  }
  return found;
}

/** The declarations of `selector`'s own rule in `css`, or null. */
function rule(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`(?:^|\\n|,\\s*)${escaped}\\s*\\{([^}]*)\\}`));
  return match ? match[1] : null;
}

test("the shared dialogs are found", () => {
  const frames = sharedDialogs().map((d) => d.frame).sort();
  assert.ok(frames.includes("settings-modal") && frames.includes("history-modal"), frames.join(", "));
});

for (const dialog of sharedDialogs()) {
  test(`${dialog.module} frames itself in ${dialog.css}, which both pages link`, () => {
    const css = fs.readFileSync(path.join(COMMON, dialog.css), "utf8");
    const frame = rule(css, `.${dialog.frame}`);
    assert.ok(frame, `${dialog.css} has no rule for .${dialog.frame}`);
    assert.match(frame, /position:\s*fixed/, `.${dialog.frame} is no overlay`);
    assert.match(rule(css, `.${dialog.frame}.hidden`) || "", /display:\s*none/);
    const content = dialog.frame.replace(/-modal$/, "-content");
    assert.match(rule(css, `.${content}`) || "", /background-color/, `.${content} is no panel`);
    for (const page of PAGES) {
      const html = fs.readFileSync(path.join(REPO_ROOT, "web", page, "index.html"), "utf8");
      assert.ok(html.includes(`href="common/${dialog.css}"`), `web/${page}/index.html does not link ${dialog.css}`);
    }
  });
}
