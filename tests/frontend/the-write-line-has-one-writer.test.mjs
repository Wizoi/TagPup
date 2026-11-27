/**
 * Whether a write is sent (the server finishes it whatever the page does) or still in this page (closing the tab loses it,
 * so the browser asks) is decided by its entry in `state.writeQueue`: its `status` and, while a write refused as changed
 * on disk is read again to be sent once more, `backInPage`. web/tagpup/write-queue.js is the one writer of both
 * (markEntry, markBackInPage) and reads them for the write line and the Leave prompt (leaveSaid, waitingInPage).
 *
 * Cut C's second review found a re-read path that never marked its entry back in the page (#3442: a tag chunk refused
 * because a photo named by id had moved was read again while the line called it sent and closing the tab did not ask),
 * a regression of the first round's fix (retro 2026-11-26, #3449). This guard fails when:
 *
 * - a module other than write-queue.js sets an entry's `backInPage`, or its `status` to a write's state;
 * - a module of TagPup's page sends a save again through photo-write.js postOrSendAgain without `onThisPage(entry)`
 *   (edits.js), the options that mark it back in the page;
 * - a module of TagPup's page reads moved photos again (photo-write.js readMoved) and does not mark its entry back in the
 *   page and give it back (markBackInPage(entry, true) and (entry, false)).
 *
 * The phone's page (mobile-*.js) keeps no write queue: its sheet sends each change with keepalive and says it in a toast.
 * photo-write.js defines both re-reads and calls the `backInPage` it is given.
 */
import { readdirSync, readFileSync } from "node:fs";
import { test, describe } from "node:test";
import assert from "node:assert/strict";

const WEB = new URL("../../web/", import.meta.url);
const OWNER = "tagpup/write-queue.js";
const STATES = "waiting|writing|done|failed";

/** A module's code without its comments: a sentence naming a rule is no breach of it. */
const code = (source) => source.split("\n").map((line) => line.replace(/\/\/.*$/, ""))
  .filter((line) => !/^\s*\/?\*/.test(line)).join("\n");

/** Every module of the pages, {"tagpup/pending.js": its code}. */
function modules() {
  const found = {};
  for (const dir of ["tagpup", "common", "tuner"]) {
    const folder = new URL(`${dir}/`, WEB);
    for (const name of readdirSync(folder).filter((each) => each.endsWith(".js"))) {
      found[`${dir}/${name}`] = code(readFileSync(new URL(name, folder), "utf-8"));
    }
  }
  return found;
}

/** TagPup's page's own modules: the phone's keep no write queue. */
const pageModules = () => Object.entries(modules())
  .filter(([name]) => name.startsWith("tagpup/") && !name.startsWith("tagpup/mobile-"));

/** Each line of `text` matching `pattern`, as "module:line  text". */
function hits(name, text, pattern) {
  return text.split("\n").flatMap((line, at) => (pattern.test(line) ? [`${name}:${at + 1}  ${line.trim()}`] : []));
}

describe("whether a write is sent or still in this page has one writer", () => {
  test("only write-queue.js marks an entry's state or puts it back in the page", () => {
    const sets = new RegExp(`\\.backInPage\\s*=(?!=)|\\bbackInPage\\s*:\\s*(?:true|false)\\b|\\.status\\s*=\\s*['"](?:${STATES})['"]`);
    const found = Object.entries(modules()).filter(([name]) => name !== OWNER)
      .flatMap(([name, text]) => hits(name, text, sets));
    assert.deepEqual(found, [], "mark it through write-queue.js (markEntry, markBackInPage)");
  });

  test("every save sent again after a refusal is given onThisPage(entry), which marks it back in the page", () => {
    const found = pageModules().flatMap(([name, text]) => hits(name, text, /\bpostOrSendAgain\(/)
      .filter((line) => !/onThisPage\(\s*\w+\s*\)/.test(line)));
    assert.deepEqual(found, [], "pass onThisPage(entry) (edits.js) to postOrSendAgain");
  });

  test("every read again of moved photos marks its entry back in the page and gives it back", () => {
    const found = pageModules().filter(([, text]) => /\breadMoved\(/.test(text))
      .filter(([, text]) => !(/\bmarkBackInPage\(\s*\w+\s*,\s*true\s*\)/.test(text)
        && /\bmarkBackInPage\(\s*\w+\s*,\s*false\s*\)/.test(text)))
      .map(([name]) => name);
    assert.deepEqual(found, [], "around readMoved: markBackInPage(entry, true), then (entry, false) in a finally");
    assert.ok(pageModules().some(([, text]) => /\breadMoved\(/.test(text)), "no module reads moved photos again: the rule is stale");
  });
});
