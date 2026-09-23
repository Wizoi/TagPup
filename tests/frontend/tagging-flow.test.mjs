/**
 * The loop TagPup exists for: look at a photo, tag it, move to the next one.
 *
 * These cover the changes from the usability review. The behaviour that started it:
 * arrow keys moved the selection only while focus happened to be on <body>, and the
 * filter box -- the one control you use right before reaching for the arrows -- put
 * focus somewhere that made them dead, so the page scrolled instead.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, closeAllApps, openFolder } from "./harness.mjs";

const PHOTOS = [
  { path: "D:\\p\\a.jpg", filename: "a.jpg", tags: ["Beach"], people: [], captions: [], title: "" },
  { path: "D:\\p\\b.jpg", filename: "b.jpg", tags: [], people: [], captions: [], title: "" },
  { path: "D:\\p\\c.jpg", filename: "c.jpg", tags: [], people: [], captions: [], title: "" },
];

function server(photos = PHOTOS) {
  return new FakeServer()
    .on("/api/folder/scan", photos.map((p) => ({ ...p, tags: [...p.tags] })))
    .on("/api/taxonomy/tree", [])
    .on("/api/people", [])
    .on("/api/tags", [])
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/folder/suggest-status", { status: "idle" });
}

/** Load the app and scan a folder, so the list is populated. */
async function scanned(t, s = server()) {
  const { window, document } = await loadApp("tagpup", { server: s, t });
  await openFolder({ document, window }, "D:\\p");
  await new Promise((r) => window.setTimeout(r, 60));
  return { window, document, server: s };
}

function rows(document) {
  return [...document.querySelectorAll(".photo-item-file")];
}

function activePath(document) {
  const el = document.querySelector(".photo-item-file.active");
  return el && el.getAttribute("data-path");
}

function press(document, key, target) {
  const ev = new document.defaultView.KeyboardEvent("keydown", {
    key, bubbles: true, cancelable: true,
  });
  (target || document).dispatchEvent(ev);
  return ev.defaultPrevented;
}

afterEach(() => closeAllApps());

describe("moving between photos", () => {
  test("Down moves to the next photo", async (t) => {
    const { document } = await scanned(t);
    press(document, "ArrowDown");
    assert.equal(activePath(document), "D:\\p\\a.jpg");
    press(document, "ArrowDown");
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });

  test("Right does the same as Down", async (t) => {
    const { document } = await scanned(t);
    press(document, "ArrowRight");
    press(document, "ArrowRight");
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });

  test("Left goes back", async (t) => {
    const { document } = await scanned(t);
    press(document, "ArrowRight");
    press(document, "ArrowRight");
    press(document, "ArrowLeft");
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });

  test("it stops at the end rather than wrapping", async (t) => {
    const { document } = await scanned(t);
    for (let i = 0; i < 8; i++) press(document, "ArrowRight");
    assert.equal(activePath(document), "D:\\p\\c.jpg");
  });

  test("it stops at the start rather than wrapping", async (t) => {
    const { document } = await scanned(t);
    press(document, "ArrowRight");
    for (let i = 0; i < 8; i++) press(document, "ArrowLeft");
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });

  test("arrow keys still work with the filter box focused", async (t) => {
    // The reported bug: the blanket INPUT guard returned before preventDefault,
    // so the browser scrolled instead of the selection moving.
    const { document } = await scanned(t);
    const search = document.getElementById("photo-search");
    search.focus();
    const prevented = press(document, "ArrowDown", search);
    assert.equal(prevented, true, "the keypress was left to the browser to scroll with");
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });

  test("arrow keys stay out of the way while typing a tag", async (t) => {
    // The guard is still right here: an arrow key must not navigate away from
    // text that has not been committed.
    const { document } = await scanned(t);
    press(document, "ArrowDown");
    const tagInput = document.getElementById("input-add-tag");
    tagInput.focus();
    tagInput.value = "half-typed";
    press(document, "ArrowDown", tagInput);
    assert.equal(activePath(document), "D:\\p\\a.jpg", "navigated away mid-edit");
  });

  test("browser shortcuts are left alone", async (t) => {
    const { document } = await scanned(t);
    const ev = new document.defaultView.KeyboardEvent("keydown", {
      key: "ArrowDown", ctrlKey: true, bubbles: true, cancelable: true,
    });
    document.dispatchEvent(ev);
    assert.equal(ev.defaultPrevented, false);
  });
});

describe("keeping your place", () => {
  test("each photo starts at the top of the panel", async (t) => {
    const { document } = await scanned(t);
    const panel = document.getElementById("details-panel");
    press(document, "ArrowDown");
    panel.scrollTop = 400;
    press(document, "ArrowDown");
    assert.equal(panel.scrollTop, 0, "the panel kept the previous photo's scroll");
  });

  test("list rows can be reached by keyboard", async (t) => {
    const { document } = await scanned(t);
    assert.equal(rows(document)[0].tabIndex, 0);
  });

  test("Enter on a focused row opens it", async (t) => {
    const { document } = await scanned(t);
    const row = rows(document)[1];
    press(document, "Enter", row);
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });
});

