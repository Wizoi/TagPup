/**
 * The tag editor shows its tags in alphabetical order and changes them in place.
 *
 * It was emptied and built again from the server's list -- the order the nodes were made
 * in -- after every rename, new tag, flag and delete: the tree came back collapsed, the
 * scroll at the top, the focus gone, and nothing changed on screen until the server had
 * rewritten the photos and the tree had been read again (2026-10-03). Now every level is
 * alphabetical (case and accents ignored, digits as numbers), and an action patches the
 * rows it touches: a rename is shown at once, marked while the server works, and put back
 * if the server refuses.
 *
 * Rows are told apart by identity: a row that is the same element before and after was
 * not rebuilt.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const LIBRARY = "kr-track";

/** [id, name, parent id, flags]: the nodes in the order they were made, tags worked out from the chain. */
function treeOf(spec) {
  const made = new Map();
  return spec.map(([id, name, parent, extra = {}]) => {
    const tag = parent === null ? name : `${made.get(parent).tag}/${name}`;
    const node = {
      id, tag, name, parent_id: parent, has_face: parent === null ? 0 : made.get(parent).has_face,
      hidden_from_autocomplete: 0, usage_count: 1, ...extra,
    };
    made.set(id, node);
    return node;
  });
}

const BASE = () => treeOf([
  [1, "People", null, { has_face: 1 }],
  [2, "Hazel Brookmire", 1],
  [3, "Activity", null],
  [4, "Places", null],
  [5, "Avery Tolland", 1],
  [6, "Swimming", 3],
  [7, "Archery", 3],
  [8, "Harbor Town", 4, { usage_count: 0 }],
]);

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

/**
 * An app with the editor open over `world.tree`, which the tree route answers from and a test may
 * change. `app` is "tagpup" or "tagtuner". Alerts, notes and what scrolled into view are kept.
 */
