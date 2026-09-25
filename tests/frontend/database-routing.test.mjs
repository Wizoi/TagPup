/**
 * The library in every request, in both web apps (web/common/api.js).
 *
 * Every request the pages make must carry the database segment from the URL, or the
 * server resolves it against the startup database instead. This is the client half of
 * the multi-database defect that made the folder-indexing and suggestion buttons hang;
 * it had no coverage at all. The pages used to get it by replacing `fetch` and the
 * image `src` setter; now each request is built by api.js, and these tests hold it to
 * the same behaviour.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, openFolder, pageModules, APPS, REPO_ROOT } from "./harness.mjs";
import path from "node:path";
import { api, libraryIn } from "../../web/common/api.js";

/** api.js read on a page at `url`, as the page reads it: location, at each call. */
function onPage(url, server = new FakeServer()) {
  globalThis.location = new URL(url);
  server.install(globalThis);
  return server;
}

const realFetch = globalThis.fetch;
afterEach(() => {
  delete globalThis.location;
  globalThis.fetch = realFetch;
});

const baseRoutes = (server) =>
  server
    .on("/api/tags", ["Activity", "Trips/Texas"])
    .on("/api/people", ["Jane Doe"])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index", "kr-track"], selected: "kr-track" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" });

for (const app of ["tagpup", "tagtuner"]) {
  describe(`${app}: database prefix routing`, () => {
    test("API calls carry the database segment from the URL", async (t) => {
      const server = baseRoutes(new FakeServer());
      await loadApp(app, { t, url: "http://localhost:8090/kr-track/", server });

      const apiCalls = server.urls().filter((u) => u.startsWith("/"));
      assert.ok(apiCalls.length > 0, "the app made no absolute API calls");
      for (const url of apiCalls) {
        assert.ok(
          url.startsWith("/kr-track/api/"),
          `request escaped the database prefix: ${url}`
        );
      }
    });

    test("a different database in the URL routes elsewhere", async (t) => {
      const server = baseRoutes(new FakeServer());
      await loadApp(app, { t, url: "http://localhost:8090/photo_index/", server });

      for (const url of server.urls().filter((u) => u.startsWith("/"))) {
        assert.ok(
          url.startsWith("/photo_index/api/"),
          `request went to the wrong database: ${url}`
        );
      }
    });

    test("prefixes are never applied twice", async (t) => {
      const server = baseRoutes(new FakeServer());
      await loadApp(app, { t, url: "http://localhost:8090/kr-track/", server });

      for (const url of server.urls()) {
        assert.ok(
          !url.includes("/kr-track/kr-track/"),
          `prefix was applied twice: ${url}`
        );
      }
    });

    test("no request and no /api/ image is made but through api.js", () => {
      // What made the monkeypatches safe to delete: nothing left for them to catch.
      const offenders = [];
      for (const module of pageModules(path.join(REPO_ROOT, APPS[app].dir))) {
        if (module.url.endsWith("/common/api.js")) continue;
        module.body.split(/\r?\n/).forEach((line, i) => {
          if (line.trimStart().startsWith("//") || line.trimStart().startsWith("*")) return;
          if (/(?<!\bapi\.)\bfetch\(/.test(line) || /\.src\s*=\s*[`'"]\/?api\//.test(line)) {
            offenders.push(`${module.url}:${i + 1}: ${line.trim()}`);
          }
        });
      }
      assert.deepEqual(offenders, []);
    });
  });
}

describe("api.js: the library from the page's URL", () => {
  test("an API path is put under the page's library", () => {
    onPage("http://localhost:8090/kr-track/?path=D%3A%5CRun");
    assert.equal(api.url("/api/people"), "/kr-track/api/people");
    assert.equal(api.url("api/databases"), "/kr-track/api/databases");
    assert.equal(api.image("/api/face-crop?id=3"), "/kr-track/api/face-crop?id=3");
  });

  test("an already-prefixed request is left alone", async () => {
    const server = onPage("http://localhost:8090/kr-track/");
    assert.equal(api.url("/kr-track/api/people"), "/kr-track/api/people");
    await api.fetch("/kr-track/api/people");
    assert.equal(server.urls().at(-1), "/kr-track/api/people");
  });

  test("non-API requests are not rewritten", async () => {
    const server = onPage("http://localhost:8090/kr-track/");
    assert.equal(api.url("/style.css"), "/style.css");
    await api.fetch("/style.css");
    assert.equal(server.urls().at(-1), "/style.css");
  });

  test("the library is read at each call, and fetch looked up at each call", async () => {
    const server = onPage("http://localhost:8090/kr-track/");
    await api.fetch("/api/people");
    const later = new FakeServer().on("/api/tags", ["Activity"]);
    onPage("http://localhost:8090/photo_index/", later);
    assert.deepEqual(await api.json("/api/tags"), ["Activity"]);
    assert.deepEqual(server.urls(), ["/kr-track/api/people"]);
    assert.deepEqual(later.urls(), ["/photo_index/api/tags"]);
  });

  test("with no library only the picker is asked", async () => {
    const server = onPage("http://localhost:8090/");
    await assert.rejects(api.fetch("/api/tags"), /No library is open/);
    await assert.rejects(api.json("/api/people"), /No library is open/);
    await api.json("/api/databases");
    await api.fetch("/api/databases/create", { method: "POST" });
    assert.deepEqual(server.urls(), ["/api/databases", "/api/databases/create"]);
  });

  test("routes and files are not a library's name", () => {
    assert.equal(libraryIn("/kr-track/"), "kr-track");
    assert.equal(libraryIn("/"), "");
    assert.equal(libraryIn("/index.html"), "");
    assert.equal(libraryIn("/api/"), "");
    assert.equal(libraryIn("/common/api.js"), "");
    assert.equal(libraryIn("/photo_index/main.js"), "photo_index");
  });
});

describe("tagpup: image sources are routed too", () => {
  test("thumbnail src attributes carry the database prefix", async (t) => {
    const server = baseRoutes(new FakeServer()).on("/api/folder/scan", [
      photoRecord({ filename: "a.jpg" }),
      photoRecord({ filename: "b.jpg" }),
    ]);

    const { window, document } = await loadApp("tagpup", { t,
      url: "http://localhost:8090/kr-track/",
      server,
    });

    await openFolder({ document, window }, "D:\\Library\\2020", { settle: 6 });

    const images = [...document.querySelectorAll("img")].filter((img) =>
      (img.getAttribute("src") || "").includes("/api/photo-file")
    );
    assert.ok(images.length > 0, "no thumbnails were rendered");
    for (const img of images) {
      const src = img.getAttribute("src");
      assert.ok(
        src.startsWith("/kr-track/api/photo-file"),
        `image bypassed the database prefix: ${src}`
      );
      assert.ok(!src.includes("/kr-track/kr-track/"), `double prefix: ${src}`);
    }
  });
});

describe("tagpup: routing with no database segment", () => {
  test("requests are left unprefixed at the site root", async (t) => {
    const server = baseRoutes(new FakeServer());
    await loadApp("tagpup", { t, url: "http://localhost:8090/", server });

    // With no database segment the page asks only which libraries there are
    // (library-memory.test.mjs), and must not invent a prefix for that.
    for (const url of server.urls().filter((u) => u.startsWith("/"))) {
      assert.ok(
        url.startsWith("/api/"),
        `a prefix was invented where the URL had none: ${url}`
      );
    }
  });

  test("reserved path segments are not treated as a database name", async (t) => {
    const server = baseRoutes(new FakeServer());
    await loadApp("tagpup", { t, url: "http://localhost:8090/index.html", server });

    for (const url of server.urls().filter((u) => u.startsWith("/"))) {
      assert.ok(
        !url.startsWith("/index.html/"),
        `a reserved segment became a database prefix: ${url}`
      );
    }
  });
});
