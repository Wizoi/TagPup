/**
 * #791: the faces on the open photo, in TagPup's Organize.
 *
 * A face icon on the photo, when faces were found in it, shows a box over each; a click on a box opens a
 * panel to name that face, from who it looks like (with how much) or by a name typed, which names the face
 * AND puts the person on the photo, in that order, so that the tag and the face agree -- and taking a name off, or
 * ruling the face out, takes the person off the photo unless another face is them (#861). The boxes are in the
 * file's stored pixels and are placed by the geometry the zoom uses. jsdom has no layout, so the picture's
 * size is given to it, as a browser would measure it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";
import { boxInContainedImage } from "../../web/common/image-zoom.js";

afterEach(() => closeAllApps());

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Hazel Brookmire", name: "Hazel Brookmire", parent_id: 1, has_face: 1 },
  { id: 3, tag: "People/Anh Tran", name: "Anh Tran", parent_id: 1, has_face: 1 },
  { id: 4, tag: "Trips", name: "Trips", parent_id: null, has_face: 0 },
];

const face = (id, box, extra = {}) => ({
  id, box, area: (box[2] - box[0]) * (box[3] - box[1]), name: null, prob: 0.99, excluded: false,
  suggestion: null, similarity: null, ...extra,
});

/** Three faces on a 4000 x 3000 photo, none named. */
const THREE = {
  faces: [face(1, [400, 300, 800, 700]), face(2, [1000, 500, 1400, 900]), face(3, [2000, 600, 2600, 1200])],
  total: 3, unmatched: 3, size: [4000, 3000], turned: false,
};

const MATCHES = [
  { name: "Hazel Brookmire", similarity: 0.91, band: "likely" },
  { name: "Anh Tran", similarity: 0.72, band: "possible" },
];

function serverFor({ photoFaces = THREE, tree = TAXONOMY, matches = MATCHES, photo = null, extra = null, busy = { on: false } } = {}) {
  const shown = photo || photoRecord({ filename: "a.jpg", tags: ["Trips"], people: [] });
  // The faces as the server holds them: a write changes what the next read says.
  const held = structuredClone(photoFaces);
  const written = (server) => server.calls[server.calls.length - 1].body;
  // What the server holds of the photo's keywords: its own saves change it, and it says which tags go with a face (face_people).
  let keywords = [...shown.tags];
  const untag = (name) => (held.faces.some((other) => other.name === name) ? {}
    : { [shown.path]: keywords.filter((tag) => tag.endsWith("/" + name)) });
  const server = new FakeServer()
    .on("/api/tags", ["Trips", "People/Hazel Brookmire", "People/Anh Tran"])
    .on("/api/people", ["Hazel Brookmire", "Anh Tran"])
    .on("/api/taxonomy/tree", tree)
    .on("/api/taxonomy/create", { success: true })
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100 })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/scan", [shown])
    .on("/api/photo-faces", () => ({    // a fresh answer each time
      ...structuredClone(held), unmatched: held.faces.filter((f) => !f.name && !f.excluded).length,
    }))
    .on("/api/photo/save-metadata", () => {
      keywords = [...written(server).tags];
      return { success: true };
    })
    .on("/api/face/match", () => {
      if (busy.on) return Promise.reject(new Error("the library is busy"));
      const { face_id: id, person_name: name } = written(server);
      const was = held.faces.find((f) => f.id === id).name;
      held.faces.find((f) => f.id === id).name = name;
      // A face that was another person's: the server says which tags go with that name (face_people.name_face).
      return { success: true, changed: 1, untag: was && was !== name ? untag(was) : {} };
    })
    .on("/api/face/unmatch", () => {
      const was = held.faces.find((f) => f.id === written(server).face_id);
      const name = was.name;
      was.name = null;
      return { success: true, changed: name ? 1 : 0, untag: name ? untag(name) : {} };
    })
    .on("/api/faces/exclude", () => {
      const names = [];
      for (const id of written(server).face_ids) {
        const was = held.faces.find((f) => f.id === id);
        if (was.name) names.push(was.name);
        Object.assign(was, { excluded: true, name: null });
      }
      return { success: true, excluded: 1, untag: Object.assign({}, ...names.map(untag)) };
    })
    .first("/api/face-matches", () => structuredClone(matches))
    .first("/api/people-face-samples", { "Hazel Brookmire": [11, 12], "Anh Tran": [21] });
  if (extra) extra(server);
  server.shown = shown;
  return server;
}

/** The photo open, its picture "measured" at `view` and the boxes' layer drawn over it. */
async function openPhoto(t, options = {}) {
  const { view = [400, 300], ...rest } = options;
  const server = serverFor(rest);
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server });
  ctx.window.alert = () => {};
  await openFolder(ctx, "D:/Library/2020");
  const row = ctx.document.querySelector("li[data-path]");
  if (row) row.click();
  ctx.server = server;
  ctx.$ = (id) => ctx.document.getElementById(id);
  const image = ctx.$("main-image");
  const measure = (width, height) => {
    for (const [name, value] of [["clientWidth", width], ["clientHeight", height], ["offsetLeft", 20], ["offsetTop", 20]]) {
      Object.defineProperty(image, name, { value, configurable: true });
    }
  };
  ctx.measure = measure;
  measure(...view);
  await flush(ctx.window, 8);
  image.dispatchEvent(new ctx.window.Event("load"));
  ctx.layer = () => ctx.$("face-layer");
  ctx.icon = () => ctx.layer().querySelector(".face-boxes-toggle");
  ctx.boxes = () => [...ctx.layer().querySelectorAll(".face-box")];
  ctx.panel = () => ctx.layer().querySelector(".face-panel");
  ctx.posts = (route) => server.calls.filter((c) => c.method === "POST" && c.url.includes(route));
  ctx.wait = (ms) => new Promise((resolve) => ctx.window.setTimeout(resolve, ms));
  ctx.key = (target, key, extra = {}) => {
    const event = new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...extra });
    target.dispatchEvent(event);
    return event;
  };
  ctx.show = () => click(ctx.window, ctx.icon());
  return ctx;
}