async function openEditor(t, { tree = BASE(), app = "tagpup", routes = (server) => server } = {}) {
  const world = { tree };
  const server = new FakeServer();
  routes(server);
  server
    .on("/api/apps", { this: app === "tagpup" ? "tagpup" : "tuner", apps: {} })
    .on("/api/taxonomy/tree", () => JSON.parse(JSON.stringify(world.tree)))
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/people-with-counts", [])
    .on("/api/photos", [])
    .on("/api/folder/scan", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 });
  const port = app === "tagpup" ? 8090 : 8080;
  const ctx = await loadApp(app, { t, url: `http://localhost:${port}/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  const { window, document } = ctx;
  Object.assign(ctx, { world, alerts: [], answers: { prompt: null, confirm: true }, scrolled: [] });
  window.alert = (message) => ctx.alerts.push(message);
  window.prompt = () => ctx.answers.prompt;
  window.confirm = () => ctx.answers.confirm;
  window.Element.prototype.scrollIntoView = function () {
    const li = this.closest("li");
    ctx.scrolled.push(li ? li.getAttribute("data-id") : null);
  };
  ctx.open = async () => {
    click(window, document.getElementById("btn-gear"));
    click(window, document.querySelector('#gear-menu [data-action="tag-editor"]'));
    await flush(window, 4);
  };
  ctx.close = () => click(window, document.getElementById("btn-close-taxonomy"));
  await ctx.open();
  return ctx;
}

const rowOf = (document, id) => document.querySelector(`#taxonomy-modal li[data-id="${id}"]`);
const control = (document, id, name) => rowOf(document, id).firstElementChild.querySelector(`[data-control="${name}"]`);
const labelOf = (li) => li.firstElementChild.querySelector(".taxonomy-node-name").textContent;
const sublistOf = (li) => [...li.children].find((c) => c.tagName === "UL") || null;
const namesIn = (list) => (list ? [...list.children].map(labelOf) : []);
const rootNames = (document) => namesIn(document.querySelector("#taxonomy-tree-container > ul"));
const childNames = (document, id) => namesIn(sublistOf(rowOf(document, id)));
const marker = (document, id) => {
  const el = rowOf(document, id).firstElementChild.querySelector(".taxonomy-node-busy");
  return el.hidden ? "" : el.textContent;
};
const expandedOf = (document, id) => { const s = sublistOf(rowOf(document, id)); return Boolean(s) && !s.classList.contains("hidden"); };
const expand = (window, document, id) => click(window, rowOf(document, id).firstElementChild.querySelector(".taxonomy-node-expander"));
const notice = (document) => document.querySelector("#taxonomy-modal .tag-editor-notice").textContent;
const status = (document) => document.getElementById("status-text").textContent;

/** The rows an action changed, however it changed them: counted, to say what an action costs. */
function watchRows(window, container) {
  const rows = new Set();
  let records = 0;
  const observer = new window.MutationObserver((list) => {
    for (const record of list) {
      records += 1;
      const target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
      const li = target && target.closest("li");
      if (li) rows.add(li);
      for (const added of record.addedNodes) if (added.tagName === "LI") rows.add(added);
    }
  });
  observer.observe(container, { childList: true, subtree: true, attributes: true, characterData: true });
  return { rows: () => rows.size, records: () => records, reset: () => { rows.clear(); records = 0; observer.takeRecords(); } };
}

/** An answer the test lets go of when it is ready. */
function gated(server, route, body, mutate = () => {}) {
  const gate = deferred();
  server.first(route, () => gate.promise.then(() => { mutate(); return body; }));
  return gate;
}

describe("the order the tags are shown in", () => {
  test("every level is alphabetical, not the order the tags were made", async (t) => {
    const { document } = await openEditor(t);
    assert.deepEqual(rootNames(document), ["Activity", "People", "Places"]);
    assert.deepEqual(childNames(document, 1), ["Avery Tolland", "Hazel Brookmire"]);
    assert.deepEqual(childNames(document, 3), ["Archery", "Swimming"]);
  });

  test("case and accents are ignored, digits count as numbers, and ties are broken the same way every time", async (t) => {
    const names = ["zoo", "Trip 10", "Trip 3", "2024", "Banana", "apple", "Apple", "Écrit", "ecrit", "\u{1F389} party"];
    const tree = treeOf(names.map((name, i) => [i + 1, name, null]));
    const { document } = await openEditor(t, { tree });
    const shown = rootNames(document);
    assert.deepEqual(shown.filter((n) => /^(Trip|2024|zoo)/.test(n)), ["2024", "Trip 3", "Trip 10", "zoo"]);
    assert.deepEqual(shown.filter((n) => /^apple$/i.test(n)), ["Apple", "apple"]);
    assert.deepEqual(shown.filter((n) => /^[eÉ]crit$/.test(n)), ["ecrit", "Écrit"]);
    assert.ok(shown.indexOf("Banana") > shown.indexOf("apple") && shown.indexOf("Banana") < shown.indexOf("ecrit"));
    assert.equal(shown.length, names.length);
    assert.equal(shown[0], "\u{1F389} party", "a symbol sorts before the digits");
  });

  test("the delete question lists the tags it offers in the same order", async (t) => {
    const ctx = await openEditor(t, {
      routes: (server) => server.on("/api/taxonomy/delete-check", { success: true, used: true, count: 2 }),
    });
    click(ctx.window, control(ctx.document, 6, "delete"));
    await flush(ctx.window, 4);
    const options = [...ctx.document.querySelectorAll("#move-target-select option")].map((o) => o.value).filter(Boolean);
    assert.deepEqual(options, [
      "Activity", "Activity/Archery", "People", "People/Avery Tolland", "People/Hazel Brookmire", "Places", "Places/Harbor Town",
    ]);
  });

  test("an empty tree says so, and 900 tags are shown in order", async (t) => {
    const empty = await openEditor(t, { tree: [] });
    assert.match(empty.document.querySelector("#taxonomy-modal .tag-editor-empty").textContent, /taxonomy is empty/);
    assert.equal(empty.document.querySelector("#taxonomy-modal .tag-editor-empty").hidden, false);

    const spec = [];
    for (let i = 1; i <= 30; i++) spec.push([i, `Group ${31 - i}`, null]);
    for (let i = 31; i <= 900; i++) spec.push([i, `Item ${1000 - i}`, ((i - 31) % 30) + 1]);
    const big = await openEditor(t, { tree: treeOf(spec) });
    const roots = rootNames(big.document);
    assert.equal(roots.length, 30);
    assert.deepEqual(roots, [...roots].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
    assert.equal(big.document.querySelectorAll("#taxonomy-modal li.taxonomy-node").length, 900);
    assert.equal(big.document.querySelector("#taxonomy-modal .tag-editor-empty").hidden, true);
  });
});

describe("a rename is shown at once", () => {
  test("the new name, in its place, marked until the server answers; then only the mark goes", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/rename", { success: true, photos_affected: 1, photos_rewritten: 1 },
      () => { ctx.world.tree[1].name = "Zelda Brookmire"; ctx.world.tree[1].tag = "People/Zelda Brookmire"; });
    expand(window, document, 1);
    const row = rowOf(document, 2);
    const people = rowOf(document, 1);
    ctx.answers.prompt = "Zelda Brookmire";

    click(window, control(document, 2, "rename"));
    assert.equal(labelOf(row), "Zelda Brookmire", "the new name waited for the server");
    assert.match(marker(document, 2), /renaming/);
    assert.match(marker(document, 2), /photos are being rewritten/);
    assert.deepEqual(childNames(document, 1), ["Avery Tolland", "Zelda Brookmire"]);
    assert.equal(rowOf(document, 2), row, "the row was rebuilt");
    assert.equal(rowOf(document, 1), people);
    assert.ok(expandedOf(document, 1), "the open branch closed");

    const watch = watchRows(window, ctx.document.getElementById("taxonomy-tree-container"));
    gate.resolve();
    await flush(window, 8);
    assert.equal(marker(document, 2), "");
    assert.equal(rowOf(document, 2), row);
    assert.equal(watch.rows(), 1, "something besides the renamed row changed after the answer");
    assert.deepEqual(ctx.server.lastBody("/api/taxonomy/rename"), { tag_id: 2, new_name: "Zelda Brookmire" });
    assert.equal(status(document), "Ready");
    assert.deepEqual(ctx.alerts, []);
  });

  test("a name that sorts elsewhere moves the node there, into view, marked", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/rename", { success: true, photos_affected: 0, photos_rewritten: 0 });
    ctx.answers.prompt = "Zzz";
    expand(window, document, 3);
    const row = rowOf(document, 7);
    click(window, control(document, 7, "rename"));
    assert.deepEqual(childNames(document, 3), ["Swimming", "Zzz"]);
    assert.equal(rowOf(document, 7), row);
    assert.ok(ctx.scrolled.includes("7"), "the moved node was not scrolled into view");
    assert.ok(row.firstElementChild.classList.contains("taxonomy-flash"), "the moved node was not marked");
  });

  test("a rename the server refuses puts the old name back in its old place and says why", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/rename", { success: false, error: "A tag with path 'Activity/Swimming' already exists." });
    expand(window, document, 3);
    ctx.answers.prompt = "Swimming";
    const before = childNames(document, 3);
    expand(window, document, 1);
    const row = rowOf(document, 7);

    click(window, control(document, 7, "rename"));
    assert.deepEqual(childNames(document, 3), ["Swimming", "Swimming"], "not shown at once");
    gate.resolve();
    await flush(window, 8);

    assert.deepEqual(childNames(document, 3), before);
    assert.equal(rowOf(document, 7), row);
    assert.equal(marker(document, 7), "");
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /already exists/);
    assert.ok(expandedOf(document, 3) && expandedOf(document, 1), "the open branches changed");
  });

  test("a warning keeps the new name, shows the warning, and the node is not left marked", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/rename", { success: true, warning: "2 of 5 photos could not be rewritten." },
      () => { ctx.world.tree[3].name = "Venues"; ctx.world.tree[3].tag = "Venues"; ctx.world.tree[7].tag = "Venues/Harbor Town"; });
    ctx.answers.prompt = "Venues";
    click(window, control(document, 4, "rename"));
    gate.resolve();
    await flush(window, 8);
    assert.equal(labelOf(rowOf(document, 4)), "Venues");
    assert.equal(marker(document, 4), "");
    assert.equal(ctx.alerts.length, 1);
    assert.match(ctx.alerts[0], /2 of 5 photos could not be rewritten/);
  });

  test("a rename whose request fails shows the old name and reads the tree the server holds", async (t) => {
    const ctx = await openEditor(t, { routes: (s) => s.first("/api/taxonomy/rename", () => Promise.reject(new Error("offline"))) });
    ctx.window.console.error = () => {};
    const { window, document, server } = ctx;
    ctx.answers.prompt = "Venues";
    const reads = () => server.urls().filter((u) => u.includes("/api/taxonomy/tree")).length;
    const before = reads();
    click(window, control(document, 4, "rename"));
    await flush(window, 8);
    assert.equal(labelOf(rowOf(document, 4)), "Places");
    assert.equal(marker(document, 4), "");
    assert.ok(reads() > before, "the tree was not read again to settle what the server holds");
    assert.equal(status(document), "Error");
  });

  test("the branch's tag paths follow, and the Add question names the new path", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/rename", { success: true });
    ctx.answers.prompt = "Crowd";
    click(window, control(document, 1, "rename"));
    assert.equal(control(document, 2, "delete").title, "Delete Crowd/Hazel Brookmire");
    assert.equal(control(document, 1, "delete").title, "Delete Crowd");
    let asked = "";
    window.prompt = (question) => { asked = question; return null; };
    click(window, control(document, 2, "add"));
    assert.match(asked, /Crowd\/Hazel Brookmire/);
  });

  test("while the search is typed, the renamed node stays shown even if it no longer matches", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/rename", { success: true });
    const search = document.getElementById("taxonomy-search-input");
    search.value = "swim";
    search.dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(rowOf(document, 6).hidden, false);
    assert.equal(rowOf(document, 4).hidden, true);
    ctx.answers.prompt = "Diving";
    click(window, control(document, 6, "rename"));
    assert.equal(rowOf(document, 6).hidden, false, "the renamed node vanished under the person's hands");
    assert.equal(rowOf(document, 3).hidden, false);
    search.dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(rowOf(document, 6).hidden, true, "typing again did not apply the search to the new name");
  });

  test("a second action on a node still being changed is refused; one on another node is not", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/rename", { success: true },
      () => { ctx.world.tree[3].name = "Venues"; ctx.world.tree[3].tag = "Venues"; ctx.world.tree[7].tag = "Venues/Harbor Town"; });
    ctx.answers.prompt = "Venues";
    click(window, control(document, 4, "rename"));
    ctx.answers.prompt = "Venues 2";
    click(window, control(document, 4, "rename"));
    assert.match(notice(document), /still being changed/);
    assert.equal(server.calls.filter((c) => c.url.includes("/rename")).length, 0, "the first request waited");
    gate.resolve();
    await flush(window, 8);
    assert.equal(server.calls.filter((c) => c.url.includes("/rename")).length, 1);
    assert.equal(labelOf(rowOf(document, 4)), "Venues");
  });

  test("two renames of different nodes both show at once, and go to the server one after the other", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const first = deferred();
    const second = deferred();
    let n = 0;
    server.first("/api/taxonomy/rename", () => (++n === 1 ? first : second).promise);
    ctx.answers.prompt = "Alpha";
    click(window, control(document, 4, "rename"));
    ctx.answers.prompt = "Beta";
    click(window, control(document, 3, "rename"));
    assert.deepEqual(rootNames(document), ["Alpha", "Beta", "People"]);
    assert.match(marker(document, 4), /renaming/);
    assert.match(marker(document, 3), /renaming/);
    await flush(window, 4);
    assert.equal(server.calls.filter((c) => c.url.includes("/rename")).length, 1, "the second was sent before the first was answered");
    ctx.world.tree[3].name = "Alpha";
    ctx.world.tree[3].tag = "Alpha";
    ctx.world.tree[7].tag = "Alpha/Harbor Town";
    first.resolve({ success: true });
    await flush(window, 8);
    assert.equal(server.calls.filter((c) => c.url.includes("/rename")).length, 2);
    assert.equal(marker(document, 4), "");
    assert.match(marker(document, 3), /renaming/);
    second.resolve({ success: false, error: "no" });
    await flush(window, 8);
    assert.deepEqual(rootNames(document), ["Activity", "Alpha", "People"]);
    assert.equal(marker(document, 3), "");
  });

  test("closing the editor and opening it again while the server works keeps the new name, marked", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/rename", { success: true },
      () => { ctx.world.tree[3].name = "Venues"; ctx.world.tree[3].tag = "Venues"; ctx.world.tree[7].tag = "Venues/Harbor Town"; });
    ctx.answers.prompt = "Venues";
    click(window, control(document, 4, "rename"));
    ctx.close();
    await ctx.open();
    assert.equal(labelOf(rowOf(document, 4)), "Venues");
    assert.match(marker(document, 4), /renaming/);
    gate.resolve();
    await flush(window, 8);
    assert.equal(marker(document, 4), "");
    assert.equal(labelOf(rowOf(document, 4)), "Venues");
  });

  test("the focus stays on the control that was used, also when the node moves", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/rename", { success: true });
    expand(window, document, 3);
    const rename = control(document, 7, "rename");
    rename.focus();
    ctx.answers.prompt = "Zzz";
    click(window, rename);
    assert.equal(document.activeElement, control(document, 7, "rename"));
    assert.ok(document.getElementById("taxonomy-tree-container").contains(document.activeElement));
  });

  test("it works the same in TagTuner, whose editor says what it is doing in its own footer", async (t) => {
    const ctx = await openEditor(t, { app: "tagtuner" });
    const { window, document, server } = ctx;
    assert.deepEqual(rootNames(document), ["Activity", "People", "Places"]);
    const gate = gated(server, "/api/taxonomy/rename", { success: true });
    ctx.answers.prompt = "Venues";
    click(window, control(document, 4, "rename"));
    assert.equal(labelOf(rowOf(document, 4)), "Venues");
    assert.match(document.querySelector("#taxonomy-modal .tag-editor-status").textContent, /Renaming/);
    gate.resolve();
    await flush(window, 8);
    assert.equal(document.querySelector("#taxonomy-modal .tag-editor-status").textContent, "");
    assert.equal(marker(document, 4), "");
  });
});

