/**
 * The phone's filter (docs/plans/phone-redesign.md, cut 3): the Filter sheet over the Grid, its tabs People, Keywords, Folders
 * and Dates in Browse's order, each row off or marked `+` any of, `&` must or `-` not; the chips above the grid that say what
 * the filter is; and what the grid says when the filter finds no photo.
 *
 * What a filter is, is the desktop's (navigator-model.js): the marks are one search (`specFromRowStates`) and are read back
 * from the view the Grid shows (`rowStatesFromSpec`), so the Grid's place in history holds them, and Back, Forward and a reload
 * restore them; a chip's words are search-model.js `filterChip`, a tag or person the library no longer has struck through.
 * `state.filter` is the marks of the view shown, the one model a tap changes: a tap makes the next marks from it, their view
 * replaces the Grid's place (mobile-screens.js replacePlace) and the Grid reads it, a reply for the marks before dropped
 * (mobile-grid.js). Rapid taps on one row cycle it, each from the last. The server is the desktop's, asked the same
 * `kind=search`; a filter past its cap of rows (MAX_MEMBERS) is refused with the desktop's sentence on the tab's status line.
 *
 * The sheet is a step of history over the Grid, as the Edit sheet is over a photo (mobile-screens.js openSheetStep): Back,
 * the scrim or Show closes it, the filter made under it staying on the Grid's step (mobile-screens.js carries it down), so a
 * second Back leaves the Grid. The Filter tab opens it over the Grid's view when that is a filter (a search of marks, or a view
 * of rows Browse opened, a person's or a group's, those rows marked any of: the same view, nothing read again, made a search
 * by the first mark), else over the whole library. Its rows are Browse's, the section read once
 * (mobile-browse.js), their counts the whole library's ("of all photos": counts narrowed by the other marks are
 * advanced-filtering.md's cut 2, not built). Its Show button says how many photos the marks find: the Grid's own count of the
 * view read under it. The Grid's order and Photos/Clips are chosen in its head (wired by mobile-grid.js). A filter that finds
 * photos, none of the kind chosen, is not blamed on its marks: the grid says what the desktop says of such a view
 * (library-source.js emptySentence) and offers Both.
 */
import { buildElement, replaceContent } from './common/dom.js';
import { DEFAULT_MEDIA, mediaLabel, viewCountSaid } from './library-source.js';
import {
    NEXT_MARK, filterKey, filterMember, filterOverCap, markOf, rowStatesFromSpec, sectionOf, sectionRows, specFromRowStates
} from './navigator-model.js';
import { filterChip } from './search-model.js';
import { state } from './mobile-state.js';
import { closeSheetStep, openScreen, openSheetStep, replacePlace, samePlace, sheetShown, whenSheet } from './mobile-screens.js';
import { needSection, sectionState, SECTIONS, viewOf } from './mobile-browse.js';

const byId = (id) => document.getElementById(id);

/** The marks a view is, a Map of filterKey -> sign: empty for a view that is no filter. */
function marksOf(view) {
    return (view && rowStatesFromSpec(view)) || new Map();
}

/** The view these marks are, in the order and Photos/Clips of `view`: the whole library for none. */
function viewOfMarks(marks, view) {
    return viewOf({ ...(specFromRowStates(marks) || { kind: 'all' }), order: view && view.order, media: view && view.media });
}

/** The view the sheet opens over: the Grid's when its marks are a filter (navigator-model.js), else the whole library. */
function filterOf(view) {
    return marksOf(view).size ? view : viewOfMarks(new Map(), view);
}

/** The Filter tab and the chip that opens the sheet: the Grid on the filter (a step of its own when it shows another view), the sheet over it. */
export function openFilter() {
    if (state.screen === 'grid' && sheetShown()) return;
    const view = filterOf(state.screen === 'grid' ? state.place : state.view);
    if (state.screen !== 'grid' || !samePlace(state.place, view)) openScreen('grid', view);
    openSheetStep();
}

