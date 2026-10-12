/**
 * Undo puts the faces back where they were, without rebuilding the grid.
 *
 * Ignoring a cluster or assigning it in one click takes its cards out of the grid in
 * place. Undo then rebuilt the whole grid -- ten seconds on Unknown Faces, the very
 * cost the in-place removal was written to avoid -- from a list the undone faces had
 * already been dropped from, so they did not come back at all until the person was
 * opened again. And undoing an assign told the server "this is nobody", a decision
 * nobody had made.
 *
 * Against the real server (tests/test_pages_against_the_real_server.py runs this): the
 * library holds five unnamed faces, three alike Wren's decided face (Cluster 1, which
 * offers her name) and two alike each other (Cluster 2).
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, closeAllApps, REAL, realLibrary, until } from "./harness.mjs";

const press = (window, el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
const ids = (document) => [...document.querySelectorAll("#matching-faces-grid [data-face-id]")].map((c) => Number(c.dataset.faceId));
const sorted = (list) => [...list].sort((a, b) => a - b);

function section(document, title) {
  return [...document.querySelectorAll("#matching-faces-grid .matching-group-section")].find((s) =>
    ((s.querySelector(".matching-group-title") || {}).textContent || "").startsWith(title));
}

describe("Undo puts the faces back, against the server", { skip: !REAL }, () => {
  afterEach(() => closeAllApps());

  async function openGrid(t) {
    const real = await realLibrary("unknown_faces", "tagtuner");
    const url = `${real.url}?mode=unmatched-faces&person=${encodeURIComponent("Unknown Faces")}`;
    const ctx = await loadApp("tagtuner", { server: real.server, url, t });
    await until(ctx, () => ids(ctx.document).length === 5, "the five faces");
    const [first, second, third, fourth, fifth] = real.faces;
    return Object.assign(ctx, {
      bystander: ctx.document.querySelector(`#matching-faces-grid [data-face-id="${fourth}"]`),
      // The faces the library still offers as unknown, and what it calls the first photo's face.
      offered: async () => sorted((await (await fetch(`${REAL.tuner}/${real.library}/api/unmatched-faces/person-matches?name=Unknown%20Faces`)).json()).faces.map((f) => f.id)),
      firstFace: async () => (await (await fetch(`${REAL.tagpup}/${real.library}/api/photo-faces?path=${encodeURIComponent(real.photos[0])}`)).json()).faces[0],
      all: sorted([first, second, third, fourth, fifth]),
    });
  }

  const gone = (ctx) => until(ctx, () => ids(ctx.document).length === 2, "the cluster to leave the grid");
  const undo = async (ctx) => {
    press(ctx.window, ctx.document.getElementById("btn-assign-undo"));
    await until(ctx, () => ids(ctx.document).length === 5, "the faces to come back");
    assert.deepEqual(sorted(ids(ctx.document)), ctx.all, "the undone faces did not come back");
    assert.ok(ctx.document.getElementById("assign-undo-bar").classList.contains("hidden"), "the undo can be applied twice");
  };

  test("undoing an ignore: the cards return, nothing else is rebuilt, and the library offers the faces again", async (t) => {
    const ctx = await openGrid(t);
    press(ctx.window, [...section(ctx.document, "Cluster 1").querySelectorAll("button")].find((b) => b.textContent.includes("Ignore Cluster")));
    await until(ctx, () => !ctx.document.getElementById("ignore-confirm-modal").classList.contains("hidden"), "the question");
    press(ctx.window, ctx.document.getElementById("btn-ignore-confirm-ok"));
    await gone(ctx);
    assert.equal((await ctx.offered()).length, 2, "the library still offered the ignored faces");
    assert.match((await ctx.firstFace()).excluded_reason, /cluster/, "the reason says where it came from");
    assert.match(ctx.document.getElementById("assign-undo-text").textContent, /Ignored 3 faces/, "the way back was offered");
    await undo(ctx);
    assert.equal(ctx.document.querySelector(`#matching-faces-grid [data-face-id="${ctx.bystander.dataset.faceId}"]`), ctx.bystander, "the grid was rebuilt around the undo");
    assert.deepEqual(await ctx.offered(), ctx.all);
  });

  test("undoing an assign: the cards return, and the faces are unreviewed, not nobody", async (t) => {
    const ctx = await openGrid(t);
    press(ctx.window, section(ctx.document, "Cluster 1").querySelector(".cluster-suggestion-assign"));
    await gone(ctx);
    assert.equal((await ctx.firstFace()).name, "Wren Halloway");
    await undo(ctx);
    assert.equal((await ctx.firstFace()).name, null);
    assert.deepEqual(await ctx.offered(), ctx.all, "a face the undo made nobody is not offered any more");
  });
});
