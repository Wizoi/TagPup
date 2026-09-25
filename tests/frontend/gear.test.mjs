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

  test("opens on a click, onto its items: the tag editor, the settings, the history and TagTuner", async (t) => {
    const { window, document } = await tagpup(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    assert.ok(!menu.classList.contains("hidden"));
    assert.equal(button.getAttribute("aria-expanded"), "true");
    assert.deepEqual(items(menu).map((i) => i.textContent.trim()), ["Tag editor", "Settings", "History...", "Open in TagTuner"]);
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
    // On past the last item, however many the gear holds.
    for (let i = 1; i < items(menu).length; i++) key(window, document.activeElement, "ArrowDown");
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
  test("holds the tag editor, the library's settings, its history, and TagPup", async (t) => {
    const { window, document } = await tuner(t);
    const { button, menu } = gearOf(document);
    assert.ok(button.closest(".app-header"), "the gear is not in the header");
    click(window, button);
    await flush(window);
    assert.deepEqual(items(menu).map((i) => i.textContent.trim()), ["Tag editor", "Library settings", "History...", "Open in TagPup"]);
    const settings = menu.querySelector('[data-action="library-settings"]');
    assert.equal(settings.getAttribute("aria-disabled"), null, "Library settings is still disabled");
    click(window, settings);
    await flush(window, 4);
    assert.ok(menu.classList.contains("hidden"), "the menu stayed open over the settings");
    const dialog = document.getElementById("settings-modal");
    assert.ok(dialog && !dialog.classList.contains("hidden"), "Library settings opened nothing");
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

describe("The keys stay with what is in front of the page", () => {
  // TagPup steps its photos on all four arrows (web/tagpup/navigation.js); TagTuner moves
  // its sidebar on ArrowUp and ArrowDown (web/tuner/sidebar.js). Both listen on the
  // document, so a key the gear or the editor lets go of moves what is behind them.
  async function onAPhoto(t) {
    const server = new FakeServer()
      .on("/api/folder/scan", [photoRecord({ filename: "a.jpg" }), photoRecord({ filename: "b.jpg" })])
      .on("/api/photo-details", { path: "D:/Library/2020/a.jpg", tags: [], people: [] });
    const ctx = await tagpup(t, server);
    await openFolder(ctx, "D:/Library/2020", { settle: 6 });
    const rows = () => [...ctx.document.querySelectorAll(".photo-item-file")];
    assert.equal(rows().length, 2);
    click(ctx.window, rows()[0]);
    await flush(ctx.window, 4);
    ctx.shown = () => ctx.document.querySelector(".photo-item-file.active")?.getAttribute("data-path");
    ctx.first = ctx.shown();
    assert.ok(ctx.first, "no photo is shown");
    return ctx;
  }

  test("the open menu keeps every arrow key, Home and End to itself", async (t) => {
    const { window, document, shown, first } = await onAPhoto(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    await flush(window);
    for (const name of ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"]) {
      const event = key(window, document.activeElement, name);
      await flush(window, 2);
      assert.ok(event.defaultPrevented, `${name} was not kept`);
      assert.equal(shown(), first, `${name} stepped the photo behind the menu`);
      assert.ok(!menu.classList.contains("hidden"), `${name} closed the menu`);
    }
  });

  test("a closed gear opens on click, Enter and Space -- a button's -- not on the arrows", async (t) => {
    const { window, document } = await onAPhoto(t);
    const { button, menu } = gearOf(document);
    assert.equal(button.tagName, "BUTTON", "Enter and Space press only a button");
    button.focus();
    for (const name of ["ArrowDown", "ArrowUp"]) {
      key(window, button, name);
      assert.ok(menu.classList.contains("hidden"), `${name} opened the menu`);
    }
  });

  test("after the editor closes on the gear, the arrows step the photos as before", async (t) => {
    const { window, document, shown, first } = await onAPhoto(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    click(window, menu.querySelector('[data-action="tag-editor"]'));
    await flush(window, 2);
    key(window, document.activeElement, "Escape");
    await flush(window, 2);
    assert.equal(document.activeElement, button, "the focus did not come back to the gear");

    key(window, button, "ArrowDown");
    await flush(window, 4);
    assert.ok(menu.classList.contains("hidden"), "ArrowDown opened the menu");
    assert.notEqual(shown(), first, "ArrowDown did not step the photo");
    key(window, document.activeElement, "ArrowUp");
    await flush(window, 4);
    assert.equal(shown(), first, "ArrowUp did not step back");
  });

  test("TagPup's open editor keeps the page's keys, wherever its focus is", async (t) => {
    const { window, document, shown, first } = await onAPhoto(t);
    const { button, menu } = gearOf(document);
    click(window, button);
    click(window, menu.querySelector('[data-action="tag-editor"]'));
    await flush(window, 2);
    const editor = document.getElementById("taxonomy-modal");
    const treeButton = editor.querySelector(".taxonomy-node-actions button");
    for (const target of [treeButton, document.body]) {
      for (const name of ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End", "Enter", " "]) {
        if (target !== document.body) target.focus();
        key(window, target, name);
        await flush(window, 2);
        assert.equal(shown(), first, `${JSON.stringify(name)} on ${target.tagName} stepped the photo behind the editor`);
        assert.ok(editor.classList.contains("active"), `${JSON.stringify(name)} closed the editor`);
      }
    }
    key(window, treeButton, "Escape");
    assert.ok(!editor.classList.contains("active"), "Escape no longer closes it");
    key(window, document.activeElement, "ArrowDown");
    await flush(window, 4);
    assert.notEqual(shown(), first, "the page's keys stayed kept after the editor closed");
  });

  test("TagTuner's open editor keeps its sidebar's keys from the page", async (t) => {
    // The sidebar asks web/common/dialog.js whether a dialog is open before it moves.
    const server = new FakeServer().on("/api/photos", [
      { path: "D:/Library/2020/a.jpg", filename: "a.jpg", folder: "D:/Library/2020", year: "2020", unmatched_count: 1 },
      { path: "D:/Library/2020/b.jpg", filename: "b.jpg", folder: "D:/Library/2020", year: "2020", unmatched_count: 1 },
    ]);
    const { window, document } = await tuner(t, server);
    click(window, document.querySelector("#photo-list .folder-header"));   // the folder, opened
    const active = () => document.querySelector("#photo-list .photo-item.active")?.textContent || null;
    const before = active();
    click(window, gearOf(document).button);
    click(window, document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(window, 4);
    const treeButton = document.querySelector("#taxonomy-modal .taxonomy-node-actions button");
    for (const target of [treeButton, document.body]) {
      if (target !== document.body) target.focus();
      for (const name of ["ArrowUp", "ArrowDown"]) key(window, target, name);
    }
    await flush(window, 4);
    assert.equal(active(), before, "the sidebar moved behind the editor");
    key(window, treeButton, "Escape");
    key(window, document.body, "ArrowDown");
    await flush(window, 4);
    assert.notEqual(active(), before, "the sidebar does not move once the editor is closed");
  });
});

describe("The editor's removal check shows and sends the tags as they are", () => {
  const ODD = 'Places/The "Anchor" <b>Inn</b>';
  const TREE_WITH_ODD = [
    ...TREE,
    { id: 4, tag: "Places", name: "Places", parent_id: null, has_face: 0, hidden_from_autocomplete: 0, usage_count: 1 },
    { id: 5, tag: ODD, name: 'The "Anchor" <b>Inn</b>', parent_id: 4, has_face: 0, hidden_from_autocomplete: 0, usage_count: 1 },
  ];

  async function removalCheck(t, id) {
    const server = new FakeServer()
      .on("/api/taxonomy/tree", TREE_WITH_ODD)
      .on("/api/taxonomy/delete-check", { success: true, used: true, count: 1 })
      .on("/api/taxonomy/delete-confirm", { success: true, photos_affected: 1, photos_rewritten: 1 });
    const ctx = await tagpup(t, server);
    click(ctx.window, gearOf(ctx.document).button);
    click(ctx.window, ctx.document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(ctx.window);
    ctx.document.querySelectorAll("#taxonomy-modal .taxonomy-sublist").forEach((l) => l.classList.remove("hidden"));
    const del = ctx.document.querySelector(`#taxonomy-modal li[data-id="${id}"] > .taxonomy-node-content button[title^="Delete"]`);
    click(ctx.window, del);
    await flush(ctx.window, 4);
    const conflict = ctx.document.querySelector(".tag-editor-conflict");
    assert.ok(conflict, "no removal check was shown");
    return { ...ctx, conflict };
  }

  test("moving to a tag holding a quote sends that tag", async (t) => {
    const { window, server, conflict } = await removalCheck(t, 3);
    const select = conflict.querySelector("#move-target-select");
    const offered = [...select.options].map((o) => o.value).filter(Boolean);
    assert.ok(offered.includes(ODD), `the tag offered is not the tag: ${JSON.stringify(offered)}`);
    assert.equal(conflict.querySelector("b"), null, "a tag's text became markup");
    const move = conflict.querySelector('input[name="delete-opt"][value="move"]');
    move.checked = true;
    move.dispatchEvent(new window.Event("change", { bubbles: true }));
    select.value = ODD;
    click(window, conflict.querySelector(".btn-confirm"));
    await flush(window, 8);
    assert.deepEqual(server.lastBody("/api/taxonomy/delete-confirm"), { tag_id: 3, action: "move", target_tag: ODD });
  });

  test("the tag being removed is shown as text", async (t) => {
    const { conflict } = await removalCheck(t, 5);
    assert.equal(conflict.querySelector(".tag-editor-body strong").textContent, ODD);
    assert.equal(conflict.querySelector(".tag-editor-body b"), null, "the tag's text became markup");
  });
});
