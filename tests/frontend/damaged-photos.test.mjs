/**
 * The photos found damaged are not silent (docs/findings.md, #407): TagPup's folder says
 * which of its photos cannot be read, or may be incomplete copies, at its top; each such
 * card is marked in place of a gap; the open photo says why; and both apps' headers count
 * the library's, linking to the Activity page, whose Needs attention lists every one.
 * Nothing shows when there is nothing: quiet otherwise.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder, click, flush, photoRecord } from "./harness.mjs";

afterEach(() => closeAllApps());

const FOLDER = "D:\\Library\\2020";
const WHOLE = photoRecord({ filename: "IMG_0001.jpg" });
const CUT = photoRecord({ filename: "IMG_0002.jpg" });
const HALF = photoRecord({ filename: "IMG_0003.jpg" });

const DAMAGED = {
  folder: FOLDER,
  photos: [
    { path: CUT.path, name: CUT.filename, folder: FOLDER, kind: "truncated", indexed: false,
      reason: "the file ends before the picture does", detail: "image file is truncated (4 bytes not processed)",
      zero_tail: 0, size: 9000, mtime: 1700000000, found: "2026-09-28 10:00:00" },
    { path: HALF.path, name: HALF.filename, folder: FOLDER, kind: "incomplete", indexed: true,
      reason: "possibly an incomplete copy", detail: "the last 70000 bytes are zeros",
      zero_tail: 70000, size: 140000, mtime: 1700000000, found: "2026-09-28 10:00:01" },
  ],
};

function server({ damaged = DAMAGED, count = { unreadable: 13, incomplete: 1 } } = {}) {
  return new FakeServer()
    .on("/api/folder/scan", [WHOLE, CUT, HALF].map((p) => ({ ...p })))
    .on("/api/folder/damaged", damaged)
    .on("/api/damaged-photos", count)
    .on("/api/folder/membership", { photos: 3, photos_held: 3, photos_not_held: 0, others: [] })
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/folder/suggest-status", { status: "idle" });
}

const settle = (window, ms = 40) => new Promise((r) => window.setTimeout(r, ms));

function card(document, photo) {
  return [...document.querySelectorAll(".thumbnail-card")].find((c) => c.getAttribute("data-path") === photo.path);
}

describe("TagPup's folder", () => {
  test("says at its top which photos cannot be read, and which may be incomplete", async (t) => {
    const { window, document } = await loadApp("tagpup", { server: server(), t });
    await openFolder({ document, window }, FOLDER);
    await settle(window, 60);
    const notice = document.getElementById("damaged-notice");
    assert.ok(!notice.classList.contains("hidden"), "no notice");
    assert.match(notice.textContent, /1 photo in this folder can't be read — the file is damaged\. Restore it from a backup\./);
    assert.match(notice.textContent, /1 photo in this folder may be an incomplete copy/);
    assert.match(notice.textContent, /IMG_0002\.jpg — the file ends before the picture does/);
    assert.match(notice.textContent, /IMG_0003\.jpg — the last 70000 bytes are zeros/);
  });

  test("marks each damaged card in place of a gap, and leaves the rest alone", async (t) => {
    const { window, document } = await loadApp("tagpup", { server: server(), t });
    await openFolder({ document, window }, FOLDER);
    await settle(window, 60);
    const cut = card(document, CUT);
    assert.ok(cut.classList.contains("damaged"));
    assert.equal(cut.querySelector("img"), null, "a picture that will not load was asked for");
    assert.match(cut.querySelector(".thumbnail-damaged-placeholder").textContent, /Can't be read/);
    assert.match(cut.querySelector(".thumbnail-damaged").textContent, /Damaged/);
    const half = card(document, HALF);
    assert.ok(half.querySelector("img"), "a photo that decodes shows its picture");
    assert.match(half.querySelector(".thumbnail-damaged").textContent, /Incomplete\?/);
    const whole = card(document, WHOLE);
    assert.ok(!whole.classList.contains("damaged"));
    assert.equal(whole.querySelector(".thumbnail-damaged"), null);
  });

  test("the open photo says why", async (t) => {
    const { window, document } = await loadApp("tagpup", { server: server(), t });
    await openFolder({ document, window }, FOLDER);
    await settle(window, 60);
    click(window, card(document, CUT).querySelector(".btn-thumbnail-detail"));
    await settle(window);
    const note = document.getElementById("photo-damaged-note");
    assert.ok(!note.classList.contains("hidden"));
    assert.match(note.textContent, /This photo can't be read: the file ends before the picture does \(image file is truncated/);
    assert.match(note.textContent, /nothing was written to it/);
    assert.equal(document.getElementById("main-image").getAttribute("src"), "", "a picture that will not load was asked for");
  });

  test("a folder with nothing damaged shows nothing", async (t) => {
    const { window, document } = await loadApp("tagpup", {
      server: server({ damaged: { folder: FOLDER, photos: [] }, count: { unreadable: 0, incomplete: 0 } }), t });
    await openFolder({ document, window }, FOLDER);
    await settle(window, 60);
    assert.ok(document.getElementById("damaged-notice").classList.contains("hidden"));
    assert.equal(document.querySelectorAll(".thumbnail-card.damaged").length, 0);
    assert.ok(document.getElementById("damaged-badge").classList.contains("hidden"));
  });

  test("Check again reads a photo again, and the list is asked again", async (t) => {
    let asked = 0;
    const fake = server();
    fake.routes.unshift({ match: "/api/folder/damaged", status: 200,
                          body: () => (++asked === 1 ? DAMAGED : { folder: FOLDER, photos: [DAMAGED.photos[1]] }) });
    fake.routes.unshift({ match: "/api/damaged-photos/check", status: 200,
                          body: { success: true, checked: 1, whole: 1, still: 0, unreachable: 0, queued: 1 } });
    const { window, document } = await loadApp("tagpup", { server: fake, t });
    await openFolder({ document, window }, FOLDER);
    await settle(window, 60);
    const item = document.querySelector(`#damaged-notice li[data-path="${window.CSS.escape(CUT.path)}"]`);
    click(window, item.querySelector("button.damaged-check"));
    click(window, item.querySelector("button.damaged-check"));      // a double click sends one
    await settle(window, 80);
    const posts = fake.calls.filter((c) => c.url.includes("/api/damaged-photos/check"));
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].body, { paths: [CUT.path] });
    assert.equal(asked, 2, "the list was not asked again");
    assert.match(document.getElementById("status-text").textContent, /1 photo reads whole now, and is being indexed again/);
    assert.ok(!card(document, CUT).classList.contains("damaged"), "the photo read whole is still marked");
    assert.doesNotMatch(document.getElementById("damaged-notice").textContent, /can't be read/);
  });

  test("an answer for a folder left since is not shown in the next", async (t) => {
    let release;
    const late = new Promise((resolve) => { release = resolve; });
    const fake = server();
    fake.routes.unshift({ match: "/api/folder/damaged?path=D%3A%5CLibrary%5C2019", body: () => late, status: 200 });
    const { window, document } = await loadApp("tagpup", { server: fake, t });
    await openFolder({ document, window }, "D:\\Library\\2019");
    await openFolder({ document, window }, FOLDER);
    await settle(window, 60);
    release({ folder: "D:\\Library\\2019", photos: [] });
    await settle(window, 60);
    assert.match(document.getElementById("damaged-notice").textContent, /can't be read/,
                 "the folder left's answer wiped the open folder's notice");
  });
});

describe("the headers count the library's damaged photos", () => {
  test("TagPup: a link to the Activity page's Needs attention", async (t) => {
    const { window, document } = await loadApp("tagpup", { server: server(), t });
    await flush(window, 6);
    const badge = document.getElementById("damaged-badge");
    assert.ok(!badge.classList.contains("hidden"));
    assert.equal(badge.textContent, "⚠ 13 unreadable photos, 1 possibly incomplete copy");
    assert.equal(badge.getAttribute("href"), "/activity/#attention");
  });

  test("TagTuner too", async (t) => {
    const fake = new FakeServer().on("/api/damaged-photos", { unreadable: 1, incomplete: 0 });
    const { window, document } = await loadApp("tagtuner", { server: fake, t });
    await flush(window, 6);
    const badge = document.getElementById("damaged-badge");
    assert.equal(badge.textContent, "⚠ 1 unreadable photo");
    assert.ok(fake.urls().some((u) => u.startsWith("/photo_index/api/damaged-photos")), fake.urls().join("\n"));
  });
});

describe("the Activity page's Needs attention", () => {
  const ATTENTION = {
    unreadable: 1, incomplete: 0,
    libraries: [
      { name: "harbour", photos: [{ ...DAMAGED.photos[0], folder_url: "http://localhost:8090/harbour/?path=D%3A%5CLibrary%5C2020" }] },
      { name: "regatta", photos: [] },
    ],
  };

  function open(t, attention) {
    const fake = new FakeServer().on("/api/activity/attention", attention);
    return loadApp("activity", { t, server: fake, url: "http://localhost:8090/activity/?every=5000" });
  }

  test("lists each photo with why, when, its size and a link to its folder", async (t) => {
    const { window, document } = await open(t, ATTENTION);
    await flush(window, 6);
    const body = document.getElementById("attention-body");
    const rows = body.querySelectorAll("tr.damaged-photo");
    assert.equal(rows.length, 1);
    assert.match(rows[0].textContent, /D:\\Library\\2020\\IMG_0002\.jpg/);
    assert.match(rows[0].textContent, /can't be read/);
    assert.match(rows[0].textContent, /the file ends before the picture does/);
    assert.match(rows[0].textContent, /2026-09-28 10:00:00/);
    assert.match(rows[0].textContent, /9\.0 KB/);
    const link = rows[0].querySelector("a");
    assert.equal(link.getAttribute("href"), "http://localhost:8090/harbour/?path=D%3A%5CLibrary%5C2020");
    assert.equal(document.querySelector('a[href="#attention"]').textContent, "Needs attention (1)");
    assert.equal(body.querySelectorAll('.card[data-library="regatta"]').length, 0, "a library with nothing is quiet");
  });

  test("Check all again reads a library's photos again and says what it found", async (t) => {
    const fake = new FakeServer()
      .on("/api/activity/attention/check", { success: true, libraries: [{ name: "harbour", checked: 1, whole: 1, still: 0 }] })
      .on("/api/activity/attention", ATTENTION);
    const { window, document } = await loadApp("activity", { t, server: fake, url: "http://localhost:8090/activity/?every=5000" });
    await flush(window, 6);
    const button = [...document.querySelectorAll("#attention-body button.check-again")]
      .find((b) => b.textContent === "Check again" && !b.closest("tr"));
    click(window, button);
    await flush(window, 8);
    const post = fake.calls.find((c) => c.url.includes("/api/activity/attention/check"));
    assert.equal(post.url, "/api/activity/attention/check", "asked under a library, or not at all");
    assert.deepEqual(post.body, { library: "harbour" });
    assert.match(document.getElementById("attention-body").textContent, /1 photo reads whole now, and is being indexed again/);
  });

  test("says how many photos wait for their faces to be detected again", async (t) => {
    const { window, document } = await open(t, { unreadable: 0, incomplete: 0,
      libraries: [{ name: "harbour", photos: [], faces_to_detect: 2 }] });
    await flush(window, 6);
    const card = document.querySelector('#attention-body .card[data-library="harbour"]');
    assert.ok(card, "a library with faces to detect is not shown");
    assert.match(card.querySelector(".faces-to-detect").textContent, /2 photos wait for their faces to be detected again/);
  });

  test("says how many names wait to be settled and opens them in TagTuner, quietly otherwise", async (t) => {
    const { window, document } = await open(t, { unreadable: 0, incomplete: 0, names_to_review: 5,
      libraries: [{ name: "harbour", photos: [], names_to_review: 5, names_url: "http://localhost:8080/harbour/?names-to-review=1" },
                  { name: "regatta", photos: [], names_to_review: 0, names_url: null }] });
    await flush(window, 6);
    const card = document.querySelector('#attention-body .card[data-library="harbour"]');
    assert.match(card.querySelector(".names-to-review").textContent, /^5 names wait for you to settle/);
    assert.equal(card.querySelector(".names-to-review a").getAttribute("href"), "http://localhost:8080/harbour/?names-to-review=1");
    assert.equal(card.querySelector("table"), null, "no damaged photos: no empty table");
    assert.equal(document.querySelector('a[href="#attention"]').textContent, "Needs attention (5)");
    assert.equal(document.querySelectorAll('#attention-body .card[data-library="regatta"]').length, 0);
  });

  test("says so, once, when nothing needs attention", async (t) => {
    const { window, document } = await open(t, { unreadable: 0, incomplete: 0, libraries: [{ name: "harbour", photos: [] }] });
    await flush(window, 6);
    assert.equal(document.getElementById("attention-body").textContent, "Nothing needs attention.");
    assert.equal(document.querySelector('a[href="#attention"]').textContent, "Needs attention");
  });
});
