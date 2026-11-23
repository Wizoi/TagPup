/**
 * Organize's Suggested People for a selection, after a shoot is indexed and before any Suggest run: the people the
 * selected photos' unnamed faces look like (/api/photo-faces/suggestions, the strip's people at the strip's cut), counted
 * with what a Suggest run stored, a photo once a person. The owner selects all the photos of a folder, glances at the
 * suggested people, and clicks one to put them on the photos that suggested them.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, peopleRecordsOf, PEOPLE_ROUTE, until, TAGS, tagFiles } from "./harness.mjs";

const FOLDER = "D:/Library/2020";
const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Kira Bao", name: "Kira Bao", parent_id: 1, has_face: 1 },
  { id: 3, tag: "People/Rory Olwen", name: "Rory Olwen", parent_id: 1, has_face: 1 },
];
const KIRA = { id: 2, name: "Kira Bao", tag: "People/Kira Bao" };
const RORY = { id: 3, name: "Rory Olwen", tag: "People/Rory Olwen" };
const like = (person, similarity) => ({ person, name: person.name, similarity });
const ROUTE = "/api/photo-faces/suggestions";

/** `faces` answers the suggestions route: an object, or a function of the request's body. */
function build(photos, faces, suggest = { status: "idle" }) {
  const server = new FakeServer()
    .on("/api/tags", ["People/Kira Bao", "People/Rory Olwen"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .first(PEOPLE_ROUTE, peopleRecordsOf(TAXONOMY))
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", () => structuredClone(photos))   // the page's records are its own: a write changes them, not these
    .on(TAGS, tagFiles(photos))
    .on("/api/folder/suggest-status", suggest);
  server.first(ROUTE, () => (typeof faces === "function" ? faces(server.lastBody(ROUTE)) : { suggestions: faces }));
  return server;
}

async function openShoot(t, photos, faces, suggest) {
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server: build(photos, faces, suggest) });
  ctx.alerts = [];
  ctx.window.alert = (message) => ctx.alerts.push(message);
  await openFolder(ctx, FOLDER);
  ctx.asks = () => ctx.server.calls.filter((call) => call.url.includes(ROUTE));
  ctx.selectAll = async () => {
    click(ctx.window, ctx.document.getElementById("btn-select-all-thumbnails"));
    await flush(ctx.window, 6);
  };
  ctx.tick = async (path) => {
    const box = [...ctx.document.querySelectorAll(".thumbnail-checkbox")]
      .find((each) => each.closest("[data-path]").dataset.path === path);
    click(ctx.window, box);
    await flush(ctx.window, 6);
  };
  return ctx;
}

const suggested = (ctx) =>
  [...ctx.document.querySelectorAll("#selection-suggested-people-list .suggestion-chip")].map((chip) => chip.textContent);

describe("a shoot just indexed: the selection offers who its faces look like", () => {
  const photos = [
    photoRecord({ filename: "a.jpg" }),
    photoRecord({ filename: "b.jpg" }),
    photoRecord({ filename: "c.jpg", tags: ["People/Kira Bao"], people: ["Kira Bao"] }),
  ];
  const faces = {
    [photos[0].path]: [like(KIRA, 0.91)],
    [photos[1].path]: [like(KIRA, 0.82), like(RORY, 0.74)],
    [photos[2].path]: [like(KIRA, 0.95)],
  };

  test("all the folder selected: one ask, each person with the photos that lack them, and a click puts them there", async (t) => {
    const ctx = await openShoot(t, photos, faces);
    await ctx.selectAll();
    assert.equal(ctx.asks().length, 1);
    assert.deepEqual(ctx.asks()[0].body.paths.sort(), photos.map((p) => p.path).sort());
    assert.deepEqual(suggested(ctx).sort(), ["Kira Bao (2) · 91%", "Rory Olwen (1) · 74%"],
      "the photo already tagged with the person is not offered them");
    click(ctx.window, [...ctx.document.querySelectorAll("#selection-suggested-people-list .suggestion-chip")]
      .find((chip) => chip.textContent.startsWith("Kira Bao")));
    await flush(ctx.window, 6);
    const body = ctx.server.lastBody("/api/photos/write");
    assert.deepEqual(Object.keys(body.changes).sort(), [photos[0].path, photos[1].path].sort());
    for (const change of Object.values(body.changes)) assert.deepEqual(change, { id: null, add: ["People/Kira Bao"], remove: [] });
  });

  test("a photo a Suggest run and its faces both offer someone counts once", async (t) => {
    const ctx = await openShoot(t, photos.slice(0, 1), { [photos[0].path]: [like(KIRA, 0.91)] }, {
      status: "completed",
      suggestions: { [photos[0].path]: { people: [{ name: "Kira Bao", score: 0.8 }], tags: [{ tag: "People/Kira Bao", score: 0.8 }] } },
    });
    await ctx.selectAll();
    assert.deepEqual(suggested(ctx), ["Kira Bao (1) · 91%"]);
  });

  test("a chip says Apply All writes it only for the photos a Suggest run stored it for", async (t) => {
    const ctx = await openShoot(t, photos.slice(0, 2), {
      [photos[0].path]: [like(KIRA, 0.91)],
      [photos[1].path]: [like(KIRA, 0.82)],
    }, {
      status: "completed",
      suggestions: { [photos[0].path]: { people: [{ name: "Kira Bao", score: 0.8 }] } },
    });
    await ctx.selectAll();
    const title = () => ctx.document.querySelector("#selection-suggested-people-list .suggestion-chip").title;
    assert.match(title(), /Apply All writes only what Suggest stored, so it leaves out 1 of them/);
    await ctx.tick(photos[1].path);
    assert.match(title(), /or leave it for Apply All, which writes everything here/,
      "every photo of the chip is stored: Apply All writes all of them");
  });

  test("photos answered already are not asked again, and a new photo in the selection is asked alone", async (t) => {
    const ctx = await openShoot(t, photos, faces);
    await ctx.tick(photos[0].path);
    await ctx.tick(photos[1].path);
    await ctx.tick(photos[0].path);
    await ctx.tick(photos[0].path);
    assert.deepEqual(ctx.asks().map((call) => call.body.paths), [[photos[0].path], [photos[1].path]]);
  });
});

