/**
 * Smart Rename says how many photos it renamed.
 *
 * It cleared the selection and then counted it, so it always reported
 * "Renamed 0 photo(s)". The count is what the server says moved: the entries of
 * updated_paths whose new path differs from the old.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder } from "./harness.mjs";

test("a grouping holding ' - ' is refused beside the field, and nothing is sent", async (t) => {
  // Editing a caption later splits the name on " - " to find the photo's number.
  const records = ["a.jpg", "b.jpg"].map((filename) => photoRecord({ filename }));
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", records);

  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.confirm = () => true;
  await openFolder(ctx, "D:\\Library\\2020", { settle: 6 });
  click(ctx.window, ctx.document.querySelector(".thumbnail-card .thumbnail-checkbox"));

  const field = ctx.document.getElementById("rename-grouping-input");
  field.value = "2019-06 - Summer Camp";
  const apply = ctx.document.getElementById("btn-apply-rename");
  apply.disabled = false;
  click(ctx.window, apply);
  await flush(ctx.window, 4);

  assert.ok(!server.calls.some((c) => c.url.includes("rename-photos")), "the rename was sent");
  assert.ok(field.classList.contains("field-invalid"));
  assert.match(ctx.document.getElementById("status-text").textContent, /cannot contain " - "/);
});

test("Smart Rename reports the photos it renamed, not zero", async (t) => {
  const files = ["a.jpg", "b.jpg", "c.jpg"];
  const records = files.map((filename) => photoRecord({ filename }));
  const [a, b] = records.map((r) => r.path);
  const renamedA = a.replace("a.jpg", "Harbour Day 001.jpg");
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", records)
    .on("/api/folder/rename-photos", {
      success: true,
      // b already had the name it would have been given.
      updated_paths: { [a]: renamedA, [b]: b },
      updated_photos: [{ ...records[0], path: renamedA, filename: "Harbour Day 001.jpg" },
        records[1], records[2]],
    });

  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.confirm = () => true;
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:\\Library\\2020", { settle: 6 });

  const boxes = [...ctx.document.querySelectorAll(".thumbnail-card .thumbnail-checkbox")];
  click(ctx.window, boxes[0]);
  click(ctx.window, boxes[1]);

  ctx.document.getElementById("rename-grouping-input").value = "Harbour Day";
  const apply = ctx.document.getElementById("btn-apply-rename");
  apply.disabled = false;   // enabled by the panel's own toggle, which is not under test
  click(ctx.window, apply);
  await flush(ctx.window, 8);

  assert.ok(server.calls.some((c) => c.url.includes("rename-photos")), "no rename was sent");
  assert.equal(ctx.document.getElementById("status-text").textContent, "Renamed 1 photo(s)");
});