describe("a new tag", () => {
  test("is shown at once under its parent, which opens, in its place, and takes the server's id", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/create", { success: true, id: 9, tag: "Activity/Chess" },
      () => ctx.world.tree.push(...treeOf([[3, "Activity", null], [9, "Chess", 3]]).slice(1)));
    assert.equal(expandedOf(document, 3), false);
    const activity = rowOf(document, 3);
    ctx.answers.prompt = "Chess";
    click(window, control(document, 3, "add"));
    assert.equal(expandedOf(document, 3), true);
    assert.deepEqual(childNames(document, 3), ["Archery", "Chess", "Swimming"]);
    const made = [...sublistOf(activity).children].find((li) => labelOf(li) === "Chess");
    assert.match(marker(document, made.getAttribute("data-id")), /adding/);
    assert.ok(ctx.scrolled.length > 0 && made.firstElementChild.classList.contains("taxonomy-flash"));
    gate.resolve();
    await flush(window, 8);
    assert.equal(rowOf(document, 9), made, "the row was rebuilt under the server's id");
    assert.equal(marker(document, 9), "");
    assert.deepEqual(childNames(document, 3), ["Archery", "Chess", "Swimming"]);
    assert.deepEqual(server.lastBody("/api/taxonomy/create"), { name: "Chess", parent_id: 3, has_face: 0 });
  });

  test("a new root goes to its place among the roots", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    ctx.answers.prompt = "Birthdays";
    ctx.answers.confirm = false;
    gated(server, "/api/taxonomy/create", { success: true, id: 9, tag: "Birthdays" }, () => ctx.world.tree.push(...treeOf([[9, "Birthdays", null]])));
    click(window, document.getElementById("btn-taxonomy-add-root"));
    assert.deepEqual(rootNames(document), ["Activity", "Birthdays", "People", "Places"]);
  });

  test("the server's refusal takes it out again and says why", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/create", { success: false, error: "That name is not allowed." });
    ctx.answers.prompt = "Chess";
    click(window, control(document, 3, "add"));
    assert.equal(childNames(document, 3).length, 3);
    gate.resolve();
    await flush(window, 8);
    assert.deepEqual(childNames(document, 3), ["Archery", "Swimming"]);
    assert.match(ctx.alerts[0], /not allowed/);
  });

  test("a name already there is shown, not made again, and asks nothing of the server", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    ctx.answers.prompt = "swimming";
    click(window, control(document, 3, "add"));
    assert.equal(server.calls.filter((c) => c.url.includes("/create")).length, 0);
    assert.match(notice(document), /already there/);
    assert.deepEqual(childNames(document, 3), ["Archery", "Swimming"]);
  });

  test("a name that is a path is shown when the server has made its levels", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/create", { success: true, id: 10, tag: "Places/Parks/Dog Park" }, () =>
      ctx.world.tree.push(...treeOf([[4, "Places", null], [9, "Parks", 4], [10, "Dog Park", 9]]).slice(1)));
    ctx.answers.prompt = "Parks/Dog Park";
    click(window, control(document, 4, "add"));
    assert.deepEqual(childNames(document, 4), ["Harbor Town"], "a made-up level was shown for a path");
    gate.resolve();
    await flush(window, 8);
    assert.deepEqual(childNames(document, 4), ["Harbor Town", "Parks"]);
    assert.deepEqual(childNames(document, 9), ["Dog Park"]);
    assert.ok(expandedOf(document, 9));
  });

  test("a name that is the parent's own, which the server does not make, leaves nothing behind", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/create", { success: true, id: 4, tag: "Places" });
    ctx.answers.prompt = "Places";
    click(window, control(document, 4, "add"));
    assert.deepEqual(childNames(document, 4), ["Harbor Town", "Places"]);
    gate.resolve();
    await flush(window, 8);
    assert.deepEqual(childNames(document, 4), ["Harbor Town"]);
  });

  test("a tag made under one still being made waits for its parent's id", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    const first = deferred();
    server.first("/api/taxonomy/create", () => first.promise);
    ctx.answers.prompt = "Parks";
    click(window, control(document, 4, "add"));
    const parks = [...sublistOf(rowOf(document, 4)).children].find((li) => labelOf(li) === "Parks");
    ctx.answers.prompt = "Dog Park";
    click(window, parks.firstElementChild.querySelector('[data-control="add"]'));
    assert.deepEqual(childNames(document, 4), ["Harbor Town", "Parks"]);
    ctx.world.tree.push(...treeOf([[4, "Places", null], [9, "Parks", 4], [10, "Dog Park", 9]]).slice(1));
    first.resolve({ success: true, id: 9, tag: "Places/Parks" });
    await flush(window, 4);
    const second = server.calls.filter((c) => c.url.includes("/create"))[1];
    assert.deepEqual(second && second.body, { name: "Dog Park", parent_id: 9, has_face: 0 });
  });
});

