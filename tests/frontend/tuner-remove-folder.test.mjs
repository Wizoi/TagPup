/**
 * TagTuner's Remove Folder offers the library's folders, not the disk's (#47).
 *
 * It chose its folder with the system folder dialog, which lists what is on disk. It
 * could not choose a folder that is gone -- the usual reason to remove one: a training
 * folder deleted from disk left its rows behind with no way to pick it -- and it offered
 * folders never indexed. Now it lists what the server says the library holds
 * (/api/folder/indexed), a folder gone from disk included and marked.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps } from "./harness.mjs";

const GONE = "D:\\Training\\Cross Country\\Old Meet";
const HERE = "D:\\Training\\Cross Country\\Meet 2";

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));
const rows = (document) => [...document.querySelectorAll("#remove-folder-list .folder-picker-row")];

function server() {
  return new FakeServer()
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/people-with-counts", [])
    .on("/api/photos", [])
    .on("/api/browse-folder", { path: HERE })
    .on("/api/folder/indexed", {
      folders: [
        { path: HERE, photos: 12, on_disk: true },
        { path: GONE, photos: 28, on_disk: false },
      ],
    })
    .on("/api/folder/remove", { success: true, photos_removed: 28, faces_removed: 40, manual_lost: 0, excluded_lost: 0 });
}

async function openRemove(t) {
  const ctx = await loadApp("tagtuner", { server: server(), t });
  ctx.window.confirm = () => true;
  ctx.window.alert = () => {};
  await settle(ctx.window, 20);
  ctx.document.getElementById("btn-remove-folder").click();
  await settle(ctx.window, 20);
  return ctx;
}

describe("Remove Folder", () => {
  afterEach(() => closeAllApps());

  test("asks the library for its folders, not the disk", async (t) => {
    const { server: s } = await openRemove(t);
    assert.ok(s.urls().some((u) => u.includes("/api/folder/indexed")), "the library was not asked");
    assert.ok(!s.urls().some((u) => u.includes("/api/browse-folder")), "the disk's dialog was opened");
  });

  test("lists every folder the library holds, one gone from disk marked", async (t) => {
    const { document } = await openRemove(t);
    const shown = rows(document);
    assert.equal(shown.length, 2);
    const gone = shown.find((r) => r.textContent.includes("Old Meet"));
    assert.ok(gone, "the folder gone from disk is not offered");
    assert.match(gone.textContent, /28 photo/);
    assert.match(gone.textContent, /not on disk/);
    assert.doesNotMatch(shown.find((r) => r.textContent.includes("Meet 2")).textContent, /not on disk/);
  });

  test("removes the folder chosen, as the library spells it", async (t) => {
    const { window, document, server: s } = await openRemove(t);
    const confirmButton = document.getElementById("btn-remove-folder-confirm");
    assert.equal(confirmButton.disabled, true, "Remove is offered before a folder is chosen");
    rows(document).find((r) => r.textContent.includes("Old Meet")).querySelector("input").click();
    assert.equal(confirmButton.disabled, false);
    confirmButton.click();
    await settle(window, 20);
    assert.deepEqual(s.lastBody("/api/folder/remove"), { folder_path: GONE });
    assert.ok(document.getElementById("remove-folder-modal").classList.contains("hidden"));
  });

  test("the filter narrows the list", async (t) => {
    const { window, document } = await openRemove(t);
    const filter = document.getElementById("remove-folder-filter");
    filter.value = "old";
    filter.dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.deepEqual(rows(document).map((r) => r.querySelector(".folder-picker-name").textContent), [GONE]);
  });

  test("declining the question removes nothing", async (t) => {
    const { window, document, server: s } = await openRemove(t);
    window.confirm = () => false;
    rows(document)[0].querySelector("input").click();
    document.getElementById("btn-remove-folder-confirm").click();
    await settle(window, 20);
    assert.ok(!s.urls().some((u) => u.includes("/api/folder/remove")));
  });
});
