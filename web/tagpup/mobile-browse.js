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
 *
 * Each row shows a cover, the first photo of the row's source (GET /api/library/covers; docs/plans/phone-redesign.md, cut
 * 2), in a box of fixed size whose placeholder is there first, so nothing moves when it lands. Covers are asked only for the
 * rows within about two screens (one IntersectionObserver), at most 24 an ask and two asks out at once, and only while
 * Browse is shown with no sheet over it; each answer is kept per section (`state.sections[name].covers`), a row scrolled
 * away included, so a level walked again asks nothing. A cover that fails or is slow leaves the placeholder; the row is a
 * button all along. The top of Browse shows the people with the most photos in a strip, their covers asked the same way,
 * See all opening People.
 */
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { DEFAULT_MEDIA, DEFAULT_ORDER, MAX_WORDS, searchAsksSomething, searchValue, searchWords, viewSpecFromSearch }
    from './library-source.js';
import { compress, indexSection, peopleGroupIds, rowTree, sectionRows, specOfMembers, withRowsUnder } from './navigator-model.js';
import { state } from './mobile-state.js';
import { openScreen, samePlace, sheetShown, upAScreen } from './mobile-screens.js';

/** The four sections: what each is called, and what an empty one says. */
const SECTIONS = {
    folders: { label: 'Folders', empty: 'This library holds no photos in any folder yet.' },
    keywords: { label: 'Keywords', empty: 'This library has no keywords yet.' },
    people: { label: 'People', empty: 'No photo in this library names a person yet.' },
    dates: { label: 'Dates', empty: 'No photo in this library has a date yet.' },
};

/** The most rows one ask for covers names (tagpup.services.library_view MAX_COVERS). */
const COVERS_AT_ONCE = 24;
/** Asks for covers out at once: a slow one does not hold the rows that came near after it. */
const COVERS_OUT = 2;
/** How far past the screen a row's cover is asked for: about two screens each way (sideways, the People strip's). */
const COVERS_AHEAD = '200% 200% 200% 200%';
/** The people the strip at the top of Browse shows, those with the most photos; See all opens every one. */
const STRIP_PEOPLE = 24;

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

/** A section as the page holds it (state.sections): made empty the first time it is named. */
export function sectionState(name) {
    if (!state.sections[name]) {
        state.sections[name] = { asked: 0, index: null, status: 'loading', message: '', covers: new Map(), coversOut: new Set() };
    }
    return state.sections[name];
}

/** Ask for a section; a reply to an older ask is dropped. A read that fails keeps the index already shown, if any. */
function readSection(name) {
    const sec = sectionState(name);
    const asked = ++sec.asked;
    if (!sec.index) sec.status = 'loading';
    sec.reading = api.fetch(`/api/library/navigator?section=${name}`)
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
            if (asked !== sec.asked || state.screen !== 'browse') return;
            if (state.place && state.place[0] === name) drawLevel(state.place);
            else if (state.place === null && name === 'people') drawStrip();
        });
}

/**
 * A section read unless it is, or is being read (the People strip, the Filter sheet's tabs and chips); one that failed is
 * asked again. Its read, settled when the reply is in (a read that fails too).
 */