describe("flags", () => {
  test("Face Matching is set at once on the node and its branch, and put back if refused", async (t) => {
    const ctx = await openEditor(t, { tree: treeOf([[1, "Pets", null], [2, "Rex", 1]]) });
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/update", { success: false, error: "Refused." });
    const box = control(document, 1, "face");
    box.checked = true;
    box.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.equal(rowOf(document, 2).firstElementChild.querySelector(".taxonomy-node-badge").hidden, false);
    assert.match(marker(document, 1), /saving/);
    gate.resolve();
    await flush(window, 8);
    assert.equal(control(document, 1, "face").checked, false);
    assert.equal(rowOf(document, 2).firstElementChild.querySelector(".taxonomy-node-badge").hidden, true);
    assert.match(ctx.alerts[0], /Refused/);
  });

  test("Hide toggles in place and the tree is not rebuilt", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/update", { success: true }, () => { ctx.world.tree[2].hidden_from_autocomplete = 1; });
    expand(window, document, 3);
    const rows = [...document.querySelectorAll("#taxonomy-modal li.taxonomy-node")];
    const box = control(document, 3, "hide");
    box.focus();
    box.checked = true;
    box.dispatchEvent(new window.Event("change", { bubbles: true }));
    await flush(window, 2);
    assert.deepEqual(server.lastBody("/api/taxonomy/update"), { id: 3, hidden_from_autocomplete: 1 });
    assert.equal(document.activeElement, box);
    assert.deepEqual([...document.querySelectorAll("#taxonomy-modal li.taxonomy-node")], rows);
  });
});

