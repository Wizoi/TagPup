// TagPup's page: the navigator beside the grid (docs/ARCHITECTURE.md, phase 9c). The sidebar is two panes, the open
// folder's file list and the library's navigator, and a switch between them; the navigator is four sections as
// tabs -- Folders, Keywords, People, Dates -- each a tree or a list of what the library holds with the photos
// counted, a filter box, and nothing drawn that is not on screen. A click opens the library view of the item
// (library-view.js openLibraryView), so the address names the source and Back, Forward and a bookmark work, and the
// item that is open is the one highlighted, in the tab it belongs to.
//
// Each section is read from GET /api/library/navigator?section= when its tab is first opened, and again -- the counts
// are read at each call -- after an edit that changes counts (debounced), and on Refresh view. An answer that is not
// the newest asked for its section is dropped, and a section is painted into its own panel only: a tab opened while
// another loads, or a source followed before its section is here, paints nowhere else. A section that cannot be read
// says why in a sentence (the server's: a library at an older schema, an unplaced root); never a trace.
//
// What is drawn is navigator-model.js's rows through navigator-tree.js: at most 1,500 for a tree's open branches, 5,000 for a flat list.
//
// SEVERAL ROWS AT ONCE (the owner's review, #672), in every tab: a click selects a row (and every row under it) and opens
// its view; Ctrl-click adds a row or takes it away; Shift-click selects every row drawn from the last one picked to this one
// (Ctrl+Shift adds them); Ctrl+A every row drawn; the keys are Space and Enter, Ctrl+Space and Shift+Space. The view is the
// union of the rows selected: one source when it is one row, else `any_of` their sources (navigator-model.js compress), so the
// address holds the selection and Back, Forward and a bookmark restore it. A row selected stays selected when the filter
// hides it, and when another tab is shown; Ctrl-click in another tab adds its rows to the same union; a plain click anywhere
// starts again. A click that would leave nothing selected leaves the selection as it is.
//
// The sort (#671) is one control above the tabs, for every tab: Date taken or Name, either way; the view open is read again
// in the order chosen, and the views opened after it are read in it. The address holds it (`order`).
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import {
    sidebarPaneFolder, sidebarPaneLibrary, sidebarSwitch, sidebarTabFolder, sidebarTabLibrary
} from './elements.js';
import { openLibraryView } from './library-view.js';
import { DEFAULT_ORDER, LIBRARY_ORDERS, MAX_MEMBERS, ORDER_LABELS, addressTooLong } from './library-source.js';
import {
    compress, indexDates, indexFolders, indexKeywords, indexPeople, membersOf, peopleGroupIds, rowTree, sectionOf, sectionRows,
    selectedRows, specOfMembers, withRowsUnder
} from './navigator-model.js';
import { paintRows, rowElement, tabKeyTarget, treeKeyAction } from './navigator-tree.js';

const NAV_ORDER = ['folders', 'keywords', 'people', 'dates'];

const NAV_SECTIONS = {
    folders: {
        label: 'Folders', tree: true, noun: 'folders', loading: 'Loading the folders...',
        empty: 'The library holds no photos in any folder yet.', build: body => indexFolders(body.folders),
    },
    keywords: {
        label: 'Keywords', tree: true, noun: 'keywords', loading: 'Loading the keywords...',
        empty: 'The library has no keywords yet.', build: body => indexKeywords(body.keywords),
    },
    people: {
        label: 'People', tree: true, noun: 'people', loading: 'Loading the people...',
        empty: 'No photo names a person yet.', build: body => indexPeople(body.people, body.groups, body.unfiled),
    },
    dates: {
        label: 'Dates', tree: true, noun: 'dates', loading: 'Loading the dates...',
        empty: 'No photo has a date yet.', build: body => indexDates(body),
    },
};

/** How long a filter waits for the next key before it filters. */
export const NAV_FILTER_MS = 120;
/** How long after an edit the counts are read again: edits come in runs, and one read answers them all. */
export const NAV_COUNTS_MS = 1200;

