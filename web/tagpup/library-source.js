// TagPup's page: a library view's photos -- the grid's second kind of source (docs/ARCHITECTURE.md,
// phase 9b-2). A folder on disk is walked and every photo's record is in the page; a view of the
// library is up to 200,000 photos, so the page holds only their ORDER (`ids`, one array, from
// GET /api/library/ids) and, for the photos near the window, their cards (GET /api/library/cards).
//
// The grid asks the three questions of its source interface -- count(), recordAt(i), indexOfKey(key) --
// and `recordAt` answers at once: the card if it is held, else a placeholder with the same stable key,
// asking for the cards around the window. A request is made at most every FETCH_DELAY_MS, for the
// window as it is THEN (a scroll that flies past asks for nothing it has left behind), two at a time,
// and one the window has left is cancelled. A batch that fails is tried once more and then given up:
// the placeholder says so and Refresh view tries again. Cards are kept by id, the last-used ones, at
// most MAX_CARDS: the far ones go.
//
// Everything the view holds is `state.library` (state.js); this module has no state of its own. A view
// that is closed or replaced is told apart from the one a reply was asked for by the object itself:
// a reply for a view that is no longer `state.library` is dropped.
import { api } from './common/api.js';
import { baseName, pathKey } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { forgetSelectedIds, newIdSelection, reconcileIdSelection } from './selected.js';

/**
 * The kinds a view is, as GET /api/library/view names them: the sources a navigator row holds -- `keyword_only` a keyword's
 * node without the nodes under it, `year_other` the photos of a year whose date names no month of it -- and `any_of`, the
 * union of a list of them (the rows selected together, #672; its value is the list, each { kind, value, recursive }).
 */
export const LIBRARY_KINDS = ['all', 'folder', 'keyword', 'person', 'year', 'month', 'keyword_only', 'year_other', 'any_of'];
/** The kinds a union holds. */
export const MEMBER_KINDS = LIBRARY_KINDS.filter(kind => kind !== 'any_of');
/** The most sources a union holds: as the server takes (services.library_view.MAX_MEMBERS). */
export const MAX_MEMBERS = 1000;
/** The longest union an address may name, in characters of its JSON. */
export const MAX_UNION_VALUE = 200000;

/**
 * The orders a view is read in (#671): by Date Taken -- photos with none after the dated ones, either way -- or by file
 * name, each either way. `taken` is the order an address that names none has.
 */
export const LIBRARY_ORDERS = ['taken', 'taken-desc', 'name', 'name-desc'];
export const DEFAULT_ORDER = 'taken';
/** What each order is called where it is chosen. */
export const ORDER_LABELS = {
    taken: 'Date taken, oldest first', 'taken-desc': 'Date taken, newest first', name: 'Name, A to Z', 'name-desc': 'Name, Z to A',
};

/** Cards in one request: the most the server answers. */
export const BATCH = 200;
/** Cards kept: the least recently used go. */
export const MAX_CARDS = 2000;
/**
 * How long the window must stand still before its cards are asked for: a scroll that flies past asks for
 * nothing it leaves behind. A scroll that never stops is answered every MAX_REARMS delays all the same.
 */
export const FETCH_DELAY_MS = 100;
export const MAX_REARMS = 5;
/** Requests for cards under way at once. */
export const MAX_IN_FLIGHT = 2;
/** A card's batch fails this many times and its placeholder gives up. */
export const MAX_TRIES = 2;
/** After a failure, before the one more try. */
export const RETRY_MS = 2000;
/** The longest value an address may name. */
export const MAX_VALUE = 1000;

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const YEAR = /^\d{1,4}$/;

// ---- The address ----------------------------------------------------------------------

/**
 * The view an address names: null when it names none (`?view=` is absent), else { kind, value,
 * recursive }, or { error } when it names something that cannot be a view -- a sentence for the page
 * to show, never an exception.
 */