/** These marks shown: their view the Grid's place, read; refused past the server's cap, the sentence on the sheet's status line. */
function showMarks(marks) {
    const spec = specFromRowStates(marks);
    const sheet = state.filterSheet;
    sheet.notice = spec ? filterOverCap(spec) : '';
    const view = viewOfMarks(marks, state.place);
    if (sheet.notice || samePlace(view, state.place)) drawSheet();
    else replacePlace(view);
}

/** A row tapped: its mark the next (off, +, &, -, off), made from the marks of the view shown; nothing off the Grid. */
function markRow(member) {
    if (state.screen !== 'grid') return;
    const marks = new Map(state.filter);
    const key = filterKey(member);
    const next = NEXT_MARK[marks.get(key) || ''];
    if (next) marks.set(key, next);
    else marks.delete(key);
    showMarks(marks);
}

/** A chip's x, or Remove: its row's mark taken off. */
function takeOff(key) {
    if (state.screen !== 'grid' || !state.filter.has(key)) return;
    const marks = new Map(state.filter);
    marks.delete(key);
    showMarks(marks);
}

/** Clear and Clear all: every mark off, the whole library in the view's order. */
function clearAll() {
    if (state.screen === 'grid' && state.filter.size) showMarks(new Map());
}

/** The chips of these marks (the marks shown), in the filter's order, their words from the sections read so far. */
function chipsOf(marks = state.filter) {
    const keywords = sectionState('keywords').index;
    const people = sectionState('people').index;
    return [...marks].map(([key, sign]) => filterChip(key, sign, keywords, people));
}

/** A chip of the row above the grid: its mark, its name (struck when the library has it no more) and its x. */
function chipButton(chip) {
    return buildElement('button', {
        className: `grid-chip grid-chip-${chip.part}${chip.known === false ? ' grid-chip-unknown' : ''}`,
        attrs: { type: 'button', 'aria-label': `Take ${chip.text} (${chip.said}) off the filter` },
        title: `${chip.said}: ${chip.title}${chip.why}`, data: { key: chip.key },
    }, [
        buildElement('span', { className: 'grid-chip-mark', text: chip.sign, attrs: { 'aria-hidden': 'true' } }),
        buildElement('span', { className: 'grid-chip-text', text: chip.text }),
        buildElement('span', { className: 'grid-chip-x', text: '×', attrs: { 'aria-hidden': 'true' } }),
    ]);
}

/** The row of chips above the grid: Filter (how many marks; it opens the sheet), then a chip for each mark. */
function drawChips() {
    const chips = chipsOf();
    const open = buildElement('button', {
        className: 'grid-chip grid-chip-filter',
        attrs: { type: 'button', 'aria-label': chips.length ? `Filter: ${chips.length} marked` : 'Filter' },
    }, [
        buildElement('span', { text: 'Filter' }),
        chips.length ? buildElement('span', { className: 'grid-chip-count', text: String(chips.length) }) : null,
    ]);
    replaceContent(byId('grid-chips'), open, ...chips.map(chipButton));
}

/**
 * Does this read of the Grid's view find none under marks made in the sheet (a search of them): the grid's words for none are
 * then the filter's, its count "0 photos". A row's own view (a person's photos) that finds none says why in the view's sentence.
 */
export function marksFindNone(lib) {
    return Boolean(lib) && lib.status === 'empty' && lib.kind === 'search' && marksOf(lib).size > 0;
}

/**
 * What the read of the view says to the filter (mobile-grid.js, each time the Grid's count line is drawn): the sheet's Show
 * with the count, "No photos" and not pressable when there is none; and under marks that find none, the grid's words:
 * when they find photos but none of the kind chosen (`of`, what they find of every kind), that, and Both; else its chips
 * named, Remove the last and Clear all.
 */