function panelOf(name) {
    return document.getElementById(`nav-panel-${name}`);
}

function partsOf(name) {
    const panel = panelOf(name);
    return {
        panel,
        filter: panel.querySelector('.nav-filter'),
        status: panel.querySelector('.nav-status'),
        tree: panel.querySelector('.nav-tree'),
        note: panel.querySelector('.nav-note'),
    };
}

// ---- Drawing a section ----------------------------------------------------------------------

/** Is this section on screen: the library pane shown, and this its tab? Only that is drawn. */
function onScreen(name) {
    return state.nav.shown === 'library' && state.nav.tab === name;
}

function paintSection(name) {
    const sec = state.nav.sections[name];
    const cfg = NAV_SECTIONS[name];
    const { status, tree, note } = partsOf(name);
    const chosen = sec.index && state.nav.followed ? selectedRows(name, sec.index, state.nav.followed) : { rows: new Set(), open: [] };
    let reveal = false;
    if (chosen.rows.size && sec.reveal) {
        for (const id of chosen.open) sec.expanded.add(id);
        sec.reveal = false;
        reveal = true;
    }
    const { rows, hidden } = sectionRows(name, sec.index, sec.expanded, sec.filter);
    sec.rows = rows;
    paintRows(tree, rows, { selected: chosen.rows, currentId: sec.currentId, tree: cfg.tree });
    const wanted = sec.filter.trim();
    let said = '';
    if (sec.status === 'loading' && !sec.index) said = cfg.loading;
    else if (sec.message) said = sec.message;
    else if (sec.notice) said = sec.notice;
    else if (sec.index && rows.length === 0) said = wanted ? `Nothing matches “${wanted}”.` : cfg.empty;
    setSaid(status, said, Boolean(sec.message || sec.notice));
    let more = '';
    if (hidden > 0) more = `${hidden.toLocaleString()} more are not shown. Type in the filter to narrow the list.`;
    else if (name === 'dates' && sec.index && sec.index.undated > 0 && !wanted) {
        more = `${sec.index.undated.toLocaleString()} ${sec.index.undated === 1 ? 'photo has' : 'photos have'} no date.`;
    }
    if (wanted && chosen.rows.size) {
        // The filter hides rows; what is selected stays selected, and says so.
        const drawnIds = new Set(rows.map(row => row.id));
        const unseen = [...chosen.rows].filter(id => !drawnIds.has(id)).length;
        if (unseen) more = `${more ? `${more} ` : ''}${unseen.toLocaleString()} selected ${unseen === 1 ? 'row is' : 'rows are'} hidden by the filter and still selected.`;
    }
    note.textContent = more;
    note.classList.toggle('hidden', !more);
    tree.setAttribute('aria-busy', sec.status === 'loading' ? 'true' : 'false');
    if (reveal) {
        // The row just clicked if it is selected, else the first selected row drawn.
        const target = chosen.rows.has(sec.currentId) ? sec.currentId : (rows.find(row => chosen.rows.has(row.id)) || {}).id;
        const el = target ? rowElement(tree, target) : null;
        if (el) el.scrollIntoView({ block: 'nearest' });
    }
}

function setSaid(el, text, problem) {
    if (el.textContent !== text) el.textContent = text;
    el.classList.toggle('hidden', !text);
    el.classList.toggle('nav-problem', problem);
}

function paintIfShown(name) {
    if (onScreen(name)) paintSection(name);
}

// ---- Reading a section ----------------------------------------------------------------------

/**
 * Ask the library for a section's counts. `quiet`: the rows that are there stay while it is read (counts after an
 * edit, Refresh view), so nothing flickers and what is open stays open. Resolves when this ask is answered or dropped.
 */
