/**
 * The phone's Grid screen (docs/plans/phone-viewer.md, Screens 3): a view's photos as thumbnails, in its order, the
 * photos alone or the clips alone if chosen; a tap opens one (the Photo screen, mobile-photo.js).
 *
 * The view is the Grid's place in history (mobile-screens.js), set by Browse's "Show these photos" and Search, or by an
 * address naming one; `state.view` is it while the Grid is shown. What is held for it, its ids and the cards asked so far,
 * is a view of library-source.js (`newLibrary`) in `state.photos`, shown through this page's host: its ids are read and its
 * cards asked a window at a time (at most 200 a request) by the desktop page's own code, and a reply for a view the Grid no
 * longer holds is dropped. Back from a photo finds the view held and asks nothing again, the card of the photo left (swiped
 * to, mobile-photo.js) in view; Browse lets it go.
 *
 * The order and Photos/Clips are the view's own names (`order`, `media`), chosen in the head of the Filter sheet
 * (mobile-filter.js): a change is the view's place in its history step, so Back and a reload keep it, and the photos are read
 * again from the top.
 */
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import {
    LIBRARY_MEDIA, LIBRARY_ORDERS, cardAt, destroyLibrary, forgetCards, indexOfCardKey, libraryCardKey,
    loadLibraryIds, mediaLabel, newLibrary, orderLabel, staleText, viewCountSaid, viewHeading
} from './library-source.js';
import { createVGrid } from './vgrid.js';
import { state } from './mobile-state.js';
import { openScreen, replacePlace, samePlace } from './mobile-screens.js';
import { viewOf } from './mobile-browse.js';
import { drawFilterStatus, marksFindNone } from './mobile-filter.js';

const byId = (id) => document.getElementById(id);

/** What library-source.js asks of the page showing a view: the phone shows one, the Grid's. */
const HOST = {
    shows: (lib) => lib === state.photos,
    grid: () => state.grid,
    changed: () => drawStatus(),
    idsRead: () => {},
    dropped: () => {},
};

/**
 * The count line, and the problem with Try again when the ids or a window's cards could not be read; and what the read says
 * to the filter (mobile-filter.js). Marks that find none count "0 photos", the filter's words under it; any other view that
 * holds none says why.
 */
function drawStatus() {
    const lib = state.photos;
    if (!lib) return;
    const failed = lib.status === 'error' || Boolean(lib.notice);
    byId('grid-count').textContent = lib.status === 'loading' ? 'Loading...' : lib.status === 'error' ? ''
        : lib.status === 'empty' && !marksFindNone(lib) ? lib.message : viewCountSaid(lib);
    drawFilterStatus();
    byId('grid-problem-text').textContent = lib.status === 'error' ? `Could not load photos. ${lib.message}`.trim()
        : 'Could not load photos.';
    byId('grid-problem').classList.toggle('hidden', !failed);
}

/** Read the view's photos afresh, from the top: what was held for another view, or for this one, is let go. */
function readView(view) {
    destroyLibrary(state.photos);
    const lib = newLibrary(view, HOST);
    state.photos = lib;
    state.grid.reset();
    loadLibraryIds(lib).then((replaced) => {
        if (replaced && lib === state.photos) state.grid.reset();
    });
}

/** Show the Grid's view: as it was held when it is the one (Back from a photo), else read. */
export function showGrid() {
    const view = state.place;
    state.view = view;
    if (!view) return;
    byId('grid-heading').textContent = viewHeading(view);
    byId('grid-order').value = view.order;
    byId('grid-media').value = view.media;
    const held = state.photos;
    if (held && held.status !== 'error' && samePlace(viewOf(held), view)) {
        drawStatus();
        state.grid.render();
        // The photo last open (`activeId`, as the desktop's view means it: tapped here, or swiped to in mobile-photo.js):
        // its card in view, the scroll kept when it already is (vgrid.js, 'nearest').
        // Only coming back from the Photo screen: from the list of libraries the grid stays where it was scrolled to.
        const open = held.activeId === null || state.cameFrom !== 'photo' ? -1 : held.ids.indexOf(held.activeId);
        if (open >= 0) state.grid.scrollToIndex(open);
        return;
    }
    readView(view);
}

