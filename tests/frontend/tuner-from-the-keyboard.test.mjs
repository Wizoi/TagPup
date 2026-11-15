/**
 * TagTuner from the keyboard alone: every control Tab reaches, in the order the page shows it.
 *
 * A folder of Folder Matches could not be opened without a mouse (its header was a div with a
 * click), the Show Matched Photos switch was display:none and so out of the Tab order, and the
 * Tune target select and the Assign field showed no focus at all. The Tab order is computed as a
 * browser does from the page's own stylesheets (tests/frontend/tab-order.mjs); jsdom has no Tab
 * and no activation behaviour of its own, so `press` does what a browser does with Enter and Space.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, flush } from "./harness.mjs";
import { withStylesheets, recordClickTargets, tabOrder, nameOf, invisibleStops, mouseOnly } from "./tab-order.mjs";

const MEET = "D:\\Meet";
const FINISH = "D:\\Finish";
const PHOTOS = [
  { path: "D:\\Meet\\120.jpg", filename: "120.jpg", unmatched_count: 2, matched_count: 0, mtime: 4, year: "2026", folder: MEET },
  { path: "D:\\Meet\\121.jpg", filename: "121.jpg", unmatched_count: 1, matched_count: 0, mtime: 3, year: "2026", folder: MEET },
  { path: "D:\\Finish\\7.jpg", filename: "7.jpg", unmatched_count: 1, matched_count: 0, mtime: 2, year: "2026", folder: FINISH },
  { path: "D:\\Finish\\8.jpg", filename: "8.jpg", unmatched_count: 1, matched_count: 0, mtime: 1, year: "2026", folder: FINISH },
  { path: "D:\\Old\\1.jpg", filename: "1.jpg", unmatched_count: 1, matched_count: 0, mtime: 0, year: "2025", folder: "D:\\Old" },
];

function face(id) {
  return {
    id, photo_path: `D:\\Meet\\m${id}.jpg`, filename: `m${id}.jpg`, box: [10, 10, 40, 40], prob: 0.99, mtime: 0, year: 2026,
    similarity: 0.5, cluster_id: id % 2, cluster_name: `Group ${id % 2}`, other_names: [],
    suggested_name: "Rowan Thackeray", suggested_similarity: 0.6, suggestion_strength: "strong",
  };
}

function server() {
  return new FakeServer()
    .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/people-with-counts", [{ name: "Rowan Thackeray", count: 3 }])
    .on("/api/people", [{ id: 1, name: "Rowan Thackeray", tag: "People/Rowan Thackeray" }])
    // A list of its own each time: the page keeps the records it is sent and writes on them.
    .on("/api/photos", () => PHOTOS.map((p) => ({ ...p })))
    .on("/api/photo-details", (url) => ({
      path: decodeURIComponent(url.split("path=")[1]), filename: "120.jpg", year: "2026", caption: "",
      tags: [], people: ["Rowan Thackeray"],
      faces: [{ id: 41, box: [10, 10, 40, 40], name: null, excluded: false, max_similarity: 0.4 },
              { id: 42, box: [50, 10, 90, 40], name: "Rowan Thackeray", excluded: false, max_similarity: 0.9 }],
    }))
    .on("/api/face-matches", [])
    .on("/api/unmatched-faces/people", [{ name: "Unknown Faces", count: 3, unit: "face" }])
    .on("/api/unmatched-faces/person-matches", {
      faces: [1, 2, 3].map(face), total: 3, total_count: 3, unclustered_total: 0, unclustered_shown: 0,
      has_more: false, page: 1, limit: -1,
    });
}

async function open(t, query) {
  const ctx = await loadApp("tagtuner", {
    t, url: `http://localhost:8080/kr-track/?${query}`, server: server(),
    before: (window) => { withStylesheets("tuner")(window); recordClickTargets(window); },
  });
  await flush(ctx.window, 8);
  return ctx;
}

/** Enter or Space on the focused `el` as a browser takes it: the page's keydown first, then a button's or a box's own click. */
async function press(ctx, el, key) {
  assert.equal(ctx.document.activeElement, el, `${nameOf(el)} has the focus`);
  const event = new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  el.dispatchEvent(event);
  const activates = key === "Enter" ? el.matches("button") : key === " " && el.matches("button, input[type=checkbox]");
  if (!event.defaultPrevented && activates) el.click();
  await flush(ctx.window, 6);
}

/** Tab to `el`: it must be one of the page's stops; the focus is put on it. */
function tabTo(ctx, el) {
  assert.ok(el, "the control is on the page");
  assert.ok(tabOrder(ctx.document).includes(el), `${nameOf(el)} is in the Tab order`);
  el.focus();
}