function loadSection(name, quiet) {
    const sec = state.nav.sections[name];
    const cfg = NAV_SECTIONS[name];
    sec.asked += 1;
    const token = sec.asked;
    sec.stale = false;
    if (!quiet || !sec.index) {
        sec.status = 'loading';
        sec.message = '';
    }
    paintIfShown(name);
    return api.fetch(`/api/library/navigator?section=${name}`)
        .then(res => res.json().catch(() => ({})).then(body => ({ ok: res.ok, status: res.status, body })))
        .then(({ ok, status, body }) => {
            if (token !== sec.asked) return;             // a newer ask is out: this answer is not the section's
            if (!ok) {
                const failure = new Error((body && body.error) || `The library answered ${status}.`);
                failure.fromServer = true;
                throw failure;
            }
            sec.index = cfg.build(body || {});
            sec.status = 'ready';
            sec.message = '';
            if (!sec.opened) {
                // People's branches start open: the people show under their headers (#673).
                for (const id of peopleGroupIds(name === 'people' ? sec.index : null)) sec.expanded.add(id);
                sec.opened = true;
            }
            paintIfShown(name);
        })
        .catch(err => {
            if (token !== sec.asked) return;
            // The library saying why it will not (an older schema, a root this computer does not place) is an answer, not a fault.
            (err.fromServer ? console.warn : console.error)(`Could not read the navigator's ${name}:`, err);
            sec.status = sec.index ? 'ready' : 'error';
            sec.message = err.fromServer ? err.message : `Could not read the ${cfg.noun} (${err.message}).`;
            paintIfShown(name);
        });
}

/** Read a section if it has not been, or its counts are out of date; draw it either way. */
function ensureLoaded(name) {
    const sec = state.nav.sections[name];
    if (sec.status === 'idle' || sec.status === 'error') return loadSection(name, false);
    if (sec.stale) return loadSection(name, true);
    paintSection(name);
    return Promise.resolve();
}

/**
 * Counts changed -- a write finished, or the view was refreshed: every section read is out of date, and the one
 * on screen is read again after a moment (or `now`), the others when their tab is opened.
 */
export function navigatorCountsChanged({ now = false } = {}) {
    const nav = state.nav;
    let any = false;
    for (const name of NAV_ORDER) {
        if (nav.sections[name].index) {
            nav.sections[name].stale = true;
            any = true;
        }
    }
    if (!any) return;
    window.clearTimeout(nav.timer);
    nav.timer = window.setTimeout(() => {
        nav.timer = null;
        if (nav.shown === 'library') ensureLoaded(nav.tab);
    }, now ? 0 : NAV_COUNTS_MS);
}

// ---- The panes and the tabs -----------------------------------------------------------------

function showPane(name) {
    const nav = state.nav;
    nav.shown = name;
    sidebarPaneLibrary.classList.toggle('hidden', name !== 'library');
    sidebarPaneFolder.classList.toggle('hidden', name !== 'folder');
    for (const [tab, pane] of [[sidebarTabLibrary, 'library'], [sidebarTabFolder, 'folder']]) {
        tab.setAttribute('aria-selected', pane === name ? 'true' : 'false');
        tab.tabIndex = pane === name ? 0 : -1;
        tab.classList.toggle('active', pane === name);
    }
    if (name === 'library') ensureLoaded(nav.tab);
}

/** The person chose a pane: remembered for this kind of view, until the page is left. */
function choosePane(name) {
    state.nav.choice[state.library ? 'library' : 'folder'] = name;
    showPane(name);
}

function selectTab(name, { focus = false, load = true } = {}) {
    state.nav.tab = name;
    for (const each of NAV_ORDER) {
        const tab = document.getElementById(`nav-tab-${each}`);
        tab.setAttribute('aria-selected', each === name ? 'true' : 'false');
        tab.tabIndex = each === name ? 0 : -1;
        tab.classList.toggle('active', each === name);
        panelOf(each).classList.toggle('hidden', each !== name);
    }
    if (focus) document.getElementById(`nav-tab-${name}`).focus();
    if (load && state.nav.shown === 'library') ensureLoaded(name);
}

/**
 * The view that is open (or none) is the source highlighted, in the tab it belongs to, with the branches to it
 * open; and the sidebar shows the pane this kind of view has. Called as a view opens, and as one closes.
 */