export function viewSpecFromSearch(search) {
    const params = new URLSearchParams(search || '');
    if (!params.has('view')) return null;
    const kind = params.get('view') || '';
    if (!LIBRARY_KINDS.includes(kind)) {
        return { error: `This address names a view of the library (“${kind.slice(0, 40)}”) that TagPup does not know. `
            + `The kinds are ${LIBRARY_KINDS.join(', ')}.` };
    }
    const order = params.get('order') || DEFAULT_ORDER;
    if (!LIBRARY_ORDERS.includes(order)) {
        return { error: `This address names an order (“${order.slice(0, 40)}”) that TagPup does not know. The orders are ${LIBRARY_ORDERS.join(', ')}.` };
    }
    if (kind === 'all') return { kind, value: null, recursive: false, order };
    if (kind === 'any_of') {
        const members = unionFromText(params.get('value') || '');
        return members.error ? members : { kind, value: members, recursive: false, order };
    }
    const found = memberOf(kind, params.get('value'), ['1', 'true', 'yes'].includes((params.get('recursive') || '').toLowerCase()));
    return found.error ? found : { ...found, order };
}

/** One source as an address or a union names it: { kind, value, recursive }, or { error } saying why it is none. */
function memberOf(kind, raw, recursive) {
    if (!MEMBER_KINDS.includes(kind)) return { error: `A view of the library cannot hold “${String(kind).slice(0, 40)}”.` };
    if (kind === 'all') return { kind, value: null, recursive: false };
    const value = (raw === null || raw === undefined ? '' : String(raw)).trim();
    if (!value) return { error: `This address names a ${kind} view without saying which.` };
    if (value.length > MAX_VALUE) return { error: `This address names a ${kind} too long to be one.` };
    if ((kind === 'year' || kind === 'year_other') && !YEAR.test(value)) return { error: 'A year is written as 2024.' };
    if (kind === 'month' && !MONTH.test(value)) return { error: 'A month is written as 2024-06.' };
    return { kind, value, recursive: kind === 'folder' && Boolean(recursive) };
}

/** The sources a union's JSON names, or { error }: a list of at least two and at most MAX_MEMBERS, none a union. */
function unionFromText(text) {
    if (!text.trim()) return { error: 'This address names a view of several rows without saying which.' };
    if (text.length > MAX_UNION_VALUE) return { error: 'This address names more rows than a view can show at once.' };
    let found;
    try {
        found = JSON.parse(text);
    } catch (err) {
        return { error: 'This address names a view of several rows that cannot be read.' };
    }
    if (!Array.isArray(found) || found.length === 0) return { error: 'This address names a view of several rows that cannot be read.' };
    if (found.length > MAX_MEMBERS) return { error: `This address names ${found.length.toLocaleString()} rows; a view shows at most ${MAX_MEMBERS.toLocaleString()} at once.` };
    const members = [];
    for (const each of found) {
        const member = each && typeof each === 'object' ? memberOf(each.kind, each.value, each.recursive) : { error: 'This address names a view of several rows that cannot be read.' };
        if (member.error) return member;
        members.push(member);
    }
    return members;
}

/** A union's sources as its JSON holds them: short, `recursive` only where it is true. */
function unionText(members) {
    return JSON.stringify(members.map(m => (m.recursive ? { kind: m.kind, value: m.value, recursive: true } : { kind: m.kind, value: m.value })));
}

/** The query string that opens a view: the inverse of viewSpecFromSearch. */
export function viewSearch(spec) {
    const params = new URLSearchParams();
    params.set('view', spec.kind);
    if (spec.kind === 'any_of') params.set('value', unionText(spec.value));
    else if (spec.kind !== 'all') params.set('value', spec.value);
    if (spec.recursive) params.set('recursive', '1');
    if (spec.order && spec.order !== DEFAULT_ORDER) params.set('order', spec.order);
    return `?${params.toString()}`;
}

