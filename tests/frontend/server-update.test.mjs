/**
 * The always-on process moves onto a new version between requests (docs/ARCHITECTURE.md,
 * phase 8, "Updating itself"; tagpup.web.lifecycle). While it drains, a request is
 * answered 503 with X-TagPup-Updating and nothing done with it; then, while the server
 * restarts, nothing answers. A page waits that out and sends the request again
 * (web/common/api.js), and its gear says which version answers (web/common/gear.js).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";
import { api } from "../../web/common/api.js";
import { versionText } from "../../web/common/gear.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  delete globalThis.location;
  globalThis.fetch = realFetch;
  closeAllApps();
});

function reply(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name] ?? null },
    json: () => Promise.resolve(body),
  };
}

/** A fetch answering each call with the next of `answers`: a reply, or an Error to throw. */
function scripted(answers) {
  const calls = [];
  globalThis.fetch = (url, options) => {
    calls.push({ url, method: (options && options.method) || "GET" });
    const next = answers.shift();
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
  };
  return calls;
}

const UPDATING = { "X-TagPup-Updating": "1", "Retry-After": "0" };

describe("api.js through an update", () => {
  test("a request turned away for an update is sent again, through the restart, until the new version answers", async () => {
    globalThis.location = new URL("http://localhost:8090/kr-track/");
    const calls = scripted([
      reply(503, { success: false, updating: true }, UPDATING),
      new TypeError("Failed to fetch"),   // the server restarting: nothing answers
      reply(200, { success: true, saved: 1 }),
    ]);
    const answer = await api.json("/api/photo/save", { method: "POST", body: "{}" });
    assert.deepEqual(answer, { success: true, saved: 1 });
    assert.equal(calls.length, 3);
    assert.ok(calls.every((c) => c.url === "/kr-track/api/photo/save" && c.method === "POST"));
  });

  test("any other 503 is the page's to read, as before", async () => {
    globalThis.location = new URL("http://localhost:8090/kr-track/");
    const calls = scripted([reply(503, { success: false, error: "busy" })]);
    const res = await api.fetch("/api/tags");
    assert.equal(res.status, 503);
    assert.equal(calls.length, 1);
  });

  test("nothing answering, with no update said, is an error after one more try", async () => {
    globalThis.location = new URL("http://localhost:8090/kr-track/");
    const calls = scripted([new TypeError("Failed to fetch"), new TypeError("Failed to fetch")]);
    await assert.rejects(api.fetch("/api/tags"), /Failed to fetch/);
    assert.equal(calls.length, 2);
  });

  test("a first read that meets a launch replacing the server gets the new one (#746)", async () => {
    // The old server ended and the new not yet listening: the second it takes to bind.
    globalThis.location = new URL("http://localhost:8090/kr-track/");
    const calls = scripted([new TypeError("Failed to fetch"), reply(200, { tags: ["Harbour"] })]);
    const answer = await api.json("/api/tags");
    assert.deepEqual(answer, { tags: ["Harbour"] });
    assert.deepEqual(calls.map((c) => c.method), ["GET", "GET"]);
  });

  test("a write nothing answered is not sent again: it may have been done (#746)", async () => {
    globalThis.location = new URL("http://localhost:8090/kr-track/");
    const calls = scripted([new TypeError("Failed to fetch"), reply(200, { success: true, saved: 1 })]);
    await assert.rejects(api.json("/api/photo/save", { method: "POST", body: "{}" }), /Failed to fetch/);
    assert.equal(calls.length, 1);
  });

  test("a failure that is not a connection's is not sent again", async () => {
    globalThis.location = new URL("http://localhost:8090/kr-track/");
    const calls = scripted([new Error("offline")]);
    await assert.rejects(api.fetch("/api/tags"), /offline/);
    assert.equal(calls.length, 1);
  });

  test("which version answers may be asked with no library open", async () => {
    globalThis.location = new URL("http://localhost:8090/");
    const calls = scripted([reply(200, { version: null })]);
    await api.json("/api/server");
    assert.deepEqual(calls.map((c) => c.url), ["/api/server"]);
  });
});

describe("the gear says which version answers", () => {
  test("its text, from /api/server", () => {
    assert.equal(versionText({ version: "20260926-101500-dc32868" }), "TagPup 20260926-101500-dc32868");
    assert.equal(versionText({ version: null }), "TagPup, run from its code folder");
    assert.equal(versionText([]), "");
  });

  test("below the items, and not one of them", async (t) => {
    const server = new FakeServer()
      .on("/api/server", { version: "20260926-101500-dc32868", supervised: true, taking_work: true })
      .on("/api/databases", { databases: ["kr-track"] })
      .on("/api/folder/suggest-status", { status: "idle" })
      .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" });
    const { window, document } = await loadApp("tagpup", { t, url: "http://localhost:8090/kr-track/", server });
    await flush(window, 6);
    const button = document.getElementById("btn-gear");
    const menu = document.getElementById("gear-menu");
    click(window, button);
    await flush(window, 4);
    const line = menu.querySelector(".gear-version");
    assert.ok(line, "the gear shows no version");
    assert.equal(line.textContent, "TagPup 20260926-101500-dc32868");
    assert.equal(line.getAttribute("role"), "none");
    assert.ok(![...menu.querySelectorAll('[role="menuitem"]')].includes(line));
    assert.ok(server.urls().includes("/kr-track/api/server"));
  });
});
