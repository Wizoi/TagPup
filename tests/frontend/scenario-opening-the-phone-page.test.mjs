/**
 * Owner scenario: the owner opens TagPup's phone page (web/tagpup/mobile.html, docs/plans/phone-viewer.md) on a phone,
 * from the home-screen icon or a bookmark, and moves between its screens and libraries.
 *
 * - The icon's address names no library: the page goes to the one this browser opened last, while the server still
 *   lists it (web/common/library.js rememberedAndListed), and otherwise shows the list.
 * - Each screen is a step in the browser's history, so the phone's Back goes up a screen (web/tagpup/mobile-screens.js).
 * - Another library is another page, /<name>/mobile.html (goToLibrary): nothing of the first is carried over or reset.
 * - A server replaced by a newer version while the page is open says so in the banner (web/common/api.js).
 * - A list that could not be read says so with Try again; a slow first reply never draws over a newer one
 *   (web/tagpup/mobile-library.js).
 *
 * jsdom does not navigate: a page that goes to another library records "Not implemented: navigation" in its console,
 * and the address it goes to is libraryAddress's (library-picker.test.mjs does the same).
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps, pageExports, testClock } from "./harness.mjs";
import { tab } from "./phone-page.mjs";

beforeEach((t) => testClock(t));   // the page's timers run when a test advances them

const LIBRARIES = ["alpha", "beta"];

const navigated = (consoleErrors) =>
  consoleErrors.some((e) => /navigation/i.test(e && e.message ? e.message : String(e)));

const el = (ctx, id) => ctx.document.getElementById(id);

const screen = (ctx, name) => ctx.document.querySelector(`[data-screen="${name}"]`);

const shown = (ctx) =>
  [...ctx.document.querySelectorAll("[data-screen]")].filter((s) => !s.classList.contains("hidden")).map((s) => s.dataset.screen);

const choices = (ctx) => [...el(ctx, "library-list").querySelectorAll(".library-choice")].map((b) => b.textContent);

const remember = (name) => (window) => window.localStorage.setItem("tagpup.library", name);

/** Back, as the phone's gesture: the history steps back and the page hears popstate (jsdom fires it a task later). */
async function back(ctx) {
  ctx.window.history.back();
  await flush(ctx.window);
}

afterEach(() => closeAllApps());

