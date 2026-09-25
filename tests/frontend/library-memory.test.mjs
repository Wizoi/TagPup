/**
 * Which library a page opens, when its URL names none (docs/findings.md, #100).
 *
 * The server kept "the library chosen last" in config.ini and wrote that file on every
 * choice; a bare URL was sent to it. Now the browser remembers, and a bare URL goes to
 * the library it opened last -- if that library is still there -- or shows the picker
 * empty and asks. Until one is chosen, the page asks the server about libraries and
 * nothing else: every other route needs a library.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush } from "./harness.mjs";

const LIBRARY_KEY = "tagpup.library";

const libraries = (names) =>
  new FakeServer()
    .on("/api/databases", { databases: names })
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 });

const remember = (name) => (window) => window.localStorage.setItem(LIBRARY_KEY, name);

/** jsdom cannot navigate; it reports the attempt instead, which is what is asserted. */
const navigated = (consoleErrors) =>
  consoleErrors.some((e) => /navigation/i.test(e && e.message ? e.message : String(e)));

for (const app of ["tagpup", "tagtuner"]) {
  describe(`${app}: the library the browser remembers`, () => {
    test("the library in the URL is remembered", async (t) => {
      const { window } = await loadApp(app, {
        t, url: "http://localhost:8090/kr-track/", server: libraries(["kr-track", "photo_index"]),
      });
      assert.equal(window.localStorage.getItem(LIBRARY_KEY), "kr-track");
    });

    test("a bare URL goes to the remembered library", async (t) => {
      const { consoleErrors } = await loadApp(app, {
        t, url: "http://localhost:8090/", server: libraries(["kr-track", "photo_index"]),
        before: remember("kr-track"),
      });
      assert.ok(navigated(consoleErrors), "the page did not navigate to the remembered library");
    });

    test("a remembered library that is gone is forgotten, and the picker asks", async (t) => {
      const { window, document, consoleErrors } = await loadApp(app, {
        t, url: "http://localhost:8090/", server: libraries(["photo_index"]),
        before: remember("gone"),
      });
      await flush(window);
      assert.ok(!navigated(consoleErrors), "the page navigated to a library that is not there");
      assert.equal(window.localStorage.getItem(LIBRARY_KEY), null);
      const select = document.getElementById("db-select");
      assert.equal(select.value, "");
      assert.match(select.options[0].textContent, /Choose a library/);
      assert.ok(select.options[0].disabled);
      assert.deepEqual([...select.options].slice(1).map((o) => o.value), ["photo_index"]);
    });

    test("with no library, the page asks the server about libraries only", async (t) => {
      const { server } = await loadApp(app, {
        t, url: "http://localhost:8090/", server: libraries(["photo_index"]),
      });
      const asked = server.urls().filter((u) => u.includes("api/"));
      assert.ok(asked.length > 0, "the page never asked for the libraries");
      for (const url of asked) {
        assert.match(url, /^\/?api\/databases(?:[/?]|$)/, `asked for more than the libraries: ${url}`);
      }
    });

    test("choosing a library goes to it without asking the server to remember", async (t) => {
      const { window, document, server, consoleErrors } = await loadApp(app, {
        t, url: "http://localhost:8090/kr-track/", server: libraries(["kr-track", "photo_index"]),
      });
      if (window.confirm) window.confirm = () => true;
      const select = document.getElementById("db-select");
      select.value = "photo_index";
      select.dispatchEvent(new window.Event("change", { bubbles: true }));
      await flush(window);
      assert.ok(navigated(consoleErrors), "the page did not go to the chosen library");
      assert.ok(
        !server.urls().some((u) => u.includes("api/databases/select")),
        "the page asked the server to remember the choice"
      );
    });
  });
}
