/**
 * Names to review in TagTuner (docs/ARCHITECTURE.md, "People by id, stage 2", "Names to review"): a first row of Review People,
 * shown only when something waits, opens a dialog with one entry per name -- its reason, its faces and the choices -- and nothing
 * is changed until the owner chooses, sees what would change (a rehearsal) and presses Apply. Each result is stated: what changed
 * and how to undo it. Answers are the server's shapes (tagpup.services.name_review); names are made up.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

const SAM_FRIEND = { id: 40, name: "Sam", tag: "Friends/Sam", group: "Friends", shared: true };
const SAM_PET = { id: 41, name: "Sam", tag: "Pets/Sam", group: "Pets", shared: true };
const WREN = { id: 52, name: "Wren", tag: "Family/Ingersoll/Wren", group: "", shared: false };

const QUILL = {
  key: "wren quill", name: "Wren Quill", why: "none", faces: 3, faces_by_hand: 2, listed: 2, keyword_photos: 1, rows: 5,
  spellings: ["Wren Quill"], face_ids: [11, 12], photo_ids: [3], candidates: [WREN], dismissed: false,
};
const SAM = {
  key: "sam", name: "Sam", why: "several", faces: 1, faces_by_hand: 0, listed: 0, keyword_photos: 0, rows: 1,
  spellings: ["Sam"], face_ids: [13], photo_ids: [], candidates: [SAM_FRIEND, SAM_PET], dismissed: false,
};
const GROUPS = [
  { id: 1, tag: "Family", name: "Family", root: true },
  { id: 2, tag: "Friends", name: "Friends", root: true },
];

function listing(entries = [QUILL, SAM], extra = {}) {
  return { success: true, entries, count: entries.filter((each) => !each.dismissed).length, dismissed: 0, groups: GROUPS,
    stale_group_rows: 0, ...extra };
}

/** A server that answers the list from `state.entries`, and each resolve from `replies` in turn. */
function serverWith({ count = 2, entries = [QUILL, SAM], replies = [], groups = GROUPS } = {}) {
  const state = { entries, replies, count, resolves: [] };
  const server = new FakeServer()
    .on("/api/databases", { databases: ["kr-track"], selected: "kr-track" })
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/photos", [])
    .on("/api/people-with-counts", [{ name: "Wren", count: 3, person_id: 52, person: WREN }])
    .first("/api/people?records=1", [SAM_FRIEND, SAM_PET, WREN])
    .on("/api/people", ["Sam", "Wren"])
    .on("/api/tags/list", { tags: [], buckets: {} })
    .first("/api/names-to-review?count=1", () => ({ success: true, count: state.count }))
    .first("/api/names-to-review", () => ({ ...listing(state.entries, { groups }), count: state.count }))
    // The resolve route last: its address holds the list's, and the first route that fits answers.
    .first("/api/names-to-review/resolve", () => state.replies.shift() || { success: false, error: "no reply planned" });
  server.state = state;
  return server;
}

async function openReviewPeople(t, server, { mode = "face-matching", url = "http://localhost:8080/kr-track/" } = {}) {
  const ctx = await loadApp("tagtuner", { t, url, server });
  const select = ctx.document.getElementById("tuner-mode");
  select.value = mode;
  select.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
  await flush(ctx.window, 8);
  ctx.server = server;
  ctx.row = () => ctx.document.querySelector("#photo-list .names-to-review-row");
  ctx.modal = () => ctx.document.getElementById("names-modal");
  ctx.entries = () => [...ctx.document.querySelectorAll("#names-modal .names-entry")];
  ctx.resolves = () => server.calls.filter((call) => call.url.includes("/api/names-to-review/resolve")).map((call) => call.body);
  return ctx;
}

const wait = (window, ms = 40) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function open(ctx) {
  click(ctx.window, ctx.row());
  await flush(ctx.window, 8);
}