describe("a delete", () => {
  test("keeps the node, marked, until the server has done it; then takes it out and the parent's expander follows", async (t) => {
    const ctx = await openEditor(t, { routes: (s) => s.on("/api/taxonomy/delete-check", { success: true, used: false, count: 0 }) });
    const { window, document, server } = ctx;
    const gate = gated(server, "/api/taxonomy/delete-confirm", { success: true, photos_affected: 0, photos_rewritten: 0 },
      () => { ctx.world.tree = ctx.world.tree.filter((n) => n.id !== 8); });
    expand(window, document, 4);
    expand(window, document, 3);
    const places = rowOf(document, 4);
    click(window, control(document, 8, "delete"));
    await flush(window, 4);
    assert.match(marker(document, 8), /deleting/);
    assert.ok(rowOf(document, 8), "removed before the server said so");
    gate.resolve();
    await flush(window, 8);
    assert.equal(rowOf(document, 8), null);
    assert.equal(rowOf(document, 4), places);
    assert.equal(places.firstElementChild.querySelector(".taxonomy-node-expander").textContent, "•");
    assert.ok(expandedOf(document, 3), "another open branch closed");
    assert.deepEqual(server.lastBody("/api/taxonomy/delete-confirm"), { tag_id: 8, action: "remove" });
  });

  test("a branch is taken out with everything under it, through the same question as before", async (t) => {
    const ctx = await openEditor(t, { routes: (s) => s.on("/api/taxonomy/delete-check", { success: true, used: true, count: 4 }) });
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/delete-confirm", { success: true, photos_affected: 4, photos_rewritten: 4 },
      () => { ctx.world.tree = ctx.world.tree.filter((n) => ![3, 6, 7].includes(n.id)); }).resolve();
    click(window, control(document, 3, "delete"));
    await flush(window, 4);
    const conflict = document.querySelector(".tag-editor-conflict");
    assert.ok(conflict);
    const options = [...conflict.querySelectorAll("#move-target-select option")].map((o) => o.value).filter(Boolean);
    assert.ok(!options.some((o) => o.startsWith("Activity")), "the branch offered itself as the place to move to");
    click(window, conflict.querySelector(".btn-confirm"));
    await flush(window, 8);
    assert.equal(rowOf(document, 3), null);
    assert.equal(rowOf(document, 6), null);
    assert.deepEqual(rootNames(document), ["People", "Places"]);
  });

  test("one the server cannot finish stays in the tree", async (t) => {
    const ctx = await openEditor(t, { routes: (s) => s.on("/api/taxonomy/delete-check", { success: true, used: true, count: 4 }) });
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/delete-confirm", { success: false, error: "1 of 4 photos could not be rewritten.", photos_rewritten: 3 }).resolve();
    click(window, control(document, 3, "delete"));
    await flush(window, 4);
    click(window, document.querySelector(".tag-editor-conflict .btn-confirm"));
    await flush(window, 8);
    assert.ok(rowOf(document, 3));
    assert.equal(marker(document, 3), "");
    assert.match(ctx.alerts[0], /could not be rewritten/);
  });

  test("while another node is being renamed, it goes ahead and the rename is not disturbed", async (t) => {
    const ctx = await openEditor(t, { routes: (s) => s.on("/api/taxonomy/delete-check", { success: true, used: false, count: 0 }) });
    const { window, document, server } = ctx;
    const rename = gated(server, "/api/taxonomy/rename", { success: true },
      () => { ctx.world.tree[3].name = "Venues"; ctx.world.tree[3].tag = "Venues"; ctx.world.tree[7].tag = "Venues/Harbor Town"; });
    ctx.answers.prompt = "Venues";
    click(window, control(document, 4, "rename"));
    gated(server, "/api/taxonomy/delete-confirm", { success: true, photos_affected: 0, photos_rewritten: 0 },
      () => { ctx.world.tree = ctx.world.tree.filter((n) => n.id !== 7); }).resolve();
    click(window, control(document, 7, "delete"));
    await flush(window, 6);
    assert.match(marker(document, 4), /renaming/, "the delete waited for the rename and was held up by it");
    rename.resolve();
    await flush(window, 10);
    assert.equal(rowOf(document, 7), null);
    assert.equal(labelOf(rowOf(document, 4)), "Venues");
    assert.equal(marker(document, 4), "");
  });
});