const visible = (el) => Boolean(el) && !el.classList.contains("hidden");
const px = (el, property) => parseFloat(el.style[property]);

describe("the face icon", () => {
  test("is not offered on a photo with no faces", async (t) => {
    const ctx = await openPhoto(t, { photoFaces: { faces: [], total: 0, unmatched: 0, size: [4000, 3000], turned: false } });
    assert.equal(visible(ctx.layer()), false);
    assert.equal(ctx.icon(), null);
  });

  test("says how many faces, and boxes appear only when it is pressed", async (t) => {
    const ctx = await openPhoto(t);
    assert.equal(visible(ctx.layer()), true);
    assert.match(ctx.icon().textContent, /3/);
    assert.equal(ctx.icon().getAttribute("aria-pressed"), "false");
    assert.match(ctx.icon().getAttribute("aria-label"), /3 faces found, 3 not named/);
    assert.equal(ctx.boxes().length, 0);
    ctx.show();
    assert.equal(ctx.icon().getAttribute("aria-pressed"), "true");
    assert.equal(ctx.boxes().length, 3);
    ctx.show();
    assert.equal(ctx.boxes().length, 0);
  });

  test("is a button the keyboard reaches, and takes Enter", async (t) => {
    const ctx = await openPhoto(t);
    assert.equal(ctx.icon().tagName, "BUTTON");
    assert.ok(ctx.icon().tabIndex >= 0);
    ctx.icon().focus();
    ctx.icon().click();                        // what Enter and Space do to a button
    assert.equal(ctx.boxes().length, 3);
    assert.equal(ctx.document.activeElement, ctx.icon(), "the focus was lost when the boxes were drawn");
  });

  test("is not offered while just looking: no faces are recorded in a folder the library does not hold", async (t) => {
    const folder = "D:/Library/2020";
    const ctx = await openPhoto(t, {
      extra: (server) => server.on("/api/folder/membership", {
        library: "kr-track", folder, photos: 1, photos_held: 0, photos_not_held: 1, folders_not_held: 1,
        first_not_held: folder, has_roots: true, under_roots: false, ignored: false,
      }),
    });
    click(ctx.window, ctx.$("btn-just-look"));
    await flush(ctx.window, 6);
    assert.ok(ctx.document.body.classList.contains("just-looking"));
    assert.equal(visible(ctx.layer()), false);
  });

  test("leaves a face that was ruled out unboxed, and counts the rest", async (t) => {
    const ctx = await openPhoto(t, {
      photoFaces: { ...THREE, faces: [THREE.faces[0], { ...THREE.faces[1], excluded: true }, THREE.faces[2]] },
    });
    assert.match(ctx.icon().textContent, /2/);
    ctx.show();
    assert.deepEqual(ctx.boxes().map((b) => b.dataset.faceId), ["1", "3"]);
  });

  test("keeps its boxes on from one photo to the next", async (t) => {
    const second = photoRecord({ path: "D:/Library/2020/b.jpg", filename: "b.jpg" });
    const ctx = await openPhoto(t, { extra: (server) => server.on("/api/folder/scan", [server.shown, second]) });
    ctx.show();
    assert.equal(ctx.boxes().length, 3);
    ctx.key(ctx.document.body, "ArrowDown");
    await flush(ctx.window, 8);
    ctx.$("main-image").dispatchEvent(new ctx.window.Event("load"));
    assert.equal(ctx.boxes().length, 3, "the toggle was forgotten with the photo");
  });
});

describe("the boxes", () => {
  test("sit where the stored pixels put them on the picture as shown", async (t) => {
    const ctx = await openPhoto(t);               // 4000 x 3000 shown 400 x 300: a tenth
    ctx.show();
    const [first, second] = ctx.boxes();
    assert.deepEqual([px(first, "left"), px(first, "top"), px(first, "width"), px(first, "height")], [40, 30, 40, 40]);
    assert.deepEqual([px(second, "left"), px(second, "top"), px(second, "width"), px(second, "height")], [100, 50, 40, 40]);
    assert.equal(px(ctx.layer(), "left"), 20, "the layer is not over the picture");
    assert.equal(px(ctx.layer(), "width"), 400);
  });

  test("follow the picture when it changes size", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    ctx.measure(800, 600);
    ctx.window.dispatchEvent(new ctx.window.Event("resize"));
    assert.deepEqual([px(ctx.boxes()[1], "left"), px(ctx.boxes()[1], "width")], [200, 80]);
  });

  test("are not drawn when the size of the pixels they are in is not known", async (t) => {
    const ctx = await openPhoto(t, { photoFaces: { ...THREE, size: null } });
    ctx.show();
    assert.equal(ctx.boxes().length, 0, "a box was drawn on a guess");
    assert.equal(visible(ctx.icon()), true);
  });

  test("a photo stored turned draws them where the stored pixels put them, and the panel says they may be off", async (t) => {
    // The known limit (the tabled orientation redesign): the picture is shown turned, the boxes are not.
    const ctx = await openPhoto(t, { photoFaces: { ...THREE, size: [4000, 3000], turned: true } });
    ctx.show();
    assert.equal(px(ctx.boxes()[1], "left"), 100, "the box moved with the picture: that is the redesign, not this");
    click(ctx.window, ctx.boxes()[1]);
    assert.match(ctx.panel().textContent, /stored turned/);
    assert.match(ctx.panel().textContent, /may not sit on the face/);
    await flush(ctx.window, 4);                // the suggestions it asked for arrive while the page is still there
  });

  test("name the person on a named face, and say so to a screen reader", async (t) => {
    const ctx = await openPhoto(t, {
      photoFaces: { ...THREE, faces: [{ ...THREE.faces[0], name: "Hazel Brookmire" }, THREE.faces[1], THREE.faces[2]], unmatched: 2 },
    });
    ctx.show();
    const [named, unnamed] = ctx.boxes();
    assert.match(named.getAttribute("aria-label"), /Face 1 of 3: Hazel Brookmire/);
    assert.match(named.textContent, /Hazel Brookmire/);
    assert.match(unnamed.getAttribute("aria-label"), /Face 2 of 3: not named/);
    assert.equal(unnamed.tagName, "BUTTON");
  });
});