export function drawFilterStatus() {
    const lib = state.photos;
    const read = Boolean(lib) && (lib.status === 'ready' || lib.status === 'empty');
    const none = marksFindNone(lib);
    byId('grid-none').classList.toggle('hidden', !none);
    if (none) {
        const kind = lib.media !== DEFAULT_MEDIA && lib.of > 0;
        const chips = chipsOf(marksOf(lib));     // the marks of the view read, whichever was drawn first
        byId('grid-none-text').textContent = kind ? lib.message : 'No photo has all of these.';
        byId('grid-none-chips').textContent = kind ? '' : chips.map((chip) => `${chip.sign} ${chip.text}`).join(', ');
        byId('grid-none-remove').textContent = `Remove ${chips.at(-1).text}`;
        byId('grid-none-remove').classList.toggle('hidden', kind);
        byId('grid-none-clear').classList.toggle('hidden', kind);
        byId('grid-none-media').textContent = mediaLabel(DEFAULT_MEDIA);
        byId('grid-none-media').classList.toggle('hidden', !kind);
    }
    const show = byId('filter-show');
    show.textContent = !read ? 'Show photos' : lib.status === 'empty' ? 'No photos' : `Show ${viewCountSaid(lib)}`;
    show.disabled = read && lib.status === 'empty';
}

// ---- The sheet ---------------------------------------------------------------------------------------

/** The rows open under a tab, kept while the page is. */
function expandedOf(name) {
    const sheet = state.filterSheet;
    if (!sheet.expanded[name]) sheet.expanded[name] = new Set();
    return sheet.expanded[name];
}

/** A row of a tab: its name, its count and its mark, a tap the next mark; a row with rows under it opens beside. */
function rowOf(row) {
    const sign = row.spec ? state.filter.get(filterKey(row.spec)) || '' : '';
    const mark = sign ? markOf(sign) : null;
    const said = mark ? mark.said : row.spec ? 'off' : '';
    const line = buildElement('div', { className: 'filter-row', data: { sign } }, [
        buildElement('button', {
            className: 'filter-row-mark',
            attrs: { type: 'button', 'aria-label': `${row.aria || row.label}${said ? `: ${said}` : ''}` }, data: { id: row.id },
        }, [
            buildElement('span', { className: 'filter-row-label', text: row.label }),
            buildElement('span', { className: 'filter-row-count', text: Number(row.count || 0).toLocaleString() }),
            buildElement('span', { className: `filter-row-sign${mark ? ` filter-row-${mark.part}` : ''}`, text: sign, attrs: { 'aria-hidden': 'true' } }),
        ]),
        row.expandable ? buildElement('button', {
            className: 'filter-row-open', text: row.expanded ? '▾' : '▸', data: { open: row.id },
            attrs: { type: 'button', 'aria-label': `${row.expanded ? 'Close' : 'Open'} ${row.label}`, 'aria-expanded': String(Boolean(row.expanded)) },
        }) : null,
    ]);
    line.style.setProperty('--level', String(Math.max(0, row.level - 1)));
    return line;
}

/** The sheet as the step and the marks say: its tab, the tab's rows and status line; nothing while it is shut. */
function drawSheet() {
    if (!sheetShown() || state.screen !== 'grid') return;
    const sheet = state.filterSheet;
    const name = sheet.tab;
    for (const tab of byId('filter-tabs').querySelectorAll('[data-filter-tab]')) {
        tab.setAttribute('aria-selected', String(tab.dataset.filterTab === name));
    }
    const sec = sectionState(name);
    const failed = sec.status === 'error' && !sec.index;
    const { rows, hidden } = sec.index ? sectionRows(name, sec.index, expandedOf(name), '') : { rows: [], hidden: 0 };
    sheet.rows = rows;
    let said = '';
    if (failed) said = `Could not load ${SECTIONS[name].label.toLowerCase()}. ${sec.message}`.trim();
    else if (!sec.index) said = 'Loading...';
    else if (sheet.notice) said = sheet.notice;
    else if (!rows.length) said = `This library has no ${SECTIONS[name].label.toLowerCase()} to filter by yet.`;
    else if (hidden) said = `${hidden.toLocaleString()} more are not shown: close a row to see them.`;
    byId('filter-said').textContent = said;
    byId('filter-said').classList.toggle('problem-text', failed || Boolean(sheet.notice));
    byId('filter-retry').classList.toggle('hidden', !failed);
    replaceContent(byId('filter-rows'), ...rows.map(rowOf));
}

