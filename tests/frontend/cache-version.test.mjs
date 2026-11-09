/**
 * The shape of the photo records a folder's kept scan holds: the fixture tests/test_page_record_fields.py writes names the CACHE_VERSION
 * the server's record fields were made for, and it must be the one web/tagpup/cache.js carries, or a scan kept before a field was added
 * is shown as current.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./harness.mjs";

test("the page's CACHE_VERSION is the one the server's record fields were made for", () => {
  const made = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "tests", "frontend", "fixtures", "page_record.json"), "utf8"));
  const source = fs.readFileSync(path.join(REPO_ROOT, "web", "tagpup", "cache.js"), "utf8");
  const version = Number(/export const CACHE_VERSION = (\d+);/.exec(source)[1]);
  assert.equal(made.version, version,
    "bump CACHE_VERSION in web/tagpup/cache.js and regenerate the fixture (python tests/test_page_record_fields.py --write)");
});
