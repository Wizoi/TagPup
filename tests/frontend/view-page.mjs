/**
 * The TagPup page on a library, for the tests of the library views' navigator, moves, staleness and keys (phase 9c): a
 * FakeServer that answers what the page asks of the library -- the navigator's counts, the order of a view, its cards,
 * a photo, the membership of a folder, when the library was last in step -- and a layout for the grid, which jsdom does
 * not have: a 600 px view, four columns of 200 px cards, a 16 px gap.
 *
 * The library is invented: 2,746 folders, 895 keyword nodes, 413 people and 61 years, the sizes photo_index has. Every
 * name is fictional; the real library is photographs of real people, many of them minors.
 */
import { FakeServer, loadApp, flush, pageExports, photoRecord } from "./harness.mjs";

/** Every page loaded by this file's tests: afterwards each is checked for an error the page itself raised. */
export const opened = [];
export function pageErrors() {
  const found = opened.flatMap((ctx) => ctx.consoleErrors.map(String));
  opened.length = 0;
  return found;
}

/** What GET /api/library/bulk/status says of a job: every field the page reads, as tagpup.jobs.bulk_edits.Job.status makes it. */
export const jobStatus = (extra = {}) => ({
  job: 7, op: "tags", state: "running", total: 100, done: 0, changed: 0, unchanged: 0, skipped_missing: 0, skipped_damaged: 0,
  errors: [], error_count: 0, started: 1760000000, finished: null, eta_seconds: null, message: null, cancelling: false,
  resumable: false, what: "bulk tags: add 1 tag(s), 0 of 100 photos", ...extra,
});

/**
 * Make the page's slow clocks fast: a timer of a second or more runs after a hundredth of that (a poll of the bulk job every second
 * is every 10 ms). The delays the page asked for are kept in `ctx.delays`, as it asked for them.
 */
export function speedUp(ctx) {
  const real = ctx.window.setTimeout.bind(ctx.window);
  ctx.delays = [];
  ctx.window.setTimeout = (fn, ms, ...rest) => {
    if (ms >= 1000) ctx.delays.push(ms);
    return real(fn, ms >= 1000 ? ms / 100 : ms, ...rest);
  };
}

export const GRID_TOP = 100;
export const STRIDE = 216;
export const COLUMNS = 4;

const GIVEN = ["Wren", "Rowan", "Hazel", "Ash", "Juniper", "Basil", "Linden", "Marlow", "Sorrel", "Tamsin", "Orla", "Perrin", "Quill"];
const FAMILY = ["Halloway", "Thackeray", "Brookmire", "Ashdown", "Pemberley", "Fairweather", "Gantry", "Holloway", "Ironside", "Kettleby"];

/** 2,746 folders: a library folder, 40 years under it, and 2,705 events under the years; each event with its photos. */
export function syntheticFolders() {
  const folders = [];
  const root = "D:\\Library";
  folders.push({ path: root, name: "Library", parent: null, direct: 0, recursive: 0 });
  let total = 0;
  for (let y = 0; y < 40; y++) {
    const year = 1985 + y;
    const yearPath = `${root}\\${year}`;
    const events = y < 39 ? 68 : 53;
    const yearEntry = { path: yearPath, name: String(year), parent: root, direct: 2, recursive: 2 };
    folders.push(yearEntry);
    for (let e = 0; e < events; e++) {
      const direct = 10 + ((y * 7 + e) % 40);
      folders.push({ path: `${yearPath}\\Event ${String(e + 1).padStart(2, "0")}`, name: `Event ${String(e + 1).padStart(2, "0")}`, parent: yearPath, direct, recursive: direct });
      yearEntry.recursive += direct;
    }
    total += yearEntry.recursive;
  }
  folders[0].recursive = total;
  return folders;
}