describe("the panel of a face", () => {
  test("opens on a click with who it looks like, and how much", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    const panel = ctx.panel();
    assert.ok(panel, "no panel opened");
    assert.equal(panel.getAttribute("role"), "dialog");
    assert.match(panel.querySelector(".face-panel-crop").src, /api\/face-crop\?id=2/);
    const asked = ctx.server.urls().filter((u) => u.includes("/api/face-matches"));
    assert.deepEqual(asked.map((u) => new URL(u, "http://x/").searchParams.get("id")), ["2"]);
    const offered = [...panel.querySelectorAll(".face-panel-suggestion")];
    assert.deepEqual(offered.map((b) => b.textContent), ["Hazel Brookmire91%", "Anh Tran72%"]);
    assert.ok(offered[0].classList.contains("is-likely"));
    assert.ok(offered[1].classList.contains("is-possible"));
    assert.equal(ctx.boxes()[1].getAttribute("aria-expanded"), "true");
  });

  test("does not offer a person another face of the photo already is", async (t) => {
    const ctx = await openPhoto(t, {
      photoFaces: { ...THREE, faces: [{ ...THREE.faces[0], name: "Hazel Brookmire" }, THREE.faces[1], THREE.faces[2]], unmatched: 2 },
    });
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    assert.deepEqual([...ctx.panel().querySelectorAll(".face-panel-suggestion")].map((b) => b.textContent), ["Anh Tran72%"]);
  });

  test("shows the person's faces on hover, from the one component both pages use", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    ctx.panel().querySelector(".face-panel-suggestion").dispatchEvent(new ctx.window.MouseEvent("mouseenter"));
    await ctx.wait(190);
    const popup = ctx.$("person-faces-popup");
    assert.ok(visible(popup));
    assert.equal(popup.querySelectorAll("img").length, 2);
  });

  test("says when the suggestions cannot be had, and still lets a name be typed", async (t) => {
    const ctx = await openPhoto(t, { extra: (server) => server.first("/api/face-matches", () => Promise.reject(new Error("down"))) });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 6);
    assert.match(ctx.panel().querySelector(".face-panel-suggestions").textContent, /could not be loaded/);
    assert.ok(ctx.panel().querySelector(".face-panel-input"));
  });

  test("closes on Escape and gives the focus back to its box; it does not trap the keyboard", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    const box = ctx.boxes()[1];
    box.focus();
    box.click();
    await flush(ctx.window, 4);
    assert.ok(ctx.panel());
    assert.equal(ctx.document.activeElement.className, "face-panel-input", "the keyboard was not taken into the panel");
    const tab = ctx.key(ctx.document.activeElement, "Tab");
    assert.equal(tab.defaultPrevented, false, "Tab was held in the panel");
    for (const element of ctx.panel().querySelectorAll("[tabindex]")) assert.ok(element.tabIndex <= 0, "a positive tab stop");
    assert.ok(ctx.panel().closest("[data-own-keys]") || ctx.panel().hasAttribute("data-own-keys"),
      "the arrows would step to another photo from inside the panel");
    ctx.key(ctx.document.activeElement, "Escape");
    assert.equal(ctx.panel(), null);
    assert.equal(ctx.document.activeElement, ctx.boxes()[1]);
  });

  test("closes when something else on the page is pressed", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    ctx.document.body.dispatchEvent(new ctx.window.Event("pointerdown", { bubbles: true }));
    assert.equal(ctx.panel(), null);
  });

  test("keeps what is being typed when the picture is drawn again", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    const input = ctx.panel().querySelector(".face-panel-input");
    input.focus();
    input.value = "Anh T";
    input.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    ctx.window.dispatchEvent(new ctx.window.Event("resize"));
    assert.equal(ctx.panel().querySelector(".face-panel-input").value, "Anh T");
    assert.equal(ctx.document.activeElement, ctx.panel().querySelector(".face-panel-input"));
    await flush(ctx.window, 4);
  });

  test("another box replaces the open panel", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    click(ctx.window, ctx.boxes()[2]);
    assert.equal(ctx.layer().querySelectorAll(".face-panel").length, 1);
    assert.match(ctx.panel().querySelector(".face-panel-crop").src, /id=3/);
    await flush(ctx.window, 4);
  });
});