export function navigatorFollows() {
    const nav = state.nav;
    const lib = state.library;
    nav.followed = lib && !lib.invalid ? { kind: lib.kind, value: lib.value, recursive: lib.recursive } : null;
    if (lib && !lib.invalid) nav.order = lib.order || DEFAULT_ORDER;
    showOrder();
    const target = sectionOf(nav.followed);
    const touched = new Set(membersOf(nav.followed).map(sectionOf));
    for (const name of NAV_ORDER) nav.sections[name].reveal = touched.has(name);
    // A Ctrl-click in one tab keeps the tab: the union may start in another.
    if (target && !touched.has(nav.tab)) selectTab(target, { load: false });
    showPane(lib ? nav.choice.library : nav.choice.folder);
}

// ---- The sort --------------------------------------------------------------------------------

function showOrder() {
    const select = sidebarPaneLibrary.querySelector('.nav-sort-select');   // built by buildSort, not in index.html
    if (select && select.value !== state.nav.order) select.value = state.nav.order;
}

/** The person chose an order: the view open is read again in it, and the views opened after it are. */
function chooseOrder(order) {
    if (!LIBRARY_ORDERS.includes(order)) return;
    state.nav.order = order;
    const lib = state.library;
    if (lib && !lib.invalid) openLibraryView({ kind: lib.kind, value: lib.value, recursive: lib.recursive, order });
}

// ---- Rows: opening, expanding, the keys ---------------------------------------------------------

function setExpanded(name, id, open) {
    const sec = state.nav.sections[name];
    if (open) sec.expanded.add(id);
    else sec.expanded.delete(id);
    paintSection(name);
}

/**
 * A row picked: alone (a click, Enter, Space), added or taken away (`ctrl`), or the rows drawn from the last one picked to
 * it (`shift`; with `ctrl` they are added). Selecting a row selects every row under it; the view opened is the union of
 * what is selected, in the fewest sources (navigator-model.js compress). A plain click also opens a closed branch.
 */
function chooseRow(name, id, { ctrl = false, shift = false } = {}) {
    const sec = state.nav.sections[name];
    const tree = rowTree(name, sec.index);
    const row = sec.rows.find(each => each.id === id);
    if (!tree || !row || !tree.children.has(id)) return;
    sec.currentId = id;
    const followed = state.nav.followed;
    const current = selectedRows(name, sec.index, followed).rows;
    let next;
    if (shift) {
        const drawnIds = sec.rows.map(each => each.id);
        const firstSelected = drawnIds.find(each => current.has(each));
        const from = drawnIds.indexOf(drawnIds.includes(sec.anchor) ? sec.anchor : (firstSelected || id));
        const to = drawnIds.indexOf(id);
        const range = drawnIds.slice(Math.min(from, to), Math.max(from, to) + 1);
        const picked = withRowsUnder(tree, range);
        next = ctrl ? new Set([...current, ...picked]) : picked;
    } else if (ctrl) {
        sec.anchor = id;
        const under = withRowsUnder(tree, [id]);
        next = new Set(current);
        for (const each of under) {
            if (current.has(id)) next.delete(each);
            else next.add(each);
        }
    } else {
        sec.anchor = id;
        next = withRowsUnder(tree, [id]);
        if (row.expandable && !row.expanded) sec.expanded.add(id);
    }
    // Ctrl keeps what other tabs selected; a plain click or Shift-click starts again.
    const kept = ctrl ? membersOf(followed).filter(member => sectionOf(member) !== name) : [];
    const members = [...kept, ...compress(tree, next)];
    if (!members.length) {
        sec.notice = 'One row at least stays selected: click another row to show it instead.';
        paintSection(name);
        return;
    }
    if (members.length > MAX_MEMBERS) {
        sec.notice = `That selects ${members.length.toLocaleString()} rows; a view shows at most ${MAX_MEMBERS.toLocaleString()} at once. `
            + 'Select fewer, or a row above them.';
        paintSection(name);
        return;
    }
    const spec = specOfMembers(members);
    if (addressTooLong({ ...spec, order: state.nav.order })) {
        // The address holds the selection, and one this long could not be reloaded or bookmarked (#696).
        sec.notice = `Those ${members.length.toLocaleString()} rows are too many to name in the page's address. Select fewer, or a row above them.`;
        paintSection(name);
        return;
    }
    sec.notice = '';
    paintSection(name);
    openLibraryView(spec);
}

