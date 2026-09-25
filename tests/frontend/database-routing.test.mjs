/**
 * The URL database-prefix interceptor, in both web apps.
 *
 * Every request the pages make must carry the database segment from the URL, or the
 * server resolves it against the startup database instead. This is the client half of
 * the multi-database defect that made the folder-indexing and suggestion buttons hang;
 * it had no coverage at all.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, openFolder } from "./harness.mjs";

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

    test("an already-prefixed request is left alone", async (t) => {
      const server = baseRoutes(new FakeServer());
      const { window } = await loadApp(app, { t,
        url: "http://localhost:8090/kr-track/",
        server,
      });

      await window.fetch("/kr-track/api/people");
      const last = server.urls().at(-1);
      assert.equal(last, "/kr-track/api/people");
    });

    test("non-API requests are not rewritten", async (t) => {
      const server = baseRoutes(new FakeServer());
      const { window } = await loadApp(app, { t,
        url: "http://localhost:8090/kr-track/",
        server,
      });

      await window.fetch("/style.css");
      assert.equal(server.urls().at(-1), "/style.css");
    });
  });
}

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