/**
 * Does `el` show the keyboard's focus: a rule of the page's stylesheets for it under :focus or :focus-visible (on it, or
 * on what follows it, as the switch's slider) giving an outline, an accent border or a glow -- and no `outline: none` in
 * its own style attribute, which wins over every such rule.
 */
function focusShown(document, el) {
  if (el.style.getPropertyValue("outline") === "none" || el.style.getPropertyValue("outline-style") === "none") return false;
  const rules = [];
  const walk = (list) => {
    for (const rule of list) {
      if (rule.cssRules && !rule.selectorText) walk(rule.cssRules);
      else if (rule.selectorText) rules.push(rule);
    }
  };
  for (const sheet of document.styleSheets) walk(sheet.cssRules);
  return rules.some((rule) => rule.selectorText.split(",").some((selector) => {
    const at = selector.search(/:focus(-visible)?\b/);
    if (at < 0) return false;
    const subject = selector.slice(0, at).trim();
    let matches = false;
    try { matches = subject !== "" && el.matches(subject); } catch { matches = false; }
    const style = rule.style;
    const outline = style.getPropertyValue("outline") || style.getPropertyValue("outline-style");
    const shows = (outline !== "" && outline !== "none" && outline !== "0") || style.getPropertyValue("border-color") !== ""
      || style.getPropertyValue("box-shadow") !== "";
    return matches && shows;
  }));
}

const folderHeader = (document, folder) => [...document.querySelectorAll("#photo-list .folder-header")]
  .find((h) => h.querySelector(".folder-title").title === folder);

/** The stops a browser tabs onto unseen, but the tag editor's: its closed look is web/common/tag-editor.css's, the shared closed-dialog CSS (cut 3a). */
const unseen = (document) => invisibleStops(document).filter((el) => !el.closest(".tag-editor")).map(nameOf);

/**
 * What a click acts on that Tab does not reach, each for a reason: a container whose click reaches the same thing as a
 * stop inside it, or a row the arrow keys step.
 */
const DELEGATED = {
  "div.year-header": "its toggle button is the stop; the header's click covers its padding",
  "div.folder-header": "its toggle button is the stop; the header's click covers its padding",
  "li.photo-item": "ArrowUp and ArrowDown step the rows from anywhere but a field (web/tuner/sidebar.js)",
  "div.face-card": "its header is the stop (role button); the card's click covers the rest of it",
};

/** The rows the arrow keys step; every other delegated container holds the stop that does what its click does. */
const ROWS = "li.photo-item";

/**
 * A face of a person's grid (Identify Faces, Review People): one stop each would put up to 10,751 faces (the largest
 * person, 2026-11-15) between the banner and the face's details. Its keyboard is a roving grid, not built here.
 */
const GRID_FACE = "div.face-match-item";

/** The page's stops: none unseen, no tabindex above 0, and every click a stop, a row, or a container holding a stop. */
function checkStops(ctx, allowed = {}) {
  assert.deepEqual(unseen(ctx.document), [], "no stop at opacity 0");
  assert.deepEqual([...ctx.document.querySelectorAll("[tabindex]")].filter((el) => el.tabIndex > 0).map(nameOf), [],
    "no tabindex above 0: the order is the page's");
  const mouse = mouseOnly(ctx.window);
  assert.deepEqual(mouse.map(nameOf).filter((name) => !DELEGATED[name] && !allowed[name]), [],
    "every other click target is a stop");
  const order = tabOrder(ctx.document);
  const empty = mouse.filter((el) => DELEGATED[nameOf(el)] && !el.matches(ROWS) && !order.some((stop) => el.contains(stop)));
  assert.deepEqual(empty.map(nameOf), [], "each delegated container holds its stop");
}