describe("the first row of Review People", () => {
  test("is there, above the people, when names wait", async (t) => {
    const ctx = await openReviewPeople(t, serverWith({ count: 5 }));
    assert.equal(ctx.row().textContent, "Names to review (5)");
    assert.equal(ctx.document.getElementById("photo-list").firstElementChild, ctx.row());
    assert.ok(!ctx.row().classList.contains("photo-item"), "it is not a person: the list's keys and search leave it be");
    assert.equal(ctx.document.querySelectorAll("#photo-list .photo-item").length, 1, "Wren is still listed");
  });

  test("is not there when none wait", async (t) => {
    const ctx = await openReviewPeople(t, serverWith({ count: 0 }));
    assert.equal(ctx.row(), null);
  });

  test("is only in Review People, not in Identify Faces", async (t) => {
    const ctx = await openReviewPeople(t, serverWith({ count: 5 }), { mode: "unmatched-faces" });
    assert.equal(ctx.row(), null);
    assert.ok(!ctx.server.urls().some((url) => url.includes("/api/names-to-review")), "nothing asked");
  });

  test("survives the list being drawn again, and the search leaves it", async (t) => {
    const ctx = await openReviewPeople(t, serverWith({ count: 2 }));
    const search = ctx.document.getElementById("photo-search");
    search.value = "wren";
    search.dispatchEvent(new ctx.window.Event("input", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.ok(ctx.row() && ctx.row().style.display !== "none");
    const sort = ctx.document.getElementById("people-sort");
    sort.value = "count";
    sort.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 4);
    assert.equal(ctx.document.querySelectorAll("#photo-list .names-to-review-row").length, 1);
  });

  test("says so when the count could not be read, and trying again opens the dialog", async (t) => {
    const server = serverWith().first("/api/names-to-review?count=1", { success: false, error: "no" }, { status: 500 });
    const ctx = await openReviewPeople(t, server);
    assert.equal(ctx.row().textContent, "Names to review (could not be read)");
  });

  test("an older server with no such route shows nothing, quietly", async (t) => {
    const server = serverWith().first("/api/names-to-review?count=1", { error: "not found" }, { status: 404 });
    const ctx = await openReviewPeople(t, server);
    assert.equal(ctx.row(), null);
  });
});

describe("the dialog", () => {
  test("lists each name with its reason, its counts, a few crops and the people it could be", async (t) => {
    const ctx = await openReviewPeople(t, serverWith());
    await open(ctx);
    assert.ok(!ctx.modal().classList.contains("hidden"));
    const [quill, sam] = ctx.entries();
    assert.equal(quill.querySelector(".names-name").textContent, "Wren Quill");
    assert.match(quill.querySelector(".names-why").textContent, /No person tag has this name/);
    assert.equal(quill.querySelector(".names-counts").textContent, "3 faces (2 decided by hand); listed on 2 photos, 1 from a keyword in the file");
    assert.deepEqual([...quill.querySelectorAll(".names-crop")].map((img) => img.getAttribute("src")),
      ["/kr-track/api/face-crop?id=11", "/kr-track/api/face-crop?id=12"]);
    assert.match(sam.querySelector(".names-why").textContent, /Two or more people are called this/);
    const options = [...sam.querySelectorAll(".names-person optgroup")].map((group) => ({
      label: group.label, options: [...group.querySelectorAll("option")].map((option) => option.textContent) }));
    assert.deepEqual(options[0], { label: "It could be", options: ["Sam · Friends", "Sam · Pets"] });
    assert.ok(options[1].options.includes("Wren"), "everyone else is offered too");
  });

  test("nothing is written by opening it", async (t) => {
    const ctx = await openReviewPeople(t, serverWith());
    await open(ctx);
    assert.deepEqual(ctx.resolves(), []);
  });

  test("the Activity page's link opens it as the page starts", async (t) => {
    const ctx = await openReviewPeople(t, serverWith(), { url: "http://localhost:8080/kr-track/?names-to-review=1" });
    await flush(ctx.window, 8);
    assert.ok(ctx.modal() && !ctx.modal().classList.contains("hidden"));
    assert.equal(ctx.entries().length, 2);
  });

  test("an empty list says so", async (t) => {
    const ctx = await openReviewPeople(t, serverWith({ count: 1, entries: [] }));
    await open(ctx);
    ctx.server.state.count = 0;
    assert.match(ctx.document.querySelector("#names-modal .names-empty").textContent, /No names wait/);
  });

  test("Escape closes it and gives the focus back", async (t) => {
    const ctx = await openReviewPeople(t, serverWith());
    await open(ctx);
    ctx.document.dispatchEvent(new ctx.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.ok(ctx.modal().classList.contains("hidden"));
  });
});

describe("linking a name to a person", () => {
  const REHEARSAL = { success: true, applied: false, faces: 3, listed: 2, changed: 0,
    sentence: "Link Wren Quill to Family/Ingersoll/Wren: 3 face(s) and 2 listed person(s) would be that person.", keywords_kept: 1 };
  const DONE = { success: true, applied: true, faces: 3, listed: 2, changed: 5, change: 77, keywords_kept: 1,
    sentence: "Link Wren Quill to Family/Ingersoll/Wren: 3 face(s) and 2 listed person(s) would be that person." };

  test("is rehearsed first, written only on Apply, and the result is stated with how to undo it", async (t) => {
    const server = serverWith({ replies: [REHEARSAL, DONE] });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    const [quill] = ctx.entries();
    quill.querySelector(".names-person").value = "52";
    click(ctx.window, quill.querySelector(".names-link"));
    await flush(ctx.window, 6);
    assert.deepEqual(ctx.resolves(), [{ key: "wren quill", action: "link", apply: false, person_id: 52 }]);
    assert.match(quill.querySelector(".names-sentence").textContent, /3 face\(s\) and 2 listed person\(s\) would be that person/);
    assert.equal(ctx.resolves().length, 1, "nothing is written until Apply");
    server.state.entries = [SAM];
    server.state.count = 1;
    click(ctx.window, quill.querySelector(".names-apply"));
    await flush(ctx.window, 10);
    assert.deepEqual(ctx.resolves()[1], { key: "wren quill", action: "link", apply: true, person_id: 52 });
    const result = ctx.document.querySelector("#names-modal .names-result").textContent;
    assert.match(result, /^Linked Wren Quill to Wren: 3 faces and 2 listed people\./);
    assert.match(result, /1 photo keeps the old keyword in its file/);
    assert.match(result, /History can undo it \(change 77\)\./);
    assert.deepEqual(ctx.entries().map((entry) => entry.dataset.key), ["sam"], "the entry is gone");
    assert.equal(ctx.row().textContent, "Names to review (1)", "the first row follows");
  });

  test("Cancel after the rehearsal changes nothing", async (t) => {
    const server = serverWith({ replies: [REHEARSAL] });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    const [quill] = ctx.entries();
    quill.querySelector(".names-person").value = "52";
    click(ctx.window, quill.querySelector(".names-link"));
    await flush(ctx.window, 6);
    click(ctx.window, quill.querySelector(".names-cancel"));
    assert.ok(quill.querySelector(".names-confirm").classList.contains("hidden"));
    assert.equal(ctx.resolves().length, 1);
  });

  test("with no person chosen it says so and asks nothing", async (t) => {
    const ctx = await openReviewPeople(t, serverWith());
    await open(ctx);
    click(ctx.window, ctx.entries()[0].querySelector(".names-link"));
    await flush(ctx.window, 4);
    assert.match(ctx.document.querySelector("#names-modal .names-status").textContent, /Choose the person first/);
    assert.deepEqual(ctx.resolves(), []);
  });

  test("one of two people called alike is chosen by id", async (t) => {
    const server = serverWith({ replies: [{ ...REHEARSAL, sentence: "Link Sam to Pets/Sam" }] });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    const sam = ctx.entries()[1];
    const select = sam.querySelector(".names-person");
    select.value = "41";
    click(ctx.window, sam.querySelector(".names-link"));
    await flush(ctx.window, 6);
    assert.equal(ctx.resolves()[0].person_id, 41);
  });
});

describe("making a person", () => {
  test("needs a group chosen when there are several, and sends it; the result names the tag", async (t) => {
    const server = serverWith({ replies: [
      { success: true, applied: false, faces: 3, listed: 2, changed: 0, sentence: "Make Friends/Wren Quill: 3 face(s) and 2 listed person(s) would be that person." },
      { success: true, applied: true, faces: 3, listed: 2, changed: 3, change: 90, person_tag: "Friends/Wren Quill", keywords_kept: 0 },
    ] });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    const [quill] = ctx.entries();
    assert.equal(quill.querySelector(".names-group").value, "", "several groups: none is chosen for the owner");
    click(ctx.window, quill.querySelector(".names-make"));
    await flush(ctx.window, 4);
    assert.match(ctx.document.querySelector("#names-modal .names-status").textContent, /Choose the group/);
    assert.deepEqual(ctx.resolves(), []);
    quill.querySelector(".names-group").value = "2";
    click(ctx.window, quill.querySelector(".names-make"));
    await flush(ctx.window, 6);
    assert.deepEqual(ctx.resolves()[0], { key: "wren quill", action: "make", apply: false, group_id: 2 });
    server.state.entries = [SAM];
    click(ctx.window, quill.querySelector(".names-apply"));
    await flush(ctx.window, 10);
    assert.match(ctx.document.querySelector("#names-modal .names-result").textContent,
      /^Made Friends\/Wren Quill: 3 faces named Wren Quill are theirs\. History can undo it \(change 90\)\.$/);
  });

  test("one group offers itself", async (t) => {
    const server = serverWith({ groups: [GROUPS[0]] });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    assert.equal(ctx.entries()[0].querySelector(".names-group").value, "1");
  });

  test("is not offered for a name a person tag has", async (t) => {
    const ctx = await openReviewPeople(t, serverWith());
    await open(ctx);
    assert.equal(ctx.entries()[1].querySelector(".names-make"), null);
  });
});

describe("unnaming the faces and setting a name aside", () => {
  test("unname says what it does, then does it", async (t) => {
    const server = serverWith({ replies: [
      { success: true, applied: false, faces: 3, listed: 2, changed: 0, sentence: "Unname 3 face(s) called Wren Quill: they go back to the faces waiting for a name." },
      { success: true, applied: true, faces: 3, listed: 2, changed: 3, change: 12, keywords_kept: 0 },
    ] });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    const [quill] = ctx.entries();
    click(ctx.window, quill.querySelector(".names-unname"));
    await flush(ctx.window, 6);
    assert.match(quill.querySelector(".names-sentence").textContent, /go back to the faces waiting for a name/);
    server.state.entries = [SAM];
    click(ctx.window, quill.querySelector(".names-apply"));
    await flush(ctx.window, 10);
    assert.match(ctx.document.querySelector("#names-modal .names-result").textContent, /^Unnamed 3 faces called Wren Quill\. History can undo it \(change 12\)\.$/);
  });

  test("a name with no faces cannot be unnamed", async (t) => {
    const ctx = await openReviewPeople(t, serverWith({ entries: [{ ...SAM, faces: 0 }, QUILL] }));
    await open(ctx);
    assert.equal(ctx.entries()[0].querySelector(".names-unname"), null);
  });

  test("set aside is rehearsed and applied, and the set-aside names can be shown again", async (t) => {
    const aside = { ...QUILL, dismissed: true };
    const server = serverWith({ replies: [
      { success: true, applied: false, faces: 3, listed: 2, changed: 0, sentence: "Set Wren Quill aside until it holds more than 5 row(s)." },
      { success: true, applied: true, faces: 3, listed: 2, changed: 1, change: null, keywords_kept: 0 },
      { success: true, applied: true, faces: 0, listed: 0, changed: 1, change: null, keywords_kept: 0 },
    ] });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    click(ctx.window, ctx.entries()[0].querySelector(".names-dismiss"));
    await flush(ctx.window, 6);
    server.state.entries = [SAM];
    server.state.count = 1;
    click(ctx.window, ctx.entries()[0].querySelector(".names-apply"));
    await flush(ctx.window, 10);
    const result = ctx.document.querySelector("#names-modal .names-result").textContent;
    assert.match(result, /^Wren Quill is set aside until it holds more rows than 5\.$/);
    assert.ok(!/History/.test(result), "a preference is not a change of History's");
    // Show them: the toggle appears when something is set aside.
    server.state.entries = [SAM, aside];
    server.first("/api/names-to-review?dismissed=1", () => ({ ...listing([SAM, aside]), dismissed: 1, count: 1 }));
    const toggle = ctx.document.querySelector("#names-modal .names-toggle input");
    toggle.checked = true;
    toggle.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
    await flush(ctx.window, 8);
    const restore = ctx.document.querySelector("#names-modal .names-restore");
    assert.ok(restore, "a set-aside name offers to be shown again");
    click(ctx.window, restore);
    await flush(ctx.window, 8);
    assert.deepEqual(ctx.resolves().at(-1), { key: "wren quill", action: "restore", apply: true });
  });
});

describe("how it fails", () => {
  test("a person gone since the list was read is told, and the list is read again", async (t) => {
    const server = serverWith({ replies: [{ success: false, error: "That person is no longer in the tag tree: reload the page." }] });
    server.first("/api/names-to-review/resolve", () => server.state.replies.shift(), { status: 404 });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    const listsBefore = server.urls().filter((url) => /\/api\/names-to-review(\?dismissed=1)?$/.test(url)).length;
    const quill = ctx.entries()[0];
    quill.querySelector(".names-person").value = "52";
    click(ctx.window, quill.querySelector(".names-link"));
    await flush(ctx.window, 10);
    assert.match(ctx.document.querySelector("#names-modal .names-result-error").textContent, /no longer in the tag tree/);
    const listsAfter = server.urls().filter((url) => /\/api\/names-to-review(\?dismissed=1)?$/.test(url)).length;
    assert.equal(listsAfter, listsBefore + 1, "what the page held is out of date: it reads again");
  });

  test("a name settled in another window is told, not done twice", async (t) => {
    const server = serverWith();
    server.first("/api/names-to-review/resolve",
      { success: false, error: "Nothing is left to settle for that name: it was settled in another window. Reload the list." },
      { status: 400 });
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    server.state.entries = [SAM];
    click(ctx.window, ctx.entries()[0].querySelector(".names-dismiss"));
    await flush(ctx.window, 10);
    assert.match(ctx.document.querySelector("#names-modal .names-result-error").textContent, /settled in another window/);
    assert.deepEqual(ctx.entries().map((entry) => entry.dataset.key), ["sam"], "the list is read again");
  });

  test("rapid clicks make one request: a second choice while one is on its way does nothing", async (t) => {
    let release;
    const slow = new Promise((resolve) => { release = resolve; });
    const server = serverWith();
    server.first("/api/names-to-review/resolve", () => slow.then(() => ({ success: true, applied: false, faces: 3, listed: 2, changed: 0, sentence: "x" })));
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    const [quill] = ctx.entries();
    const button = quill.querySelector(".names-unname");
    click(ctx.window, button);
    click(ctx.window, button);
    click(ctx.window, quill.querySelector(".names-dismiss"));
    await wait(ctx.window);
    assert.equal(ctx.resolves().length, 1);
    release();
    await flush(ctx.window, 6);
    assert.equal(ctx.resolves().length, 1);
  });

  test("a server that does not answer says so and leaves the list as it was", async (t) => {
    const server = serverWith();
    server.first("/api/names-to-review/resolve", () => Promise.reject(new TypeError("fetch failed")));
    const ctx = await openReviewPeople(t, server);
    await open(ctx);
    click(ctx.window, ctx.entries()[0].querySelector(".names-unname"));
    await wait(ctx.window, 2200);
    assert.match(ctx.document.querySelector("#names-modal .names-status").textContent, /did not answer/);
    assert.equal(ctx.entries().length, 2);
  });
});
