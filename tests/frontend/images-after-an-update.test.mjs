/**
 * An image the page asked for while the server moved onto a new version failed -- 503
 * while it drained, nothing while it restarted -- and stayed broken until the page was
 * reloaded: `<img>` has no retry, and api.js's own retry is for fetch. Now a failed /api/
 * image makes the page ask how the server is; once it has seen the server away and then
 * answering again, each such image is asked for again (web/common/api.js).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { imagesAfterAnUpdate } from "../../web/common/api.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  delete globalThis.location;
  globalThis.fetch = realFetch;
});

function reply(status, headers = {}) {
  return { ok: status === 200, status, headers: { get: (name) => headers[name] ?? null }, json: () => Promise.resolve({}) };
}

function page(answers) {
  const dom = new JSDOM("<!doctype html><body></body>", { url: "http://localhost:8090/kr-track/" });
  globalThis.location = new URL("http://localhost:8090/kr-track/");
  const asked = [];
  globalThis.fetch = (url) => {
    asked.push(url);
    const next = answers.length > 1 ? answers.shift() : answers[0];
    return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
  };
  const document = dom.window.document;
  imagesAfterAnUpdate.watch(document, { every: 5 });
  return { dom, document, asked };
}

function image(document, src) {
  const img = document.createElement("img");
  img.src = src;
  document.body.append(img);
  img.dispatchEvent(new document.defaultView.Event("error"));
  return img;
}

const settle = (ms = 80) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Wait for `done()`, polling, at most 10 s. The watcher is one per module (api.js), so a test that left before its probe
 * ended lent its "the server was away" to the next test's image (#721): each test waits for its probe to end.
 */
async function until(done, what) {
  const end = Date.now() + 10000;
  while (!done()) {
    if (Date.now() > end) throw new Error(`waited 10 s for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  await new Promise((resolve) => setTimeout(resolve, 0));   // and what the answer's promise does with it
}

describe("an image that failed during an update", () => {
  test("is asked for again once the server answers again", async () => {
    const { dom, document, asked } = page([
      reply(503, { "X-TagPup-Updating": "1" }), new TypeError("Failed to fetch"), reply(200)]);
    const img = image(document, "/kr-track/api/face-crop?id=3");
    await until(() => img.getAttribute("src") !== "/kr-track/api/face-crop?id=3", "the image to be asked for again");
    assert.ok(asked.every((url) => url === "/kr-track/api/server"), asked.join(", "));
    assert.equal(asked.length, 3, "away (503), gone (nothing), back");
    assert.match(img.getAttribute("src"), /^\/kr-track\/api\/face-crop\?id=3&_again=\d+$/);
    dom.window.close();
  });

  test("but one that failed with the server there is left as it is", async () => {
    const { dom, document, asked } = page([reply(200)]);
    const img = image(document, "/kr-track/api/face-crop?id=4");
    await until(() => asked.length > 0, "the server to be asked");
    await settle();
    assert.equal(asked.length, 1, "the server answered: asked once");
    assert.equal(img.getAttribute("src"), "/kr-track/api/face-crop?id=4");
    dom.window.close();
  });

  test("and nothing that is not an /api/ image is looked at", async () => {
    const { dom, document, asked } = page([reply(503, { "X-TagPup-Updating": "1" }), reply(200)]);
    const img = image(document, "/icons/gear.png");
    await settle();
    assert.deepEqual([], asked);
    assert.equal(img.getAttribute("src"), "/icons/gear.png");
    dom.window.close();
  });
});