describe("opening the phone page", () => {
  test("an address naming no library opens the one this browser opened last, while the server lists it", async (t) => {
    const server = new FakeServer().on("/api/databases", { databases: LIBRARIES });
    const ctx = await loadApp("phone", { t, server, url: "http://localhost:8090/mobile.html", before: remember("beta") });

    assert.ok(navigated(ctx.consoleErrors), "the page goes to the remembered library");
    assert.equal(pageExports(ctx.window, "web/common/library.js").libraryAddress("beta"), "/beta/mobile.html");
    assert.equal(ctx.window.localStorage.getItem("tagpup.library"), "beta", "still remembered");
    assert.deepEqual(choices(ctx), [], "no list is drawn over a page that is leaving");
  });

  test("a remembered library the server no longer lists is forgotten, and the list is shown", async (t) => {
    const server = new FakeServer().on("/api/databases", { databases: LIBRARIES });
    const ctx = await loadApp("phone", { t, server, url: "http://localhost:8090/mobile.html", before: remember("gone") });

    assert.ok(!navigated(ctx.consoleErrors));
    assert.deepEqual(shown(ctx), ["library"]);
    assert.deepEqual(choices(ctx), LIBRARIES);
    assert.equal(ctx.window.localStorage.getItem("tagpup.library"), null);
  });

  test("Back goes up a screen, and the bar's Back does the same", async (t) => {
    const server = new FakeServer().on("/api/databases", { databases: LIBRARIES });
    const ctx = await loadApp("phone", { t, server, url: "http://localhost:8090/alpha/mobile.html" });
    const first = ctx.window.history.length;

    assert.deepEqual(shown(ctx), ["browse"]);
    assert.ok(el(ctx, "bar-back").classList.contains("hidden"), "no Back on the first screen");

    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);
    assert.deepEqual(shown(ctx), ["library"]);
    assert.equal(ctx.window.history.length, first + 1, "the screen is a step in history");
    assert.equal(el(ctx, "bar-title").textContent, "Libraries");
    assert.ok(!el(ctx, "bar-back").classList.contains("hidden"));

    await back(ctx);
    assert.deepEqual(shown(ctx), ["browse"], "the phone's Back goes up a screen, not off the page");
    assert.equal(el(ctx, "bar-title").textContent, "alpha");
    assert.ok(!navigated(ctx.consoleErrors));

    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);
    click(ctx.window, el(ctx, "bar-back"));
    await flush(ctx.window);
    assert.deepEqual(shown(ctx), ["browse"], "the bar's Back too");

    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);
    const current = el(ctx, "library-list").querySelector(".library-choice.current");
    assert.equal(current.textContent, "alpha");
    click(ctx.window, current);
    await flush(ctx.window);
    assert.deepEqual(shown(ctx), ["browse"], "the library already open goes back to it");
    assert.ok(!navigated(ctx.consoleErrors));
  });

  test("choosing another library opens the phone's page on it", async (t) => {
    const server = new FakeServer().on("/api/databases", { databases: LIBRARIES });
    const ctx = await loadApp("phone", { t, server, url: "http://localhost:8090/alpha/mobile.html" });

    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);
    const other = [...el(ctx, "library-list").querySelectorAll(".library-choice")].find((b) => b.textContent === "beta");
    assert.ok(other && !other.classList.contains("current"));
    assert.ok(!navigated(ctx.consoleErrors));

    click(ctx.window, other);
    await flush(ctx.window);
    assert.ok(navigated(ctx.consoleErrors), "another library is another page");
    assert.equal(pageExports(ctx.window, "web/common/library.js").libraryAddress("beta"), "/beta/mobile.html",
      "the phone's page, not the desktop's");
  });

  test("a server updated while the page is open says so in the banner", async (t) => {
    const server = new FakeServer()
      .on("/api/databases", { databases: LIBRARIES }, { headers: { "X-TagPup-Version": "1.0.0" } });
    const ctx = await loadApp("phone", { t, server, url: "http://localhost:8090/alpha/mobile.html" });

    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);
    assert.deepEqual(choices(ctx), LIBRARIES);
    assert.ok(el(ctx, "roots-banner").classList.contains("hidden"), "the first version seen is no update");

    await back(ctx);
    server.first("/api/databases", { databases: LIBRARIES }, { headers: { "X-TagPup-Version": "1.1.0" } });
    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);

    assert.equal(server.urls().filter((u) => u.includes("/api/databases")).length, 2, "the list is asked each time it is shown");
    const banner = el(ctx, "roots-banner");
    assert.ok(!banner.classList.contains("hidden"));
    assert.match(banner.textContent, /TagPup was updated while this page was open/);
    assert.match(banner.textContent, /1\.1\.0/);
  });

  test("a list that could not be read says so, and Try again reads it again", async (t) => {
    const server = new FakeServer().on("/api/databases", { error: "the server could not list them" }, { status: 500 });
    const ctx = await loadApp("phone", { t, server, url: "http://localhost:8090/mobile.html" });

    assert.deepEqual(shown(ctx), ["library"]);
    assert.deepEqual(choices(ctx), []);
    const problem = el(ctx, "library-problem");
    assert.ok(!problem.classList.contains("hidden"));
    assert.match(problem.textContent, /Could not load the libraries/);
    assert.equal(server.urls().filter((u) => u.includes("/api/databases")).length, 1);

    server.first("/api/databases", { databases: LIBRARIES });
    click(ctx.window, el(ctx, "library-retry"));
    await flush(ctx.window);

    assert.equal(server.urls().filter((u) => u.includes("/api/databases")).length, 2, "Try again asks once more");
    assert.ok(problem.classList.contains("hidden"));
    assert.deepEqual(choices(ctx), LIBRARIES);
  });

  test("a slow first reply does not draw over a newer one", async (t) => {
    const held = [];
    let asked = 0;
    const server = new FakeServer().on("/api/databases", () => {
      asked += 1;
      if (asked === 1) return new Promise((resolve) => held.push(resolve));
      return { databases: [...LIBRARIES, "gamma"] };
    });
    const ctx = await loadApp("phone", { t, server, url: "http://localhost:8090/alpha/mobile.html" });

    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);
    assert.equal(held.length, 1, "the first ask is still out");
    await back(ctx);
    click(ctx.window, tab(ctx, "library"));
    await flush(ctx.window);
    assert.deepEqual(choices(ctx), [...LIBRARIES, "gamma"]);

    held[0]({ databases: ["alpha"] });
    await flush(ctx.window);
    assert.deepEqual(choices(ctx), [...LIBRARIES, "gamma"], "the older reply is dropped");
    assert.ok(screen(ctx, "library") && !screen(ctx, "library").classList.contains("hidden"));
  });
});