/** Ctrl+A: every row drawn, and the rows under them. */
function chooseAllRows(name) {
    const sec = state.nav.sections[name];
    if (!sec.rows.length) return;
    const first = sec.rows[0].id;
    const last = sec.rows.at(-1).id;
    sec.anchor = first;
    chooseRow(name, last, { shift: true });
}

/** The arrow keys are on this row: it is the section's one tab stop. */
function makeCurrent(name, id) {
    const sec = state.nav.sections[name];
    const { tree } = partsOf(name);
    const before = sec.currentId ? rowElement(tree, sec.currentId) : null;
    if (before) before.tabIndex = -1;
    sec.currentId = id;
    const el = rowElement(tree, id);
    if (el) el.tabIndex = 0;
    return el;
}

function rowIdOf(target) {
    const row = target && target.closest ? target.closest('.nav-row') : null;
    return row ? row.dataset.row : null;
}

function wireSection(name) {
    const cfg = NAV_SECTIONS[name];
    const sec = state.nav.sections[name];
    const { filter, tree } = partsOf(name);
    filter.addEventListener('input', () => {
        window.clearTimeout(sec.filterTimer);
        sec.filterTimer = window.setTimeout(() => {
            sec.filterTimer = null;
            sec.filter = filter.value;
            paintSection(name);
        }, NAV_FILTER_MS);
    });
    filter.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' && sec.rows.length) {
            e.preventDefault();
            const el = makeCurrent(name, sec.currentId && sec.rows.some(r => r.id === sec.currentId) ? sec.currentId : sec.rows[0].id);
            if (el) el.focus();
        } else if (e.key === 'Escape' && filter.value) {
            e.preventDefault();
            filter.value = '';
            filter.dispatchEvent(new Event('input'));
        }
    });
    tree.addEventListener('mousedown', (e) => {
        if (e.shiftKey && rowIdOf(e.target)) e.preventDefault();   // a Shift-click selects rows, not the text between them
    });
    tree.addEventListener('click', (e) => {
        const id = rowIdOf(e.target);
        if (!id) return;
        makeCurrent(name, id);
        const twisty = e.target.closest('.nav-twisty');
        const row = sec.rows.find(each => each.id === id);
        if (twisty && row && row.expandable && !e.shiftKey && !e.ctrlKey && !e.metaKey) setExpanded(name, id, !row.expanded);
        else chooseRow(name, id, { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey });
    });
    tree.addEventListener('focusin', (e) => {
        const id = rowIdOf(e.target);
        if (id && id !== sec.currentId) makeCurrent(name, id);
    });
    tree.addEventListener('keydown', (e) => {
        const ctrl = e.ctrlKey || e.metaKey;
        if (ctrl && !e.altKey && (e.key === 'a' || e.key === 'A')) {
            e.preventDefault();
            e.stopPropagation();
            chooseAllRows(name);
            return;
        }
        if (e.key === ' ' && !e.altKey && (ctrl || e.shiftKey)) {
            e.preventDefault();
            e.stopPropagation();
            const at = rowIdOf(e.target) || sec.currentId;
            if (at) chooseRow(name, at, { ctrl, shift: e.shiftKey });
            return;
        }
        if (ctrl || e.altKey) return;
        const id = rowIdOf(e.target) || sec.currentId;
        const action = treeKeyAction(e.key, sec.rows, id, cfg.tree);
        if (!action) return;
        e.preventDefault();
        e.stopPropagation();
        if (action.focus) {
            const el = makeCurrent(name, action.focus);
            if (el) {
                el.focus();
                el.scrollIntoView({ block: 'nearest' });
            }
        } else if (action.open) setExpanded(name, action.open, true);
        else if (action.close) setExpanded(name, action.close, false);
        else if (action.activate) chooseRow(name, action.activate);
    });
}