describe("how the ask fails", () => {
  test("the selection moves on while the ask is out: its late answer does not draw", async (t) => {
    const photos = [photoRecord({ filename: "a.jpg" }), photoRecord({ filename: "b.jpg" })];
    const late = [];
    const ctx = await openShoot(t, photos, (body) => {
      if (body.paths.includes(photos[0].path)) {
        return new Promise((resolve) => late.push(() => resolve({ suggestions: { [photos[0].path]: [like(KIRA, 0.9)] } })));
      }
      return { suggestions: { [photos[1].path]: [like(RORY, 0.8)] } };
    });
    await ctx.tick(photos[0].path);
    await ctx.tick(photos[0].path);   // off again: the selection is b alone
    await ctx.tick(photos[1].path);
    late.forEach((answer) => answer());
    await flush(ctx.window, 6);
    assert.deepEqual(suggested(ctx), ["Rory Olwen (1) · 80%"]);
    await ctx.tick(photos[0].path);
    assert.equal(ctx.asks().filter((call) => call.body.paths.includes(photos[0].path)).length, 2,
      "the photo whose answer came too late is asked again");
  });

  test("an ask that fails changes nothing on screen, alerts nobody, and is asked again with the next selection", async (t) => {
    const photos = [photoRecord({ filename: "a.jpg" }), photoRecord({ filename: "b.jpg" })];
    let failing = true;
    const ctx = await openShoot(t, photos, () => (failing ? Promise.reject(new TypeError("Failed to fetch"))
      : { suggestions: { [photos[0].path]: [like(KIRA, 0.9)] } }));
    await ctx.tick(photos[0].path);
    assert.deepEqual(suggested(ctx), []);
    assert.deepEqual(ctx.alerts, []);
    failing = false;
    await ctx.tick(photos[1].path);
    assert.deepEqual(ctx.asks().at(-1).body.paths.sort(), [photos[0].path, photos[1].path].sort());
    assert.deepEqual(suggested(ctx), ["Kira Bao (1) · 90%"]);
  });

  test("a server that refuses the ask is a failure too, not an answer of nobody", async (t) => {
    const photos = [photoRecord({ filename: "a.jpg" })];
    const ctx = await openShoot(t, photos, {});
    ctx.server.first(ROUTE, { error: "The library is busy." }, { status: 500 });
    await ctx.tick(photos[0].path);
    ctx.server.routes.shift();
    await ctx.tick(photos[0].path);
    await ctx.tick(photos[0].path);
    assert.equal(ctx.asks().length, 2, "the photo was kept as having no suggestion after a refusal");
  });

  test("three hundred photos selected are one ask; more than five hundred ask nothing", async (t) => {
    const many = Array.from({ length: 300 }, (_, n) => photoRecord({ filename: `r${String(n).padStart(3, "0")}.jpg` }));
    const ctx = await openShoot(t, many, {});
    await ctx.selectAll();
    assert.equal(ctx.asks().length, 1);
    assert.equal(ctx.asks()[0].body.paths.length, 300);

    const more = Array.from({ length: 501 }, (_, n) => photoRecord({ filename: `s${String(n).padStart(3, "0")}.jpg` }));
    const other = await openShoot(t, more, {});
    await other.selectAll();
    assert.equal(other.asks().length, 0);
  });
});

describe("a face of a selected photo named or left out", () => {
  test("the selection stops offering who the face looked like", async (t) => {
    const photo = photoRecord({ filename: "a.jpg" });
    const face = { id: 7, box: [400, 300, 800, 700], area: 160000, name: null, prob: 0.99, excluded: false,
      suggestion: "Kira Bao", suggestion_person: KIRA, similarity: 0.9, shown_box: [400, 300, 800, 700] };
    let left = false;
    const ctx = await openShoot(t, [photo], () => ({ suggestions: left ? {} : { [photo.path]: [like(KIRA, 0.9)] } }));
    ctx.server
      .on("/api/photo-faces", () => ({ faces: [{ ...face, excluded: left }], total: 1, unmatched: left ? 0 : 1,
        size: [4000, 3000], shown_size: [4000, 3000] }))
      .on("/api/faces/exclude", () => {
        left = true;
        return { success: true, excluded: 1, untag: {} };
      });
    ctx.document.querySelector("li[data-path]").click();
    await flush(ctx.window, 6);
    await ctx.tick(photo.path);
    assert.deepEqual(suggested(ctx), ["Kira Bao (1) · 90%"]);

    const image = ctx.document.getElementById("main-image");
    for (const [name, value] of [["clientWidth", 400], ["clientHeight", 300], ["offsetLeft", 20], ["offsetTop", 20]]) {
      Object.defineProperty(image, name, { value, configurable: true });
    }
    image.dispatchEvent(new ctx.window.Event("load"));
    const layer = ctx.document.getElementById("face-layer");
    click(ctx.window, layer.querySelector(".face-boxes-toggle"));
    click(ctx.window, layer.querySelector(".face-box"));
    await flush(ctx.window, 6);
    click(ctx.window, [...layer.querySelectorAll(".face-panel button")].find((b) => b.textContent === "Not important"));
    await until(ctx, () => suggested(ctx).length === 0, "the suggestion to go");
    assert.equal(ctx.asks().length, 2, "the photo was asked about again once its face was left out");
  });
});