/** 895 keyword nodes: 5 roots, 150 below them, 740 below those (tags are full paths with a slash). */
export function syntheticKeywords() {
  const names = ["Trips", "Activity", "Events", "Places", "People"];
  const nodes = [];
  for (const root of names) nodes.push({ tag: root, name: root, parent: null, count: 0 });
  for (const root of names) {
    for (let i = 0; i < 30; i++) {
      const tag = `${root}/Group ${String(i).padStart(2, "0")}`;
      nodes.push({ tag, name: `Group ${String(i).padStart(2, "0")}`, parent: root, count: 0 });
    }
  }
  let leaves = 0;
  const second = nodes.filter((n) => n.parent !== null);
  for (let i = 0; leaves < 740; i++) {
    const parent = second[i % second.length];
    const name = `Item ${String(leaves).padStart(3, "0")}`;
    nodes.push({ tag: `${parent.tag}/${name}`, name, parent: parent.tag, count: 3 + (leaves % 50) });
    leaves += 1;
  }
  const byTag = new Map(nodes.map((n) => [n.tag, n]));
  for (const n of nodes) {
    if (n.count && n.parent) for (let up = byTag.get(n.parent); up; up = up.parent ? byTag.get(up.parent) : null) up.count += n.count;
  }
  return nodes;
}

/** 413 people, fictional. */
export function syntheticPeople() {
  const people = [];
  for (let i = 0; i < 413; i++) {
    people.push({ name: `${GIVEN[i % GIVEN.length]} ${FAMILY[Math.floor(i / GIVEN.length) % FAMILY.length]}${i >= 130 ? " " + (Math.floor(i / 130) + 1) : ""}`, count: 5 + (i * 37) % 900 });
  }
  return people;
}

/**
 * 61 years: 1970 up to this year, and as many years that are not dates (the year 1, 2099, ...) as make up the 61. Each
 * carries `implausible` as the route says it (tagpup.store.library_view.plausible_year: before 1900 or after next year;
 * tests/test_library_view.py holds the rule itself): the page only shows it.
 */
export function syntheticDates(thisYear = new Date().getFullYear()) {
  const years = [];
  const make = (year, count) => {
    const months = [];
    for (let m = 12; m >= 1; m--) months.push({ month: `${String(year).padStart(4, "0")}-${String(m).padStart(2, "0")}`, count: Math.floor(count / 12) });
    return { year, count, months, other: count - 12 * Math.floor(count / 12), implausible: year < 1900 || year > thisYear + 1 };
  };
  for (let year = 1970; year <= thisYear; year++) years.push(make(year, 600 + year % 97));
  const junk = [1, 1899, 1901, 2099, 2100, 2200, 9999, 1969, 1950, 1000];
  for (let i = 0; years.length < 61; i++) years.push(make(junk[i], 3 + i));
  return { years: years.sort((a, b) => a.year - b.year), undated: 1180 };
}

const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
const frame = (window) => new Promise((resolve) => window.requestAnimationFrame(() => resolve()));

export const cardOf = (id, extra = {}) => ({
  id, name: `IMG_${id}.jpg`, path: `D:\\Library\\2020\\Event 01\\IMG_${id}.jpg`, taken: "2020:01:01 10:00:00",
  damaged: false, damage: null, thumb: `/api/photo-thumb?id=${id}&v=1600000000.0`, ...extra,
});
export const recordOf = (id, extra = {}) => photoRecord({
  id, filename: `IMG_${id}.jpg`, path: `D:\\Library\\2020\\Event 01\\IMG_${id}.jpg`, tags: ["Trips/Coast"], people: [],
  taken: "2020:01:01 10:00:00", ...extra,
});

/**
 * Load the page. `search` is the address's query; `navigator` the four sections' answers (each data, or { status, body },
 * or a function of the URL returning either, or a promise of it); `ids` the order of a view; `cardExtra(id)` extra fields
 * of a card; `membership` and `sync` the answers to those two.
 */