describe("naming a face names the person on the photo too", () => {
  test("a suggestion: the tag is written first, then the face, and both agree afterwards", async (t) => {
    // The owner's scenario: a photo with three faces and one tag; name one face from its box.
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    const before = ctx.server.calls.length;
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    await flush(ctx.window, 14);
    const sent = ctx.server.calls.slice(before).filter((c) => c.method === "POST").map((c) => c.url.replace(/^.*\/api\//, ""));
    assert.deepEqual(sent, ["photo/save-metadata", "face/match"], "the tag first, then the face");
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["Trips", "People/Hazel Brookmire"]);
    assert.deepEqual(ctx.posts("/api/face/match")[0].body, { face_id: 2, person_name: "Hazel Brookmire", page_writes_tags: true });
    assert.equal(ctx.panel(), null, "the panel stayed open after the face was named");
    assert.match(ctx.boxes()[1].getAttribute("aria-label"), /Face 2 of 3: Hazel Brookmire/);
    assert.match(ctx.$("detail-people").textContent, /Hazel Brookmire/, "the photo's people do not show the tag");
    assert.match(ctx.$("status-text").textContent, /Named Hazel Brookmire/);
    const strips = ctx.server.urls().filter((u) => u.includes("/api/photo-faces"));
    assert.ok(strips.length >= 2, "the strip under the photo was not asked again");
    assert.equal(ctx.document.activeElement, ctx.boxes()[1]);
  });

  test("a person already on the photo is not written again: only the face is named", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Hazel Brookmire"], people: ["Hazel Brookmire"] });
    const ctx = await openPhoto(t, { photo });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    await flush(ctx.window, 10);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 0);
    assert.equal(ctx.posts("/api/face/match").length, 1);
  });

  test("a person only a face names is not on the photo: the tag is written", async (t) => {
    // photo.people lists a person a face names (source face); the keywords are what the tag is (#835).
    const photo = photoRecord({ filename: "a.jpg", tags: ["Trips"], people: ["Hazel Brookmire"] });
    const ctx = await openPhoto(t, { photo });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    await flush(ctx.window, 14);
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["Trips", "People/Hazel Brookmire"]);
    assert.equal(ctx.posts("/api/face/match").length, 1);
    assert.match(ctx.$("status-text").textContent, /Named Hazel Brookmire/);
  });

  test("a typed name that the tree holds: Enter does the same", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[2]);
    await flush(ctx.window, 4);
    const input = ctx.panel().querySelector(".face-panel-input");
    assert.equal(input.getAttribute("list"), "people-datalist", "no autocomplete from the vocabulary");
    input.value = "Anh Tran";
    ctx.key(input, "Enter");
    await flush(ctx.window, 14);
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["Trips", "People/Anh Tran"]);
    assert.deepEqual(ctx.posts("/api/face/match")[0].body, { face_id: 3, person_name: "Anh Tran", page_writes_tags: true });
  });

  test("a typed new name goes through the existing placement: created in the tree, then written, then the face", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    const input = ctx.panel().querySelector(".face-panel-input");
    input.value = "Imogen Vale";
    click(ctx.window, ctx.panel().querySelector(".face-panel-name"));
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/taxonomy/create")[0].body.name, "People/Imogen Vale");
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["Trips", "People/Imogen Vale"]);
    assert.deepEqual(ctx.posts("/api/face/match")[0].body, { face_id: 1, person_name: "Imogen Vale", page_writes_tags: true });
  });

  test("a placement answered Cancel adds no tag and names no face", async (t) => {
    const two = [...TAXONOMY, { id: 5, tag: "Family", name: "Family", parent_id: null, has_face: 1 }];
    const ctx = await openPhoto(t, { tree: two });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    ctx.panel().querySelector(".face-panel-input").value = "Imogen Vale";
    click(ctx.window, ctx.panel().querySelector(".face-panel-name"));
    await flush(ctx.window, 8);
    const modal = ctx.document.querySelector(".modal-overlay.active");
    assert.ok(modal, "the placement question did not open");
    click(ctx.window, modal.querySelector(".btn-cancel"));
    await flush(ctx.window, 10);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 0);
    assert.equal(ctx.posts("/api/face/match").length, 0, "a face was named for a person who is not on the photo");
    assert.match(ctx.panel().textContent, /not added to the photo, so the face is not named/);
  });

  test("a save that fails names no face", async (t) => {
    const ctx = await openPhoto(t, { extra: (server) => server.first("/api/photo/save-metadata", { success: false, error: "locked" }) });
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/face/match").length, 0);
    assert.equal(ctx.boxes()[1].getAttribute("aria-label").includes("Hazel"), false);
  });

  test("a face the server refuses to name says why, keeps the tag, and the same choice names it", async (t) => {
    const busy = { on: true };
    const ctx = await openPhoto(t, { busy });
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    await flush(ctx.window, 14);
    assert.match(ctx.panel().querySelector(".face-panel-message").textContent, /is on the photo, but the face could not be named/);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 1);
    busy.on = false;
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 1, "the tag was written a second time");
    assert.equal(ctx.panel(), null);
    assert.match(ctx.boxes()[1].getAttribute("aria-label"), /Hazel Brookmire/);
  });

  test("two rapid clicks name once", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    const button = ctx.panel().querySelector(".face-panel-suggestion");
    click(ctx.window, button);
    click(ctx.window, button);
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 1);
    assert.equal(ctx.posts("/api/face/match").length, 1);
  });

  test("leaving for another photo meanwhile still names the face of the photo it was chosen on", async (t) => {
    const second = photoRecord({ path: "D:/Library/2020/b.jpg", filename: "b.jpg" });
    const ctx = await openPhoto(t, { extra: (server) => server.on("/api/folder/scan", [server.shown, second]) });
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    click(ctx.window, ctx.panel().querySelector(".face-panel-suggestion"));
    ctx.key(ctx.document.body, "ArrowDown");       // on to the next photo before the save is over
    await flush(ctx.window, 16);
    assert.equal(ctx.posts("/api/face/match").length, 1, "the tag was written and the face left unnamed");
    assert.equal(ctx.posts("/api/face/match")[0].body.face_id, 2);
  });
});

