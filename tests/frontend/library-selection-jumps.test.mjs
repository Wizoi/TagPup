/**
 * The selection details of a library view in two halves (web/tagpup/selection-panel.js, #781): NAVIGATION above -- Folders to Organize,
 * People jump and Keyword jump, links that open a view -- and TAGGING below, a section that starts closed and keeps its choice per
 * browser, holding the tools the panel always had. Fictional names only.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { closeAllApps, click, openFolder, photoRecord } from "./harness.mjs";
import { loadViewPage, pageErrors } from "./view-page.mjs";

afterEach(() => {
  closeAllApps();
  assert.deepEqual(pageErrors(), [], "the page raised an error");
});

const el = (ctx, id) => ctx.document.getElementById(id);
const pick = (ctx, id) => click(ctx.window, ctx.cardById(id).querySelector(".thumbnail-checkbox"));
const links = (ctx, list) => [...ctx.document.querySelectorAll(`#${list} a.selection-jump-link`)];
const labels = (ctx, list) => links(ctx, list).map((a) => a.textContent);
const listText = (ctx, list) => el(ctx, list).textContent.replace(/\s+/g, " ").trim();
const TAGGING = "btn-selection-tagging";

const TALLY = {
  total: 3, more_tags: 0, more_people: 0,
  people: [{ name: "Wren Halloway", count: 2 }, { name: "Ash Brookmire", count: 3 }],
  tags: [
    { tag: "People/Wren Halloway", count: 2 }, { tag: "People/Ash Brookmire", count: 3 },
    { tag: "Zoo/Cats", count: 1 }, { tag: "Animals/Dogs", count: 3 }, { tag: "Trips/Coast", count: 2 },
  ],
  folders: { count: 1, listed: [{ path: "D:\\Library\\2020\\Event 01", name: "Event 01", photos: 3 }] },
};

async function view(t, { tally = TALLY, before } = {}) {
  const ctx = await loadViewPage(t, { search: "?view=all", ids: [11, 12, 13, 14, 15, 16, 17, 18], before });
  ctx.bulk.tally = tally;
  pick(ctx, 12);
  await ctx.settle(700);
  return ctx;
}

describe("People jump and Keyword jump", () => {
  test("each person of the selection is a link to their People view, in the shared order, with how many of the photos", async (t) => {
    const ctx = await view(t);
    assert.deepEqual(labels(ctx, "selection-people-jump"), ["Ash Brookmire", "Wren Halloway"]);
    const [ash] = links(ctx, "selection-people-jump");
    assert.equal(ash.getAttribute("href"), "?view=person&value=Ash+Brookmire");
    assert.deepEqual([...ctx.document.querySelectorAll("#selection-people-jump .selection-jump-item")].map((item) => item.textContent),
      ["Ash Brookmire (3)", "Wren Halloway (2)"]);
  });

  test("the keywords leave out the people already listed, and a folder the panel offers to Organize", async (t) => {
    const tally = { ...TALLY, tags: [...TALLY.tags, { tag: "D:\\Library\\2020\\Event 01", count: 3 }] };
    const ctx = await view(t, { tally });
    assert.deepEqual(labels(ctx, "selection-keyword-jump"), ["Animals/Dogs", "Trips/Coast", "Zoo/Cats"]);
    assert.equal(links(ctx, "selection-keyword-jump")[1].getAttribute("href"), "?view=keyword&value=Trips%2FCoast");
  });

  test("a click opens the People view as a click on the sidebar does: the address, the strip, the navigator, the selection left behind", async (t) => {
    const ctx = await view(t);
    assert.deepEqual(ctx.selectedIds(), [12]);
    const idsBefore = ctx.idsAsked.length;
    links(ctx, "selection-people-jump")[1].click();
    await ctx.settle(200);
    assert.equal(ctx.window.location.search, "?view=person&value=Wren+Halloway");
    assert.equal(ctx.state.library.kind, "person");
    assert.equal(ctx.state.library.value, "Wren Halloway");
    assert.equal(ctx.idsAsked.length, idsBefore + 1, "the view's order is asked for");
    assert.match(ctx.stripText(), /Wren Halloway/);
    assert.deepEqual(ctx.selectedIds(), [], "a view has its own selection: the old one is left behind");
    assert.equal(ctx.state.nav.shown, "library");
    assert.equal(ctx.state.nav.tab, "people");
    assert.deepEqual(ctx.selectedRows("people").map((row) => row.querySelector(".nav-label").textContent), ["Wren Halloway"]);
  });

  test("a keyword's link opens the Keywords view of that tag", async (t) => {
    const ctx = await view(t);
    links(ctx, "selection-keyword-jump")[1].click();
    await ctx.settle(200);
    assert.equal(ctx.window.location.search, "?view=keyword&value=Trips%2FCoast");
    assert.equal(ctx.state.library.kind, "keyword");
    assert.equal(ctx.state.library.value, "Trips/Coast");
    assert.equal(ctx.state.nav.tab, "keywords");
  });

  test("a link is a link: Ctrl-click and the middle button are the browser's (a new tab opens from the address)", async (t) => {
    const ctx = await view(t);
    const link = links(ctx, "selection-people-jump")[0];
    const asked = ctx.idsAsked.length;
    const event = new ctx.window.MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true });
    let leftToTheBrowser = null;
    ctx.window.addEventListener("click", (each) => {      // the last to see it: what the browser would be left to do
      leftToTheBrowser = !each.defaultPrevented;
      each.preventDefault();                              // (jsdom cannot navigate)
    });
    link.dispatchEvent(event);
    await ctx.settle(100);
    assert.equal(leftToTheBrowser, true, "the page leaves the click to the browser");
    assert.equal(ctx.idsAsked.length, asked);
    assert.equal(ctx.state.library.kind, "all");
    assert.equal(link.tagName, "A");
    assert.ok(link.getAttribute("href"));
  });

  test("a link's address carries the order the navigator reads views in, as a plain click does (#868)", async (t) => {
    const ctx = await view(t);
    ctx.state.nav.order = "name-desc";
    pick(ctx, 13);
    await ctx.settle(700);
    const [first] = links(ctx, "selection-people-jump");
    assert.match(first.getAttribute("href"), /&order=name-desc$/);
    first.click();
    await ctx.settle(200);
    assert.equal(ctx.state.library.order, "name-desc", "the plain click opens it in that order too");
  });

  test("a name is text, never markup", async (t) => {
    const tally = { ...TALLY, people: [{ name: "<img src=x onerror=alert(1)>", count: 1 }], tags: [{ tag: "Trips/<b>Coast</b>", count: 1 }] };
    const ctx = await view(t, { tally });
    assert.equal(ctx.document.querySelectorAll("#selection-people-jump img, #selection-keyword-jump b").length, 0);
    assert.deepEqual(labels(ctx, "selection-people-jump"), ["<img src=x onerror=alert(1)>"]);
    assert.deepEqual(labels(ctx, "selection-keyword-jump"), ["Trips/<b>Coast</b>"]);
  });

  test("none, and counting, and the server's 'more, not listed' are said", async (t) => {
    const ctx = await view(t, { tally: { total: 1, tags: [], people: [], more_tags: 4, more_people: 0 } });
    assert.equal(listText(ctx, "selection-people-jump"), "None");
    assert.equal(listText(ctx, "selection-keyword-jump"), "4 more, not listed");
    ctx.bulk.tally = () => new Promise(() => {});
    pick(ctx, 13);
    assert.equal(listText(ctx, "selection-people-jump"), "counting\u2026");
    assert.equal(listText(ctx, "selection-keyword-jump"), "counting\u2026");
  });
});

describe("a long list shows its first few and 'and N more'", () => {
  const MANY = {
    total: 30, more_tags: 0, more_people: 0, folders: { count: 1, listed: [] },
    people: Array.from({ length: 20 }, (_, i) => ({ name: `Person ${String(i + 1).padStart(2, "0")}`, count: 30 - i })),
    tags: Array.from({ length: 12 }, (_, i) => ({ tag: `Trips/Place ${String(i + 1).padStart(2, "0")}`, count: 2 })),
  };

  test("12 of 20 people, then all 20 and 'Show fewer'; the keywords (12) need no button", async (t) => {
    const ctx = await view(t, { tally: MANY });
    assert.equal(links(ctx, "selection-people-jump").length, 12);
    assert.equal(links(ctx, "selection-keyword-jump").length, 12);
    const more = ctx.document.querySelector("#selection-people-jump .selection-jump-more");
    assert.equal(more.textContent, "and 8 more");
    assert.equal(more.tagName, "BUTTON");
    assert.equal(more.getAttribute("aria-expanded"), "false");
    assert.equal(ctx.document.querySelector("#selection-keyword-jump .selection-jump-more"), null);
    more.click();
    assert.equal(links(ctx, "selection-people-jump").length, 20);
    const fewer = ctx.document.querySelector("#selection-people-jump .selection-jump-more");
    assert.equal(fewer.textContent, "Show fewer");
    assert.equal(fewer.getAttribute("aria-expanded"), "true");
    fewer.click();
    assert.equal(links(ctx, "selection-people-jump").length, 12);
  });
});

describe("the Tagging section", () => {
  test("it starts closed: a disclosure button that says so, and the editing tools out of the way", async (t) => {
    const ctx = await view(t);
    const button = el(ctx, TAGGING);
    assert.equal(button.tagName, "BUTTON");
    assert.equal(button.getAttribute("aria-expanded"), "false");
    assert.equal(button.getAttribute("aria-controls"), "selection-tagging-body");
    assert.ok(el(ctx, "selection-tagging-body").classList.contains("hidden"));
    assert.ok(el(ctx, "selection-tagging-body").contains(el(ctx, "bulk-add-people-input")));
    assert.ok(el(ctx, "selection-tagging-body").contains(el(ctx, "selection-people-list")));
    assert.ok(!el(ctx, "selection-navigation").classList.contains("hidden"));
  });

  test("opening it shows the people and tag tools exactly as they were, and the choice is kept in this browser", async (t) => {
    const ctx = await view(t);
    el(ctx, TAGGING).click();
    assert.equal(el(ctx, TAGGING).getAttribute("aria-expanded"), "true");
    assert.ok(!el(ctx, "selection-tagging-body").classList.contains("hidden"));
    assert.equal(ctx.window.localStorage.getItem("tagpup.selectionTagging"), "open");
    assert.deepEqual([...ctx.document.querySelectorAll("#selection-tags-list .selection-summary-chip")].map((c) => c.firstChild.textContent),
      ["Animals/Dogs (3)", "People/Ash Brookmire (3)", "People/Wren Halloway (2)", "Trips/Coast (2)", "Zoo/Cats (1)"]);
    assert.ok(ctx.document.querySelector("#selection-tags-list .selection-summary-chip-remove"));
    el(ctx, TAGGING).click();
    assert.equal(ctx.window.localStorage.getItem("tagpup.selectionTagging"), "closed");
    assert.ok(el(ctx, "selection-tagging-body").classList.contains("hidden"));
  });

  test("a page opened after it was left open starts open", async (t) => {
    const ctx = await view(t, { before: (window) => window.localStorage.setItem("tagpup.selectionTagging", "open") });
    assert.equal(el(ctx, TAGGING).getAttribute("aria-expanded"), "true");
    assert.ok(!el(ctx, "selection-tagging-body").classList.contains("hidden"));
  });

  test("a browser that keeps nothing still has the section, closed, and works", async (t) => {
    const ctx = await view(t, {
      before: (window) => {
        const real = window.localStorage;
        const off = (key) => String(key).startsWith("tagpup.selectionTagging");
        const stub = {
          getItem: (key) => { if (off(key)) throw new Error("storage is off"); return real.getItem(key); },
          setItem: (key, value) => { if (off(key)) throw new Error("storage is off"); real.setItem(key, value); },
          removeItem: (key) => real.removeItem(key),
        };
        Object.defineProperty(window, "localStorage", { get: () => stub, configurable: true });
      },
    });
    assert.equal(el(ctx, TAGGING).getAttribute("aria-expanded"), "false");
    el(ctx, TAGGING).click();
    assert.equal(el(ctx, TAGGING).getAttribute("aria-expanded"), "true");
  });

  test("Suggest's auto-apply is not in a library view", async (t) => {
    const ctx = await view(t);
    assert.ok(el(ctx, "selection-auto-apply").classList.contains("hidden"));
  });
});

describe("Organize's panel is not split", () => {
  test("its tagging is open with no button, auto-apply is there, and there is no navigation", async (t) => {
    const FOLDER = "D:\\Library\\2020\\Event 01";
    const scan = () => [1, 2, 3].map((n) => photoRecord({ filename: `IMG_000${n}.jpg`, path: `${FOLDER}\\IMG_000${n}.jpg`, tags: ["Trips/Coast"], people: [] }));
    const ctx = await loadViewPage(t, { search: "", scan });
    await openFolder(ctx, FOLDER);
    await ctx.settle(100);
    el(ctx, "btn-select-all-thumbnails").click();
    await ctx.settle(100);
    assert.ok(el(ctx, TAGGING).classList.contains("hidden"));
    assert.ok(!el(ctx, "selection-tagging-body").classList.contains("hidden"));
    assert.ok(!el(ctx, "selection-auto-apply").classList.contains("hidden"));
    assert.ok(el(ctx, "selection-navigation").classList.contains("hidden"));
  });
});