/** The tab shown read, if it is not (or it failed: Try again), and drawn now and when it is. */
function askTab() {
    const name = state.filterSheet.tab;
    needSection(name).then(() => {
        if (state.filterSheet.tab === name) drawSheet();
    });
    drawSheet();
}

/** The sections the chips name read for their words (a person's name, a tag still in the library), the chips drawn again then. */
function askChipSections() {
    const wanted = new Set([...state.filter.keys()].map((key) => sectionOf(filterMember(key))));
    for (const name of ['keywords', 'people']) {
        if (!wanted.has(name)) continue;
        needSection(name).then(() => {
            if (state.screen === 'grid') drawFilter();
        });
    }
}

/** Everything the marks show: the chips, the sheet, Show and the words for none. */
function drawFilter() {
    drawChips();
    drawSheet();
    drawFilterStatus();
}

/** The Grid shown at a place (before it is read: mobile.js wires this first): its marks, and all they show. */
export function filterFollows() {
    state.filter = marksOf(state.place);
    askChipSections();
    drawFilter();
}

/** The step shown has the sheet over the Grid or not (open, Back, Forward, a reload): the sheet and its scrim shown as it says. */
function sheetStep(open) {
    const shown = open && state.screen === 'grid';
    state.filterSheet.closing = false;
    byId('filter-sheet').classList.toggle('hidden', !shown);
    byId('filter-scrim').classList.toggle('hidden', !shown);
    if (!shown) return;
    state.filterSheet.notice = '';
    askTab();
}

/** Show, or a tap on the scrim: the sheet's step goes (Back), the filter staying on the Grid's; once however fast the taps. */
function closeSheet() {
    if (!sheetShown() || state.screen !== 'grid' || state.filterSheet.closing) return;
    state.filterSheet.closing = true;
    closeSheetStep();
}

/** The sheet's tabs, rows, Clear, Show, scrim and Try again; the chips above the grid, and Remove and Clear all under none. */
export function wireFilter() {
    whenSheet(sheetStep);
    byId('filter-tabs').addEventListener('click', (event) => {
        const tab = event.target.closest('[data-filter-tab]');
        if (!tab || !SECTIONS[tab.dataset.filterTab]) return;
        state.filterSheet.tab = tab.dataset.filterTab;
        state.filterSheet.notice = '';
        askTab();
    });
    byId('filter-rows').addEventListener('click', (event) => {
        const button = event.target.closest('[data-open], .filter-row-mark');
        // A row of the rows drawn last (one tapped as they were drawn again is found again by its id).
        const id = button && (button.dataset.open ?? button.dataset.id);
        const row = id === undefined ? null : state.filterSheet.rows.find((each) => String(each.id) === id);
        if (!row || !sheetShown()) return;
        if (button.dataset.open !== undefined || !row.spec) {
            const open = expandedOf(state.filterSheet.tab);
            if (open.has(row.id)) open.delete(row.id);
            else open.add(row.id);
            drawSheet();
            return;
        }
        markRow(row.spec);
    });
    byId('filter-clear').addEventListener('click', clearAll);
    byId('filter-show').addEventListener('click', closeSheet);
    byId('filter-scrim').addEventListener('click', closeSheet);
    byId('filter-retry').addEventListener('click', askTab);
    byId('grid-chips').addEventListener('click', (event) => {
        const chip = event.target.closest('.grid-chip');
        if (!chip || sheetShown()) return;
        if (chip.classList.contains('grid-chip-filter')) openFilter();
        else takeOff(chip.dataset.key);
    });
    byId('grid-none-remove').addEventListener('click', () => takeOff([...state.filter.keys()].at(-1)));
    byId('grid-none-clear').addEventListener('click', clearAll);
    byId('grid-none-media').addEventListener('click', () => {
        if (state.screen === 'grid' && state.place) replacePlace(viewOf({ ...state.place, media: DEFAULT_MEDIA }));
    });
}
