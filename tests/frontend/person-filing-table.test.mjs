/**
 * A click on a suggested person's chip files them by one table, the same the server's Apply All is fed
 * (tests/fixtures/person_filing.json, tests/test_person_filing_table.py): where the tree files the person,
 * under the one people root, People when there is none, the page asks when there are several, and a file that
 * already names them by their leaf is written nothing (#555). The page's rule is resolveTagOrPerson; the
 * server's, tagpup.core.vocabulary.person_tag, mirrors it.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const TABLE = JSON.parse(readFileSync(new URL("../fixtures/person_filing.json", import.meta.url), "utf-8"));

/** The tree as the page is sent it: a node for each root and path, and for the ancestors of a path. */
function nodes(roots, paths) {
  const found = [];
  const add = (tag, parentId, hasFace) => {
    let node = found.find((n) => n.tag === tag);
    if (!node) {
      node = { id: found.length + 1, tag, name: tag.split("/").pop(), parent_id: parentId, has_face: hasFace };
      found.push(node);
    }
    return node;
  };
  for (const [name, hasFace] of roots) add(name, null, hasFace);
  for (const path of paths) {
    const parts = path.split("/");
    const root = found.find((n) => n.tag === parts[0]);
    add(path, root.id, root.has_face);
  }
  return found;
}

for (const found of TABLE.cases) {
  test(`${found.why}: ${found.expect}`, async (t) => {
    const photo = photoRecord({ filename: "wren.jpg", tags: found.file, people: [] });
    const server = new FakeServer()
      .on("/api/tags", [])
      .on("/api/people", found.known)
      .on("/api/taxonomy/tree", nodes(found.roots, found.paths))
      .on("/api/taxonomy/create", { success: true })
      .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
      .on("/api/folder/index-status", { status: "completed", percent: 100 })
      .on("/api/folder/scan", [photo])
      .on("/api/folder/membership", { library: "photo_index", folder: "D:/Library/2020", photos: 1, photos_held: 1,
        photos_not_held: 0, folders_not_held: 0, first_not_held: null, has_roots: true, under_roots: true, ignored: false })
      .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
      .on("/api/photo/save-metadata", { success: true })
      .on("/api/folder/suggest-status", {
        status: "completed", suggestions: { [photo.path]: { tags: [], people: [{ name: TABLE.name, score: 0.9 }], title: null } },
      });
    const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
    await openFolder(ctx, "D:/Library/2020");
    const row = ctx.document.querySelector("li[data-path]");
    if (row) row.click();
    await flush(ctx.window, 8);
    const chip = ctx.document.querySelector("#suggested-people-container .suggestion-chip");
    const saves = () => server.calls.filter((c) => c.method === "POST" && c.url.includes("/api/photo/save-metadata"));
    if (found.expect === "already") {
      assert.equal(chip, null, "the person is offered though the file names them");
      return;
    }
    assert.ok(chip, "the person is not offered");
    click(ctx.window, chip);
    await flush(ctx.window, 10);
    if (found.expect === "ask") {
      assert.equal(saves().length, 0, "written without asking which people folder");
      assert.ok(ctx.document.querySelector(".modal-overlay.active"), "the page did not ask");
      return;
    }
    assert.equal(saves().length, 1, "nothing was written");
    assert.deepEqual(saves()[0].body.tags, [...found.file, found.expect]);
  });
}
