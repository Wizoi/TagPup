// TagPup's page: Filter mode (docs/plans/advanced-filtering.md, cut 1b) -- the navigator's rows marked any of (`+`), must (`&`)
// or not (`-`), and the strip of chips above the grid that says what the filter is. The marks are no state of their own: they
// are read from the view open (navigator-model.js rowStatesFromSpec), so Back, Forward and a bookmark restore them, and a search
// made elsewhere (words, an Any-of list, Within) marks nothing; a click then starts a new filter.
//
// The toggle (navigator.js draws it) is remembered in this browser, as details-panel.js remembers its panel, and is off until it
// is turned on; off, the navigator is Browse as it always was. The first mark made from a view that is no filter is a new place in
// the history (Back returns to that view); each later mark replaces it. A filter emptied -- its last mark taken off, a chip's x,
// Clear all, or Filter turned off with one open -- is left as the search box's Clear leaves a search, through library-view.js
// leaveSearch, once however fast the clicks come. A filter holds at most MAX_MEMBERS rows in each list of its search, as the server
// takes (library-source.js listOverCap), and an address the page can be reloaded by: a click past either is refused with a
// sentence on the tab's status line (navigator.js paints `notice`). What it holds is `state.nav.filter`.
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import { btnFilterBarClear, filterBar, filterBarChips } from './elements.js';
import { leaveSearch, openLibraryView } from './library-view.js';
import { addressTooLong } from './library-source.js';
import { NEXT_MARK, filterKey, filterOverCap, rowStatesFromSpec, specFromRowStates } from './navigator-model.js';
import { filterChip } from './search-model.js';

/** Where this browser keeps whether Filter mode is on ('on' or 'off'). */
export const FILTER_MODE_KEY = 'tagpup.filterMode';

/**
 * The rows' marks of the view open, when it is a filter (a Map of filterKey -> sign); null for any other view, or none. A filter
 * here is a search: a row's own view (a person, a group's union) is left as it is, never taken for a filter to leave or replace.
 */
function openFilter() {
    const lib = state.library;
    return lib && !lib.invalid && lib.kind === 'search' ? rowStatesFromSpec(lib) : null;
}

/** The marks the navigator and the strip show: the open filter's while Filter mode is on, else none. */
export function shownMarks() {
    return state.nav.filter.on ? openFilter() : null;
}

/**
 * Show the filter these marks are: the view of their search, a new place from a view that is no filter (`replace` false), else
 * in the filter's own place; no mark left leaves the filter. Refused, with the sentence in `notice`, past MAX_MEMBERS rows in one
 * list of the search or an address too long to reload.
 */
function showMarks(marks, replace) {
    const filter = state.nav.filter;
    const spec = specFromRowStates(marks);
    if (!spec) {
        filter.notice = '';
        leaveSearch();
        return;
    }
    const over = filterOverCap(spec);
    if (over) {
        filter.notice = over;
        return;
    }
    if (addressTooLong({ ...spec, order: state.nav.order })) {
        filter.notice = 'That filter is too long to name in the page’s address: take a row off before marking another.';
        return;
    }
    filter.notice = '';
    openLibraryView(spec, { history: replace ? 'replace' : 'push' });
}

/** A row clicked in Filter mode: its source's mark goes to the next (`ctrl`: any of). Nothing while the filter is being left. */
export function markRow(member, { ctrl = false } = {}) {
    if (state.nav.filter.leaving) return;
    const open = openFilter();
    const marks = new Map(open || []);
    const key = filterKey(member);
    const next = ctrl ? '+' : NEXT_MARK[marks.get(key) || ''];
    if (next) marks.set(key, next);
    else marks.delete(key);
    showMarks(marks, Boolean(open));
}

/** A chip clicked: its row's mark taken off. */
function takeOff(key) {
    const open = openFilter();
    if (state.nav.filter.leaving || !open || !open.has(key)) return;
    open.delete(key);
    showMarks(open, true);
}

/** Filter mode turned on or off, and remembered; turned off with a filter open, the filter is left. */
export function setFilterMode(on) {
    const filter = state.nav.filter;
    filter.on = on;
    filter.notice = '';
    try {
        window.localStorage.setItem(FILTER_MODE_KEY, on ? 'on' : 'off');
    } catch (err) { /* this browser keeps nothing: the choice lasts until the page is closed */ }
    if (!on && openFilter()) leaveSearch();
    filterFollows();
}

/** A chip: the mark and the row's name, struck through when the library no longer has the tag or the person. */
function chipOf(key, sign) {
    const chip = filterChip(key, sign, state.nav.sections.keywords.index, state.nav.sections.people.index);
    return buildElement('button', {
        className: `library-search-chip filter-chip filter-chip-${chip.part}${chip.known === false ? ' library-search-chip-unknown' : ''}`,
        attrs: { type: 'button', role: 'listitem', 'aria-label': `Take ${chip.text} (${chip.said}) off the filter` },
        title: `${chip.said}: ${chip.title}${chip.why}. Click to take it off the filter`, data: { key },
    }, [
        buildElement('span', { className: 'filter-chip-mark', text: sign, attrs: { 'aria-hidden': 'true' } }),
        buildElement('span', { className: 'library-search-chip-text', text: chip.text }),
        buildElement('span', { className: 'library-search-chip-remove', text: '×', attrs: { 'aria-hidden': 'true' } }),
    ]);
}

/**
 * The strip shows the filter open, a chip for each row marked, in the filter's order: called as a view opens, changes or closes
 * (library-view.js, through `upper`), as the toggle changes, and as the keywords or the people are read (their chips' marks).
 */
export function filterFollows() {
    const marks = shownMarks();
    filterBar.classList.toggle('hidden', !marks);
    replaceContent(filterBarChips, ...(marks ? [...marks].map(([key, sign]) => chipOf(key, sign)) : []));
}

export function wireFilterBar() {
    try {
        state.nav.filter.on = window.localStorage.getItem(FILTER_MODE_KEY) === 'on';
    } catch (err) { /* off, then */ }
    filterBarChips.addEventListener('click', (e) => {
        const chip = e.target.closest('.filter-chip');
        if (chip) takeOff(chip.dataset.key);
    });
    btnFilterBarClear.addEventListener('click', () => {
        if (!state.nav.filter.leaving && openFilter()) leaveSearch();
    });
    filterFollows();
}