/** Are these the same view: the same source, read in the same order? */
export function sameView(a, b) {
    if (!a || !b || a.kind !== b.kind || Boolean(a.recursive) !== Boolean(b.recursive)) return false;
    if ((a.order || DEFAULT_ORDER) !== (b.order || DEFAULT_ORDER)) return false;
    if (a.kind === 'any_of') return unionText(a.value || []) === unionText(b.value || []);
    return (a.value ?? null) === (b.value ?? null);
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
    'October', 'November', 'December'];

/** What one source of a union is called, short. */
function memberLabel(spec) {
    if (spec.kind === 'month') {
        const [year, month] = String(spec.value).split('-');
        return `${MONTHS[Number(month) - 1] || month} ${year}`;
    }
    if (spec.kind === 'year') return String(spec.value);
    if (spec.kind === 'year_other') return `${spec.value} with no month`;
    if (spec.kind === 'keyword_only') return `${spec.value} alone`;
    if (spec.kind === 'folder') return `${baseName(spec.value) || spec.value}${spec.recursive ? '' : ' alone'}`;
    if (spec.kind === 'all') return 'everything';
    return String(spec.value);
}

/** What a view is called, in the header. */
export function viewLabel(spec) {
    if (spec.kind === 'all') return 'The whole library';
    if (spec.kind === 'any_of') {
        const members = Array.isArray(spec.value) ? spec.value : [];
        const named = members.slice(0, 3).map(memberLabel);
        if (members.length <= 3) return `${named.slice(0, -1).join(', ')}${members.length > 1 ? ' and ' : ''}${named.at(-1) || ''}`;
        return `${named.join(', ')} and ${(members.length - 3).toLocaleString()} more`;
    }
    if (spec.kind === 'keyword_only') return `Keyword ${spec.value}, without the keywords under it`;
    if (spec.kind === 'year_other') return `Photos of ${spec.value} whose date names no month`;
    if (spec.kind === 'year') return `Photos of ${spec.value}`;
    if (spec.kind === 'month') {
        const [year, month] = String(spec.value).split('-');
        return `Photos of ${MONTHS[Number(month) - 1] || month} ${year}`;
    }
    if (spec.kind === 'keyword') return `Keyword ${spec.value}, and everything under it`;
    if (spec.kind === 'person') return `Photos of ${spec.value}`;
    return `Folder ${spec.value}${spec.recursive ? ', and its subfolders' : ''}`;
}

/** Why a view holds nothing, in a sentence. */
export function emptySentence(spec) {
    if (spec.kind === 'all') return 'This library holds no photos.';
    if (spec.kind === 'any_of') return 'None of the rows selected holds a photo, or the library no longer has them.';
    if (spec.kind === 'keyword_only') return `No photo carries the keyword “${spec.value}” itself, or the library's tag tree has no such keyword.`;
    if (spec.kind === 'year_other') return `Every photo of ${spec.value} has a month.`;
    if (spec.kind === 'keyword') return `No photo carries the keyword “${spec.value}”, or the library's tag tree has no such keyword.`;
    if (spec.kind === 'person') return `No photo names ${spec.value}.`;
    if (spec.kind === 'folder') return `The library holds no photo in ${spec.value}${spec.recursive ? ' or below it' : ''}.`;
    return `The library holds no ${spec.kind === 'year' ? 'photo of the year' : 'photo of'} ${spec.value}.`;
}

function idsUrl(lib) {
    const query = [`kind=${encodeURIComponent(lib.kind)}`];
    if (lib.kind === 'folder') query.push(`folder=${encodeURIComponent(lib.value)}`, `recursive=${lib.recursive ? 1 : 0}`);
    else if (lib.kind === 'any_of') query.push(`value=${encodeURIComponent(unionText(lib.value))}`);
    else if (lib.value !== null) query.push(`value=${encodeURIComponent(lib.value)}`);
    if (lib.order && lib.order !== DEFAULT_ORDER) query.push(`order=${encodeURIComponent(lib.order)}`);
    return `/api/library/ids?${query.join('&')}`;
}

// ---- The view's state -----------------------------------------------------------------