describe("seeing what is left to do", () => {
  test("the count says how much is tagged, not how much is loaded", async (t) => {
    const { document } = await scanned(t);
    assert.match(document.getElementById("list-stats").textContent, /1 of 3 tagged, 2 to go/);
  });

  test("a fully tagged folder says so", async (t) => {
    const all = PHOTOS.map((p) => ({ ...p, tags: ["Beach"] }));
    const { document } = await scanned(t, server(all));
    assert.match(document.getElementById("list-stats").textContent, /all tagged/);
  });

  test("tagged rows are marked", async (t) => {
    const { document } = await scanned(t);
    const marked = rows(document).map((r) => r.classList.contains("is-tagged"));
    assert.deepEqual(marked, [true, false, false]);
  });

  test("a row shows how many tags it has", async (t) => {
    const { document } = await scanned(t);
    const count = rows(document)[0].querySelector(".photo-item-tagcount");
    assert.equal(count.textContent, "1");
  });

  test("an untagged row shows no count rather than a zero", async (t) => {
    const { document } = await scanned(t);
    assert.equal(rows(document)[1].querySelector(".photo-item-tagcount"), null);
  });

  // The "only show what still needs tagging" checkbox was removed from the sidebar:
  // it sat above the file list adding noise to a column that is read constantly, for
  // a filter reached rarely. The idea is worth keeping -- narrowing a folder to what
  // is left turns it into a work queue -- but it wants a better home than a stray
  // checkbox, so the tests for it go with the control. What it was built on is still
  // here and still covered: isPhotoTagged, the tagged/to-go counts, and the row
  // markers above.
});

describe("carrying tags forward", () => {
  test("it copies the previous photo's tags onto this one", async (t) => {
    const { document, window, server: s } = await scanned(t);
    press(document, "ArrowDown");   // a.jpg, tagged Beach
    press(document, "ArrowDown");   // b.jpg, untagged
    document.getElementById("btn-carry-forward").click();
    await new Promise((r) => window.setTimeout(r, 30));

    const body = s.lastBody("save-metadata");
    assert.equal(body.path, "D:\\p\\b.jpg");
    assert.deepEqual(body.tags, ["Beach"]);
  });

  test("Ctrl+D does the same", async (t) => {
    const { document, window, server: s } = await scanned(t);
    press(document, "ArrowDown");
    press(document, "ArrowDown");
    const ev = new window.KeyboardEvent("keydown", {
      key: "d", ctrlKey: true, bubbles: true, cancelable: true,
    });
    document.dispatchEvent(ev);
    await new Promise((r) => window.setTimeout(r, 30));
    assert.equal(ev.defaultPrevented, true);
    assert.deepEqual(s.lastBody("save-metadata").tags, ["Beach"]);
  });

  test("it is offered only when there is something to copy", async (t) => {
    const { document } = await scanned(t);
    press(document, "ArrowDown");   // first photo: nothing before it
    assert.equal(document.getElementById("btn-carry-forward").disabled, true);
    press(document, "ArrowDown");   // second: the first has a tag
    assert.equal(document.getElementById("btn-carry-forward").disabled, false);
  });

  test("it adds to existing tags rather than replacing them", async (t) => {
    const photos = [
      { ...PHOTOS[0], tags: ["Beach"] },
      { ...PHOTOS[1], tags: ["Portrait"] },
    ];
    const { document, window, server: s } = await scanned(t, server(photos));
    press(document, "ArrowDown");
    press(document, "ArrowDown");
    document.getElementById("btn-carry-forward").click();
    await new Promise((r) => window.setTimeout(r, 30));

    const tags = s.lastBody("save-metadata").tags;
    assert.ok(tags.includes("Portrait"), "the photo's own tag was dropped");
    assert.ok(tags.includes("Beach"), "the carried tag was not added");
  });
});

describe("taking it back", () => {
  test("undo is offered only after something undoable", async (t) => {
    const { document, window } = await scanned(t);
    assert.equal(document.getElementById("btn-undo").disabled, true);

    press(document, "ArrowDown");
    press(document, "ArrowDown");
    document.getElementById("btn-carry-forward").click();
    await new Promise((r) => window.setTimeout(r, 30));

    assert.equal(document.getElementById("btn-undo").disabled, false);
  });

  test("undo writes back the tags the photo had before", async (t) => {
    const { document, window, server: s } = await scanned(t);
    press(document, "ArrowDown");
    press(document, "ArrowDown");
    document.getElementById("btn-carry-forward").click();
    await new Promise((r) => window.setTimeout(r, 30));

    document.getElementById("btn-undo").click();
    await new Promise((r) => window.setTimeout(r, 30));

    const body = s.lastBody("save-metadata");
    assert.equal(body.path, "D:\\p\\b.jpg");
    assert.deepEqual(body.tags, [], "b.jpg was not restored to having no tags");
  });

  test("undo empties itself, so it cannot be applied twice", async (t) => {
    const { document, window } = await scanned(t);
    press(document, "ArrowDown");
    press(document, "ArrowDown");
    document.getElementById("btn-carry-forward").click();
    await new Promise((r) => window.setTimeout(r, 30));
    document.getElementById("btn-undo").click();
    await new Promise((r) => window.setTimeout(r, 30));
    assert.equal(document.getElementById("btn-undo").disabled, true);
  });
});

