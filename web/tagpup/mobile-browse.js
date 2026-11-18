/**
 * The phone's Browse screen (docs/plans/phone-viewer.md, Screens 2): a search box and four big rows, Folders, Keywords,
 * People and Dates, each opening its section one level at a time. A level is a step in history (mobile-screens.js
 * `openScreen` with the place [section, row ids...]), so the phone's Back goes up one level; the bar above the rows
 * names the path and goes up too. Every level has "Show these photos", which, like Search, opens the Grid on a view.
 *
 * What a section holds is the desktop navigator's model (navigator-model.js): its index, its rows with their counts, and
 * the source a row's photos are (`compress` of the row and every row under it: a folder with its subfolders). Nothing of
 * it is worked out here again. A section is read from GET /api/library/navigator?section= when its top is shown, and the
 * index is kept while its levels are walked; a reply to an older ask is dropped, so a slow reply cannot draw over a
 * newer one, and a read that fails says so with Try again rather than showing an empty list.
 */
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { DEFAULT_MEDIA, DEFAULT_ORDER, MAX_WORDS, searchAsksSomething, searchValue, searchWords, viewSpecFromSearch }
    from './library-source.js';
import { compress, indexSection, rowTree, sectionRows, specOfMembers, withRowsUnder } from './navigator-model.js';
import { state } from './mobile-state.js';
import { openScreen, samePlace, upAScreen } from './mobile-screens.js';

/** The four sections: what each is called, and what an empty one says. */
const SECTIONS = {
    folders: { label: 'Folders', empty: 'This library holds no photos in any folder yet.' },
    keywords: { label: 'Keywords', empty: 'This library has no keywords yet.' },
    people: { label: 'People', empty: 'No photo in this library names a person yet.' },
    dates: { label: 'Dates', empty: 'No photo in this library has a date yet.' },
};

const byId = (id) => document.getElementById(id);

/** A view as the page holds it: the names of library-source.js, in one order (mobile-screens.js compares places). */
export function viewOf(spec) {
    return {
        kind: spec.kind, value: spec.value ?? null, recursive: Boolean(spec.recursive),
        order: spec.order || DEFAULT_ORDER, media: spec.media || DEFAULT_MEDIA,
    };
}

/** Open the Grid on these photos. */
function showPhotos(spec) {
    openScreen('grid', viewOf(spec));
}

/**
 * The view the page's address names (`?view=`), to open as the page starts; null for none. An address naming no view
 * TagPup knows says why above the rows.
 */
export function linkedView() {
    const spec = viewSpecFromSearch(window.location.search);
    if (!spec) return null;
    if (spec.error) {
        byId('browse-link-problem').textContent = spec.error;
        byId('browse-link-problem').classList.remove('hidden');
        return null;
    }
    return viewOf(spec);
}

function sectionState(name) {
    if (!state.sections[name]) state.sections[name] = { asked: 0, index: null, status: 'loading', message: '' };
    return state.sections[name];
}

/** Ask for a section; a reply to an older ask is dropped. A read that fails keeps the index already shown, if any. */
function readSection(name) {
    const sec = sectionState(name);
    const asked = ++sec.asked;
    if (!sec.index) sec.status = 'loading';
    api.fetch(`/api/library/navigator?section=${name}`)
        .then((res) => res.json().catch(() => ({})).then((body) => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (asked !== sec.asked) return;
            if (!ok) throw new Error((body && body.error) || '');
            sec.index = indexSection(name, body || {});
            sec.status = 'ready';
        })
        .catch((err) => {
            if (asked !== sec.asked) return;
            sec.status = sec.index ? 'ready' : 'error';
            sec.message = err.message;
        })
        .then(() => {
            if (asked === sec.asked && state.screen === 'browse' && state.place && state.place[0] === name) drawLevel(state.place);
        });
}

/** What a row's photos are, or a level's: the rows `ids` with everything under them, as the fewest sources (a folder with its
 *  subfolders); null for none. */
function photosOf(tree, ids) {
    return specOfMembers(compress(tree, withRowsUnder(tree, ids)));
}

