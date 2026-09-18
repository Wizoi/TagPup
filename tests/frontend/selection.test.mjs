/**
 * Thumbnail grid selection in the TagPup workspace.
 *
 * Selection drives every bulk operation -- bulk tagging, sequential renaming, folder
 * auto-apply -- so an off-by-one in the shift-click range silently applies a write to
 * the wrong photos. The logic had no coverage.
 */
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click } from "./harness.mjs";

const FILES = ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"];

async function loadGrid(t) {
  const server = new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", FILES.map((filename) => photoRecord({ filename })));

  const ctx = await loadApp("tagpup", { t,
    url: "http://localhost:8090/photo_index/",
    server,
  });

  ctx.document.getElementById("folder-path-input").value = "D:\\Library\\2020";
  ctx.document.getElementById("btn-scan-folder").click();
  await flush(ctx.window, 6);

  ctx.cards = () => [...ctx.document.querySelectorAll(".thumbnail-card")];
  ctx.checkboxes = () =>
    ctx.cards().map((card) => card.querySelector(".thumbnail-checkbox"));
  ctx.selectedFilenames = () =>
    ctx
      .cards()
      .filter((card) => card.classList.contains("selected"))
      .map((card) => card.getAttribute("data-path").split("\\").pop());
  ctx.selectedCount = () =>
    ctx.document.getElementById("selected-thumbnails-count").textContent;

  return ctx;
}

/** Click a checkbox the way a user does.
 *
 * Dispatching a click runs the checkbox's native activation behaviour, so `checked`
 * is toggled for us before the app's listener reads it -- exactly as in a browser.
 */
function clickCheckbox(window, checkbox, { shiftKey = false } = {}) {
  click(window, checkbox, { shiftKey });
}

describe("thumbnail grid selection", () => {
  test("the grid renders one card per photo", async (t) => {
    const ctx = await loadGrid(t);
    assert.equal(ctx.cards().length, FILES.length);
  });

  test("clicking a checkbox selects that photo only", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[1]);
    assert.deepEqual(ctx.selectedFilenames(), ["b.jpg"]);
  });

  test("clicking again deselects it", async (t) => {
    const ctx = await loadGrid(t);
    const box = ctx.checkboxes()[1];
    clickCheckbox(ctx.window, box);
    clickCheckbox(ctx.window, box);
    assert.deepEqual(ctx.selectedFilenames(), []);
  });

  test("the selection count is reported to the user", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[0]);
    clickCheckbox(ctx.window, ctx.checkboxes()[2]);
    assert.match(ctx.selectedCount(), /2/);
  });
});

describe("shift-click range selection", () => {
  test("selects the contiguous range between two clicks", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[1]);                    // b
    clickCheckbox(ctx.window, ctx.checkboxes()[3], { shiftKey: true }); // ...through d
    assert.deepEqual(ctx.selectedFilenames(), ["b.jpg", "c.jpg", "d.jpg"]);
  });

  test("the range is inclusive at both ends", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[0]);
    clickCheckbox(ctx.window, ctx.checkboxes()[4], { shiftKey: true });
    assert.deepEqual(ctx.selectedFilenames(), FILES);
  });

  test("works backwards as well as forwards", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[3]);                    // d
    clickCheckbox(ctx.window, ctx.checkboxes()[1], { shiftKey: true }); // back to b
    assert.deepEqual(ctx.selectedFilenames(), ["b.jpg", "c.jpg", "d.jpg"]);
  });

  test("a range of one is just that photo", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[2]);
    assert.deepEqual(ctx.selectedFilenames(), ["c.jpg"]);
  });

  test("shift-clicking with nothing selected behaves like a plain click", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[2], { shiftKey: true });
    assert.deepEqual(ctx.selectedFilenames(), ["c.jpg"]);
  });

  test("shift-unchecking clears the whole range", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[0]);
    clickCheckbox(ctx.window, ctx.checkboxes()[4], { shiftKey: true });
    assert.equal(ctx.selectedFilenames().length, 5);

    clickCheckbox(ctx.window, ctx.checkboxes()[1]);                     // uncheck b
    clickCheckbox(ctx.window, ctx.checkboxes()[3], { shiftKey: true }); // ...through d
    assert.deepEqual(ctx.selectedFilenames(), ["a.jpg", "e.jpg"]);
  });

  test("checkbox state stays in step with the highlight", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[1]);
    clickCheckbox(ctx.window, ctx.checkboxes()[3], { shiftKey: true });

    ctx.cards().forEach((card, i) => {
      const checked = ctx.checkboxes()[i].checked;
      const highlighted = card.classList.contains("selected");
      assert.equal(
        checked,
        highlighted,
        `card ${i} checkbox and highlight disagree (${checked} vs ${highlighted})`
      );
    });
  });

  test("a second range extends from the most recent click", async (t) => {
    const ctx = await loadGrid(t);
    clickCheckbox(ctx.window, ctx.checkboxes()[0]);
    clickCheckbox(ctx.window, ctx.checkboxes()[1], { shiftKey: true }); // a-b
    clickCheckbox(ctx.window, ctx.checkboxes()[3], { shiftKey: true }); // b-d
    assert.deepEqual(ctx.selectedFilenames(), ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]);
  });
});

describe("select all and none", () => {
  test("select all takes every photo", async (t) => {
    const ctx = await loadGrid(t);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    assert.deepEqual(ctx.selectedFilenames(), FILES);
  });

  test("select none clears the selection", async (t) => {
    const ctx = await loadGrid(t);
    ctx.document.getElementById("btn-select-all-thumbnails").click();
    ctx.document.getElementById("btn-select-none-thumbnails").click();
    assert.deepEqual(ctx.selectedFilenames(), []);
  });
});
