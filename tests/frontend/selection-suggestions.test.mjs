/**
 * What the selection panel says is suggested, and when it says it.
 *
 * A report that looked alarming and turned out to be neither of the things it looked
 * like. After Apply All on a folder, the panel read "Suggested people: None"; a click
 * on an unrelated tag's x then made eleven people appear there, which reads as "that
 * click removed them from the photos".
 *
 * Nothing had been removed -- the keyword counts in the files matched the panel's own
 * people chips exactly. Two separate faults made it look otherwise:
 *
 *   - Apply All rescans, and a rescan clears folderSuggestions, so the panel showed
 *     "None" truthfully. The suggestions then arrived from the poller and nothing
 *     refreshed the panel, so they surfaced on the next click, whatever it happened
 *     to be.
 *   - "already on this photo" was `tags.includes(leaf)`, comparing a bare suggestion
 *     against tags that are paths. It never matched. The list was right only because
 *     photo.people happens to hold leaf names, and would have offered everybody the
 *     moment it did not.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, photoRecord, flush, click, openFolder } from "./harness.mjs";

const FOLDER = "D:/Library/2020";

const TAXONOMY = [
  { id: 1, tag: "People", name: "People", parent_id: null, has_face: 1 },
  { id: 2, tag: "People/Kira Bao", name: "Kira Bao", parent_id: 1, has_face: 1 },
  { id: 3, tag: "People/Rory Olwen", name: "Rory Olwen", parent_id: 1, has_face: 1 },
];

function build(photos, suggestions) {
  return new FakeServer()
    .on("/api/tags", ["People/Kira Bao", "People/Rory Olwen", "Cross Country"])
    .on("/api/people", ["Kira Bao", "Rory Olwen"])
    .on("/api/taxonomy/tree", TAXONOMY)
    .on("/api/databases", { databases: ["photo_index"], selected: "photo_index" })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/scan", photos)
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photos/bulk-tags", { success: true })
    .on("/api/folder/suggest-status", suggestions);
}

async function loadSelected(t, photos, suggestions) {
  const ctx = await loadApp("tagpup", {
    t,
    url: "http://localhost:8090/photo_index/",
    server: build(photos, suggestions),
  });
  await openFolder(ctx, FOLDER);
  for (const box of ctx.document.querySelectorAll(".thumbnail-checkbox")) {
    click(ctx.window, box);
  }
  await flush(ctx.window, 6);
  return ctx;
}

const suggestedPeople = (ctx) =>
  [...ctx.document.querySelectorAll("#selection-suggested-people-list .suggestion-chip")]
    .map((c) => c.textContent);

const suggestedTags = (ctx) =>
  [...ctx.document.querySelectorAll("#selection-suggested-tags-list .suggestion-chip")]
    .map((c) => c.textContent);

describe("someone already on the photo is not offered again", () => {
  test("a person tagged by their path is not suggested by their leaf", async (t) => {
    const photo = photoRecord({
      filename: "a.jpg",
      tags: ["Cross Country", "People/Kira Bao"],
      people: [],   // deliberately empty: the leaf list must not be what saves this
    });
    const ctx = await loadSelected(t, [photo], {
      status: "completed",
      suggestions: { [photo.path]: { people: [{ name: "Kira Bao", score: 0.9 }], tags: [] } },
    });

    assert.deepEqual(
      suggestedPeople(ctx), [],
      "a person already tagged on the photo was offered as a suggestion"
    );
  });

  test("the leaf list alone still suppresses it", async (t) => {
    const photo = photoRecord({
      filename: "a.jpg",
      tags: ["Cross Country"],
      people: ["Kira Bao"],
    });
    const ctx = await loadSelected(t, [photo], {
      status: "completed",
      suggestions: { [photo.path]: { people: [{ name: "Kira Bao", score: 0.9 }], tags: [] } },
    });
    assert.deepEqual(suggestedPeople(ctx), []);
  });

  test("a person the photo really lacks is still offered", async (t) => {
    const photo = photoRecord({
      filename: "a.jpg",
      tags: ["Cross Country", "People/Kira Bao"],
      people: ["Kira Bao"],
    });
    const ctx = await loadSelected(t, [photo], {
      status: "completed",
      suggestions: {
        [photo.path]: { people: [{ name: "Rory Olwen", score: 0.9 }], tags: [] },
      },
    });
    assert.ok(
      suggestedPeople(ctx).some((s) => s.includes("Rory Olwen")),
      "a genuinely missing person was not offered"
    );
  });

  test("a pathed person arriving through the tags list is suppressed too", async (t) => {
    // The suggester puts people in suggested_tags as well, already resolved.
    const photo = photoRecord({
      filename: "a.jpg",
      tags: ["People/Kira Bao"],
      people: [],
    });
    const ctx = await loadSelected(t, [photo], {
      status: "completed",
      suggestions: {
        [photo.path]: { people: [], tags: [{ tag: "People/Kira Bao", score: 0.9 }] },
      },
    });
    assert.deepEqual(suggestedPeople(ctx), []);
  });

  test("a plain keyword already present is not offered", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: ["Cross Country"], people: [] });
    const ctx = await loadSelected(t, [photo], {
      status: "completed",
      suggestions: {
        [photo.path]: { people: [], tags: [{ tag: "Cross Country", score: 0.9 }] },
      },
    });
    assert.deepEqual(suggestedTags(ctx), []);
  });

  test("a plain keyword the photo lacks is offered", async (t) => {
    const photo = photoRecord({ filename: "a.jpg", tags: [], people: [] });
    const ctx = await loadSelected(t, [photo], {
      status: "completed",
      suggestions: {
        [photo.path]: { people: [], tags: [{ tag: "Cross Country", score: 0.9 }] },
      },
    });
    assert.ok(suggestedTags(ctx).some((s) => s.includes("Cross Country")));
  });
});

describe("suggestions do not surface on an unrelated click", () => {
  test("removing a tag does not change anyone else's count", async (t) => {
    // The shape of the original report: one x click, and the panel appeared to lose
    // people. The people chips must be unmoved by it.
    const photos = [
      photoRecord({ filename: "a.jpg", tags: ["People/Kira Bao", "Saskia Wrenn"], people: ["Kira Bao"] }),
      photoRecord({ filename: "b.jpg", tags: ["People/Kira Bao"], people: ["Kira Bao"] }),
    ];
    const ctx = await loadSelected(t, photos, { status: "idle" });

    const peopleChips = () =>
      [...ctx.document.querySelectorAll("#selection-people-list .selection-summary-chip")]
        .map((c) => c.textContent.trim());

    const before = peopleChips();
    assert.ok(before.some((c) => c.includes("Kira Bao (2)")), `unexpected: ${before}`);

    const tagChip = [...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")]
      .find((c) => c.textContent.includes("Saskia Wrenn"));
    assert.ok(tagChip, "the tag chip under test was not rendered");
    click(ctx.window, tagChip.querySelector(".selection-summary-chip-remove"));
    await flush(ctx.window, 6);

    assert.ok(
      peopleChips().some((c) => c.includes("Kira Bao (2)")),
      `removing an unrelated tag changed the people counts: ${peopleChips()}`
    );
  });
});

describe("the panel says what the lists mean", () => {
  test("the suggestion headings say these are not on the photos yet", async (t) => {
    // "AI Suggested People (Selection)" beside a list of people who ARE applied reads
    // as a report of what just happened, which is how one x click looked like a purge.
    const ctx = await loadSelected(t, [photoRecord({ filename: "a.jpg" })], { status: "idle" });
    const headings = [...ctx.document.querySelectorAll(".selection-summary-group .detail-label")]
      .map((el) => el.textContent);

    assert.ok(
      headings.some((h) => /Suggested People/i.test(h) && /not yet/i.test(h)),
      `people heading does not say what the list is: ${JSON.stringify(headings)}`
    );
    assert.ok(
      headings.some((h) => /Suggested Tags/i.test(h) && /not yet/i.test(h)),
      `tags heading does not say what the list is: ${JSON.stringify(headings)}`
    );
  });
});

describe("a suggestion says how sure it is, and lands where it was asked for", () => {
  // Reported as "I clicked Apply All and these are still listed". They were: the panel
  // lists every suggestion, while Auto-Apply writes only those at 0.75 or above. The
  // leftovers were real and correctly skipped, but nothing on screen said so.
  const twoPhotos = [
    photoRecord({ filename: "a.jpg", tags: ["Cross Country"], people: [] }),
    photoRecord({ filename: "b.jpg", tags: ["Cross Country"], people: [] }),
  ];

  function suggestOnFirstOnly(photos, score) {
    return {
      status: "completed",
      suggestions: {
        [photos[0].path]: { people: [{ name: "Anh Tran", score }], tags: [] },
      },
    };
  }

  test("the chip carries its confidence", async (t) => {
    const ctx = await loadSelected(t, twoPhotos, suggestOnFirstOnly(twoPhotos, 0.63));
    const chip = suggestedPeople(ctx).find((c) => c.includes("Anh Tran"));
    assert.ok(chip, "the suggestion was not rendered");
    assert.match(chip, /63%/, `confidence not shown: ${chip}`);
  });

  test("a low-confidence one is drawn differently", async (t) => {
    // It still gets applied -- Apply All writes everything offered -- but how sure the
    // machine was is worth seeing before pressing a button that writes to every
    // selected photo.
    const ctx = await loadSelected(t, twoPhotos, suggestOnFirstOnly(twoPhotos, 0.63));
    const el = [...ctx.document.querySelectorAll("#selection-suggested-people-list .suggestion-chip")]
      .find((c) => c.textContent.includes("Anh Tran"));
    assert.ok(el.classList.contains("suggestion-chip-unsure"));
    assert.match(el.title, /Apply All/i, `the tooltip does not say it will be applied: ${el.title}`);
  });

  test("one above the bar is not marked", async (t) => {
    const ctx = await loadSelected(t, twoPhotos, suggestOnFirstOnly(twoPhotos, 0.92));
    const el = [...ctx.document.querySelectorAll("#selection-suggested-people-list .suggestion-chip")]
      .find((c) => c.textContent.includes("Anh Tran"));
    assert.ok(!el.classList.contains("suggestion-chip-unsure"));
  });

  test("clicking it applies only to the photo that suggested it", async (t) => {
    // It used to apply to the whole selection: a suggestion for one photo, written
    // onto all 77 selected, is the opposite of what the suggestion meant.
    const ctx = await loadSelected(t, twoPhotos, suggestOnFirstOnly(twoPhotos, 0.63));
    const el = [...ctx.document.querySelectorAll("#selection-suggested-people-list .suggestion-chip")]
      .find((c) => c.textContent.includes("Anh Tran"));

    click(ctx.window, el);
    await flush(ctx.window, 6);

    const body = ctx.server.lastBody("/api/photos/bulk-tags");
    assert.ok(body, "clicking the suggestion sent nothing");
    assert.deepEqual(
      body.paths, [twoPhotos[0].path],
      `applied to photos that never suggested it: ${JSON.stringify(body.paths)}`
    );
    assert.deepEqual(body.add_tags, ["Anh Tran"]);
  });
});

describe("Apply All applies all of it", () => {
  // Reported as ambiguity, and it was: the panel offered 153 suggestions, the button
  // wrote the 141 scoring 0.75 or above, and the 12 it left were indistinguishable
  // from the ones it took. A button called Apply All that applies most of them is
  // worse than one that asks. What is offered is now what gets written.
  const photos = [photoRecord({ filename: "a.jpg", tags: [], people: [] })];

  async function applyAll(t, score) {
    const ctx = await loadSelected(t, photos, {
      status: "completed",
      suggestions: {
        [photos[0].path]: { people: [], tags: [{ tag: "Regatta", score }] },
      },
    });
    ctx.server.on("/api/folder/auto-apply", { success: true });
    ctx.window.confirm = () => true;
    ctx.document.getElementById("btn-folder-auto-apply").click();
    await flush(ctx.window, 6);
    return ctx;
  }

  test("it asks the server for every suggestion, not the confident ones", async (t) => {
    const ctx = await applyAll(t, 0.9);
    const body = ctx.server.lastBody("/api/folder/auto-apply");
    assert.ok(body, "Apply All sent nothing");
    assert.equal(body.threshold, 0.0,
      `a threshold was still sent, so some suggestions would be skipped: ${body.threshold}`);
  });

  test("a low-confidence suggestion still enables the button", async (t) => {
    // It used to stay disabled when everything on offer was below the bar, which
    // looked like there was nothing to apply when the panel plainly listed things.
    const ctx = await applyAll(t, 0.31);
    assert.equal(
      ctx.document.getElementById("btn-folder-auto-apply").disabled, false,
      "the button was disabled while suggestions were on screen"
    );
  });
});