describe("the tree the page reads again", () => {
  test("is shown by what differs: rows that did not change are the same elements", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    expand(window, document, 1);
    expand(window, document, 3);
    const rows = new Map([...document.querySelectorAll("#taxonomy-modal li.taxonomy-node")].map((li) => [li.getAttribute("data-id"), li]));
    // Someone else renamed one, removed one and added one while the editor was open.
    ctx.world.tree = ctx.world.tree.filter((n) => n.id !== 7);
    ctx.world.tree.find((n) => n.id === 5).name = "Avery Brook";
    ctx.world.tree.find((n) => n.id === 5).tag = "People/Avery Brook";
    ctx.world.tree.push(...treeOf([[3, "Activity", null], [11, "Zip-lining", 3]]).slice(1));
    gated(server, "/api/taxonomy/update", { success: true }).resolve();
    const box = control(document, 3, "hide");
    box.checked = true;
    box.dispatchEvent(new window.Event("change", { bubbles: true }));
    await flush(window, 8);

    assert.equal(rowOf(document, 7), null);
    assert.equal(labelOf(rowOf(document, 5)), "Avery Brook");
    assert.deepEqual(childNames(document, 3), ["Swimming", "Zip-lining"]);
    for (const id of ["1", "2", "3", "4", "5", "6", "8"]) assert.equal(rowOf(document, id), rows.get(id), `row ${id} was rebuilt`);
    assert.ok(expandedOf(document, 1) && expandedOf(document, 3));
  });

  test("a read that came back empty is not taken for an empty tree", async (t) => {
    const ctx = await openEditor(t);
    const { window, document, server } = ctx;
    gated(server, "/api/taxonomy/update", { success: true }, () => { ctx.world.tree = []; }).resolve();
    const box = control(document, 3, "hide");
    box.checked = true;
    box.dispatchEvent(new window.Event("change", { bubbles: true }));
    await flush(window, 8);
    assert.deepEqual(rootNames(document), ["Activity", "People", "Places"]);
  });
});