export async function loadViewPage(t, {
  search = "", library = "photo_index", navigator = {}, ids = [11, 12, 13, 14, 15, 16, 17, 18], onIds, onCards, onPhoto,
  cardExtra, membership = { photos_not_held: 0 }, sync = { library: "photo_index", last_run: null, last_in_step: null, syncing: false },
  find, layout = true, scan, before,
} = {}) {
  const server = new FakeServer();
  const ctx = { server, held: [], navigatorAsked: [], idsAsked: [], cardsAsked: [], photosAsked: [], membershipAsked: [], syncAsked: [], hold: {} };
  // What the library says of bulk edits (phase 9d): `current` for GET .../current, `status` for .../status and .../cancel, `start`
  // and `resume` what those routes add to their answers, `tally` for the selection's tally. A test changes them as the server would.
  ctx.bulk = {
    current: null, status: jobStatus(), start: {}, resume: {},
    tally: { total: 0, tags: [], more_tags: 0, people: [], more_people: 0 },
  };
  const answer = (key, make) => (url) => {
    const reply = make(url);
    if (!ctx.hold[key]) return reply;
    return new Promise((resolve) => ctx.held.push({ key, release: () => resolve(reply) }));
  };
  const section = (name) => (url) => {
    ctx.navigatorAsked.push(name);
    const given = navigator[name];
    const body = typeof given === "function" ? given(url) : given;
    if (body === undefined) {
      const stock = { folders: { folders: syntheticFolders() }, keywords: { keywords: syntheticKeywords() }, people: { people: syntheticPeople() }, dates: syntheticDates() };
      return stock[name];
    }
    return body;
  };
  server
    .on("/api/tags", [])
    .on("/api/people", [])
    .on("/api/taxonomy/tree", [])
    .on("/api/databases", { databases: ["photo_index", "other"], selected: library })
    .on("/api/folder/index-status", { status: "completed", percent: 100, message: "Ready" })
    .on("/api/folder/suggest-status", { status: "idle" })
    .on("/api/folder/damaged", { photos: [] })
    .on("/api/photo-faces", { faces: [], total: 0, unmatched: 0 })
    .on("/api/photo/save-metadata", { success: true })
    .on("/api/photo/delete", { success: true })
    .on("/api/photos/bulk-tags", { success: true })
    .on("/api/library/bulk/current", () => ({ success: true, job: typeof ctx.bulk.current === "function" ? ctx.bulk.current() : ctx.bulk.current }))
    .on("/api/library/bulk/status", () => ({ success: true, ...ctx.bulk.status }))
    .on("/api/library/bulk/start", (url) => ({ success: true, job: 7, op: "tags", total: 0, requested: 0, missing: 0, excluded: 0, ...ctx.bulk.start }))
    .on("/api/library/bulk/cancel", () => ({ success: true, ...ctx.bulk.status }))
    .on("/api/library/bulk/resume", () => ({ success: true, job: 7, total: 0, done: 0, ...ctx.bulk.resume }))
    .on("/api/library/selection/tally", () => (typeof ctx.bulk.tally === "function" ? ctx.bulk.tally() : ctx.bulk.tally))
    .on("/api/folder/scan", () => (scan ? scan() : [photoRecord({ filename: "a.jpg" }), photoRecord({ filename: "b.jpg" })]))
    .on("/api/folder/membership", answer("membership", (url) => {
      ctx.membershipAsked.push(url);
      return typeof membership === "function" ? membership(url) : membership;
    }))
    .on("/api/sync", answer("sync", (url) => {
      ctx.syncAsked.push(url);
      return typeof sync === "function" ? sync(url) : sync;
    }));
  for (const name of ["folders", "keywords", "people", "dates"]) {
    server.on(`/api/library/navigator?section=${name}`, answer(name, section(name)));
  }
  server.on("/api/library/ids", answer("ids", (url) => {
    ctx.idsAsked.push(url);
    if (onIds) return onIds(url);
    return { source: {}, total: ids.length, ids, complete: true };
  }));
  server.on("/api/library/cards", answer("cards", (url) => {
    const asked = decodeURIComponent((url.match(/ids=([^&]+)/) || [])[1] || "").split(",").map(Number);
    ctx.cardsAsked.push(asked);
    if (onCards) return onCards(asked, url);
    return { cards: asked.map((id) => cardOf(id, cardExtra ? cardExtra(id) : {})) };
  }));
  server.on("/api/library/photo", (url) => {
    const id = Number((url.match(/id=(\d+)/) || [])[1]);
    ctx.photosAsked.push(id);
    return onPhoto ? onPhoto(id) : { photo: recordOf(id) };
  });
  server.on("/api/library/find", (url) => (find ? find(url) : { id: 12 }));
  const loaded = await loadApp("tagpup", { t, url: `http://localhost:8090/${library}/${search}`, server, before });
  Object.assign(ctx, loaded);
  ctx.window.alert = () => {};
  ctx.window.confirm = () => true;
  const { state } = pageExports(ctx.window, "web/tagpup/state.js");
  ctx.state = state;
  const scroller = ctx.document.getElementById("folder-view-main");
  const content = ctx.document.getElementById("folder-view-content");
  const here = { scrollTop: 0, viewport: 600, columns: COLUMNS, cardHeight: 200, rowGap: 16, gridTop: GRID_TOP, cardWidth: 150, columnGap: 16 };
  Object.defineProperty(scroller, "scrollTop", { get: () => here.scrollTop, set: (v) => { here.scrollTop = v; }, configurable: true });
  Object.defineProperty(scroller, "clientHeight", { get: () => (content.classList.contains("hidden") ? 0 : here.viewport), configurable: true });
  if (layout) {
    state.grid.setMeasure(() => {
      if (content.classList.contains("hidden")) {
        here.scrollTop = 0;
        return { ...here, viewport: 0 };
      }
      return { ...here };
    });
    state.grid.refresh();
  }
  const module = (file) => pageExports(ctx.window, `web/tagpup/${file}`);
  Object.assign(ctx, {
    here, scroller, module,
    // What is selected in the view: the ids, in the view's order, whichever way the page holds them (selected.js).
    selectedIds: () => {
      const { sel, ids } = ctx.state.library;
      return sel.mode === "ids" ? ids.filter((id) => sel.ids.has(id)) : ids.filter((id) => !sel.excluded.has(id));
    },

    cards: () => [...ctx.document.querySelectorAll("#thumbnails-grid .thumbnail-card")],
    real: () => ctx.cards().filter((c) => !c.classList.contains("placeholder")),
    cardById: (id) => ctx.document.querySelector(`#thumbnails-grid [data-id="${id}"]`),
    scrollTo: async (top) => {
      here.scrollTop = top;
      scroller.dispatchEvent(new ctx.window.Event("scroll"));
      await frame(ctx.window);
    },
    strip: () => ctx.document.getElementById("library-strip"),
    stripText: () => ctx.strip().textContent.replace(/\s+/g, " ").trim(),
    settle: async (ms = 160) => { await wait(ctx.window, ms); await flush(ctx.window, 6); },
    release: async (key) => {
      const waiting = ctx.held.filter((each) => !key || each.key === key);
      ctx.held = ctx.held.filter((each) => !waiting.includes(each));
      waiting.forEach((each) => each.release());
      await flush(ctx.window, 6);
    },
    // The navigator
    pane: (name) => ctx.document.getElementById(`sidebar-pane-${name}`),
    paneTab: (name) => ctx.document.getElementById(`sidebar-tab-${name}`),
    tab: (name) => ctx.document.getElementById(`nav-tab-${name}`),
    panel: (name) => ctx.document.getElementById(`nav-panel-${name}`),
    rows: (name) => [...ctx.document.querySelectorAll(`#nav-panel-${name} .nav-row`)],
    rowByLabel: (name, label) => ctx.rows(name).find((row) => row.querySelector(".nav-label").textContent === label),
    row: (name, id) => ctx.rows(name).find((each) => each.dataset.row === id) || null,
    selectedRows: (name) => ctx.rows(name).filter((row) => row.getAttribute("aria-selected") === "true"),
    status: (name) => ctx.document.querySelector(`#nav-panel-${name} .nav-status`).textContent,
    note: (name) => ctx.document.querySelector(`#nav-panel-${name} .nav-note`).textContent,
    openTab: async (name) => {
      ctx.tab(name).click();
      await ctx.settle(20);
    },
    showLibraryPane: async () => {
      ctx.paneTab("library").click();
      await ctx.settle(20);
    },
    // Wait (in 40 ms steps, at most `ms`) for something that arrives after a timer or a request, not for a fixed time.
    until: async (check, ms = 4000) => {
      for (let waited = 0; waited < ms && !check(); waited += 40) await ctx.settle(40);
      return check();
    },
    key: (el, key, init = {}) => {
      const event = new ctx.window.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
      el.dispatchEvent(event);
      return event;
    },
    popTo: async (search) => {
      ctx.window.history.replaceState({}, "", `/${library}/${search}`);
      ctx.window.dispatchEvent(new ctx.window.PopStateEvent("popstate", { state: {} }));
      await ctx.settle(60);
    },
  });
  await ctx.settle();
  opened.push(ctx);
  return ctx;
}