/** A level's rows, as the model draws them with the path to it open: the children of its last row, or the section's tops. */
function levelRows(name, index, ids) {
    const tree = rowTree(name, index);
    const last = ids.at(-1);
    const wanted = new Set(last === undefined ? tree.tops : tree.children.get(last) || []);
    const { rows } = sectionRows(name, index, new Set(ids), '', Infinity);
    const labels = new Map(rows.map((row) => [row.id, row.label]));
    return {
        rows: rows.filter((row) => wanted.has(row.id) && row.level === ids.length + 1),
        path: ids.map((id) => labels.get(id) || ''),
        known: last === undefined || tree.children.has(last),
        // What "Show these photos" opens: the level's row with everything under it, or every row of the section.
        photos: photosOf(tree, last === undefined ? tree.tops : [last]),
    };
}

/** A row of a level: a tap opens the level under it, or, for a row with nothing under it, its photos. */
function rowButton(row, place, tree) {
    const button = buildElement('button', {
        className: row.expandable ? 'browse-item opens' : 'browse-item',
        attrs: { type: 'button', 'aria-label': row.aria },
    }, [
        buildElement('span', { className: 'browse-item-label', text: row.label }),
        buildElement('span', { className: 'browse-item-count', text: row.count.toLocaleString() }),
    ]);
    button.addEventListener('click', () => {
        // A tap on a level no longer shown (a second tap while the first opened the next) does nothing.
        if (state.screen !== 'browse' || !samePlace(state.place, place)) return;
        if (row.expandable) openScreen('browse', [...place, row.id]);
        else showPhotos(photosOf(tree, [row.id]));
    });
    return button;
}

/** Draw a level of a section: its path, Show these photos, its rows; or that it is loading, empty or could not be read. */
function drawLevel(place) {
    const [name, ...ids] = place;
    const sec = sectionState(name);
    const cfg = SECTIONS[name];
    const note = byId('browse-note');
    const problem = byId('browse-problem');
    problem.classList.add('hidden');
    note.classList.add('hidden');
    byId('browse-show').classList.add('hidden');
    replaceContent(byId('browse-rows'));
    let path = [];
    if (sec.status === 'error') {
        byId('browse-problem-text').textContent = `Could not load ${cfg.label.toLowerCase()}. ${sec.message}`.trim();
        problem.classList.remove('hidden');
    } else if (!sec.index) {
        note.textContent = 'Loading...';
        note.classList.remove('hidden');
    } else {
        const level = levelRows(name, sec.index, ids);
        path = level.path;
        if (!level.known) note.textContent = 'This is no longer in the library.';
        else if (!level.rows.length) note.textContent = ids.length ? 'Nothing here.' : cfg.empty;
        note.classList.toggle('hidden', level.known && level.rows.length > 0);
        if (level.known && level.photos) {
            byId('browse-show').classList.remove('hidden');
            byId('browse-show').onclick = () => showPhotos(level.photos);
        }
        replaceContent(byId('browse-rows'), ...(level.known ? level.rows : []).map((row) => rowButton(row, place, rowTree(name, sec.index))));
    }
    byId('browse-path').textContent = [cfg.label, ...path].join(' › ');
}

/** Browse as its place says: the top (search and the four sections), or a level of a section, read when its top is shown. */
export function showBrowse() {
    const place = Array.isArray(state.place) && SECTIONS[state.place[0]] ? state.place : null;
    byId('browse-top').classList.toggle('hidden', place !== null);
    byId('browse-level').classList.toggle('hidden', place === null);
    if (!place) return;
    if (place.length === 1 || !sectionState(place[0]).index) readSection(place[0]);
    drawLevel(place);
}

/** The four sections, Search, the path's Back and Try again. */
export function wireBrowse() {
    for (const button of document.querySelectorAll('[data-section]')) {
        button.addEventListener('click', () => {
            if (state.screen === 'browse' && state.place === null) openScreen('browse', [button.dataset.section]);
        });
    }
    const words = byId('browse-words');
    words.maxLength = MAX_WORDS;
    byId('browse-search').addEventListener('submit', (event) => {
        event.preventDefault();
        // Words alone: an empty box, or one of blanks, asks nothing and opens nothing.
        const value = searchValue({ words: searchWords(words.value) });
        if (searchAsksSomething(value)) showPhotos({ kind: 'search', value });
    });
    byId('browse-up').addEventListener('click', upAScreen);
    byId('browse-retry').addEventListener('click', () => {
        if (!Array.isArray(state.place)) return;
        readSection(state.place[0]);
        drawLevel(state.place);
    });
}