describe("not losing what was typed", () => {
  // Leaving a field used to commit what was in it. That commit raced the move to
  // the next photo and lost it (see unsaved-edits.test.mjs, which covers the Save
  // button, Ctrl+S and the prompt that replaced it). Blurring now writes nothing.
  test("leaving the field neither writes nor asks", async (t) => {
    const { document, window, server: s } = await scanned(t);
    press(document, "ArrowDown");

    const tagInput = document.getElementById("input-add-tag");
    tagInput.value = "Sunset";
    tagInput.dispatchEvent(new window.Event("blur"));
    await new Promise((r) => window.setTimeout(r, 60));

    assert.equal(s.lastBody("save-metadata"), undefined, "it saved on blur");
    assert.equal(document.querySelector(".modal-overlay.active"), null, "it asked on blur");
    assert.equal(tagInput.value, "Sunset", "the typed text was cleared");
  });

  test("typed text never follows you to the next photo", async (t) => {
    // It used to: the field was only cleared on a successful save, so text typed
    // for one photo survived into the next and Enter applied it to the wrong one.
    // Moving on now asks; discarding is the one answer that leaves the text behind.
    const { document, window } = await scanned(t);
    press(document, "ArrowDown");

    const tagInput = document.getElementById("input-add-tag");
    tagInput.value = "Sunset";
    press(document, "ArrowDown");
    document.querySelector('.unsaved-edits-modal [data-choice="discard"]').click();
    await new Promise((r) => window.setTimeout(r, 40));

    assert.equal(activePath(document), "D:\\p\\b.jpg");
    assert.equal(tagInput.value, "", "the text carried over to another photo");
  });

  test("an empty field commits nothing", async (t) => {
    const { document, window, server: s } = await scanned(t);
    press(document, "ArrowDown");
    const tagInput = document.getElementById("input-add-tag");
    tagInput.value = "   ";
    tagInput.dispatchEvent(new window.Event("blur"));
    await new Promise((r) => window.setTimeout(r, 40));
    assert.equal(s.lastBody("save-metadata"), undefined);
  });

  test("Escape abandons the text instead of saving it", async (t) => {
    const { document, window, server: s } = await scanned(t);
    press(document, "ArrowDown");

    const tagInput = document.getElementById("input-add-tag");
    tagInput.focus();
    tagInput.value = "Mistake";
    press(document, "Escape", tagInput);
    tagInput.dispatchEvent(new window.Event("blur"));
    await new Promise((r) => window.setTimeout(r, 40));

    assert.equal(tagInput.value, "");
    assert.equal(s.lastBody("save-metadata"), undefined, "the abandoned tag was saved");
  });
});

describe("swiping across the image", () => {
  function swipe(window, el, dx, dy = 0) {
    const down = new window.Event("pointerdown", { bubbles: true, cancelable: true });
    Object.assign(down, { pointerId: 1, pointerType: "touch", button: 0, clientX: 300, clientY: 200 });
    el.dispatchEvent(down);
    const up = new window.Event("pointerup", { bubbles: true, cancelable: true });
    Object.assign(up, { pointerId: 1, pointerType: "touch", button: 0, clientX: 300 + dx, clientY: 200 + dy });
    el.dispatchEvent(up);
  }

  test("right to left goes forwards", async (t) => {
    const { document, window } = await scanned(t);
    press(document, "ArrowDown");
    swipe(window, document.getElementById("main-image"), -150);
    assert.equal(activePath(document), "D:\\p\\b.jpg");
  });

  test("left to right goes back", async (t) => {
    const { document, window } = await scanned(t);
    press(document, "ArrowDown");
    press(document, "ArrowDown");
    swipe(window, document.getElementById("main-image"), 150);
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });

  test("a tap does not count as a swipe", async (t) => {
    const { document, window } = await scanned(t);
    press(document, "ArrowDown");
    swipe(window, document.getElementById("main-image"), 4);
    assert.equal(activePath(document), "D:\\p\\a.jpg");
  });

  test("a scroll that drifts sideways does not count", async (t) => {
    const { document, window } = await scanned(t);
    press(document, "ArrowDown");
    swipe(window, document.getElementById("main-image"), 70, 200);
    assert.equal(activePath(document), "D:\\p\\a.jpg", "a vertical scroll changed photo");
  });
});