describe("taking a name off, and ruling a face out", () => {
  const NAMED = { ...THREE, faces: [{ ...THREE.faces[0], name: "Hazel Brookmire" }, THREE.faces[1], THREE.faces[2]], unmatched: 2 };

  const notThisPerson = (ctx) => [...ctx.panel().querySelectorAll("button")].find((b) => b.textContent === "Not this person");

  test("Not this person takes the name off the face and the person off the photo, the face first", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["Trips", "People/Hazel Brookmire"], people: ["Hazel Brookmire"] });
    const ctx = await openPhoto(t, { photoFaces: NAMED, photo });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    click(ctx.window, notThisPerson(ctx));
    await flush(ctx.window, 12);
    assert.deepEqual(ctx.posts("/api/face/unmatch")[0].body, { face_id: 1, page_writes_tags: true });
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["Trips"], "the person's tag stayed on the photo");
    const order = ctx.server.calls.map((c) => c.url).filter((u) => /face\/unmatch|save-metadata/.test(u));
    assert.match(order[0], /face\/unmatch/, "the face goes first, the tag second");
    assert.match(ctx.boxes()[0].getAttribute("aria-label"), /not named/);
    assert.doesNotMatch(ctx.$("detail-people").textContent, /Hazel Brookmire/);
  });

  test("the tag stays when the server says another face is the person", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Hazel Brookmire"], people: ["Hazel Brookmire"] });
    const both = { ...NAMED, faces: [NAMED.faces[0], { ...NAMED.faces[1], name: "Hazel Brookmire" }, NAMED.faces[2]], unmatched: 1 };
    const ctx = await openPhoto(t, { photoFaces: both, photo });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    click(ctx.window, notThisPerson(ctx));
    await flush(ctx.window, 12);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 0, "the person is still on another face");
    assert.match(ctx.$("detail-people").textContent, /Hazel Brookmire/);
  });

  test("a tag that cannot be taken off is said, and the face stays unnamed", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Hazel Brookmire"], people: ["Hazel Brookmire"] });
    const ctx = await openPhoto(t, {
      photoFaces: NAMED, photo,
      extra: (server) => server.first("/api/photo/save-metadata", { success: false, error: "locked" }),
    });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    click(ctx.window, notThisPerson(ctx));
    await flush(ctx.window, 12);
    assert.match(ctx.boxes()[0].getAttribute("aria-label"), /not named/);
    assert.match(ctx.$("status-text").textContent, /tag could not be taken off the photo/);
  });

  test("naming a face that was another person's takes the old person off the photo as well", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["Trips", "People/Hazel Brookmire"], people: ["Hazel Brookmire"] });
    const ctx = await openPhoto(t, { photoFaces: NAMED, photo });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    const input = ctx.panel().querySelector(".face-panel-input");
    input.value = "Anh Tran";
    click(ctx.window, ctx.panel().querySelector(".face-panel-name"));
    await flush(ctx.window, 16);
    const saves = ctx.posts("/api/photo/save-metadata").map((c) => c.body.tags);
    assert.deepEqual(saves[0], ["Trips", "People/Hazel Brookmire", "People/Anh Tran"], "the new person first");
    assert.deepEqual(saves[1], ["Trips", "People/Anh Tran"], "then the old person off");
    assert.match(ctx.boxes()[0].getAttribute("aria-label"), /Anh Tran/);
  });

  test("an unnamed face has nothing to take off", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    assert.equal([...ctx.panel().querySelectorAll("button")].some((b) => b.textContent === "Not this person"), false);
    await flush(ctx.window, 4);
  });

  test("Not important rules the face out and its box goes", async (t) => {
    const ctx = await openPhoto(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[2]);
    await flush(ctx.window, 4);
    const out = [...ctx.panel().querySelectorAll("button")].find((b) => b.textContent === "Not important");
    click(ctx.window, out);
    await flush(ctx.window, 8);
    assert.deepEqual(ctx.posts("/api/faces/exclude")[0].body, { face_ids: [3], page_writes_tags: true });
    assert.deepEqual(ctx.boxes().map((b) => b.dataset.faceId), ["1", "2"]);
    assert.match(ctx.icon().textContent, /2/);
    assert.equal(ctx.panel(), null);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 0, "an unnamed face had no person to take off");
  });

  test("Not important on a named face takes the person off the photo as well", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["Trips", "People/Hazel Brookmire"], people: ["Hazel Brookmire"] });
    const ctx = await openPhoto(t, { photoFaces: NAMED, photo });
    ctx.show();
    click(ctx.window, ctx.boxes()[0]);
    await flush(ctx.window, 4);
    click(ctx.window, [...ctx.panel().querySelectorAll("button")].find((b) => b.textContent === "Not important"));
    await flush(ctx.window, 12);
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["Trips"]);
    assert.deepEqual(ctx.boxes().map((b) => b.dataset.faceId), ["2", "3"]);
  });

  test("a refusal is said in the server's words and nothing changes on the page", async (t) => {
    const ctx = await openPhoto(t, {
      extra: (server) => server.first("/api/faces/exclude", { success: false, error: "Server is currently clustering faces." }, { status: 409 }),
    });
    ctx.show();
    click(ctx.window, ctx.boxes()[2]);
    await flush(ctx.window, 4);
    click(ctx.window, [...ctx.panel().querySelectorAll("button")].find((b) => b.textContent === "Not important"));
    await flush(ctx.window, 8);
    assert.match(ctx.panel().querySelector(".face-panel-message").textContent, /clustering faces/);
    assert.equal(ctx.boxes().length, 3);
  });
});

