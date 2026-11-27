/**
 * The library's index queue has one follower on each page (plan docs/plans/working-on-a-new-folder.md, cut F's review):
 * on TagPup's, web/tagpup/suggestions.js (checkIndexingStatus, askIndexes, heardIndexes) asks GET /api/folder/index-active
 * once a second while something is queued, and is the one place that reads again what the queue's moves change -- the
 * Library pane's Folders tab marked out of date when what the queue holds changes, and at most every NAV_QUEUED_MS while a
 * folder is being indexed. Cut F had a second: navigator.js read the Folders tab again on a 5 s timer of its own while it
 * marked a folder, a second clock for one rule. On TagTuner's page the follower is web/tuner/indexing.js. The phone's page
 * follows no index.
 *
 * Fails when a second module asks the queue, when navigator.js sets a timer other than its two pauses (the counts' after
 * an edit, the filter's), or when the Folders tab is read again on a timer while the queue does not answer.
 */
import { readdirSync, readFileSync } from "node:fs";
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, testClock } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

const WEB = new URL("../../web/", import.meta.url);
const ASKS_THE_QUEUE = /api\/folder\/index-active/;
/** A module's code without its comments: a comment naming the route is no ask. */
const code = (source) => source.split("\n").filter((line) => !/^\s*(\/\/|\/?\*)/.test(line)).join("\n");

/** The modules under web/<dir> whose source asks the index queue. */
function askers(dir) {
  const folder = new URL(`${dir}/`, WEB);
  return readdirSync(folder).filter((name) => name.endsWith(".js"))
    .filter((name) => ASKS_THE_QUEUE.test(code(readFileSync(new URL(name, folder), "utf-8"))))
    .map((name) => `${dir}/${name}`);
}

describe("the index queue has one follower on each page", () => {
  test("TagPup's page asks the queue from suggestions.js alone, TagTuner's from its indexing.js alone", () => {
    assert.deepEqual([...askers("tagpup"), ...askers("common")], ["tagpup/suggestions.js"]);
    assert.deepEqual(askers("tuner"), ["tuner/indexing.js"]);
  });

  test("navigator.js keeps no clock of its own: its timers are the counts' pause and the filter's", () => {
    const source = readFileSync(new URL("tagpup/navigator.js", WEB), "utf-8");
    const timers = [...source.matchAll(/([\w.]+)\s*=\s*window\.setTimeout\(/g)].map((match) => match[1]);
    assert.deepEqual(timers.sort(), ["nav.timer", "sec.filterTimer"]);
    assert.equal((source.match(/setTimeout\(/g) || []).length, timers.length, "a timer set without a name");
    assert.doesNotMatch(source, /setInterval\(/);
  });
});

describe("a queue that does not answer", () => {
  beforeEach((t) => testClock(t));

  afterEach(() => {
    closeAllApps();
    assert.deepEqual(pageErrors(), [], "the page raised an error");
  });

  test("the Folders tab marking a folder indexing is not read again on a timer: the one follower has stopped asking", async (t) => {
    const folders = { folders: [
      { path: "D:\\Pictures", name: "Pictures", parent: null, direct: 2, recursive: 3, queue: null },
      { path: "D:\\Pictures\\Relay Day", name: "Relay Day", parent: "D:\\Pictures", direct: 1, recursive: 1, queue: "indexing" },
    ] };
    const ctx = await loadViewPage(t, { search: "?view=all", navigator: { folders }, queue: () => ({ error: "Internal Server Error" }) });
    ctx.server.first("/api/folder/index-active", { error: "Internal Server Error" }, { status: 500 });
    await ctx.settle(10000);   // the follower's asks fail, and it gives up
    const read = ctx.navigatorAsked.filter((name) => name === "folders").length;
    assert.ok(read >= 1, "fixture: the Folders tab was not read");
    await ctx.settle(60000);
    assert.equal(ctx.navigatorAsked.filter((name) => name === "folders").length, read, "the Folders tab follows the queue on a clock of its own");
  });
});
