/**
 * Organize: stepping to the next or previous photo keeps its card clear of the sticky header (#3185).
 *
 * stepPhoto used to scroll the sidebar row with scrollIntoView, which knows nothing of the header floating over the grid,
 * so ArrowUp brought the card under it. The grid's own scrollToIndex subtracts the header's height. The index is the
 * card's place in the grid (state.shownIndex), not the row's place in a list. Fictional names only.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click, pageExports } from "./harness.mjs";

const photo = (n) => ({ path: `D:\\Pictures\\Run 2026\\Meet - 0${n}.jpg`, filename: `Meet - 0${n}.jpg`,
                        tags: [], people: [], captions: [], title: "" });

function server() {
  return new FakeServer()
    .on("/api/folder/scan", [photo(1), photo(2), photo(3)])
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

afterEach(() => closeAllApps());

test("stepping to another photo scrolls the grid to its card through the grid, not the row", async (t) => {
  const { window, document } = await loadApp("tagpup", { server: server(), t });
  await openFolder({ document, window }, "D:\\Pictures\\Run 2026");
  await settle(window, 60);
  const { state } = pageExports(window, "web/tagpup/state.js");
  const { stepPhoto } = pageExports(window, "web/tagpup/navigation.js");
  click(window, document.querySelector(".photo-item-file"));
  await settle(window);

  const calls = [];
  const real = state.grid.scrollToIndex;
  state.grid.scrollToIndex = (index, align) => { calls.push([index, align]); return real(index, align); };

  assert.equal(stepPhoto(1), true);
  assert.equal(stepPhoto(1), true);
  assert.equal(stepPhoto(-1), true);
  assert.deepEqual(calls, [[1, "nearest"], [2, "nearest"], [1, "nearest"]]);
});
