/**
 * Making a library: the person is told "library" (never "database"), and the first load of the new library opens
 * Manage folders once, saying what to do next. A refused create, a later reload and a switch to an existing library
 * open nothing. Both pages (web/common/library.js, wired in each page's gear.js).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, click, flush } from "./harness.mjs";

const KEY = "tagpup.new-library";
const IDLE = { active: [], queued: [], busy: false, remaining: 0 };

afterEach(() => closeAllApps());

function serverFor(created = { success: true }) {
  return new FakeServer()
    .on("/api/databases/create", created)
    .on("/api/databases", { databases: ["alpha", "fresh_one"] })
    .on("/api/folder/index-active", IDLE)
    .on("/api/folder/indexed", { folders: [] })
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", []);
}

const modalOpen = (document) => {
  const modal = document.getElementById("folder-manager-modal");
  return !!modal && !modal.classList.contains("hidden");
};

for (const app of ["tagpup", "tagtuner"]) {
  describe(`${app}: a new library`, () => {
    test("creating one says library, remembers it for one load, and a refusal remembers nothing", async (t) => {
      const asked = [];
      const alerts = [];
      const ctx = await loadApp(app, { t, url: "http://localhost:8090/alpha/", server: serverFor() });
      ctx.window.prompt = (words) => { asked.push(words); return "fresh_one"; };
      ctx.window.alert = (words) => alerts.push(words);
      click(ctx.window, ctx.document.getElementById("btn-create-db"));
      await flush(ctx.window, 4);
      assert.match(asked[0], /new library/);
      assert.doesNotMatch(asked[0], /database/i);
      assert.equal(ctx.window.sessionStorage.getItem(KEY), "fresh_one");

      const refused = await loadApp(app, {
        t, url: "http://localhost:8090/alpha/", server: serverFor({ success: false, error: "name taken" }),
      });
      refused.window.prompt = () => "alpha2";
      refused.window.alert = (words) => alerts.push(words);
      click(refused.window, refused.document.getElementById("btn-create-db"));
      await flush(refused.window, 4);
      assert.equal(refused.window.sessionStorage.getItem(KEY), null);
      assert.match(alerts[0], /Error creating library: name taken/);
      assert.ok(!modalOpen(refused.document));
    });

    test("the new library opens Manage folders once; a reload does not", async (t) => {
      const before = (window) => window.sessionStorage.setItem(KEY, "fresh_one");
      const ctx = await loadApp(app, { t, url: "http://localhost:8090/fresh_one/", server: serverFor(), before });
      await flush(ctx.window, 4);
      assert.ok(modalOpen(ctx.document), "Manage folders did not open on the new library");
      assert.match(ctx.document.getElementById("folder-manager-modal").textContent, /Add the folders this library holds/);
      assert.equal(ctx.window.sessionStorage.getItem(KEY), null);

      // The same browser tab loading the library again: the mark is gone.
      const again = await loadApp(app, { t, url: "http://localhost:8090/fresh_one/", server: serverFor() });
      await flush(again.window, 4);
      assert.ok(!modalOpen(again.document));
    });

    test("an existing library, even with a mark left for another, opens nothing", async (t) => {
      const before = (window) => window.sessionStorage.setItem(KEY, "fresh_one");
      const ctx = await loadApp(app, { t, url: "http://localhost:8090/alpha/", server: serverFor(), before });
      await flush(ctx.window, 4);
      assert.ok(!modalOpen(ctx.document));
      assert.equal(ctx.window.sessionStorage.getItem(KEY), null);
    });
  });
}
