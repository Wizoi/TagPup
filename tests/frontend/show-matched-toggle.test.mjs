/**
 * Folder-match's Show matched toggle. It was sent to the server and never read, so a
 * photo whose faces were all named could not be stepped through in its folder: the
 * list went from 120 to 122. With the toggle on, such a photo is listed, marked done.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\Meet\\120.jpg", filename: "120.jpg", unmatched_count: 2, matched_count: 0,
    mtime: 3, year: "2026", folder: "D:\\Meet" },
  { path: "D:\\Meet\\121.jpg", filename: "121.jpg", unmatched_count: 0, matched_count: 1,
    mtime: 2, year: "2026", folder: "D:\\Meet" },
];

function server() {
  return new FakeServer()
    .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", PHOTOS);
}

test("with Show matched on, the page asks for finished photos and marks them done", async (t) => {
  const ctx = await loadApp("tagtuner", {
    t, url: "http://localhost:8080/kr-track/?mode=folder-match&show_matched=true", server: server(),
  });
  await flush();
  assert.ok(ctx.server.urls().some((u) => u.includes("/api/photos") && u.includes("show_matched=true")),
    "the list was asked for with show_matched=true");
  const items = [...ctx.document.querySelectorAll(".folder-photo-item")];
  assert.equal(items.length, 2);
  const done = items.filter((li) => li.classList.contains("all-matched"));
  assert.equal(done.length, 1, "the photo with every face named is marked done");
  assert.equal(done[0].photo.filename, "121.jpg");
});