describe("the strip under the photo and the boxes are one decision (#860)", () => {
  // The owner's photo type: three faces, one of them named. The strip's card for another face used to add the person's TAG
  // only: the face stayed a red box (in the page's copy of the answer, and in the library's, since a tag names a face only
  // when it is the one face to be named), "named" in the owner's mind and not in the library.
  const PHOTO3 = {
    faces: [
      face(1, [400, 300, 800, 700], { name: "Anh Tran" }),
      face(2, [1000, 500, 1400, 900], { suggestion: "Hazel Brookmire", similarity: 0.91 }),
      face(3, [2000, 600, 2600, 1200]),
    ],
    total: 3, unmatched: 2, size: [4000, 3000], turned: false,
  };
  const cards = (ctx) => [...ctx.document.querySelectorAll(".face-card")];

  test("clicking a suggested face's card names that face and adds the person, and the box turns named at once", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Anh Tran"], people: ["Anh Tran"] });
    const ctx = await openPhoto(t, { photoFaces: PHOTO3, photo });
    ctx.show();
    assert.match(ctx.boxes()[1].getAttribute("aria-label"), /not named/);
    const card = cards(ctx)[1];
    assert.ok(card.classList.contains("face-card-actionable"));
    assert.match(card.title, /name this face Hazel Brookmire and add them to this photo/);
    click(ctx.window, card);
    await flush(ctx.window, 14);
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["People/Anh Tran", "People/Hazel Brookmire"]);
    assert.deepEqual(ctx.posts("/api/face/match")[0].body, { face_id: 2, person_name: "Hazel Brookmire", page_writes_tags: true });
    assert.match(ctx.boxes()[1].getAttribute("aria-label"), /Hazel Brookmire/, "the box still said not named");
    assert.match(cards(ctx)[1].querySelector(".face-card-label").textContent, /Hazel Brookmire$/);
    assert.match(ctx.$("faces-summary").textContent, /1 unidentified/);
    // The page asked again for what the library holds, and drew the boxes and the strip from that one answer.
    assert.ok(ctx.server.calls.filter((c) => c.url.includes("/api/photo-faces")).length >= 2);
  });

  test("a suggested face whose person the photo has already is still clickable: it names the face and writes nothing else", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Anh Tran", "People/Hazel Brookmire"], people: ["Anh Tran", "Hazel Brookmire"] });
    const ctx = await openPhoto(t, { photoFaces: PHOTO3, photo });
    ctx.show();
    const card = cards(ctx)[1];
    assert.ok(card.classList.contains("face-card-actionable"), "the face stayed unnamed with nothing to click");
    assert.match(card.title, /Click to name this face Hazel Brookmire\./);
    click(ctx.window, card);
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 0);
    assert.deepEqual(ctx.posts("/api/face/match")[0].body, { face_id: 2, person_name: "Hazel Brookmire", page_writes_tags: true });
    assert.match(ctx.boxes()[1].getAttribute("aria-label"), /Hazel Brookmire/);
  });

  test("a named face whose photo lacks the person: its card adds the person (the face is named already)", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: [], people: [] });
    const ctx = await openPhoto(t, { photoFaces: PHOTO3, photo });
    ctx.show();
    const card = cards(ctx)[0];
    assert.match(card.title, /Click to add Anh Tran to this photo\./);
    click(ctx.window, card);
    await flush(ctx.window, 14);
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["People/Anh Tran"]);
    assert.deepEqual(ctx.posts("/api/face/match")[0].body, { face_id: 1, person_name: "Anh Tran", page_writes_tags: true });
  });

  test("a card that would change nothing is settled", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Anh Tran"], people: ["Anh Tran"] });
    const ctx = await openPhoto(t, { photoFaces: PHOTO3, photo });
    assert.ok(cards(ctx)[0].classList.contains("face-card-settled"));
  });

  test("two clicks, a card and a box, name once", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Anh Tran"], people: ["Anh Tran"] });
    const ctx = await openPhoto(t, { photoFaces: PHOTO3, photo });
    ctx.show();
    click(ctx.window, cards(ctx)[1]);
    click(ctx.window, cards(ctx)[1]);
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/photo/save-metadata").length, 1);
    assert.equal(ctx.posts("/api/face/match").length, 1);
  });

  test("a card whose person cannot be added says so, and names no face", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["People/Anh Tran"], people: ["Anh Tran"] });
    const ctx = await openPhoto(t, {
      photoFaces: PHOTO3, photo,
      extra: (server) => server.first("/api/photo/save-metadata", { success: false, error: "locked" }),
    });
    ctx.show();
    click(ctx.window, cards(ctx)[1]);
    await flush(ctx.window, 14);
    assert.equal(ctx.posts("/api/face/match").length, 0);
    assert.match(ctx.$("status-text").textContent, /not added to the photo, so the face is not named/);
  });

  test("a tag written for the photo can name its one face on the server: the boxes are read again, not left saying not found", async (t) => {
    // The library's rule (#788): one face to be named and one person newly on the photo name it. The page's copy was
    // drawn before the save and nothing drew it again: the box stayed red until the photo was opened once more.
    const one = { faces: [face(1, [400, 300, 800, 700])], total: 1, unmatched: 1, size: [4000, 3000], turned: false };
    const ctx = await openPhoto(t, { photoFaces: one });
    ctx.show();
    assert.match(ctx.boxes()[0].getAttribute("aria-label"), /not named/);
    ctx.server.first("/api/photo/save-metadata", () => {
      // What the library does at the save: the tag names the face.
      one.faces[0].name = "Anh Tran";
      return { success: true };
    });
    ctx.server.first("/api/photo-faces", () => structuredClone(one));
    ctx.$("input-add-person").value = "Anh Tran";
    click(ctx.window, ctx.$("btn-add-person"));
    await flush(ctx.window, 16);
    assert.match(ctx.boxes()[0].getAttribute("aria-label"), /Anh Tran/, "the box did not follow the tag");
  });

  test("a save of the photo's tags asks nothing of the faces when none is left to be named", async (t) => {
    const done = { faces: [face(1, [400, 300, 800, 700], { name: "Anh Tran" })], total: 1, unmatched: 0, size: [4000, 3000], turned: false };
    const ctx = await openPhoto(t, { photoFaces: done });
    const asked = () => ctx.server.calls.filter((c) => c.url.includes("/api/photo-faces")).length;
    const before = asked();
    ctx.$("input-add-tag").value = "Trips";
    click(ctx.window, ctx.$("btn-add-tag"));
    await flush(ctx.window, 12);
    assert.equal(asked(), before);
  });
});