/** A view just opened, holding nothing yet. It is `state.library` from then until it is closed. */
export function newLibrary(spec) {
    state.libraryTokens += 1;
    return {
        token: state.libraryTokens,
        kind: spec.kind, value: spec.value ?? null, recursive: Boolean(spec.recursive),
        order: LIBRARY_ORDERS.includes(spec.order) ? spec.order : DEFAULT_ORDER,   // how the ids are ordered (#671)
        status: 'loading',            // loading | ready | empty | error
        message: '',                  // why it is empty or failed, in a sentence
        notice: '',                   // something that went wrong beside the view: a refresh, a selection
        ids: [], total: 0, complete: true,
        loading: false, controller: null,
        cards: new Map(),             // id -> card, least recently used first
        tries: new Map(),             // id -> failed batches
        requested: new Set(),         // ids in a batch under way
        inflight: new Map(),          // batch number -> { ids, lo, hi, controller }
        batches: 0, timer: 0, retryTimer: 0, more: false, baseline: null, rearms: 0,
        activeId: null, wantedId: null, lastId: null, openToken: 0,
        sel: newIdSelection(),        // what is selected, by photo id (selected.js): survives Refresh view and edits
    };
}

/** Stop everything a view has under way: its requests and its timers. The view is then to be dropped. */
export function destroyLibrary(lib) {
    if (!lib) return;
    if (lib.controller) lib.controller.abort();
    for (const batch of lib.inflight.values()) batch.controller.abort();
    lib.inflight.clear();
    lib.requested.clear();
    window.clearTimeout(lib.timer);
    window.clearTimeout(lib.retryTimer);
    lib.timer = 0;
    lib.retryTimer = 0;
}

/** Ask for the view's order: its ids and total. Resolves true when they were replaced. */
export function loadLibraryIds(lib, refreshing = false) {
    if (lib.controller) lib.controller.abort();
    const controller = new AbortController();
    lib.controller = controller;
    lib.loading = true;
    if (!refreshing) lib.status = 'loading';
    upper.libraryChanged();
    const current = () => lib === state.library && controller === lib.controller;
    return api.fetch(idsUrl(lib), { signal: controller.signal })
        .then(res => res.json().catch(() => ({})).then(body => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (!current()) return false;
            if (!ok) throw new Error((body && body.error) || 'The library could not be asked.');
            lib.loading = false;
            lib.ids = Array.isArray(body.ids) ? body.ids : [];
            lib.total = Number.isFinite(body.total) ? body.total : lib.ids.length;
            lib.complete = body.complete !== false;
            lib.status = lib.ids.length === 0 ? 'empty' : 'ready';
            lib.message = lib.ids.length === 0 ? emptySentence(lib) : '';
            lib.notice = '';
            reconcileIdSelection();       // a photo the view no longer holds is no longer selected
            return true;
        })
        .catch(err => {
            if (err.name === 'AbortError' || !current()) return false;
            lib.loading = false;
            if (refreshing) lib.notice = `Could not refresh the view: ${err.message}`;
            else {
                lib.status = 'error';
                lib.message = err.message;
            }
            return false;
        })
        .then(replaced => {
            if (lib === state.library) upper.libraryChanged();
            return replaced;
        });
}

/** Forget the cards and what failed, to be asked for again (Refresh view). */
export function forgetCards(lib) {
    for (const batch of lib.inflight.values()) batch.controller.abort();
    lib.inflight.clear();
    lib.requested.clear();
    lib.cards.clear();
    lib.tries.clear();
}

// ---- The grid's source ----------------------------------------------------------------

export function libraryCount() {
    return state.library ? state.library.ids.length : 0;
}

/** The key a card, or the placeholder in its place, is drawn under: the photo's id, so a rename redraws nothing. */
export function libraryCardKey(record) {
    return `#${record.id}`;
}

