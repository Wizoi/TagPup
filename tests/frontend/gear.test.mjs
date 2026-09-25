/**
 * The gear at the top of each page, and the tag editor both open from it
 * (docs/ARCHITECTURE.md, phase 7.6).
 *
 * TagPup's Manage Tags dialog was TagPup's alone -- its module and its markup -- and
 * TagTuner could not edit the tree at all. Now the editor is web/common/tag-editor.js,
 * which builds its own markup, and each page opens it from its gear, beside a link to
 * the other app on the same library. The link's address is the server's (/api/apps):
 * a page that spelled a port would point at the wrong one the day the launcher was
 * told another.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT, APPS, loadApp, FakeServer, photoRecord, flush, openFolder, click, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const LIBRARY = "kr-track";
const APP_URLS = {
  this: "tagpup",
  apps: { tagpup: `http://localhost:8090/${LIBRARY}/`, tuner: `http://localhost:8080/${LIBRARY}/` },
};

const TREE = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1, hidden_from_autocomplete: 0, usage_count: 3 },
  { id: 2, tag: "People/Hazel Brookmire", name: "Hazel Brookmire", parent_id: 1, has_face: 1, hidden_from_autocomplete: 0, usage_count: 2 },
  { id: 3, tag: "Activity", name: "Activity", parent_id: null, has_face: 0, hidden_from_autocomplete: 0, usage_count: 1 },
];

function key(window, target, name) {
  const event = new window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
}

function gearOf(document) {
  return { button: document.getElementById("btn-gear"), menu: document.getElementById("gear-menu") };
}

function items(menu) {
  return [...menu.querySelectorAll('[role="menuitem"]')];
}

async function tagpup(t, server = new FakeServer()) {
  server
    .on("/api/apps", APP_URLS)
    .on("/api/taxonomy/tree", TREE)
    .on("/api/tags", ["Activity"])
    .on("/api/people", ["Hazel Brookmire"])
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg", tags: ["Activity"] })])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 });
  const ctx = await loadApp("tagpup", { t, url: `http://localhost:8090/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  return ctx;
}

async function tuner(t, server = new FakeServer()) {
  server
    .on("/api/apps", { ...APP_URLS, this: "tuner" })
    .on("/api/taxonomy/tree", TREE)
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/people-with-counts", [])
    .on("/api/people", ["Hazel Brookmire"])
    .on("/api/photos", []);
  const ctx = await loadApp("tagtuner", { t, url: `http://localhost:8080/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  return ctx;
}

describe("TagPup's gear", () => {
  test("replaces the Manage Tags button", async (t) => {
    const { document } = await tagpup(t);
    assert.equal(document.getElementById("btn-manage-taxonomy"), null);
    const { button, menu } = gearOf(document);
    assert.ok(button && menu, "no gear in the top bar");
    assert.ok(button.closest(".app-header"), "the gear is not in the top bar");
    assert.ok(menu.classList.contains("hidden"), "the menu starts open");
  });

  test("opens on a click, onto its items: the tag editor and TagTuner", async (t) => {
    const { window, document } = await tagpup(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    assert.ok(!menu.classList.contains("hidden"));
    assert.equal(button.getAttribute("aria-expanded"), "true");
    assert.deepEqual(items(menu).map((i) => i.textContent.trim()), ["Tag editor", "Open in TagTuner"]);
    assert.equal(document.activeElement, items(menu)[0], "the focus is not on the first item");
  });

  test("its TagTuner link is the server's address for this same library", async (t) => {
    const { window, document, server } = await tagpup(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    const asked = server.urls().filter((u) => u.includes("/api/apps"));
    assert.deepEqual(asked, [`/${LIBRARY}/api/apps`], "the page did not ask about its own library");
    const link = menu.querySelector("a[data-app]");
    assert.equal(link.getAttribute("href"), `http://localhost:8080/${LIBRARY}/`);
    assert.equal(link.getAttribute("aria-disabled"), null);
    assert.equal(link.getAttribute("target"), "_blank");
  });

  test("the page spells no port for the link", () => {
    for (const file of ["web/common/gear.js", "web/tagpup/gear.js", "web/tuner/gear.js",
                        "web/tagpup/index.html", "web/tuner/index.html"]) {
      const text = fs.readFileSync(path.join(REPO_ROOT, file), "utf8");
      assert.ok(!/80[89]0/.test(text), `${file} spells a port`);
    }
  });

  test("Escape closes it and gives the focus back to the gear", async (t) => {
    const { window, document } = await tagpup(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    key(window, document.activeElement, "Escape");
    assert.ok(menu.classList.contains("hidden"));
    assert.equal(button.getAttribute("aria-expanded"), "false");
    assert.equal(document.activeElement, button);
  });

  test("the arrow keys move through it and do not step the photos", async (t) => {
    const { window, document } = await tagpup(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    const event = key(window, document.activeElement, "ArrowDown");
    assert.equal(document.activeElement, items(menu)[1]);
    assert.ok(event.defaultPrevented);
    key(window, document.activeElement, "ArrowDown");
    assert.equal(document.activeElement, items(menu)[0], "the focus does not wrap round");
  });

  test("a click anywhere else closes it", async (t) => {
    const { window, document } = await tagpup(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    document.body.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true }));
    assert.ok(menu.classList.contains("hidden"));
  });

  test("Tag editor opens the editor over the tree the page holds, and Escape closes it", async (t) => {
    const { window, document, server } = await tagpup(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    const treeReads = server.urls().filter((u) => u.includes("/api/taxonomy/tree")).length;
    click(window, menu.querySelector('[data-action="tag-editor"]'));
    await flush(window);

    const editor = document.getElementById("taxonomy-modal");
    assert.ok(editor.classList.contains("active"), "the editor did not open");
    assert.ok(menu.classList.contains("hidden"), "the menu stayed open under it");
    const names = [...editor.querySelectorAll(".taxonomy-node-name")].map((n) => n.textContent);
    assert.deepEqual(names, ["People", "Hazel Brookmire", "Activity"]);
    assert.equal(server.urls().filter((u) => u.includes("/api/taxonomy/tree")).length, treeReads,
      "TagPup's editor shows the tree the page already read, as Manage Tags did");

    key(window, document.activeElement, "Escape");
    assert.ok(!editor.classList.contains("active"));
    assert.equal(document.activeElement, button, "the focus did not come back to the gear");
  });
});

describe("TagPup's tag editor, as Manage Tags was", () => {
  async function openEditor(t, server) {
    const ctx = await tagpup(t, server);
    await openFolder(ctx, "D:/Library/2020", { settle: 6 });
    ctx.alerts = [];
    ctx.window.alert = (message) => ctx.alerts.push(message);
    click(ctx.window, gearOf(ctx.document).button);
    click(ctx.window, ctx.document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(ctx.window);
    return ctx;
  }

  const nodeButton = (document, id, text) =>
    [...document.querySelectorAll(`#taxonomy-modal li[data-id="${id}"] > .taxonomy-node-content button`)]
      .find((b) => b.textContent.includes(text));

  test("a rename is sent, the tree read again, and the open folder scanned again", async (t) => {
    const server = new FakeServer().on("/api/taxonomy/rename", { success: true, photos_affected: 1, photos_rewritten: 1 });
    const ctx = await openEditor(t, server);
    ctx.window.prompt = () => "Pastimes";
    const scans = () => ctx.server.urls().filter((u) => u.includes("/api/folder/scan")).length;
    const trees = () => ctx.server.urls().filter((u) => u.includes("/api/taxonomy/tree")).length;
    const [scansBefore, treesBefore] = [scans(), trees()];

    click(ctx.window, nodeButton(ctx.document, 3, "Rename"));
    await flush(ctx.window, 8);

    assert.deepEqual(ctx.server.lastBody("/api/taxonomy/rename"), { tag_id: 3, new_name: "Pastimes" });
    assert.ok(trees() > treesBefore, "the tree was not read again");
    assert.ok(scans() > scansBefore, "the folder was not scanned again");
    assert.equal(ctx.document.getElementById("status-text").textContent, "Ready");
    assert.deepEqual(ctx.alerts, []);
  });

  test("a delete of a tag in use asks what to do with its photos", async (t) => {
    const server = new FakeServer()
      .on("/api/taxonomy/delete-check", { success: true, used: true, count: 1 })
      .on("/api/taxonomy/delete-confirm", { success: true, photos_affected: 1, photos_rewritten: 1 });
    const ctx = await openEditor(t, server);

    click(ctx.window, nodeButton(ctx.document, 3, "\u{1F5D1}"));
    await flush(ctx.window, 4);
    const conflict = ctx.document.querySelector(".tag-editor-conflict");
    assert.ok(conflict, "no removal check was shown");
    const options = [...conflict.querySelectorAll("#move-target-select option")].map((o) => o.value).filter(Boolean);
    assert.deepEqual(options, ["People", "People/Hazel Brookmire"]);
    click(ctx.window, conflict.querySelector(".btn-confirm"));
    await flush(ctx.window, 8);

    assert.deepEqual(ctx.server.lastBody("/api/taxonomy/delete-confirm"), { tag_id: 3, action: "remove" });
  });

  test("a root's Face Matching switch updates it", async (t) => {
    const server = new FakeServer().on("/api/taxonomy/update", { success: true });
    const ctx = await openEditor(t, server);
    const box = ctx.document.querySelector('#taxonomy-modal li[data-id="3"] .switch input');
    box.checked = true;
    box.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 6);
    assert.deepEqual(ctx.server.lastBody("/api/taxonomy/update"), { id: 3, has_face: 1 });
  });

  test("its markup is built by the shared module, not carried by either page", () => {
    for (const app of Object.values(APPS)) {
      const html = fs.readFileSync(path.join(REPO_ROOT, app.dir, "index.html"), "utf8");
      assert.ok(!/taxonomy-modal|taxonomy-tree-container/.test(html), `${app.dir}/index.html carries the editor's markup`);
      assert.match(html, /href="common\/tag-editor\.css"/, `${app.dir}/index.html does not link the editor's look`);
    }
    assert.ok(!fs.existsSync(path.join(REPO_ROOT, "web", "tagpup", "taxonomy.js")));
  });
});

describe("TagTuner's gear", () => {
  test("holds the tag editor, the library's settings (not yet), and TagPup", async (t) => {
    const { window, document } = await tuner(t);
    const { button, menu } = gearOf(document);
    assert.ok(button.closest(".app-header"), "the gear is not in the header");
    click(window, button);
    await flush(window);
    assert.deepEqual(items(menu).map((i) => i.textContent.trim()), ["Tag editor", "Library settings", "Open in TagPup"]);
    const settings = menu.querySelector('[data-action="library-settings"]');
    assert.equal(settings.getAttribute("aria-disabled"), "true");
    assert.match(settings.title, /next/i);
    click(window, settings);
    assert.ok(!menu.classList.contains("hidden"), "a disabled item closed the menu as if it did something");
  });

  test("its TagPup link is the server's address for this same library", async (t) => {
    const { window, document, server } = await tuner(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    assert.deepEqual(server.urls().filter((u) => u.includes("/api/apps")), [`/${LIBRARY}/api/apps`]);
    assert.equal(menu.querySelector('a[data-app="tagpup"]').getAttribute("href"), `http://localhost:8090/${LIBRARY}/`);
  });

  test("Escape closes it and gives the focus back to the gear", async (t) => {
    const { window, document } = await tuner(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    key(window, document.activeElement, "Escape");
    assert.ok(menu.classList.contains("hidden"));
    assert.equal(document.activeElement, button);
  });

  test("Tag editor opens the same editor, reading the tree from this library", async (t) => {
    const { window, document, server } = await tuner(t);
    click(window, gearOf(document).button);
    click(window, document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(window, 4);

    const editor = document.getElementById("taxonomy-modal");
    assert.ok(editor.classList.contains("active"), "the editor did not open");
    assert.ok(server.urls().includes(`/${LIBRARY}/api/taxonomy/tree`), "the tree was not read from this library");
    const names = [...editor.querySelectorAll(".taxonomy-node-name")].map((n) => n.textContent);
    assert.deepEqual(names, ["People", "Hazel Brookmire", "Activity"]);
  });

  test("an edit made there is sent to this library, and the tree read again", async (t) => {
    const server = new FakeServer().on("/api/taxonomy/create", { success: true, id: 4, tag: "Places" });
    const { window, document } = await tuner(t, server);
    click(window, gearOf(document).button);
    click(window, document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(window, 4);
    window.prompt = () => "Places";
    window.confirm = () => false;
    const trees = () => server.urls().filter((u) => u.includes("/api/taxonomy/tree")).length;
    const before = trees();

    click(window, document.getElementById("btn-taxonomy-add-root"));
    await flush(window, 6);

    const call = server.calls.find((c) => c.url.includes("/api/taxonomy/create"));
    assert.equal(call.url, `/${LIBRARY}/api/taxonomy/create`);
    assert.deepEqual(call.body, { name: "Places", parent_id: null, has_face: 0 });
    assert.ok(trees() > before, "the tree was not read again");
    assert.equal(document.querySelector(".tag-editor-status").textContent, "", "the editor did not finish");
  });
});
