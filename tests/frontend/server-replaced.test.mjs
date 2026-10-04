/**
 * A page left open while its server is replaced by another version (a launch of a newer
 * one: tagpup/launcher.py) says so, rather than running its old code against the new
 * server unnoticed (docs/findings.md, #726). Every response names the version answering
 * (X-TagPup-Version); the first naming another than the page's first did shows the banner
 * (web/common/api.js, web/common/roots-banner.js). The page is not reloaded for the owner:
 * an unsaved edit would go with it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const OLD = "20261004-100000-aaaaaaa";
const NEW = "20261004-110000-bbbbbbb";

function server(version) {
  const said = { headers: { "X-TagPup-Version": version } };
  return new FakeServer()
    .on("/api/server", { version, supervised: false, taking_work: true }, said)
    .on("/api/databases", { databases: ["kr-track"] }, said)
    .on("/api/folder/suggest-status", { status: "idle" }, said)
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" }, said);
}

async function openOn(t, fake) {
  const app = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server: fake });
  await flush(app.window, 6);
  return app;
}

describe("a page whose server was replaced", () => {
  test("says so once, in the banner, when a response names another version", async (t) => {
    const fake = server(OLD);
    const { window, document } = await openOn(t, fake);
    const banner = document.getElementById("roots-banner");
    assert.ok(banner.classList.contains("hidden"), "a banner before anything changed");

    // The gear asks /api/server: the new version answers it.
    fake.first("/api/server", { version: NEW, supervised: false, taking_work: true },
      { headers: { "X-TagPup-Version": NEW } });
    click(window, document.getElementById("btn-gear"));
    await flush(window, 6);
    assert.ok(!banner.classList.contains("hidden"), "the page did not notice the new version");
    assert.match(banner.textContent, /TagPup was updated while this page was open/);
    assert.match(banner.textContent, new RegExp("Reload this page to use it \\(" + NEW + "\\)"));
  });

  test("the same version throughout shows nothing", async (t) => {
    const fake = server(OLD);
    const { window, document } = await openOn(t, fake);
    click(window, document.getElementById("btn-gear"));
    await flush(window, 6);
    assert.ok(document.getElementById("roots-banner").classList.contains("hidden"));
  });

  test("one run from a checkout replaced by an installed version is told too", async (t) => {
    const fake = server("checkout");
    const { window, document } = await openOn(t, fake);
    fake.first("/api/server", { version: NEW }, { headers: { "X-TagPup-Version": NEW } });
    click(window, document.getElementById("btn-gear"));
    await flush(window, 6);
    assert.ok(!document.getElementById("roots-banner").classList.contains("hidden"));
  });
});