export function libraryIndexOfKey(key) {
    const lib = state.library;
    if (!lib || typeof key !== 'string' || key[0] !== '#') return -1;
    return lib.ids.indexOf(Number(key.slice(1)));
}

/** The card at an index, or a placeholder with the same key (and a request for the window's cards on its way). */
export function libraryRecordAt(index) {
    const lib = state.library;
    const id = lib.ids[index];
    const card = lib.cards.get(id);
    if (card) {
        lib.cards.delete(id);        // used just now: the last to go
        lib.cards.set(id, card);
        return card;
    }
    askForCards(lib);
    return { id, placeholder: true, failed: (lib.tries.get(id) || 0) >= MAX_TRIES };
}

function askForCards(lib) {
    if (lib.timer || lib.status !== 'ready' || lib !== state.library) return;
    lib.rearms = 0;
    lib.baseline = null;
    // The first window of a view is asked for at once; later ones once the window has stood still.
    lib.timer = window.setTimeout(() => checkWindow(lib), lib.batches === 0 ? 0 : FETCH_DELAY_MS);
    // Where the window is, once the draw that missed a card is done (it is mid-draw now).
    window.setTimeout(() => {
        if (lib.timer && state.grid) lib.baseline = state.grid.extent().first;
    }, 0);
}

function checkWindow(lib) {
    lib.timer = 0;
    if (lib !== state.library || !state.grid) return;
    const first = state.grid.extent().first;
    if (lib.batches > 0 && lib.baseline !== null && first !== lib.baseline && lib.rearms < MAX_REARMS) {
        // The window moved while we waited: wait for it to stop.
        lib.rearms += 1;
        lib.baseline = first;
        lib.timer = window.setTimeout(() => checkWindow(lib), FETCH_DELAY_MS);
        return;
    }
    fetchWindow(lib);
}

/** The cards missing around the window, as the window is now: nearest the top first, in batches. */
function fetchWindow(lib) {
    if (lib !== state.library || !state.grid) return;
    const extent = state.grid.extent();
    const span = Math.max(extent.end - extent.first, 1);
    const from = Math.max(0, extent.first - (span >> 1));
    const to = Math.min(lib.ids.length, extent.end + (span >> 1));
    for (const [key, batch] of lib.inflight) {
        // A batch for a part of the view the window has left is of no use: cancel it.
        if (batch.hi < from || batch.lo >= to) {
            batch.controller.abort();
            lib.inflight.delete(key);
            batch.ids.forEach(id => lib.requested.delete(id));
        }
    }
    const missing = [];
    for (let i = from; i < to; i++) {
        const id = lib.ids[i];
        if (lib.cards.has(id) || lib.requested.has(id) || (lib.tries.get(id) || 0) >= MAX_TRIES) continue;
        missing.push(i);
    }
    lib.more = false;
    for (let start = 0; start < missing.length; start += BATCH) {
        if (lib.inflight.size >= MAX_IN_FLIGHT) {
            lib.more = true;
            break;
        }
        sendBatch(lib, missing.slice(start, start + BATCH));
    }
}

function sendBatch(lib, indexes) {
    const ids = indexes.map(i => lib.ids[i]);
    const controller = new AbortController();
    lib.batches += 1;
    const number = lib.batches;
    const batch = { ids, lo: indexes[0], hi: indexes[indexes.length - 1], controller };
    lib.inflight.set(number, batch);
    ids.forEach(id => lib.requested.add(id));
    const settle = () => {
        if (lib.inflight.get(number) === batch) lib.inflight.delete(number);
        ids.forEach(id => lib.requested.delete(id));
    };
    api.fetch(`/api/library/cards?ids=${ids.join(',')}`, { signal: controller.signal })
        .then(res => res.json().catch(() => ({})).then(body => {
            if (!res.ok) throw new Error((body && body.error) || `The library answered ${res.status}`);
            return body;
        }))
        .then(body => {
            if (lib !== state.library || controller.signal.aborted) return;
            settle();
            cardsArrived(lib, ids, Array.isArray(body.cards) ? body.cards : []);
        })
        .catch(err => {
            if (err.name === 'AbortError' || lib !== state.library) return;
            settle();
            cardsFailed(lib, ids, err);
        });
}