function buildSort() {
    const select = buildElement('select', {
        className: 'nav-sort-select', id: 'nav-sort',
        attrs: { 'aria-label': 'Sort the photos of the view by', 'data-own-keys': true, title: 'Sort the photos of the view' },
    }, LIBRARY_ORDERS.map(order => buildElement('option', { text: ORDER_LABELS[order], attrs: { value: order } })));
    select.value = state.nav.order;
    select.addEventListener('change', () => chooseOrder(select.value));
    return buildElement('div', { className: 'nav-sort' }, [
        buildElement('label', { className: 'nav-sort-label', text: 'Sort', attrs: { for: 'nav-sort' } }),
        select,
    ]);
}

function buildPanels() {
    sidebarPaneLibrary.appendChild(buildSort());
    const tabs = buildElement('div', {
        className: 'nav-tabs', id: 'nav-tabs',
        attrs: { role: 'tablist', 'aria-label': 'Browse the library by', 'data-own-keys': true },
    });
    for (const name of NAV_ORDER) {
        const tab = buildElement('button', {
            className: 'nav-tab', id: `nav-tab-${name}`, text: NAV_SECTIONS[name].label,
            attrs: { type: 'button', role: 'tab', 'aria-controls': `nav-panel-${name}`, 'aria-selected': 'false', tabindex: '-1' },
            data: { section: name },
        });
        tab.addEventListener('click', () => selectTab(name, { focus: false }));
        tabs.appendChild(tab);
    }
    tabs.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const at = NAV_ORDER.indexOf(state.nav.tab);
        const to = tabKeyTarget(e.key, at, NAV_ORDER.length);
        if (to < 0) return;
        e.preventDefault();
        e.stopPropagation();
        selectTab(NAV_ORDER[to], { focus: true });
    });
    sidebarPaneLibrary.appendChild(tabs);
    for (const name of NAV_ORDER) {
        const cfg = NAV_SECTIONS[name];
        sidebarPaneLibrary.appendChild(buildElement('div', {
            className: 'nav-panel hidden', id: `nav-panel-${name}`,
            attrs: { role: 'tabpanel', 'aria-labelledby': `nav-tab-${name}` }, data: { section: name },
        }, [
            buildElement('input', {
                className: 'nav-filter',
                attrs: { type: 'search', placeholder: `Filter ${cfg.noun}...`, 'aria-label': `Filter ${cfg.noun}`, autocomplete: 'off' },
            }),
            buildElement('div', { className: 'nav-status hidden', attrs: { role: 'status' } }),
            buildElement('div', {
                className: 'nav-tree',
                attrs: { role: cfg.tree ? 'tree' : 'listbox', 'aria-label': cfg.label, 'data-own-keys': true },
            }),
            buildElement('div', { className: 'nav-note hidden' }),
        ]));
        wireSection(name);
    }
    selectTab(state.nav.tab, { load: false });
}

/** Build the navigator's panels and wire the switch between the sidebar's panes. Starts nothing: a section is read when it is shown. */
export function wireNavigator() {
    buildPanels();
    sidebarTabLibrary.addEventListener('click', () => choosePane('library'));
    sidebarTabFolder.addEventListener('click', () => choosePane('folder'));
    sidebarSwitch.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const to = tabKeyTarget(e.key, state.nav.shown === 'library' ? 0 : 1, 2);
        if (to < 0) return;
        e.preventDefault();
        e.stopPropagation();
        choosePane(to === 0 ? 'library' : 'folder');
        (to === 0 ? sidebarTabLibrary : sidebarTabFolder).focus();
    });
    showPane(state.nav.shown);
}