describe("TagTuner from the keyboard", () => {
  afterEach(() => closeAllApps());

  test("a folder opens from its header with Enter, and its photos step on ArrowDown", async (t) => {
    const ctx = await open(t, "mode=folder-match");
    const header = folderHeader(ctx.document, FINISH);
    const toggle = header.querySelector("button.tree-toggle");
    tabTo(ctx, toggle);
    assert.equal(toggle.getAttribute("aria-expanded"), "false", "the folder starts closed");
    assert.ok(!header.querySelector(".btn-folder-automatch").closest(".tree-toggle"), "Re-examine is beside the toggle, not in it");

    await press(ctx, toggle, "Enter");
    assert.equal(toggle.getAttribute("aria-expanded"), "true", "Enter opened the folder");
    const rows = [...header.parentElement.querySelectorAll(".folder-photo-item")];
    assert.ok(rows.every((row) => ctx.window.getComputedStyle(row.parentElement).display !== "none"), "its photos show");

    ctx.document.activeElement.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await flush(ctx.window, 6);
    assert.equal(ctx.document.querySelector(".folder-photo-item.active"), rows[0], "ArrowDown selected the folder's first photo");
    assert.ok(ctx.server.urls().some((u) => u.includes("/api/photo-details") && u.includes("7.jpg")), "and its details were asked for");

    toggle.focus();
    await press(ctx, toggle, " ");
    assert.equal(toggle.getAttribute("aria-expanded"), "false", "Space closed it again");
  });

  test("the year's header opens and closes from the keyboard too", async (t) => {
    const ctx = await open(t, "mode=folder-match");
    const years = [...ctx.document.querySelectorAll("#photo-list .year-header .tree-toggle")];
    assert.equal(years.length, 2);
    assert.deepEqual(years.map((y) => y.getAttribute("aria-expanded")), ["true", "false"], "the newest year starts open");
    tabTo(ctx, years[1]);
    await press(ctx, years[1], "Enter");
    assert.equal(years[1].getAttribute("aria-expanded"), "true");
    assert.ok(tabOrder(ctx.document).some((el) => el.closest(".year-group") === years[1].closest(".year-group")
      && el.closest(".folder-header")), "the opened year's folders are stops");
  });

  test("the Show Matched switch is a stop, Space turns it on, and the list is asked for again", async (t) => {
    const ctx = await open(t, "mode=folder-match");
    const toggle = ctx.document.getElementById("show-matched-toggle");
    tabTo(ctx, toggle);
    assert.ok(focusShown(ctx.document, toggle), "its slider shows the focus");
    assert.equal(toggle.checked, false);
    const asked = ctx.server.urls().length;
    await press(ctx, toggle, " ");
    assert.equal(toggle.checked, true, "Space turned it on");
    assert.ok(ctx.server.urls().slice(asked).some((u) => u.includes("/api/photos") && u.includes("show_matched=true")),
      "the change handler asked for the finished photos too");
  });

  test("the Tune target select and the Assign field show where the focus is", async (t) => {
    const ctx = await open(t, `mode=unmatched-faces&person=${encodeURIComponent("Unknown Faces")}`);
    const order = tabOrder(ctx.document);
    for (const id of ["tuner-mode", "input-reassign-name"]) {
      const el = ctx.document.getElementById(id);
      assert.ok(order.includes(el), `#${id} is a stop`);
      assert.ok(focusShown(ctx.document, el), `#${id} shows the focus`);
    }
  });

  test("a face of the open photo opens its panel from the keyboard, and Tab goes on into the panel", async (t) => {
    const ctx = await open(t, `mode=folder-match&photo=${encodeURIComponent(PHOTOS[0].path)}`);
    const card = ctx.document.querySelector("#faces-grid .face-card");
    const header = card.querySelector(".face-card-header");
    tabTo(ctx, header);
    assert.ok(focusShown(ctx.document, header), "the header shows the focus");
    await press(ctx, header, "Enter");
    assert.ok(card.classList.contains("selected"), "Enter selected the face");
    const panel = card.querySelector(".face-edit-panel");
    assert.ok(!panel.classList.contains("hidden"), "and opened its panel");
    const order = tabOrder(ctx.document);
    assert.ok(panel.contains(order[order.indexOf(header) + 1]), "Tab goes from the face into its panel");
    assert.ok(order.includes(panel.querySelector(".custom-match-input")), "whose name field is a stop");
    assert.ok(ctx.server.urls().some((u) => u.includes("/api/face-matches")), "its suggestions were asked for");
  });

  test("the page's stops: none unseen, no tabindex above 0, every click reachable or delegated", async (t) => {
    const ctx = await open(t, `mode=folder-match&photo=${encodeURIComponent(PHOTOS[0].path)}`);
    assert.ok(ctx.document.querySelector(".face-card"), "a photo is open, with its faces");
    checkStops(ctx);
  });

  test("Identify Faces' stops: none unseen, the banner, then the tabs, then each group's buttons", async (t) => {
    const ctx = await open(t, `mode=unmatched-faces&person=${encodeURIComponent("Unknown Faces")}`);
    assert.ok(ctx.document.querySelector(".face-match-item"), "a grid of faces is shown");
    checkStops(ctx, { [GRID_FACE]: "the grid's faces: see GRID_FACE" });
    const order = tabOrder(ctx.document).map(nameOf);
    const at = (name) => order.indexOf(name);
    assert.ok(at("#input-reassign-name") < at("#tab-matches"), "the banner before the tabs");
    assert.ok(at("#tab-matches") < order.findIndex((name) => name.startsWith("button.cluster-suggestion")),
      "the tabs before the groups");
  });
});