/** Keep a card as the page reads it: `filename` is what a folder's record calls its name. */
function held(card) {
    card.filename = card.name;
    return card;
}

function trim(lib) {
    if (lib.cards.size <= MAX_CARDS) return;
    let over = lib.cards.size - Math.floor(MAX_CARDS * 0.9);
    for (const id of lib.cards.keys()) {
        if (over-- <= 0) break;
        lib.cards.delete(id);
    }
}

function cardsArrived(lib, asked, cards) {
    const got = new Set();
    for (const card of cards) {
        if (!card || !Number.isInteger(card.id)) continue;
        lib.cards.set(card.id, held(card));
        lib.tries.delete(card.id);
        got.add(card.id);
    }
    trim(lib);
    // A photo the library no longer has when its card is asked for is dropped, quietly.
    const gone = asked.filter(id => !got.has(id));
    if (gone.length) dropPhotos(lib, gone);
    if (state.grid) {
        state.grid.patch([...got].map(id => `#${id}`));
        if (gone.length) state.grid.refresh();
    }
    if (lib.more) askForCards(lib);
}

function cardsFailed(lib, asked, err) {
    console.error('Could not fetch cards of the library:', err);
    const fresh = [];
    for (const id of asked) {
        const tries = (lib.tries.get(id) || 0) + 1;
        lib.tries.set(id, tries);
        if (tries >= MAX_TRIES) fresh.push(`#${id}`);
    }
    if (fresh.length && state.grid) state.grid.patch(fresh);   // their placeholders say they gave up
    lib.notice = fresh.length ? 'Some photos could not be loaded. Refresh view asks again.' : '';
    // One more try, soon, whether or not anyone scrolls; then the placeholders give up.
    if (asked.some(id => (lib.tries.get(id) || 0) < MAX_TRIES)) {
        window.clearTimeout(lib.retryTimer);
        lib.retryTimer = window.setTimeout(() => askForCards(lib), RETRY_MS);
    }
    upper.libraryChanged();
}

/** Take photos out of the order and the total: deleted, or no longer in the library. One pass. */
export function dropPhotos(lib, ids) {
    const gone = new Set(ids);
    lib.ids = lib.ids.filter(id => !gone.has(id));
    lib.total = Math.max(0, lib.total - gone.size);
    for (const id of gone) {
        lib.cards.delete(id);
        lib.tries.delete(id);
    }
    forgetSelectedIds(gone);
    upper.updateSelectedThumbnailsCount();   // the selection is the view's total less the excluded: the count moved
    if (lib.ids.length === 0) {
        lib.status = 'empty';
        lib.message = emptySentence(lib);
    }
    upper.libraryChanged();
}

/** A photo deleted from the page: its card goes and the total drops. */
export function forgetPhoto(id) {
    const lib = state.library;
    if (!lib) return;
    dropPhotos(lib, [id]);
    if (state.grid) state.grid.refresh();
}

/** Ask again for these photos' cards (a rotate changed the file's stamp: the thumbnail's URL has a new `v`). */
export function refetchCards(ids) {
    const lib = state.library;
    if (!lib || !ids.length) return Promise.resolve();
    return api.json(`/api/library/cards?ids=${ids.join(',')}`).then(body => {
        if (lib !== state.library) return;
        const got = [];
        for (const card of (body && body.cards) || []) {
            lib.cards.set(card.id, held(card));
            got.push(`#${card.id}`);
        }
        if (state.grid && got.length) state.grid.patch(got);
    }).catch(err => console.error('Could not ask for the card again:', err));
}

/** What a card says of damage, as the folder's damaged list does (damaged.js markCard reads it). */
export function cardDamage(card) {
    if (!card || !card.damaged) return null;
    const incomplete = card.damage === 'incomplete';
    return {
        kind: card.damage, indexed: incomplete, detail: '',
        reason: incomplete ? 'Possibly an incomplete copy: the file ends in zero bytes' : 'The picture cannot be read',
    };
}

