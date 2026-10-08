/**
 * #790: hovering (or focusing) a suggested person shows up to four of their faces.
 *
 * The component is web/common/person-faces.js; both pages attach it to every place a suggested
 * person is a text button. These tests drive it where TagPup shows a suggestion on a detected
 * face and where TagTuner lists who a selected face resembles: the pointer comes and goes
 * quickly, the answer comes late or not at all, the popup is near the window's edge, the
 * keyboard does it too, and a name is only ever text.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder, closeAllApps } from "./harness.mjs";
import { REPO_ROOT } from "./harness.mjs";

const JANE = "Jane Doe";
const SAMPLES = { [JANE]: [11, 12, 13, 14], "Rowan Thackeray": [21] };

afterEach(() => closeAllApps());

function serverWith(faces, samples) {
  return new FakeServer()
    .on("/api/tags", [])
    .on("/api/people", [JANE])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", [photoRecord({ filename: "a.jpg" })])
    .on("/api/photo-faces", faces)
    .first("/api/people-face-samples", samples);
}

const SUGGESTED = {
  faces: [{ id: 3, box: [0, 0, 9, 9], area: 81, name: null, suggestion: JANE, similarity: 0.82, excluded: false }],
  total: 1, unmatched: 1,
};

async function openPhoto(t, { faces = SUGGESTED, samples = SAMPLES, server = null } = {}) {
  const fake = server || serverWith(faces, samples);
  const ctx = await loadApp("tagpup", { t, url: "http://localhost:8090/photo_index/", server: fake });
  await openFolder(ctx, "D:/Library/2020", { settle: 6 });
  const item = ctx.document.querySelector("li[data-path]");
  if (item) item.click();
  await flush(ctx.window, 6);
  ctx.server = fake;
  ctx.card = () => ctx.document.querySelector(".face-card");
  ctx.popup = () => ctx.document.getElementById("person-faces-popup");
  ctx.shown = () => Boolean(ctx.popup()) && !ctx.popup().classList.contains("hidden");
  ctx.fire = (target, type) => target.dispatchEvent(new ctx.window.MouseEvent(type, { bubbles: false }));
  ctx.wait = (ms) => new Promise((resolve) => ctx.window.setTimeout(resolve, ms));
  ctx.samplesAsked = () => fake.urls().filter((u) => u.includes("/api/people-face-samples")).length;
  return ctx;
}

const RESTS = 170;      // longer than the popup's own delay

describe("the popup of a suggested person's faces", () => {
  test("the pointer resting on the suggestion shows their faces, as crops from the face-crop route", async (t) => {
    const ctx = await openPhoto(t);
    ctx.fire(ctx.card(), "mouseenter");
    assert.equal(ctx.shown(), false, "shown before the pointer had rested");
    await ctx.wait(RESTS);
    assert.equal(ctx.shown(), true);
    const crops = [...ctx.popup().querySelectorAll("img")];
    assert.deepEqual(crops.map((img) => new URL(img.src).searchParams.get("id")), ["11", "12", "13", "14"]);
    assert.ok(crops.every((img) => new URL(img.src).pathname.endsWith("/api/face-crop")));
    assert.match(ctx.popup().textContent, /Jane Doe/);
    assert.equal(ctx.popup().getAttribute("role"), "tooltip");
  });

  test("it is described by, not focused: aria-describedby while shown, nothing to click or tab to", async (t) => {
    const ctx = await openPhoto(t);
    const card = ctx.card();
    card.setAttribute("aria-describedby", "elsewhere");
    ctx.fire(card, "mouseenter");
    await ctx.wait(RESTS);
    assert.equal(card.getAttribute("aria-describedby"), "person-faces-popup");
    assert.equal(ctx.popup().tabIndex, -1);
    assert.equal(ctx.popup().querySelector("button, a, input, [tabindex]"), null);
    ctx.fire(card, "mouseleave");
    assert.equal(ctx.shown(), false);
    assert.equal(card.getAttribute("aria-describedby"), "elsewhere", "what it described itself by before comes back");
    const css = fs.readFileSync(path.join(REPO_ROOT, "web", "common", "person-faces.css"), "utf8");
    assert.match(css, /\.person-faces\s*\{[^}]*pointer-events:\s*none/, "the popup would take the click meant for the button");
  });

  test("a pointer that passes over without resting shows nothing and asks for nothing", async (t) => {
    const ctx = await openPhoto(t);
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(30);
    ctx.fire(ctx.card(), "mouseleave");
    await ctx.wait(RESTS);
    assert.equal(ctx.shown(), false);
    assert.equal(ctx.samplesAsked(), 0);
  });

  test("a click is not stopped, and takes the popup away", async (t) => {
    const ctx = await openPhoto(t);
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    let reached = false;
    ctx.card().addEventListener("click", () => { reached = true; });
    click(ctx.window, ctx.card());
    assert.equal(reached, true);
    assert.equal(ctx.shown(), false);
    await flush(ctx.window, 10);      // the click's own work (adding the person) finishes before the page goes
  });

  test("the keyboard gets it too: focus shows, Escape and blur take it away", async (t) => {
    const ctx = await openPhoto(t);
    const card = ctx.card();
    assert.equal(card.tabIndex, 0, "the suggestion cannot be reached with the keyboard");
    card.focus();
    await ctx.wait(RESTS);
    assert.equal(ctx.shown(), true);
    assert.equal(ctx.window.document.activeElement, card, "the popup took the focus");
    card.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.equal(ctx.shown(), false);
    card.blur();
    card.focus();
    await ctx.wait(RESTS);
    assert.equal(ctx.shown(), true);
    card.blur();
    assert.equal(ctx.shown(), false);
  });

  test("one answer for everyone, asked once for several hovers", async (t) => {
    const ctx = await openPhoto(t);
    for (let i = 0; i < 3; i++) {
      ctx.fire(ctx.card(), "mouseenter");
      await ctx.wait(RESTS);
      ctx.fire(ctx.card(), "mouseleave");
    }
    assert.equal(ctx.samplesAsked(), 1);
  });

  test("a person with no face named says so", async (t) => {
    const ctx = await openPhoto(t, { samples: { "Rowan Thackeray": [21] } });
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    assert.equal(ctx.shown(), true);
    assert.match(ctx.popup().textContent, /no face named yet/);
    assert.equal(ctx.popup().querySelectorAll("img").length, 0);
  });

  test("an answer that cannot be had says so, and the next hover asks again", async (t) => {
    const server = serverWith(SUGGESTED, SAMPLES);
    let fail = true;
    server.first("/api/people-face-samples", () => {
      if (fail) return Promise.reject(new Error("the server went away"));
      return SAMPLES;
    });
    const ctx = await openPhoto(t, { server });
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    assert.match(ctx.popup().textContent, /could not be loaded/);
    ctx.fire(ctx.card(), "mouseleave");
    fail = false;
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    assert.equal(ctx.popup().querySelectorAll("img").length, 4);
  });

  test("a late answer for a hover that has gone is not shown over the next one", async (t) => {
    let release;
    const slow = new Promise((resolve) => { release = resolve; });
    const server = serverWith({
      faces: [
        { id: 3, box: [0, 0, 9, 9], area: 81, name: null, suggestion: JANE, similarity: 0.82, excluded: false },
        { id: 4, box: [0, 0, 9, 9], area: 81, name: null, suggestion: "Rowan Thackeray", similarity: 0.8, excluded: false },
      ],
      total: 2, unmatched: 2,
    }, SAMPLES);
    server.first("/api/people-face-samples", () => slow.then(() => SAMPLES));
    const ctx = await openPhoto(t, { server });
    const [first, second] = ctx.document.querySelectorAll(".face-card");
    ctx.fire(first, "mouseenter");
    await ctx.wait(RESTS);                     // asked, not answered
    ctx.fire(first, "mouseleave");
    ctx.fire(second, "mouseenter");
    await ctx.wait(RESTS);
    release();
    await ctx.wait(60);
    assert.equal(ctx.shown(), true);
    assert.match(ctx.popup().textContent, /Rowan Thackeray/);
    assert.doesNotMatch(ctx.popup().textContent, /Jane Doe/);
    assert.equal(second.getAttribute("aria-describedby"), "person-faces-popup");
    assert.equal(first.getAttribute("aria-describedby"), null);
  });

  test("near the bottom of the window it opens above, and never past the right edge", async (t) => {
    const ctx = await openPhoto(t);
    const { window } = ctx;
    Object.defineProperty(window, "innerWidth", { value: 800, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 600, configurable: true });
    const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });
    window.Element.prototype.getBoundingClientRect = function () {
      return this.id === "person-faces-popup" ? rect(0, 0, 200, 100) : rect(760, 560, 30, 20);
    };
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    const popup = ctx.popup();
    assert.equal(parseFloat(popup.style.top), 560 - 6 - 100, "below the button there was no room");
    assert.equal(parseFloat(popup.style.left), 800 - 200 - 4, "the popup went past the right edge");
    window.Element.prototype.getBoundingClientRect = function () {
      return this.id === "person-faces-popup" ? rect(0, 0, 200, 100) : rect(10, 100, 30, 20);
    };
    ctx.fire(ctx.card(), "mouseleave");
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    assert.equal(parseFloat(popup.style.top), 100 + 20 + 6, "with room below it is below");
    assert.equal(parseFloat(popup.style.left), 10);
  });

  test("a name is text, whatever it holds", async (t) => {
    const evil = "<b id=pwned>Jane";
    const ctx = await openPhoto(t, {
      faces: { faces: [{ id: 3, box: [0, 0, 9, 9], area: 81, name: null, suggestion: evil, similarity: 0.8, excluded: false }],
               total: 1, unmatched: 1 },
      samples: { [evil]: [11] },
    });
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    assert.equal(ctx.document.getElementById("pwned"), null);
    assert.ok(ctx.popup().textContent.includes("<b id=pwned>Jane"));
  });

  test("the popup goes when the suggestion is drawn again under the pointer", async (t) => {
    const ctx = await openPhoto(t);
    ctx.fire(ctx.card(), "mouseenter");
    await ctx.wait(RESTS);
    ctx.card().remove();
    await ctx.wait(300);
    assert.equal(ctx.shown(), false);
  });
});

describe("TagTuner's lists of who a face resembles", () => {
  test("the resemblances beside a selected face show the person's faces", async (t) => {
    const FACE = {
      id: 1, photo_path: "D:\\xc\\photo1.jpg", filename: "photo1.jpg", box: [10, 10, 60, 60], prob: 0.99, mtime: 0,
      year: 2026, similarity: 0, cluster_id: -1, cluster_name: "Unclustered", other_names: [], suggested_name: null,
      suggested_similarity: 0, suggestion_strength: null,
    };
    const server = new FakeServer()
      .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
      .on("/api/unmatched-faces/people", [{ name: "Unknown Faces", count: 1, unit: "face" }])
      .on("/api/unmatched-faces/person-matches", {
        faces: [FACE], total: 1, total_count: 1, unclustered_total: 1, unclustered_shown: 1, has_more: false, page: 1, limit: -1,
      })
      .on("/api/people-with-counts", [])
      .on("/api/people", [])
      .on("/api/photo-details", { tags: [], people: [], size: [100, 100] })
      .on("/api/face-matches", [{ name: JANE, similarity: 0.91, band: "likely" }])
      .first("/api/people-face-samples", SAMPLES);
    const { window, document } = await loadApp("tagtuner", {
      t, server, url: `http://localhost:8080/kr-track/?mode=unmatched-faces&person=${encodeURIComponent("Unknown Faces")}`,
    });
    await new Promise((r) => window.setTimeout(r, 140));
    click(window, document.querySelector('#matching-faces-grid .face-match-item[data-face-id="1"]'));
    await new Promise((r) => window.setTimeout(r, 60));
    const row = document.querySelector("#matching-detail-diagnostics .diagnostics-item");
    assert.ok(row, "the resemblance was not listed");
    assert.equal(row.tabIndex, 0);
    row.dispatchEvent(new window.MouseEvent("mouseenter"));
    await new Promise((r) => window.setTimeout(r, RESTS));
    const popup = document.getElementById("person-faces-popup");
    assert.equal(popup.classList.contains("hidden"), false);
    assert.equal(popup.querySelectorAll("img").length, 4);
  });
});