export function needSection(name) {
    const sec = sectionState(name);
    if (!sec.index && (sec.asked === 0 || sec.status === 'error')) readSection(name);
    return sec.reading || Promise.resolve();
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

// ---- Covers ----------------------------------------------------------------------------------------

/** A row's cover: a box of fixed size, its placeholder until the first photo of the row's source is drawn in it. */
function coverBox(name, id, tree, className = 'browse-cover') {
    const box = buildElement('span', { className, attrs: { 'aria-hidden': 'true' }, data: { coverSection: name, coverRow: id } });
    const sec = sectionState(name);
    if (sec.covers.has(id)) drawCover(box, sec.covers.get(id));
    else state.covers.wanted.set(box, { name, id, tree });
    return box;
}

/** A cover's thumbnail into its box, once; a row with no photo keeps its placeholder. */
function drawCover(box, thumb) {
    if (!thumb || box.firstChild) return;
    const img = buildElement('img', {
        className: 'browse-cover-img', attrs: { alt: '', loading: 'lazy', decoding: 'async', src: api.image(thumb) },
    });
    // A thumbnail that fails shows the placeholder, not a broken picture; api.js asks it again after an update.
    img.addEventListener('error', () => img.classList.add('failed'));
    img.addEventListener('load', () => img.classList.remove('failed'));
    box.append(img);
}

/** The boxes drawn of row `id` of section `name`. */
function boxesOf(name, id) {
    return [...document.querySelectorAll('.browse-cover')].filter((box) => box.dataset.coverSection === name && box.dataset.coverRow === id);
}

/** Watch these boxes, the ones drawn now, coming near the screen; the boxes drawn before them went with their level. */
function watchCovers(boxes) {
    const covers = state.covers;
    covers.observer.disconnect();
    covers.queue = [];
    for (const box of boxes) if (covers.wanted.has(box)) covers.observer.observe(box);
}

/** The observer's word: a row near the screen is queued for its cover; one gone before it was asked is taken off the queue. */
function nearing(entries) {
    const { queue, wanted } = state.covers;
    for (const entry of entries) {
        const want = wanted.get(entry.target);
        if (!want) continue;
        const at = queue.indexOf(want);
        if (entry.isIntersecting && at < 0) queue.push(want);
        else if (!entry.isIntersecting && at >= 0) queue.splice(at, 1);
    }
    askCovers();
}

/** Ask for the covers of the rows queued, a section's at a time; nothing while Browse is not shown or a sheet is over it. */
function askCovers() {
    const covers = state.covers;
    while (covers.out < COVERS_OUT && covers.queue.length && state.screen === 'browse' && !sheetShown()) {
        const { name } = covers.queue[0];
        const sec = sectionState(name);
        const batch = [];
        covers.queue = covers.queue.filter((want) => {
            if (want.name !== name || batch.length >= COVERS_AT_ONCE) return true;
            if (!sec.covers.has(want.id) && !sec.coversOut.has(want.id) && !batch.some((each) => each.id === want.id)) batch.push(want);
            return false;
        });
        if (batch.length) askBatch(name, sec, batch);
    }
}

/** One ask for covers: each answer kept and drawn into the row's boxes shown; one that fails leaves the placeholders. */
function askBatch(name, sec, batch) {
    // Each row's source as a tap on it opens it, the rows under it included (photosOf), worked out only as it is asked.
    const rows = batch.map(({ id, tree }) => ({ id, ...photosOf(tree, [id]) }));
    for (const { id } of batch) sec.coversOut.add(id);
    state.covers.out += 1;
    api.fetch(`/api/library/covers?section=${name}&rows=${encodeURIComponent(JSON.stringify(rows))}`)
        .then((res) => api.answered(res, 'The covers could not be read.'))
        .then((body) => {
            for (const cover of body.covers) {
                sec.covers.set(cover.id, cover.thumb);
                for (const box of boxesOf(name, cover.id)) drawCover(box, cover.thumb);
            }
        })
        // The placeholders stay and the rows are tappable as they were; a row that comes near the screen again asks again.
        .catch(() => {})
        .finally(() => {
            for (const { id } of batch) sec.coversOut.delete(id);
            state.covers.out -= 1;
            askCovers();
        });
}

// ---- The People strip ------------------------------------------------------------------------------

/** A person of the strip: their cover, name and count; a tap opens their photos. */
function personTile(row, tree) {
    const button = buildElement('button', { className: 'browse-person', attrs: { type: 'button', 'aria-label': row.aria } }, [
        coverBox('people', row.id, tree, 'browse-cover browse-person-cover'),
        buildElement('span', { className: 'browse-person-name', text: row.label }),
        buildElement('span', { className: 'browse-person-count', text: row.count.toLocaleString() }),
    ]);
    button.addEventListener('click', () => {
        if (state.screen === 'browse' && state.place === null) showPhotos(photosOf(tree, [row.id]));
    });
    return button;
}

/** The strip: the people with the most photos (the model's rows of them, every branch open), or nothing while none is read. */
function drawStrip() {
    const strip = byId('browse-top').querySelector('.browse-people');
    const { index } = sectionState('people');
    const people = index
        ? sectionRows('people', index, new Set(peopleGroupIds(index)), '', Infinity).rows
            .filter((row) => row.spec && row.spec.kind === 'person')
            .sort((a, b) => b.count - a.count)
            .slice(0, STRIP_PEOPLE)
        : [];
    strip.classList.toggle('hidden', people.length === 0);
    const tiles = people.map((row) => personTile(row, rowTree('people', index)));
    replaceContent(strip.querySelector('.browse-people-row'), ...tiles);
    watchCovers(tiles.map((tile) => tile.querySelector('.browse-cover')));
}

/** The top of Browse: the strip, the people read the first time it is shown (or again after a read that failed). */
function showTop() {
    needSection('people');
    drawStrip();
}

// ---- A level ---------------------------------------------------------------------------------------

/** A row of a level: a tap opens the level under it, or, for a row with nothing under it, its photos. */
function rowButton(row, place, tree) {
    const button = buildElement('button', {
        className: row.expandable ? 'browse-item opens' : 'browse-item',
        attrs: { type: 'button', 'aria-label': row.aria },
    }, [
        coverBox(place[0], row.id, tree),
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
    watchCovers(byId('browse-rows').querySelectorAll('.browse-cover'));
}

/** Browse as its place says: the top (search and the four sections), or a level of a section, read when its top is shown. */
export function showBrowse() {
    const place = Array.isArray(state.place) && SECTIONS[state.place[0]] ? state.place : null;
    byId('browse-top').classList.toggle('hidden', place !== null);
    byId('browse-level').classList.toggle('hidden', place === null);
    if (!place) {
        showTop();
        return;
    }
    if (place.length === 1 || !sectionState(place[0]).index) readSection(place[0]);
    drawLevel(place);
}

/** The four sections, Search, the People strip, the path's Back, Try again, and the watch on the covers. */
export function wireBrowse() {
    const seeAll = buildElement('button', { className: 'browse-people-all', attrs: { type: 'button' }, text: 'See all' });
    seeAll.addEventListener('click', () => {
        if (state.screen === 'browse' && state.place === null) openScreen('browse', ['people']);
    });
    byId('browse-search').after(buildElement('section', { className: 'browse-people hidden', attrs: { 'aria-label': 'People' } }, [
        buildElement('div', { className: 'browse-people-head' }, [
            buildElement('h2', { className: 'browse-people-title', text: 'People' }),
            seeAll,
        ]),
        buildElement('div', { className: 'browse-people-row' }),
    ]));
    state.covers.observer = new IntersectionObserver(nearing, { rootMargin: COVERS_AHEAD });
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