// ---- A card and the photo it stands for ------------------------------------------------

/** The id of the held card whose photo is at this path, or null. */
export function libraryIdOfPath(path) {
    const lib = state.library;
    if (!lib || !path) return null;
    for (const card of lib.cards.values()) if (card.path === path) return card.id;
    const key = pathKey(path);
    for (const card of lib.cards.values()) if (pathKey(card.path) === key) return card.id;
    return null;
}

/**
 * A photo as the details panel reads it (GET /api/library/photo): resolves the record, or null when the
 * library no longer has the photo (it is then dropped from the view, quietly) or the view changed since.
 */
export function fetchLibraryRecord(id) {
    const lib = state.library;
    if (!lib) return Promise.resolve(null);
    return api.fetch(`/api/library/photo?id=${id}`).then(res => res.json().catch(() => ({})).then(body => {
        if (lib !== state.library) return null;
        if (res.status === 404) {
            dropPhotos(lib, [id]);
            if (state.grid) state.grid.refresh();
            return null;
        }
        if (!res.ok || !body.photo) throw new Error((body && body.error) || `The library answered ${res.status}`);
        return body.photo;
    }));
}

/**
 * The cards of the photos the details panel has edited, brought up to date in place: the photo keeps its
 * place in the order (the order is not recomputed until the view is refreshed). Called after any edit.
 */
export function applyEditedRecords() {
    const lib = state.library;
    if (!lib || !state.grid) return;
    const changed = [];
    for (const record of state.folderPhotos) {
        const card = record.id !== undefined ? lib.cards.get(record.id) : null;
        if (!card) continue;
        const title = record.title || '';
        const taken = record.taken || null;
        if (card.path === record.path && (card.title || '') === title && (card.taken || null) === taken) continue;
        card.path = record.path;
        card.name = record.filename;
        card.filename = record.filename;
        card.title = title;
        card.taken = taken;
        changed.push(`#${card.id}`);
    }
    if (changed.length) state.grid.patch(changed);
}

/**
 * A bulk edit has rewritten files: the cards in the window are asked for again (their `thumb` carries the file's new stamp,
 * and `stale` is what the file is now), and the others are let go, to be asked for when they are scrolled to. Resolves when
 * the window's cards have arrived.
 */
export function refreshHeldCards() {
    const lib = state.library;
    if (!lib || !state.grid) return Promise.resolve();
    const extent = state.grid.extent();
    const near = lib.ids.slice(extent.first, extent.end);
    const keep = new Set(near);
    for (const id of [...lib.cards.keys()]) if (!keep.has(id)) lib.cards.delete(id);
    lib.tries.clear();
    const asking = [];
    for (let start = 0; start < near.length; start += BATCH) asking.push(refetchCards(near.slice(start, start + BATCH)));
    return Promise.all(asking);
}

/** Which photo comes `delta` after the open one in the view's order: its id, or null at either end. */
export function libraryStepTarget(delta) {
    const lib = state.library;
    if (!lib || lib.ids.length === 0) return null;
    // From the photo being opened, if one is: two quick presses go two photos on.
    const from = lib.wantedId !== null ? lib.wantedId : lib.activeId;
    const at = from === null ? -1 : lib.ids.indexOf(from);
    let next;
    if (at === -1) next = delta > 0 ? 0 : lib.ids.length - 1;
    else next = Math.min(Math.max(at + delta, 0), lib.ids.length - 1);
    return next === at ? null : lib.ids[next];
}

/** Where the open photo is in the view: { index, total } (1-based), or null. */
export function libraryPosition() {
    const lib = state.library;
    if (!lib || lib.activeId === null) return null;
    const at = lib.ids.indexOf(lib.activeId);
    return at === -1 ? null : { index: at + 1, total: lib.ids.length };
}