describe("opening the editor again", () => {
  test("shows the same rows, with what was open still open, the search typed, and the scroll where it was", async (t) => {
    const ctx = await openEditor(t);
    const { window, document } = ctx;
    expand(window, document, 3);
    const search = document.getElementById("taxonomy-search-input");
    search.value = "a";
    search.dispatchEvent(new window.Event("input", { bubbles: true }));
    const rows = [...document.querySelectorAll("#taxonomy-modal li.taxonomy-node")];
    const container = document.getElementById("taxonomy-tree-container");
    container.scrollTop = 25;
    const scrolled = container.scrollTop;
    ctx.close();
    await ctx.open();
    assert.deepEqual([...document.querySelectorAll("#taxonomy-modal li.taxonomy-node")], rows);
    assert.ok(expandedOf(document, 3));
    assert.equal(search.value, "a");
    assert.equal(container.scrollTop, scrolled);
  });

  test("in TagTuner, which reads the tree each time, shows what someone else changed meanwhile", async (t) => {
    const ctx = await openEditor(t, { app: "tagtuner" });
    const { document } = ctx;
    ctx.close();
    ctx.world.tree.push(...treeOf([[4, "Places", null], [9, "Beach", 4]]).slice(1));
    await ctx.open();
    assert.deepEqual(childNames(document, 4), ["Beach", "Harbor Town"]);
  });
});

describe("what an action costs", () => {
  test("one rename in 900 tags changes one row; opening builds each once", async (t) => {
    const spec = [];
    for (let i = 1; i <= 30; i++) spec.push([i, `Group ${i}`, null]);
    for (let i = 31; i <= 900; i++) spec.push([i, `Item ${i}`, ((i - 31) % 30) + 1]);
    const ctx = await openEditor(t, { tree: treeOf(spec) });
    const { window, document, server } = ctx;
    const container = document.getElementById("taxonomy-tree-container");
    const watch = watchRows(window, container);
    gated(server, "/api/taxonomy/rename", { success: true },
      () => { ctx.world.tree[899].name = "Item 900 renamed"; ctx.world.tree[899].tag = "Group 30/Item 900 renamed"; }).resolve();
    ctx.answers.prompt = "Item 900 renamed";
    click(window, control(document, 900, "rename"));
    await flush(window, 8);
    const rows = watch.rows();
    const records = watch.records();
    assert.ok(rows <= 2, `a rename touched ${rows} rows`);
    assert.ok(records < 30, `a rename made ${records} changes to the page`);
    console.log(`# a rename in 900 tags: ${rows} row(s), ${records} DOM change(s)`);
  });
});
