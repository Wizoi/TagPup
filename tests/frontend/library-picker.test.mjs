/**
 * The library picker is a listbox of the page's own, on both pages (docs/findings.md, #909, #782).
 *
 * A native select's open list is an operating-system popup, and Chrome on Windows drew a blank block under its last
 * option that no style of the page reaches. The list is now a button and a role="listbox" in the page: its last child
 * is the last library, there is nothing below it, and the native select stays hidden as the one place the choice lives
 * (library.js reads its value and hears its change, folder.js disables it). Fictional library names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const libraries = (names) =>
  new FakeServer()
    .on("/api/databases", { databases: names })
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 });

const NAMES = ["alder-park", "birch-lane", "cedar-yard", "dune-field"];
const navigated = (consoleErrors) =>
  consoleErrors.some((e) => /navigation/i.test(e && e.message ? e.message : String(e)));

async function open(app, t, { url = "http://localhost:8090/birch-lane/" } = {}) {
  const ctx = await loadApp(app, { t, url, server: libraries(NAMES) });
  await flush(ctx.window);
  const { document } = ctx;
  ctx.button = document.querySelector(".lib-picker-button");
  ctx.list = document.querySelector(".lib-picker-list");
  ctx.select = document.getElementById("db-select");
  ctx.key = (key, target = document.activeElement) =>
    target.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  ctx.shown = () => !ctx.list.classList.contains("hidden");
  ctx.names = () => [...ctx.list.children].map((li) => li.textContent);
  return ctx;
}

for (const app of ["tagpup", "tagtuner"]) {
  describe(`${app}: the library picker`, () => {
    test("a button shows the library; the list is closed and holds the libraries and nothing else", async (t) => {
      const ctx = await open(app, t);
      assert.ok(ctx.button, "no picker button");
      assert.equal(ctx.button.textContent.includes("birch-lane"), true);
      assert.equal(ctx.button.getAttribute("aria-haspopup"), "listbox");
      assert.equal(ctx.button.getAttribute("aria-expanded"), "false");
      assert.equal(ctx.shown(), false);
      assert.equal(ctx.list.getAttribute("role"), "listbox");
      assert.deepEqual(ctx.names(), NAMES, "the list holds anything but the libraries (a blank block under the last?)");
      for (const li of ctx.list.children) assert.equal(li.getAttribute("role"), "option");
      assert.equal(ctx.list.querySelector("[aria-selected=true]").textContent, "birch-lane");
      assert.ok(ctx.select.classList.contains("lib-picker-source"), "the native select is still shown");
    });

    test("a click opens it on the current library, and a second click closes it", async (t) => {
      const ctx = await open(app, t);
      ctx.button.click();
      assert.equal(ctx.shown(), true);
      assert.equal(ctx.button.getAttribute("aria-expanded"), "true");
      assert.equal(ctx.document.activeElement, ctx.list);
      const active = ctx.document.getElementById(ctx.list.getAttribute("aria-activedescendant"));
      assert.equal(active.textContent, "birch-lane");
      ctx.button.click();
      assert.equal(ctx.shown(), false);
    });

    test("the arrows, Home and End move; Enter chooses and the page goes to that library", async (t) => {
      const ctx = await open(app, t);
      ctx.button.click();
      const activeName = () => ctx.document.getElementById(ctx.list.getAttribute("aria-activedescendant")).textContent;
      ctx.key("ArrowDown");
      assert.equal(activeName(), "cedar-yard");
      ctx.key("End");
      assert.equal(activeName(), "dune-field");
      ctx.key("ArrowDown");
      assert.equal(activeName(), "dune-field", "the end is not wrapped past");
      ctx.key("Home");
      assert.equal(activeName(), "alder-park");
      ctx.key("ArrowDown");
      ctx.key("ArrowDown");
      ctx.key("Enter");
      assert.equal(ctx.select.value, "cedar-yard");
      assert.equal(ctx.shown(), false);
      assert.equal(ctx.document.activeElement, ctx.button);
      await flush(ctx.window);
      assert.ok(navigated(ctx.consoleErrors), "choosing a library did not go to it");
    });

    test("a keyboard opens it from the button, and typing goes to a name", async (t) => {
      const ctx = await open(app, t);
      ctx.button.focus();
      ctx.key("ArrowDown", ctx.button);
      assert.equal(ctx.shown(), true);
      ctx.key("d");
      ctx.key("Enter");
      assert.equal(ctx.select.value, "dune-field");
    });

    test("Escape closes without choosing and gives the focus back; so does a click outside", async (t) => {
      const ctx = await open(app, t);
      ctx.button.click();
      ctx.key("ArrowDown");
      ctx.key("Escape");
      assert.equal(ctx.shown(), false);
      assert.equal(ctx.select.value, "birch-lane");
      assert.equal(ctx.document.activeElement, ctx.button);
      ctx.button.click();
      ctx.document.body.dispatchEvent(new ctx.window.MouseEvent("mousedown", { bubbles: true }));
      assert.equal(ctx.shown(), false);
      await flush(ctx.window);
      assert.ok(!navigated(ctx.consoleErrors), "closing the list went to another library");
    });

    test("clicking the current library only closes the list", async (t) => {
      const ctx = await open(app, t);
      ctx.button.click();
      ctx.list.children[1].click();
      assert.equal(ctx.shown(), false);
      await flush(ctx.window);
      assert.ok(!navigated(ctx.consoleErrors));
    });

    test("clicking another library goes to it", async (t) => {
      const ctx = await open(app, t);
      ctx.button.click();
      ctx.list.children[3].click();
      assert.equal(ctx.select.value, "dune-field");
      await flush(ctx.window);
      assert.ok(navigated(ctx.consoleErrors));
    });

    test("a disabled select disables the button, and the page focusing the select reaches the button", async (t) => {
      const ctx = await open(app, t);
      ctx.select.disabled = true;
      await flush(ctx.window);
      assert.equal(ctx.button.disabled, true);
      ctx.select.disabled = false;
      await flush(ctx.window);
      assert.equal(ctx.button.disabled, false);
      ctx.select.focus();
      assert.equal(ctx.document.activeElement, ctx.button);
    });

    test("with no library in the address the button asks for one, and the prompt is not offered", async (t) => {
      const ctx = await open(app, t, { url: "http://localhost:8090/" });
      assert.match(ctx.button.textContent, /Choose a library/);
      assert.deepEqual(ctx.names(), NAMES);
    });
  });
}