/** Let the view go (Browse and the list of libraries are shown): opened again, it is read afresh. */
export function forgetGrid() {
    destroyLibrary(state.photos);
    state.photos = null;
}

/** Try again: the ids when they could not be read, else the cards that could not. */
function tryAgain() {
    const lib = state.photos;
    if (!lib || lib.status === 'error') {
        if (state.view) readView(state.view);
        return;
    }
    forgetCards(lib);
    lib.notice = '';
    drawStatus();
    state.grid.refresh();
}

/** A card: the picture (a clip's poster, its play mark), a stale photo's badge, the name small under it. */
function buildCard(record, index) {
    const picture = buildElement('div', { className: 'phone-card-picture' });
    if (record.placeholder) {
        if (record.failed) picture.append(buildElement('span', { className: 'phone-card-note', text: 'Could not load' }));
        return buildElement('div', { className: 'phone-card placeholder', attrs: { 'aria-hidden': 'true' } },
            [picture, buildElement('span', { className: 'phone-card-name', text: '\u00A0' })]);
    }
    const img = buildElement('img', { attrs: { alt: '' } });
    if (record.thumb) img.dataset.src = api.image(record.thumb);   // the grid gives it its src once in view (vgrid.js)
    // A picture that cannot be had is a grey card; the server answers one it cannot make with its own placeholder.
    img.addEventListener('error', () => img.classList.add('failed'));
    picture.append(img);
    // A clip is one the server named a video with the view's ids (library-source.js): its picture is its poster.
    if (state.photos && state.photos.videos.has(record.id)) picture.append(buildElement('span', { className: 'phone-card-play', text: '\u25B6', attrs: { 'aria-label': 'Video' } }));
    const stale = staleText(record.stale);
    if (stale) picture.append(buildElement('span', { className: 'phone-card-stale', text: stale }));
    return buildElement('button', {
        className: `phone-card${record.stale === 'missing' ? ' stale-missing' : ''}`,
        attrs: { type: 'button' }, data: { id: record.id, at: index },
    }, [picture, buildElement('span', { className: 'phone-card-name', text: record.name || '' })]);
}

/** A choice of order or of Photos/Clips: the view's place in this step, read again from the top. */
function choose(name, value) {
    if (!state.view || state.screen !== 'grid') return;
    replacePlace(viewOf({ ...state.view, [name]: value }));
}

/** The grid, its two choices, a tap on a card and Try again. */
export function wireGrid() {
    const order = byId('grid-order');
    const media = byId('grid-media');
    order.append(...LIBRARY_ORDERS.map((each) => buildElement('option', { text: orderLabel(each), attrs: { value: each } })));
    media.append(...LIBRARY_MEDIA.map((each) => buildElement('option', { text: mediaLabel(each), attrs: { value: each } })));
    order.addEventListener('change', () => choose('order', order.value));
    media.addEventListener('change', () => choose('media', media.value));
    state.grid = createVGrid({
        container: byId('grid-cards'),
        scroller: document.querySelector('[data-screen="grid"]'),
        source: {
            count: () => (state.photos ? state.photos.ids.length : 0),
            recordAt: (index) => cardAt(state.photos, index),
            indexOfKey: (key) => indexOfCardKey(state.photos, key),
        },
        buildCard,
        cardKey: libraryCardKey,
    });
    byId('grid-cards').addEventListener('click', (event) => {
        const card = event.target.closest('.phone-card[data-id]');
        if (!card) return;
        if (state.photos) state.photos.activeId = Number(card.dataset.id);    // the photo open, as a swipe sets it
        openScreen('photo', { id: Number(card.dataset.id), at: Number(card.dataset.at) });
    });
    byId('grid-retry').addEventListener('click', tryAgain);
}