describe("in the full-window zoom (#859)", () => {
  // The boxes and the panel are drawn over the zoomed picture by the same code as over the photo in the panel, with the
  // zoom's own arithmetic (boxInContainedImage) and the pixel size the server gave; jsdom has no layout, so the window the
  // picture is fitted in is given to it, as a browser would measure it.
  const WINDOW = { width: 1200, height: 800 };
  const PHOTO = {
    faces: [
      face(1, [400, 300, 800, 700], { name: "Anh Tran" }),
      face(2, [1000, 500, 1400, 900], { suggestion: "Hazel Brookmire", similarity: 0.91 }),
      face(3, [2000, 600, 2600, 1200]),
    ],
    total: 3, unmatched: 2, size: [4000, 3000], turned: false,
  };

  async function zoomed(t, options = {}) {
    const ctx = await openPhoto(t, { photoFaces: PHOTO, photo: photoRecord({ filename: "a.jpg", tags: ["People/Anh Tran"], people: ["Anh Tran"] }), ...options });
    const rect = (width, height) => ({ left: 0, top: 0, right: width, bottom: height, width, height });
    ctx.$("image-zoom-img").getBoundingClientRect = () => rect(WINDOW.width, WINDOW.height);
    ctx.$("image-zoom").getBoundingClientRect = () => rect(WINDOW.width, WINDOW.height);
    ctx.zoomLayer = () => ctx.document.querySelector(".image-zoom-layer");
    ctx.zoomBoxes = () => [...ctx.zoomLayer().querySelectorAll(".face-box")];
    ctx.zoomPanel = () => ctx.zoomLayer().querySelector(".face-panel");
    ctx.zoomIsOpen = () => !ctx.$("image-zoom").classList.contains("hidden");
    ctx.zoomIn = () => click(ctx.window, ctx.$("main-image"));
    return ctx;
  }

  test("every face's box is drawn on the zoomed picture, named and not named, where the zoom's arithmetic puts it", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    assert.equal(ctx.zoomIsOpen(), true);
    assert.equal(ctx.zoomBoxes().length, 3);
    assert.deepEqual(ctx.zoomBoxes().map((b) => b.classList.contains("is-named")), [true, false, false]);
    ctx.zoomBoxes().forEach((el, n) => {
      const placed = boxInContainedImage({ width: 4000, height: 3000 }, WINDOW, PHOTO.faces[n].box);
      assert.ok(Math.abs(px(el, "left") - placed.left) < 0.01 && Math.abs(px(el, "top") - placed.top) < 0.01
        && Math.abs(px(el, "width") - placed.width) < 0.01, "box " + (n + 1) + " is not where the picture puts it");
    });
    // The same icon, in the zoom.
    assert.match(ctx.zoomLayer().querySelector(".face-boxes-toggle").textContent, /3/);
  });

  test("one surface at a time: the photo in the panel has no second copy while the zoom is open, and has the boxes again after", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    assert.equal(ctx.boxes().length, 3);
    ctx.zoomIn();
    await flush(ctx.window, 4);
    assert.equal(ctx.boxes().length, 0, "the boxes behind the zoom are a second copy");
    assert.equal(ctx.zoomBoxes().length, 3);
    ctx.key(ctx.document.body, "Escape");
    await flush(ctx.window, 4);
    assert.equal(ctx.zoomIsOpen(), false);
    assert.equal(ctx.zoomBoxes().length, 0);
    assert.equal(ctx.boxes().length, 3, "the boxes did not come back");
  });

  test("the icon in the zoom turns the boxes on and off, as the one in the panel does", async (t) => {
    const ctx = await zoomed(t);
    ctx.zoomIn();
    await flush(ctx.window, 4);
    assert.equal(ctx.zoomBoxes().length, 0);
    click(ctx.window, ctx.zoomLayer().querySelector(".face-boxes-toggle"));
    assert.equal(ctx.zoomBoxes().length, 3);
    assert.equal(ctx.zoomIsOpen(), true, "a click on the icon closed the zoom");
    ctx.key(ctx.document.body, "Escape");
    assert.equal(ctx.boxes().length, 3, "what was turned on in the zoom is on in the panel");
  });

  test("a box in the zoom opens the same panel, and a click in the panel does not close the zoom", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomBoxes()[1]);
    await flush(ctx.window, 4);
    assert.equal(ctx.zoomIsOpen(), true, "a click on a box closed the zoom");
    assert.ok(ctx.zoomPanel(), "no panel");
    assert.equal(ctx.panel(), null, "a panel in the photo behind the zoom as well");
    assert.match(ctx.zoomPanel().textContent, /Hazel Brookmire/);
    click(ctx.window, ctx.zoomPanel().querySelector(".face-panel-title"));
    assert.equal(ctx.zoomIsOpen(), true, "a click in the panel closed the zoom");
  });

  test("Escape takes the panel away first and the zoom after", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomBoxes()[1]);
    await flush(ctx.window, 4);
    ctx.key(ctx.document.body, "Escape");
    assert.equal(ctx.zoomPanel(), null, "the panel stayed");
    assert.equal(ctx.zoomIsOpen(), true, "Escape closed the zoom with the panel still open");
    ctx.key(ctx.document.body, "Escape");
    assert.equal(ctx.zoomIsOpen(), false);
  });

  test("Escape in the panel's name box takes the panel away first, too", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomBoxes()[2]);
    await flush(ctx.window, 4);
    const input = ctx.zoomPanel().querySelector(".face-panel-input");
    input.focus();
    ctx.key(input, "Escape");
    assert.equal(ctx.zoomPanel(), null);
    assert.equal(ctx.zoomIsOpen(), true);
  });

  test("a click on the backdrop takes the panel away first and the zoom after", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomBoxes()[1]);
    await flush(ctx.window, 4);
    ctx.document.dispatchEvent(new ctx.window.Event("pointerdown", { bubbles: true }));
    click(ctx.window, ctx.$("image-zoom"));
    assert.equal(ctx.zoomPanel(), null);
    assert.equal(ctx.zoomIsOpen(), true);
    click(ctx.window, ctx.$("image-zoom"));
    assert.equal(ctx.zoomIsOpen(), false);
  });

  test("naming from the zoom: the tag first, then the face, and the box in the zoom turns named", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomBoxes()[1]);
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomPanel().querySelector(".face-panel-suggestion"));
    await flush(ctx.window, 14);
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, ["People/Anh Tran", "People/Hazel Brookmire"]);
    assert.deepEqual(ctx.posts("/api/face/match")[0].body, { face_id: 2, person_name: "Hazel Brookmire", page_writes_tags: true });
    assert.equal(ctx.zoomIsOpen(), true, "naming closed the zoom");
    assert.match(ctx.zoomBoxes()[1].getAttribute("aria-label"), /Hazel Brookmire/);
    assert.equal(ctx.zoomPanel(), null);
  });

  test("Not this person from the zoom takes the person off the photo too", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomBoxes()[0]);
    await flush(ctx.window, 4);
    click(ctx.window, [...ctx.zoomPanel().querySelectorAll("button")].find((b) => b.textContent === "Not this person"));
    await flush(ctx.window, 12);
    assert.deepEqual(ctx.posts("/api/photo/save-metadata")[0].body.tags, []);
    assert.match(ctx.zoomBoxes()[0].getAttribute("aria-label"), /not named/);
  });

  test("a panel open in the photo goes with it into the zoom, and back", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    click(ctx.window, ctx.boxes()[1]);
    await flush(ctx.window, 4);
    assert.ok(ctx.panel());
    ctx.zoomIn();
    await flush(ctx.window, 4);
    assert.equal(ctx.panel(), null);
    assert.ok(ctx.zoomPanel(), "the open panel was lost in the zoom");
    click(ctx.window, ctx.$("image-zoom"));          // the panel first
    click(ctx.window, ctx.$("image-zoom"));          // then the zoom
    assert.equal(ctx.zoomIsOpen(), false);
  });

  test("a photo whose pixel size is not known has no boxes in the zoom either", async (t) => {
    const ctx = await zoomed(t, { photoFaces: { ...PHOTO, size: null } });
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    assert.equal(ctx.zoomBoxes().length, 0);
  });

  test("a photo stored turned keeps the documented limit in the zoom: the panel says the box may be off", async (t) => {
    const ctx = await zoomed(t, { photoFaces: { ...PHOTO, turned: true } });
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    click(ctx.window, ctx.zoomBoxes()[1]);
    await flush(ctx.window, 4);
    assert.match(ctx.zoomPanel().textContent, /may not sit on the face/);
  });

  test("the window resized: the boxes follow the picture", async (t) => {
    const ctx = await zoomed(t);
    ctx.show();
    ctx.zoomIn();
    await flush(ctx.window, 4);
    const before = px(ctx.zoomBoxes()[0], "width");
    const rect = (width, height) => ({ left: 0, top: 0, right: width, bottom: height, width, height });
    ctx.$("image-zoom-img").getBoundingClientRect = () => rect(600, 400);
    ctx.$("image-zoom").getBoundingClientRect = () => rect(600, 400);
    ctx.window.dispatchEvent(new ctx.window.Event("resize"));
    await flush(ctx.window, 2);
    assert.ok(px(ctx.zoomBoxes()[0], "width") < before);
  });

  test("a photo with no faces draws nothing in the zoom", async (t) => {
    const ctx = await zoomed(t, { photoFaces: { faces: [], total: 0, unmatched: 0, size: [4000, 3000], turned: false } });
    ctx.zoomIn();
    await flush(ctx.window, 4);
    assert.equal(ctx.zoomLayer().children.length, 0);
  });
});
